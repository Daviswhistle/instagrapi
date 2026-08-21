from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from tkinter import TclError, Tk, messagebox, ttk

from apps.following_auto_liker.engine import AutoLikerError, FollowingAutoLiker
from apps.following_auto_liker.storage import AppConfig, Storage, parse_iso_datetime
from apps.following_auto_liker.view import APP_TITLE, AutoLikerViewMixin


def configure_logging(storage: Storage) -> logging.Logger:
    logger = logging.getLogger("following_auto_liker")
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


class AutoLikerApp(Tk, AutoLikerViewMixin):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("780x760")
        self.minsize(720, 680)

        self.storage = Storage.default()
        self.logger = configure_logging(self.storage)
        self.saved_config = self.storage.load_config()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.editable_widgets: list[ttk.Widget] = []

        self._build_variables()
        self._build_ui()
        self._refresh_status_from_disk()
        for warning in self.storage.pop_warnings():
            self._append_log(warning)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._process_events)

    def _read_config(self) -> AppConfig:
        exclusions = [value.strip() for value in self.exclusions_var.get().replace("\n", ",").split(",")]
        config = AppConfig(
            username=self.username_var.get().strip(),
            daily_limit=int(self.daily_limit_var.get()),
            like_probability=int(self.like_probability_var.get()),
            scan_interval_minutes=int(self.scan_interval_var.get()),
            min_delay_seconds=int(self.min_delay_var.get()),
            max_delay_seconds=int(self.max_delay_var.get()),
            lookback_hours=int(self.lookback_var.get()),
            following_refresh_hours=self.saved_config.following_refresh_hours,
            excluded_usernames=exclusions,
            max_failures_per_media=self.saved_config.max_failures_per_media,
        )
        config.validate()
        return config

    def _start(self) -> None:
        if self.running:
            return
        try:
            config = self._read_config()
        except (ValueError, TypeError, TclError) as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return

        self.saved_config = config
        self.storage.save_config(config)
        self.stop_event = threading.Event()
        self.running = True
        self._set_running_ui(True)
        self.run_state_var.set("연결 중")
        self._append_log(f"@{config.username} 자동 좋아요를 시작합니다.")

        password = self.password_var.get()
        verification_code = self.verification_code_var.get()
        self.worker = threading.Thread(
            target=self._worker_main,
            args=(config, password, verification_code),
            name="following-auto-liker-worker",
            daemon=True,
        )
        self.worker.start()

    def _worker_main(self, config: AppConfig, password: str, verification_code: str) -> None:
        try:
            engine = FollowingAutoLiker(
                config.username,
                config,
                self.storage,
                on_log=lambda message: self.events.put(("log", message)),
                on_status=lambda status: self.events.put(("status", status)),
            )
            auth_source = engine.authenticate(password=password, verification_code=verification_code)
            self.events.put(("running", auth_source))
            engine.emit_status()
            engine.run_forever(self.stop_event)
        except AutoLikerError as exc:
            self.events.put(("error", (exc.code, exc.user_message)))
        except Exception as exc:
            self.logger.exception("Unhandled auto-liker error")
            self.events.put(
                (
                    "error",
                    (
                        "UNEXPECTED_ERROR",
                        f"예상하지 못한 오류가 발생했습니다 ({type(exc).__name__}). app.log를 확인하세요.",
                    ),
                )
            )
        finally:
            self.events.put(("stopped", None))

    def _stop(self) -> None:
        if not self.running:
            return
        self.run_state_var.set("중지 요청 중")
        self.stop_button.configure(state="disabled")
        self.stop_event.set()
        self._append_log("중지를 요청했습니다. 진행 중인 네트워크 요청이 끝나면 멈춥니다.")

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "log":
                    self._append_log(str(payload))
                elif event == "status":
                    self._apply_status(dict(payload))  # type: ignore[arg-type]
                elif event == "running":
                    self.run_state_var.set("실행 중")
                    self.password_var.set("")
                    self.verification_code_var.set("")
                    if payload == "saved_session":
                        self._append_log("저장된 세션을 사용했습니다.")
                elif event == "error":
                    code, message = payload  # type: ignore[misc]
                    self._show_worker_error(str(code), str(message))
                elif event == "stopped":
                    self.running = False
                    self._set_running_ui(False)
                    if self.run_state_var.get() not in {"오류", "중지됨"}:
                        self.run_state_var.set("중지됨")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(200, self._process_events)

    def _show_worker_error(self, code: str, message: str) -> None:
        self.run_state_var.set("오류")
        self._append_log(message)
        if code == "TWO_FACTOR_REQUIRED":
            title = "2단계 인증 필요"
        elif code in {"PASSWORD_REQUIRED", "SESSION_EXPIRED"}:
            title = "비밀번호 필요"
        elif code == "CHALLENGE_REQUIRED":
            title = "인스타그램 확인 필요"
        else:
            title = APP_TITLE
        messagebox.showerror(title, message, parent=self)

    def _apply_status(self, status: dict[str, object]) -> None:
        self.today_likes_var.set(f"{status.get('today_likes', 0)} / {status.get('daily_limit', 0)}")
        self.following_count_var.set(f"{int(status.get('following_count', 0)):,}명")
        initialized = bool(status.get("initialized", False))
        self.baseline_var.set(
            "현재 피드 기준선 저장 완료 · 새 게시물만 처리"
            if initialized
            else "첫 실행 시 현재 피드를 기준선으로 저장"
        )
        self.last_scan_var.set(self._format_timestamp(str(status.get("last_scan_at", ""))))

    def _refresh_status_from_disk(self) -> None:
        username = self.username_var.get().strip()
        if not username:
            return
        state = self.storage.load_state(username)
        today = datetime.now().astimezone().date().isoformat()
        self._apply_status(
            {
                "today_likes": state.likes_for_day(today),
                "daily_limit": self.saved_config.daily_limit,
                "following_count": len(state.following_ids),
                "initialized": state.initialized,
                "last_scan_at": state.last_scan_at,
            }
        )

    def _reset_account(self) -> None:
        if self.running:
            messagebox.showinfo(APP_TITLE, "먼저 자동 좋아요를 중지하세요.", parent=self)
            return
        username = self.username_var.get().strip()
        if not username:
            messagebox.showinfo(APP_TITLE, "초기화할 사용자 이름을 입력하세요.", parent=self)
            return
        confirmed = messagebox.askyesno(
            APP_TITLE,
            f"@{username}의 저장된 로그인과 처리 기록을 모두 삭제할까요?\n다음 시작은 첫 로그인으로 진행됩니다.",
            parent=self,
        )
        if not confirmed:
            return
        self.storage.delete_account_data(username)
        self.password_var.set("")
        self.verification_code_var.set("")
        self.today_likes_var.set(f"0 / {self.daily_limit_var.get()}")
        self.following_count_var.set("0명")
        self.last_scan_var.set("아직 없음")
        self.baseline_var.set("첫 실행 시 현재 피드를 기준선으로 저장")
        self._append_log(f"@{username}의 계정 데이터를 초기화했습니다.")

    def _open_data_folder(self) -> None:
        path = self.storage.paths.root
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"데이터 폴더를 열지 못했습니다: {exc}", parent=self)

    def _set_running_ui(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for widget in self.editable_widgets:
            widget.configure(state=state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.logger.info(message)

    @staticmethod
    def _format_timestamp(value: str) -> str:
        timestamp = parse_iso_datetime(value)
        if timestamp is None:
            return "아직 없음"
        return timestamp.astimezone().strftime("%m-%d %H:%M")

    def _on_close(self) -> None:
        self.stop_event.set()
        self.destroy()


def main() -> None:
    app = AutoLikerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
