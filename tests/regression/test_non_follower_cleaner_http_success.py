from __future__ import annotations

import unittest
from typing import Any

from apps.following_auto_liker.engine import LoginRequiredError
from apps.non_follower_cleaner.browser import PlaywrightFriendshipBackend
from apps.non_follower_cleaner.engine import FriendshipRequestError, NonFollowerCleanerError


class FakePage:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.evaluate_calls: list[tuple[str, dict[str, Any]]] = []

    def evaluate(self, script: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.evaluate_calls.append((script, arguments))
        return self.response


class FakeContext:
    def cookies(self, _urls: list[str]) -> list[dict[str, str]]:
        return [
            {"name": "sessionid", "value": "session"},
            {"name": "ds_user_id", "value": "1"},
            {"name": "csrftoken", "value": "csrf"},
        ]


class FakeSession:
    def __init__(self, response: dict[str, Any]):
        self.page = FakePage(response)
        self.context = FakeContext()

    def _require_page(self, *, create_if_missing: bool = False) -> FakePage:
        return self.page

    @staticmethod
    def raise_browser_error(exc: Exception) -> None:
        raise exc


def response(
    *,
    status_code: int = 200,
    text: str = "",
    content_type: str = "text/plain; charset=utf-8",
    redirected: bool = False,
    url: str = "https://www.instagram.com/api/v1/friendships/destroy/2/",
) -> dict[str, Any]:
    return {
        "ok": 200 <= status_code < 300,
        "statusCode": status_code,
        "url": url,
        "redirected": redirected,
        "contentType": content_type,
        "text": text,
    }


class NonJsonUnfollowResponseTestCase(unittest.TestCase):
    def test_successful_non_json_unfollow_response_is_accepted(self) -> None:
        cases = (
            (200, "", "text/plain; charset=utf-8"),
            (200, "OK", "text/plain"),
            (200, "<html><body>accepted</body></html>", "text/html"),
            (204, "", ""),
        )
        for status_code, text, content_type in cases:
            with self.subTest(
                status_code=status_code,
                text=text,
                content_type=content_type,
            ):
                session = FakeSession(
                    response(
                        status_code=status_code,
                        text=text,
                        content_type=content_type,
                    )
                )
                payload = PlaywrightFriendshipBackend(session).unfollow("2")

                self.assertEqual(payload["status"], "ok")
                self.assertIs(payload["friendship_status"]["following"], False)
                self.assertEqual(payload["_transport"]["confirmation"], "http_2xx")
                self.assertEqual(payload["_transport"]["status_code"], status_code)
                script = session.page.evaluate_calls[0][0]
                self.assertIn(
                    'contentType: response.headers.get("content-type") || ""',
                    script,
                )
                self.assertIn("redirected: response.redirected", script)

    def test_valid_json_unfollow_response_is_preserved(self) -> None:
        session = FakeSession(
            response(
                text='{"status":"ok","friendship_status":{"following":false}}',
                content_type="application/json",
            )
        )

        payload = PlaywrightFriendshipBackend(session).unfollow("2")

        self.assertEqual(
            payload,
            {"status": "ok", "friendship_status": {"following": False}},
        )

    def test_login_redirect_still_stops_the_run(self) -> None:
        session = FakeSession(
            response(
                text="<!doctype html><html><body>login</body></html>",
                content_type="text/html",
                redirected=True,
                url="https://www.instagram.com/accounts/login/",
            )
        )

        with self.assertRaises(LoginRequiredError):
            PlaywrightFriendshipBackend(session).unfollow("2")

    def test_plain_text_restriction_still_stops_the_run(self) -> None:
        session = FakeSession(response(text="Please wait a few minutes before you try again."))

        with self.assertRaises(NonFollowerCleanerError):
            PlaywrightFriendshipBackend(session).unfollow("2")

    def test_non_successful_non_json_response_still_fails(self) -> None:
        session = FakeSession(
            response(
                status_code=500,
                text="upstream failure",
                content_type="text/plain",
            )
        )

        with self.assertRaises(FriendshipRequestError) as cm:
            PlaywrightFriendshipBackend(session).unfollow("2")

        self.assertIn("HTTP 500", str(cm.exception))
        self.assertIn("upstream failure", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
