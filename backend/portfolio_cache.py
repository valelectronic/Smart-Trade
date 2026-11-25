"""Small process-local cache for per-user Cantex account balances.

The cache is shared by /api/balances and /api/portfolio-value.
It stores only successful balance responses and never mixes users.
"""
from __future__ import annotations

import threading
import time
from typing import Any

TTL_SECONDS = 20

_cache: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()


def get(user_id: str) -> dict[str, Any] | None:
    """Return a fresh cached balance response, or None when absent/expired."""
    key = str(user_id)
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        if now - entry["timestamp"] >= TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return entry["data"]


def set(user_id: str, data: dict[str, Any]) -> None:
    """Store a successful balance response for this user."""
    key = str(user_id)
    with _lock:
        _cache[key] = {"data": data, "timestamp": time.monotonic()}


def invalidate(user_id: str) -> None:
    """Remove this user's cached balance response."""
    with _lock:
        _cache.pop(str(user_id), None)
