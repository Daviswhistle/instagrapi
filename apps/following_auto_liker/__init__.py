"""Desktop auto-liker for new posts from accounts the user follows."""

from .engine import FollowingAutoLiker
from .storage import AppConfig, Storage

__all__ = ["AppConfig", "FollowingAutoLiker", "Storage"]
