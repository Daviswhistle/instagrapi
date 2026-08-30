from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal

from apps.following_auto_liker.engine import (
    AutoLikerError,
    BrowserClosedError,
    FollowingAutoLiker,
    FollowingFeedScanner,
    InstagramRestrictionError,
)
from apps.following_auto_liker.storage import AppConfig, Storage
from apps.non_follower_cleaner.engine import (
    CleanerConfig,
    FriendshipAccount,
    NonFollowerCleaner,
    NonFollowerCleanerError,
    OperationStopped,
    UnfollowRunError,
)

from .browser import SharedChromeBrowserSession, VerifiedFriendshipBackend

OperationKind = Literal["auto_like", "scan", "unfollow", "clear_profile"]
EventQueue = queue.Queue[tuple[str, object]]
BrowserFactory = Callable[..., SharedChromeBrowserSession]
LOGGER = logging.getLogger("instagram_tools.worker")


@dataclass(frozen=True, slots=True)
class AutomationCommand:
    kind: OperationKind | Literal["shutdown"]
    payload: dict[str, Any]


class InstagramAutomationWorker:
    """Own Playwright on one persistent thread and reuse one Chrome window."""

    def __init__(
        self,
        storage: Storage,
        events: EventQueue,
        *,
        browser_factory: BrowserFactory = SharedChromeBrowserSession,
    ) -> None:
        self.storage = storage
        self.events = events
        self.browser_factory = browser_factory
        self.commands: queue.Queue[AutomationCommand] = queue.Queue()
        self._state_lock = threading.Lock()
        self._busy = False
        self._current_stop = threading.Event()
        self._shutdown = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            daemon=False,
            name="instagram-tools-browser-worker",
        )

    @property
    def busy(self) -> bool:
        with self._state_lock:
            return self._busy

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def submit(self, kind: OperationKind, **payload: Any) -> bool:
        with self._state_lock:
            if self._busy or self._shutdown.is_set():
                return False
            self._busy = True
            self._current_stop = threading.Event()
        self.commands.put(AutomationCommand(kind=kind, payload=payload))
        return True

    def stop_current(self) -> None:
        with self._state_lock:
            self._current_stop.set()

    def shutdown(self) -> None:
        self._shutdown.set()
        self.stop_current()
        self.commands.put(AutomationCommand(kind="shutdown", payload={}))

    def _run(self) -> None:
        browser: SharedChromeBrowserSession | None = None
        try:
            while True:
                command = self.commands.get()
                if command.kind == "shutdown":
                    break
                stop_event = self._current_stop
                try:
                    if command.kind == "clear_profile":
                        if browser is not None:
                            browser.close()
                        browser = None
                        self.storage.clear_browser_profile()
                        self.events.put(("profile_cleared", None))
                    else:
                        browser = self._ensure_browser(browser)
                        self._wait_until_logged_in(browser, stop_event)
                        if command.kind == "auto_like":
                            self._run_auto_like(browser, command.payload, stop_event)
                        elif command.kind == "scan":
                            self._run_scan(browser, command.payload, stop_event)
                        elif command.kind == "unfollow":
                            self._run_unfollow(browser, command.payload, stop_event)
                except OperationStopped:
                    self.events.put(("status", "사용자 요청으로 중지했습니다."))
                except UnfollowRunError as exc:
                    LOGGER.exception("Unfollow operation failed: %s", exc.user_message)
                    self.events.put(("unfollow_error", (exc.user_message, exc.summary)))
                except InstagramRestrictionError as exc:
                    self.events.put(("error", exc.user_message))
                except (NonFollowerCleanerError, AutoLikerError) as exc:
                    if isinstance(exc, BrowserClosedError):
                        browser = self._discard_browser(browser)
                    self.events.put(("error", exc.user_message))
                except Exception as exc:
                    LOGGER.exception("Unexpected worker failure during %s", command.kind)
                    self.events.put(
                        (
                            "error",
                            "예상하지 못한 오류로 중지했습니다. 데이터 폴더의 app.log를 확인하세요. "
                            f"({type(exc).__name__})",
                        )
                    )
                finally:
                    with self._state_lock:
                        self._busy = False
                    self.events.put(("operation_finished", command.kind))
        finally:
            self._discard_browser(browser)
            self.events.put(("worker_stopped", None))

    def _ensure_browser(
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

    def _run_auto_like(
        self,
        browser: SharedChromeBrowserSession,
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        config = payload.get("config")
        if not isinstance(config, AppConfig):
            raise TypeError("자동 좋아요 설정이 올바르지 않습니다.")

        session_likes = 0
        last_scan_at = ""

        def on_like() -> None:
            nonlocal session_likes
            session_likes += 1
            self.events.put(
                (
                    "auto_status",
                    {
                        "message": f"좋아요 처리 중 · 누적 {session_likes}개",
                        "session_likes": session_likes,
                        "last_scan_at": last_scan_at,
                    },
                )
            )

        scanner = FollowingFeedScanner(
            config,
            on_log=lambda message: self.events.put(("log", message)),
            on_like=on_like,
        )
        feed = browser.following_feed()
        self.events.put(
            (
                "auto_status",
                {
                    "message": "로그인되었습니다. 팔로잉 피드를 확인합니다.",
                    "session_likes": 0,
                    "last_scan_at": "",
                },
            )
        )

        while not stop_event.is_set():
            self.events.put(("log", "팔로잉 시간순 피드를 처음부터 확인합니다."))
            try:
                summary = scanner.scan_once(feed, stop_event)
            except InstagramRestrictionError as exc:
                last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if exc.summary is not None:
                    self.events.put(
                        (
                            "log",
                            f"제한 감지 전 처리 결과: {FollowingAutoLiker._summary_message(exc.summary)}",
                        )
                    )
                self.events.put(
                    (
                        "auto_status",
                        {
                            "message": f"활동 제한으로 중지 · 누적 {session_likes}개",
                            "session_likes": session_likes,
                            "last_scan_at": last_scan_at,
                        },
                    )
                )
                raise

            last_scan_at = datetime.now().astimezone().isoformat(timespec="seconds")
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
            self.events.put(
                (
                    "auto_status",
                    {
                        "message": f"확인 완료 · 이번 {summary.liked}개 · 누적 {session_likes}개",
                        "session_likes": session_likes,
                        "last_scan_at": last_scan_at,
                    },
                )
            )

            interval_seconds = config.check_interval_minutes * 60
            self.events.put(("log", f"다음 확인은 {interval_seconds // 60}분 뒤에 시작합니다."))
            if stop_event.wait(interval_seconds):
                break

    def _run_scan(
        self,
        browser: SharedChromeBrowserSession,
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        config = payload.get("config")
        if not isinstance(config, CleanerConfig):
            raise TypeError("미팔로워 정리 설정이 올바르지 않습니다.")
        backend = VerifiedFriendshipBackend(browser, stop_event=stop_event)
        backend.prepare()
        cleaner = NonFollowerCleaner(
            backend,
            config,
            on_log=lambda message: self.events.put(("log", message)),
            on_progress=lambda progress: self.events.put(("cleaner_progress", progress)),
        )
        self.events.put(("status", "팔로잉과 팔로워 목록을 확인합니다."))
        self.events.put(("scan_result", cleaner.scan(stop_event)))

    def _run_unfollow(
        self,
        browser: SharedChromeBrowserSession,
        payload: dict[str, Any],
        stop_event: threading.Event,
    ) -> None:
        config = payload.get("config")
        selected = payload.get("selected")
        expected_viewer_id = str(payload.get("expected_viewer_id") or "")
        if not isinstance(config, CleanerConfig):
            raise TypeError("미팔로워 정리 설정이 올바르지 않습니다.")
        if not isinstance(selected, tuple) or not all(isinstance(item, FriendshipAccount) for item in selected):
            raise TypeError("선택한 계정 목록이 올바르지 않습니다.")

        backend = VerifiedFriendshipBackend(browser, stop_event=stop_event)
        backend.prepare()
        cleaner = NonFollowerCleaner(
            backend,
            config,
            on_log=lambda message: self.events.put(("log", message)),
            on_progress=lambda progress: self.events.put(("cleaner_progress", progress)),
        )
        self.events.put(
            (
                "unfollow_result",
                cleaner.unfollow_selected(selected, expected_viewer_id, stop_event),
            )
        )

    @staticmethod
    def _discard_browser(
        browser: SharedChromeBrowserSession | None,
    ) -> None:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        return None
