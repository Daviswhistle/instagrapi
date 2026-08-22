from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.following_auto_liker.config import AppConfig, Storage, StoragePaths
from apps.following_auto_liker.model import InstagramRestrictionError, normalize_post_key
from apps.following_auto_liker.scanner import FollowingFeedScanner


class FakePost:
    def __init__(
        self,
        key: str,
        username: str = "friend",
        state: str = "unliked",
        reason: str | None = None,
        succeeds: bool = True,
        after_click=None,
    ):
        self.key = key
        self.username = username
        self.like_state = state
        self.exclusion_reason = reason
        self.succeeds = succeeds
        self.after_click = after_click
        self.clicks = 0

    def click_like(self) -> bool:
        self.clicks += 1
        if self.after_click:
            self.after_click()
        if self.succeeds:
            self.like_state = "liked"
            return True
        return False


class FakeFeed:
    def __init__(self, pages, *, caught_up_at=None):
        self.pages = pages
        self.index = 0
        self.opened = 0
        self.scrolls = 0
        self.restriction = None
        self.caught_up_at = caught_up_at

    def open_following(self):
        self.opened += 1
        self.index = 0

    def posts(self):
        return list(self.pages[self.index])

    def scroll_for_more(self):
        self.scrolls += 1
        if self.index + 1 >= len(self.pages):
            return False
        self.index += 1
        return True

    def restriction_message(self):
        return self.restriction

    def is_caught_up(self):
        return self.caught_up_at is not None and self.index >= self.caught_up_at


class FollowingAutoLikerRegressionTest(unittest.TestCase):
    def config(self, **updates):
        values = {
            "check_interval_minutes": 30,
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "max_likes_per_cycle": 0,
            "max_scroll_rounds": 20,
            "unchanged_scroll_rounds": 2,
        }
        values.update(updates)
        return AppConfig(**values).validate()

    def scanner(self, config=None, wait_fn=None):
        return FollowingFeedScanner(config or self.config(), wait_fn=wait_fn or (lambda _event, _seconds: False))

    def test_default_delay_is_three_to_five_seconds(self):
        config = AppConfig().validate()
        self.assertEqual(config.min_delay_seconds, 3)
        self.assertEqual(config.max_delay_seconds, 5)

    def test_likes_all_eligible_and_skips_filtered_posts(self):
        one = FakePost("/p/one/")
        two = FakePost("/reel/two/")
        feed = FakeFeed(
            [[
                one,
                FakePost("/p/liked/", state="liked"),
                FakePost("/p/ad/", reason="sponsored"),
                FakePost("/p/recommended/", reason="recommended"),
                FakePost("/p/unknown/", state="unknown"),
            ], [two]],
            caught_up_at=1,
        )
        summary = self.scanner().scan_once(feed)
        self.assertEqual(summary.liked, 2)
        self.assertEqual(summary.already_liked, 1)
        self.assertEqual(summary.sponsored, 1)
        self.assertEqual(summary.recommended, 1)
        self.assertEqual(summary.unknown, 1)
        self.assertTrue(summary.caught_up)
        self.assertEqual(one.clicks + two.clicks, 2)

    def test_deduplicates_posts_across_scroll_rounds(self):
        first = FakePost("/p/one/")
        duplicate = FakePost("https://www.instagram.com/p/one/?utm_source=x")
        second = FakePost("/p/two/")
        summary = self.scanner().scan_once(FakeFeed([[first], [duplicate, second]], caught_up_at=1))
        self.assertEqual(summary.discovered, 2)
        self.assertEqual(summary.liked, 2)
        self.assertEqual(duplicate.clicks, 0)

    def test_zero_max_likes_is_unlimited(self):
        posts = [FakePost(f"/p/{index}/") for index in range(25)]
        summary = self.scanner(self.config(max_likes_per_cycle=0)).scan_once(FakeFeed([posts], caught_up_at=0))
        self.assertEqual(summary.liked, 25)
        self.assertFalse(summary.max_likes_reached)

    def test_optional_cycle_limit_stops_before_next_post(self):
        posts = [FakePost(f"/p/{index}/") for index in range(5)]
        summary = self.scanner(self.config(max_likes_per_cycle=2)).scan_once(FakeFeed([posts]))
        self.assertEqual(summary.liked, 2)
        self.assertTrue(summary.max_likes_reached)
        self.assertEqual(sum(post.clicks for post in posts), 2)

    def test_stop_during_delay_does_not_click(self):
        stop = threading.Event()
        post = FakePost("/p/stop/")

        def wait(_event, _seconds):
            stop.set()
            return True

        summary = self.scanner(self.config(min_delay_seconds=3, max_delay_seconds=5), wait_fn=wait).scan_once(
            FakeFeed([[post]]), stop
        )
        self.assertTrue(summary.stopped)
        self.assertEqual(post.clicks, 0)

    def test_records_successful_like_before_restriction_abort(self):
        feed = FakeFeed([[]])
        post = FakePost("/p/one/", after_click=lambda: setattr(feed, "restriction", "Try again later"))
        feed.pages = [[post]]
        scanner = self.scanner()
        with self.assertRaises(InstagramRestrictionError):
            scanner.scan_once(feed)
        self.assertEqual(post.clicks, 1)
        self.assertEqual(post.like_state, "liked")

    def test_does_not_scroll_after_last_allowed_round(self):
        feed = FakeFeed([[FakePost("/p/one/")], [FakePost("/p/two/")]])
        summary = self.scanner(self.config(max_scroll_rounds=1)).scan_once(feed)
        self.assertEqual(summary.liked, 1)
        self.assertEqual(feed.scrolls, 0)

    def test_config_validation_and_url_normalization(self):
        self.assertEqual(normalize_post_key("https://www.instagram.com/reel/ABC/?x=1"), "/reel/ABC/")
        self.assertEqual(normalize_post_key("/p/POST42/"), "/p/POST42/")
        with self.assertRaises(ValueError):
            self.config(min_delay_seconds=10, max_delay_seconds=5)
        with self.assertRaises(ValueError):
            self.config(max_likes_per_cycle=-1)

    def test_storage_round_trip_and_profile_reset(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            storage = Storage(
                StoragePaths(
                    root=root,
                    config=root / "config.json",
                    chrome_profile=root / "chrome-profile",
                    log=root / "app.log",
                )
            )
            config = self.config(min_delay_seconds=3, max_delay_seconds=5)
            storage.save_config(config)
            loaded = storage.load_config()
            self.assertEqual(loaded.min_delay_seconds, 3)
            self.assertEqual(json.loads(storage.paths.config.read_text())["max_delay_seconds"], 5)
            (storage.paths.chrome_profile / "Session").write_text("secret")
            self.assertTrue(storage.chrome_profile_has_data())
            storage.reset_chrome_profile()
            self.assertFalse(storage.chrome_profile_has_data())


if __name__ == "__main__":
    unittest.main()
