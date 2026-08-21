"""Pure, testable logic for the Following Auto Like desktop application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
MAX_PROCESSED_MEDIA_IDS = 5_000


@dataclass(frozen=True)
class AutomationConfig:
    """User-facing pacing and selection settings."""

    daily_limit: int = 20
    check_interval_minutes: int = 30
    like_probability: float = 0.90
    lookback_hours: int = 24
    min_delay_seconds: int = 60
    max_delay_seconds: int = 150

    def validate(self) -> None:
        if not 1 <= self.daily_limit <= 100:
            raise ValueError("하루 최대 좋아요는 1~100 사이여야 합니다.")
        if not 15 <= self.check_interval_minutes <= 360:
            raise ValueError("확인 간격은 15~360분 사이여야 합니다.")
        if not 0.10 <= self.like_probability <= 1.0:
            raise ValueError("좋아요 비율은 10~100% 사이여야 합니다.")
        if not 1 <= self.lookback_hours <= 72:
            raise ValueError("새 글로 볼 시간은 1~72시간 사이여야 합니다.")
        if not 20 <= self.min_delay_seconds <= 900:
            raise ValueError("최소 대기 시간은 20~900초 사이여야 합니다.")
        if not self.min_delay_seconds <= self.max_delay_seconds <= 1_800:
            raise ValueError("최대 대기 시간은 최소 대기 이상, 1,800초 이하여야 합니다.")

    def to_mapping(self) -> dict[str, int | float]:
        return {
            "daily_limit": self.daily_limit,
            "check_interval_minutes": self.check_interval_minutes,
            "like_probability": self.like_probability,
            "lookback_hours": self.lookback_hours,
            "min_delay_seconds": self.min_delay_seconds,
            "max_delay_seconds": self.max_delay_seconds,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> AutomationConfig:
        value = value or {}
        config = cls(
            daily_limit=int(value.get("daily_limit", cls.daily_limit)),
            check_interval_minutes=int(
                value.get("check_interval_minutes", cls.check_interval_minutes)
            ),
            like_probability=float(value.get("like_probability", cls.like_probability)),
            lookback_hours=int(value.get("lookback_hours", cls.lookback_hours)),
            min_delay_seconds=int(
                value.get("min_delay_seconds", cls.min_delay_seconds)
            ),
            max_delay_seconds=int(
                value.get("max_delay_seconds", cls.max_delay_seconds)
            ),
        )
        config.validate()
        return config


@dataclass(frozen=True)
class AccountPaths:
    """Per-account paths, with no password stored anywhere."""

    session: Path
    state: Path

    @classmethod
    def for_username(cls, base_dir: Path, username: str) -> AccountPaths:
        canonical = username.strip().lower()
        if not canonical:
            raise ValueError("Instagram 사용자 이름을 입력하세요.")
        slug = re.sub(r"[^a-z0-9_.-]+", "_", canonical).strip("._-") or "account"
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
        account_dir = base_dir / "accounts" / f"{slug[:48]}-{digest}"
        return cls(
            session=account_dir / "session.json",
            state=account_dir / "state.json",
        )


@dataclass
class AccountState:
    """Small persistent state used to prevent duplicate or historical likes."""

    initialized: bool = False
    processed_media_ids: list[str] = field(default_factory=list)
    daily_date: str = ""
    daily_likes: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> AccountState:
        value = value or {}
        raw_ids = value.get("processed_media_ids", [])
        unique_ids: list[str] = []
        seen: set[str] = set()
        if isinstance(raw_ids, list):
            for raw_id in raw_ids:
                media_id = str(raw_id).strip()
                if media_id and media_id not in seen:
                    seen.add(media_id)
                    unique_ids.append(media_id)
        return cls(
            initialized=bool(value.get("initialized", False)),
            processed_media_ids=unique_ids[-MAX_PROCESSED_MEDIA_IDS:],
            daily_date=str(value.get("daily_date", "")),
            daily_likes=max(0, int(value.get("daily_likes", 0))),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "initialized": self.initialized,
            "processed_media_ids": self.processed_media_ids[-MAX_PROCESSED_MEDIA_IDS:],
            "daily_date": self.daily_date,
            "daily_likes": self.daily_likes,
        }

    def processed_set(self) -> set[str]:
        return set(self.processed_media_ids)

    def mark_processed(self, media_ids: list[str] | tuple[str, ...] | set[str]) -> None:
        existing = set(self.processed_media_ids)
        for raw_id in media_ids:
            media_id = str(raw_id).strip()
            if media_id and media_id not in existing:
                existing.add(media_id)
                self.processed_media_ids.append(media_id)
        if len(self.processed_media_ids) > MAX_PROCESSED_MEDIA_IDS:
            self.processed_media_ids = self.processed_media_ids[
                -MAX_PROCESSED_MEDIA_IDS:
            ]

    def ensure_day(self, day: str) -> bool:
        if self.daily_date == day:
            return False
        self.daily_date = day
        self.daily_likes = 0
        return True

    def likes_today(self, day: str) -> int:
        if self.daily_date != day:
            return 0
        return self.daily_likes

    def record_like(self, media_id: str, day: str) -> None:
        self.ensure_day(day)
        self.mark_processed([media_id])
        self.daily_likes += 1


class StateStore:
    """Atomic JSON persistence for one account."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AccountState:
        if not self.path.exists():
            return AccountState()
        try:
            return AccountState.from_mapping(read_json_file(self.path))
        except (OSError, TypeError, ValueError):
            return AccountState()

    def save(self, state: AccountState) -> None:
        write_json_file(self.path, state.to_mapping())


