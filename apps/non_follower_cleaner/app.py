from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
from dataclasses import asdict
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import END, LEFT, RIGHT, StringVar, Text, Tk, messagebox, ttk

from apps.following_auto_liker.browser import ChromeBrowserSession
from apps.following_auto_liker.engine import AutoLikerError
from apps.following_auto_liker.storage import AppAlreadyRunningError, Storage
from apps.non_follower_cleaner.browser import PlaywrightFriendshipBackend
from apps.non_follower_cleaner.engine import (
    CleanerConfig,
    FriendshipAccount,
    NonFollowerCleaner,
    NonFollowerCleanerError,
    OperationStopped,
    ScanResult,
    UnfollowRunError,
    UnfollowSummary,
)

APP_TITLE = "미팔로워 정리"
INTRO_TEXT = (
    "내가 팔로우하지만 나를 팔로우하지 않는 계정을 찾고, 검토한 계정만 언팔로우합니다. "
    "목록 수집이 완전하지 않으면 한 명도 처리하지 않습니다."
)
RISK_TEXT = (
    "Instagram의 비공식 웹 API를 사용합니다. 많은 계정을 빠르게 언팔로우하면 활동 제한이나 "
    "계정 확인이 발생할 수 있습니다. 제한 안내가 확인되면 즉시 중지합니다."
)


def configure_logging(storage: Storage) -> logging.Logger:
    logger = logging.getLogger("non_follower_cleaner")
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


