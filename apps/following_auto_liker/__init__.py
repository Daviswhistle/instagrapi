"""Chrome-based desktop auto-liker for Instagram's Following feed."""

from .engine import FollowingAutoLiker, FollowingFeedScanner
from .storage import AppConfig, Storage

__all__ = ["AppConfig", "FollowingAutoLiker", "FollowingFeedScanner", "Storage"]
