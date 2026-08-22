from __future__ import annotations

import random
import threading
import time
from typing import Any

from .config import AppConfig
from .model import (
    AutoLikerError,
    BrowserClosedError,
    FollowingFeed,
    InstagramRestrictionError,
    LogCallback,
    ScanSummary,
    WaitFunction,
    format_delay,
    normalize_post_key,
)


class FollowingFeedScanner:
    """Like each visible, unliked, organic post exposed by the Following feed."""

    def __init__(
        self,
        config: AppConfig,
        *,
        rng: random.Random | Any | None = None,
        wait_fn: WaitFunction | None = None,
        on_log: LogCallback | None = None,
    ):
        self.config = config.validate()
        self.rng = rng or random.Random()
        self.wait_fn = wait_fn or self._default_wait
        self.on_log = on_log or (lambda _message: None)

    def scan_once(self, feed: FollowingFeed, stop_event: threading.Event | None = None) -> ScanSummary:
        summary = ScanSummary()
        seen: set[str] = set()
        unchanged = 0
        feed.open_following()
        self._raise_if_restricted(feed)

        for round_number in range(1, self.config.max_scroll_rounds + 1):
            summary.scroll_rounds = round_number
            if stop_event and stop_event.is_set():
                summary.stopped = True
                break

            new_count = 0
            for post in feed.posts():
                if stop_event and stop_event.is_set():
                    summary.stopped = True
                    break
                key = normalize_post_key(post.key)
                if not key or key in seen:
                    continue
                seen.add(key)
                new_count += 1
                summary.discovered += 1

                reason = post.exclusion_reason
                if reason == "sponsored":
                    summary.sponsored += 1
                    continue
                if reason == "recommended":
                    summary.recommended += 1
                    continue
                if post.like_state == "liked":
                    summary.already_liked += 1
                    continue
                if post.like_state != "unliked":
                    summary.unknown += 1
                    continue
                if self.config.max_likes_per_cycle and summary.liked >= self.config.max_likes_per_cycle:
                    summary.max_likes_reached = True
                    break

                delay = int(self.rng.randint(self.config.min_delay_seconds, self.config.max_delay_seconds))
                account = f"@{post.username}" if post.username else "게시물"
                if delay:
                    self.on_log(f"{account} 좋아요 전 {format_delay(delay)} 대기합니다.")
                if self.wait_fn(stop_event, delay):
                    summary.stopped = True
                    break

                try:
                    clicked = bool(post.click_like())
                except BrowserClosedError:
                    raise
                except AutoLikerError:
                    raise
                except Exception as exc:
                    self._raise_if_restricted(feed)
                    summary.failed += 1
                    self.on_log(f"{account} 좋아요 실패 ({type(exc).__name__}).")
                    continue

                if clicked:
                    summary.liked += 1
                    self.on_log(f"좋아요 완료: {account} · 이번 확인 {summary.liked}개")
                else:
                    summary.failed += 1
                    self.on_log(f"{account} 좋아요 상태를 확인하지 못했습니다.")
                self._raise_if_restricted(feed)

            if summary.stopped or summary.max_likes_reached:
                break
            if feed.is_caught_up():
                summary.caught_up = True
                break
            if round_number >= self.config.max_scroll_rounds:
                break

            moved = feed.scroll_for_more()
            unchanged = unchanged + 1 if new_count == 0 and not moved else 0
            if unchanged >= self.config.unchanged_scroll_rounds:
                break
            self._raise_if_restricted(feed)
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
    def _raise_if_restricted(feed: FollowingFeed) -> None:
        message = feed.restriction_message()
        if message:
            raise InstagramRestrictionError(
                "Instagram이 활동을 제한했습니다. 자동화를 중지했습니다. "
                "공식 Instagram에서 계정 상태를 확인하고 충분히 지난 뒤 다시 사용하세요. "
                f"표시 내용: {message}"
            )
