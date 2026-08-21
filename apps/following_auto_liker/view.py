from __future__ import annotations

from tkinter import IntVar, StringVar
from tkinter import scrolledtext, ttk

APP_TITLE = "팔로잉 자동 좋아요"


class AutoLikerViewMixin:
    def _build_variables(self) -> None:
        config = self.saved_config
        self.username_var = StringVar(value=config.username)
        self.password_var = StringVar()
        self.verification_code_var = StringVar()
        self.daily_limit_var = IntVar(value=config.daily_limit)
        self.like_probability_var = IntVar(value=config.like_probability)
        self.scan_interval_var = IntVar(value=config.scan_interval_minutes)
        self.min_delay_var = IntVar(value=config.min_delay_seconds)
        self.max_delay_var = IntVar(value=config.max_delay_seconds)
        self.lookback_var = IntVar(value=config.lookback_hours)
        self.exclusions_var = StringVar(value=", ".join(config.excluded_usernames))

        self.run_state_var = StringVar(value="중지됨")
        self.today_likes_var = StringVar(value=f"0 / {config.daily_limit}")
        self.following_count_var = StringVar(value="0명")
        self.last_scan_var = StringVar(value="아직 없음")
        self.baseline_var = StringVar(value="첫 실행 시 현재 피드를 기준선으로 저장")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(5, weight=1)

        title = ttk.Label(root, text=APP_TITLE, font=("TkDefaultFont", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")
        ttk.Label(
            root,
            text="내가 팔로우한 계정의 새 게시물만 천천히 확인해 좋아요를 누릅니다.",
        ).grid(row=1, column=0, sticky="w", pady=(2, 14))

        account_frame = ttk.LabelFrame(root, text="인스타그램 계정", padding=12)
        account_frame.grid(row=2, column=0, sticky="ew")
        account_frame.columnconfigure(1, weight=1)

        ttk.Label(account_frame, text="사용자 이름").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        username_entry = ttk.Entry(account_frame, textvariable=self.username_var)
        username_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self.editable_widgets.append(username_entry)

        ttk.Label(account_frame, text="비밀번호").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        password_entry = ttk.Entry(account_frame, textvariable=self.password_var, show="*")
        password_entry.grid(row=1, column=1, sticky="ew", pady=4)
        self.editable_widgets.append(password_entry)

        ttk.Label(account_frame, text="2단계 인증 코드").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        code_entry = ttk.Entry(account_frame, textvariable=self.verification_code_var, width=18)
        code_entry.grid(row=2, column=1, sticky="w", pady=4)
        self.editable_widgets.append(code_entry)

        ttk.Label(
            account_frame,
            text="비밀번호는 저장하지 않습니다. 첫 로그인 또는 세션 만료 때만 입력하세요.",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        settings_frame = ttk.LabelFrame(root, text="자동화 설정", padding=12)
        settings_frame.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        settings_frame.columnconfigure(1, weight=1)
        settings_frame.columnconfigure(3, weight=1)

        self._add_spinbox(
            settings_frame,
            row=0,
            label="하루 최대 좋아요",
            variable=self.daily_limit_var,
            from_=1,
            to=200,
            suffix="개",
            column=0,
        )
        self._add_spinbox(
            settings_frame,
            row=0,
            label="좋아요 비율",
            variable=self.like_probability_var,
            from_=0,
            to=100,
            suffix="%",
            column=2,
        )
        self._add_spinbox(
            settings_frame,
            row=1,
            label="피드 확인 주기",
            variable=self.scan_interval_var,
            from_=5,
            to=240,
            suffix="분",
            column=0,
        )
        self._add_spinbox(
            settings_frame,
            row=1,
            label="새 글 인정 시간",
            variable=self.lookback_var,
            from_=1,
            to=168,
            suffix="시간",
            column=2,
        )
        self._add_spinbox(
            settings_frame,
            row=2,
            label="좋아요 전 최소 대기",
            variable=self.min_delay_var,
            from_=30,
            to=3_600,
            suffix="초",
            column=0,
        )
        self._add_spinbox(
            settings_frame,
            row=2,
            label="좋아요 전 최대 대기",
            variable=self.max_delay_var,
            from_=30,
            to=7_200,
            suffix="초",
            column=2,
        )

        ttk.Label(settings_frame, text="제외할 계정").grid(row=3, column=0, sticky="w", pady=(8, 4))
        exclusions_entry = ttk.Entry(settings_frame, textvariable=self.exclusions_var)
        exclusions_entry.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(8, 4))
        self.editable_widgets.append(exclusions_entry)
        ttk.Label(settings_frame, text="쉼표로 구분합니다. 예: account1, account2").grid(
            row=4,
            column=1,
            columnspan=3,
            sticky="w",
        )

        controls = ttk.Frame(root)
        controls.grid(row=4, column=0, sticky="ew", pady=12)
        controls.columnconfigure(4, weight=1)

        self.start_button = ttk.Button(controls, text="시작", command=self._start)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="중지", command=self._stop, state="disabled")
        self.stop_button.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(controls, text="계정 데이터 초기화", command=self._reset_account).grid(
            row=0,
            column=2,
            padx=(0, 8),
        )
        ttk.Button(controls, text="데이터 폴더 열기", command=self._open_data_folder).grid(row=0, column=3)

        lower = ttk.Frame(root)
        lower.grid(row=5, column=0, sticky="nsew")
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(1, weight=1)

        status_frame = ttk.LabelFrame(lower, text="상태", padding=12)
        status_frame.grid(row=0, column=0, sticky="ew")
        for column in range(4):
            status_frame.columnconfigure(column, weight=1)

        self._add_status(status_frame, 0, "실행 상태", self.run_state_var)
        self._add_status(status_frame, 1, "오늘 좋아요", self.today_likes_var)
        self._add_status(status_frame, 2, "확인한 팔로잉", self.following_count_var)
        self._add_status(status_frame, 3, "마지막 확인", self.last_scan_var)
        ttk.Label(status_frame, textvariable=self.baseline_var).grid(
            row=2,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        log_frame = ttk.LabelFrame(lower, text="실행 기록", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        warning = ttk.Label(
            lower,
            text=(
                "주의: 이 앱은 Instagram의 비공식 Private API를 사용합니다. 계정 확인이나 기능 제한이 발생할 수 "
                "있으므로 보수적인 기본값을 유지하고, 문제가 생기면 즉시 중지하세요."
            ),
            wraplength=720,
        )
        warning.grid(row=2, column=0, sticky="ew", pady=(10, 0))

    def _add_spinbox(
        self,
        parent: ttk.LabelFrame,
        *,
        row: int,
        label: str,
        variable: IntVar,
        from_: int,
        to: int,
        suffix: str,
        column: int,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=4)
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=column + 1, sticky="w", padx=(0, 18), pady=4)
        spinbox = ttk.Spinbox(holder, textvariable=variable, from_=from_, to=to, width=8)
        spinbox.grid(row=0, column=0)
        ttk.Label(holder, text=suffix).grid(row=0, column=1, padx=(4, 0))
        self.editable_widgets.append(spinbox)

    @staticmethod
    def _add_status(parent: ttk.LabelFrame, column: int, title: str, variable: StringVar) -> None:
        ttk.Label(parent, text=title).grid(row=0, column=column, sticky="w")
        ttk.Label(parent, textvariable=variable, font=("TkDefaultFont", 11, "bold")).grid(
            row=1,
            column=column,
            sticky="w",
            pady=(2, 0),
        )

