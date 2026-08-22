from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable
from urllib.parse import parse_qs, urlparse

from .model import (
    BrowserClosedError,
    ChromeLaunchError,
    FollowingFeedUnavailableError,
    LikeState,
    LoginRequiredError,
    PlaywrightMissingError,
    normalize_post_key,
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

INSTAGRAM_HOME_URL = "https://www.instagram.com/"
FOLLOWING_URL = "https://www.instagram.com/?variant=following"
LIKE_LABELS = ("Like", "좋아요")
UNLIKE_LABELS = ("Unlike", "좋아요 취소")
SPONSORED_LABELS = ("Sponsored", "광고")
RECOMMENDED_LABELS = (
    "Suggested for you",
    "Suggested post",
    "Because you follow",
    "Recommended for you",
    "회원님을 위한 추천",
    "추천 게시물",
)
CAUGHT_UP_LABELS = (
    "You're all caught up",
    "You’re all caught up",
    "모두 확인했습니다",
    "최신 게시물을 모두 확인했습니다",
    "새 게시물을 모두 확인했습니다",
)
RESTRICTION_PHRASES = (
    "try again later",
    "we restrict certain activity",
    "action blocked",
    "temporarily blocked",
    "please wait a few minutes",
    "feedback_required",
    "잠시 후 다시 시도",
    "일부 활동을 제한",
    "활동을 제한",
    "작업이 차단",
    "일시적으로 차단",
    "몇 분 후 다시 시도",
)
RESERVED_PATHS = {
    "about",
    "accounts",
    "api",
    "challenge",
    "developer",
    "direct",
    "directory",
    "emails",
    "explore",
    "legal",
    "oauth",
    "p",
    "privacy",
    "push",
    "reel",
    "reels",
    "session",
    "stories",
    "terms",
    "tv",
    "web",
}


def _is_instagram_host(host: str) -> bool:
    host = host.lower().split(":", 1)[0]
    return host == "instagram.com" or host.endswith(".instagram.com")


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class ChromeSession:
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
                "브라우저 자동화 구성요소가 없습니다. 배포된 실행 파일을 다시 내려받아 주세요."
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
            pages = [page for page in self.context.pages if not page.is_closed()]
            self.page = pages[0] if pages else self.context.new_page()
            self.page.set_default_timeout(5_000)
            self.page.set_default_navigation_timeout(60_000)
        except Exception as exc:
            self.close()
            detail = str(exc).lower()
            if any(term in detail for term in ("executable doesn't exist", "channel 'chrome'", "could not find chrome")):
                raise ChromeLaunchError("Google Chrome을 찾지 못했습니다. Chrome을 설치한 뒤 다시 시작하세요.") from exc
            if any(term in detail for term in ("processsingleton", "profile is in use", "user data directory is already in use")):
                raise ChromeLaunchError(
                    "자동 좋아요 전용 Chrome 프로필이 이미 사용 중입니다. 열린 전용 Chrome 창을 모두 닫고 다시 시작하세요."
                ) from exc
            raise ChromeLaunchError(f"Chrome을 열지 못했습니다: {_normalized_text(str(exc))[:240]}") from exc
        self.on_log("자동 좋아요 전용 Chrome 창을 열었습니다.")

    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        page = self._require_page(create=True)
        self._goto(page, INSTAGRAM_HOME_URL)
        if self._has_session_cookie() and not self._looks_logged_out(page):
            self.on_log("저장된 Instagram 로그인 상태를 사용합니다.")
            return
        self.on_log("열린 Chrome 창에서 Instagram에 직접 로그인하세요. 앱은 비밀번호를 받지 않습니다.")
        while not stop_event.is_set():
            page = self._require_page(create=True)
            if self._has_session_cookie() and not self._looks_logged_out(page):
                self.on_log("Instagram 로그인을 확인했습니다.")
                return
            stop_event.wait(2)
        raise LoginRequiredError("로그인 전에 중지되었습니다.")

    def following_feed(self) -> PlaywrightFollowingFeed:
        if self.context is None:
            raise BrowserClosedError("Chrome이 실행되어 있지 않습니다.")
        return PlaywrightFollowingFeed(self)

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
        self.context = None
        self.page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None

    def _require_page(self, *, create: bool = False) -> Page:
        if self.context is None:
            raise BrowserClosedError("Chrome이 닫혔습니다.")
        if self.page is not None and not self.page.is_closed():
            return self.page
        pages = [page for page in self.context.pages if not page.is_closed()]
        if pages:
            self.page = pages[0]
            return self.page
        if create:
            try:
                self.page = self.context.new_page()
                return self.page
            except Exception as exc:
                self._raise_browser_error(exc)
        raise BrowserClosedError("Chrome 창이 닫혔습니다.")

    def _has_session_cookie(self) -> bool:
        if self.context is None:
            return False
        try:
            cookies = self.context.cookies([INSTAGRAM_HOME_URL])
        except Exception as exc:
            self._raise_browser_error(exc)
        return any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies)

    def _looks_logged_out(self, page: Page) -> bool:
        try:
            if "/accounts/login" in page.url:
                return True
            return page.locator('input[name="username"]').count() > 0
        except Exception as exc:
            self._raise_browser_error(exc)

    def _goto(self, page: Page, url: str) -> None:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            self._raise_browser_error(exc)

    @staticmethod
    def _raise_browser_error(exc: Exception) -> None:
        detail = str(exc).lower()
        if any(term in detail for term in ("target page", "browser has been closed", "target closed", "context closed")):
            raise BrowserClosedError("Chrome 창이 닫혔습니다. 다시 시작하세요.") from exc
        raise exc


