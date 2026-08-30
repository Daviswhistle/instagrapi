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
        '''class SharedChromeBrowserSession(ChromeBrowserSession):
    """Keep one Chrome context alive and avoid navigating to Home twice."""
''',
        '''class SharedChromeBrowserSession(ChromeBrowserSession):
    """Keep one Chrome context alive while validating each saved login."""
''',
        "shared browser docstring",
    )
    text = replace_once(
        text,
        '''    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        page = self._require_page(create_if_missing=True)

        # A persistent context often already has an authenticated Instagram page.
        # Reusing it avoids the visible blank-page -> Home -> Home reload sequence.
        navigated_home = False
        if not is_instagram_web_url(page.url):
            self._safe_goto(page, INSTAGRAM_HOME_URL)
            navigated_home = True

        if self._has_session_cookie() and not self._page_looks_logged_out(page):
            self.on_log("전용 Chrome에 저장된 Instagram 로그인을 사용합니다.")
            return

        if not navigated_home:
            self._safe_goto(page, INSTAGRAM_HOME_URL)

        self.on_log(
            "처음 한 번만 열린 Chrome 창에서 Instagram에 로그인하세요. 앱에는 아이디나 비밀번호를 입력하지 않습니다."
        )
        while not stop_event.is_set():
            page = self._require_page(create_if_missing=True)
            if self._has_session_cookie() and not self._page_looks_logged_out(page):
                self.on_log("Instagram 로그인을 확인했습니다. 다음 실행에도 이 로그인 상태를 사용합니다.")
                return
            stop_event.wait(2)
''',
        '''    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        page = self._require_page(create_if_missing=True)

        # A locally retained sessionid is not proof that Instagram still accepts
        # the session. Reload Home once per operation so a server-side expiry is
        # rendered as a login surface instead of reusing a stale feed forever.
        self._safe_goto(page, INSTAGRAM_HOME_URL)

        if self._has_session_cookie() and not self._page_looks_logged_out(page):
            self.on_log("전용 Chrome에 저장된 Instagram 로그인을 사용합니다.")
            return

        self.on_log(
            "처음 한 번만 열린 Chrome 창에서 Instagram에 로그인하세요. 앱에는 아이디나 비밀번호를 입력하지 않습니다."
        )
        while not stop_event.is_set():
            page = self._require_page(create_if_missing=True)
            if self._has_session_cookie() and not self._page_looks_logged_out(page):
                self.on_log("Instagram 로그인을 확인했습니다. 다음 실행에도 이 로그인 상태를 사용합니다.")
                return
            stop_event.wait(2)
''',
        "server-validated saved session",
    )
    path.write_text(text, encoding="utf-8")


def patch_worker() -> None:
    path = Path("apps/instagram_tools/worker.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''            last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
            if summary.stopped:
                break

            summary_message = FollowingAutoLiker._summary_message(summary)
            self.events.put(("log", summary_message))
''',
        '''            last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
            summary_message = FollowingAutoLiker._summary_message(summary)
            if summary.stopped:
                self.events.put(("log", f"중지 전 처리 결과: {summary_message}"))
                self.events.put(
                    (
                        "auto_status",
                        {
                            "message": f"중지 전 결과 · 이번 {summary.liked}개 · 누적 {session_likes}개",
                            "session_likes": session_likes,
                            "last_scan_at": last_scan_at,
                        },
                    )
                )
                break

            self.events.put(("log", summary_message))
''',
        "publish stopped auto-like snapshot",
    )
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/regression/test_instagram_tools.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''class SharedBrowserRegressionTestCase(unittest.TestCase):
    def test_logged_in_instagram_page_is_reused_without_home_reload(self) -> None:
        page = SimpleNamespace(url="https://www.instagram.com/?variant=following")
        session = SharedChromeBrowserSession(Path("/tmp/instagram-tools-profile"))
        session._require_page = Mock(return_value=page)
        session._has_session_cookie = Mock(return_value=True)
        session._page_looks_logged_out = Mock(return_value=False)
        session._safe_goto = Mock()

        session.wait_until_logged_in(threading.Event())

        session._safe_goto.assert_not_called()

    def test_expired_session_on_web_host_navigates_home_to_show_login(self) -> None:
''',
        '''class SharedBrowserRegressionTestCase(unittest.TestCase):
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
''',
        "saved-session regressions",
    )
    text = replace_once(
        text,
        '''class RestrictingScanner:
''',
        '''class StoppedScanner:
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
''',
        "stopped scanner fixture",
    )
    text = replace_once(
        text,
        '''    def test_restriction_publishes_partial_auto_like_summary_and_timestamp(self) -> None:
''',
        '''    def test_stopped_auto_like_publishes_partial_summary_and_timestamp(self) -> None:
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
                isinstance(message, str)
                and "중지 전 처리 결과:" in message
                and "좋아요 1개" in message
                for message in logs
            )
        )
        self.assertEqual(statuses[-1]["message"], "중지 전 결과 · 이번 1개 · 누적 1개")
        self.assertEqual(statuses[-1]["session_likes"], 1)
        self.assertTrue(statuses[-1]["last_scan_at"])

    def test_restriction_publishes_partial_auto_like_summary_and_timestamp(self) -> None:
''',
        "stopped auto-like snapshot regression",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_browser()
    patch_worker()
    patch_tests()


if __name__ == "__main__":
    main()
