"""Chrome-based desktop auto-liker for Instagram's Following feed."""

from .config import AppConfig, Storage
from .engine import FollowingAutoLiker
from .scanner import FollowingFeedScanner

__all__ = ["AppConfig", "FollowingAutoLiker", "FollowingFeedScanner", "Storage"]
