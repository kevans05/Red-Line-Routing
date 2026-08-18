"""Replay accepted events onto the derived, fast-to-query tables.

The `events` log is the source of truth (see server/chain.py); the
registry tables and jobs/job_steps are just a materialized view of it,
rebuilt incrementally as each event is accepted.

Two kinds of events flow through here:

- **Registry events** (`drawing`/`maintenance_standard`/
  `engineering_standard`/`tailboard_ref`, ops `registry_add`/
  `registry_edit`) — keyed by the same natural key wire_planner.py's dict
  registries already use (drawing name, standard_id, tailboard kind),
  combined with project_id. Replaying the same event twice is a harmless
  upsert.
- **Job events** (`job`/`job_step`, ops `job_created`/`step_added`/
  `step_assigned`/`step_completed`) — the per-job checklist with full
  attribution the desktop app never had. `added_by`/`completed_by` are
  always derived from the event's *verified signer*, never trusted from
  the payload — a client can't claim someone else did the work.

Phase 2/3 keep replay conflict-free by construction: last-applied-wins per
entity. Real concurrent-edit conflict detection (flagging `needs_review`
for two genuinely racing edits from different offline devices) is Phase 4
work, once there's more than one device actually able to go offline.
"""

import json

from shared.models import JOB_TYPE_SHORT

REGISTRY_FIELDS = {
    "drawing": ("name", "title", "rev", "url", "notes"),
    "maintenance_standard": ("standard_id", "title", "revision", "url_telecom",
                              "url_transmission", "notes"),
    "engineering_standard": ("standard_id", "title", "revision", "standard_type",
                              "url", "notes"),
    "tailboard_ref": ("kind", "url"),
}

_TABLE = {
    "drawing": "drawings",
    "maintenance_standard": "maintenance_standards",
    "engineering_standard": "engineering_standards",
    "tailboard_ref": "tailboard_refs",
}

_NATURAL_KEY_FIELD = {
    "drawing": "name",
    "maintenance_standard": "standard_id",
    "engineering_standard": "standard_id",
    "tailboard_ref": "kind",
}

REGISTRY_ENTITY_TYPES = frozenset(REGISTRY_FIELDS)
REGISTRY_OPS = frozenset(("registry_add", "registry_edit"))

JOB_OPS = frozenset(("job_created",))
JOB_STEP_OPS = frozenset(("step_added", "step_assigned", "step_completed"))

# Minimum role required to post each (entity_type, op). Anything not listed
# falls back to DEFAULT_MIN_ROLE — the same "editor to write" behavior
# Phase 1/2 already had. `approver` is intentionally lower-privilege than
# `editor` in shared.models.ROLE_RANK but still allowed to complete a step
# they're doing the work on, per the RBAC model's "approver: complete/sign
# off assigned steps" — editors and admins can do it too, since roles are
# cumulative.
MIN_ROLE_FOR_OP = {
    ("job", "job_created"): "editor",
    ("job_step", "step_added"): "editor",
    ("job_step", "step_assigned"): "editor",
    ("job_step", "step_completed"): "approver",
}
DEFAULT_MIN_ROLE = "editor"


class ReplayError(ValueError):
    pass


def min_role_for_event(entity_type, op) -> str:
    return MIN_ROLE_FOR_OP.get((entity_type, op), DEFAULT_MIN_ROLE)


def is_registry_event(event: dict) -> bool:
    return event.get("entity_type") in REGISTRY_ENTITY_TYPES and event.get("op") in REGISTRY_OPS


def is_job_event(event: dict) -> bool:
    entity_type, op = event.get("entity_type"), event.get("op")
    return (entity_type == "job" and op in JOB_OPS) or \
        (entity_type == "job_step" and op in JOB_STEP_OPS)


def _resolve_signer_user_id(conn, fingerprint):
    row = conn.execute(
        "SELECT user_id FROM client_keys WHERE fingerprint=?", (fingerprint,),
    ).fetchone()
    if row is None:
        raise ReplayError(f"no user registered for signer fingerprint {fingerprint!r}")
    return row["user_id"]


def _lookup_job(conn, project_id, job_id):
    return conn.execute(
        "SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id),
    ).fetchone()


def _lookup_step(conn, project_id, step_id):
    return conn.execute(
        "SELECT job_steps.* FROM job_steps JOIN jobs ON job_steps.job_id = jobs.id "
        "WHERE job_steps.id=? AND jobs.project_id=?", (step_id, project_id),
    ).fetchone()


def _user_exists(conn, user_id):
    return conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is not None


def validate_payload(conn, event: dict) -> dict:
    """Parse and sanity-check an event's payload *before* the event is
    appended to the immutable log — a malformed or referentially-invalid
    payload (unknown job, unknown assignee, cross-project reference) must
    never be allowed to land in the log. Returns the parsed payload dict.
    No-op (returns {}) for event types this module doesn't know about.

    Call this before INSERT-ing into `events`; call `apply_event` after,
    once the event row is committed.
    """
    if is_registry_event(event):
        return _validate_registry(event)
    if is_job_event(event):
        return _validate_job(conn, event)
    return {}


