from __future__ import annotations

from typing import TYPE_CHECKING, Iterable
from urllib.parse import parse_qs, urlparse

from .browser_common import truncate_text
from .browser_constants import (
    CAUGHT_UP_PHRASES,
    DISMISS_BUTTON_LABELS,
    FOLLOWING_FEED_URL,
    RESTRICTION_PHRASES,
)
from .browser_post import PlaywrightFeedPost
from .engine_shared import FollowingFeedUnavailableError, LoginRequiredError

if TYPE_CHECKING:
    from .browser_session import ChromeBrowserSession

class PlaywrightFollowingFeed:
    def __init__(self, session: ChromeBrowserSession):
        self.session = session

    @property
    def page(self):
        return self.session._require_page()

    def open_following(self) -> None:
        if not self.session._has_session_cookie():
            raise LoginRequiredError(
                "Instagram 로그인이 풀렸습니다. 전용 Chrome 데이터 지우기 후 다시 로그인하거나 다시 시작하세요."
            )

        page = self.page
        self.session._safe_goto(page, FOLLOWING_FEED_URL)
        try:
            page.wait_for_timeout(2_000)
        except Exception as exc:
            self.session.raise_browser_error(exc)

        if self._looks_logged_out():
            raise LoginRequiredError(
                "Instagram 로그인이 풀렸습니다. 열린 Chrome에서 다시 로그인한 뒤 앱을 다시 시작하세요."
            )
        if not self._is_following_url(page.url):
            raise FollowingFeedUnavailableError(
                "Instagram이 시간순 팔로잉 피드를 열지 않았습니다. 웹 화면이 변경되었을 수 있어 "
                "일반 홈 피드를 잘못 처리하지 않도록 중지했습니다."
            )
        self._dismiss_common_dialogs()
        try:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(1_500)
        except Exception as exc:
            self.session.raise_browser_error(exc)

    def posts(self) -> Iterable[PlaywrightFeedPost]:
        try:
            articles = self.page.locator("article")
            count = articles.count()
        except Exception as exc:
            self.session.raise_browser_error(exc)

        posts = []
        for index in range(count):
            post = PlaywrightFeedPost(self.session, articles.nth(index))
            if post.key:
                posts.append(post)
        return posts

    def scroll_for_more(self) -> bool:
        page = self.page
        try:
            before = page.evaluate(
                """() => ({
                    y: window.scrollY,
                    height: document.documentElement.scrollHeight,
                    articles: document.querySelectorAll('article').length
                })"""
            )
            page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 900))")
            page.wait_for_timeout(1_800)
            after = page.evaluate(
                """() => ({
                    y: window.scrollY,
                    height: document.documentElement.scrollHeight,
                    articles: document.querySelectorAll('article').length
                })"""
            )
        except Exception as exc:
            self.session.raise_browser_error(exc)

        return bool(
            after["y"] > before["y"] + 20
            or after["height"] > before["height"]
            or after["articles"] > before["articles"]
        )

    def restriction_message(self) -> str | None:
        try:
            dialogs = self.page.locator('[role="dialog"]:visible')
            texts = []
            for index in range(min(dialogs.count(), 5)):
                texts.append(dialogs.nth(index).inner_text(timeout=2_000))
            if not texts:
                texts.append(self.page.locator("body").inner_text(timeout=2_000))
        except Exception as exc:
            self.session.raise_browser_error(exc)

        for text in texts:
            normalized = " ".join(str(text).split()).lower()
            for phrase in RESTRICTION_PHRASES:
                if phrase in normalized:
                    return truncate_text(text)
        return None

    def is_caught_up(self) -> bool:
        try:
            body_text = self.page.locator("body").inner_text(timeout=2_000)
        except Exception as exc:
            self.session.raise_browser_error(exc)
        normalized = " ".join(body_text.split()).lower()
        return any(phrase in normalized for phrase in CAUGHT_UP_PHRASES)

    @staticmethod
    def _is_following_url(url: str) -> bool:
        parsed = urlparse(str(url or ""))
        return parsed.netloc.endswith("instagram.com") and parse_qs(parsed.query).get("variant") == ["following"]

    def _looks_logged_out(self) -> bool:
        page = self.page
        try:
            if "/accounts/login" in page.url:
                return True
            return page.locator('input[name="username"]').count() > 0
        except Exception as exc:
            self.session.raise_browser_error(exc)

    def _dismiss_common_dialogs(self) -> None:
        for label in DISMISS_BUTTON_LABELS:
            try:
                button = self.page.get_by_role("button", name=label, exact=True)
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=2_000)
                    self.page.wait_for_timeout(300)
            except Exception:
                continue

