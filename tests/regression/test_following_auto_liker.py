from __future__ import annotations

import random
import shutil
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.following_auto_liker.browser import (
    ChromeBrowserSession,
    PlaywrightFeedPost,
    PlaywrightFollowingFeed,
    is_instagram_hostname,
)
from apps.following_auto_liker.engine import (
    FollowingAutoLiker,
    FollowingFeedScanner,
    FollowingFeedUnavailableError,
    InstagramRestrictionError,
    normalize_post_key,
)
from apps.following_auto_liker.storage import (
    AppAlreadyRunningError,
    AppConfig,
    Storage,
    StoragePaths,
)


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
    def __init__(self, pages, *, caught_up_at=None, caught_up_boundaries=None):
        self.pages = pages
        self.index = 0
        self.open_count = 0
        self.restriction = None
        self.caught_up_at = caught_up_at
        self.caught_up_boundaries = caught_up_boundaries or {}

    def open_following(self):
        self.open_count += 1
        self.index = 0

    def posts(self):
        return list(self.pages[self.index])

    def posts_before_caught_up(self):
        posts = self.posts()
        boundary = self.caught_up_boundaries.get(self.index)
        if boundary is None:
            return posts, self.is_caught_up()
        return posts[: max(0, int(boundary))], True

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

    def test_caught_up_boundary_skips_older_posts_loaded_in_same_page(self):
        recent = FakePost("/p/recent/")
        older = FakePost("/p/older/")
        feed = FakeFeed(
            [[recent, older]],
            caught_up_boundaries={0: 1},
        )

        summary = self.scanner().scan_once(feed)

        self.assertTrue(summary.caught_up)
        self.assertEqual(summary.discovered, 1)
        self.assertEqual(summary.liked, 1)
        self.assertEqual(recent.clicks, 1)
        self.assertEqual(older.clicks, 0)

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

    def test_instance_lock_blocks_second_instance_and_guards_profile_deletion(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = StoragePaths(
                root=root,
                config=root / "config.json",
                chrome_profile=root / "chrome-profile",
                log=root / "app.log",
                instance_lock=root / "app.lock",
            )
            first = Storage(paths)
            second = Storage(paths)

            with self.assertRaises(AppAlreadyRunningError):
                second.clear_browser_profile()

            lock = first.acquire_instance_lock()
            try:
                with self.assertRaises(AppAlreadyRunningError):
                    second.acquire_instance_lock()

                cookie = first.paths.chrome_profile / "Cookies"
                cookie.write_text("session", encoding="utf-8")
                first.clear_browser_profile()
                self.assertTrue(first.paths.chrome_profile.is_dir())
                self.assertFalse(cookie.exists())
            finally:
                lock.release()

            replacement_lock = second.acquire_instance_lock()
            replacement_lock.release()

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
        self.assertTrue(PlaywrightFollowingFeed._is_following_url("https://www.instagram.com/?variant=following"))
        self.assertFalse(
            PlaywrightFollowingFeed._is_following_url("https://www.instagram.com/checkpoint/?variant=following")
        )
        self.assertFalse(
            PlaywrightFollowingFeed._is_following_url("https://www.instagram.com/accounts/login/?variant=following")
        )
        with self.assertRaisesRegex(ValueError, "최소 대기"):
            self.config(min_delay_seconds=30, max_delay_seconds=10)
        with self.assertRaisesRegex(ValueError, "0~10000"):
            self.config(max_likes_per_cycle=-1)


class _InterruptedNavigationPage:
    def __init__(self):
        self.load_states = []

    def goto(self, _url, *, wait_until):
        self.wait_until = wait_until
        raise RuntimeError(
            'Navigation to "https://www.instagram.com/?variant=following" '
            'is interrupted by another navigation to "https://www.instagram.com/accounts/onetap/"'
        )

    def wait_for_load_state(self, state, *, timeout):
        self.load_states.append((state, timeout))


class BrowserNavigationRegressionTestCase(unittest.TestCase):
    def test_interrupted_instagram_redirect_is_allowed_to_settle(self):
        page = _InterruptedNavigationPage()
        session = ChromeBrowserSession(Path("/tmp/following-auto-liker-test-profile"))

        session._safe_goto(page, "https://www.instagram.com/?variant=following")

        self.assertEqual(page.wait_until, "domcontentloaded")
        self.assertEqual(page.load_states, [("domcontentloaded", 20_000)])


class _PageWithUrl:
    def __init__(self, page, url: str):
        self._page = page
        self.url = url

    def __getattr__(self, name):
        return getattr(self._page, name)


class _DomSession:
    def __init__(self, page):
        self.page = page

    def _require_page(self):
        return self.page

    @staticmethod
    def _page_looks_logged_out(page):
        if any(path in page.url for path in ("/accounts/login", "/accounts/onetap")):
            return True
        return page.locator('input[name="username"]').count() > 0

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

    def test_feed_surface_requires_posts_caught_up_or_explicit_empty_state(self):
        session = _DomSession(_PageWithUrl(self.page, "https://www.instagram.com/?variant=following"))
        feed = PlaywrightFollowingFeed(session)

        self.page.set_content("<main><p>Something went wrong</p></main>")
        self.assertFalse(feed._has_post_surface())
        self.assertIn("Something went wrong", feed._feed_error_message())
        with self.assertRaises(FollowingFeedUnavailableError):
            feed._wait_until_following_surface(timeout_seconds=0)

        self.page.set_content('<main><article><a href="/p/ABC123/">post</a></article></main>')
        self.assertTrue(feed._has_post_surface())
        self.assertIsNone(feed._feed_error_message())
        feed._wait_until_following_surface(timeout_seconds=0)

        self.page.set_content(
            "<main><p>When you follow people, you'll see the photos and videos they post here.</p></main>"
        )
        self.assertTrue(feed._has_legitimate_empty_state())
        feed._wait_until_following_surface(timeout_seconds=0)

    def test_onetap_route_is_treated_as_logged_out(self):
        session = _DomSession(_PageWithUrl(self.page, "https://www.instagram.com/accounts/onetap/"))
        feed = PlaywrightFollowingFeed(session)

        self.page.set_content("<main><p>Continue with Instagram</p></main>")

        self.assertTrue(feed._looks_logged_out())

    def test_common_dialogs_are_dismissed_in_every_supported_locale(self):
        feed = PlaywrightFollowingFeed(self.session)
        for label in (
            "Not Now",
            "나중에 하기",
            "後で",
            "Ahora no",
            "Plus tard",
            "Jetzt nicht",
            "Agora não",
            "Не сейчас",
            "暂不",
            "暫不",
        ):
            with self.subTest(label=label):
                self.page.set_content(
                    f'<div role="dialog" id="modal"><button '
                    f"onclick=\"document.querySelector('#modal').remove()\">{label}</button></div>"
                )
                feed._dismiss_common_dialogs()
                self.assertEqual(self.page.locator("#modal").count(), 0)

    def test_restriction_dialog_is_never_dismissed_as_a_common_popup(self):
        self.page.set_content('<div role="dialog" id="restriction"><p>Action blocked</p><button>Close</button></div>')
        feed = PlaywrightFollowingFeed(self.session)
        feed._dismiss_common_dialogs()
        self.assertEqual(self.page.locator("#restriction").count(), 1)
        self.assertIsNotNone(feed.restriction_message())

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

    def test_caught_up_markers_are_recognized_in_every_supported_locale(self):
        feed = PlaywrightFollowingFeed(self.session)
        phrases = (
            "You're all caught up",
            "모두 확인했습니다",
            "すべてチェック済みです",
            "Estás al día",
            "Vous êtes à jour",
            "Du bist auf dem neuesten Stand",
            "Você está em dia",
            "Вы всё просмотрели",
            "以上是最新动态",
            "以上是最新動態",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.page.set_content(f"<main><div id='caught-up'>{phrase}</div></main>")
                self.assertTrue(feed.is_caught_up())

    def test_posts_before_caught_up_excludes_articles_below_marker(self):
        self.page.set_content(
            """
            <main>
              <article><a href="/p/recent/">recent</a></article>
              <div id="caught-up">Estás al día</div>
              <article><a href="/p/older/">older</a></article>
            </main>
            """
        )
        feed = PlaywrightFollowingFeed(self.session)

        posts, caught_up = feed.posts_before_caught_up()

        self.assertTrue(caught_up)
        self.assertEqual([post.key for post in posts], ["/p/recent/"])


if __name__ == "__main__":
    unittest.main()
