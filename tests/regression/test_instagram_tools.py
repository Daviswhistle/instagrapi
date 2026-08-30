from __future__ import annotations

import json
import queue
import runpy
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from apps.following_auto_liker.browser import INSTAGRAM_HOME_URL
from apps.following_auto_liker.engine import InstagramRestrictionError, ScanSummary
from apps.following_auto_liker.storage import AppConfig
from apps.instagram_tools.app import InstagramToolsApp, window_height_for_screen
from apps.instagram_tools.browser import SharedChromeBrowserSession, VerifiedFriendshipBackend
from apps.instagram_tools.worker import InstagramAutomationWorker
from apps.non_follower_cleaner.engine import (
    FriendshipRequestError,
    NonFollowerCleanerError,
    OperationStopped,
)

DESTROY_URL = "https://www.instagram.com/api/v1/friendships/destroy/2/"
SHOW_URL = "https://www.instagram.com/api/v1/friendships/show/2/"
FALLBACK_URL = "https://www.instagram.com/web/friendships/2/unfollow/"


class FakePage:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.url = "https://www.instagram.com/"
        self.evaluate_calls: list[tuple[str, dict[str, object]]] = []
        self.wait_calls: list[int] = []

    def evaluate(self, script: str, arguments: dict[str, object]) -> dict[str, object]:
        self.evaluate_calls.append((script, arguments))
        if not self.responses:
            raise AssertionError("No fake browser response remains")
        return self.responses.pop(0)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.wait_calls.append(milliseconds)


class FakeContext:
    def cookies(self, _urls: list[str]) -> list[dict[str, str]]:
        return [
            {"name": "sessionid", "value": "session"},
            {"name": "ds_user_id", "value": "1"},
            {"name": "csrftoken", "value": "csrf"},
        ]


class FakeSession:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.page = FakePage(responses)
        self.context = FakeContext()

    def _require_page(self, *, create_if_missing: bool = False) -> FakePage:
        return self.page

    @staticmethod
    def raise_browser_error(exc: Exception) -> None:
        raise exc


def response(
    url: str,
    *,
    status_code: int = 200,
    text: str = "",
    content_type: str = "text/plain; charset=utf-8",
    redirected: bool = False,
) -> dict[str, object]:
    return {
        "ok": 200 <= status_code < 300,
        "statusCode": status_code,
        "url": url,
        "redirected": redirected,
        "contentType": content_type,
        "text": text,
    }


