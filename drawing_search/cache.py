"""SQLite-backed result cache for drawing search queries.

Cache key: SHA-256 of the sorted params form-data serialised to JSON.
The cache never auto-expires; callers check age and decide whether to
prompt the user for a refresh.
"""

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict
from typing import Optional, Tuple

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
        """Return cached results for *params*, or None on a cache miss (ignores age)."""
        results, _ = self.get_with_age(params)
        return results

    def get_with_age(self, params) -> "Tuple[Optional[list[DrawingResult]], float]":
        """Return (results, age_in_seconds) or (None, 0.0) on a cache miss."""
        key = _params_hash(params)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT results_json, cached_at FROM search_cache WHERE params_hash = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None, 0.0
        try:
            raw = json.loads(row[0])
            results = [DrawingResult(**d) for d in raw]
            age = time.time() - row[1]
            return results, age
        except Exception:
            return None, 0.0

    def put(self, params, results: "list[DrawingResult]") -> None:
        """Store *results* in the cache under the key derived from *params*."""
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

    def count(self) -> int:
        """Return the number of cached query entries."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]

    # ── internals ─────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.isolation_level = None          # autocommit
        return conn
