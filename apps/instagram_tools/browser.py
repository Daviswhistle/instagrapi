from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

from apps.following_auto_liker.browser import (
    INSTAGRAM_HOME_URL,
    ChromeBrowserSession,
    is_instagram_hostname,
)
from apps.following_auto_liker.engine import LoginRequiredError
from apps.non_follower_cleaner.engine import (
    FriendshipList,
    FriendshipRequestError,
    NonFollowerCleanerError,
    OperationStopped,
)

WEB_APP_ID = "936619743392459"
WEB_ASBD_ID = "129477"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20.0
POST_CONFIRMATION_DELAY_MS = 350

_RESTRICTION_MARKERS = (
    "feedback_required",
    "challenge_required",
    "checkpoint_required",
    "rate_limit_error",
    "sentry_block",
    "please wait a few minutes",
    "try again later",
    "we restrict certain activity",
    "action blocked",
    "temporarily blocked",
    "나중에 다시 시도",
    "특정 활동을 제한",
    "작업이 차단",
)

_BROWSER_REQUEST_SCRIPT = r"""
async ({path, method, csrfToken, appId, asbdId, timeoutMs, includeIgHeaders}) => {
  const headers = {
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest"
  };
  if (includeIgHeaders) {
    headers["X-IG-App-ID"] = appId;
    headers["X-ASBD-ID"] = asbdId;
    headers["X-IG-WWW-Claim"] = sessionStorage.getItem("www-claim-v2") || "0";
  }
  if (csrfToken) headers["X-CSRFToken"] = csrfToken;
  if (method === "POST") {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  }

  const controller = new AbortController();
  let timedOut = false;
  const timeoutId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(path, {
      method,
      headers,
      credentials: "include",
      cache: "no-store",
      signal: controller.signal
    });
    const text = await response.text();
    return {
      ok: response.ok,
      statusCode: response.status,
      url: response.url,
      redirected: response.redirected,
      contentType: response.headers.get("content-type") || "",
      text
    };
  } catch (error) {
    return {networkError: String(error), timedOut};
  } finally {
    clearTimeout(timeoutId);
  }
}
"""


