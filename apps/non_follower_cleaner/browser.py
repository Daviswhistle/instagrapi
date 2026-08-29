from __future__ import annotations

import json
import threading
from typing import Any
from urllib.parse import urlencode, urlparse

from apps.following_auto_liker.browser import INSTAGRAM_HOME_URL, ChromeBrowserSession
from apps.following_auto_liker.engine import LoginRequiredError

from .engine import (
    FriendshipList,
    FriendshipRequestError,
    NonFollowerCleanerError,
    OperationStopped,
)

WEB_APP_ID = "936619743392459"
WEB_ASBD_ID = "129477"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20.0

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

_BROWSER_FETCH_SCRIPT = r"""
async ({path, method, body, csrfToken, appId, asbdId, timeoutMs}) => {
  const headers = {
    "Accept": "*/*",
    "X-IG-App-ID": appId,
    "X-ASBD-ID": asbdId,
    "X-IG-WWW-Claim": sessionStorage.getItem("www-claim-v2") || "0",
    "X-Requested-With": "XMLHttpRequest"
  };
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
      body: method === "POST" ? body : undefined,
      signal: controller.signal
    });
    const text = await response.text();
    // Return the untouched JSON text. Parsing it in JavaScript can round
    // Instagram's 64-bit numeric user IDs beyond Number.MAX_SAFE_INTEGER.
    return {
      ok: response.ok,
      statusCode: response.status,
      url: response.url,
      text
    };
  } catch (error) {
    return {networkError: String(error), timedOut};
  } finally {
    clearTimeout(timeoutId);
  }
}
"""


class PlaywrightFriendshipBackend:
    """Call Instagram's same-origin web endpoints inside the dedicated Chrome session."""

    def __init__(
        self,
        session: ChromeBrowserSession,
        *,
        stop_event: threading.Event | None = None,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ):
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
        self.session._safe_goto(page, INSTAGRAM_HOME_URL)
        try:
            page.wait_for_timeout(1_000)
        except Exception as exc:
            self.session.raise_browser_error(exc)

        host = (urlparse(page.url).hostname or "").casefold()
        if host not in {"instagram.com", "www.instagram.com"}:
            raise FriendshipRequestError(
                "Instagram이 아닌 화면으로 이동했습니다. 체크포인트나 로그인 오류 화면인지 확인해 주세요."
            )
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
        return self._request_json(path, method="GET")

    def friendship(self, user_id: str) -> dict[str, Any]:
        query = urlencode({"is_external_deeplink_profile_view": "false"})
        return self._request_json(
            f"/api/v1/friendships/show/{user_id}/?{query}",
            method="GET",
        )

    def unfollow(self, user_id: str) -> dict[str, Any]:
        cookies = self._cookie_map()
        csrf_token = str(cookies.get("csrftoken") or "").strip()
        if not csrf_token:
            raise LoginRequiredError(
                "Instagram CSRF 쿠키를 확인하지 못했습니다. 열린 Chrome에서 페이지를 새로고침한 뒤 다시 시도하세요."
            )
        body = urlencode({"container_module": "profile", "user_id": str(user_id)})
        return self._request_json(
            f"/api/v1/friendships/destroy/{user_id}/",
            method="POST",
            body=body,
            csrf_token=csrf_token,
        )

    def _request_json(
        self,
        path: str,
        *,
        method: str,
        body: str = "",
        csrf_token: str = "",
    ) -> dict[str, Any]:
        self._raise_if_stopped()
        page = self.session._require_page()
        try:
            result = page.evaluate(
                _BROWSER_FETCH_SCRIPT,
                {
                    "path": path,
                    "method": method,
                    "body": body,
                    "csrfToken": csrf_token,
                    "appId": WEB_APP_ID,
                    "asbdId": WEB_ASBD_ID,
                    "timeoutMs": self.request_timeout_ms,
                },
            )
        except Exception as exc:
            self.session.raise_browser_error(exc)

        # A completed POST may already have changed the account. Preserve and
        # validate that response before honoring a stop request so the caller can
        # record the write accurately; GET responses can still stop immediately.
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

        status_code = int(result.get("statusCode") or 0)
        response_url = str(result.get("url") or "")
        response_path = urlparse(response_url).path.casefold()
        response_text = str(result.get("text") or "")
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            payload = None
        payload_dict = payload if isinstance(payload, dict) else {}
        message = " ".join(
            str(value)
            for value in (
                payload_dict.get("message"),
                payload_dict.get("error_type"),
                payload_dict.get("feedback_message"),
                payload_dict.get("error_title"),
            )
            if value
        )
        # Do not scan a successful relationship payload wholesale for words such
        # as ``spam``. Instagram includes unrelated keys such as
        # ``show_spam_follow_request_tab`` in normal responses. Raw response text
        # is only useful when the request failed or was not valid JSON.
        if not message and (not result.get("ok") or payload is None):
            message = response_text[:1000].strip()
        normalized = message.casefold()
        error_type = str(payload_dict.get("error_type") or "").casefold()
        explicit_spam_block = payload_dict.get("spam") is True or error_type == "spam"

        if (
            status_code == 401
            or "login_required" in normalized
            or "/accounts/login" in response_path
            or "/accounts/onetap" in response_path
        ):
            raise LoginRequiredError(
                "Instagram 로그인이 만료되었습니다. 열린 Chrome에서 다시 로그인한 뒤 재시도하세요."
            )
        redirected_to_checkpoint = any(marker in response_path for marker in ("/challenge", "/checkpoint"))
        if (
            status_code in {403, 429}
            or redirected_to_checkpoint
            or explicit_spam_block
            or any(marker in normalized for marker in _RESTRICTION_MARKERS)
        ):
            raise NonFollowerCleanerError(
                "Instagram이 활동을 제한하거나 본인 확인을 요구했습니다. "
                "추가 언팔로우를 즉시 중지했습니다. 공식 Instagram에서 계정 상태를 확인하세요. "
                f"표시 내용: {message or status_code}"
            )
        if not result.get("ok"):
            raise FriendshipRequestError(
                f"Instagram 요청이 실패했습니다 (HTTP {status_code}): {message or '응답 내용 없음'}"
            )
        if not isinstance(payload, dict):
            raise FriendshipRequestError("Instagram이 JSON이 아닌 응답을 보냈습니다.")
        if str(payload.get("status") or "").casefold() == "fail":
            raise FriendshipRequestError(f"Instagram 요청이 실패했습니다: {message or '알 수 없는 오류'}")
        return payload

    def _raise_if_stopped(self) -> None:
        if self.stop_event and self.stop_event.is_set():
            raise OperationStopped("사용자가 미팔로워 정리 작업을 중지했습니다.")

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