class VerifiedUnfollowRegressionTestCase(unittest.TestCase):
    def backend(self, responses: list[dict[str, object]]) -> tuple[VerifiedFriendshipBackend, FakeSession]:
        session = FakeSession(responses)
        return VerifiedFriendshipBackend(session), session

    def test_explicit_unfollow_confirmation_needs_no_followup_read(self) -> None:
        payload = {"status": "ok", "friendship_status": {"following": False}}
        backend, session = self.backend(
            [response(DESTROY_URL, text=json.dumps(payload), content_type="application/json")]
        )

        self.assertEqual(backend.unfollow("2"), payload)
        self.assertEqual(len(session.page.evaluate_calls), 1)
        self.assertEqual(session.page.wait_calls, [])

    def test_off_origin_page_is_rejected_before_sensitive_evaluate(self) -> None:
        backend, session = self.backend([])
        session.page.url = "https://example.com/account"

        with self.assertRaises(FriendshipRequestError) as raised:
            backend.unfollow("2")

        self.assertIn("다른 사이트", str(raised.exception))
        self.assertEqual(session.page.evaluate_calls, [])

    def test_navigation_race_is_rejected_inside_browser_evaluate(self) -> None:
        backend, session = self.backend([{"originError": "https://example.com/"}])

        with self.assertRaises(FriendshipRequestError) as raised:
            backend.unfollow("2")

        self.assertIn("다른 사이트", str(raised.exception))
        self.assertEqual(len(session.page.evaluate_calls), 1)
        script, arguments = session.page.evaluate_calls[0]
        self.assertEqual(arguments["expectedOrigin"], "https://www.instagram.com")
        self.assertIn("window.location.origin", script)
        self.assertIn("fetch(requestUrl.href", script)

    def test_top_level_unfollow_confirmation_is_normalized_for_the_cleaner(self) -> None:
        payload = {"status": "ok", "following": False}
        backend, session = self.backend(
            [response(DESTROY_URL, text=json.dumps(payload), content_type="application/json")]
        )

        result = backend.unfollow("2")

        self.assertIs(result["following"], False)
        self.assertIs(result["friendship_status"]["following"], False)
        self.assertEqual(len(session.page.evaluate_calls), 1)

    def test_stop_during_ambiguous_write_finishes_current_state_check(self) -> None:
        stop_event = threading.Event()
        backend, session = self.backend(
            [
                response(DESTROY_URL),
                response(SHOW_URL, text='{"status":"ok","following":false}', content_type="application/json"),
            ]
        )
        backend.stop_event = stop_event
        original_evaluate = session.page.evaluate

        def evaluate(script: str, arguments: dict[str, object]) -> dict[str, object]:
            result = original_evaluate(script, arguments)
            if arguments["method"] == "POST":
                stop_event.set()
            return result

        session.page.evaluate = evaluate
        result = backend.unfollow("2")

        self.assertTrue(stop_event.is_set())
        self.assertIs(result["friendship_status"]["following"], False)
        methods = [arguments["method"] for _script, arguments in session.page.evaluate_calls]
        self.assertEqual(methods, ["POST", "GET"])

    def test_stop_after_unchanged_write_does_not_start_fallback(self) -> None:
        stop_event = threading.Event()
        backend, session = self.backend(
            [
                response(DESTROY_URL),
                response(SHOW_URL, text='{"status":"ok","following":true}', content_type="application/json"),
            ]
        )
        backend.stop_event = stop_event
        original_evaluate = session.page.evaluate

        def evaluate(script: str, arguments: dict[str, object]) -> dict[str, object]:
            result = original_evaluate(script, arguments)
            if arguments["method"] == "POST":
                stop_event.set()
            return result

        session.page.evaluate = evaluate

        with self.assertRaises(OperationStopped):
            backend.unfollow("2")

        methods = [arguments["method"] for _script, arguments in session.page.evaluate_calls]
        self.assertEqual(methods, ["POST", "GET"])

    def test_ambiguous_2xx_is_accepted_only_after_post_write_state_check(self) -> None:
        backend, session = self.backend(
            [
                response(DESTROY_URL),
                response(SHOW_URL, text='{"status":"ok","following":false}', content_type="application/json"),
            ]
        )

        payload = backend.unfollow("2")

        self.assertEqual(payload["_transport"]["confirmation"], "post_write_friendship_check")
        self.assertIs(payload["friendship_status"]["following"], False)
        calls = [arguments for _script, arguments in session.page.evaluate_calls]
        self.assertEqual([call["method"] for call in calls], ["POST", "GET"])
        self.assertEqual(calls[0]["path"], "/api/v1/friendships/destroy/2/")
        self.assertTrue(str(calls[1]["path"]).startswith("/api/v1/friendships/show/2/"))
        self.assertNotIn("body", calls[0])

    def test_timed_out_post_is_verified_before_reporting_failure(self) -> None:
        backend, session = self.backend(
            [
                {"networkError": "AbortError", "timedOut": True},
                response(
                    SHOW_URL,
                    text='{"status":"ok","following":false}',
                    content_type="application/json",
                ),
            ]
        )

        payload = backend.unfollow("2")

        self.assertEqual(
            payload["_transport"]["confirmation"],
            "post_timeout_friendship_check",
        )
        self.assertIs(payload["friendship_status"]["following"], False)
        methods = [arguments["method"] for _script, arguments in session.page.evaluate_calls]
        self.assertEqual(methods, ["POST", "GET"])

    def test_fallback_endpoint_runs_when_primary_write_did_not_change_state(self) -> None:
        fallback_payload = {"status": "ok", "friendship_status": {"following": False}}
        backend, session = self.backend(
            [
                response(DESTROY_URL),
                response(SHOW_URL, text='{"status":"ok","following":true}', content_type="application/json"),
                response(FALLBACK_URL, text=json.dumps(fallback_payload), content_type="application/json"),
            ]
        )

        self.assertEqual(backend.unfollow("2"), fallback_payload)
        calls = [arguments for _script, arguments in session.page.evaluate_calls]
        self.assertEqual(
            [call["path"] for call in calls],
            [
                "/api/v1/friendships/destroy/2/",
                calls[1]["path"],
                "/web/friendships/2/unfollow/",
            ],
        )
        self.assertTrue(str(calls[1]["path"]).startswith("/api/v1/friendships/show/2/"))

    def test_unfollow_is_not_reported_complete_when_both_writes_leave_following_true(self) -> None:
        backend, session = self.backend(
            [
                response(DESTROY_URL),
                response(SHOW_URL, text='{"status":"ok","following":true}', content_type="application/json"),
                response(FALLBACK_URL),
                response(SHOW_URL, text='{"status":"ok","following":true}', content_type="application/json"),
            ]
        )

        with self.assertRaises(FriendshipRequestError) as raised:
            backend.unfollow("2")

        self.assertIn("실제로 반영하지 않았습니다", str(raised.exception))
        self.assertEqual(len(session.page.evaluate_calls), 4)

    def test_redirected_success_page_is_rejected_without_state_check(self) -> None:
        backend, session = self.backend(
            [
                response(
                    "https://www.instagram.com/consent/",
                    text="<html>consent</html>",
                    content_type="text/html",
                    redirected=True,
                )
            ]
        )

        with self.assertRaises(FriendshipRequestError):
            backend.unfollow("2")

        self.assertEqual(len(session.page.evaluate_calls), 1)

    def test_activity_restriction_still_stops_before_fallback(self) -> None:
        backend, session = self.backend(
            [response(DESTROY_URL, status_code=429, text="Please wait a few minutes before you try again.")]
        )

        with self.assertRaises(NonFollowerCleanerError):
            backend.unfollow("2")

        self.assertEqual(len(session.page.evaluate_calls), 1)


