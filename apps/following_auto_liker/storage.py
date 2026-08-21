from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

APP_DIRECTORY_NAME = "FollowingAutoLiker"
STATE_SCHEMA_VERSION = 1
MAX_PROCESSED_MEDIA_IDS = 10_000

T = TypeVar("T")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _default_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_DIRECTORY_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY_NAME
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "following-auto-liker"


def _account_key(username: str) -> str:
    normalized = username.strip().lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:20]


def _known_fields(model_type: type[T], payload: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(model_type)}
    return {key: value for key, value in payload.items() if key in names}


@dataclass(slots=True)
class AppPaths:
    root: Path

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(_default_root())

    @classmethod
    def from_root(cls, root: Path | str) -> "AppPaths":
        return cls(Path(root))

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def accounts(self) -> Path:
        return self.root / "accounts"

    @property
    def log(self) -> Path:
        return self.root / "app.log"

    def account(self, username: str) -> "AccountPaths":
        return AccountPaths(self.accounts / _account_key(username))


@dataclass(slots=True)
class AccountPaths:
    root: Path

    @property
    def session(self) -> Path:
        return self.root / "session.json"

    @property
    def state(self) -> Path:
        return self.root / "state.json"


@dataclass(slots=True)
class AppConfig:
    username: str = ""
    daily_limit: int = 30
    like_probability: int = 90
    scan_interval_minutes: int = 15
    min_delay_seconds: int = 90
    max_delay_seconds: int = 240
    lookback_hours: int = 24
    following_refresh_hours: int = 24
    excluded_usernames: list[str] = field(default_factory=list)
    max_failures_per_media: int = 3

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AppConfig":
        config = cls(**_known_fields(cls, payload))
        config.username = str(config.username or "").strip().lstrip("@")
        if isinstance(config.excluded_usernames, str):
            config.excluded_usernames = config.excluded_usernames.split(",")
        config.excluded_usernames = config.normalized_exclusions()
        return config

    def normalized_exclusions(self) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for raw_value in self.excluded_usernames:
            value = str(raw_value).strip().lstrip("@").lower()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        return values

    def validate(self) -> None:
        self.username = self.username.strip().lstrip("@")
        if not self.username:
            raise ValueError("인스타그램 사용자 이름을 입력하세요.")
        if not 1 <= int(self.daily_limit) <= 200:
            raise ValueError("하루 최대 좋아요는 1~200 사이여야 합니다.")
        if not 0 <= int(self.like_probability) <= 100:
            raise ValueError("좋아요 비율은 0~100 사이여야 합니다.")
        if not 5 <= int(self.scan_interval_minutes) <= 240:
            raise ValueError("확인 주기는 5~240분 사이여야 합니다.")
        if not 30 <= int(self.min_delay_seconds) <= 3_600:
            raise ValueError("최소 대기 시간은 30~3,600초 사이여야 합니다.")
        if not 30 <= int(self.max_delay_seconds) <= 7_200:
            raise ValueError("최대 대기 시간은 30~7,200초 사이여야 합니다.")
        if int(self.min_delay_seconds) > int(self.max_delay_seconds):
            raise ValueError("최소 대기 시간은 최대 대기 시간보다 클 수 없습니다.")
        if not 1 <= int(self.lookback_hours) <= 168:
            raise ValueError("새 게시물 인정 시간은 1~168시간 사이여야 합니다.")
        if not 1 <= int(self.following_refresh_hours) <= 168:
            raise ValueError("팔로잉 목록 갱신 주기는 1~168시간 사이여야 합니다.")
        if not 1 <= int(self.max_failures_per_media) <= 10:
            raise ValueError("게시물별 최대 재시도 횟수는 1~10 사이여야 합니다.")
        self.excluded_usernames = self.normalized_exclusions()


