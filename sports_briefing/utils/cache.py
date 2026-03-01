"""
Lightweight disk-based JSON cache with TTL.

Cache entries are stored as individual JSON files named by the MD5 hash of
the cache key.  An entry is considered stale after *ttl_hours* hours and will
be re-fetched transparently.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Cache:
    """JSON file cache with time-to-live expiry."""

    def __init__(self, cache_dir: Path, ttl_hours: int = 6) -> None:
        """Initialise the cache.

        Args:
            cache_dir: Directory where cache files are stored.  Created
                       automatically if it does not exist.
            ttl_hours: Number of hours before an entry is considered stale.
        """
        self.cache_dir = cache_dir
        self.ttl = timedelta(hours=ttl_hours)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return cached data for *key*, or ``None`` if missing / expired.

        Args:
            key: Logical cache key (human-readable string).

        Returns:
            The cached Python object, or ``None``.
        """
        path = self._key_to_path(key)
        if not path.exists():
            return None

        try:
            with path.open(encoding="utf-8") as fh:
                entry = json.load(fh)

            cached_at = datetime.fromisoformat(entry["cached_at"])
            if datetime.now() - cached_at > self.ttl:
                logger.debug("Cache expired for key: %s", key)
                return None

            return entry["data"]

        except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            logger.warning("Cache read error for key '%s': %s", key, exc)
            return None

    def set(self, key: str, data: Any) -> None:
        """Persist *data* under *key*.

        Non-serialisable values are silently dropped — the fetch will just
        happen again on the next run.

        Args:
            key:  Logical cache key.
            data: JSON-serialisable Python object.
        """
        path = self._key_to_path(key)
        try:
            with path.open("w", encoding="utf-8") as fh:
                json.dump({"cached_at": datetime.now().isoformat(), "data": data}, fh)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Cache write error for key '%s': %s", key, exc)

    def clear(self) -> None:
        """Delete all cache files."""
        for path in self.cache_dir.glob("*.json"):
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Could not delete cache file %s: %s", path, exc)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _key_to_path(self, key: str) -> Path:
        """Map a logical key to a deterministic file path."""
        digest = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"
