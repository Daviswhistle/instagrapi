from __future__ import annotations

import threading
import unittest
from collections import deque
from typing import Any

from apps.non_follower_cleaner.browser import PlaywrightFriendshipBackend
from apps.non_follower_cleaner.engine import (
    CleanerConfig,
    FriendshipAccount,
    FriendshipRequestError,
    IncompleteFriendshipListError,
    NonFollowerCleaner,
    NonFollowerCleanerError,
    OperationStopped,
    UnfollowRunError,
    ViewerAccountChangedError,
)


def user(pk: str, username: str | None = None, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pk": pk,
        "username": username or f"user_{pk}",
        "full_name": f"User {pk}",
        "is_private": False,
        "is_verified": False,
    }
    payload.update(overrides)
    return payload


class FakeBackend:
    def __init__(self, pages: dict[tuple[str, str], dict[str, Any]]):
        self.pages = pages
        self.fetch_calls: list[tuple[str, str, str, int]] = []
        self.friendship_calls: list[str] = []
        self.friendship_results: dict[str, object] = {}
        self.unfollow_calls: list[str] = []
        self.unfollow_results: dict[str, object] = {}
        self.current_viewer_id = "1"
        self.viewer_id_calls = 0

    def viewer_id(self) -> str:
        self.viewer_id_calls += 1
        return self.current_viewer_id

    def fetch_page(self, list_name: str, viewer_id: str, cursor: str, count: int):
        self.fetch_calls.append((list_name, viewer_id, cursor, count))
        return self.pages[(list_name, cursor)]

    def friendship(self, user_id: str):
        self.friendship_calls.append(user_id)
        result = self.friendship_results.get(
            user_id,
            {"status": "ok", "followed_by": False, "following": True},
        )
        if isinstance(result, Exception):
            raise result
        return result

    def unfollow(self, user_id: str):
        self.unfollow_calls.append(user_id)
        result = self.unfollow_results.get(
            user_id,
            {"status": "ok", "friendship_status": {"following": False}},
        )
        if isinstance(result, Exception):
            raise result
        return result


class SequenceRandom:
    def __init__(self, values: list[int]):
        self.values = deque(values)

    def randint(self, _minimum: int, _maximum: int) -> int:
        return self.values.popleft()


