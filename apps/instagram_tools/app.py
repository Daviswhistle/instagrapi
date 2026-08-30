from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import END, LEFT, RIGHT, StringVar, Text, Tk, messagebox, ttk

from apps.following_auto_liker.storage import AppAlreadyRunningError, AppConfig, Storage
from apps.instagram_tools.worker import InstagramAutomationWorker
from apps.non_follower_cleaner.engine import (
    CleanerConfig,
    FriendshipAccount,
    ScanResult,
    UnfollowSummary,
)

APP_TITLE = "Instagram 도구"
MIN_WINDOW_HEIGHT = 620
MAX_WINDOW_HEIGHT = 820
WINDOW_VERTICAL_MARGIN = 120
COMPACT_LAYOUT_SCREEN_HEIGHT = 850


def use_compact_layout(screen_height: int) -> bool:
    return int(screen_height) <= COMPACT_LAYOUT_SCREEN_HEIGHT


def window_height_for_screen(screen_height: int) -> int:
    return max(
        MIN_WINDOW_HEIGHT,
        min(MAX_WINDOW_HEIGHT, int(screen_height) - WINDOW_VERTICAL_MARGIN),
    )


def configure_logging(storage: Storage) -> logging.Logger:
    logger = logging.getLogger("instagram_tools")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = RotatingFileHandler(
        storage.paths.log,
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


class InstagramToolsApp(Tk):
    def __init__(self, storage: Storage | None = None) -> None:
        super().__init__()
        self.title(APP_TITLE)
        screen_height = self.winfo_screenheight()
        self.compact_ui = use_compact_layout(screen_height)
        self.geometry(f"980x{window_height_for_screen(screen_height)}")
        self.minsize(860, MIN_WINDOW_HEIGHT)

        self.storage = storage or Storage.default()
        self.logger = configure_logging(self.storage)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker = InstagramAutomationWorker(self.storage, self.events)
        self.worker.start()
        self.running_kind = ""
        self.stop_requested_kind = ""
        self.closing = False
        self.candidates: dict[str, FriendshipAccount] = {}
        self.scanned_viewer_id = ""
        self.cleaner_config_path = self.storage.paths.root / "non_follower_cleaner.json"
        self.auto_entries: list[ttk.Entry] = []
        self.cleaner_entries: list[ttk.Entry] = []

        self._build_variables()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_events)

    def _build_variables(self) -> None:
        auto_config = self.storage.load_config()
        cleaner_config = self._load_cleaner_config()

        self.global_status_var = StringVar(value="대기 중 · Chrome은 첫 작업부터 앱 종료까지 한 창을 재사용합니다.")

        self.auto_interval_var = StringVar(value=str(auto_config.check_interval_minutes))
        self.auto_min_delay_var = StringVar(value=str(auto_config.min_delay_seconds))
        self.auto_max_delay_var = StringVar(value=str(auto_config.max_delay_seconds))
        self.auto_max_likes_var = StringVar(value=str(auto_config.max_likes_per_cycle))
        self.auto_max_scroll_var = StringVar(value=str(auto_config.max_scroll_rounds))
        self.auto_status_var = StringVar(value="중지됨")
        self.auto_likes_var = StringVar(value="0개")
        self.auto_last_scan_var = StringVar(value="아직 없음")

        self.cleaner_min_delay_var = StringVar(value=str(cleaner_config.min_delay_seconds))
        self.cleaner_max_delay_var = StringVar(value=str(cleaner_config.max_delay_seconds))
        self.cleaner_max_unfollows_var = StringVar(value=str(cleaner_config.max_unfollows_per_run))
        self.cleaner_status_var = StringVar(value="목록 확인 전")
        self.cleaner_counts_var = StringVar(value="팔로워 0 · 팔로잉 0 · 미팔로워 0")

    def _build_ui(self) -> None:
        compact = self.compact_ui
        outer_padding = 10 if compact else 16
        outer_gap = 6 if compact else 10
        tab_padding = 6 if compact else 14
        log_padding = 4 if compact else 8
        log_height = 2 if compact else 9
        self._section_padding = 6 if compact else 12
        self._status_padding = 6 if compact else 10
        self._list_padding = 4 if compact else 8
        self._row_pady = 1 if compact else 4
        self._status_row_pady = 1 if compact else 3
        self._action_pady = 6 if compact else 12
        self._section_gap = 6 if compact else 10

        container = ttk.Frame(self, padding=outer_padding)
        container.pack(fill="both", expand=True)

        ttk.Label(
            container,
            text=APP_TITLE,
            font=("", 18 if compact else 20, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            container,
            text=(
                "자동 좋아요와 미팔로워 정리를 한 앱에서 사용합니다. 두 기능은 같은 로그인과 Chrome 창을 "
                "공유하며 동시에 실행되지는 않습니다."
            ),
            wraplength=930,
            justify=LEFT,
        ).pack(anchor="w", pady=(4 if compact else 6, 4 if compact else 8))
        ttk.Label(
            container,
            textvariable=self.global_status_var,
            font=("", 10, "bold"),
        ).pack(anchor="w")

        self.notebook = ttk.Notebook(container)
        auto_tab = ttk.Frame(self.notebook, padding=tab_padding)
        cleaner_tab = ttk.Frame(self.notebook, padding=tab_padding)
        self.notebook.add(auto_tab, text="자동 좋아요")
        self.notebook.add(cleaner_tab, text="미팔로워 정리")
        self._build_auto_tab(auto_tab)
        self._build_cleaner_tab(cleaner_tab)

        self.log_frame = ttk.LabelFrame(container, text="공통 진행 기록", padding=log_padding)
        self.log_text = Text(
            self.log_frame,
            height=log_height,
            wrap="word",
            state="disabled",
        )
        log_scrollbar = ttk.Scrollbar(
            self.log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.pack(side=LEFT, fill="both", expand=True)
        log_scrollbar.pack(side=RIGHT, fill="y")

        self.bottom_bar = ttk.Frame(container)
        ttk.Label(
            self.bottom_bar,
            text="비공식 Instagram 웹 인터페이스를 사용하므로 활동 제한이나 호환성 변경이 발생할 수 있습니다.",
            wraplength=670,
            justify=LEFT,
        ).pack(side=LEFT, fill="x", expand=True)
        self.open_data_button = ttk.Button(
            self.bottom_bar,
            text="데이터 폴더 열기",
            command=self._open_data_folder,
        )
        self.open_data_button.pack(side=RIGHT)
        self.clear_browser_button = ttk.Button(
            self.bottom_bar,
            text="전용 Chrome 데이터 지우기",
            command=self._clear_browser_data,
        )
        self.clear_browser_button.pack(side=RIGHT, padx=(0, 8))

        # Reserve fixed bottom controls before the notebook consumes the remaining
        # height. Compact mode also reduces low-value whitespace and log height.
        self.bottom_bar.pack(
            side="bottom",
            fill="x",
            pady=(6 if compact else 8, 0),
        )
        self.log_frame.pack(side="bottom", fill="both", pady=(outer_gap, 0))
        self.notebook.pack(fill="both", expand=True, pady=(outer_gap, 0))

    def _build_auto_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="팔로잉 시간순 피드에서 아직 좋아요하지 않은 일반 게시물만 처리합니다.",
            wraplength=900,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, self._section_gap))

        settings = ttk.LabelFrame(
            parent,
            text="자동 좋아요 설정",
            padding=self._section_padding,
        )
        settings.pack(fill="x")
        self._setting_row(settings, 0, "피드 확인 간격", self.auto_interval_var, "분", self.auto_entries)
        self._setting_row(settings, 1, "좋아요 전 최소 대기", self.auto_min_delay_var, "초", self.auto_entries)
        self._setting_row(settings, 2, "좋아요 전 최대 대기", self.auto_max_delay_var, "초", self.auto_entries)
        self._setting_row(
            settings,
            3,
            "회차당 최대 좋아요",
            self.auto_max_likes_var,
            "개 · 0은 제한 없음",
            self.auto_entries,
        )
        self._setting_row(settings, 4, "최대 스크롤 횟수", self.auto_max_scroll_var, "회", self.auto_entries)

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=self._action_pady)
        self.auto_start_button = ttk.Button(
            actions,
            text="자동 좋아요 시작",
            command=self._start_auto_like,
        )
        self.auto_start_button.pack(side=LEFT)
        self.auto_stop_button = ttk.Button(
            actions,
            text="중지",
            command=self._stop,
            state="disabled",
        )
        self.auto_stop_button.pack(side=LEFT, padx=(8, 0))

        self.auto_status_frame = ttk.LabelFrame(
            parent,
            text="자동 좋아요 상태",
            padding=self._section_padding,
        )
        self.auto_status_frame.pack(fill="x")
        self._status_row(self.auto_status_frame, 0, "상태", self.auto_status_var)
        self._status_row(self.auto_status_frame, 1, "이번 실행 좋아요", self.auto_likes_var)
        self._status_row(self.auto_status_frame, 2, "마지막 확인", self.auto_last_scan_var)

    def _build_cleaner_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text=(
                "팔로워와 팔로잉 목록을 비교해 나를 팔로우하지 않는 계정을 보여줍니다. 목록 확인은 "
                "페이지당 200개와 짧은 요청 간격을 사용하며, 언팔로우할 때는 선택한 계정만 처리합니다."
            ),
            wraplength=900,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, self._section_gap))

        settings = ttk.LabelFrame(
            parent,
            text="언팔로우 설정",
            padding=self._section_padding,
        )
        settings.pack(fill="x")
        self._setting_row(
            settings,
            0,
            "언팔로우 전 최소 대기",
            self.cleaner_min_delay_var,
            "초",
            self.cleaner_entries,
        )
        self._setting_row(
            settings,
            1,
            "언팔로우 전 최대 대기",
            self.cleaner_max_delay_var,
            "초",
            self.cleaner_entries,
        )
        self._setting_row(
            settings,
            2,
            "회차당 최대 언팔로우",
            self.cleaner_max_unfollows_var,
            "개 · 0은 선택한 계정 전부",
            self.cleaner_entries,
        )

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=self._action_pady)
        self.scan_button = ttk.Button(actions, text="목록 확인", command=self._start_scan)
        self.scan_button.pack(side=LEFT)
        self.unfollow_button = ttk.Button(
            actions,
            text="선택 계정 언팔로우",
            command=self._start_unfollow,
            state="disabled",
        )
        self.unfollow_button.pack(side=LEFT, padx=(8, 0))
        self.cleaner_stop_button = ttk.Button(
            actions,
            text="중지",
            command=self._stop,
            state="disabled",
        )
        self.cleaner_stop_button.pack(side=LEFT, padx=(8, 0))
        self.select_all_button = ttk.Button(actions, text="전체 선택", command=self._select_all)
        self.select_all_button.pack(side=RIGHT)
        self.clear_selection_button = ttk.Button(actions, text="전체 해제", command=self._clear_selection)
        self.clear_selection_button.pack(side=RIGHT, padx=(0, 8))

        self.cleaner_status_frame = ttk.LabelFrame(
            parent,
            text="미팔로워 정리 상태",
            padding=self._status_padding,
        )
        self.cleaner_status_frame.pack(fill="x")
        ttk.Label(
            self.cleaner_status_frame,
            textvariable=self.cleaner_status_var,
            font=("", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self.cleaner_status_frame,
            textvariable=self.cleaner_counts_var,
        ).pack(anchor="w", pady=(self._status_row_pady, 0))

        self.cleaner_list_frame = ttk.LabelFrame(
            parent,
            text="나를 팔로우하지 않는 계정",
            padding=self._list_padding,
        )
        self.cleaner_list_frame.pack(
            fill="both",
            expand=True,
            pady=(self._section_gap, 0),
        )
        self.tree = ttk.Treeview(
            self.cleaner_list_frame,
            columns=("username", "full_name", "private", "verified"),
            show="headings",
            selectmode="extended",
        )
        self.tree.heading("username", text="사용자명")
        self.tree.heading("full_name", text="이름")
        self.tree.heading("private", text="비공개")
        self.tree.heading("verified", text="인증")
        self.tree.column("username", width=190, anchor="w")
        self.tree.column("full_name", width=350, anchor="w")
        self.tree.column("private", width=70, anchor="center")
        self.tree.column("verified", width=70, anchor="center")
        scrollbar = ttk.Scrollbar(
            self.cleaner_list_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill="both", expand=True)
        scrollbar.pack(side=RIGHT, fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_controls())

    def _setting_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: StringVar,
        unit: str,
        registry: list[ttk.Entry],
    ) -> None:
        ttk.Label(parent, text=label).grid(
            row=row,
            column=0,
            sticky="w",
            pady=self._row_pady,
        )
        entry = ttk.Entry(parent, textvariable=variable, width=10)
        entry.grid(
            row=row,
            column=1,
            sticky="w",
            padx=(12, 6),
            pady=self._row_pady,
        )
        ttk.Label(parent, text=unit).grid(
            row=row,
            column=2,
            sticky="w",
            pady=self._row_pady,
        )
        registry.append(entry)

    def _status_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: StringVar,
    ) -> None:
        ttk.Label(parent, text=label).grid(
            row=row,
            column=0,
            sticky="w",
            pady=self._status_row_pady,
        )
        ttk.Label(parent, textvariable=variable).grid(
            row=row,
            column=1,
            sticky="w",
            padx=(14, 0),
            pady=self._status_row_pady,
        )

    def _read_auto_config(self) -> AppConfig:
        try:
            config = AppConfig(
                check_interval_minutes=int(self.auto_interval_var.get().strip()),
                min_delay_seconds=int(self.auto_min_delay_var.get().strip()),
                max_delay_seconds=int(self.auto_max_delay_var.get().strip()),
                max_likes_per_cycle=int(self.auto_max_likes_var.get().strip()),
                max_scroll_rounds=int(self.auto_max_scroll_var.get().strip()),
                unchanged_scroll_rounds=self.storage.load_config().unchanged_scroll_rounds,
            ).validate()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        self.storage.save_config(config)
        return config

    def _read_cleaner_config(self) -> CleanerConfig:
        try:
            config = CleanerConfig(
                min_delay_seconds=int(self.cleaner_min_delay_var.get().strip()),
                max_delay_seconds=int(self.cleaner_max_delay_var.get().strip()),
                max_unfollows_per_run=int(self.cleaner_max_unfollows_var.get().strip()),
                page_delay_seconds=0.15,
                page_size=200,
            ).validate()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        self._save_cleaner_config(config)
        return config

    def _start_auto_like(self) -> None:
        if self.running_kind:
            return
        try:
            config = self._read_auto_config()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.auto_likes_var.set("0개")
        self.auto_last_scan_var.set("아직 없음")
        self.auto_status_var.set("시작 중")
        if self.worker.submit("auto_like", config=config):
            self._begin_operation("auto_like", "자동 좋아요를 시작합니다.")

    def _start_scan(self) -> None:
        if self.running_kind:
            return
        try:
            config = self._read_cleaner_config()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._invalidate_results()
        self.cleaner_status_var.set("목록 확인 시작")
        if self.worker.submit("scan", config=config):
            self._begin_operation("scan", "미팔로워 목록 확인을 시작합니다.")

    def _start_unfollow(self) -> None:
        if self.running_kind:
            return
        selected_ids = tuple(self.tree.selection())
        selected = tuple(self.candidates[user_id] for user_id in selected_ids if user_id in self.candidates)
        if not selected:
            messagebox.showinfo(APP_TITLE, "언팔로우할 계정을 먼저 선택해 주세요.")
            return
        if len(selected) != len(selected_ids) or not self.scanned_viewer_id:
            messagebox.showinfo(APP_TITLE, "목록이 바뀌었습니다. 목록을 다시 확인해 주세요.")
            self._invalidate_results(counts_message="목록을 다시 확인해 주세요")
            return
        try:
            config = self._read_cleaner_config()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        limit_text = (
            "선택한 계정 전부" if config.max_unfollows_per_run == 0 else f"최대 {config.max_unfollows_per_run:,}개"
        )
        confirmed = messagebox.askyesno(
            APP_TITLE,
            f"선택한 {len(selected):,}개 중 {limit_text}를 언팔로우할까요?\n\n"
            "전체 목록이나 개별 관계를 실행 전에 다시 조회하지 않습니다.\n"
            "각 쓰기 요청 뒤에는 그 계정의 팔로우 상태가 실제로 바뀌었는지만 확인합니다.",
        )
        if not confirmed:
            return
        self.cleaner_status_var.set("언팔로우 시작")
        if self.worker.submit(
            "unfollow",
            config=config,
            selected=selected,
            expected_viewer_id=self.scanned_viewer_id,
        ):
            self._begin_operation("unfollow", "선택 계정 언팔로우를 시작합니다.")

    def _begin_operation(self, kind: str, log_message: str) -> None:
        self.stop_requested_kind = ""
        self.running_kind = kind
        self.global_status_var.set("작업 중 · Chrome 한 창을 계속 재사용합니다.")
        self._append_log(log_message)
        self._refresh_controls()

    def _stop(self) -> None:
        if not self.running_kind:
            return
        self.stop_requested_kind = self.running_kind
        self.worker.stop_current()
        self.global_status_var.set("중지 요청됨")
        if self.running_kind == "auto_like":
            self.auto_status_var.set("중지 요청됨")
        else:
            self.cleaner_status_var.set("중지 요청됨")
        self._append_log("중지를 요청했습니다. 현재 요청이나 대기를 마친 뒤 작업만 중지하며 Chrome 창은 유지합니다.")
        self._refresh_controls()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status":
                    if not self.stop_requested_kind:
                        self.global_status_var.set(str(payload))
                elif kind == "auto_status" and isinstance(payload, dict):
                    self._apply_auto_status(payload)
                    self._restore_pending_stop_status()
                elif kind == "cleaner_progress" and isinstance(payload, dict):
                    self._apply_cleaner_progress(payload)
                    self._restore_pending_stop_status()
                elif kind == "scan_result" and isinstance(payload, ScanResult):
                    self._apply_scan_result(payload)
                    self._restore_pending_stop_status()
                elif kind == "unfollow_result" and isinstance(payload, UnfollowSummary):
                    self._apply_unfollow_result(payload)
                    self._restore_pending_stop_status()
                elif kind == "unfollow_error" and isinstance(payload, tuple):
                    self.stop_requested_kind = ""
                    message, summary = payload
                    if isinstance(summary, UnfollowSummary):
                        self._apply_unfollow_result(summary)
                    if not self.closing:
                        messagebox.showerror(APP_TITLE, str(message))
                elif kind == "error":
                    self._append_log(str(payload))
                    self.stop_requested_kind = ""
                    if self.running_kind == "auto_like":
                        self.auto_status_var.set("오류로 중지")
                    elif self.running_kind in {"scan", "unfollow"}:
                        self.cleaner_status_var.set("오류로 중지")
                    if not self.closing:
                        messagebox.showerror(APP_TITLE, str(payload))
                elif kind == "profile_cleared":
                    self._invalidate_results(
                        counts_message="로그인 정보가 초기화되었습니다 · 목록을 다시 확인해 주세요"
                    )
                    self.auto_status_var.set("로그인 정보 초기화됨")
                    self.cleaner_status_var.set("목록 확인 전")
                    self._append_log("전용 Chrome 데이터와 저장된 Instagram 로그인을 지웠습니다.")
                    if not self.closing:
                        messagebox.showinfo(
                            APP_TITLE,
                            "전용 Chrome 데이터를 지웠습니다. 다음 작업에서 Instagram에 다시 로그인해 주세요.",
                        )
                elif kind == "operation_finished":
                    self._finalize_finished_tab_status(payload)
                    self.running_kind = ""
                    self.stop_requested_kind = ""
                    self.global_status_var.set("대기 중 · Chrome 창을 다음 작업에 재사용합니다.")
                    self._refresh_controls()
                elif kind == "worker_stopped":
                    if self.closing:
                        self.destroy()
        except queue.Empty:
            pass
        if not self.closing:
            self.after(100, self._drain_events)

    def _restore_pending_stop_status(self) -> None:
        if self.stop_requested_kind == "auto_like":
            self.auto_status_var.set("중지 요청됨")
        elif self.stop_requested_kind in {"scan", "unfollow"}:
            self.cleaner_status_var.set("중지 요청됨")

    def _finalize_finished_tab_status(self, finished_kind: object) -> None:
        if self.stop_requested_kind != finished_kind:
            return
        if finished_kind == "auto_like":
            self.auto_status_var.set("사용자 요청으로 중지")
        elif finished_kind in {"scan", "unfollow"}:
            self.cleaner_status_var.set("사용자 요청으로 중지")

    def _apply_auto_status(self, status: dict[str, object]) -> None:
        message = str(status.get("message") or "")
        if message:
            self.auto_status_var.set(message)
        self.auto_likes_var.set(f"{int(status.get('session_likes') or 0):,}개")
        last_scan = str(status.get("last_scan_at") or "")
        if last_scan:
            try:
                self.auto_last_scan_var.set(datetime.fromisoformat(last_scan).strftime("%Y-%m-%d %H:%M:%S"))
            except ValueError:
                self.auto_last_scan_var.set(last_scan)

    def _apply_cleaner_progress(self, progress: dict[str, object]) -> None:
        phase = str(progress.get("phase") or "")
        if phase in {"followers", "following"}:
            label = "팔로워" if phase == "followers" else "팔로잉"
            self.cleaner_status_var.set(f"{label} 확인 중 · {int(progress.get('collected') or 0):,}개")
        elif phase == "unfollow":
            self.cleaner_status_var.set(
                f"언팔로우 확인 완료 · {int(progress.get('succeeded') or 0):,}/{int(progress.get('eligible') or 0):,}개"
            )

    def _apply_scan_result(self, result: ScanResult) -> None:
        self.scanned_viewer_id = result.viewer_id
        self.candidates = {account.pk: account for account in result.non_followers}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for account in result.non_followers:
            self.tree.insert(
                "",
                "end",
                iid=account.pk,
                values=(
                    f"@{account.username}",
                    account.full_name,
                    "예" if account.is_private else "아니오",
                    "예" if account.is_verified else "아니오",
                ),
            )
        self._select_all()
        self.cleaner_counts_var.set(
            f"팔로워 {len(result.followers):,} · 팔로잉 {len(result.following):,} · "
            f"미팔로워 {len(result.non_followers):,}"
        )
        self.cleaner_status_var.set("목록 확인 완료")
        self._append_log(
            f"목록 확인 완료: 팔로워 {len(result.followers):,}개 · 팔로잉 {len(result.following):,}개 · "
            f"미팔로워 {len(result.non_followers):,}개"
        )
        self._refresh_controls()

    def _apply_unfollow_result(self, summary: UnfollowSummary) -> None:
        succeeded = len(summary.succeeded)
        self._append_log(
            f"언팔로우 종료: 선택 {summary.selected:,}개 · 시도 {summary.attempted:,}개 · "
            f"실제 변경 확인 {succeeded:,}개 · 실패 {len(summary.failed):,}개 · "
            f"회차 한도 이월 {summary.deferred_by_limit:,}개"
        )
        if summary.failed:
            self.cleaner_status_var.set(f"오류로 중지 · 실제 변경 확인 {succeeded:,}개")
        elif summary.stopped:
            self.cleaner_status_var.set(f"사용자 요청으로 중지 · 실제 변경 확인 {succeeded:,}개")
        else:
            self.cleaner_status_var.set(f"언팔로우 완료 · 실제 변경 확인 {succeeded:,}개")
        self._invalidate_results(counts_message="관계가 변경되었습니다 · 목록을 다시 확인해 주세요")

    def _refresh_controls(self) -> None:
        running = bool(self.running_kind)
        entry_state = "disabled" if running else "normal"
        for entry in self.auto_entries + self.cleaner_entries:
            entry.configure(state=entry_state)
        self.auto_start_button.configure(state="disabled" if running else "normal")
        self.scan_button.configure(state="disabled" if running else "normal")
        self.select_all_button.configure(state="disabled" if running else "normal")
        self.clear_selection_button.configure(state="disabled" if running else "normal")
        self.clear_browser_button.configure(state="disabled" if running else "normal")
        self.auto_stop_button.configure(state="normal" if self.running_kind == "auto_like" else "disabled")
        self.cleaner_stop_button.configure(state="normal" if self.running_kind in {"scan", "unfollow"} else "disabled")
        can_unfollow = (
            not running and bool(self.scanned_viewer_id) and bool(self.candidates) and bool(self.tree.selection())
        )
        self.unfollow_button.configure(state="normal" if can_unfollow else "disabled")

    def _select_all(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)
        self._refresh_controls()

    def _clear_selection(self) -> None:
        self.tree.selection_remove(self.tree.selection())
        self._refresh_controls()

    def _invalidate_results(self, *, counts_message: str | None = None) -> None:
        self.scanned_viewer_id = ""
        self.candidates.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.cleaner_counts_var.set(counts_message or "팔로워 0 · 팔로잉 0 · 미팔로워 0")
        self._refresh_controls()

    def _append_log(self, message: str) -> None:
        self.logger.info(message)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"[{timestamp}] {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _load_cleaner_config(self) -> CleanerConfig:
        try:
            payload = json.loads(self.cleaner_config_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid config")
            return CleanerConfig(
                min_delay_seconds=int(payload.get("min_delay_seconds", 10)),
                max_delay_seconds=int(payload.get("max_delay_seconds", 18)),
                max_unfollows_per_run=int(payload.get("max_unfollows_per_run", 40)),
                page_delay_seconds=0.15,
                page_size=200,
            ).validate()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return CleanerConfig(page_delay_seconds=0.15, page_size=200)

    def _save_cleaner_config(self, config: CleanerConfig) -> None:
        self.cleaner_config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(f"{self.cleaner_config_path}.tmp")
        temp_path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.cleaner_config_path)

    def _clear_browser_data(self) -> None:
        if self.running_kind:
            messagebox.showinfo(APP_TITLE, "먼저 현재 작업을 중지해 주세요.")
            return
        confirmed = messagebox.askyesno(
            APP_TITLE,
            "전용 Chrome 창을 닫고 저장된 Instagram 로그인과 브라우저 데이터를 지울까요?\n\n"
            "자동 좋아요와 미팔로워 정리 설정 및 app.log는 유지됩니다.",
        )
        if not confirmed:
            return
        if self.worker.submit("clear_profile"):
            self._begin_operation("clear_profile", "전용 Chrome 데이터 지우기를 시작합니다.")
            self.global_status_var.set("전용 Chrome 데이터를 지우는 중입니다.")

    def _open_data_folder(self) -> None:
        path = str(self.storage.paths.root)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"데이터 폴더를 열지 못했습니다: {exc}")

    def _on_close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.global_status_var.set("Chrome을 종료하는 중입니다.")
        self.worker.shutdown()
        self.after(100, self._wait_for_worker)

    def _wait_for_worker(self) -> None:
        if self.worker.thread.is_alive():
            self.after(100, self._wait_for_worker)
            return
        self.destroy()


def main() -> None:
    multiprocessing.freeze_support()
    storage = Storage.default()
    try:
        instance_lock = storage.acquire_instance_lock()
    except AppAlreadyRunningError as exc:
        startup = Tk()
        startup.withdraw()
        try:
            messagebox.showerror(APP_TITLE, str(exc), parent=startup)
        finally:
            startup.destroy()
        return

    try:
        InstagramToolsApp(storage=storage).mainloop()
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
