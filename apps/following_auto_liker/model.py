from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Protocol
from urllib.parse import urlparse

LikeState = Literal["unliked", "liked", "unknown"]
LogCallback = Callable[[str], None]
StatusCallback = Callable[[dict[str, Any]], None]
WaitFunction = Callable[[threading.Event | None, float], bool]


class AutoLikerError(RuntimeError):
    code = "AUTO_LIKER_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


class PlaywrightMissingError(AutoLikerError):
    code = "PLAYWRIGHT_MISSING"


class ChromeLaunchError(AutoLikerError):
    code = "CHROME_LAUNCH_ERROR"


class BrowserClosedError(AutoLikerError):
    code = "BROWSER_CLOSED"


class LoginRequiredError(AutoLikerError):
    code = "LOGIN_REQUIRED"


class InstagramRestrictionError(AutoLikerError):
    code = "INSTAGRAM_RESTRICTION"


class FollowingFeedUnavailableError(AutoLikerError):
    code = "FOLLOWING_FEED_UNAVAILABLE"


class FeedPost(Protocol):
    key: str
    username: str

    @property
    def exclusion_reason(self) -> str | None: ...

    @property
    def like_state(self) -> LikeState: ...

    def click_like(self) -> bool: ...


class FollowingFeed(Protocol):
    def open_following(self) -> None: ...

    def posts(self) -> Iterable[FeedPost]: ...

    def scroll_for_more(self) -> bool: ...

    def restriction_message(self) -> str | None: ...

    def is_caught_up(self) -> bool: ...


@dataclass(slots=True)
class ScanSummary:
    discovered: int = 0
    liked: int = 0
    already_liked: int = 0
    sponsored: int = 0
    recommended: int = 0
    unknown: int = 0
    failed: int = 0
    scroll_rounds: int = 0
    max_likes_reached: bool = False
    caught_up: bool = False
    stopped: bool = False


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    phase: str
    message: str
    session_likes: int
    last_scan_at: str


def normalize_post_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://www.instagram.com{raw if raw.startswith('/') else '/' + raw}"
    parts = [part for part in urlparse(candidate).path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"p", "reel", "tv"}:
        return f"/{parts[0]}/{parts[1]}/"
    return ""


def format_delay(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}초"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}분 {remainder}초" if remainder else f"{minutes}분"
