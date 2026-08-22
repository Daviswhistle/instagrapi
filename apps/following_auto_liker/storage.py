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
        if not 1 <= int(self.check_interval_minutes) <= 1440:
            raise ValueError("확인 간격은 1~1440분 사이여야 합니다.")
        if not 0 <= int(self.min_delay_seconds) <= 3600:
            raise ValueError("최소 대기 시간은 0~3600초 사이여야 합니다.")
        if not 0 <= int(self.max_delay_seconds) <= 3600:
            raise ValueError("최대 대기 시간은 0~3600초 사이여야 합니다.")
        if int(self.min_delay_seconds) > int(self.max_delay_seconds):
            raise ValueError("최소 대기 시간은 최대 대기 시간보다 클 수 없습니다.")
        if not 0 <= int(self.max_likes_per_cycle) <= 10_000:
            raise ValueError("한 번에 처리할 최대 좋아요 수는 0~10000 사이여야 합니다. 0은 제한 없음입니다.")
        if not 1 <= int(self.max_scroll_rounds) <= 1000:
            raise ValueError("최대 스크롤 횟수는 1~1000 사이여야 합니다.")
        if not 1 <= int(self.unchanged_scroll_rounds) <= 20:
            raise ValueError("종료 판단 횟수는 1~20 사이여야 합니다.")

        self.check_interval_minutes = int(self.check_interval_minutes)
        self.min_delay_seconds = int(self.min_delay_seconds)
        self.max_delay_seconds = int(self.max_delay_seconds)
        self.max_likes_per_cycle = int(self.max_likes_per_cycle)
        self.max_scroll_rounds = int(self.max_scroll_rounds)
        self.unchanged_scroll_rounds = int(self.unchanged_scroll_rounds)
        return self

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> AppConfig:
        data = data or {}
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        filtered = {key: value for key, value in data.items() if key in allowed}
        try:
            return cls(**filtered).validate()
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
        self._atomic_write_json(self.paths.config, asdict(config))

    def clear_browser_profile(self) -> None:
        if self.paths.chrome_profile.exists():
            shutil.rmtree(self.paths.chrome_profile)
        self.paths.chrome_profile.mkdir(parents=True, exist_ok=True)

    def browser_profile_has_data(self) -> bool:
        try:
            next(self.paths.chrome_profile.iterdir())
        except (StopIteration, FileNotFoundError, OSError):
            return False
        return True

    @staticmethod
    def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