class NonFollowerCleanerEngineTestCase(unittest.TestCase):
    def test_scan_paginates_both_lists_and_preserves_following_order(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2"), user("3")], "next_max_id": "next"},
                ("following", "next"): {"users": [user("4"), user("3")], "next_max_id": None},
                ("followers", ""): {"users": [user("3"), user("9")], "next_max_id": None},
            }
        )
        waits: list[float] = []
        progress: list[dict[str, Any]] = []
        cleaner = NonFollowerCleaner(
            backend,
            wait_fn=lambda _stop, seconds: waits.append(seconds) or False,
            on_progress=progress.append,
        )

        result = cleaner.scan()

        self.assertEqual([account.pk for account in result.following], ["2", "3", "4"])
        self.assertEqual([account.pk for account in result.followers], ["3", "9"])
        self.assertEqual([account.pk for account in result.non_followers], ["2", "4"])
        self.assertEqual(waits, [1.0])
        self.assertEqual(
            backend.fetch_calls,
            [
                ("following", "1", "", 100),
                ("following", "1", "next", 100),
                ("followers", "1", "", 100),
            ],
        )
        self.assertEqual(progress[-1]["phase"], "scan_complete")
        self.assertEqual(progress[-1]["non_followers"], 2)

    def test_scan_normalizes_pk_and_id_but_rejects_unidentified_rows(self):
        backend = FakeBackend(
            {
                ("following", ""): {
                    "users": [{"id": 2, "username": "two"}],
                    "next_max_id": None,
                },
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        result = NonFollowerCleaner(backend).scan()
        self.assertEqual(result.non_followers[0].pk, "2")

        backend.pages[("following", "")] = {"users": [{"pk": "2"}], "next_max_id": None}
        with self.assertRaises(IncompleteFriendshipListError):
            NonFollowerCleaner(backend).scan()

    def test_scan_fails_closed_when_instagram_limits_followers(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2")], "next_max_id": None},
                ("followers", ""): {
                    "users": [],
                    "next_max_id": None,
                    "should_limit_list_of_followers": True,
                },
            }
        )

        with self.assertRaises(IncompleteFriendshipListError):
            NonFollowerCleaner(backend).scan()
        self.assertEqual(backend.unfollow_calls, [])

    def test_scan_treats_string_limit_flags_explicitly(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2")], "next_max_id": None},
                ("followers", ""): {
                    "users": [],
                    "next_max_id": None,
                    "should_limit_list_of_followers": "false",
                },
            }
        )
        result = NonFollowerCleaner(backend).scan()
        self.assertEqual([account.pk for account in result.non_followers], ["2"])

        backend.pages[("followers", "")]["should_limit_list_of_followers"] = "true"
        with self.assertRaises(IncompleteFriendshipListError):
            NonFollowerCleaner(backend).scan()

    def test_scan_fails_closed_on_repeated_cursor(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2")], "next_max_id": "same"},
                ("following", "same"): {"users": [user("3")], "next_max_id": "same"},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )

        with self.assertRaises(IncompleteFriendshipListError):
            NonFollowerCleaner(backend).scan()

    def test_scan_fails_closed_on_empty_page_with_next_cursor(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [], "next_max_id": "next"},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )

        with self.assertRaises(IncompleteFriendshipListError):
            NonFollowerCleaner(backend).scan()

    def test_scan_fails_closed_when_explicit_more_signal_lacks_cursor(self):
        for key, value in (("has_more", True), ("more_available", "true")):
            with self.subTest(key=key):
                backend = FakeBackend(
                    {
                        ("following", ""): {
                            "users": [user("2")],
                            "next_max_id": None,
                        },
                        ("followers", ""): {
                            "users": [],
                            "next_max_id": None,
                            key: value,
                        },
                    }
                )

                with self.assertRaises(IncompleteFriendshipListError):
                    NonFollowerCleaner(backend).scan()
                self.assertEqual(backend.unfollow_calls, [])

    def test_unfollow_rechecks_each_target_immediately_before_action(self):
        backend = FakeBackend(
            {
                ("following", ""): {
                    "users": [user("2", "two"), user("3", "three")],
                    "next_max_id": None,
                },
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        backend.friendship_results["3"] = {
            "status": "ok",
            "followed_by": True,
            "following": True,
        }
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=0, max_delay_seconds=0),
        )

        summary = cleaner.unfollow_selected(["2", "3"], "1")

        self.assertEqual(backend.friendship_calls, ["2", "3"])
        self.assertEqual(backend.unfollow_calls, ["2"])
        self.assertEqual(summary.skipped_relationship_changed, 1)
        self.assertEqual([account.pk for account in summary.succeeded], ["2"])

    def test_unfollow_skips_target_that_is_no_longer_followed(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2", "two")], "next_max_id": None},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        backend.friendship_results["2"] = {
            "status": "ok",
            "followed_by": False,
            "following": False,
        }
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=0, max_delay_seconds=0),
        )

        summary = cleaner.unfollow_selected(["2"], "1")

        self.assertEqual(backend.friendship_calls, ["2"])
        self.assertEqual(backend.unfollow_calls, [])
        self.assertEqual(summary.skipped_relationship_changed, 1)

    def test_unfollow_rescans_and_skips_relationships_that_changed(self):
        backend = FakeBackend(
            {
                ("following", ""): {
                    "users": [user("2", "two"), user("3", "three"), user("4", "four")],
                    "next_max_id": None,
                },
                ("followers", ""): {"users": [user("3", "three")], "next_max_id": None},
            }
        )
        waits: list[float] = []
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=3, max_delay_seconds=5),
            rng=SequenceRandom([3, 5]),
            wait_fn=lambda _stop, seconds: waits.append(seconds) or False,
        )

        summary = cleaner.unfollow_selected(["2", "3", "4"], "1")

        self.assertEqual(summary.selected, 3)
        self.assertEqual(summary.eligible, 2)
        self.assertEqual(summary.skipped_relationship_changed, 1)
        self.assertEqual([account.pk for account in summary.succeeded], ["2", "4"])
        self.assertEqual(backend.unfollow_calls, ["2", "4"])
        self.assertEqual(waits, [3, 5])

    def test_unfollow_applies_per_run_limit_after_fresh_relationship_check(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2"), user("3")], "next_max_id": None},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(
                min_delay_seconds=0,
                max_delay_seconds=0,
                max_unfollows_per_run=1,
            ),
        )

        summary = cleaner.unfollow_selected(["2", "3"], "1")

        self.assertEqual(summary.eligible, 1)
        self.assertEqual(summary.deferred_by_limit, 1)
        self.assertEqual(backend.unfollow_calls, ["2"])

    def test_action_limit_counts_actual_unfollow_attempts_not_changed_relationships(self):
        backend = FakeBackend(
            {
                ("following", ""): {
                    "users": [user("2"), user("3"), user("4")],
                    "next_max_id": None,
                },
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        backend.friendship_results["2"] = {
            "status": "ok",
            "followed_by": True,
            "following": True,
        }
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(
                min_delay_seconds=0,
                max_delay_seconds=0,
                max_unfollows_per_run=1,
            ),
        )

        summary = cleaner.unfollow_selected(["2", "3", "4"], "1")

        self.assertEqual(backend.friendship_calls, ["2", "3"])
        self.assertEqual(backend.unfollow_calls, ["3"])
        self.assertEqual(summary.skipped_relationship_changed, 1)
        self.assertEqual(summary.deferred_by_limit, 1)

    def test_unfollow_stops_when_completion_is_not_explicitly_confirmed(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2"), user("3")], "next_max_id": None},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        backend.unfollow_results["2"] = {"status": "ok", "friendship_status": {"following": True}}
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=0, max_delay_seconds=0),
        )

        with self.assertRaises(UnfollowRunError) as cm:
            cleaner.unfollow_selected(["2", "3"], "1")

        self.assertEqual(backend.unfollow_calls, ["2"])
        self.assertEqual(cm.exception.summary.attempted, 1)
        self.assertEqual(len(cm.exception.summary.failed), 1)
        self.assertEqual(cm.exception.summary.succeeded, [])

    def test_unfollow_stops_on_restriction_and_preserves_completed_accounts(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2"), user("3")], "next_max_id": None},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        backend.unfollow_results["3"] = NonFollowerCleanerError("활동 제한")
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=0, max_delay_seconds=0),
        )

        with self.assertRaises(UnfollowRunError) as cm:
            cleaner.unfollow_selected(["2", "3"], "1")

        self.assertEqual([account.pk for account in cm.exception.summary.succeeded], ["2"])
        self.assertEqual(cm.exception.summary.attempted, 2)
        self.assertEqual(backend.unfollow_calls, ["2", "3"])

    def test_stop_event_interrupts_action_delay_before_write(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2")], "next_max_id": None},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=3, max_delay_seconds=3),
            wait_fn=lambda _stop, _seconds: True,
        )

        summary = cleaner.unfollow_selected(["2"], "1", threading.Event())

        self.assertTrue(summary.stopped)
        self.assertEqual(summary.attempted, 0)
        self.assertEqual(backend.unfollow_calls, [])

    def test_completed_unfollow_is_recorded_before_stop_takes_effect(self):
        backend = FakeBackend(
            {
                ("following", ""): {
                    "users": [user("2"), user("3")],
                    "next_max_id": None,
                },
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        stop_event = threading.Event()
        original_unfollow = backend.unfollow

        def unfollow_and_stop(user_id: str):
            payload = original_unfollow(user_id)
            stop_event.set()
            return payload

        backend.unfollow = unfollow_and_stop
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=0, max_delay_seconds=0),
        )

        summary = cleaner.unfollow_selected(["2", "3"], "1", stop_event)

        self.assertTrue(summary.stopped)
        self.assertEqual(summary.attempted, 1)
        self.assertEqual([account.pk for account in summary.succeeded], ["2"])
        self.assertEqual(backend.unfollow_calls, ["2"])

    def test_unfollow_rejects_a_different_logged_in_account_before_requests(self):
        backend = FakeBackend(
            {
                ("following", ""): {"users": [user("2")], "next_max_id": None},
                ("followers", ""): {"users": [], "next_max_id": None},
            }
        )
        backend.current_viewer_id = "999"
        cleaner = NonFollowerCleaner(
            backend,
            CleanerConfig(min_delay_seconds=0, max_delay_seconds=0),
        )

        with self.assertRaises(ViewerAccountChangedError):
            cleaner.unfollow_selected(["2"], "1")

        self.assertEqual(backend.fetch_calls, [])
        self.assertEqual(backend.friendship_calls, [])
        self.assertEqual(backend.unfollow_calls, [])

    def test_config_validation_rejects_unsafe_or_invalid_ranges(self):
        with self.assertRaises(ValueError):
            CleanerConfig(min_delay_seconds=-1).validate()
        with self.assertRaises(ValueError):
            CleanerConfig(min_delay_seconds=5, max_delay_seconds=3).validate()
        with self.assertRaises(ValueError):
            CleanerConfig(max_unfollows_per_run=-1).validate()
        with self.assertRaises(ValueError):
            CleanerConfig(page_size=0).validate()

    def test_relationship_status_requires_both_explicit_booleans(self):
        self.assertEqual(
            NonFollowerCleaner._require_relationship_status({"status": "ok", "followed_by": False, "following": True}),
            (False, True),
        )
        with self.assertRaises(FriendshipRequestError):
            NonFollowerCleaner._require_relationship_status({"status": "ok", "followed_by": False})

    def test_unfollow_confirmation_requires_following_false(self):
        with self.assertRaises(FriendshipRequestError):
            NonFollowerCleaner._require_unfollow_confirmation({"status": "ok"})
        NonFollowerCleaner._require_unfollow_confirmation({"status": "ok", "friendship_status": {"following": False}})