class PlaywrightFollowingFeed:
    def __init__(self, session: ChromeSession):
        self.session = session

    @property
    def page(self) -> Page:
        return self.session._require_page(create=True)

    def open_following(self) -> None:
        page = self.page
        self.session._goto(page, FOLLOWING_URL)
        try:
            page.wait_for_timeout(1_500)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
        except Exception as exc:
            self.session._raise_browser_error(exc)
        if self.session._looks_logged_out(page):
            raise LoginRequiredError("Instagram 로그인이 풀렸습니다. 전용 Chrome에서 다시 로그인하세요.")
        parsed = urlparse(page.url)
        if not _is_instagram_host(parsed.hostname or ""):
            raise FollowingFeedUnavailableError("Instagram이 아닌 페이지로 이동되어 자동화를 중지했습니다.")
        variant = parse_qs(parsed.query).get("variant")
        if variant != ["following"]:
            raise FollowingFeedUnavailableError(
                "Instagram이 시간순 팔로잉 피드를 열지 않았습니다. 웹 화면이 변경되었을 수 있어 자동화를 중지했습니다."
            )
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(800)
        except Exception as exc:
            self.session._raise_browser_error(exc)

    def posts(self) -> Iterable[PlaywrightFeedPost]:
        try:
            articles = self.page.locator("article")
            return [PlaywrightFeedPost(self.session, articles.nth(index)) for index in range(articles.count())]
        except Exception as exc:
            self.session._raise_browser_error(exc)

    def scroll_for_more(self) -> bool:
        try:
            before = self.page.evaluate(
                "() => ({y: window.scrollY, h: document.documentElement.scrollHeight, n: document.querySelectorAll('article').length})"
            )
            self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            self.page.wait_for_timeout(1_800)
            after = self.page.evaluate(
                "() => ({y: window.scrollY, h: document.documentElement.scrollHeight, n: document.querySelectorAll('article').length})"
            )
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return after["y"] > before["y"] + 20 or after["h"] > before["h"] or after["n"] > before["n"]

    def restriction_message(self) -> str | None:
        page = self.page
        selectors = (
            '[role="dialog"]:visible',
            '[role="alert"]:visible',
            '[aria-live="assertive"]:visible',
            '[aria-live="polite"]:visible',
        )
        texts: list[str] = []
        try:
            for selector in selectors:
                nodes = page.locator(selector)
                for index in range(min(nodes.count(), 8)):
                    text = _normalized_text(nodes.nth(index).inner_text(timeout=1_500))
                    if text:
                        texts.append(text)
            if not texts and page.locator("article").count() == 0:
                texts.append(_normalized_text(page.locator("body").inner_text(timeout=2_000)))
        except Exception as exc:
            self.session._raise_browser_error(exc)
        for text in texts:
            lower = text.lower()
            if any(phrase in lower for phrase in RESTRICTION_PHRASES):
                return text[:300]
        return None

    def is_caught_up(self) -> bool:
        try:
            regions = self.page.locator('h1, h2, h3, [role="status"], [aria-live="polite"]')
            for index in range(min(regions.count(), 40)):
                text = _normalized_text(regions.nth(index).inner_text(timeout=800)).lower()
                if text and any(label.lower() in text for label in CAUGHT_UP_LABELS):
                    return True
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return False


