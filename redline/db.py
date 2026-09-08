"""Global application database and drawing-search cache.

_AppDB   — SQLite wrapper for settings, standards library, drawing cache,
           engineering standards cache, and control room desks.
_GlobalDrawingCache — thin facade over _AppDB's drawing-cache tables,
           exposing the same get/put/iter_keys interface as the old
           per-project cache.
"""
import json
import os
import sqlite3
from datetime import datetime

try:
    from drawing_search import DrawingResult
    _DRAWING_SEARCH_AVAILABLE = True
except ImportError:
    _DRAWING_SEARCH_AVAILABLE = False
    DrawingResult = None


class _AppDB:
    """Global application database (~/.redlinerouting.db).

    Holds the app settings, the cross-project standards library, and the
    drawing-search cache shared by all projects. A new connection is
    opened per call so the same instance is safe from any thread
    (background cache refreshes write here too).

    On first run, settings and the standards library are migrated from
    the legacy ~/.redlinerouting.json (the file is left in place).
    """

    PATH = os.path.expanduser("~/.redlinerouting.db")
    _LEGACY_JSON = os.path.expanduser("~/.redlinerouting.json")

    def __init__(self, path=None):
        self.path = path or self.PATH
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS config(
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS standards_library(
                    kind        TEXT NOT NULL,
                    standard_id TEXT NOT NULL,
                    info        TEXT NOT NULL,
                    updated_at  REAL NOT NULL,
                    PRIMARY KEY (kind, standard_id));
                CREATE TABLE IF NOT EXISTS drawing_cache(
                    cache_key TEXT PRIMARY KEY,
                    results   TEXT NOT NULL,
                    cached_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS eng_standards_cache(
                    series_value TEXT PRIMARY KEY,
                    results      TEXT NOT NULL,
                    cached_at    REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS control_room_desks(
                    desk_id     TEXT PRIMARY KEY,
                    desk_name   TEXT NOT NULL DEFAULT '',
                    desk_type   TEXT NOT NULL DEFAULT '',
                    phone_int   TEXT NOT NULL DEFAULT '',
                    phone_local TEXT NOT NULL DEFAULT '',
                    phone_toll  TEXT NOT NULL DEFAULT '',
                    stations    TEXT NOT NULL DEFAULT '[]');
            """)
        self._migrate_legacy_json()

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.isolation_level = None   # autocommit
        return conn

    def _migrate_legacy_json(self):
        try:
            with self._conn() as c:
                if c.execute("SELECT COUNT(*) FROM config").fetchone()[0]:
                    return   # already migrated / in use
            with open(self._LEGACY_JSON, encoding="utf-8") as fh:
                legacy = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, sqlite3.Error):
            return
        lib = legacy.pop("standards_library", {})
        self.save_config(legacy)
        for kind, bucket in lib.items():
            for sid, info in bucket.items():
                self.put_standard(kind, sid, info)

    # ── config ────────────────────────────────────────────────────

    def load_config(self) -> dict:
        try:
            with self._conn() as c:
                rows = c.execute("SELECT key, value FROM config").fetchall()
            return {k: json.loads(v) for k, v in rows}
        except (sqlite3.Error, json.JSONDecodeError):
            return {}

    def save_config(self, cfg: dict):
        with self._conn() as c:
            c.execute("BEGIN")
            c.execute("DELETE FROM config")
            c.executemany(
                "INSERT INTO config(key, value) VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in cfg.items()])
            c.execute("COMMIT")

    # ── standards library ─────────────────────────────────────────

    def get_standards(self, kind) -> dict:
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT standard_id, info FROM standards_library "
                    "WHERE kind = ?", (kind,)).fetchall()
            return {sid: json.loads(info) for sid, info in rows}
        except (sqlite3.Error, json.JSONDecodeError):
            return {}

    def put_standard(self, kind, sid, info):
        with self._conn() as c:
            c.execute(
                "INSERT INTO standards_library(kind, standard_id, info, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, standard_id) DO UPDATE SET "
                "info = excluded.info, updated_at = excluded.updated_at",
                (kind, sid, json.dumps(info), datetime.now().timestamp()))

    def delete_standard(self, kind, sid):
        with self._conn() as c:
            c.execute("DELETE FROM standards_library "
                      "WHERE kind = ? AND standard_id = ?", (kind, sid))

    # ── drawing search cache ──────────────────────────────────────

    def cache_get(self, key):
        """Return the cached list of result dicts for key, or None."""
        try:
            with self._conn() as c:
                row = c.execute(
                    "SELECT results FROM drawing_cache WHERE cache_key = ?",
                    (key,)).fetchone()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, json.JSONDecodeError):
            return None

    def cache_put(self, key, results, cached_at=None):
        with self._conn() as c:
            c.execute(
                "INSERT INTO drawing_cache(cache_key, results, cached_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET "
                "results = excluded.results, cached_at = excluded.cached_at",
                (key, json.dumps(results),
                 cached_at if cached_at is not None
                 else datetime.now().timestamp()))

    def cache_keys(self):
        try:
            with self._conn() as c:
                return [r[0] for r in
                        c.execute("SELECT cache_key FROM drawing_cache")]
        except sqlite3.Error:
            return []

    def cache_stale_keys(self, max_age_seconds):
        """Return cache keys whose entry is older than max_age_seconds."""
        cutoff = datetime.now().timestamp() - max_age_seconds
        try:
            with self._conn() as c:
                return [r[0] for r in c.execute(
                    "SELECT cache_key FROM drawing_cache WHERE cached_at < ?",
                    (cutoff,))]
        except sqlite3.Error:
            return []

    # ── engineering standards cache ───────────────────────────────

    def eng_cache_load_all(self):
        """Return all rows as {series_value: (cached_at, results_json_str)}."""
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT series_value, results, cached_at FROM eng_standards_cache"
                ).fetchall()
            return {r[0]: (r[2], r[1]) for r in rows}
        except sqlite3.Error:
            return {}

    def eng_cache_put(self, series_value: str, results_json: str, cached_at: float):
        with self._conn() as c:
            c.execute(
                "INSERT INTO eng_standards_cache(series_value, results, cached_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(series_value) DO UPDATE SET "
                "results = excluded.results, cached_at = excluded.cached_at",
                (series_value, results_json, cached_at))

    def eng_cache_stale_values(self, max_age_seconds: float):
        cutoff = datetime.now().timestamp() - max_age_seconds
        try:
            with self._conn() as c:
                return [r[0] for r in c.execute(
                    "SELECT series_value FROM eng_standards_cache WHERE cached_at < ?",
                    (cutoff,))]
        except sqlite3.Error:
            return []

    def cache_merge_legacy(self, store: dict):
        """Import a per-project drawing_search_cache dict from an old .redline.

        Entries newer than what the DB already holds win.
        """
        for key, entry in store.items():
            try:
                ts = float(entry.get("cached_at", 0))
                results = entry.get("results", [])
            except (AttributeError, TypeError, ValueError):
                continue
            with self._conn() as c:
                row = c.execute(
                    "SELECT cached_at FROM drawing_cache WHERE cache_key = ?",
                    (key,)).fetchone()
            if row is None or row[0] < ts:
                self.cache_put(key, results, cached_at=ts)

    # ── control room desks ────────────────────────────────────────

    def get_ctrl_desks(self) -> list:
        """Return all control room desks ordered by name."""
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT desk_id,desk_name,desk_type,phone_int,phone_local,phone_toll,stations "
                    "FROM control_room_desks ORDER BY desk_name"
                ).fetchall()
            return [{"desk_id": r[0], "desk_name": r[1], "desk_type": r[2],
                     "phone_int": r[3], "phone_local": r[4], "phone_toll": r[5],
                     "stations": json.loads(r[6])} for r in rows]
        except (sqlite3.Error, json.JSONDecodeError):
            return []

    def put_ctrl_desk(self, desk: dict):
        with self._conn() as c:
            c.execute(
                "INSERT INTO control_room_desks"
                "(desk_id,desk_name,desk_type,phone_int,phone_local,phone_toll,stations) "
                "VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(desk_id) DO UPDATE SET "
                "desk_name=excluded.desk_name, desk_type=excluded.desk_type, "
                "phone_int=excluded.phone_int, phone_local=excluded.phone_local, "
                "phone_toll=excluded.phone_toll, stations=excluded.stations",
                (desk["desk_id"], desk.get("desk_name", ""), desk.get("desk_type", ""),
                 desk.get("phone_int", ""), desk.get("phone_local", ""),
                 desk.get("phone_toll", ""), json.dumps(desk.get("stations", []))))

    def delete_ctrl_desk(self, desk_id: str):
        with self._conn() as c:
            c.execute("DELETE FROM control_room_desks WHERE desk_id=?", (desk_id,))


class _GlobalDrawingCache:
    """Drawing search cache shared across all projects (SQLite-backed).

    Only categorical searches (facility / drawing_type / drawing_subject / state,
    no free-text filters) are cached and auto-refreshed.
    """

    def __init__(self, db: "_AppDB"):
        self._db = db

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def is_cacheable(params) -> bool:
        return not any([
            params.drawing_num, params.title, params.title2,
            params.serial_from, params.serial_to,
            params.manufacturer_name, params.manufacturer_doc_num,
            params.remarks_contain, params.legacy_document_num,
        ])

    @staticmethod
    def _key(params) -> str:
        return f"{params.facility}|{params.drawing_type}|{params.drawing_subject}|{params.state}"

    # ── public API ─────────────────────────────────────────────────

    def get(self, params) -> "list | None":
        if not self.is_cacheable(params):
            return None
        raw = self._db.cache_get(self._key(params))
        if raw is None:
            return None
        try:
            return [DrawingResult(**d) for d in raw]
        except Exception:
            return None

    def put(self, params, results: list) -> None:
        if not self.is_cacheable(params):
            return
        self._db.cache_put(self._key(params), [vars(r) for r in results])

    def iter_keys(self):
        """Yield (facility, drawing_type, drawing_subject, state) for every cached entry."""
        for k in self._db.cache_keys():
            parts = k.split("|")
            if len(parts) == 4:
                yield tuple(parts)
