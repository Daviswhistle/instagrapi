from __future__ import annotations

import json
import queue
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from apps.following_auto_liker.browser import INSTAGRAM_HOME_URL
from apps.instagram_tools.browser import SharedChromeBrowserSession, VerifiedFriendshipBackend
from apps.instagram_tools.worker import InstagramAutomationWorker
from apps.non_follower_cleaner.engine import FriendshipRequestError, NonFollowerCleanerError

DESTROY_URL = "https://www.instagram.com/api/v1/friendships/destroy/2/"
SHOW_URL = "https://www.instagram.com/api/v1/friendships/show/2/"
FALLBACK_URL = "https://www.instagram.com/web/friendships/2/unfollow/"


class FakePage:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
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


class SharedBrowserRegressionTestCase(unittest.TestCase):
    def test_logged_in_instagram_page_is_reused_without_home_reload(self) -> None:
        page = SimpleNamespace(url="https://www.instagram.com/?variant=following")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_not_called()

    def test_non_instagram_start_page_navigates_home_once(self) -> None:
        page = SimpleNamespace(url="about:blank")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_called_once_with(page, INSTAGRAM_HOME_URL)


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


class RecordingWorker(InstagramAutomationWorker):
    def _run_scan(self, _browser, _payload, _stop_event) -> None:
        return None


class PersistentWorkerRegressionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        FakePersistentBrowser.instances.clear()

    @staticmethod
    def wait_for_finished(events: queue.Queue[tuple[str, object]], timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                kind, _payload = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if kind == "operation_finished":
                return
        raise AssertionError("worker did not finish operation")

    def test_two_operations_reuse_one_browser_until_application_shutdown(self) -> None:
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        storage = SimpleNamespace(paths=SimpleNamespace(chrome_profile=Path("/tmp/instagram-tools-profile")))
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


if __name__ == "__main__":
    unittest.main()