class PlaywrightFeedPost:
    def __init__(self, session: ChromeSession, article: Locator):
        self.session = session
        self.article = article
        self._key: str | None = None
        self._username: str | None = None
        self._reason: str | None | object = _UNSET

    @property
    def key(self) -> str:
        if self._key is None:
            self._key = self._extract_key()
        return self._key

    @property
    def username(self) -> str:
        if self._username is None:
            self._username = self._extract_username()
        return self._username

    @property
    def exclusion_reason(self) -> str | None:
        if self._reason is _UNSET:
            self._reason = self._detect_exclusion()
        return self._reason if isinstance(self._reason, str) else None

    @property
    def like_state(self) -> LikeState:
        if self._find_main_action(UNLIKE_LABELS) is not None:
            return "liked"
        if self._find_main_action(LIKE_LABELS) is not None:
            return "unliked"
        return "unknown"

    def click_like(self) -> bool:
        control = self._find_main_action(LIKE_LABELS)
        if control is None:
            return self.like_state == "liked"
        try:
            control.scroll_into_view_if_needed(timeout=4_000)
            control.click(timeout=5_000)
            self.session.page.wait_for_timeout(700)
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return self.like_state == "liked"

    def _extract_key(self) -> str:
        try:
            links = self.article.locator('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]')
            for index in range(min(links.count(), 20)):
                key = normalize_post_key(links.nth(index).get_attribute("href") or "")
                if key:
                    return key
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return ""

    def _extract_username(self) -> str:
        try:
            links = self.article.locator('header a[href^="/"], a[href^="/"]')
            for index in range(min(links.count(), 20)):
                path = urlparse(links.nth(index).get_attribute("href") or "").path
                parts = [part for part in path.split("/") if part]
                if len(parts) == 1 and parts[0].lower() not in RESERVED_PATHS:
                    return parts[0]
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return ""

    def _detect_exclusion(self) -> str | None:
        try:
            article_box = self.article.bounding_box()
            if not article_box:
                return None
            limit_y = article_box["y"] + max(160, article_box["height"] * 0.4)
            for reason, labels in (("sponsored", SPONSORED_LABELS), ("recommended", RECOMMENDED_LABELS)):
                for label in labels:
                    matches = self.article.get_by_text(label, exact=True)
                    for index in range(min(matches.count(), 5)):
                        node = matches.nth(index)
                        if node.is_visible() and (box := node.bounding_box()) and box["y"] <= limit_y:
                            return reason
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return None

    def _find_main_action(self, labels: tuple[str, ...]) -> Locator | None:
        candidates: list[tuple[float, Any]] = []
        try:
            article_box = self.article.bounding_box()
            if not article_box:
                return None
            for label in labels:
                escaped = label.replace('"', '\\"')
                locator = self.article.locator(
                    f'button:has(svg[aria-label="{escaped}"]), [role="button"]:has(svg[aria-label="{escaped}"])'
                )
                for index in range(min(locator.count(), 12)):
                    node = locator.nth(index)
                    if not node.is_visible():
                        continue
                    if node.evaluate("el => Boolean(el.closest('li'))"):
                        continue
                    box = node.bounding_box()
                    if not box or box["y"] > article_box["y"] + article_box["height"] * 0.9:
                        continue
                    candidates.append((box["width"] * box["height"], node))
        except Exception as exc:
            self.session._raise_browser_error(exc)
        return max(candidates, key=lambda item: item[0])[1] if candidates else None


_UNSET = object()
