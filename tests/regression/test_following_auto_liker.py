from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from apps.following_auto_liker.engine import FollowingAutoLiker
from apps.following_auto_liker.storage import AppConfig, AppPaths, Storage


class FixedRandom:
    def __init__(self, probability_value: float = 0.0):
        self.probability_value = probability_value

    def random(self) -> float:
        return self.probability_value

    @staticmethod
    def randint(start: int, _end: int) -> int:
        return start


class FakeClient:
    def __init__(self, feed_items: list[dict], following_ids: list[str]):
        self.user_id = "999"
        self.feed_items = feed_items
        self.following_ids = following_ids
        self.liked: list[str] = []
        self.fail_ids: set[str] = set()

    def get_timeline_feed(self, reason: str = "pull_to_refresh", **_kwargs):
        return {"feed_items": self.feed_items, "more_available": False, "reason": reason}

    def iter_user_following_v1(self, _user_id: str, amount: int = 0, page_size: int = 200):
        del amount, page_size
        for user_id in self.following_ids:
            yield SimpleNamespace(pk=user_id, username=f"user{user_id}")

    def media_like(self, media_id: str) -> bool:
        if media_id in self.fail_ids:
            raise RuntimeError("temporary failure")
        self.liked.append(media_id)
        return True


def feed_item(
    pk: str,
    user_id: str,
    username: str,
    taken_at: datetime,
    *,
    has_liked: bool = False,
    is_ad: bool = False,
) -> dict:
    media = {
        "pk": pk,
        "id": f"{pk}_{user_id}",
        "taken_at": int(taken_at.timestamp()),
        "has_liked": has_liked,
        "user": {"pk": user_id, "username": username},
    }
    if is_ad:
        media["is_ad"] = True
    return {"media_or_ad": media}


class PagedFakeClient(FakeClient):
    def __init__(self, pages: dict[str, dict], following_ids: list[str]):
        super().__init__([], following_ids)
        self.pages = pages
        self.timeline_requests: list[tuple[str, str]] = []

    def get_timeline_feed(self, reason: str = "pull_to_refresh", **kwargs):
        max_id = str(kwargs.get("max_id") or "")
        self.timeline_requests.append((reason, max_id))
        return self.pages[max_id]


class FollowingAutoLikerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage = Storage(AppPaths.from_root(Path(self.temporary_directory.name)))
        self.now = datetime(2026, 8, 21, 14, 0, tzinfo=timezone(timedelta(hours=9)))

    def config(self, **overrides) -> AppConfig:
        values = {
            "username": "owner",
            "daily_limit": 30,
            "like_probability": 100,
            "scan_interval_minutes": 15,
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "lookback_hours": 24,
            "following_refresh_hours": 24,
            "excluded_usernames": [],
            "max_failures_per_media": 3,
        }
        values.update(overrides)
        return AppConfig(**values)

    def engine(self, client: FakeClient, config: AppConfig, rng: FixedRandom | None = None) -> FollowingAutoLiker:
        return FollowingAutoLiker(
            "owner",
            config,
            self.storage,
            client=client,
            rng=rng or FixedRandom(),
            now_fn=lambda: self.now,
        )

    def mark_following_cache_fresh(self, engine: FollowingAutoLiker, following_ids: list[str]) -> None:
        engine.state.initialized = True
        engine.state.following_ids = following_ids
        engine.state.following_refreshed_at = self.now.astimezone(timezone.utc).isoformat()
        engine.save_state()

    def test_timeline_fetch_uses_next_cursor_and_respects_page_limit(self) -> None:
        client = PagedFakeClient(
            {
                "": {
                    "feed_items": [feed_item("601", "1", "first", self.now - timedelta(minutes=2))],
                    "more_available": True,
                    "next_max_id": "cursor-1",
                },
                "cursor-1": {
                    "feed_items": [feed_item("602", "2", "second", self.now - timedelta(minutes=1))],
                    "more_available": True,
                    "next_max_id": "cursor-2",
                },
            },
            ["1", "2"],
        )
        engine = self.engine(client, self.config())

        posts = engine.fetch_timeline_posts(max_pages=2)

        self.assertEqual([post.media_id for post in posts], ["601_1", "602_2"])
        self.assertEqual(
            client.timeline_requests,
            [("pull_to_refresh", ""), ("pagination", "cursor-1")],
        )

    def test_first_run_only_records_a_baseline(self) -> None:
        items = [
            feed_item("101", "1", "friend", self.now - timedelta(minutes=10)),
            feed_item("102", "2", "another", self.now - timedelta(minutes=20)),
        ]
        client = FakeClient(items, ["1", "2"])
        engine = self.engine(client, self.config())

        baseline_count = engine.initialize_baseline()

        self.assertEqual(baseline_count, 2)
        self.assertTrue(engine.state.initialized)
        self.assertEqual(set(engine.state.processed_media_ids), {"101_1", "102_2"})
        self.assertEqual(client.liked, [])

    def test_scan_likes_only_recent_organic_posts_from_following_accounts(self) -> None:
        items = [
            feed_item("201", "1", "friend", self.now - timedelta(minutes=30)),
            feed_item("202", "8", "recommended", self.now - timedelta(minutes=10)),
            feed_item("203", "1", "friend", self.now - timedelta(minutes=5), is_ad=True),
            feed_item("204", "1", "friend", self.now - timedelta(minutes=6), has_liked=True),
            feed_item("205", "1", "friend", self.now - timedelta(hours=30)),
            feed_item("206", "2", "excluded", self.now - timedelta(minutes=7)),
        ]
        client = FakeClient(items, ["1", "2"])
        engine = self.engine(client, self.config(excluded_usernames=["@excluded"]))
        self.mark_following_cache_fresh(engine, ["1", "2"])

        summary = engine.scan_once()

        self.assertEqual(client.liked, ["201_1"])
        self.assertEqual(summary.liked, 1)
        self.assertEqual(summary.candidates, 1)
        self.assertTrue({"202_8", "203_1", "204_1", "205_1", "206_2"}.issubset(engine.state.processed_media_ids))

    def test_daily_limit_leaves_remaining_candidate_for_later(self) -> None:
        items = [
            feed_item("301", "1", "first", self.now - timedelta(minutes=20)),
            feed_item("302", "2", "second", self.now - timedelta(minutes=10)),
        ]
        client = FakeClient(items, ["1", "2"])
        engine = self.engine(client, self.config(daily_limit=1))
        self.mark_following_cache_fresh(engine, ["1", "2"])

        summary = engine.scan_once()

        self.assertEqual(client.liked, ["301_1"])
        self.assertTrue(summary.daily_limit_reached)
        self.assertIn("301_1", engine.state.processed_media_ids)
        self.assertNotIn("302_2", engine.state.processed_media_ids)

    def test_probability_skip_is_processed_once(self) -> None:
        client = FakeClient(
            [feed_item("401", "1", "friend", self.now - timedelta(minutes=5))],
            ["1"],
        )
        engine = self.engine(client, self.config(like_probability=90), FixedRandom(0.95))
        self.mark_following_cache_fresh(engine, ["1"])

        summary = engine.scan_once()

        self.assertEqual(summary.skipped_probability, 1)
        self.assertEqual(client.liked, [])
        self.assertIn("401_1", engine.state.processed_media_ids)

    def test_repeated_failures_stop_after_configured_attempts(self) -> None:
        client = FakeClient(
            [feed_item("501", "1", "friend", self.now - timedelta(minutes=5))],
            ["1"],
        )
        client.fail_ids.add("501_1")
        engine = self.engine(client, self.config(max_failures_per_media=2))
        self.mark_following_cache_fresh(engine, ["1"])

        first = engine.scan_once()
        second = engine.scan_once()

        self.assertEqual(first.failed, 1)
        self.assertEqual(second.failed, 1)
        self.assertIn("501_1", engine.state.processed_media_ids)
        self.assertNotIn("501_1", engine.state.failed_attempts)


if __name__ == "__main__":
    unittest.main()
