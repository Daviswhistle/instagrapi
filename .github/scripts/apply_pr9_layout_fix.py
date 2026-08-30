from __future__ import annotations

from pathlib import Path


BUILD_UI = '''    def _build_ui(self) -> None:
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

'''


AUTO_TAB = '''    def _build_auto_tab(self, parent: ttk.Frame) -> None:
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

'''


CLEANER_TAB = '''    def _build_cleaner_tab(self, parent: ttk.Frame) -> None:
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

'''


ROW_METHODS = '''    def _setting_row(
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

'''


LAYOUT_SMOKE = '''from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

import apps.instagram_tools.app as app_module
from apps.following_auto_liker.storage import Storage, StoragePaths


class _FakeThread:
    @staticmethod
    def is_alive() -> bool:
        return False


class _FakeWorker:
    def __init__(self, _storage: Storage, _events) -> None:
        self.thread = _FakeThread()

    def start(self) -> None:
        return None

    def submit(self, _kind: str, **_payload) -> bool:
        return True

    def stop_current(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def _assert_fully_visible(app: app_module.InstagramToolsApp, widget, label: str) -> None:
    app.update_idletasks()
    assert widget.winfo_ismapped(), f"{label} is not mapped"
    top = widget.winfo_rooty() - app.winfo_rooty()
    height = widget.winfo_height()
    assert height > 1, f"{label} collapsed to {height}px"
    assert top >= 0, f"{label} starts above the window: {top}px"
    assert top + height <= app.winfo_height(), (
        f"{label} extends below the window: {top + height}px > {app.winfo_height()}px"
    )


def main() -> None:
    original_worker = app_module.InstagramAutomationWorker
    app_module.InstagramAutomationWorker = _FakeWorker
    app = None
    try:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            storage = Storage(
                StoragePaths(
                    root=root,
                    config=root / "config.json",
                    chrome_profile=root / "chrome-profile",
                    log=root / "app.log",
                    instance_lock=root / "app.lock",
                )
            )
            app = app_module.InstagramToolsApp(storage=storage)
            app.update_idletasks()

            assert app.winfo_screenheight() == 768
            assert app.compact_ui
            assert app.winfo_height() <= 680

            app.notebook.select(0)
            app.update_idletasks()
            for widget, label in (
                (app.notebook, "notebook"),
                (app.auto_status_frame, "auto status"),
                (app.log_frame, "shared log"),
                (app.bottom_bar, "bottom controls"),
                (app.clear_browser_button, "profile reset button"),
                (app.open_data_button, "data folder button"),
            ):
                _assert_fully_visible(app, widget, label)

            app.notebook.select(1)
            app.update_idletasks()
            for widget, label in (
                (app.cleaner_status_frame, "cleaner status"),
                (app.cleaner_list_frame, "cleaner account list"),
                (app.tree, "cleaner tree"),
                (app.log_frame, "shared log on cleaner tab"),
                (app.bottom_bar, "bottom controls on cleaner tab"),
            ):
                _assert_fully_visible(app, widget, label)
    finally:
        if app is not None:
            logger = app.logger
            app.destroy()
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
        app_module.InstagramAutomationWorker = original_worker
        logging.shutdown()


if __name__ == "__main__":
    main()
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    if end_index <= start_index:
        raise RuntimeError(f"{label}: invalid replacement range")
    return text[:start_index] + replacement + text[end_index:]


def patch_app() -> None:
    path = Path("apps/instagram_tools/app.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "WINDOW_VERTICAL_MARGIN = 120\n\n\ndef window_height_for_screen",
        "WINDOW_VERTICAL_MARGIN = 120\n"
        "COMPACT_LAYOUT_SCREEN_HEIGHT = 850\n\n\n"
        "def use_compact_layout(screen_height: int) -> bool:\n"
        "    return int(screen_height) <= COMPACT_LAYOUT_SCREEN_HEIGHT\n\n\n"
        "def window_height_for_screen",
        "compact layout helper",
    )
    text = replace_once(
        text,
        '        self.title(APP_TITLE)\n'
        '        self.geometry(f"980x{window_height_for_screen(self.winfo_screenheight())}")\n'
        '        self.minsize(860, MIN_WINDOW_HEIGHT)\n',
        '        self.title(APP_TITLE)\n'
        '        screen_height = self.winfo_screenheight()\n'
        '        self.compact_ui = use_compact_layout(screen_height)\n'
        '        self.geometry(f"980x{window_height_for_screen(screen_height)}")\n'
        '        self.minsize(860, MIN_WINDOW_HEIGHT)\n',
        "compact layout initialization",
    )
    text = replace_between(
        text,
        "    def _build_ui(self) -> None:\n",
        "    def _build_auto_tab",
        BUILD_UI,
        "main layout",
    )
    text = replace_between(
        text,
        "    def _build_auto_tab",
        "    def _build_cleaner_tab",
        AUTO_TAB,
        "auto tab",
    )
    text = replace_between(
        text,
        "    def _build_cleaner_tab",
        "    @staticmethod\n    def _setting_row",
        CLEANER_TAB,
        "cleaner tab",
    )
    text = replace_between(
        text,
        "    @staticmethod\n    def _setting_row",
        "    def _read_auto_config",
        ROW_METHODS,
        "row helpers",
    )
    path.write_text(text, encoding="utf-8")


def patch_workflow() -> None:
    path = Path(".github/workflows/instagram-tools.yml")
    text = path.read_text(encoding="utf-8")

    path_line = '      - "tests/regression/test_instagram_tools.py"\n'
    count = text.count(path_line)
    if count != 2:
        raise RuntimeError(f"layout smoke path: expected two matches, found {count}")
    text = text.replace(
        path_line,
        path_line + '      - "tests/regression/instagram_tools_layout_smoke.py"\n',
    )

    continuation = "\\\n"
    lint_line = "            tests/regression/test_instagram_tools.py " + continuation
    count = text.count(lint_line)
    if count != 2:
        raise RuntimeError(f"layout smoke lint path: expected two matches, found {count}")
    text = text.replace(
        lint_line,
        lint_line + "            tests/regression/instagram_tools_layout_smoke.py " + continuation,
    )

    text = replace_once(
        text,
        "          tests.regression.test_non_follower_cleaner_http_success\n\n  build:\n",
        "          tests.regression.test_non_follower_cleaner_http_success\n"
        "      - name: Verify compact 1366x768 layout\n"
        "        run: |\n"
        "          if ! command -v xvfb-run >/dev/null 2>&1; then\n"
        "            sudo apt-get update\n"
        "            sudo apt-get install -y xvfb\n"
        "          fi\n"
        "          xvfb-run -a -s \"-screen 0 1366x768x24\" "
        "python tests/regression/instagram_tools_layout_smoke.py\n\n"
        "  build:\n",
        "layout smoke workflow step",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_app()
    Path("tests/regression/instagram_tools_layout_smoke.py").write_text(
        LAYOUT_SMOKE,
        encoding="utf-8",
    )
    patch_workflow()


if __name__ == "__main__":
    main()
