from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apps.following_auto_like.core import (
    MAX_PROCESSED_MEDIA_IDS,
    AccountPaths,
    AccountState,
    AutomationConfig,
    StateStore,
    baseline_media_ids,
    scan_timeline,
)


class FollowingAutoLikeCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    def test_scan_selects_only_recent_unliked_media_from_following(self) -> None:
        payload = {
            "feed_items": [
                self._item("101_1", "1", minutes_ago=5, username="followed"),
                self._item("102_2", "2", minutes_ago=5, username="not_followed"),
                self._item(
                    "103_1",
                    "1",
                    minutes_ago=5,
                    username="already_liked",
                    has_liked=True,
                ),
                self._item("104_1", "1", minutes_ago=1_500, username="too_old"),
                self._item("105_1", "1", minutes_ago=5, username="processed"),
                self._item("106_9", "9", minutes_ago=5, username="self"),
            ]
        }

        result = scan_timeline(
            payload,
            following_ids={"1"},
            processed_ids={"105_1"},
            lookback_hours=24,
            now=self.now,
            own_user_id="9",
        )

        self.assertEqual([candidate.media_id for candidate in result.candidates], ["101_1"])
        self.assertEqual(result.candidates[0].username, "followed")
        self.assertEqual(result.counters["not_following"], 1)
        self.assertEqual(result.counters["already_liked"], 1)
        self.assertEqual(result.counters["too_old"], 1)
        self.assertEqual(result.counters["already_processed"], 1)
        self.assertEqual(result.counters["own_media"], 1)

    def test_scan_excludes_ads_and_suggested_posts_even_from_following(self) -> None:
        ad = self._item("201_1", "1", minutes_ago=2, username="ad")
        ad["is_ad"] = True
        suggested = self._item("202_1", "1", minutes_ago=2, username="suggested")
        suggested["suggested_post_info"] = {"reason": "recommended"}
        payload = {"feed_items": [ad, suggested]}

        result = scan_timeline(
            payload,
            following_ids={"1"},
            processed_ids=set(),
            lookback_hours=24,
            now=self.now,
        )

        self.assertEqual(result.candidates, ())
        self.assertEqual(set(result.handled_media_ids), {"201_1", "202_1"})
        self.assertEqual(result.counters["ad_or_suggested"], 2)

    def test_baseline_collects_both_supported_feed_shapes(self) -> None:
        first = self._item("301_1", "1", minutes_ago=1)
        second_media = self._item("302_2", "2", minutes_ago=1)["media_or_ad"]
        payload = {
            "feed_items": [
                first,
                {"media": second_media},
                {"end_of_feed_demarcator": {}},
            ]
        }

        self.assertEqual(baseline_media_ids(payload), ("301_1", "302_2"))

    def test_state_rolls_over_daily_counter_and_caps_processed_ids(self) -> None:
        state = AccountState()
        state.record_like("first", "2026-08-20")
        self.assertEqual(state.likes_today("2026-08-20"), 1)
        self.assertEqual(state.likes_today("2026-08-21"), 0)

        self.assertTrue(state.ensure_day("2026-08-21"))
        self.assertEqual(state.daily_likes, 0)
        state.mark_processed([str(index) for index in range(MAX_PROCESSED_MEDIA_IDS + 5)])

        self.assertEqual(len(state.processed_media_ids), MAX_PROCESSED_MEDIA_IDS)
        self.assertEqual(state.processed_media_ids[0], "5")

    def test_state_store_and_account_paths_are_account_specific(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_dir = Path(temporary_directory)
            first_paths = AccountPaths.for_username(base_dir, "Example.User")
            second_paths = AccountPaths.for_username(base_dir, "another_user")
            self.assertNotEqual(first_paths.state, second_paths.state)
            self.assertNotIn("password", str(first_paths.state).lower())

            state = AccountState(
                initialized=True,
                processed_media_ids=["401_1"],
                daily_date="2026-08-21",
                daily_likes=3,
            )
            store = StateStore(first_paths.state)
            store.save(state)
            loaded = store.load()

            self.assertTrue(loaded.initialized)
            self.assertEqual(loaded.processed_media_ids, ["401_1"])
            self.assertEqual(loaded.likes_today("2026-08-21"), 3)

    def test_config_rejects_aggressive_or_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            AutomationConfig(check_interval_minutes=5).validate()
        with self.assertRaises(ValueError):
            AutomationConfig(min_delay_seconds=10).validate()
        with self.assertRaises(ValueError):
            AutomationConfig(min_delay_seconds=200, max_delay_seconds=100).validate()

    def _item(
        self,
        media_id: str,
        user_id: str,
        *,
        minutes_ago: int,
        username: str = "user",
        has_liked: bool = False,
    ) -> dict[str, object]:
        taken_at = self.now - timedelta(minutes=minutes_ago)
        return {
            "media_or_ad": {
                "id": media_id,
                "pk": media_id.split("_", 1)[0],
                "taken_at": int(taken_at.timestamp()),
                "has_liked": has_liked,
                "code": f"CODE{media_id.split('_', 1)[0]}",
                "product_type": "feed",
                "user": {
                    "pk": user_id,
                    "username": username,
                },
            }
        }


if __name__ == "__main__":
    unittest.main()