def apply_event(conn, event: dict) -> None:
    """Apply one already-validated, already-committed event to its derived
    table. No-op for event types this module doesn't know about. Assumes
    `validate_payload` already succeeded for this event.
    """
    if is_registry_event(event):
        _apply_registry(conn, event)
    elif is_job_event(event):
        _apply_job(conn, event)


# ── Registry events ──────────────────────────────────────────────────

def _validate_registry(event: dict) -> dict:
    entity_type = event["entity_type"]
    try:
        payload = json.loads(event["payload_json"])
    except json.JSONDecodeError as e:
        raise ReplayError(f"payload_json is not valid JSON: {e}") from e

    key_field = _NATURAL_KEY_FIELD[entity_type]
    natural_key = event["entity_id"]
    if payload.get(key_field) and payload[key_field] != natural_key:
        raise ReplayError(
            f"entity_id ({natural_key!r}) does not match payload {key_field} "
            f"({payload[key_field]!r})")
    if not natural_key:
        raise ReplayError("entity_id (natural key) must not be empty")

    return payload


def _apply_registry(conn, event: dict) -> None:
    entity_type = event["entity_type"]
    payload = json.loads(event["payload_json"])
    key_field = _NATURAL_KEY_FIELD[entity_type]
    natural_key = event["entity_id"]

    fields = REGISTRY_FIELDS[entity_type]
    table = _TABLE[entity_type]
    row_id = f"{event['project_id']}:{natural_key}"

    values = {f: payload.get(f, "") for f in fields}
    values[key_field] = natural_key

    columns = ["id", "project_id"] + list(fields)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{f}=excluded.{f}" for f in fields)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
        [row_id, event["project_id"]] + [values[f] for f in fields],
    )


# ── Job / job_step events ────────────────────────────────────────────

def _validate_job(conn, event: dict) -> dict:
    entity_type, op, project_id = event["entity_type"], event["op"], event["project_id"]
    try:
        payload = json.loads(event["payload_json"])
    except json.JSONDecodeError as e:
        raise ReplayError(f"payload_json is not valid JSON: {e}") from e
    if not event.get("entity_id"):
        raise ReplayError("entity_id must not be empty")

    if entity_type == "job":
        job_type = payload.get("type")
        if job_type not in JOB_TYPE_SHORT:
            raise ReplayError(f"unknown job type: {job_type!r}")
        return payload

    # entity_type == "job_step"
    if op == "step_added":
        job_id = payload.get("job_id")
        if not job_id:
            raise ReplayError("step_added payload must include job_id")
        if _lookup_job(conn, project_id, job_id) is None:
            raise ReplayError(f"no job {job_id!r} in project {project_id!r}")
        if not payload.get("description"):
            raise ReplayError("step_added payload must include a description")
        return payload

    # step_assigned / step_completed both act on an existing step
    if _lookup_step(conn, project_id, event["entity_id"]) is None:
        raise ReplayError(f"no job_step {event['entity_id']!r} in project {project_id!r}")

    if op == "step_assigned":
        assignee = payload.get("assigned_to")
        if not assignee:
            raise ReplayError("step_assigned payload must include assigned_to")
        if not _user_exists(conn, assignee):
            raise ReplayError(f"unknown user for assigned_to: {assignee!r}")
        return payload

    return payload  # step_completed: no required fields


def _apply_job(conn, event: dict) -> None:
    entity_type, op = event["entity_type"], event["op"]
    payload = json.loads(event["payload_json"])
    project_id, entity_id = event["project_id"], event["entity_id"]
    actor_id = _resolve_signer_user_id(conn, event["signer_fingerprint"])
    when = event["server_time"]

    if entity_type == "job":
        conn.execute(
            "INSERT INTO jobs(id, project_id, type, data_json, created_by, created_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json",
            (entity_id, project_id, payload["type"], json.dumps(payload), actor_id, when),
        )
        return

    if op == "step_added":
        conn.execute(
            "INSERT INTO job_steps(id, job_id, description, status, added_by, added_at) "
            "VALUES (?,?,?,'pending',?,?) "
            "ON CONFLICT(id) DO UPDATE SET description=excluded.description",
            (entity_id, payload["job_id"], payload["description"], actor_id, when),
        )
    elif op == "step_assigned":
        conn.execute(
            "UPDATE job_steps SET assigned_to=? WHERE id=?",
            (payload["assigned_to"], entity_id),
        )
    elif op == "step_completed":
        conn.execute(
            "UPDATE job_steps SET status='complete', completed_by=?, completed_at=? WHERE id=?",
            (actor_id, when, entity_id),
        )