class FakePage:
    def __init__(self, responses: list[dict[str, Any]] | None = None):
        self.url = "https://www.instagram.com/"
        self.responses = deque(responses or [])
        self.evaluate_calls: list[tuple[str, dict[str, Any]]] = []

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def evaluate(self, script: str, arguments: dict[str, Any]):
        self.evaluate_calls.append((script, arguments))
        return self.responses.popleft()


class FakeContext:
    def __init__(self, cookies: list[dict[str, str]]):
        self._cookies = cookies

    def cookies(self, _urls):
        return self._cookies


class FakeSession:
    def __init__(self, responses: list[dict[str, Any]], cookies: list[dict[str, str]] | None = None):
        self.page = FakePage(responses)
        self.context = FakeContext(
            cookies
            or [
                {"name": "sessionid", "value": "session"},
                {"name": "ds_user_id", "value": "1"},
                {"name": "csrftoken", "value": "csrf"},
            ]
        )

    def _require_page(self, *, create_if_missing: bool = False):
        return self.page

    def _safe_goto(self, page: FakePage, url: str) -> None:
        page.url = url

    @staticmethod
    def raise_browser_error(exc: Exception) -> None:
        raise exc


class PlaywrightFriendshipBackendTestCase(unittest.TestCase):
    def test_fetch_page_uses_same_origin_endpoint_and_viewer_cookie(self):
        session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "text": '{"status":"ok","users":[],"next_max_id":null}',
                }
            ]
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()

        payload = backend.fetch_page("followers", backend.viewer_id(), "cursor", 100)

        self.assertEqual(payload["status"], "ok")
        arguments = session.page.evaluate_calls[0][1]
        self.assertEqual(arguments["method"], "GET")
        self.assertEqual(arguments["appId"], "936619743392459")
        self.assertEqual(arguments["asbdId"], "129477")
        self.assertEqual(arguments["timeoutMs"], 20_000)
        self.assertIn("AbortController", session.page.evaluate_calls[0][0])
        self.assertIn("signal: controller.signal", session.page.evaluate_calls[0][0])
        self.assertEqual(
            arguments["path"],
            "/api/v1/friendships/1/followers/?count=100&max_id=cursor",
        )

    def test_backend_preserves_user_ids_larger_than_javascript_safe_integer(self):
        session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "text": '{"status":"ok","users":[{"pk":9876543210123456789,"username":"large"}]}',
                }
            ]
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()

        payload = backend.fetch_page("following", "1", "", 100)

        self.assertEqual(payload["users"][0]["pk"], 9876543210123456789)
        account = FriendshipAccount.from_payload(payload["users"][0])
        self.assertEqual(account.pk, "9876543210123456789")

    def test_friendship_uses_current_relationship_endpoint(self):
        session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "text": '{"status":"ok","following":true,"followed_by":false}',
                }
            ]
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()

        payload = backend.friendship("42")

        self.assertFalse(payload["followed_by"])
        arguments = session.page.evaluate_calls[0][1]
        self.assertEqual(arguments["method"], "GET")
        self.assertEqual(
            arguments["path"],
            "/api/v1/friendships/show/42/?is_external_deeplink_profile_view=false",
        )

    def test_success_payload_with_spam_named_metadata_is_not_a_restriction(self):
        session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "text": ('{"status":"ok","users":[],"show_spam_follow_request_tab":true}'),
                }
            ]
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()

        payload = backend.fetch_page("followers", "1", "", 100)

        self.assertTrue(payload["show_spam_follow_request_tab"])

    def test_unfollow_posts_csrf_protected_request(self):
        session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "text": '{"status":"ok","friendship_status":{"following":false}}',
                }
            ]
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()

        backend.unfollow("42")

        arguments = session.page.evaluate_calls[0][1]
        self.assertEqual(arguments["method"], "POST")
        self.assertEqual(arguments["path"], "/api/v1/friendships/destroy/42/")
        self.assertEqual(arguments["csrfToken"], "csrf")
        self.assertIn("user_id=42", arguments["body"])

    def test_completed_post_response_is_returned_before_stop_is_honored(self):
        stop_event = threading.Event()
        session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "text": '{"status":"ok","friendship_status":{"following":false}}',
                }
            ]
        )
        original_evaluate = session.page.evaluate

        def evaluate_and_stop(script: str, arguments: dict[str, Any]):
            result = original_evaluate(script, arguments)
            stop_event.set()
            return result

        session.page.evaluate = evaluate_and_stop
        backend = PlaywrightFriendshipBackend(session, stop_event=stop_event)
        backend.prepare()

        payload = backend.unfollow("42")

        self.assertFalse(payload["friendship_status"]["following"])
        self.assertTrue(stop_event.is_set())

    def test_backend_detects_login_and_checkpoint_redirects(self):
        login_session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "url": "https://www.instagram.com/accounts/login/",
                    "text": "<html>login</html>",
                }
            ]
        )
        login_backend = PlaywrightFriendshipBackend(login_session)
        login_backend.prepare()
        with self.assertRaises(Exception):
            login_backend.fetch_page("following", "1", "", 100)

        checkpoint_session = FakeSession(
            [
                {
                    "ok": True,
                    "statusCode": 200,
                    "url": "https://www.instagram.com/challenge/123/",
                    "text": "<html>checkpoint</html>",
                }
            ]
        )
        checkpoint_backend = PlaywrightFriendshipBackend(checkpoint_session)
        checkpoint_backend.prepare()
        with self.assertRaises(NonFollowerCleanerError):
            checkpoint_backend.fetch_page("following", "1", "", 100)

    def test_backend_promotes_activity_limit_response(self):
        session = FakeSession(
            [
                {
                    "ok": False,
                    "statusCode": 429,
                    "text": '{"status":"fail","message":"feedback_required"}',
                }
            ]
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()

        with self.assertRaises(NonFollowerCleanerError):
            backend.fetch_page("following", "1", "", 100)

    def test_backend_bounds_hung_requests_and_honors_stop_before_fetch(self):
        timeout_session = FakeSession([{"networkError": "AbortError", "timedOut": True}])
        timeout_backend = PlaywrightFriendshipBackend(
            timeout_session,
            request_timeout_seconds=0.25,
        )
        timeout_backend.prepare()

        with self.assertRaises(FriendshipRequestError) as cm:
            timeout_backend.fetch_page("following", "1", "", 100)

        self.assertIn("0.25초", str(cm.exception))
        self.assertEqual(timeout_session.page.evaluate_calls[0][1]["timeoutMs"], 250)

        stop_event = threading.Event()
        stop_event.set()
        stopped_session = FakeSession([])
        stopped_backend = PlaywrightFriendshipBackend(
            stopped_session,
            stop_event=stop_event,
        )
        stopped_backend.prepare()

        with self.assertRaises(OperationStopped):
            stopped_backend.fetch_page("following", "1", "", 100)

        self.assertEqual(stopped_session.page.evaluate_calls, [])

    def test_viewer_id_is_refreshed_from_current_browser_cookies(self):
        session = FakeSession([])
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()
        self.assertEqual(backend.viewer_id(), "1")

        session.context._cookies = [
            {"name": "sessionid", "value": "new-session"},
            {"name": "ds_user_id", "value": "999"},
            {"name": "csrftoken", "value": "csrf"},
        ]

        self.assertEqual(backend.viewer_id(), "999")

    def test_backend_requires_login_and_csrf_cookies(self):
        session = FakeSession([], cookies=[{"name": "sessionid", "value": "session"}])
        backend = PlaywrightFriendshipBackend(session)
        with self.assertRaises(Exception):
            backend.prepare()

        session = FakeSession(
            [],
            cookies=[
                {"name": "sessionid", "value": "session"},
                {"name": "ds_user_id", "value": "1"},
            ],
        )
        backend = PlaywrightFriendshipBackend(session)
        backend.prepare()
        with self.assertRaises(Exception):
            backend.unfollow("42")


if __name__ == "__main__":
    unittest.main()
