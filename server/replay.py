"""Replay accepted events onto the derived, fast-to-query registry tables.

The `events` log is the source of truth (see server/chain.py); the
drawings/maintenance_standards/engineering_standards/tailboard_refs tables
are just a materialized view of it, rebuilt incrementally as each event is
accepted. Each registry entry's natural key (drawing name, standard_id, or
tailboard kind — the same keys wire_planner.py's dict registries already
use) combines with project_id for the derived row's primary key, so
replaying the same event twice is a harmless upsert.

Phase 2 keeps replay conflict-free by construction: last-applied-wins per
entity. Real concurrent-edit conflict detection (flagging `needs_review`
for two genuinely racing edits from different offline devices) is Phase 4
work, once there's more than one device actually able to go offline.
"""

import json

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


class ReplayError(ValueError):
    pass


def is_registry_event(event: dict) -> bool:
    return event.get("entity_type") in REGISTRY_ENTITY_TYPES and event.get("op") in REGISTRY_OPS


def validate_payload(event: dict) -> dict:
    """Parse and sanity-check a registry event's payload *before* the event
    is appended to the immutable log — a malformed payload should never be
    allowed to land in the log in the first place. Returns the parsed
    payload dict. No-op (returns {}) for non-registry event types.

    Call this before INSERT-ing into `events`; call `apply_event` after,
    once the event row is committed.
    """
    if not is_registry_event(event):
        return {}

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


def apply_event(conn, event: dict) -> None:
    """Apply one already-validated, already-committed event to its derived
    table. No-op for event types this function doesn't know about (e.g.
    future job/job_step events). Assumes `validate_payload` already
    succeeded for this event — call that first, before the event is
    inserted into the log.
    """
    if not is_registry_event(event):
        return

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