class CleanerApp(Tk):
    def __init__(self, storage: Storage | None = None) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x780")
        self.minsize(780, 680)

        self.storage = storage or Storage.default()
        self.logger = configure_logging(self.storage)
        self.config_path = self.storage.paths.root / "non_follower_cleaner.json"
        self.saved_config = self._load_config()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.closing = False
        self.candidates: dict[str, FriendshipAccount] = {}
        self.scanned_viewer_id = ""
        self.editable_widgets: list[ttk.Entry] = []

        self._build_variables()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_events)

    def _build_variables(self) -> None:
        config = self.saved_config
        self.min_delay_var = StringVar(value=str(config.min_delay_seconds))
        self.max_delay_var = StringVar(value=str(config.max_delay_seconds))
        self.max_unfollows_var = StringVar(value=str(config.max_unfollows_per_run))
        self.status_var = StringVar(value="목록 확인 전")
        self.counts_var = StringVar(value="팔로워 0 · 팔로잉 0 · 미팔로워 0")

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=18)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text=APP_TITLE, font=("", 19, "bold")).pack(anchor="w")
        ttk.Label(container, text=INTRO_TEXT, wraplength=850, justify=LEFT).pack(anchor="w", pady=(8, 12))

        settings = ttk.LabelFrame(container, text="설정", padding=12)
        settings.pack(fill="x")
        self._setting_row(settings, 0, "언팔로우 전 최소 대기", self.min_delay_var, "초")
        self._setting_row(settings, 1, "언팔로우 전 최대 대기", self.max_delay_var, "초")
        self._setting_row(
            settings,
            2,
            "회차당 최대 언팔로우",
            self.max_unfollows_var,
            "개 · 0은 선택한 계정 전부",
        )

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=12)
        self.scan_button = ttk.Button(actions, text="목록 확인", command=self._start_scan)
        self.scan_button.pack(side=LEFT)
        self.stop_button = ttk.Button(actions, text="중지", command=self._stop, state="disabled")
        self.stop_button.pack(side=LEFT, padx=(8, 0))
        self.unfollow_button = ttk.Button(
            actions,
            text="선택 계정 언팔로우",
            command=self._start_unfollow,
            state="disabled",
        )
        self.unfollow_button.pack(side=LEFT, padx=(8, 0))
        self.select_all_button = ttk.Button(
            actions,
            text="전체 선택",
            command=self._select_all,
        )
        self.select_all_button.pack(side=RIGHT)
        self.clear_selection_button = ttk.Button(
            actions,
            text="전체 해제",
            command=self._clear_selection,
        )
        self.clear_selection_button.pack(side=RIGHT, padx=(0, 8))

        status = ttk.LabelFrame(container, text="현재 상태", padding=12)
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(status, textvariable=self.counts_var).pack(anchor="w", pady=(5, 0))

        list_frame = ttk.LabelFrame(container, text="나를 팔로우하지 않는 계정", padding=8)
        list_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.tree = ttk.Treeview(
            list_frame,
            columns=("username", "full_name", "private", "verified"),
            show="headings",
            selectmode="extended",
        )
        self.tree.heading("username", text="사용자명")
        self.tree.heading("full_name", text="이름")
        self.tree.heading("private", text="비공개")
        self.tree.heading("verified", text="인증")
        self.tree.column("username", width=190, anchor="w")
        self.tree.column("full_name", width=330, anchor="w")
        self.tree.column("private", width=70, anchor="center")
        self.tree.column("verified", width=70, anchor="center")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill="both", expand=True)
        scrollbar.pack(side=RIGHT, fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_unfollow_button())

        log_frame = ttk.LabelFrame(container, text="진행 기록", padding=8)
        log_frame.pack(fill="both", pady=(12, 0))
        self.log_text = Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        bottom = ttk.Frame(container)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, text=RISK_TEXT, wraplength=690, justify=LEFT).pack(side=LEFT, fill="x", expand=True)
        ttk.Button(bottom, text="데이터 폴더 열기", command=self._open_data_folder).pack(side=RIGHT)

    def _setting_row(
        self,
        parent: ttk.LabelFrame,
        row: int,
        label: str,
        variable: StringVar,
        unit: str,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=10)
        entry.grid(row=row, column=1, sticky="w", padx=(12, 6), pady=4)
        ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w", pady=4)
        self.editable_widgets.append(entry)

    def _read_config(self) -> CleanerConfig:
        try:
            config = CleanerConfig(
                min_delay_seconds=int(self.min_delay_var.get().strip()),
                max_delay_seconds=int(self.max_delay_var.get().strip()),
                max_unfollows_per_run=int(self.max_unfollows_var.get().strip()),
            ).validate()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        self._save_config(config)
        self.saved_config = config
        return config

    def _start_scan(self) -> None:
        if self.running:
            return
        try:
            config = self._read_config()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self._begin_worker("scan", config, ())

    def _start_unfollow(self) -> None:
        if self.running:
            return
        selected = tuple(self.tree.selection())
        if not selected:
            messagebox.showinfo(APP_TITLE, "언팔로우할 계정을 먼저 선택해 주세요.")
            return
        expected_viewer_id = self.scanned_viewer_id
        if not expected_viewer_id:
            self._invalidate_results(counts_message="목록을 다시 확인해 주세요")
            messagebox.showinfo(APP_TITLE, "로그인 계정을 확인하려면 목록을 다시 확인해 주세요.")
            return
        try:
            config = self._read_config()
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        limit_text = (
            "선택한 계정 전부" if config.max_unfollows_per_run == 0 else f"최대 {config.max_unfollows_per_run:,}개"
        )
        confirmed = messagebox.askyesno(
            APP_TITLE,
            f"선택한 {len(selected):,}개 중 {limit_text}를 언팔로우할까요?\n\n"
            "실행 직전에 관계를 다시 확인하며, 그때 나를 팔로우하는 계정은 제외합니다.\n"
            "이 작업은 Instagram에서 즉시 반영됩니다.",
        )
        if not confirmed:
            return
        self._begin_worker("unfollow", config, selected, expected_viewer_id)

    def _begin_worker(
        self,
        operation: str,
        config: CleanerConfig,
        selected: tuple[str, ...],
        expected_viewer_id: str = "",
    ) -> None:
        # Relationship data becomes stale as soon as a new scan or write run
        # starts. Clear it before launching the worker so an interrupted or failed
        # operation can never leave an actionable stale selection behind.
        self._invalidate_results()
        self.stop_event = threading.Event()
        self.running = True
        self._set_controls_running(True)
        self.status_var.set("Chrome을 여는 중")
        self._append_log("목록 확인을 시작합니다." if operation == "scan" else "언팔로우 작업을 시작합니다.")
        self.worker = threading.Thread(
            target=self._worker_main,
            args=(operation, config, selected, expected_viewer_id),
            daemon=False,
            name=f"non-follower-{operation}",
        )
        self.worker.start()

    def _worker_main(
        self,
        operation: str,
        config: CleanerConfig,
        selected: tuple[str, ...],
        expected_viewer_id: str,
    ) -> None:
        browser = ChromeBrowserSession(
            self.storage.paths.chrome_profile,
            on_log=lambda message: self._engine_log(message.replace("자동 좋아요", "미팔로워 정리")),
        )
        try:
            browser.start()
            self.events.put(("status", "Chrome에서 Instagram 로그인을 확인하고 있습니다."))
            browser.wait_until_logged_in(self.stop_event)
            if self.stop_event.is_set():
                return

            backend = PlaywrightFriendshipBackend(browser, stop_event=self.stop_event)
            backend.prepare()
            cleaner = NonFollowerCleaner(
                backend,
                config,
                on_log=self._engine_log,
                on_progress=lambda progress: self.events.put(("progress", progress)),
            )
            if operation == "scan":
                result = cleaner.scan(self.stop_event)
                self.events.put(("scan_result", result))
            else:
                summary = cleaner.unfollow_selected(
                    selected,
                    expected_viewer_id,
                    self.stop_event,
                )
                self.events.put(("unfollow_result", summary))
        except OperationStopped:
            self.events.put(("status", "사용자 요청으로 중지했습니다."))
        except UnfollowRunError as exc:
            self.logger.warning("Unfollow run stopped: %s", exc, exc_info=True)
            self.events.put(("unfollow_error", (exc.user_message, exc.summary)))
        except (NonFollowerCleanerError, AutoLikerError) as exc:
            message = str(getattr(exc, "user_message", str(exc))).replace("자동 좋아요", "미팔로워 정리")
            self.logger.warning("Cleaner stopped: %s", exc, exc_info=True)
            self.events.put(("error", message))
        except Exception as exc:
            self.logger.exception("Unexpected non-follower cleaner failure")
            self.events.put(
                (
                    "error",
                    f"예상하지 못한 오류로 중지했습니다. 데이터 폴더의 app.log를 확인하세요. ({type(exc).__name__})",
                )
            )
        finally:
            browser.close()
            self.events.put(("stopped", None))

    def _engine_log(self, message: str) -> None:
        self.logger.info(message)
        self.events.put(("log", message))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "progress" and isinstance(payload, dict):
                    self._apply_progress(payload)
                elif kind == "scan_result" and isinstance(payload, ScanResult):
                    self._apply_scan_result(payload)
                elif kind == "unfollow_result" and isinstance(payload, UnfollowSummary):
                    self._apply_unfollow_result(payload)
                elif kind == "unfollow_error" and isinstance(payload, tuple):
                    message, summary = payload
                    self._apply_unfollow_result(summary)
                    if not self.closing:
                        messagebox.showerror(APP_TITLE, str(message))
                elif kind == "error":
                    self.status_var.set("오류로 중지")
                    self._append_log(str(payload))
                    if not self.closing:
                        messagebox.showerror(APP_TITLE, str(payload))
                elif kind == "stopped":
                    self.running = False
                    self._set_controls_running(False)
        except queue.Empty:
            pass
        if not self.closing:
            self.after(100, self._drain_events)

    def _apply_progress(self, progress: dict[str, object]) -> None:
        phase = str(progress.get("phase") or "")
        if phase in {"followers", "following"}:
            label = "팔로워" if phase == "followers" else "팔로잉"
            self.status_var.set(f"{label} 목록 확인 중 · {int(progress.get('collected') or 0):,}개")
        elif phase == "unfollow":
            self.status_var.set(
                f"언팔로우 처리 중 · {int(progress.get('succeeded') or 0):,}/{int(progress.get('eligible') or 0):,}개"
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
        self.counts_var.set(
            f"팔로워 {len(result.followers):,} · 팔로잉 {len(result.following):,} · "
            f"미팔로워 {len(result.non_followers):,}"
        )
        self.status_var.set("목록 확인 완료")
        self._append_log(
            f"목록 확인 완료: 팔로워 {len(result.followers):,}개 · "
            f"팔로잉 {len(result.following):,}개 · 미팔로워 {len(result.non_followers):,}개"
        )
        self._refresh_unfollow_button()

    def _apply_unfollow_result(self, summary: UnfollowSummary) -> None:
        succeeded = len(summary.succeeded)
        self._append_log(
            f"언팔로우 종료: 선택 {summary.selected:,}개 · 실행 대상 {summary.eligible:,}개 · "
            f"완료 {succeeded:,}개 · 실패 {len(summary.failed):,}개 · "
            f"관계 변경 제외 {summary.skipped_relationship_changed:,}개 · "
            f"회차 한도 이월 {summary.deferred_by_limit:,}개"
        )
        if summary.failed:
            status = f"오류로 중지 · 중지 전 완료 {succeeded:,}개"
        elif summary.stopped:
            status = f"사용자 요청으로 중지 · 중지 전 완료 {succeeded:,}개"
        else:
            status = f"언팔로우 완료 {succeeded:,}개 · 목록을 다시 확인해 주세요."
        self.status_var.set(status)
        self._invalidate_results(counts_message="관계가 변경되었습니다 · 목록을 다시 확인해 주세요")

    def _invalidate_results(self, *, counts_message: str | None = None) -> None:
        self.scanned_viewer_id = ""
        self.candidates.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.counts_var.set(counts_message or "팔로워 0 · 팔로잉 0 · 미팔로워 0")
        self._refresh_unfollow_button()

    def _set_controls_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in self.editable_widgets:
            widget.configure(state=state)
        self.scan_button.configure(state=state)
        self.select_all_button.configure(state=state)
        self.clear_selection_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        self._refresh_unfollow_button()

    def _refresh_unfollow_button(self) -> None:
        enabled = (
            not self.running and bool(self.scanned_viewer_id) and bool(self.tree.selection()) and bool(self.candidates)
        )
        self.unfollow_button.configure(state="normal" if enabled else "disabled")

    def _select_all(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)
        self._refresh_unfollow_button()

    def _clear_selection(self) -> None:
        self.tree.selection_remove(self.tree.selection())
        self._refresh_unfollow_button()

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("중지 요청됨")
        self.stop_button.configure(state="disabled")
        self._append_log("중지를 요청했습니다. 현재 요청이나 대기를 마친 뒤 Chrome을 닫습니다.")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"[{timestamp}] {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _load_config(self) -> CleanerConfig:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid config")
            return CleanerConfig(
                min_delay_seconds=int(payload.get("min_delay_seconds", 10)),
                max_delay_seconds=int(payload.get("max_delay_seconds", 18)),
                max_unfollows_per_run=int(payload.get("max_unfollows_per_run", 40)),
            ).validate()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return CleanerConfig()

    def _save_config(self, config: CleanerConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(f"{self.config_path}.tmp")
        temp_path.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, self.config_path)

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
        self.stop_event.set()
        self.status_var.set("Chrome을 종료하는 중")
        self._finish_close()

    def _finish_close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.after(100, self._finish_close)
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
            messagebox.showerror(
                APP_TITLE,
                "팔로잉 자동 좋아요 또는 미팔로워 정리가 이미 실행 중입니다. "
                "기존 앱과 전용 Chrome 창을 닫은 뒤 다시 실행하세요.",
                parent=startup,
            )
        finally:
            startup.destroy()
        return

    try:
        CleanerApp(storage=storage).mainloop()
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
