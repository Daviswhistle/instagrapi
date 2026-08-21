"""Tkinter desktop interface for liking new posts from followed accounts."""

from __future__ import annotations

import os
import queue
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, simpledialog, ttk
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    ClientError,
    ClientThrottledError,
    LoginRequired,
    PleaseWaitFewMinutes,
    TwoFactorRequired,
)

from apps.following_auto_like.core import (
    AccountPaths,
    AutomationConfig,
    StateStore,
    app_data_dir,
    baseline_media_ids,
    local_day,
    read_json_file,
    scan_timeline,
    write_json_file,
)

APP_NAME = "팔로잉 새 글 자동 좋아요"
APP_VERSION = "0.1.0"
FOLLOWING_REFRESH_SECONDS = 12 * 60 * 60


class UserCancelled(RuntimeError):
    """The user cancelled an interactive credential prompt."""


class FriendlyError(RuntimeError):
    """An error that is already suitable for display to a nondeveloper."""


class FollowingAutoLikeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.base_dir = app_data_dir()
        self.preferences_path = self.base_dir / "preferences.json"
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.running = False
        self.controlled_widgets: list[tk.Widget] = []

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.daily_limit_var = tk.StringVar(value="20")
        self.interval_var = tk.StringVar(value="30")
        self.probability_var = tk.StringVar(value="90")
        self.lookback_var = tk.StringVar(value="24")
        self.min_delay_var = tk.StringVar(value="60")
        self.max_delay_var = tk.StringVar(value="150")
        self.status_var = tk.StringVar(value="중지됨")
        self.count_var = tk.StringVar(value="오늘 0회")

        self._configure_window()
        self._build_ui()
        self._load_preferences()
        self._refresh_count_from_disk()
        self.root.after(100, self._drain_messages)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("700x720")
        self.root.minsize(620, 620)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        title = ttk.Label(outer, text=APP_NAME, font=("TkDefaultFont", 17, "bold"))
        title.grid(row=0, column=0, sticky="w")

        description = ttk.Label(
            outer,
            text=(
                "내가 실제로 팔로우하는 계정의 새 게시물만 확인합니다. "
                "첫 실행은 현재 피드를 기준선으로 저장할 뿐, 기존 글에는 좋아요를 누르지 않습니다."
            ),
            wraplength=640,
            justify="left",
        )
        description.grid(row=1, column=0, sticky="ew", pady=(6, 12))

        warning = ttk.Label(
            outer,
            text=(
                "주의: Instagram이 제공하는 공식 자동화 기능이 아닙니다. "
                "계정에 확인 요청이나 일시 제한이 생길 수 있으므로 낮은 한도부터 사용하세요."
            ),
            wraplength=640,
            justify="left",
        )
        warning.grid(row=2, column=0, sticky="ew", pady=(0, 12))

        account_frame = ttk.LabelFrame(outer, text="Instagram 계정", padding=12)
        account_frame.grid(row=3, column=0, sticky="ew")
        account_frame.columnconfigure(1, weight=1)

        ttk.Label(account_frame, text="사용자 이름").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=4
        )
        username_entry = ttk.Entry(account_frame, textvariable=self.username_var)
        username_entry.grid(row=0, column=1, sticky="ew", pady=4)
        username_entry.bind("<FocusOut>", lambda _event: self._refresh_count_from_disk())

        ttk.Label(account_frame, text="비밀번호").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=4
        )
        password_entry = ttk.Entry(
            account_frame,
            textvariable=self.password_var,
            show="●",
        )
        password_entry.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(
            account_frame,
            text="비밀번호는 저장하지 않습니다. 저장된 세션이 유효하면 비워도 됩니다.",
        ).grid(row=2, column=1, sticky="w", pady=(0, 2))

        settings_frame = ttk.LabelFrame(outer, text="동작 설정", padding=12)
        settings_frame.grid(row=4, column=0, sticky="ew", pady=(12, 12))
        for column in (1, 3):
            settings_frame.columnconfigure(column, weight=1)

        daily_limit_spin = self._add_spinbox(
            settings_frame,
            row=0,
            label="하루 최대 좋아요",
            variable=self.daily_limit_var,
            from_=1,
            to=100,
            column=0,
        )
        interval_spin = self._add_spinbox(
            settings_frame,
            row=0,
            label="피드 확인 간격(분)",
            variable=self.interval_var,
            from_=15,
            to=360,
            column=2,
        )
        probability_spin = self._add_spinbox(
            settings_frame,
            row=1,
            label="새 글 좋아요 비율(%)",
            variable=self.probability_var,
            from_=10,
            to=100,
            column=0,
        )
        lookback_spin = self._add_spinbox(
            settings_frame,
            row=1,
            label="새 글로 볼 시간(시간)",
            variable=self.lookback_var,
            from_=1,
            to=72,
            column=2,
        )
        min_delay_spin = self._add_spinbox(
            settings_frame,
            row=2,
            label="좋아요 전 최소 대기(초)",
            variable=self.min_delay_var,
            from_=20,
            to=900,
            column=0,
        )
        max_delay_spin = self._add_spinbox(
            settings_frame,
            row=2,
            label="좋아요 전 최대 대기(초)",
            variable=self.max_delay_var,
            from_=20,
            to=1800,
            column=2,
        )

        self.controlled_widgets.extend(
            [
                username_entry,
                password_entry,
                daily_limit_spin,
                interval_spin,
                probability_spin,
                lookback_spin,
                min_delay_spin,
                max_delay_spin,
            ]
        )

        activity_frame = ttk.LabelFrame(outer, text="상태와 기록", padding=12)
        activity_frame.grid(row=5, column=0, sticky="nsew")
        activity_frame.columnconfigure(0, weight=1)
        activity_frame.rowconfigure(1, weight=1)

        status_row = ttk.Frame(activity_frame)
        status_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        status_row.columnconfigure(0, weight=1)
        ttk.Label(status_row, textvariable=self.status_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(status_row, textvariable=self.count_var).grid(
            row=0, column=1, sticky="e"
        )

        self.log_text = scrolledtext.ScrolledText(
            activity_frame,
            height=14,
            wrap="word",
            state="disabled",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")

        button_row = ttk.Frame(outer)
        button_row.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        button_row.columnconfigure(5, weight=1)

        self.start_button = ttk.Button(
            button_row,
            text="시작",
            command=self._start,
        )
        self.start_button.grid(row=0, column=0, padx=(0, 8))

        self.stop_button = ttk.Button(
            button_row,
            text="중지",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=1, padx=(0, 8))

        self.reset_button = ttk.Button(
            button_row,
            text="계정 데이터 지우기",
            command=self._reset_account,
        )
        self.reset_button.grid(row=0, column=2, padx=(0, 8))

        ttk.Button(
            button_row,
            text="저장 폴더 열기",
            command=self._open_data_folder,
        ).grid(row=0, column=3, padx=(0, 8))

        ttk.Button(
            button_row,
            text="로그 복사",
            command=self._copy_log,
        ).grid(row=0, column=4)

        ttk.Label(
            outer,
            text="앱이 열려 있는 동안만 동작하며, 창을 닫으면 즉시 중지합니다.",
        ).grid(row=7, column=0, sticky="w", pady=(10, 0))

    def _add_spinbox(
        self,
        parent: ttk.Frame,
        *,
        row: int,
        label: str,
        variable: tk.StringVar,
        from_: int,
        to: int,
        column: int,
    ) -> ttk.Spinbox:
        ttk.Label(parent, text=label).grid(
            row=row,
            column=column,
            sticky="w",
            padx=(0, 8),
            pady=4,
        )
        spinbox = ttk.Spinbox(
            parent,
            textvariable=variable,
            from_=from_,
            to=to,
            width=10,
        )
        spinbox.grid(
            row=row,
            column=column + 1,
            sticky="ew",
            padx=(0, 18 if column == 0 else 0),
            pady=4,
        )
        return spinbox

    def _load_preferences(self) -> None:
        if not self.preferences_path.exists():
            return
        try:
            preferences = read_json_file(self.preferences_path)
            config = AutomationConfig.from_mapping(preferences.get("config"))
        except (OSError, TypeError, ValueError):
            self._append_log("저장된 설정을 읽지 못해 기본값을 사용합니다.")
            return

        self.username_var.set(str(preferences.get("username", "")))
        self.daily_limit_var.set(str(config.daily_limit))
        self.interval_var.set(str(config.check_interval_minutes))
        self.probability_var.set(str(round(config.like_probability * 100)))
        self.lookback_var.set(str(config.lookback_hours))
        self.min_delay_var.set(str(config.min_delay_seconds))
        self.max_delay_var.set(str(config.max_delay_seconds))

    def _save_preferences(self, username: str, config: AutomationConfig) -> None:
        write_json_file(
            self.preferences_path,
            {
                "username": username,
                "config": config.to_mapping(),
            },
        )

    def _config_from_ui(self) -> AutomationConfig:
        try:
            config = AutomationConfig(
                daily_limit=int(self.daily_limit_var.get()),
                check_interval_minutes=int(self.interval_var.get()),
                like_probability=float(self.probability_var.get()) / 100,
                lookback_hours=int(self.lookback_var.get()),
                min_delay_seconds=int(self.min_delay_var.get()),
                max_delay_seconds=int(self.max_delay_var.get()),
            )
        except ValueError as error:
            raise ValueError("설정에는 숫자만 입력하세요.") from error
        config.validate()
        return config

    def _start(self) -> None:
        if self.running:
            return
        username = self.username_var.get().strip().lstrip("@").strip()
        if not username:
            messagebox.showerror(APP_NAME, "Instagram 사용자 이름을 입력하세요.")
            return
        try:
            config = self._config_from_ui()
            AccountPaths.for_username(self.base_dir, username)
            self._save_preferences(username, config)
        except (OSError, ValueError) as error:
            messagebox.showerror(APP_NAME, str(error))
            return

        password = self.password_var.get()
        self.stop_event.clear()
        self._set_running(True)
        self._append_log(
            f"시작: @{username} · 하루 최대 {config.daily_limit}회 · "
            f"{config.check_interval_minutes}분마다 확인"
        )
        self.worker_thread = threading.Thread(
            target=self._run_worker,
            args=(username, password, config),
            name="following-auto-like-worker",
            daemon=True,
        )
        self.worker_thread.start()

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("중지 요청 중…")
        self._append_log("중지를 요청했습니다.")

    def _run_worker(
        self,
        username: str,
        supplied_password: str,
        config: AutomationConfig,
    ) -> None:
        paths = AccountPaths.for_username(self.base_dir, username)
        store = StateStore(paths.state)
        try:
            self._post("status", "로그인 확인 중…")
            client = self._authenticate(username, supplied_password, paths)
            self._post("clear_password", None)

            state = store.load()
            today = local_day()
            if state.ensure_day(today):
                store.save(state)
            self._post("count", state.likes_today(today))

            following_ids = self._load_following_ids(client)
            following_refreshed_at = time.monotonic()
            payload = self._fetch_timeline(client)

            if not state.initialized:
                baseline_ids = baseline_media_ids(payload)
                state.mark_processed(list(baseline_ids))
                state.initialized = True
                store.save(state)
                self._post(
                    "log",
                    f"첫 실행 기준선 저장 완료: 현재 피드 {len(baseline_ids)}개는 건너뜁니다. "
                    "다음에 올라오는 새 글부터 처리합니다.",
                )
            else:
                self._process_payload(
                    client,
                    payload,
                    following_ids,
                    state,
                    store,
                    config,
                )

            while not self.stop_event.is_set():
                if not self._interruptible_wait(
                    config.check_interval_minutes * 60,
                    "다음 피드 확인",
                ):
                    break

                if time.monotonic() - following_refreshed_at >= FOLLOWING_REFRESH_SECONDS:
                    following_ids = self._load_following_ids(client)
                    following_refreshed_at = time.monotonic()

                payload = self._fetch_timeline(client)
                self._process_payload(
                    client,
                    payload,
                    following_ids,
                    state,
                    store,
                    config,
                )
                self._dump_session(client, paths.session)

        except UserCancelled:
            self._post("log", "사용자가 로그인을 취소했습니다.")
        except FriendlyError as error:
            self._post("log", f"중지: {error}")
            self._post("dialog_error", str(error))
        except Exception as error:
            safe_error = self._safe_error_text(error)
            self._post("log", f"예상하지 못한 오류로 중지했습니다: {safe_error}")
            self._post(
                "dialog_error",
                "예상하지 못한 오류가 발생했습니다. 화면의 로그를 복사해 전달해 주세요.\n\n"
                f"{safe_error}",
            )
        finally:
            self._post("status", "중지됨")
            self._post("running", False)

    def _authenticate(
        self,
        username: str,
        supplied_password: str,
        paths: AccountPaths,
    ) -> Client:
        if paths.session.exists():
            client = self._new_client()
            try:
                client.load_settings(str(paths.session))
                account = client.account_info()
                if account.username.casefold() != username.casefold():
                    raise LoginRequired("Saved session belongs to another account")
                self._post("log", "저장된 로그인 세션을 확인했습니다.")
                return client
            except (PleaseWaitFewMinutes, ClientThrottledError) as error:
                raise FriendlyError(
                    "Instagram이 잠시 요청을 제한했습니다. 공식 앱에서 계정을 확인한 뒤 나중에 다시 실행하세요."
                ) from error
            except ChallengeRequired as error:
                raise FriendlyError(
                    "Instagram 계정 확인이 필요합니다. 공식 앱에서 확인 절차를 마친 뒤 다시 실행하세요."
                ) from error
            except Exception:
                self._post("log", "저장된 세션이 만료되어 다시 로그인합니다.")

        password = supplied_password or self._ask_secret(
            "Instagram 로그인",
            f"@{username} 비밀번호를 입력하세요. 비밀번호는 저장하지 않습니다.",
        )
        if not password:
            raise UserCancelled

        client = self._new_client()
        try:
            client.login(username, password)
        except TwoFactorRequired:
            verification_code = self._ask_secret(
                "2단계 인증",
                "Instagram 2단계 인증 코드를 입력하세요.",
            )
            if not verification_code:
                raise UserCancelled
            try:
                client.login(
                    username,
                    password,
                    verification_code=verification_code.strip(),
                )
            except TwoFactorRequired as error:
                raise FriendlyError("2단계 인증 코드가 올바르지 않습니다.") from error
        except BadPassword as error:
            raise FriendlyError("Instagram 비밀번호가 올바르지 않습니다.") from error
        except ChallengeRequired as error:
            raise FriendlyError(
                "Instagram 계정 확인이 필요합니다. 공식 앱에서 확인 절차를 마친 뒤 다시 실행하세요."
            ) from error
        except (PleaseWaitFewMinutes, ClientThrottledError) as error:
            raise FriendlyError(
                "Instagram이 잠시 로그인을 제한했습니다. 공식 앱에서 계정을 확인한 뒤 나중에 다시 실행하세요."
            ) from error
        except ClientError as error:
            raise FriendlyError(
                "Instagram 로그인에 실패했습니다. 사용자 이름, 네트워크 상태와 공식 앱의 알림을 확인하세요."
            ) from error

        try:
            account = client.account_info()
        except ClientError as error:
            raise FriendlyError("로그인 후 계정 정보를 확인하지 못했습니다.") from error
        if account.username.casefold() != username.casefold():
            raise FriendlyError("로그인된 계정이 입력한 사용자 이름과 다릅니다.")

        self._dump_session(client, paths.session)
        self._post("log", "로그인에 성공했고 암호화되지 않은 비밀번호는 저장하지 않았습니다.")
        return client

    def _new_client(self) -> Client:
        client = Client()
        client.delay_range = [1, 3]
        client.request_timeout = 10
        return client

    def _load_following_ids(self, client: Client) -> set[str]:
        if not client.user_id:
            raise FriendlyError("로그인된 사용자 ID를 확인하지 못했습니다.")
        self._post("status", "팔로잉 목록 확인 중…")
        try:
            following = client.user_following(client.user_id, amount=0)
        except (PleaseWaitFewMinutes, ClientThrottledError) as error:
            raise FriendlyError(
                "팔로잉 목록을 읽는 동안 요청 제한이 발생했습니다. 잠시 후 다시 실행하세요."
            ) from error
        except (ChallengeRequired, LoginRequired) as error:
            raise FriendlyError(
                "Instagram에서 계정 확인을 요구했습니다. 공식 앱을 확인하세요."
            ) from error
        except ClientError as error:
            raise FriendlyError("팔로잉 목록을 불러오지 못했습니다.") from error

        following_ids = {str(user_id) for user_id in following}
        self._post("log", f"팔로잉 계정 {len(following_ids)}개를 확인했습니다.")
        return following_ids

    def _fetch_timeline(self, client: Client) -> dict[str, Any]:
        self._post("status", "새 게시물 확인 중…")
        try:
            payload = client.get_timeline_feed(reason="pull_to_refresh")
        except (PleaseWaitFewMinutes, ClientThrottledError) as error:
            raise FriendlyError(
                "피드를 확인하는 동안 요청 제한이 발생했습니다. 공식 앱에서 계정을 확인하고 나중에 다시 실행하세요."
            ) from error
        except (ChallengeRequired, LoginRequired) as error:
            raise FriendlyError(
                "Instagram에서 계정 확인을 요구했습니다. 공식 앱을 확인하세요."
            ) from error
        except ClientError as error:
            raise FriendlyError("홈 피드를 불러오지 못했습니다.") from error
        if not isinstance(payload, dict):
            raise FriendlyError("Instagram 피드 응답 형식이 예상과 달라 중지했습니다.")
        return payload

    def _process_payload(
        self,
        client: Client,
        payload: dict[str, Any],
        following_ids: set[str],
        state: Any,
        store: StateStore,
        config: AutomationConfig,
    ) -> None:
        today = local_day()
        if state.ensure_day(today):
            store.save(state)
            self._post("count", 0)
            self._post("log", "날짜가 바뀌어 오늘의 좋아요 횟수를 0으로 초기화했습니다.")

        result = scan_timeline(
            payload,
            following_ids=following_ids,
            processed_ids=state.processed_set(),
            lookback_hours=config.lookback_hours,
            own_user_id=str(client.user_id),
        )
        if result.handled_media_ids:
            state.mark_processed(list(result.handled_media_ids))
            store.save(state)

        current_count = state.likes_today(today)
        self._post("count", current_count)
        if current_count >= config.daily_limit:
            self._post(
                "log",
                f"오늘 한도 {config.daily_limit}회에 도달해 새 글을 확인만 하고 좋아요는 누르지 않습니다.",
            )
            return

        if not result.candidates:
            self._post("log", "이번 확인에서는 조건에 맞는 새 게시물이 없었습니다.")
            return

        self._post("log", f"조건에 맞는 새 게시물 {len(result.candidates)}개를 찾았습니다.")
        for candidate in result.candidates:
            if self.stop_event.is_set():
                return
            current_count = state.likes_today(today)
            if current_count >= config.daily_limit:
                self._post("log", f"오늘 한도 {config.daily_limit}회에 도달했습니다.")
                return

            if random.random() > config.like_probability:
                state.mark_processed([candidate.media_id])
                store.save(state)
                self._post("log", f"설정한 비율에 따라 건너뜀: {candidate.label}")
                continue

            delay_seconds = random.randint(
                config.min_delay_seconds,
                config.max_delay_seconds,
            )
            self._post(
                "log",
                f"{delay_seconds}초 뒤 좋아요 예정: {candidate.label}",
            )
            if not self._interruptible_wait(
                delay_seconds,
                f"좋아요 대기 · {candidate.label}",
            ):
                return

            try:
                liked = client.media_like(candidate.media_id)
            except (PleaseWaitFewMinutes, ClientThrottledError) as error:
                raise FriendlyError(
                    "좋아요 요청이 일시 제한되었습니다. 더 시도하지 않고 중지합니다. 공식 앱에서 계정을 확인하세요."
                ) from error
            except (ChallengeRequired, LoginRequired) as error:
                raise FriendlyError(
                    "좋아요 중 Instagram이 계정 확인을 요구했습니다. 더 시도하지 않고 중지합니다."
                ) from error
            except ClientError as error:
                raise FriendlyError(
                    "좋아요 요청 중 오류가 발생해 반복 시도하지 않고 중지합니다."
                ) from error

            if liked:
                state.record_like(candidate.media_id, today)
                store.save(state)
                current_count = state.likes_today(today)
                self._post("count", current_count)
                self._post(
                    "log",
                    f"좋아요 완료 ({current_count}/{config.daily_limit}): {candidate.label}",
                )
            else:
                state.mark_processed([candidate.media_id])
                store.save(state)
                self._post(
                    "log",
                    f"Instagram이 좋아요를 완료하지 않아 다시 시도하지 않습니다: {candidate.label}",
                )

    def _dump_session(self, client: Client, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            client.dump_settings(str(path))
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except (OSError, TypeError, ValueError) as error:
            raise FriendlyError("로그인 세션을 로컬에 저장하지 못했습니다.") from error

    def _interruptible_wait(self, seconds: int, label: str) -> bool:
        deadline = time.monotonic() + max(0, seconds)
        last_displayed = -1
        while True:
            remaining = max(0, int(round(deadline - time.monotonic())))
            if remaining <= 0:
                return not self.stop_event.is_set()
            display_bucket = remaining if remaining < 60 else remaining // 60
            if display_bucket != last_displayed:
                last_displayed = display_bucket
                if remaining < 60:
                    self._post("status", f"{label}: {remaining}초")
                else:
                    self._post("status", f"{label}: 약 {(remaining + 59) // 60}분")
            if self.stop_event.wait(timeout=min(1.0, remaining)):
                return False

    def _ask_secret(self, title: str, prompt: str) -> str | None:
        result: queue.Queue[str | None] = queue.Queue(maxsize=1)

        def show_dialog() -> None:
            try:
                answer = simpledialog.askstring(
                    title,
                    prompt,
                    parent=self.root,
                    show="●",
                )
            except tk.TclError:
                answer = None
            result.put(answer)

        try:
            self.root.after(0, show_dialog)
        except tk.TclError:
            return None

        while not self.stop_event.is_set():
            try:
                return result.get(timeout=0.2)
            except queue.Empty:
                continue
        return None

    def _post(self, kind: str, value: object) -> None:
        self.messages.put((kind, value))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "status":
                    self.status_var.set(str(value))
                elif kind == "count":
                    self.count_var.set(f"오늘 {int(value)}회")
                elif kind == "clear_password":
                    self.password_var.set("")
                elif kind == "running":
                    self._set_running(bool(value))
                elif kind == "dialog_error":
                    messagebox.showerror(APP_NAME, str(value))
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._drain_messages)
        except tk.TclError:
            pass

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.running = running
        widget_state = "disabled" if running else "normal"
        for widget in self.controlled_widgets:
            widget.configure(state=widget_state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.reset_button.configure(state="disabled" if running else "normal")
        if not running:
            self.worker_thread = None

    def _refresh_count_from_disk(self) -> None:
        username = self.username_var.get().strip().lstrip("@").strip()
        if not username:
            self.count_var.set("오늘 0회")
            return
        try:
            paths = AccountPaths.for_username(self.base_dir, username)
            state = StateStore(paths.state).load()
            self.count_var.set(f"오늘 {state.likes_today(local_day())}회")
        except (OSError, ValueError):
            self.count_var.set("오늘 0회")

    def _reset_account(self) -> None:
        username = self.username_var.get().strip().lstrip("@").strip()
        if not username:
            messagebox.showerror(APP_NAME, "먼저 사용자 이름을 입력하세요.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"@{username}의 저장 세션과 처리 기록을 모두 지울까요?\n"
            "다음 시작은 다시 첫 실행으로 취급됩니다.",
        ):
            return
        paths = AccountPaths.for_username(self.base_dir, username)
        for path in (paths.session, paths.state):
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                messagebox.showerror(APP_NAME, f"계정 데이터를 지우지 못했습니다.\n{error}")
                return
        try:
            paths.state.parent.rmdir()
        except OSError:
            pass
        self.password_var.set("")
        self.count_var.set("오늘 0회")
        self._append_log(f"@{username}의 로컬 계정 데이터를 지웠습니다.")

    def _open_data_folder(self) -> None:
        try:
            if os.name == "nt":
                os.startfile(self.base_dir)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.base_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.base_dir)])
        except OSError as error:
            messagebox.showerror(APP_NAME, f"저장 폴더를 열지 못했습니다.\n{error}")

    def _copy_log(self) -> None:
        log = self.log_text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(log)
        self.status_var.set("로그를 클립보드에 복사했습니다.")

    def _on_close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    @staticmethod
    def _safe_error_text(error: Exception) -> str:
        first_line = str(error).splitlines()[0].strip()
        if not first_line:
            first_line = error.__class__.__name__
        return f"{error.__class__.__name__}: {first_line[:240]}"


def main() -> None:
    root = tk.Tk()
    FollowingAutoLikeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
