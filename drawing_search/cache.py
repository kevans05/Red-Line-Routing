"""SQLite-backed result cache for drawing search queries.

Cache key: SHA-256 of the sorted params form-data serialised to JSON.
Default TTL: 24 hours (86 400 seconds).
"""

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict
from typing import Optional

from .models import DrawingResult

_DEFAULT_DB_PATH = os.path.expanduser("~/.redlinerouting_drawing_cache.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS search_cache (
    params_hash  TEXT PRIMARY KEY,
    params_json  TEXT NOT NULL,
    results_json TEXT NOT NULL,
    cached_at    REAL NOT NULL
)
"""


def _params_hash(params) -> str:
    """Return a stable SHA-256 hex digest for a SearchParams instance."""
    form_data = params._to_form_data()
    serialised = json.dumps(form_data, sort_keys=True)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


class DrawingSearchCache:
    """Persistent SQLite cache for drawing search results."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    # ── public API ────────────────────────────────────────────────

    def get(self, params) -> "Optional[list[DrawingResult]]":
        """Return cached results for *params*, or None on a cache miss."""
        key = _params_hash(params)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT results_json FROM search_cache WHERE params_hash = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row[0])
            return [DrawingResult(**d) for d in raw]
        except Exception:
            return None

    def put(self, params, results: "list[DrawingResult]") -> None:
        """Store *results* in the cache under the key derived from *params*."""
        if not results:
            # Never cache empty results — they are indistinguishable from an
            # auth failure (server returned a login redirect instead of data).
            return
        key = _params_hash(params)
        params_json = json.dumps(params._to_form_data(), sort_keys=True)
        results_json = json.dumps([asdict(r) for r in results])
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO search_cache (params_hash, params_json, results_json, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(params_hash) DO UPDATE SET
                    params_json  = excluded.params_json,
                    results_json = excluded.results_json,
                    cached_at    = excluded.cached_at
                """,
                (key, params_json, results_json, now),
            )

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._connect() as conn:
            conn.execute("DELETE FROM search_cache")

    def clear_expired(self, ttl_seconds: float = 86400) -> int:
        """Delete entries older than *ttl_seconds*. Returns number of rows removed."""
        cutoff = time.time() - ttl_seconds
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM search_cache WHERE cached_at < ?", (cutoff,)
            )
            return cur.rowcount

    # ── internals ─────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.isolation_level = None          # autocommit
        return conn
