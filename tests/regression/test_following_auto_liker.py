from __future__ import annotations

import random
import shutil
import threading
import unittest
from dataclasses import dataclass

from apps.following_auto_liker.browser import (
    PlaywrightFeedPost,
    PlaywrightFollowingFeed,
    is_instagram_hostname,
)
from apps.following_auto_liker.engine import (
    FollowingAutoLiker,
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
    click_exception: Exception | None = None

    @property
    def like_state(self):
        return self.state

    def click_like(self) -> bool:
        self.clicks += 1
        if callable(self.after_click):
            self.after_click()
        if self.click_exception is not None:
            raise self.click_exception
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

    def scanner(self, config=None, *, wait_fn=None, on_like=None):
        return FollowingFeedScanner(
            config or self.config(),
            rng=random.Random(7),
            wait_fn=wait_fn or (lambda _event, _seconds: False),
            on_like=on_like,
        )

    def test_default_delay_is_three_to_five_seconds(self):
        config = AppConfig().validate()
        self.assertEqual(config.min_delay_seconds, 3)
        self.assertEqual(config.max_delay_seconds, 5)

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

    def test_final_allowed_scroll_content_is_processed(self):
        first = FakePost("/p/one/")
        loaded_by_final_scroll = FakePost("/p/two/")
        feed = FakeFeed([[first], [loaded_by_final_scroll]])

        summary = self.scanner(self.config(max_scroll_rounds=1)).scan_once(feed)

        self.assertEqual(summary.scroll_rounds, 1)
        self.assertEqual(summary.liked, 2)
        self.assertEqual(loaded_by_final_scroll.clicks, 1)

    def test_caught_up_marker_ends_scan_without_extra_scroll(self):
        first = FakePost("/p/one/")
        second = FakePost("/p/two/")
        feed = FakeFeed([[first], [second]], caught_up_at=0)

        summary = self.scanner().scan_once(feed)

        self.assertTrue(summary.caught_up)
        self.assertEqual(summary.liked, 1)
        self.assertEqual(second.clicks, 0)
        self.assertEqual(feed.index, 0)

    def test_restriction_after_like_stops_immediately_and_preserves_count(self):
        feed = FakeFeed([[]])
        post = FakePost("/p/one/")
        post.after_click = lambda: setattr(feed, "restriction", "Try again later")
        feed.pages = [[post, FakePost("/p/two/")]]
        recorded_likes = []

        with self.assertRaises(InstagramRestrictionError) as raised:
            self.scanner(on_like=lambda: recorded_likes.append(1)).scan_once(feed)

        self.assertEqual(post.clicks, 1)
        self.assertEqual(feed.pages[0][1].clicks, 0)
        self.assertEqual(len(recorded_likes), 1)
        self.assertIsNotNone(raised.exception.summary)
        self.assertEqual(raised.exception.summary.liked, 1)

    def test_restriction_after_click_exception_stops_before_next_post(self):
        feed = FakeFeed([[]])
        first = FakePost("/p/one/", click_exception=TimeoutError("overlay intercepted click"))
        first.after_click = lambda: setattr(feed, "restriction", "Action blocked")
        second = FakePost("/p/two/")
        feed.pages = [[first, second]]

        with self.assertRaises(InstagramRestrictionError):
            self.scanner().scan_once(feed)

        self.assertEqual(first.clicks, 1)
        self.assertEqual(second.clicks, 0)

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

    def test_summary_reports_unknown_like_state(self):
        summary = self.scanner().scan_once(FakeFeed([[FakePost("/p/one/", state="unknown")]]))
        message = FollowingAutoLiker._summary_message(summary)
        self.assertIn("상태 미확인 1개", message)

    def test_config_validation_url_normalization_and_hostname_check(self):
        self.assertEqual(
            normalize_post_key("https://www.instagram.com/reel/ABC123/?igsh=example"),
            "/reel/ABC123/",
        )
        self.assertEqual(normalize_post_key("/p/POST42/"), "/p/POST42/")
        self.assertTrue(is_instagram_hostname("www.instagram.com"))
        self.assertTrue(is_instagram_hostname("instagram.com"))
        self.assertFalse(is_instagram_hostname("evilinstagram.com"))
        self.assertFalse(is_instagram_hostname("instagram.com.evil.example"))
        with self.assertRaisesRegex(ValueError, "최소 대기"):
            self.config(min_delay_seconds=30, max_delay_seconds=10)
        with self.assertRaisesRegex(ValueError, "0~10000"):
            self.config(max_likes_per_cycle=-1)


class _DomSession:
    def __init__(self, page):
        self.page = page

    def _require_page(self):
        return self.page

    @staticmethod
    def raise_browser_error(exc):
        raise exc


class BrowserDomRegressionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("playwright is not installed") from exc

        executable = (
            shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        if not executable:
            raise unittest.SkipTest("Chrome/Chromium is not installed")

        cls._playwright = sync_playwright().start()
        cls._browser = cls._playwright.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_browser"):
            cls._browser.close()
        if hasattr(cls, "_playwright"):
            cls._playwright.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={"width": 900, "height": 900})
        self.session = _DomSession(self.page)

    def tearDown(self):
        self.page.close()

    def _set_article(self, *, header_marker="", caption="", main_state="Like", comment_state="Unlike"):
        marker = f"<span>{header_marker}</span>" if header_marker else ""
        html = f"""
        <style>
          article {{ width: 620px; min-height: 600px; }}
          .media {{ height: 420px; background: #ddd; }}
          .actions button {{ width: 44px; height: 44px; }}
          .comment-like {{ width: 22px; height: 22px; }}
        </style>
        <article>
          <header><a href="/friend/">friend</a>{marker}</header>
          <a href="/p/ABC123/"><div class="media"></div></a>
          <section class="actions">
            <button aria-label="{main_state}"
              onclick="this.setAttribute('aria-label', this.getAttribute('aria-label') === 'Like' ? 'Unlike' : 'Like')">
              <svg aria-label="{main_state}"></svg>
            </button>
            <button aria-label="Comment"></button>
            <button aria-label="Share Post"></button>
          </section>
          <div class="caption">{caption}</div>
          <div class="comment"><button class="comment-like" aria-label="{comment_state}"></button></div>
        </article>
        """
        self.page.set_content(html)
        return PlaywrightFeedPost(self.session, self.page.locator("article"))

    def test_caption_labels_do_not_trigger_exclusion_but_header_marker_does(self):
        caption_post = self._set_article(caption="Sponsored")
        self.assertIsNone(caption_post.exclusion_reason)

        self.page.set_content("")
        sponsored_post = self._set_article(header_marker="Sponsored")
        self.assertEqual(sponsored_post.exclusion_reason, "sponsored")

    def test_comment_heart_does_not_override_main_post_like_state(self):
        post = self._set_article(main_state="Like", comment_state="Unlike")
        self.assertEqual(post.like_state, "unliked")
        self.assertTrue(post.click_like())
        self.assertEqual(post.like_state, "liked")

    def test_caption_status_phrases_are_ignored_but_status_ui_is_detected(self):
        self.page.set_content(
            """
            <article><a href="/p/ABC123/">post</a><p>Try again later. You're all caught up.</p></article>
            """
        )
        feed = PlaywrightFollowingFeed(self.session)
        self.assertIsNone(feed.restriction_message())
        self.assertFalse(feed.is_caught_up())

        self.page.set_content('<div role="dialog">アクションがブロックされました</div>')
        self.assertIsNotNone(feed.restriction_message())

        self.page.set_content("<main><div>You're all caught up</div></main>")
        self.assertTrue(feed.is_caught_up())


if __name__ == "__main__":
    unittest.main()