class PackagedEntryRegressionTestCase(unittest.TestCase):
    def test_app_script_imports_without_package_context(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        namespace = runpy.run_path(
            str(repository_root / "apps/instagram_tools/app.py"),
            run_name="instagram_tools_entry_import_test",
        )
        self.assertIn("main", namespace)


class SharedBrowserRegressionTestCase(unittest.TestCase):
    def test_saved_session_is_revalidated_by_home_navigation(self) -> None:
        page = SimpleNamespace(url="https://www.instagram.com/?variant=following")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_called_once_with(page, INSTAGRAM_HOME_URL)

    def test_stale_saved_cookie_does_not_skip_the_login_surface(self) -> None:
        page = SimpleNamespace(url="https://www.instagram.com/?variant=following")
        messages: list[str] = []
        session = SharedChromeBrowserSession(
            Path("/tmp/instagram-tools-profile"),
            on_log=messages.append,
        )
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(side_effect=[True, False])
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_called_once_with(page, INSTAGRAM_HOME_URL)
        self.assertTrue(any("로그인하세요" in message for message in messages))
        self.assertTrue(any("로그인을 확인했습니다" in message for message in messages))
        self.assertEqual(session._page_looks_logged_out.call_count, 2)

    def test_expired_session_on_web_host_navigates_home_to_show_login(self) -> None:
        page = SimpleNamespace(url="https://www.instagram.com/?variant=following")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(side_effect=[False, True])
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_called_once_with(page, INSTAGRAM_HOME_URL)

    def test_non_instagram_start_page_navigates_home_once(self) -> None:
        page = SimpleNamespace(url="about:blank")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_called_once_with(page, INSTAGRAM_HOME_URL)

    def test_other_instagram_subdomain_navigates_to_web_home_once(self) -> None:
        page = SimpleNamespace(url="https://help.instagram.com/123456")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_called_once_with(page, INSTAGRAM_HOME_URL)


class FakeStringVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class UnifiedAppStateRegressionTestCase(unittest.TestCase):
    def test_finished_operation_finalizes_only_a_pending_stop_status(self) -> None:
        app = object.__new__(InstagramToolsApp)
        app.auto_status_var = FakeStringVar("중지 요청됨")
        app.cleaner_status_var = FakeStringVar("목록 확인 완료")
        app.stop_requested_kind = "auto_like"

        app._finalize_finished_tab_status("auto_like")

        self.assertEqual(app.auto_status_var.get(), "사용자 요청으로 중지")
        self.assertEqual(app.cleaner_status_var.get(), "목록 확인 완료")

        app.auto_status_var.set("오류로 중지")
        app.cleaner_status_var.set("중지 요청됨")
        app.stop_requested_kind = "scan"
        app._finalize_finished_tab_status("scan")

        self.assertEqual(app.auto_status_var.get(), "오류로 중지")
        self.assertEqual(app.cleaner_status_var.get(), "사용자 요청으로 중지")

    def test_window_height_fits_a_1366_by_768_laptop_display(self) -> None:
        height = window_height_for_screen(768)

        self.assertGreaterEqual(height, 620)
        self.assertLessEqual(height, 680)


class FakeStorage:
    def __init__(self) -> None:
        self.paths = SimpleNamespace(chrome_profile=Path("/tmp/instagram-tools-profile"))
        self.clear_calls = 0

    def clear_browser_profile(self) -> None:
        self.clear_calls += 1


class FakePersistentBrowser:
    instances: list[FakePersistentBrowser] = []

    def __init__(self, _profile_dir: Path, *, on_log=None) -> None:
        self.on_log = on_log or (lambda _message: None)
        self.started = 0
        self.login_checks = 0
        self.closed = 0
        self.alive = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.started += 1
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def wait_until_logged_in(self, _stop_event: threading.Event) -> None:
        self.login_checks += 1

    def close(self) -> None:
        self.closed += 1
        self.alive = False


class LoginInterruptedBrowser(FakePersistentBrowser):
    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        self.login_checks += 1
        if self.login_checks == 1:
            stop_event.set()


class StoppedScanner:
    def __init__(self, _config, *, on_like=None, **_kwargs) -> None:
        self.on_like = on_like or (lambda: None)

    def scan_once(self, _feed, _stop_event) -> ScanSummary:
        self.on_like()
        return ScanSummary(
            discovered=3,
            liked=1,
            already_liked=1,
            sponsored=1,
            stopped=True,
        )


class RestrictingScanner:
    def __init__(self, _config, *, on_like=None, **_kwargs) -> None:
        self.on_like = on_like or (lambda: None)

    def scan_once(self, _feed, _stop_event) -> ScanSummary:
        self.on_like()
        raise InstagramRestrictionError(
            "Instagram이 좋아요 활동을 제한했습니다.",
            summary=ScanSummary(discovered=2, liked=1, sponsored=1),
        )


class AutoLikeBrowser:
    @staticmethod
    def following_feed():
        return object()


class RecordingWorker(InstagramAutomationWorker):
    def _run_scan(self, _browser, _payload, _stop_event) -> None:
        return None


class FailingWorker(InstagramAutomationWorker):
    def _run_scan(self, _browser, _payload, _stop_event) -> None:
        raise RuntimeError("worker exploded")


class PersistentWorkerRegressionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        FakePersistentBrowser.instances.clear()

    @staticmethod
    def wait_for_finished(events: queue.Queue[tuple[str, object]], timeout: float = 3.0) -> list[tuple[str, object]]:
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

    def test_two_operations_reuse_one_browser_until_application_shutdown(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        storage = FakeStorage()
        worker = RecordingWorker(storage, events, browser_factory=FakePersistentBrowser)
        worker.start()

        self.assertTrue(worker.submit("scan"))
        self.wait_for_finished(events)
        self.assertTrue(worker.submit("scan"))
        self.wait_for_finished(events)
        worker.shutdown()
        worker.thread.join(timeout=3)

        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(len(FakePersistentBrowser.instances), 1)
        browser = FakePersistentBrowser.instances[0]
        self.assertEqual(browser.started, 1)
        self.assertEqual(browser.login_checks, 2)
        self.assertEqual(browser.closed, 1)

    def test_login_interruption_retains_browser_for_reuse_and_shutdown(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        storage = FakeStorage()
        worker = RecordingWorker(storage, events, browser_factory=LoginInterruptedBrowser)
        worker.start()

        self.assertTrue(worker.submit("scan"))
        first_events = self.wait_for_finished(events)

        self.assertIn(("status", "사용자 요청으로 중지했습니다."), first_events)
        self.assertEqual(len(LoginInterruptedBrowser.instances), 1)
        browser = LoginInterruptedBrowser.instances[0]
        self.assertEqual(browser.started, 1)
        self.assertEqual(browser.login_checks, 1)
        self.assertEqual(browser.closed, 0)
        self.assertTrue(browser.is_alive())

        self.assertTrue(worker.submit("scan"))
        self.wait_for_finished(events)
        worker.shutdown()
        worker.thread.join(timeout=3)

        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(len(LoginInterruptedBrowser.instances), 1)
        self.assertEqual(browser.started, 1)
        self.assertEqual(browser.login_checks, 2)
        self.assertEqual(browser.closed, 1)

    def test_profile_clear_closes_browser_and_next_operation_uses_a_fresh_instance(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        storage = FakeStorage()
        worker = RecordingWorker(storage, events, browser_factory=FakePersistentBrowser)
        worker.start()

        self.assertTrue(worker.submit("scan"))
        self.wait_for_finished(events)
        first_browser = FakePersistentBrowser.instances[0]

        self.assertTrue(worker.submit("clear_profile"))
        clear_events = self.wait_for_finished(events)

        self.assertIn(("profile_cleared", None), clear_events)
        self.assertEqual(storage.clear_calls, 1)
        self.assertEqual(first_browser.closed, 1)
        self.assertEqual(len(FakePersistentBrowser.instances), 1)

        self.assertTrue(worker.submit("scan"))
        self.wait_for_finished(events)
        worker.shutdown()
        worker.thread.join(timeout=3)

        self.assertFalse(worker.thread.is_alive())
        self.assertEqual(len(FakePersistentBrowser.instances), 2)
        self.assertEqual(FakePersistentBrowser.instances[1].started, 1)
        self.assertEqual(FakePersistentBrowser.instances[1].closed, 1)

    def test_stopped_auto_like_publishes_partial_summary_and_timestamp(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        worker = InstagramAutomationWorker(
            FakeStorage(),
            events,
            browser_factory=FakePersistentBrowser,
        )

        with patch("apps.instagram_tools.worker.FollowingFeedScanner", StoppedScanner):
            worker._run_auto_like(
                AutoLikeBrowser(),
                {"config": AppConfig().validate()},
                threading.Event(),
            )

        captured: list[tuple[str, object]] = []
        while not events.empty():
            captured.append(events.get_nowait())

        logs = [payload for kind, payload in captured if kind == "log"]
        statuses = [payload for kind, payload in captured if kind == "auto_status"]
        self.assertTrue(
            any(
                isinstance(message, str) and "중지 전 처리 결과:" in message and "좋아요 1개" in message
                for message in logs
            )
        )
        self.assertEqual(statuses[-1]["message"], "중지 전 결과 · 이번 1개 · 누적 1개")
        self.assertEqual(statuses[-1]["session_likes"], 1)
        self.assertTrue(statuses[-1]["last_scan_at"])

    def test_restriction_publishes_partial_auto_like_summary_and_timestamp(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        worker = InstagramAutomationWorker(
            FakeStorage(),
            events,
            browser_factory=FakePersistentBrowser,
        )

        with patch("apps.instagram_tools.worker.FollowingFeedScanner", RestrictingScanner):
            with self.assertRaises(InstagramRestrictionError):
                worker._run_auto_like(
                    AutoLikeBrowser(),
                    {"config": AppConfig().validate()},
                    threading.Event(),
                )

        captured: list[tuple[str, object]] = []
        while not events.empty():
            captured.append(events.get_nowait())

        logs = [payload for kind, payload in captured if kind == "log"]
        statuses = [payload for kind, payload in captured if kind == "auto_status"]
        self.assertTrue(
            any(
                isinstance(message, str) and "제한 감지 전 처리 결과:" in message and "좋아요 1개" in message
                for message in logs
            )
        )
        self.assertEqual(statuses[-1]["message"], "활동 제한으로 중지 · 누적 1개")
        self.assertEqual(statuses[-1]["session_likes"], 1)
        self.assertTrue(statuses[-1]["last_scan_at"])

    def test_unexpected_worker_exception_is_logged_with_traceback(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        storage = FakeStorage()
        worker = FailingWorker(storage, events, browser_factory=FakePersistentBrowser)
        worker.start()

        with self.assertLogs("instagram_tools.worker", level="ERROR") as captured:
            self.assertTrue(worker.submit("scan"))
            self.wait_for_finished(events)

        worker.shutdown()
        worker.thread.join(timeout=3)

        self.assertFalse(worker.thread.is_alive())
        self.assertIn("RuntimeError: worker exploded", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
