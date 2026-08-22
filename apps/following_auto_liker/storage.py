from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO


class AppAlreadyRunningError(RuntimeError):
    """Raised when another process already owns the app/profile lock."""


class InstanceLock:
    """Cross-platform advisory lock held for the lifetime of the desktop app."""

    def __init__(self, path: Path, handle: BinaryIO):
        self.path = path
        self._handle: BinaryIO | None = handle

    @classmethod
    def acquire(cls, path: Path) -> InstanceLock:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise AppAlreadyRunningError(
                "팔로잉 자동 좋아요가 이미 실행 중입니다. 기존 앱과 전용 Chrome 창을 닫은 뒤 다시 실행하세요."
            ) from exc
        return cls(path, handle)

    @property
    def locked(self) -> bool:
        return self._handle is not None

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


@dataclass(frozen=True, slots=True)
class StoragePaths:
    root: Path
    config: Path
    chrome_profile: Path
    log: Path
    instance_lock: Path


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
        self._instance_lock: InstanceLock | None = None
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
                instance_lock=root / "app.lock",
            )
        )

    def acquire_instance_lock(self) -> InstanceLock:
        if self._instance_lock is not None and self._instance_lock.locked:
            return self._instance_lock
        self._instance_lock = InstanceLock.acquire(self.paths.instance_lock)
        return self._instance_lock

    @property
    def instance_lock_held(self) -> bool:
        return self._instance_lock is not None and self._instance_lock.locked

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
        if not self.instance_lock_held:
            raise AppAlreadyRunningError(
                "전용 Chrome 데이터를 지우려면 이 앱의 단일 실행 잠금이 필요합니다. 다른 실행 창을 모두 닫아 주세요."
            )
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