@dataclass(frozen=True)
class MediaCandidate:
    media_id: str
    user_id: str
    username: str
    taken_at: datetime
    code: str = ""
    product_type: str = ""

    @property
    def label(self) -> str:
        if self.code:
            return f"@{self.username} · {self.code}"
        return f"@{self.username}"


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[MediaCandidate, ...]
    handled_media_ids: tuple[str, ...]
    counters: Mapping[str, int]


def app_data_dir() -> Path:
    """Return a private, per-user application data directory."""

    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / "FollowingAutoLike"
    path.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path, directory=True)
    return path


def local_day(now: datetime | None = None) -> str:
    local_now = now or datetime.now().astimezone()
    return local_now.date().isoformat()


def read_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def write_json_file(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path.parent, directory=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        _restrict_permissions(temporary_path, directory=False)
        os.replace(temporary_path, path)
        _restrict_permissions(path, directory=False)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def baseline_media_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return every visible media id so first run never likes historical posts."""

    media_ids: list[str] = []
    seen: set[str] = set()
    for item in _feed_items(payload):
        media = _extract_media(item)
        if media is None:
            continue
        media_id = _media_id(media)
        if media_id and media_id not in seen:
            seen.add(media_id)
            media_ids.append(media_id)
    return tuple(media_ids)


def scan_timeline(
    payload: Mapping[str, Any],
    *,
    following_ids: set[str],
    processed_ids: set[str],
    lookback_hours: int,
    now: datetime | None = None,
    own_user_id: str | None = None,
) -> ScanResult:
    """Select only recent, unliked media from accounts the user actually follows."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    cutoff = current_time - timedelta(hours=lookback_hours)
    future_tolerance = current_time + timedelta(minutes=10)
    normalized_following = {str(user_id) for user_id in following_ids}
    normalized_own_id = str(own_user_id) if own_user_id is not None else ""

    candidates: list[MediaCandidate] = []
    handled_ids: list[str] = []
    counters: Counter[str] = Counter()

    for item in _feed_items(payload):
        media = _extract_media(item)
        if media is None:
            counters["without_media"] += 1
            continue

        media_id = _media_id(media)
        if not media_id:
            counters["without_media_id"] += 1
            continue
        if media_id in processed_ids:
            counters["already_processed"] += 1
            continue

        if _is_ad_or_suggested(item, media):
            handled_ids.append(media_id)
            counters["ad_or_suggested"] += 1
            continue

        user = _media_user(media)
        user_id = _normalize_id(user.get("pk") or user.get("id"))
        if not user_id:
            handled_ids.append(media_id)
            counters["without_user"] += 1
            continue
        if user_id == normalized_own_id:
            handled_ids.append(media_id)
            counters["own_media"] += 1
            continue
        if user_id not in normalized_following:
            handled_ids.append(media_id)
            counters["not_following"] += 1
            continue
        if _truthy(media.get("has_liked")):
            handled_ids.append(media_id)
            counters["already_liked"] += 1
            continue

        taken_at = _as_utc_datetime(
            media.get("taken_at")
            or media.get("taken_at_ts")
            or media.get("device_timestamp")
        )
        if taken_at is None:
            handled_ids.append(media_id)
            counters["without_timestamp"] += 1
            continue
        if taken_at < cutoff:
            handled_ids.append(media_id)
            counters["too_old"] += 1
            continue
        if taken_at > future_tolerance:
            handled_ids.append(media_id)
            counters["future_timestamp"] += 1
            continue

        username = str(user.get("username") or user_id)
        candidates.append(
            MediaCandidate(
                media_id=media_id,
                user_id=user_id,
                username=username,
                taken_at=taken_at,
                code=str(media.get("code") or ""),
                product_type=str(media.get("product_type") or ""),
            )
        )
        counters["candidate"] += 1

    candidates.sort(key=lambda candidate: candidate.taken_at)
    return ScanResult(
        candidates=tuple(candidates),
        handled_media_ids=tuple(dict.fromkeys(handled_ids)),
        counters=dict(counters),
    )


def _feed_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_items = payload.get("feed_items", [])
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, Mapping)]


def _extract_media(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("media_or_ad", "media"):
        media = item.get(key)
        if isinstance(media, Mapping):
            return media
    return None


def _media_user(media: Mapping[str, Any]) -> Mapping[str, Any]:
    user = media.get("user") or media.get("owner")
    if isinstance(user, Mapping):
        return user
    return {}


def _media_id(media: Mapping[str, Any]) -> str:
    user = _media_user(media)
    user_id = _normalize_id(user.get("pk") or user.get("id"))
    raw_id = media.get("id")
    if raw_id is not None:
        media_id = str(raw_id).strip()
        if media_id:
            if "_" not in media_id and user_id:
                return f"{media_id}_{user_id}"
            return media_id
    media_pk = _normalize_id(media.get("pk"))
    if media_pk and user_id:
        return f"{media_pk}_{user_id}"
    return media_pk


def _is_ad_or_suggested(
    item: Mapping[str, Any], media: Mapping[str, Any]
) -> bool:
    boolean_markers = (
        "is_ad",
        "is_suggested",
        "suggested_post",
        "injected",
    )
    value_markers = (
        "ad_id",
        "ad_action",
        "ad_metadata",
        "suggested_reason",
        "suggested_post_info",
    )
    for source in (item, media):
        if any(_truthy(source.get(key)) for key in boolean_markers):
            return True
        if any(source.get(key) not in (None, "", [], {}) for key in value_markers):
            return True
    return False


def _normalize_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "null"}
    return bool(value)


def _as_utc_datetime(value: Any) -> datetime | None:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1_000
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.replace(".", "", 1).isdigit():
            return _as_utc_datetime(float(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _restrict_permissions(path: Path, *, directory: bool) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass
