from __future__ import annotations

import re

def safe_error_text(exc: Exception) -> str:
    return truncate_text(str(exc).replace("\n", " "), limit=240) or type(exc).__name__


def truncate_text(value: str, limit: int = 300) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
