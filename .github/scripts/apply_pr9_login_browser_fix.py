from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_worker() -> None:
    path = Path("apps/instagram_tools/worker.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''                    else:
                        browser = self._ensure_browser(browser, stop_event)
                        if command.kind == "auto_like":
''',
        '''                    else:
                        browser = self._ensure_browser(browser)
                        self._wait_until_logged_in(browser, stop_event)
                        if command.kind == "auto_like":
''',
        "assign browser before interruptible login wait",
    )
    text = replace_once(
        text,
        '''    def _ensure_browser(
        self,
        browser: SharedChromeBrowserSession | None,
        stop_event: threading.Event,
    ) -> SharedChromeBrowserSession:
        if browser is None or not browser.is_alive():
            browser = self._discard_browser(browser)
            browser = self.browser_factory(
                self.storage.paths.chrome_profile,
                on_log=lambda message: self.events.put(("log", message.replace("자동 좋아요", "Instagram 도구"))),
            )
            self.events.put(("status", "Chrome을 여는 중입니다."))
            browser.start()
        self.events.put(("status", "Chrome에서 Instagram 로그인을 확인하고 있습니다."))
        browser.wait_until_logged_in(stop_event)
        if stop_event.is_set():
            raise OperationStopped("사용자가 Instagram 작업을 중지했습니다.")
        return browser
''',
        '''    def _ensure_browser(
        self,
        browser: SharedChromeBrowserSession | None,
    ) -> SharedChromeBrowserSession:
        if browser is not None and browser.is_alive():
            return browser

        self._discard_browser(browser)
        browser = self.browser_factory(
            self.storage.paths.chrome_profile,
            on_log=lambda message: self.events.put(("log", message.replace("자동 좋아요", "Instagram 도구"))),
        )
        self.events.put(("status", "Chrome을 여는 중입니다."))
        try:
            browser.start()
        except Exception:
            self._discard_browser(browser)
            raise
        return browser

    def _wait_until_logged_in(
        self,
        browser: SharedChromeBrowserSession,
        stop_event: threading.Event,
    ) -> None:
        self.events.put(("status", "Chrome에서 Instagram 로그인을 확인하고 있습니다."))
        browser.wait_until_logged_in(stop_event)
        if stop_event.is_set():
            raise OperationStopped("사용자가 Instagram 작업을 중지했습니다.")
''',
        "split browser ownership from login wait",
    )
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/regression/test_instagram_tools.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''class RestrictingScanner:
''',
        '''class LoginInterruptedBrowser(FakePersistentBrowser):
    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        self.login_checks += 1
        if self.login_checks == 1:
            stop_event.set()


class RestrictingScanner:
''',
        "interrupted login browser fixture",
    )
    text = replace_once(
        text,
        '''    def test_profile_clear_closes_browser_and_next_operation_uses_a_fresh_instance(self) -> None:
''',
        '''    def test_login_interruption_retains_browser_for_reuse_and_shutdown(self) -> None:
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
''',
        "interrupted login browser lifecycle regression",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_worker()
    patch_tests()


if __name__ == "__main__":
    main()
