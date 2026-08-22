from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from tkinter import END, LEFT, RIGHT, StringVar, Text, Tk, messagebox, ttk

from .config import AppConfig, Storage
from .engine import FollowingAutoLiker
from .model import AutoLikerError

APP_TITLE = "팔로잉 자동 좋아요"


def configure_logging(storage: Storage) -> logging.Logger:
    logger = logging.getLogger("following_auto_liker")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    handler = RotatingFileHandler(storage.paths.log, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


class AutoLikerApp(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("800x780")
        self.minsize(740, 700)

        self.storage = Storage.default()
        self.logger = configure_logging(self.storage)
        self.saved_config = self.storage.load_config()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.closing = False
        self.editable_widgets: list[ttk.Entry] = []

        self._build_variables()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_events)

    def _build_variables(self) -> None:
        config = self.saved_config
        self.interval_var = StringVar(value=str(config.check_interval_minutes))
        self.min_delay_var = StringVar(value=str(config.min_delay_seconds))
        self.max_delay_var = StringVar(value=str(config.max_delay_seconds))
        self.max_likes_var = StringVar(value=str(config.max_likes_per_cycle))
        self.max_scroll_var = StringVar(value=str(config.max_scroll_rounds))
        self.status_var = StringVar(value="중지됨")
        self.likes_var = StringVar(value="0개")
        self.last_scan_var = StringVar(value="아직 없음")

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=18)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text=APP_TITLE, font=("", 19, "bold")).pack(anchor="w")
        ttk.Label(
            container,
            text=(
                "전용 Google Chrome 창에서 Instagram의 시간순 팔로잉 피드를 끝까지 확인하고, "
                "광고·추천·이미 좋아요한 글을 제외한 미좋아요 게시물에 좋아요를 누릅니다."
            ),
            wraplength=750,
            justify=LEFT,
        ).pack(anchor="w", pady=(8, 4))
        ttk.Label(
            container,
            text=(
                "처음 시작할 때 열린 Chrome에서 직접 로그인하세요. 앱은 Instagram 아이디, 비밀번호, "
                "2단계 인증 코드를 받거나 저장하지 않습니다."
            ),
            wraplength=750,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 12))

        settings = ttk.LabelFrame(container, text="설정", padding=12)
        settings.pack(fill="x")
        self._setting_row(settings, 0, "피드 확인 간격", self.interval_var, "분", "끝까지 확인한 뒤 다시 시작할 간격")
        self._setting_row(
            settings, 1, "좋아요 전 최소 대기", self.min_delay_var, "초", "각 좋아요 사이 무작위 대기의 하한"
        )
        self._setting_row(
            settings, 2, "좋아요 전 최대 대기", self.max_delay_var, "초", "각 좋아요 사이 무작위 대기의 상한"
        )
        self._setting_row(
            settings, 3, "회차당 최대 좋아요", self.max_likes_var, "개", "0이면 발견한 미좋아요 글을 모두 처리"
        )
        self._setting_row(
            settings, 4, "최대 피드 화면 수", self.max_scroll_var, "회", "피드가 끝나지 않을 때의 처리 상한"
        )
        settings.columnconfigure(3, weight=1)

        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=12)
        self.start_button = ttk.Button(actions, text="시작", command=self._start)
        self.start_button.pack(side=LEFT)
        self.stop_button = ttk.Button(actions, text="중지", command=self._stop, state="disabled")
        self.stop_button.pack(side=LEFT, padx=(8, 0))
        ttk.Button(actions, text="데이터 폴더 열기", command=self._open_data_folder).pack(side=RIGHT)
        self.clear_button = ttk.Button(actions, text="전용 Chrome 로그인 지우기", command=self._clear_browser_data)
        self.clear_button.pack(side=RIGHT, padx=(0, 8))

        status = ttk.LabelFrame(container, text="현재 상태", padding=12)
        status.pack(fill="x")
        ttk.Label(status, text="상태").grid(row=0, column=0, sticky="w")
        ttk.Label(status, textvariable=self.status_var, font=("", 10, "bold")).grid(
            row=0, column=1, sticky="w", padx=(14, 0)
        )
        ttk.Label(status, text="이번 실행 좋아요").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(status, textvariable=self.likes_var).grid(row=1, column=1, sticky="w", padx=(14, 0), pady=(6, 0))
        ttk.Label(status, text="마지막 확인").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(status, textvariable=self.last_scan_var).grid(row=2, column=1, sticky="w", padx=(14, 0), pady=(6, 0))
        status.columnconfigure(1, weight=1)

        log_frame = ttk.LabelFrame(container, text="진행 기록", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = Text(log_frame, height=14, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=LEFT, fill="both", expand=True)
        scrollbar.pack(side=RIGHT, fill="y")

        bottom = ttk.Frame(container)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(
            bottom,
            text=(
                "기본 3~5초 간격은 빠른 설정입니다. Instagram이 활동 제한을 표시하면 앱은 즉시 중지하며, "
                "제한·본인 확인을 우회하지 않습니다."
            ),
            wraplength=630,
            justify=LEFT,
        ).pack(side=LEFT, fill="x", expand=True)
        ttk.Button(bottom, text="로그 복사", command=self._copy_log).pack(side=RIGHT, padx=(10, 0))

    def _setting_row(self, parent, row: int, label: str, variable: StringVar, unit: str, description: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=9)
        entry.grid(row=row, column=1, sticky="w", padx=(12, 4), pady=4)
        ttk.Label(parent, text=unit).grid(row=row, column=2, sticky="w", pady=4)
        ttk.Label(parent, text=description).grid(row=row, column=3, sticky="w", padx=(14, 0), pady=4)
        self.editable_widgets.append(entry)

    def _read_config(self) -> AppConfig:
        try:
            config = AppConfig(
                check_interval_minutes=int(self.interval_var.get().strip()),
                min_delay_seconds=int(self.min_delay_var.get().strip()),
                max_delay_seconds=int(self.max_delay_var.get().strip()),
                max_likes_per_cycle=int(self.max_likes_var.get().strip()),
                max_scroll_rounds=int(self.max_scroll_var.get().strip()),
                unchanged_scroll_rounds=self.saved_config.unchanged_scroll_rounds,
            )
        except ValueError as exc:
            raise ValueError("설정값은 숫자로 입력해 주세요.") from exc
        return config.validate()

    def _start(self) -> None:
        if self.running:
            return
        try:
            config = self._read_config()
            self.storage.save_config(config)
        except (ValueError, OSError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.saved_config = config
        self.stop_event = threading.Event()
        self.running = True
        self.likes_var.set("0개")
        self.last_scan_var.set("아직 없음")
        self.status_var.set("Chrome을 여는 중")
        self._set_running_controls(True)
        self._append_log("자동 좋아요를 시작합니다.")
        self.worker = threading.Thread(
            target=self._worker_main, args=(config,), daemon=False, name="following-auto-liker"
        )
        self.worker.start()

    def _worker_main(self, config: AppConfig) -> None:
        engine = FollowingAutoLiker(
            config,
            self.storage,
            on_log=lambda message: self.events.put(("log", message)),
            on_status=lambda status: self.events.put(("status", status)),
        )
        try:
            engine.run(self.stop_event)
        except AutoLikerError as exc:
            self.logger.warning("Automation stopped: %s", exc, exc_info=True)
            self.events.put(("error", exc.user_message))
        except Exception as exc:
            self.logger.exception("Unexpected auto-liker failure")
            self.events.put(
                ("error", f"예상하지 못한 오류로 중지했습니다. app.log를 확인하세요. ({type(exc).__name__})")
            )
        finally:
            self.events.put(("stopped", None))

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.status_var.set("중지 요청됨")
        self.stop_button.configure(state="disabled")
        self._append_log("중지를 요청했습니다. 현재 대기 또는 브라우저 작업을 마친 뒤 Chrome을 닫습니다.")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status" and isinstance(payload, dict):
                    self._apply_status(payload)
                elif kind == "error":
                    self.status_var.set("오류로 중지")
                    self._append_log(str(payload))
                    if not self.closing:
                        messagebox.showerror(APP_TITLE, str(payload))
                elif kind == "stopped":
                    self.running = False
                    self._set_running_controls(False)
                    if self.status_var.get() != "오류로 중지":
                        self.status_var.set("중지됨")
        except queue.Empty:
            pass
        if self.closing:
            self._finish_close()
        else:
            self.after(100, self._drain_events)

    def _apply_status(self, status: dict[str, object]) -> None:
        message = str(status.get("message") or "")
        if message:
            self.status_var.set(message)
        self.likes_var.set(f"{int(status.get('session_likes') or 0):,}개")
        last_scan = str(status.get("last_scan_at") or "")
        if last_scan:
            try:
                last_scan = datetime.fromisoformat(last_scan).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
            self.last_scan_var.set(last_scan)

    def _set_running_controls(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in self.editable_widgets:
            widget.configure(state=state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.clear_button.configure(state="disabled" if running else "normal")

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"[{stamp}] {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.log_text.get("1.0", "end-1c"))
        self.update_idletasks()

    def _clear_browser_data(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "먼저 자동 좋아요를 중지해 주세요.")
            return
        if not self.storage.chrome_profile_has_data():
            messagebox.showinfo(APP_TITLE, "저장된 전용 Chrome 로그인이 없습니다.")
            return
        if not messagebox.askyesno(
            APP_TITLE,
            "전용 Chrome의 Instagram 로그인과 쿠키를 모두 지울까요?\n일반 Chrome 데이터에는 영향을 주지 않습니다.",
        ):
            return
        try:
            self.storage.reset_chrome_profile()
        except OSError as exc:
            messagebox.showerror(
                APP_TITLE, f"Chrome 데이터를 지우지 못했습니다. 열린 전용 Chrome 창을 닫고 다시 시도하세요.\n{exc}"
            )
            return
        self._append_log("전용 Chrome 로그인 데이터를 지웠습니다.")

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
        self.status_var.set("종료 중")
        self._set_running_controls(True)
        self._append_log("앱을 종료합니다. Chrome 자동화가 정상 종료될 때까지 기다립니다.")
        self.after(100, self._finish_close)

    def _finish_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.after(200, self._finish_close)
            return
        self.destroy()


def main() -> None:
    multiprocessing.freeze_support()
    AutoLikerApp().mainloop()


if __name__ == "__main__":
    main()
