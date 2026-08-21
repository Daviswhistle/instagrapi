from __future__ import annotations

import random
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

from .storage import AppConfig, Storage, parse_iso_datetime

LogCallback = Callable[[str], None]
StatusCallback = Callable[[dict[str, Any]], None]
NowFunction = Callable[[], datetime]


class InstagramClient(Protocol):
    user_id: Any

    def get_timeline_feed(self, reason: str = "pull_to_refresh", **kwargs: Any) -> dict[str, Any]: ...

    def iter_user_following_v1(
        self,
        user_id: str,
        amount: int = 0,
        page_size: int = 200,
    ) -> Iterable[Any]: ...

    def media_like(self, media_id: str) -> bool: ...


class AutoLikerError(RuntimeError):
    code = "AUTO_LIKER_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


class PasswordRequiredError(AutoLikerError):
    code = "PASSWORD_REQUIRED"


class BadCredentialsError(AutoLikerError):
    code = "BAD_CREDENTIALS"


class TwoFactorCodeRequiredError(AutoLikerError):
    code = "TWO_FACTOR_REQUIRED"


class InstagramChallengeError(AutoLikerError):
    code = "CHALLENGE_REQUIRED"


class SessionExpiredError(AutoLikerError):
    code = "SESSION_EXPIRED"


class AccountMismatchError(AutoLikerError):
    code = "ACCOUNT_MISMATCH"


class InstagramTemporarilyUnavailableError(AutoLikerError):
    code = "INSTAGRAM_TEMPORARILY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FeedPost:
    media_id: str
    user_id: str
    username: str
    taken_at: datetime | None
    has_liked: bool
    is_ad: bool


@dataclass(slots=True)
class ScanSummary:
    seen: int = 0
    candidates: int = 0
    liked: int = 0
    skipped_probability: int = 0
    skipped_filtered: int = 0
    failed: int = 0
    daily_limit_reached: bool = False
    stopped: bool = False


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    today_likes: int
    daily_limit: int
    following_count: int
    initialized: bool
    last_scan_at: str


class FollowingAutoLiker:
    def __init__(
        self,
        username: str,
        config: AppConfig,
        storage: Storage,
        *,
        client: InstagramClient | None = None,
        rng: random.Random | Any | None = None,
        now_fn: NowFunction | None = None,
        on_log: LogCallback | None = None,
        on_status: StatusCallback | None = None,
    ):
        self.username = username.strip().lstrip("@")
        self.config = config
        self.storage = storage
        self.client = client
        self.rng = rng or random.Random()
        self.now_fn = now_fn or (lambda: datetime.now().astimezone())
        self.on_log = on_log or (lambda _message: None)
        self.on_status = on_status or (lambda _status: None)
        self.state = storage.load_state(self.username)

        for warning in storage.pop_warnings():
            self.log(warning)

    def authenticate(self, password: str = "", verification_code: str = "") -> str:
        """Authenticate and return either ``saved_session`` or ``password``."""
        try:
            from instagrapi import Client
            from instagrapi.exceptions import (
                BadCredentials,
                BadPassword,
                ChallengeRequired,
                ClientError,
                ClientLoginRequired,
                ClientThrottledError,
                ClientUnauthorizedError,
                LoginRequired,
                PleaseWaitFewMinutes,
                TwoFactorRequired,
            )
        except ImportError as exc:
            raise InstagramTemporarilyUnavailableError(
                "instagrapi가 함께 설치되지 않았습니다. 배포된 실행 파일을 다시 내려받아 주세요."
            ) from exc

        session_path = self.storage.session_path(self.username)
        client = Client(
            request_timeout=15,
            session_retry_total=2,
            session_retry_backoff_factor=2,
            delay_range=[1, 3],
        )
        session_loaded = False
        session_invalid = False

        if session_path.exists():
            try:
                client.load_settings(session_path)
                session_loaded = True
            except Exception as exc:
                self.log(f"저장된 로그인 정보를 읽지 못해 새 로그인을 준비합니다 ({type(exc).__name__}).")
                self.storage.quarantine_session(self.username)
                client = Client(
                    request_timeout=15,
                    session_retry_total=2,
                    session_retry_backoff_factor=2,
                    delay_range=[1, 3],
                )

        if session_loaded:
            try:
                account = client.account_info()
            except (LoginRequired, ClientLoginRequired, ClientUnauthorizedError):
                session_invalid = True
            except ClientError as exc:
                if self._looks_like_expired_session(exc):
                    session_invalid = True
                else:
                    raise InstagramTemporarilyUnavailableError(
                        "저장된 로그인 상태를 확인하지 못했습니다. 네트워크를 확인한 뒤 다시 시작하세요."
                    ) from exc
            except Exception as exc:
                raise InstagramTemporarilyUnavailableError(
                    "저장된 로그인 상태를 확인하지 못했습니다. 네트워크를 확인한 뒤 다시 시작하세요."
                ) from exc
            else:
                actual_username = str(getattr(account, "username", "") or getattr(client, "username", ""))
                if actual_username and actual_username.lower() != self.username.lower():
                    raise AccountMismatchError(
                        f"저장된 로그인은 @{actual_username} 계정입니다. 현재 계정 데이터를 초기화한 뒤 다시 로그인하세요."
                    )
                client.username = actual_username or self.username
                client.dump_settings(session_path)
                self.storage.secure_session_file(self.username)
                self.client = client
                self.log(f"@{self.username} 저장된 로그인으로 연결했습니다.")
                return "saved_session"

        if not password:
            if session_invalid:
                raise PasswordRequiredError("저장된 로그인이 만료되었습니다. 비밀번호를 다시 입력하세요.")
            raise PasswordRequiredError("첫 로그인에는 인스타그램 비밀번호가 필요합니다.")

        if not session_loaded:
            self._apply_local_timezone(client)

        try:
            logged_in = client.login(
                self.username,
                password,
                verification_code=verification_code.strip(),
            )
        except TwoFactorRequired as exc:
            raise TwoFactorCodeRequiredError(
                "2단계 인증 코드가 필요합니다. 인증 앱의 현재 코드를 입력하고 다시 시작하세요."
            ) from exc
        except (BadPassword, BadCredentials) as exc:
            raise BadCredentialsError("사용자 이름이나 비밀번호가 올바르지 않습니다.") from exc
        except ChallengeRequired as exc:
            raise InstagramChallengeError(
                "인스타그램의 본인 확인이 필요합니다. 공식 앱을 열어 확인을 완료한 뒤 다시 시작하세요."
            ) from exc
        except (PleaseWaitFewMinutes, ClientThrottledError) as exc:
            raise InstagramTemporarilyUnavailableError(
                "인스타그램이 잠시 요청을 제한했습니다. 자동화를 중지하고 충분히 지난 뒤 다시 시도하세요."
            ) from exc
        except ClientError as exc:
            raise InstagramTemporarilyUnavailableError(
                f"인스타그램 로그인에 실패했습니다: {self._safe_exception_text(exc)}"
            ) from exc
        except Exception as exc:
            raise InstagramTemporarilyUnavailableError(
                "인스타그램에 연결하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 시도하세요."
            ) from exc

        if not logged_in:
            raise InstagramTemporarilyUnavailableError("인스타그램 로그인이 완료되지 않았습니다.")

        client.dump_settings(session_path)
        self.storage.secure_session_file(self.username)
        self.client = client
        self.log(f"@{self.username} 로그인에 성공했습니다. 비밀번호는 저장하지 않았습니다.")
        return "password"

    def initialize_baseline(self, stop_event: threading.Event | None = None) -> int:
        self._require_client()
        self.refresh_following(force=True, stop_event=stop_event)
        if stop_event and stop_event.is_set():
            return 0

        posts = self.fetch_timeline_posts()
        for post in posts:
            self.state.remember_processed(post.media_id)

        self.state.initialized = True
        self.state.last_scan_at = self._now_utc_iso()
        self.save_state()
        self.log(
            f"초기 설정 완료: 현재 피드 {len(posts)}개는 기준선으로만 저장했습니다. "
            "다음에 발견되는 새 게시물부터 처리합니다."
        )
        self.emit_status()
        return len(posts)

    def refresh_following(
        self,
        *,
        force: bool = False,
        stop_event: threading.Event | None = None,
    ) -> int:
        client = self._require_client()
        if not force and not self._following_cache_is_stale():
            return len(self.state.following_ids)

        self.log("팔로잉 목록을 새로 확인하고 있습니다.")
        following_ids: set[str] = set()
        cancelled = False

        if hasattr(client, "iter_user_following_v1"):
            iterator = client.iter_user_following_v1(str(client.user_id), amount=0, page_size=200)
            for user in iterator:
                if stop_event and stop_event.is_set():
                    cancelled = True
                    break
                user_id = str(getattr(user, "pk", "") or "")
                if user_id:
                    following_ids.add(user_id)
        else:
            users = client.user_following(str(client.user_id), amount=0)  # type: ignore[attr-defined]
            iterable = users.values() if isinstance(users, dict) else users
            for user in iterable:
                user_id = str(getattr(user, "pk", "") or "")
                if user_id:
                    following_ids.add(user_id)

        if cancelled:
            self.log("중지 요청을 받아 팔로잉 목록 갱신을 취소했습니다.")
            return len(self.state.following_ids)

        self.state.following_ids = sorted(following_ids)
        self.state.following_refreshed_at = self._now_utc_iso()
        self.save_state()
        self.log(f"팔로잉 {len(following_ids):,}명을 확인했습니다.")
        self.emit_status()
        return len(following_ids)

    def scan_once(self, stop_event: threading.Event | None = None) -> ScanSummary:
        client = self._require_client()
        self.refresh_following(stop_event=stop_event)
        summary = ScanSummary()
        if stop_event and stop_event.is_set():
            summary.stopped = True
            return summary

        posts = self.fetch_timeline_posts()
        summary.seen = len(posts)

        now = self._now()
        now_utc = now.astimezone(timezone.utc)
        following_ids = set(self.state.following_ids)
        excluded = set(self.config.normalized_exclusions())
        processed = set(self.state.processed_media_ids)
        current_user_id = str(getattr(client, "user_id", "") or "")
        candidates: list[FeedPost] = []
        seen_in_response: set[str] = set()

        for post in posts:
            if post.media_id in seen_in_response:
                continue
            seen_in_response.add(post.media_id)
            if post.media_id in processed:
                continue

            should_filter = (
                post.has_liked
                or post.is_ad
                or not post.user_id
                or post.user_id == current_user_id
                or post.user_id not in following_ids
                or post.username.lower() in excluded
                or not self._is_recent(post.taken_at, now_utc)
            )
            if should_filter:
                self._remember_processed(post.media_id, processed)
                summary.skipped_filtered += 1
                continue
            candidates.append(post)

        candidates.sort(key=lambda post: post.taken_at or datetime.max.replace(tzinfo=timezone.utc))
        summary.candidates = len(candidates)
        self.save_state()

        day_key = now.date().isoformat()
        for post in candidates:
            if stop_event and stop_event.is_set():
                summary.stopped = True
                break

            if self.state.likes_for_day(day_key) >= self.config.daily_limit:
                summary.daily_limit_reached = True
                break

            if self.rng.random() * 100 >= self.config.like_probability:
                self._remember_processed(post.media_id, processed)
                summary.skipped_probability += 1
                self.log(f"@{post.username}의 새 게시물을 설정된 좋아요 비율에 따라 건너뛰었습니다.")
                self.save_state()
                continue

            delay_seconds = int(self.rng.randint(self.config.min_delay_seconds, self.config.max_delay_seconds))
            if delay_seconds > 0:
                self.log(f"@{post.username}의 새 게시물을 발견했습니다. {self._format_delay(delay_seconds)} 뒤 처리합니다.")
                if stop_event and stop_event.wait(delay_seconds):
                    summary.stopped = True
                    break

            try:
                liked = bool(client.media_like(post.media_id))
            except Exception as exc:
                if self._runtime_exception_category(exc) != "other":
                    raise
                self._record_failure(post, exc, processed)
                summary.failed += 1
                continue

            if liked:
                self._remember_processed(post.media_id, processed)
                self.state.failed_attempts.pop(post.media_id, None)
                self.state.record_like(day_key)
                summary.liked += 1
                self.log(
                    f"좋아요 완료: @{post.username} · 오늘 "
                    f"{self.state.likes_for_day(day_key)}/{self.config.daily_limit}개"
                )
            else:
                self._record_failure(post, RuntimeError("Instagram returned False"), processed)
                summary.failed += 1

            self.save_state()
            self.emit_status()

        self.state.last_scan_at = self._now_utc_iso()
        self.save_state()
        self.emit_status()

        if summary.daily_limit_reached:
            self.log(f"오늘의 좋아요 한도 {self.config.daily_limit}개에 도달했습니다.")
        elif not summary.stopped:
            self.log(
                f"확인 완료: 피드 {summary.seen}개 · 새 후보 {summary.candidates}개 · "
                f"좋아요 {summary.liked}개"
            )
        return summary

    def fetch_timeline_posts(self, max_pages: int = 2) -> list[FeedPost]:
        client = self._require_client()
        posts: list[FeedPost] = []
        response = client.get_timeline_feed(reason="pull_to_refresh")
        posts.extend(self.extract_feed_posts(response))

        pages_fetched = 1
        while pages_fetched < max(1, int(max_pages)):
            if not response.get("more_available"):
                break
            max_id = str(response.get("next_max_id") or "")
            if not max_id:
                break
            response = client.get_timeline_feed(reason="pagination", max_id=max_id)
            posts.extend(self.extract_feed_posts(response))
            pages_fetched += 1
        return posts

    def run_forever(self, stop_event: threading.Event) -> None:
        self._require_client()
        while not stop_event.is_set():
            try:
                if not self.state.initialized:
                    self.initialize_baseline(stop_event)
                else:
                    self.scan_once(stop_event)
            except Exception as exc:
                category = self._runtime_exception_category(exc)
                if category == "login":
                    raise SessionExpiredError(
                        "인스타그램 로그인이 만료되었습니다. 비밀번호를 입력해 다시 시작하세요."
                    ) from exc
                if category == "challenge":
                    raise InstagramChallengeError(
                        "인스타그램의 본인 확인이 필요합니다. 공식 앱에서 확인을 완료한 뒤 다시 시작하세요."
                    ) from exc
                if category == "rate_limit":
                    cooldown = 60 * 60
                    self.log("인스타그램 요청 제한을 감지해 60분 동안 자동으로 쉽니다.")
                    if stop_event.wait(cooldown):
                        break
                    continue
                self.log(
                    f"피드 확인 중 오류가 발생했습니다 ({type(exc).__name__}). "
                    "다음 확인 주기에 다시 시도합니다."
                )

            if stop_event.wait(self.config.scan_interval_minutes * 60):
                break

        self.log("자동 좋아요를 중지했습니다.")

    def extract_feed_posts(self, response: dict[str, Any]) -> list[FeedPost]:
        posts: list[FeedPost] = []
        for item in response.get("feed_items") or []:
            if not isinstance(item, dict):
                continue
            media = item.get("media_or_ad") or item.get("media")
            if not isinstance(media, dict):
                continue
            user = media.get("user") or {}
            if not isinstance(user, dict):
                user = {}
            user_id = str(user.get("pk") or media.get("user_id") or media.get("owner_id") or "")
            username = str(user.get("username") or media.get("username") or "").strip()
            media_id = self._full_media_id(media, user_id)
            if not media_id:
                continue
            posts.append(
                FeedPost(
                    media_id=media_id,
                    user_id=user_id,
                    username=username or user_id or "알 수 없는 계정",
                    taken_at=self._parse_taken_at(media.get("taken_at") or media.get("taken_at_timestamp")),
                    has_liked=bool(media.get("has_liked")),
                    is_ad=self._is_ad(item, media),
                )
            )
        return posts

    def emit_status(self) -> None:
        now = self._now()
        snapshot = StatusSnapshot(
            today_likes=self.state.likes_for_day(now.date().isoformat()),
            daily_limit=self.config.daily_limit,
            following_count=len(self.state.following_ids),
            initialized=self.state.initialized,
            last_scan_at=self.state.last_scan_at,
        )
        self.on_status(asdict(snapshot))

    def save_state(self) -> None:
        self.storage.save_state(self.username, self.state)

    def log(self, message: str) -> None:
        self.on_log(message)

    def _require_client(self) -> InstagramClient:
        if self.client is None:
            raise AutoLikerError("인스타그램에 먼저 로그인해야 합니다.")
        return self.client

    def _apply_local_timezone(self, client: Any) -> None:
        local_now = self._now()
        offset = local_now.utcoffset()
        if offset is None:
            return
        try:
            client.set_timezone_offset(
                int(offset.total_seconds()),
                timezone_name=local_now.tzname() or "",
            )
        except (AttributeError, TypeError, ValueError):
            pass

    def _following_cache_is_stale(self) -> bool:
        refreshed_at = parse_iso_datetime(self.state.following_refreshed_at)
        if refreshed_at is None:
            return True
        age = self._now().astimezone(timezone.utc) - refreshed_at
        return age >= timedelta(hours=self.config.following_refresh_hours)

    def _is_recent(self, taken_at: datetime | None, now_utc: datetime) -> bool:
        if taken_at is None:
            return False
        taken_at_utc = taken_at.astimezone(timezone.utc)
        age = now_utc - taken_at_utc
        if age < timedelta(minutes=-15):
            return False
        return age <= timedelta(hours=self.config.lookback_hours)

    def _record_failure(self, post: FeedPost, exc: Exception, processed: set[str]) -> None:
        attempts = self.state.failed_attempts.get(post.media_id, 0) + 1
        self.state.failed_attempts[post.media_id] = attempts
        if attempts >= self.config.max_failures_per_media:
            self._remember_processed(post.media_id, processed)
            self.state.failed_attempts.pop(post.media_id, None)
            self.log(
                f"@{post.username} 게시물은 {attempts}회 실패해 더 이상 자동 재시도하지 않습니다 "
                f"({type(exc).__name__})."
            )
        else:
            self.log(
                f"@{post.username} 게시물 좋아요에 실패했습니다. 다음 확인 때 다시 시도합니다 "
                f"({attempts}/{self.config.max_failures_per_media})."
            )
        self.save_state()

    def _remember_processed(self, media_id: str, processed: set[str]) -> None:
        if media_id in processed:
            return
        self.state.remember_processed(media_id)
        processed.add(media_id)

    def _now(self) -> datetime:
        now = self.now_fn()
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc).astimezone()
        return now

    def _now_utc_iso(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _full_media_id(media: dict[str, Any], user_id: str) -> str:
        raw_id = media.get("id") or media.get("media_id") or media.get("pk")
        if raw_id is None:
            return ""
        media_id = str(raw_id)
        if "_" not in media_id and user_id:
            media_id = f"{media_id}_{user_id}"
        return media_id

    @staticmethod
    def _parse_taken_at(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            if stripped.replace(".", "", 1).isdigit():
                value = float(stripped)
            else:
                parsed = parse_iso_datetime(stripped)
                return parsed
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
        return None

    @staticmethod
    def _is_ad(item: dict[str, Any], media: dict[str, Any]) -> bool:
        flag_keys = ("is_ad", "injected", "is_injected")
        value_keys = ("ad_id", "ad_action", "ad_metadata")
        if any(bool(item.get(key)) or bool(media.get(key)) for key in flag_keys):
            return True
        return any(item.get(key) not in (None, "", False, 0, "0") for key in value_keys) or any(
            media.get(key) not in (None, "", False, 0, "0") for key in value_keys
        )

    @staticmethod
    def _looks_like_expired_session(exc: Exception) -> bool:
        message = str(exc).lower()
        return "login_required" in message or "unauthorized" in message or "not logged" in message

    @staticmethod
    def _runtime_exception_category(exc: Exception) -> str:
        name = type(exc).__name__
        if name in {"LoginRequired", "ClientLoginRequired", "ClientUnauthorizedError"}:
            return "login"
        if name in {"ChallengeRequired", "ChallengeError"}:
            return "challenge"
        if name in {"PleaseWaitFewMinutes", "ClientThrottledError"}:
            return "rate_limit"
        return "other"

    @staticmethod
    def _safe_exception_text(exc: Exception) -> str:
        message = " ".join(str(exc).split())
        if not message:
            return type(exc).__name__
        return message[:240]

    @staticmethod
    def _format_delay(seconds: int) -> str:
        minutes, remaining = divmod(seconds, 60)
        if minutes and remaining:
            return f"{minutes}분 {remaining}초"
        if minutes:
            return f"{minutes}분"
        return f"{remaining}초"
