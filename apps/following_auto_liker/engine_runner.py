from __future__ import annotations

import random
import threading
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .engine_scanner import FollowingFeedScanner
from .engine_shared import LogCallback, ScanSummary, StatusCallback, StatusSnapshot, format_delay
from .storage import AppConfig, Storage

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
                summary = scanner.scan_once(feed, stop_event)
                self.session_likes += summary.liked
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
        if summary.failed:
            suffixes.append(f"실패 {summary.failed}개")
        suffix = f" · {' · '.join(suffixes)}" if suffixes else ""
        return (
            f"확인 완료: 발견 {summary.discovered}개 · 좋아요 {summary.liked}개 · "
            f"이미 좋아요 {summary.already_liked}개 · 광고 제외 {summary.sponsored}개 · "
            f"추천 제외 {summary.recommended}개{suffix}"
        )

