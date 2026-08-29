from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, Sequence

FriendshipList = Literal["followers", "following"]
LogCallback = Callable[[str], None]
ProgressCallback = Callable[[dict[str, Any]], None]
WaitFunction = Callable[[threading.Event | None, float], bool]


class NonFollowerCleanerError(RuntimeError):
    code = "NON_FOLLOWER_CLEANER_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


class IncompleteFriendshipListError(NonFollowerCleanerError):
    code = "INCOMPLETE_FRIENDSHIP_LIST"


class FriendshipRequestError(NonFollowerCleanerError):
    code = "FRIENDSHIP_REQUEST_ERROR"


class ViewerAccountChangedError(NonFollowerCleanerError):
    code = "VIEWER_ACCOUNT_CHANGED"


class OperationStopped(NonFollowerCleanerError):
    code = "OPERATION_STOPPED"


class UnfollowRunError(NonFollowerCleanerError):
    code = "UNFOLLOW_RUN_ERROR"

    def __init__(self, message: str, *, summary: UnfollowSummary):
        super().__init__(message)
        self.summary = summary


class FriendshipBackend(Protocol):
    def viewer_id(self) -> str: ...

    def fetch_page(
        self,
        list_name: FriendshipList,
        viewer_id: str,
        cursor: str,
        count: int,
    ) -> dict[str, Any]: ...

    def friendship(self, user_id: str) -> dict[str, Any]: ...

    def unfollow(self, user_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CleanerConfig:
    min_delay_seconds: int = 10
    max_delay_seconds: int = 18
    max_unfollows_per_run: int = 40
    page_delay_seconds: float = 1.0
    page_size: int = 100

    def validate(self) -> CleanerConfig:
        if self.min_delay_seconds < 0:
            raise ValueError("언팔로우 전 최소 대기는 0초 이상이어야 합니다.")
        if self.max_delay_seconds < self.min_delay_seconds:
            raise ValueError("언팔로우 전 최대 대기는 최소 대기 이상이어야 합니다.")
        if self.max_delay_seconds > 3600:
            raise ValueError("언팔로우 전 최대 대기는 3,600초 이하여야 합니다.")
        if self.max_unfollows_per_run < 0:
            raise ValueError("회차당 최대 언팔로우는 0개 이상이어야 합니다.")
        if self.page_delay_seconds < 0:
            raise ValueError("목록 페이지 대기는 0초 이상이어야 합니다.")
        if not 1 <= self.page_size <= 200:
            raise ValueError("목록 페이지 크기는 1~200이어야 합니다.")
        return self


@dataclass(frozen=True, slots=True)
class FriendshipAccount:
    pk: str
    username: str
    full_name: str = ""
    is_private: bool = False
    is_verified: bool = False
    profile_pic_url: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FriendshipAccount:
        pk = str(payload.get("pk") or payload.get("id") or "").strip()
        username = str(payload.get("username") or "").strip()
        if not pk or not username:
            raise IncompleteFriendshipListError(
                "Instagram 목록에서 계정 식별값이 빠진 항목을 받았습니다. "
                "잘못된 언팔로우를 막기 위해 목록 처리를 중지했습니다."
            )
        return cls(
            pk=pk,
            username=username,
            full_name=str(payload.get("full_name") or "").strip(),
            is_private=bool(payload.get("is_private")),
            is_verified=bool(payload.get("is_verified")),
            profile_pic_url=str(payload.get("profile_pic_url") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    viewer_id: str
    followers: tuple[FriendshipAccount, ...]
    following: tuple[FriendshipAccount, ...]
    non_followers: tuple[FriendshipAccount, ...]


@dataclass(slots=True)
class UnfollowSummary:
    selected: int = 0
    eligible: int = 0
    skipped_relationship_changed: int = 0
    deferred_by_limit: int = 0
    attempted: int = 0
    succeeded: list[FriendshipAccount] = field(default_factory=list)
    failed: list[tuple[FriendshipAccount, str]] = field(default_factory=list)
    stopped: bool = False


class NonFollowerCleaner:
    """Compare complete relationship snapshots before issuing any unfollow action."""

    def __init__(
        self,
        backend: FriendshipBackend,
        config: CleanerConfig | None = None,
        *,
        rng: random.Random | Any | None = None,
        wait_fn: WaitFunction | None = None,
        on_log: LogCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.backend = backend
        self.config = (config or CleanerConfig()).validate()
        self.rng = rng or random.Random()
        self.wait_fn = wait_fn or self._default_wait
        self.on_log = on_log or (lambda _message: None)
        self.on_progress = on_progress or (lambda _progress: None)

    def scan(self, stop_event: threading.Event | None = None) -> ScanResult:
        viewer_id = str(self.backend.viewer_id()).strip()
        if not viewer_id:
            raise FriendshipRequestError("로그인한 Instagram 계정 ID를 확인하지 못했습니다.")

        # Both snapshots are completed before any caller can start a write action.
        # A partial followers list would classify real followers as non-followers,
        # so every suspicious pagination state fails closed.
        following = self._collect("following", viewer_id, stop_event)
        followers = self._collect("followers", viewer_id, stop_event)

        follower_ids = {account.pk for account in followers}
        non_followers = tuple(
            account for account in following if account.pk != viewer_id and account.pk not in follower_ids
        )
        result = ScanResult(
            viewer_id=viewer_id,
            followers=followers,
            following=following,
            non_followers=non_followers,
        )
        self.on_progress(
            {
                "phase": "scan_complete",
                "followers": len(followers),
                "following": len(following),
                "non_followers": len(non_followers),
            }
        )
        return result

    def unfollow_selected(
        self,
        selected_user_ids: Sequence[str],
        expected_viewer_id: str,
        stop_event: threading.Event | None = None,
    ) -> UnfollowSummary:
        normalized_ids = (str(user_id).strip() for user_id in selected_user_ids)
        selected = tuple(dict.fromkeys(user_id for user_id in normalized_ids if user_id))
        summary = UnfollowSummary(selected=len(selected))
        if not selected:
            return summary

        expected_viewer_id = str(expected_viewer_id).strip()
        self._require_same_viewer(expected_viewer_id, self.backend.viewer_id())

        self.on_log(
            "실행 직전 팔로워·팔로잉 목록을 다시 확인합니다. 그 사이 나를 팔로우하기 시작한 계정은 자동으로 제외합니다."
        )
        fresh_scan = self.scan(stop_event)
        self._require_same_viewer(expected_viewer_id, fresh_scan.viewer_id)
        fresh_candidates = {account.pk: account for account in fresh_scan.non_followers}
        targets = [fresh_candidates[user_id] for user_id in selected if user_id in fresh_candidates]
        summary.skipped_relationship_changed = len(selected) - len(targets)

        action_limit = self.config.max_unfollows_per_run
        summary.eligible = min(len(targets), action_limit) if action_limit else len(targets)

        for index, account in enumerate(targets):
            if action_limit and summary.attempted >= action_limit:
                summary.deferred_by_limit = len(targets) - index
                break
            if stop_event and stop_event.is_set():
                summary.stopped = True
                break

            self._ensure_run_viewer(expected_viewer_id, summary)

            delay_seconds = int(
                self.rng.randint(
                    self.config.min_delay_seconds,
                    self.config.max_delay_seconds,
                )
            )
            if delay_seconds:
                self.on_log(f"@{account.username} 언팔로우 전 {delay_seconds}초 대기합니다.")
            if self.wait_fn(stop_event, delay_seconds):
                summary.stopped = True
                break

            try:
                self._require_same_viewer(expected_viewer_id, self.backend.viewer_id())
                relationship = self.backend.friendship(account.pk)
                followed_by, following = self._require_relationship_status(relationship)
                if followed_by:
                    summary.skipped_relationship_changed += 1
                    self.on_log(f"@{account.username} 계정이 현재 나를 팔로우하므로 제외했습니다.")
                    continue
                if not following:
                    summary.skipped_relationship_changed += 1
                    self.on_log(f"@{account.username} 계정은 이미 팔로우 중이 아니므로 제외했습니다.")
                    continue
                if stop_event and stop_event.is_set():
                    summary.stopped = True
                    break

                self._require_same_viewer(expected_viewer_id, self.backend.viewer_id())
                summary.attempted += 1
                payload = self.backend.unfollow(account.pk)
                self._require_unfollow_confirmation(payload)
            except ViewerAccountChangedError as exc:
                raise UnfollowRunError(exc.user_message, summary=summary) from exc
            except OperationStopped:
                summary.stopped = True
                break
            except NonFollowerCleanerError as exc:
                summary.failed.append((account, exc.user_message))
                raise UnfollowRunError(
                    "Instagram 응답을 확실히 확인하지 못해 추가 언팔로우를 중지했습니다. "
                    f"마지막 대상: @{account.username}. {exc.user_message}",
                    summary=summary,
                ) from exc
            except Exception as exc:
                summary.failed.append((account, type(exc).__name__))
                raise UnfollowRunError(
                    "언팔로우 요청 결과가 불명확해 추가 작업을 중지했습니다. "
                    f"마지막 대상: @{account.username} ({type(exc).__name__}).",
                    summary=summary,
                ) from exc

            summary.succeeded.append(account)
            self.on_log(f"언팔로우 완료: @{account.username} · 이번 실행 {len(summary.succeeded)}개")
            self.on_progress(
                {
                    "phase": "unfollow",
                    "attempted": summary.attempted,
                    "succeeded": len(summary.succeeded),
                    "eligible": summary.eligible,
                    "username": account.username,
                }
            )
            if stop_event and stop_event.is_set():
                summary.stopped = True
                break

        return summary

    def _ensure_run_viewer(self, expected_viewer_id: str, summary: UnfollowSummary) -> None:
        try:
            self._require_same_viewer(expected_viewer_id, self.backend.viewer_id())
        except ViewerAccountChangedError as exc:
            raise UnfollowRunError(exc.user_message, summary=summary) from exc

    def _collect(
        self,
        list_name: FriendshipList,
        viewer_id: str,
        stop_event: threading.Event | None,
    ) -> tuple[FriendshipAccount, ...]:
        cursor = ""
        seen_cursors: set[str] = set()
        accounts: list[FriendshipAccount] = []
        seen_ids: set[str] = set()
        page_number = 0

        while True:
            if stop_event and stop_event.is_set():
                raise OperationStopped("사용자가 목록 확인을 중지했습니다.")

            payload = self.backend.fetch_page(
                list_name,
                viewer_id,
                cursor,
                self.config.page_size,
            )
            page_number += 1
            self._validate_page(payload, list_name)

            raw_users = payload.get("users")
            if not isinstance(raw_users, list):
                raise IncompleteFriendshipListError(
                    f"Instagram {self._list_label(list_name)} 응답에 계정 목록이 없습니다. "
                    "잘못된 비교를 막기 위해 중지했습니다."
                )

            for raw_user in raw_users:
                if not isinstance(raw_user, dict):
                    raise IncompleteFriendshipListError(
                        f"Instagram {self._list_label(list_name)} 목록 형식이 예상과 다릅니다."
                    )
                account = FriendshipAccount.from_payload(raw_user)
                if account.pk in seen_ids:
                    continue
                seen_ids.add(account.pk)
                accounts.append(account)

            next_cursor = str(payload.get("next_max_id") or "").strip()
            if self._payload_indicates_more(payload) and not next_cursor:
                raise IncompleteFriendshipListError(
                    f"Instagram {self._list_label(list_name)} 응답이 다음 페이지가 있다고 표시했지만 "
                    "페이지 커서를 보내지 않았습니다. 목록 누락 가능성이 있어 중지했습니다."
                )
            self.on_progress(
                {
                    "phase": list_name,
                    "page": page_number,
                    "collected": len(accounts),
                    "has_more": bool(next_cursor),
                }
            )

            if not next_cursor:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise IncompleteFriendshipListError(
                    f"Instagram {self._list_label(list_name)} 페이지 커서가 반복되었습니다. "
                    "목록 전체를 확인하지 못해 중지했습니다."
                )
            if not raw_users:
                self.on_log(
                    f"Instagram {self._list_label(list_name)} 목록에서 빈 중간 페이지를 받았습니다. "
                    "다음 커서로 계속 확인합니다."
                )
            seen_cursors.add(next_cursor)
            if self.wait_fn(stop_event, self.config.page_delay_seconds):
                raise OperationStopped("사용자가 목록 확인을 중지했습니다.")
            cursor = next_cursor

        return tuple(accounts)

    @staticmethod
    def _flag_is_true(value: Any) -> bool:
        return value is True or str(value).strip().casefold() in {"1", "true", "yes"}

    @classmethod
    def _payload_indicates_more(cls, payload: dict[str, Any]) -> bool:
        if any(cls._flag_is_true(payload.get(key)) for key in ("has_more", "more_available", "has_next_page")):
            return True
        page_info = payload.get("page_info")
        return isinstance(page_info, dict) and cls._flag_is_true(page_info.get("has_next_page"))

    @staticmethod
    def _validate_page(payload: dict[str, Any], list_name: FriendshipList) -> None:
        if not isinstance(payload, dict):
            raise IncompleteFriendshipListError(
                f"Instagram {NonFollowerCleaner._list_label(list_name)} 응답이 JSON 객체가 아닙니다."
            )
        limit_value = payload.get("should_limit_list_of_followers")
        list_is_limited = limit_value is True or str(limit_value).strip().casefold() in {
            "1",
            "true",
            "yes",
        }
        if list_is_limited:
            raise IncompleteFriendshipListError(
                "Instagram이 팔로워 목록 일부만 제공했습니다. "
                "실제 팔로워를 잘못 언팔로우할 수 있어 아무 작업도 하지 않았습니다."
            )
        if str(payload.get("status") or "").casefold() == "fail":
            message = str(payload.get("message") or payload.get("error_title") or "알 수 없는 오류")
            raise FriendshipRequestError(
                f"Instagram {NonFollowerCleaner._list_label(list_name)} 요청이 실패했습니다: {message}"
            )

    @staticmethod
    def _require_relationship_status(payload: dict[str, Any]) -> tuple[bool, bool]:
        if not isinstance(payload, dict):
            raise FriendshipRequestError("관계 확인 응답이 JSON 객체가 아닙니다.")
        followed_by = payload.get("followed_by")
        following = payload.get("following")
        if isinstance(followed_by, bool) and isinstance(following, bool):
            return followed_by, following
        message = str(payload.get("message") or payload.get("error_title") or "상태 확인 불가")
        raise FriendshipRequestError(f"Instagram이 현재 상호 팔로우 상태를 명확히 알려주지 않았습니다: {message}")

    @staticmethod
    def _require_unfollow_confirmation(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise FriendshipRequestError("언팔로우 응답이 JSON 객체가 아닙니다.")
        friendship_status = payload.get("friendship_status")
        if not isinstance(friendship_status, dict) or friendship_status.get("following") is not False:
            message = str(payload.get("message") or payload.get("error_title") or "상태 확인 불가")
            raise FriendshipRequestError(f"Instagram이 언팔로우 완료 상태를 확인해 주지 않았습니다: {message}")

    @staticmethod
    def _require_same_viewer(expected_viewer_id: str, current_viewer_id: str) -> None:
        expected_viewer_id = str(expected_viewer_id).strip()
        current_viewer_id = str(current_viewer_id).strip()
        if not expected_viewer_id:
            raise FriendshipRequestError("목록을 확인한 Instagram 계정 ID가 없습니다. 목록을 다시 확인해 주세요.")
        if not current_viewer_id:
            raise FriendshipRequestError("현재 로그인한 Instagram 계정 ID를 확인하지 못했습니다.")
        if current_viewer_id != expected_viewer_id:
            raise ViewerAccountChangedError(
                "목록을 확인한 Instagram 계정과 현재 로그인한 계정이 다릅니다. "
                "다른 계정에서는 언팔로우하지 않았으며 추가 작업을 중지했습니다. "
                "원래 계정으로 로그인한 뒤 목록을 다시 확인하세요."
            )

    @staticmethod
    def _list_label(list_name: FriendshipList) -> str:
        return "팔로워" if list_name == "followers" else "팔로잉"

    @staticmethod
    def _default_wait(stop_event: threading.Event | None, seconds: float) -> bool:
        seconds = max(0.0, float(seconds))
        if stop_event:
            return stop_event.wait(seconds)
        if seconds:
            time.sleep(seconds)
        return False