@dataclass(slots=True)
class PersistentState:
    schema_version: int = STATE_SCHEMA_VERSION
    initialized: bool = False
    processed_media_ids: list[str] = field(default_factory=list)
    failed_attempts: dict[str, int] = field(default_factory=dict)
    like_counts_by_day: dict[str, int] = field(default_factory=dict)
    following_ids: list[str] = field(default_factory=list)
    following_refreshed_at: str = ""
    last_scan_at: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PersistentState":
        state = cls(**_known_fields(cls, payload))
        state.processed_media_ids = [str(value) for value in state.processed_media_ids][-MAX_PROCESSED_MEDIA_IDS:]
        state.failed_attempts = {
            str(key): max(0, int(value)) for key, value in dict(state.failed_attempts).items()
        }
        state.like_counts_by_day = {
            str(key): max(0, int(value)) for key, value in dict(state.like_counts_by_day).items()
        }
        state.following_ids = sorted({str(value) for value in state.following_ids})
        return state

    def remember_processed(self, media_id: str) -> None:
        media_id = str(media_id)
        if not media_id:
            return
        if media_id in self.processed_media_ids:
            return
        self.processed_media_ids.append(media_id)
        if len(self.processed_media_ids) > MAX_PROCESSED_MEDIA_IDS:
            del self.processed_media_ids[: len(self.processed_media_ids) - MAX_PROCESSED_MEDIA_IDS]

    def likes_for_day(self, day_key: str) -> int:
        return int(self.like_counts_by_day.get(day_key, 0))

    def record_like(self, day_key: str) -> None:
        self.like_counts_by_day[day_key] = self.likes_for_day(day_key) + 1
        if len(self.like_counts_by_day) > 45:
            for old_key in sorted(self.like_counts_by_day)[:-45]:
                self.like_counts_by_day.pop(old_key, None)


class StorageError(RuntimeError):
    pass


class Storage:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or AppPaths.default()
        self.warnings: list[str] = []
        self._prepare_directory(self.paths.root)
        self._prepare_directory(self.paths.accounts)

    @classmethod
    def default(cls) -> "Storage":
        return cls(AppPaths.default())

    def account_paths(self, username: str) -> AccountPaths:
        if not username.strip():
            raise StorageError("계정 데이터 경로를 만들려면 사용자 이름이 필요합니다.")
        paths = self.paths.account(username)
        self._prepare_directory(paths.root)
        return paths

    def load_config(self) -> AppConfig:
        return AppConfig.from_dict(self._load_json(self.paths.config))

    def save_config(self, config: AppConfig) -> None:
        self._write_json(self.paths.config, asdict(config))

    def load_state(self, username: str) -> PersistentState:
        return PersistentState.from_dict(self._load_json(self.account_paths(username).state))

    def save_state(self, username: str, state: PersistentState) -> None:
        self._write_json(self.account_paths(username).state, asdict(state))

    def session_path(self, username: str) -> Path:
        return self.account_paths(username).session

    def secure_session_file(self, username: str) -> None:
        path = self.session_path(username)
        if path.exists():
            self._secure_file(path)

    def delete_account_data(self, username: str) -> None:
        if not username.strip():
            return
        path = self.paths.account(username).root
        if path.exists():
            shutil.rmtree(path)

    def quarantine_session(self, username: str) -> None:
        path = self.session_path(username)
        if path.exists():
            self._quarantine(path)

    def pop_warnings(self) -> list[str]:
        warnings, self.warnings = self.warnings, []
        return warnings

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except json.JSONDecodeError:
            backup = self._quarantine(path)
            self.warnings.append(f"손상된 데이터 파일을 새로 만들었습니다: {backup.name}")
            return {}
        except OSError as exc:
            raise StorageError(f"데이터 파일을 읽지 못했습니다: {path}") from exc
        if not isinstance(payload, dict):
            backup = self._quarantine(path)
            self.warnings.append(f"형식이 잘못된 데이터 파일을 새로 만들었습니다: {backup.name}")
            return {}
        return payload

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._prepare_directory(path.parent)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
                temporary_path = Path(file.name)
            os.replace(temporary_path, path)
            self._secure_file(path)
        except OSError as exc:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)
            raise StorageError(f"데이터 파일을 저장하지 못했습니다: {path}") from exc

    def _quarantine(self, path: Path) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.corrupt-{timestamp}")
        try:
            path.replace(backup)
        except OSError as exc:
            raise StorageError(f"손상된 데이터 파일을 격리하지 못했습니다: {path}") from exc
        return backup

    @staticmethod
    def _prepare_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _secure_file(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass
