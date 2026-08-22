from __future__ import annotations

import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Literal, Protocol
from urllib.parse import urlparse

from .storage import AppConfig, Storage

LogCallback = Callable[[str], None]
StatusCallback = Callable[[dict[str, Any]], None]
WaitFunction = Callable[[threading.Event | None, float], bool]
LikeCallback = Callable[[], None]
LikeState = Literal["unliked", "liked", "unknown"]


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

    def __init__(self, message: str, *, summary: ScanSummary | None = None):
        super().__init__(message)
        self.summary = summary


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


class FollowingFeedScanner:
    """Like every unliked organic post exposed by the browser's Following feed."""

    def __init__(
        self,
        config: AppConfig,
        *,
        rng: random.Random | Any | None = None,
        wait_fn: WaitFunction | None = None,
        on_log: LogCallback | None = None,
        on_like: LikeCallback | None = None,
    ):
        self.config = config.validate()
        self.rng = rng or random.Random()
        self.wait_fn = wait_fn or self._default_wait
        self.on_log = on_log or (lambda _message: None)
        self.on_like = on_like or (lambda: None)

    def scan_once(
        self,
        feed: FollowingFeed,
        stop_event: threading.Event | None = None,
    ) -> ScanSummary:
        summary = ScanSummary()
        seen_keys: set[str] = set()
        stalled_rounds = 0

        feed.open_following()
        self._raise_if_restricted(feed, summary)

        # The initial viewport is processed before scrolling. Each permitted scroll
        # gets a following processing pass, so the final scroll's content is not lost.
        for pass_index in range(self.config.max_scroll_rounds + 1):
            if stop_event and stop_event.is_set():
                summary.stopped = True
                break

            newly_discovered = 0
            for post in feed.posts():
                if stop_event and stop_event.is_set():
                    summary.stopped = True
                    break

                key = normalize_post_key(post.key)
                if not key or key in seen_keys:
                    continue
                seen_keys.add(key)
                newly_discovered += 1
                summary.discovered += 1

                exclusion_reason = post.exclusion_reason
                if exclusion_reason == "sponsored":
                    summary.sponsored += 1
                    continue
                if exclusion_reason == "recommended":
                    summary.recommended += 1
                    continue

                state = post.like_state
                if state == "liked":
                    summary.already_liked += 1
                    continue
                if state != "unliked":
                    summary.unknown += 1
                    continue

                if self.config.max_likes_per_cycle and summary.liked >= self.config.max_likes_per_cycle:
                    summary.max_likes_reached = True
                    break

                delay_seconds = int(
                    self.rng.randint(
                        self.config.min_delay_seconds,
                        self.config.max_delay_seconds,
                    )
                )
                account = f"@{post.username}" if post.username else "게시물"
                if delay_seconds:
                    self.on_log(f"{account} 좋아요 전 {format_delay(delay_seconds)} 대기합니다.")
                if self.wait_fn(stop_event, delay_seconds):
                    summary.stopped = True
                    break

                try:
                    liked = bool(post.click_like())
                except BrowserClosedError:
                    raise
                except AutoLikerError:
                    raise
                except Exception as exc:
                    # A restriction overlay commonly intercepts the click and causes
                    # Playwright to time out. Check it before attempting another post.
                    self._raise_if_restricted(feed, summary)
                    summary.failed += 1
                    self.on_log(f"{account} 좋아요에 실패했습니다 ({type(exc).__name__}).")
                    continue

                if liked:
                    summary.liked += 1
                    self.on_like()
                    self.on_log(f"좋아요 완료: {account} · 이번 확인 {summary.liked}개")
                else:
                    self._raise_if_restricted(feed, summary)
                    summary.failed += 1
                    self.on_log(f"{account} 좋아요 상태를 확인하지 못했습니다.")

                self._raise_if_restricted(feed, summary)

            if summary.stopped or summary.max_likes_reached:
                break

            if feed.is_caught_up():
                summary.caught_up = True
                break

            if pass_index >= self.config.max_scroll_rounds:
                break

            moved = feed.scroll_for_more()
            summary.scroll_rounds += 1
            if newly_discovered == 0 and not moved:
                stalled_rounds += 1
            else:
                stalled_rounds = 0

            if stalled_rounds >= self.config.unchanged_scroll_rounds:
                break

            self._raise_if_restricted(feed, summary)

        return summary

    @staticmethod
    def _default_wait(stop_event: threading.Event | None, seconds: float) -> bool:
        if seconds <= 0:
            return bool(stop_event and stop_event.is_set())
        if stop_event:
            return stop_event.wait(seconds)
        time.sleep(seconds)
        return False

    @staticmethod
    def _raise_if_restricted(feed: FollowingFeed, summary: ScanSummary) -> None:
        message = feed.restriction_message()
        if message:
            raise InstagramRestrictionError(
                "Instagram이 좋아요 활동을 제한했습니다. 자동화를 중지했습니다. "
                "공식 Instagram에서 계정 상태를 확인하고 충분히 지난 뒤 다시 사용하세요. "
                f"표시된 안내: {message}",
                summary=summary,
            )


