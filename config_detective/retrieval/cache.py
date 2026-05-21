"""SQLite cache for external API responses.

This module provides aggressive caching to avoid burning rate limits
during development and repeated investigations.

Features:
- TTL-based expiration (configurable per source)
- Automatic cleanup of expired entries
- Thread-safe operations
- Stores serialized JSON responses

Cache location: ~/.config-detective/cache.db
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default TTLs per source (in hours)
DEFAULT_TTLS: dict[str, int] = {
    "github": 24,  # GitHub issues can change frequently
    "stackoverflow": 24,  # SO answers can be edited
    "osv": 168,  # CVEs change less often (7 days)
    "libraries_io": 72,  # Package metadata (3 days)
    "default": 24,
}

# Cache database location
DEFAULT_CACHE_DIR = Path.home() / ".config-detective"
DEFAULT_CACHE_FILE = "cache.db"


class RetrievalCache:
    """SQLite-based cache for external API responses.

    Thread-safe singleton cache that stores serialized responses
    with TTL-based expiration.

    Usage:
        cache = RetrievalCache.get_instance()

        # Check cache
        cached = cache.get("github", query_hash)
        if cached:
            return cached

        # Fetch from API...

        # Store in cache
        cache.set("github", query_hash, results)
    """

    _instance: "RetrievalCache | None" = None
    _lock = threading.Lock()

    def __init__(self, cache_path: Path | None = None):
        """Initialize the cache.

        Args:
            cache_path: Path to cache database. Defaults to ~/.config-detective/cache.db
        """
        if cache_path is None:
            cache_path = DEFAULT_CACHE_DIR / DEFAULT_CACHE_FILE

        self.cache_path = cache_path
        self._ensure_cache_dir()
        self._init_db()

    @classmethod
    def get_instance(cls, cache_path: Path | None = None) -> "RetrievalCache":
        """Get the singleton cache instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(cache_path)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def _ensure_cache_dir(self) -> None:
        """Create cache directory if it doesn't exist."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def _init_db(self) -> None:
        """Initialize the SQLite database schema."""
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    UNIQUE(source, query_hash)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_lookup 
                ON cache (source, query_hash, expires_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_expiry 
                ON cache (expires_at)
            """)
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        return sqlite3.connect(self.cache_path, timeout=10.0)

    def get(self, source: str, query_hash: str) -> list[dict[str, Any]] | None:
        """Get cached results if available and not expired.

        Args:
            source: The source name (github, stackoverflow, osv)
            query_hash: Hash of the query

        Returns:
            Cached results as list of dicts, or None if not cached/expired
        """
        now = datetime.utcnow().isoformat()

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT response_json, id FROM cache 
                    WHERE source = ? AND query_hash = ? AND expires_at > ?
                    """,
                    (source, query_hash, now),
                )
                row = cursor.fetchone()

                if row:
                    # Update hit count
                    conn.execute(
                        "UPDATE cache SET hit_count = hit_count + 1 WHERE id = ?",
                        (row[1],),
                    )
                    conn.commit()

                    logger.debug(f"Cache hit for {source}:{query_hash[:8]}")
                    return json.loads(row[0])

                logger.debug(f"Cache miss for {source}:{query_hash[:8]}")
                return None

        except Exception as e:
            logger.error(f"Cache read error: {e}")
            return None

    def set(
        self,
        source: str,
        query_hash: str,
        results: list[dict[str, Any]],
        ttl_hours: int | None = None,
    ) -> bool:
        """Store results in cache.

        Args:
            source: The source name
            query_hash: Hash of the query
            results: Results to cache (list of dicts)
            ttl_hours: Optional custom TTL (defaults to source-specific TTL)

        Returns:
            True if cached successfully
        """
        if ttl_hours is None:
            ttl_hours = DEFAULT_TTLS.get(source, DEFAULT_TTLS["default"])

        now = datetime.utcnow()
        expires_at = now + timedelta(hours=ttl_hours)

        try:
            response_json = json.dumps(results)

            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache 
                    (source, query_hash, response_json, created_at, expires_at, hit_count)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (
                        source,
                        query_hash,
                        response_json,
                        now.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                conn.commit()

            logger.debug(f"Cached {len(results)} results for {source}:{query_hash[:8]}")
            return True

        except Exception as e:
            logger.error(f"Cache write error: {e}")
            return False

    def invalidate(self, source: str | None = None, query_hash: str | None = None) -> int:
        """Invalidate cache entries.

        Args:
            source: Optional source to filter by
            query_hash: Optional query hash to filter by

        Returns:
            Number of entries invalidated
        """
        try:
            with self._get_connection() as conn:
                if source and query_hash:
                    cursor = conn.execute(
                        "DELETE FROM cache WHERE source = ? AND query_hash = ?",
                        (source, query_hash),
                    )
                elif source:
                    cursor = conn.execute(
                        "DELETE FROM cache WHERE source = ?", (source,)
                    )
                elif query_hash:
                    cursor = conn.execute(
                        "DELETE FROM cache WHERE query_hash = ?", (query_hash,)
                    )
                else:
                    cursor = conn.execute("DELETE FROM cache")

                conn.commit()
                return cursor.rowcount

        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")
            return 0

    def cleanup_expired(self) -> int:
        """Remove expired cache entries.

        Returns:
            Number of entries removed
        """
        now = datetime.utcnow().isoformat()

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM cache WHERE expires_at <= ?", (now,)
                )
                conn.commit()
                count = cursor.rowcount

                if count > 0:
                    logger.info(f"Cleaned up {count} expired cache entries")

                return count

        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")
            return 0

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache statistics
        """
        now = datetime.utcnow().isoformat()

        try:
            with self._get_connection() as conn:
                # Total entries
                total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]

                # Valid (non-expired) entries
                valid = conn.execute(
                    "SELECT COUNT(*) FROM cache WHERE expires_at > ?", (now,)
                ).fetchone()[0]

                # Entries per source
                by_source = {}
                for row in conn.execute(
                    "SELECT source, COUNT(*) FROM cache GROUP BY source"
                ):
                    by_source[row[0]] = row[1]

                # Total hits
                total_hits = (
                    conn.execute("SELECT SUM(hit_count) FROM cache").fetchone()[0] or 0
                )

                # Cache size on disk
                cache_size = self.cache_path.stat().st_size if self.cache_path.exists() else 0

                return {
                    "total_entries": total,
                    "valid_entries": valid,
                    "expired_entries": total - valid,
                    "entries_by_source": by_source,
                    "total_hits": total_hits,
                    "cache_size_bytes": cache_size,
                    "cache_path": str(self.cache_path),
                }

        except Exception as e:
            logger.error(f"Cache stats error: {e}")
            return {"error": str(e)}


def make_cache_key(*parts: str) -> str:
    """Create a cache key from multiple parts.

    Combines parts and hashes them to create a consistent cache key.

    Args:
        *parts: Strings to combine into a cache key

    Returns:
        SHA256 hash of the combined parts
    """
    combined = "|".join(str(p) for p in parts if p)
    return hashlib.sha256(combined.encode()).hexdigest()


def get_cache() -> RetrievalCache:
    """Get the global cache instance.

    Convenience function for getting the singleton cache.
    """
    return RetrievalCache.get_instance()
