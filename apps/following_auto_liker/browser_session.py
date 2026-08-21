from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .browser_common import safe_error_text
from .browser_constants import INSTAGRAM_HOME_URL
from .engine_shared import (
    BrowserClosedError,
    ChromeLaunchError,
    PlaywrightMissingError,
)

class ChromeBrowserSession:
    def __init__(self, profile_dir: Path, *, on_log=None):
        self.profile_dir = Path(profile_dir)
        self.on_log = on_log or (lambda _message: None)
        self._playwright: Any = None
        self.context: Any = None
        self.page: Any = None

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PlaywrightMissingError(
                "브라우저 자동화 구성요소가 빠져 있습니다. 배포된 실행 파일을 다시 내려받아 주세요."
            ) from exc

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._playwright = sync_playwright().start()
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="chrome",
                headless=False,
                no_viewport=True,
                accept_downloads=False,
                args=["--start-maximized"],
            )
        except Exception as exc:
            self._stop_playwright_only()
            self._raise_launch_error(exc)

        pages = [page for page in self.context.pages if not page.is_closed()]
        self.page = pages[0] if pages else self.context.new_page()
        self.page.set_default_timeout(5_000)
        self.page.set_default_navigation_timeout(60_000)
        self.on_log("자동 좋아요 전용 Chrome 창을 열었습니다.")

    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        page = self._require_page()
        self._safe_goto(page, INSTAGRAM_HOME_URL)

        if self._has_session_cookie() and not self._page_looks_logged_out(page):
            self.on_log("전용 Chrome에 저장된 Instagram 로그인을 사용합니다.")
            return

        self.on_log(
            "처음 한 번만 열린 Chrome 창에서 Instagram에 로그인하세요. "
            "앱에는 아이디나 비밀번호를 입력하지 않습니다."
        )
        while not stop_event.is_set():
            page = self._require_page(create_if_missing=True)
            if self._has_session_cookie() and not self._page_looks_logged_out(page):
                self.on_log("Instagram 로그인을 확인했습니다. 다음 실행에도 이 로그인 상태를 사용합니다.")
                return
            stop_event.wait(2)

    def following_feed(self) -> PlaywrightFollowingFeed:
        from .browser_following import PlaywrightFollowingFeed

        if not self.context:
            raise BrowserClosedError("Chrome이 실행되지 않았습니다.")
        return PlaywrightFollowingFeed(self)

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            finally:
                self.context = None
                self.page = None
        self._stop_playwright_only()

    def _stop_playwright_only(self) -> None:
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            finally:
                self._playwright = None

    def _has_session_cookie(self) -> bool:
        if self.context is None:
            return False
        try:
            cookies = self.context.cookies([INSTAGRAM_HOME_URL])
        except Exception as exc:
            self.raise_browser_error(exc)
        return any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies)

    def _page_looks_logged_out(self, page) -> bool:
        try:
            if "/accounts/login" in page.url:
                return True
            return page.locator('input[name="username"]').count() > 0
        except Exception as exc:
            self.raise_browser_error(exc)

    def _require_page(self, *, create_if_missing: bool = False):
        if self.context is None:
            raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작해 주세요.")
        if self.page is not None and not self.page.is_closed():
            return self.page

        try:
            pages = [page for page in self.context.pages if not page.is_closed()]
        except Exception as exc:
            self.raise_browser_error(exc)
        if pages:
            self.page = pages[0]
            return self.page
        if create_if_missing:
            try:
                self.page = self.context.new_page()
            except Exception as exc:
                self.raise_browser_error(exc)
            return self.page
        raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작해 주세요.")

    def _safe_goto(self, page, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            self.raise_browser_error(exc)

    @staticmethod
    def _raise_launch_error(exc: Exception) -> None:
        detail = str(exc).lower()
        if any(
            phrase in detail
            for phrase in (
                "executable doesn't exist",
                "chrome distribution",
                "channel 'chrome'",
                "could not find chrome",
            )
        ):
            raise ChromeLaunchError(
                "Google Chrome을 찾지 못했습니다. Chrome을 설치한 뒤 다시 시작하세요."
            ) from exc
        if any(
            phrase in detail
            for phrase in (
                "processsingleton",
                "user data directory is already in use",
                "profile is in use",
            )
        ):
            raise ChromeLaunchError(
                "자동 좋아요 전용 Chrome 프로필이 이미 사용 중입니다. "
                "이 앱이 열었던 Chrome 창을 모두 닫은 뒤 다시 시작하세요."
            ) from exc
        raise ChromeLaunchError(f"Chrome을 열지 못했습니다: {safe_error_text(exc)}") from exc

    @staticmethod
    def raise_browser_error(exc: Exception) -> None:
        detail = str(exc).lower()
        if any(phrase in detail for phrase in ("target page", "browser has been closed", "target closed")):
            raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작해 주세요.") from exc
        raise exc