class FollowingAutoLiker:
    def __init__(
        self,
        config: AppConfig,
        storage: Storage,
        *,
        rng: random.Random | Any | None = None,
        on_log: LogCallback | None = None,
        on_status: StatusCallback | None = None,
    ):
        self.config = config.validate()
        self.storage = storage
        self.rng = rng or random.Random()
        self.on_log = on_log or (lambda _message: None)
        self.on_status = on_status or (lambda _status: None)
        self.session_likes = 0
        self.last_scan_at = ""

    def run(self, stop_event: threading.Event) -> None:
        from .browser import ChromeBrowserSession

        scanner = FollowingFeedScanner(
            self.config,
            rng=self.rng,
            on_log=self.on_log,
            on_like=self._record_like,
        )
        browser = ChromeBrowserSession(
            self.storage.paths.chrome_profile,
            on_log=self.on_log,
        )

        self.emit_status("launching", "Chrome을 여는 중입니다.")
        try:
            browser.start()
            self.emit_status("login", "Chrome에서 Instagram 로그인을 확인하고 있습니다.")
            browser.wait_until_logged_in(stop_event)
            if stop_event.is_set():
                return

            self.emit_status("running", "로그인되었습니다. 팔로잉 피드를 확인합니다.")
            feed = browser.following_feed()

            while not stop_event.is_set():
                self.on_log("팔로잉 시간순 피드를 처음부터 확인합니다.")
                try:
                    summary = scanner.scan_once(feed, stop_event)
                except InstagramRestrictionError as exc:
                    self.last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
                    if exc.summary is not None:
                        self.on_log(f"제한 감지 전 처리 결과: {self._summary_message(exc.summary)}")
                    self.emit_status(
                        "restricted",
                        f"활동 제한으로 중지 · 누적 {self.session_likes}개",
                    )
                    raise

                self.last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if summary.stopped:
                    break

                self.on_log(self._summary_message(summary))
                self.emit_status(
                    "running",
                    f"확인 완료 · 이번 {summary.liked}개 · 누적 {self.session_likes}개",
                )

                interval_seconds = self.config.check_interval_minutes * 60
                self.on_log(f"다음 확인은 {format_delay(interval_seconds)} 뒤에 시작합니다.")
                if stop_event.wait(interval_seconds):
                    break
        finally:
            self.emit_status("stopping", "Chrome 자동화를 종료하는 중입니다.")
            browser.close()
            self.emit_status("stopped", "중지되었습니다.")

    def _record_like(self) -> None:
        self.session_likes += 1
        self.emit_status(
            "running",
            f"좋아요 처리 중 · 누적 {self.session_likes}개",
        )

    def emit_status(self, phase: str, message: str) -> None:
        snapshot = StatusSnapshot(
            phase=phase,
            message=message,
            session_likes=self.session_likes,
            last_scan_at=self.last_scan_at,
        )
        self.on_status(asdict(snapshot))

    @staticmethod
    def _summary_message(summary: ScanSummary) -> str:
        suffixes = []
        if summary.caught_up:
            suffixes.append("최신 글 끝까지 도달")
        if summary.max_likes_reached:
            suffixes.append("설정한 회차 한도 도달")
        if summary.unknown:
            suffixes.append(f"상태 미확인 {summary.unknown}개")
        if summary.failed:
            suffixes.append(f"실패 {summary.failed}개")
        suffix = f" · {' · '.join(suffixes)}" if suffixes else ""
        return (
            f"확인 완료: 발견 {summary.discovered}개 · 좋아요 {summary.liked}개 · "
            f"이미 좋아요 {summary.already_liked}개 · 광고 제외 {summary.sponsored}개 · "
            f"추천 제외 {summary.recommended}개{suffix}"
        )


def normalize_post_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw if "://" in raw else f"https://www.instagram.com{raw if raw.startswith('/') else '/' + raw}")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"p", "reel", "tv"}:
        return f"/{parts[0]}/{parts[1]}/"
    return raw


def format_delay(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}초"
    minutes, remainder = divmod(seconds, 60)
    if remainder:
        return f"{minutes}분 {remainder}초"
    return f"{minutes}분"
