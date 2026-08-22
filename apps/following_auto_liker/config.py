from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class StoragePaths:
    root: Path
    config: Path
    chrome_profile: Path
    log: Path


@dataclass(slots=True)
class AppConfig:
    check_interval_minutes: int = 30
    min_delay_seconds: int = 3
    max_delay_seconds: int = 5
    max_likes_per_cycle: int = 0
    max_scroll_rounds: int = 120
    unchanged_scroll_rounds: int = 4

    def validate(self) -> AppConfig:
        values = {
            "check_interval_minutes": (1, 1440, "확인 간격"),
            "min_delay_seconds": (0, 3600, "최소 대기 시간"),
            "max_delay_seconds": (0, 3600, "최대 대기 시간"),
            "max_likes_per_cycle": (0, 10000, "회차당 최대 좋아요"),
            "max_scroll_rounds": (1, 1000, "최대 스크롤 횟수"),
            "unchanged_scroll_rounds": (1, 20, "종료 판단 횟수"),
        }
        for name, (low, high, label) in values.items():
            value = int(getattr(self, name))
            if not low <= value <= high:
                raise ValueError(f"{label}은(는) {low}~{high} 사이여야 합니다.")
            setattr(self, name, value)
        if self.min_delay_seconds > self.max_delay_seconds:
            raise ValueError("최소 대기 시간은 최대 대기 시간보다 클 수 없습니다.")
        return self

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> AppConfig:
        data = data or {}
        allowed = set(cls.__dataclass_fields__)
        try:
            return cls(**{key: value for key, value in data.items() if key in allowed}).validate()
        except (TypeError, ValueError):
            return cls().validate()


class Storage:
    def __init__(self, paths: StoragePaths):
        self.paths = paths
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.chrome_profile.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default(cls) -> Storage:
        home = Path.home()
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
            root = base / "FollowingAutoLiker"
        elif sys.platform == "darwin":
            root = home / "Library" / "Application Support" / "FollowingAutoLiker"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
            root = base / "following-auto-liker"
        return cls(
            StoragePaths(
                root=root,
                config=root / "config.json",
                chrome_profile=root / "chrome-profile",
                log=root / "app.log",
            )
        )

    def load_config(self) -> AppConfig:
        try:
            data = json.loads(self.paths.config.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return AppConfig().validate()
        return AppConfig.from_mapping(data if isinstance(data, dict) else {})

    def save_config(self, config: AppConfig) -> None:
        config.validate()
        self.paths.config.parent.mkdir(parents=True, exist_ok=True)
        temp = self.paths.config.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.paths.config)

    def reset_chrome_profile(self) -> None:
        if self.paths.chrome_profile.exists():
            shutil.rmtree(self.paths.chrome_profile)
        self.paths.chrome_profile.mkdir(parents=True, exist_ok=True)

    def chrome_profile_has_data(self) -> bool:
        try:
            next(self.paths.chrome_profile.iterdir())
        except (StopIteration, FileNotFoundError, OSError):
            return False
        return True
