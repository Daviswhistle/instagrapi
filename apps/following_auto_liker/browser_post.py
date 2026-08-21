from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .browser_constants import (
    LIKE_LABELS,
    RECOMMENDATION_LABELS,
    RESERVED_PATHS,
    SPONSORED_LABELS,
    UNLIKE_LABELS,
    _UNSET,
)
from .engine_shared import LikeState, normalize_post_key

if TYPE_CHECKING:
    from .browser_session import ChromeBrowserSession

class PlaywrightFeedPost:
    def __init__(self, session: ChromeBrowserSession, article):
        self.session = session
        self.article = article
        self._key: str | None = None
        self._username: str | None = None
        self._exclusion_reason: str | None | object = _UNSET

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
        if self._exclusion_reason is _UNSET:
            self._exclusion_reason = self._detect_exclusion_reason()
        return self._exclusion_reason if isinstance(self._exclusion_reason, str) else None

    @property
    def is_sponsored(self) -> bool:
        """Compatibility property for callers written against the first desktop prototype."""
        return self.exclusion_reason == "sponsored"

    @property
    def like_state(self) -> LikeState:
        if self._find_control(UNLIKE_LABELS) is not None:
            return "liked"
        if self._find_control(LIKE_LABELS) is not None:
            return "unliked"
        return "unknown"

    def click_like(self) -> bool:
        control = self._find_control(LIKE_LABELS)
        if control is None:
            return self.like_state == "liked"
        try:
            control.scroll_into_view_if_needed(timeout=5_000)
            control.click(timeout=5_000)
            self.session.page.wait_for_timeout(800)
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return self.like_state == "liked"

    def _extract_key(self) -> str:
        try:
            links = self.article.locator('a[href*="/p/"], a[href*="/reel/"], a[href*="/tv/"]')
            for index in range(min(links.count(), 12)):
                href = links.nth(index).get_attribute("href") or ""
                key = normalize_post_key(href)
                if key.startswith(("/p/", "/reel/", "/tv/")):
                    return key
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return ""

    def _extract_username(self) -> str:
        try:
            links = self.article.locator('a[href^="/"]')
            for index in range(min(links.count(), 20)):
                href = links.nth(index).get_attribute("href") or ""
                path = urlparse(href).path
                parts = [part for part in path.split("/") if part]
                if len(parts) == 1 and parts[0].lower() not in RESERVED_PATHS:
                    return parts[0]
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return ""

    def _detect_exclusion_reason(self) -> str | None:
        if self._has_visible_text(SPONSORED_LABELS):
            return "sponsored"
        if self._has_visible_text(RECOMMENDATION_LABELS):
            return "recommended"
        return None

    def _has_visible_text(self, labels: tuple[str, ...]) -> bool:
        for label in labels:
            try:
                match = self.article.get_by_text(label, exact=True)
                if match.count() and match.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _find_control(self, labels: tuple[str, ...]):
        for label in labels:
            try:
                role_match = self.article.get_by_role("button", name=label, exact=True)
                if role_match.count():
                    return role_match.first

                escaped = label.replace("\\", "\\\\").replace('"', '\\"')
                direct = self.article.locator(
                    f'[role="button"][aria-label="{escaped}"], button[aria-label="{escaped}"]'
                )
                if direct.count():
                    return direct.first

                icon = self.article.locator(f'svg[aria-label="{escaped}"]')
                if icon.count():
                    ancestor = icon.first.locator(
                        "xpath=ancestor::*[self::button or @role='button'][1]"
                    )
                    if ancestor.count():
                        return ancestor.first
            except Exception as exc:
                self.session.raise_browser_error(exc)
        return None


