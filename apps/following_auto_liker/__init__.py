"""Chrome-based desktop auto-liker for Instagram's Following feed."""

from .engine import FollowingAutoLiker, FollowingFeedScanner
from .storage import AppAlreadyRunningError, AppConfig, Storage

__all__ = [
    "AppAlreadyRunningError",
    "AppConfig",
    "FollowingAutoLiker",
    "FollowingFeedScanner",
    "Storage",
]
