from __future__ import annotations

import random
import threading
import unittest
from dataclasses import dataclass

from apps.following_auto_liker.engine import (
    FollowingFeedScanner,
    InstagramRestrictionError,
    normalize_post_key,
)
from apps.following_auto_liker.storage import AppConfig


@dataclass
class FakePost:
    key: str
    username: str = "friend"
    state: str = "unliked"
    exclusion_reason: str | None = None
    succeeds: bool = True
    clicks: int = 0
    after_click: object = None

    @property
    def like_state(self):
        return self.state

    def click_like(self) -> bool:
        self.clicks += 1
        if callable(self.after_click):
            self.after_click()
        if self.succeeds:
            self.state = "liked"
            return True
        return False


class FakeFeed:
    def __init__(self, pages, *, caught_up_at=None):
        self.pages = pages
        self.index = 0
        self.open_count = 0
        self.restriction = None
        self.caught_up_at = caught_up_at

    def open_following(self):
        self.open_count += 1
        self.index = 0

    def posts(self):
        return list(self.pages[self.index])

    def scroll_for_more(self):
        if self.index + 1 >= len(self.pages):
            return False
        self.index += 1
        return True

    def restriction_message(self):
        return self.restriction

    def is_caught_up(self):
        return self.caught_up_at is not None and self.index >= self.caught_up_at


class FollowingAutoLikerRegressionTestCase(unittest.TestCase):
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

    def scanner(self, config=None, *, wait_fn=None):
        return FollowingFeedScanner(
            config or self.config(),
            rng=random.Random(7),
            wait_fn=wait_fn or (lambda _event, _seconds: False),
        )

    def test_likes_every_unliked_organic_post_across_pages(self):
        first = FakePost("/p/one/")
        already = FakePost("/p/two/", state="liked")
        sponsored = FakePost("/p/ad/", exclusion_reason="sponsored")
        recommended = FakePost("/p/recommended/", exclusion_reason="recommended")
        second = FakePost("/reel/three/")
        duplicate = FakePost("https://www.instagram.com/p/one/?utm_source=test")
        feed = FakeFeed([[first, already, sponsored, recommended], [second, duplicate]])

        summary = self.scanner().scan_once(feed)

        self.assertEqual(summary.liked, 2)
        self.assertEqual(summary.already_liked, 1)
        self.assertEqual(summary.sponsored, 1)
        self.assertEqual(summary.recommended, 1)
        self.assertEqual(summary.discovered, 5)
        self.assertEqual(first.clicks, 1)
        self.assertEqual(second.clicks, 1)
        self.assertEqual(duplicate.clicks, 0)
        self.assertEqual(feed.open_count, 1)

    def test_zero_max_likes_means_unlimited(self):
        posts = [FakePost(f"/p/{index}/") for index in range(25)]
        summary = self.scanner(self.config(max_likes_per_cycle=0)).scan_once(FakeFeed([posts]))
        self.assertEqual(summary.liked, 25)
        self.assertFalse(summary.max_likes_reached)

    def test_optional_cycle_limit_stops_before_next_post(self):
        posts = [FakePost(f"/p/{index}/") for index in range(5)]
        summary = self.scanner(self.config(max_likes_per_cycle=2)).scan_once(FakeFeed([posts]))
        self.assertEqual(summary.liked, 2)
        self.assertTrue(summary.max_likes_reached)
        self.assertEqual(sum(post.clicks for post in posts), 2)

    def test_caught_up_marker_ends_scan_without_extra_scroll(self):
        first = FakePost("/p/one/")
        second = FakePost("/p/two/")
        feed = FakeFeed([[first], [second]], caught_up_at=0)

        summary = self.scanner().scan_once(feed)

        self.assertTrue(summary.caught_up)
        self.assertEqual(summary.liked, 1)
        self.assertEqual(second.clicks, 0)
        self.assertEqual(feed.index, 0)

    def test_restriction_after_like_stops_immediately(self):
        feed = FakeFeed([[]])
        post = FakePost("/p/one/")
        post.after_click = lambda: setattr(feed, "restriction", "Try again later")
        feed.pages = [[post, FakePost("/p/two/")]]

        with self.assertRaises(InstagramRestrictionError):
            self.scanner().scan_once(feed)
        self.assertEqual(post.clicks, 1)
        self.assertEqual(feed.pages[0][1].clicks, 0)

    def test_stop_during_delay_does_not_click(self):
        post = FakePost("/p/one/")
        stop_event = threading.Event()
        scanner = self.scanner(
            self.config(min_delay_seconds=10, max_delay_seconds=10),
            wait_fn=lambda _event, _seconds: True,
        )

        summary = scanner.scan_once(FakeFeed([[post]]), stop_event)

        self.assertTrue(summary.stopped)
        self.assertEqual(post.clicks, 0)

    def test_default_delay_is_three_to_five_seconds(self):
        config = AppConfig().validate()
        self.assertEqual(config.min_delay_seconds, 3)
        self.assertEqual(config.max_delay_seconds, 5)

    def test_config_validation_and_url_normalization(self):
        self.assertEqual(
            normalize_post_key("https://www.instagram.com/reel/ABC123/?igsh=example"),
            "/reel/ABC123/",
        )
        self.assertEqual(normalize_post_key("/p/POST42/"), "/p/POST42/")
        with self.assertRaisesRegex(ValueError, "최소 대기"):
            self.config(min_delay_seconds=30, max_delay_seconds=10)
        with self.assertRaisesRegex(ValueError, "0~10000"):
            self.config(max_likes_per_cycle=-1)


if __name__ == "__main__":
    unittest.main()
