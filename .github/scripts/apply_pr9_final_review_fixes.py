from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_browser() -> None:
    path = Path("apps/instagram_tools/browser.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''            if not response.ok:
                diagnostics.append(f"시도 {attempt_number}: HTTP {response.status_code}")
                continue
''',
        '''            if not response.ok:
                # A failed HTTP response does not prove that Instagram rejected the
                # write: the server may commit it and fail while producing the
                # response. Verify the current target before another write.
                confirmation = self._confirm_unfollow_after_write(
                    user_id,
                    attempt_number=attempt_number,
                    status_code=response.status_code,
                    confirmation="post_error_friendship_check",
                )
                if confirmation is not None:
                    return confirmation
                message = self._response_message(response)
                detail = f": {message}" if message else ""
                diagnostics.append(
                    f"시도 {attempt_number}: HTTP {response.status_code}{detail} · 팔로우 상태가 그대로임"
                )
                continue
''',
        "verify unsuccessful writes",
    )
    path.write_text(text, encoding="utf-8")


def patch_worker() -> None:
    path = Path("apps/instagram_tools/worker.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''                except UnfollowRunError as exc:
                    self.events.put(("unfollow_error", (exc.user_message, exc.summary)))
''',
        '''                except UnfollowRunError as exc:
                    LOGGER.exception("Unfollow operation failed: %s", exc.user_message)
                    self.events.put(("unfollow_error", (exc.user_message, exc.summary)))
''',
        "log detailed unfollow failure",
    )
    path.write_text(text, encoding="utf-8")


def patch_app() -> None:
    path = Path("apps/instagram_tools/app.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''                    if self.running_kind == "auto_like":
                        self.auto_status_var.set("오류로 중지")
                    elif self.running_kind:
                        self.cleaner_status_var.set("오류로 중지")
''',
        '''                    if self.running_kind == "auto_like":
                        self.auto_status_var.set("오류로 중지")
                    elif self.running_kind in {"scan", "unfollow"}:
                        self.cleaner_status_var.set("오류로 중지")
''',
        "isolate profile reset errors",
    )
    path.write_text(text, encoding="utf-8")


def write_tests() -> None:
    path = Path("tests/regression/test_instagram_tools_final_review.py")
    path.write_text(
        '''from __future__ import annotations

import queue
import threading
import time
import unittest
from unittest.mock import Mock

from apps.instagram_tools.app import InstagramToolsApp
from apps.instagram_tools.browser import VerifiedFriendshipBackend
from apps.instagram_tools.worker import InstagramAutomationWorker
from apps.non_follower_cleaner.engine import (
    FriendshipAccount,
    UnfollowRunError,
    UnfollowSummary,
)
from tests.regression.test_instagram_tools import (
    DESTROY_URL,
    SHOW_URL,
    FakePersistentBrowser,
    FakeSession,
    FakeStorage,
    FakeStringVar,
    response,
)


class FailedWriteVerificationRegressionTestCase(unittest.TestCase):
    def test_http_failure_is_verified_before_retrying_the_write(self) -> None:
        session = FakeSession(
            [
                response(DESTROY_URL, status_code=500, text="temporary server failure"),
                response(
                    SHOW_URL,
                    text='{"status":"ok","following":false}',
                    content_type="application/json",
                ),
            ]
        )
        backend = VerifiedFriendshipBackend(session)

        payload = backend.unfollow("2")

        self.assertEqual(
            payload["_transport"]["confirmation"],
            "post_error_friendship_check",
        )
        self.assertEqual(payload["_transport"]["status_code"], 500)
        self.assertIs(payload["friendship_status"]["following"], False)
        calls = [arguments for _script, arguments in session.page.evaluate_calls]
        self.assertEqual([call["method"] for call in calls], ["POST", "GET"])
        self.assertTrue(str(calls[1]["path"]).startswith("/api/v1/friendships/show/2/"))


class FailingUnfollowWorker(InstagramAutomationWorker):
    def _run_unfollow(self, _browser, _payload, _stop_event) -> None:
        account = FriendshipAccount(pk="2", username="target")
        summary = UnfollowSummary(
            selected=1,
            eligible=1,
            attempted=1,
            failed=[(account, "HTTP 500 after write")],
        )
        raise UnfollowRunError(
            "마지막 대상: @target. HTTP 500 after write",
            summary=summary,
        )


class WorkerFailureLoggingRegressionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        FakePersistentBrowser.instances.clear()

    @staticmethod
    def wait_for_finished(
        events: queue.Queue[tuple[str, object]],
        timeout: float = 3.0,
    ) -> list[tuple[str, object]]:
        seen: list[tuple[str, object]] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = events.get(timeout=0.1)
            except queue.Empty:
                continue
            seen.append(event)
            if event[0] == "operation_finished":
                return seen
        raise AssertionError("worker did not finish operation")

    def test_unfollow_run_error_is_logged_with_user_message_and_traceback(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        worker = FailingUnfollowWorker(
            FakeStorage(),
            events,
            browser_factory=FakePersistentBrowser,
        )
        worker.start()

        with self.assertLogs("instagram_tools.worker", level="ERROR") as captured:
            self.assertTrue(worker.submit("unfollow"))
            seen = self.wait_for_finished(events)

        worker.shutdown()
        worker.thread.join(timeout=3)

        self.assertFalse(worker.thread.is_alive())
        self.assertTrue(any(kind == "unfollow_error" for kind, _payload in seen))
        log_output = "\\n".join(captured.output)
        self.assertIn("마지막 대상: @target. HTTP 500 after write", log_output)
        self.assertIn("UnfollowRunError", log_output)


class ProfileResetErrorIsolationRegressionTestCase(unittest.TestCase):
    @staticmethod
    def app_state(running_kind: str) -> InstagramToolsApp:
        app = object.__new__(InstagramToolsApp)
        app.events = queue.Queue()
        app.running_kind = running_kind
        app.auto_status_var = FakeStringVar("대기 중")
        app.cleaner_status_var = FakeStringVar("목록 확인 완료")
        app.closing = True
        app._append_log = Mock()
        return app

    def test_profile_reset_error_does_not_mark_cleaner_tab_failed(self) -> None:
        app = self.app_state("clear_profile")
        app.events.put(("error", "프로필 삭제 실패"))

        app._drain_events()

        self.assertEqual(app.auto_status_var.get(), "대기 중")
        self.assertEqual(app.cleaner_status_var.get(), "목록 확인 완료")
        app._append_log.assert_called_once_with("프로필 삭제 실패")

    def test_cleaner_error_still_marks_cleaner_tab_failed(self) -> None:
        app = self.app_state("scan")
        app.events.put(("error", "목록 확인 실패"))

        app._drain_events()

        self.assertEqual(app.cleaner_status_var.get(), "오류로 중지")


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_browser()
    patch_worker()
    patch_app()
    write_tests()


if __name__ == "__main__":
    main()
