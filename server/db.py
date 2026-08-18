"""Server-side SQLite schema and connection helper.

Mirrors the `_AppDB` pattern from wire_planner.py: a new connection is
opened per call, so the single `Database` instance is safe to share across
request handlers and the websocket hub without any locking of our own
(SQLite handles that). Autocommit (isolation_level=None) matches the
existing app's convention.
"""

import os
import sqlite3

DEFAULT_PATH = os.path.expanduser("~/.redlinerouting-server.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id           TEXT PRIMARY KEY,
    email        TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS client_keys(
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    public_key  TEXT NOT NULL UNIQUE,   -- hex-encoded 32-byte Ed25519 public key
    fingerprint TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    revoked_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_client_keys_user ON client_keys(user_id);

CREATE TABLE IF NOT EXISTS enrollment_codes(
    code       TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    roles_json TEXT NOT NULL DEFAULT '[]',   -- [{role, project_id}]
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions(
    token           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    key_fingerprint TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS challenges(
    nonce      TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS user_roles(
    user_id    TEXT NOT NULL REFERENCES users(id),
    role       TEXT NOT NULL,
    project_id TEXT,   -- NULL = global role
    PRIMARY KEY (user_id, role, project_id)
);

CREATE TABLE IF NOT EXISTS projects(
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    site       TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

-- Append-only, signed, hash-chained event log. One chain per
-- (project_id, device_fingerprint) — see the architecture notes on why
-- per-device chains rather than one global chain per project.
CREATE TABLE IF NOT EXISTS events(
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(id),
    device_fingerprint TEXT NOT NULL,
    entity_type       TEXT NOT NULL,
    entity_id         TEXT NOT NULL,
    op                TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    client_time       TEXT NOT NULL,
    server_time       TEXT NOT NULL,
    prev_event_id     TEXT,
    needs_review       INTEGER NOT NULL DEFAULT 0,
    signature         TEXT NOT NULL,        -- hex-encoded 64-byte Ed25519 signature
    signer_fingerprint TEXT NOT NULL,
    hash              TEXT NOT NULL,
    prev_hash         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, server_time);
CREATE INDEX IF NOT EXISTS idx_events_device ON events(project_id, device_fingerprint, server_time);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id);

-- Replayed/derived current-state tables (fast queries; source of truth is `events`).
CREATE TABLE IF NOT EXISTS drawings(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', rev TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS maintenance_standards(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    standard_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL DEFAULT '',
    url_telecom TEXT NOT NULL DEFAULT '', url_transmission TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS engineering_standards(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    standard_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', revision TEXT NOT NULL DEFAULT '',
    standard_type TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tailboard_refs(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,   -- tailboard/hbr/loa/safety_regs
    url TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS files(
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id),
    owner_type    TEXT NOT NULL,   -- drawing/maintenance_standard/engineering_standard/tailboard/job/form
    owner_id      TEXT NOT NULL,
    filename      TEXT NOT NULL,
    storage_path  TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    revision      INTEGER NOT NULL DEFAULT 1,
    uploaded_by   TEXT NOT NULL REFERENCES users(id),
    uploaded_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner_type, owner_id);

CREATE TABLE IF NOT EXISTS jobs(
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    type       TEXT NOT NULL,
    data_json  TEXT NOT NULL,   -- shared.models.empty_job() shape
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);

CREATE TABLE IF NOT EXISTS job_steps(
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    added_by      TEXT NOT NULL REFERENCES users(id),
    added_at      TEXT NOT NULL,
    assigned_to   TEXT REFERENCES users(id),
    completed_by  TEXT REFERENCES users(id),
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_steps_job ON job_steps(job_id);

CREATE TABLE IF NOT EXISTS digital_forms(
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id),
    name          TEXT NOT NULL,
    schema_json   TEXT NOT NULL,
    source_pdf_id TEXT,
    created_by    TEXT NOT NULL REFERENCES users(id),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS form_submissions(
    id           TEXT PRIMARY KEY,
    form_id      TEXT NOT NULL REFERENCES digital_forms(id),
    project_id   TEXT NOT NULL REFERENCES projects(id),
    data_json    TEXT NOT NULL,
    completed_by TEXT NOT NULL REFERENCES users(id),
    completed_at TEXT NOT NULL
);
"""


class Database:
    """Server database (~/.redlinerouting-server.db by default).

    A new connection is opened per call — safe to share this one instance
    across request handlers and the websocket hub.
    """

    def __init__(self, path=None):
        self.path = path or DEFAULT_PATH
        with self.conn() as c:
            c.executescript(_SCHEMA)

    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.isolation_level = None  # autocommit
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c