@dataclass(frozen=True, slots=True)
class BrowserResponse:
    ok: bool
    status_code: int
    url: str
    redirected: bool
    content_type: str
    text: str

    def json_value(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            return None


class SharedChromeBrowserSession(ChromeBrowserSession):
    """Keep one Chrome context alive and avoid navigating to Home twice."""

    def is_alive(self) -> bool:
        if self.context is None:
            return False
        try:
            self._require_page(create_if_missing=True)
        except Exception:
            return False
        return True

    def wait_until_logged_in(self, stop_event: threading.Event) -> None:
        page = self._require_page(create_if_missing=True)
        current_host = urlparse(page.url).hostname

        # A persistent context often already has an authenticated Instagram page.
        # Reusing it avoids the visible blank-page -> Home -> Home reload sequence.
        if not is_instagram_hostname(current_host):
            self._safe_goto(page, INSTAGRAM_HOME_URL)

        if self._has_session_cookie() and not self._page_looks_logged_out(page):
            self.on_log("전용 Chrome에 저장된 Instagram 로그인을 사용합니다.")
            return

        if not is_instagram_hostname(urlparse(page.url).hostname):
            self._safe_goto(page, INSTAGRAM_HOME_URL)

        self.on_log(
            "처음 한 번만 열린 Chrome 창에서 Instagram에 로그인하세요. 앱에는 아이디나 비밀번호를 입력하지 않습니다."
        )
        while not stop_event.is_set():
            page = self._require_page(create_if_missing=True)
            if self._has_session_cookie() and not self._page_looks_logged_out(page):
                self.on_log("Instagram 로그인을 확인했습니다. 다음 실행에도 이 로그인 상태를 사용합니다.")
                return
            stop_event.wait(2)


class VerifiedFriendshipBackend:
    """Read relationship lists and verify that each selected unfollow actually changed state."""

    def __init__(
        self,
        session: SharedChromeBrowserSession,
        *,
        stop_event: threading.Event | None = None,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        request_timeout_seconds = float(request_timeout_seconds)
        if request_timeout_seconds <= 0:
            raise ValueError("Instagram 요청 제한 시간은 0초보다 커야 합니다.")
        self.session = session
        self.stop_event = stop_event
        self.request_timeout_seconds = request_timeout_seconds
        self.request_timeout_ms = max(1, int(request_timeout_seconds * 1000))
        self._viewer_id = ""

    def prepare(self) -> None:
        page = self.session._require_page(create_if_missing=True)
        if not is_instagram_hostname(urlparse(page.url).hostname):
            self.session._safe_goto(page, INSTAGRAM_HOME_URL)
        try:
            page.wait_for_timeout(250)
        except Exception as exc:
            self.session.raise_browser_error(exc)

        cookies = self._cookie_map()
        self._viewer_id = str(cookies.get("ds_user_id") or "").strip()
        if not self._viewer_id or not cookies.get("sessionid"):
            raise LoginRequiredError(
                "Instagram 로그인 정보를 확인하지 못했습니다. 열린 Chrome에서 로그인한 뒤 다시 시도하세요."
            )

    def viewer_id(self) -> str:
        self._raise_if_stopped()
        cookies = self._cookie_map()
        self._viewer_id = str(cookies.get("ds_user_id") or "").strip()
        if not self._viewer_id or not cookies.get("sessionid"):
            raise LoginRequiredError(
                "Instagram 로그인 정보를 확인하지 못했습니다. 열린 Chrome에서 로그인한 뒤 다시 시도하세요."
            )
        return self._viewer_id

    def fetch_page(
        self,
        list_name: FriendshipList,
        viewer_id: str,
        cursor: str,
        count: int,
    ) -> dict[str, Any]:
        query = {"count": str(count)}
        if cursor:
            query["max_id"] = cursor
        path = f"/api/v1/friendships/{viewer_id}/{list_name}/?{urlencode(query)}"
        return self._request_json(path, method="GET", include_ig_headers=True)

    def friendship(self, user_id: str) -> dict[str, Any]:
        query = urlencode(
            {
                "is_external_deeplink_profile_view": "false",
                "_": str(time.time_ns()),
            }
        )
        return self._request_json(
            f"/api/v1/friendships/show/{user_id}/?{query}",
            method="GET",
            include_ig_headers=True,
        )

    def unfollow(self, user_id: str) -> dict[str, Any]:
        """Write first, then verify only that selected account; never pre-scan relationships."""

        self._raise_if_stopped()
        cookies = self._cookie_map()
        csrf_token = str(cookies.get("csrftoken") or "").strip()
        if not csrf_token:
            raise LoginRequiredError(
                "Instagram CSRF 쿠키를 확인하지 못했습니다. 열린 Chrome에서 페이지를 새로고침한 뒤 다시 시도하세요."
            )

        attempts = (
            (f"/api/v1/friendships/destroy/{user_id}/", True),
            (f"/web/friendships/{user_id}/unfollow/", False),
        )
        diagnostics: list[str] = []

        for attempt_number, (path, include_ig_headers) in enumerate(attempts, start=1):
            response = self._request(
                path,
                method="POST",
                csrf_token=csrf_token,
                include_ig_headers=include_ig_headers,
            )
            self._require_expected_final_url(response, path)

            if not response.ok:
                diagnostics.append(f"시도 {attempt_number}: HTTP {response.status_code}")
                continue

            payload = response.json_value()
            if isinstance(payload, dict):
                if str(payload.get("status") or "").casefold() == "fail":
                    message = self._payload_message(payload) or "명시적 실패"
                    diagnostics.append(f"시도 {attempt_number}: {message}")
                    continue
                if self._payload_confirms_unfollow(payload):
                    return payload

            # A 2xx status alone is not proof of a write. Instagram can return a
            # generic HTML/empty response while leaving the relationship intact.
            # Verify only the account just written; this is a post-condition check,
            # not a pre-check or another full follower/following scan.
            self._wait_after_write()
            relationship = self.friendship(user_id)
            following = relationship.get("following")
            if following is False:
                return {
                    "status": "ok",
                    "friendship_status": {"following": False},
                    "_transport": {
                        "confirmation": "post_write_friendship_check",
                        "attempt": attempt_number,
                        "status_code": response.status_code,
                    },
                }
            if following is True:
                diagnostics.append(f"시도 {attempt_number}: 팔로우 상태가 그대로임")
                continue
            raise FriendshipRequestError(
                "언팔로우 요청 뒤 현재 팔로우 상태를 명확히 확인하지 못했습니다. 추가 작업을 중지했습니다."
            )

        detail = " · ".join(diagnostics) or "응답 내용 없음"
        raise FriendshipRequestError(
            f"Instagram이 선택한 계정의 언팔로우를 실제로 반영하지 않았습니다. 두 웹 엔드포인트를 시도한 결과: {detail}"
        )

    def _request_json(
        self,
        path: str,
        *,
        method: str,
        csrf_token: str = "",
        include_ig_headers: bool,
    ) -> dict[str, Any]:
        response = self._request(
            path,
            method=method,
            csrf_token=csrf_token,
            include_ig_headers=include_ig_headers,
        )
        self._require_expected_final_url(response, path)
        if not response.ok:
            raise FriendshipRequestError(
                f"Instagram 요청이 실패했습니다 (HTTP {response.status_code}): "
                f"{self._response_message(response) or '응답 내용 없음'}"
            )
        payload = response.json_value()
        if not isinstance(payload, dict):
            raise FriendshipRequestError(
                "Instagram 응답이 JSON 객체가 아닙니다. "
                f"HTTP {response.status_code}, Content-Type: {response.content_type or '확인 불가'}"
            )
        if str(payload.get("status") or "").casefold() == "fail":
            raise FriendshipRequestError(
                f"Instagram 요청이 실패했습니다: {self._payload_message(payload) or '알 수 없는 오류'}"
            )
        return payload

    def _request(
        self,
        path: str,
        *,
        method: str,
        csrf_token: str = "",
        include_ig_headers: bool,
    ) -> BrowserResponse:
        self._raise_if_stopped()
        page = self.session._require_page()
        try:
            result = page.evaluate(
                _BROWSER_REQUEST_SCRIPT,
                {
                    "path": path,
                    "method": method,
                    "csrfToken": csrf_token,
                    "appId": WEB_APP_ID,
                    "asbdId": WEB_ASBD_ID,
                    "timeoutMs": self.request_timeout_ms,
                    "includeIgHeaders": include_ig_headers,
                },
            )
        except Exception as exc:
            self.session.raise_browser_error(exc)

        if method.upper() != "POST":
            self._raise_if_stopped()
        if not isinstance(result, dict):
            raise FriendshipRequestError("Instagram 요청 결과를 읽지 못했습니다.")
        if result.get("timedOut"):
            raise FriendshipRequestError(
                f"Instagram 네트워크 요청이 {self.request_timeout_seconds:g}초 안에 완료되지 않아 중지했습니다."
            )
        if result.get("networkError"):
            raise FriendshipRequestError(f"Instagram 네트워크 요청에 실패했습니다: {result['networkError']}")

        response = BrowserResponse(
            ok=bool(result.get("ok")),
            status_code=int(result.get("statusCode") or 0),
            url=str(result.get("url") or ""),
            redirected=bool(result.get("redirected")),
            content_type=str(result.get("contentType") or "").casefold(),
            text=str(result.get("text") or ""),
        )
        self._raise_if_login_or_restricted(response)
        return response

    def _raise_if_login_or_restricted(self, response: BrowserResponse) -> None:
        payload = response.json_value()
        payload_dict = payload if isinstance(payload, dict) else {}
        message = self._payload_message(payload_dict)
        if not message and (not response.ok or not isinstance(payload, dict)):
            message = response.text[:1000].strip()
        normalized = message.casefold()
        response_path = urlparse(response.url).path.casefold()
        error_type = str(payload_dict.get("error_type") or "").casefold()
        explicit_spam_block = payload_dict.get("spam") is True or error_type == "spam"

        if (
            response.status_code == 401
            or "login_required" in normalized
            or "/accounts/login" in response_path
            or "/accounts/onetap" in response_path
        ):
            raise LoginRequiredError(
                "Instagram 로그인이 만료되었습니다. 열린 Chrome에서 다시 로그인한 뒤 재시도하세요."
            )

        redirected_to_checkpoint = any(marker in response_path for marker in ("/challenge", "/checkpoint"))
        if (
            response.status_code in {403, 429}
            or redirected_to_checkpoint
            or explicit_spam_block
            or any(marker in normalized for marker in _RESTRICTION_MARKERS)
        ):
            raise NonFollowerCleanerError(
                "Instagram이 활동을 제한하거나 본인 확인을 요구했습니다. "
                "추가 언팔로우를 즉시 중지했습니다. 공식 Instagram에서 계정 상태를 확인하세요. "
                f"표시 내용: {message or response.status_code}"
            )

    @staticmethod
    def _require_expected_final_url(response: BrowserResponse, requested_path: str) -> None:
        parsed = urlparse(response.url)
        expected_path = urlparse(requested_path).path.rstrip("/").casefold()
        actual_path = parsed.path.rstrip("/").casefold()
        if (
            not response.url
            or response.redirected
            or not is_instagram_hostname(parsed.hostname)
            or actual_path != expected_path
        ):
            raise FriendshipRequestError(
                "Instagram 요청이 예상한 엔드포인트에서 완료되지 않았습니다. "
                f"HTTP {response.status_code}, 최종 경로: {actual_path or '확인 불가'}, "
                f"리다이렉트: {'예' if response.redirected else '아니오'}"
            )

    @staticmethod
    def _payload_confirms_unfollow(payload: dict[str, Any]) -> bool:
        friendship_status = payload.get("friendship_status")
        if isinstance(friendship_status, dict) and friendship_status.get("following") is False:
            return True
        return payload.get("following") is False

    @staticmethod
    def _payload_message(payload: dict[str, Any]) -> str:
        return " ".join(
            str(value)
            for value in (
                payload.get("message"),
                payload.get("error_type"),
                payload.get("feedback_message"),
                payload.get("error_title"),
            )
            if value
        )

    def _response_message(self, response: BrowserResponse) -> str:
        payload = response.json_value()
        if isinstance(payload, dict):
            return self._payload_message(payload)
        return response.text[:1000].strip()

    def _wait_after_write(self) -> None:
        page = self.session._require_page()
        try:
            page.wait_for_timeout(POST_CONFIRMATION_DELAY_MS)
        except Exception as exc:
            self.session.raise_browser_error(exc)

    def _raise_if_stopped(self) -> None:
        if self.stop_event and self.stop_event.is_set():
            raise OperationStopped("사용자가 Instagram 작업을 중지했습니다.")

    def _cookie_map(self) -> dict[str, str]:
        if self.session.context is None:
            raise FriendshipRequestError("Chrome이 실행되지 않았습니다.")
        try:
            cookies = self.session.context.cookies([INSTAGRAM_HOME_URL])
        except Exception as exc:
            self.session.raise_browser_error(exc)
        return {
            str(cookie.get("name") or ""): str(cookie.get("value") or "") for cookie in cookies if cookie.get("name")
        }
