"""Signature + hash-chain verification for the append-only `events` log.

Chain scope is per (project_id, device_fingerprint): each device's events
form their own hash chain, tamper-evident independently of what other
devices are doing concurrently — this is what lets multiple offline
clients append to "the log" without needing to agree on a total order
first (see the architecture notes' "Every event is signed and
hash-chained" section for the reasoning).

Wire format: an event dict has exactly these fields, all of which
(except signature/hash/prev_hash/server_time/needs_review) are what gets
signed and hashed — see shared.crypto.canonical_event_bytes for the exact
exclusion list. Both client and server must build the *same* dict shape
before signing/verifying, or signatures will never match:

    id                  client-generated UUID (part of the signed content —
                        the server never invents or overrides this)
    project_id
    device_fingerprint  signer's key fingerprint, i.e. shared.crypto.fingerprint(pubkey)
    entity_type         e.g. "job", "job_step", "drawing"
    entity_id
    op                  e.g. "job_created", "step_added", "step_completed"
    payload_json        JSON-encoded string of the op's data (kept as a
                        string, not a nested dict, so its exact bytes are
                        part of what's signed/hashed unambiguously)
    client_time         ISO8601, set by the signing device
    signer_fingerprint  same as device_fingerprint today (one key per
                        device); kept as a separate field since a future
                        multi-key-per-device scheme could differ
    signature           hex-encoded 64-byte Ed25519 signature (added after
                        the fields above are finalized)
    hash                hex-encoded SHA256, added after signing
    prev_hash           hash of the previous event in this device's chain
                        for this project ("" for the first event)
"""

import binascii
import time

from shared import crypto

REQUIRED_FIELDS = (
    "id", "project_id", "device_fingerprint", "entity_type", "entity_id",
    "op", "payload_json", "client_time", "signer_fingerprint",
    "signature", "hash",
)


class ChainError(ValueError):
    """Raised when an incoming event fails signature or hash-chain verification."""


def _hex_to_bytes(s):
    try:
        return binascii.unhexlify(s)
    except (binascii.Error, TypeError) as e:
        raise ChainError(f"malformed hex field: {e}") from e


def _last_event_hash(db_conn, project_id, device_fingerprint):
    row = db_conn.execute(
        "SELECT hash FROM events WHERE project_id=? AND device_fingerprint=? "
        "ORDER BY server_time ASC, rowid ASC",
        (project_id, device_fingerprint),
    ).fetchall()
    return row[-1]["hash"] if row else ""


def _lookup_public_key(db_conn, fingerprint):
    row = db_conn.execute(
        "SELECT public_key, revoked_at FROM client_keys WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        raise ChainError(f"unknown signer fingerprint: {fingerprint}")
    if row["revoked_at"]:
        raise ChainError(f"signer key is revoked: {fingerprint}")
    return _hex_to_bytes(row["public_key"])


def verify_and_prepare(db_conn, event: dict) -> dict:
    """Verify an incoming event's signature and hash-chain linkage.

    Returns the event dict with `server_time` filled in, ready for INSERT
    as-is (including the client-assigned `id`). Raises ChainError on any
    verification failure — callers must not persist a failed event.
    A verification failure (bad signature, broken chain, unknown key) is
    a different thing from a genuine *content* conflict between two valid
    events, which is handled separately via `needs_review` at the
    entity-replay layer, not here.
    """
    missing = [k for k in REQUIRED_FIELDS if event.get(k) in (None, "") and k not in ("prev_hash",)]
    if missing:
        raise ChainError(f"event missing required fields: {missing}")

    public_key = _lookup_public_key(db_conn, event["signer_fingerprint"])
    signature = _hex_to_bytes(event["signature"])
    if not crypto.verify_event_signature(public_key, event, signature):
        raise ChainError("signature verification failed")

    expected_prev = _last_event_hash(db_conn, event["project_id"], event["device_fingerprint"])
    prev_hash = event.get("prev_hash") or ""
    if prev_hash != expected_prev:
        raise ChainError(
            f"prev_hash mismatch (out of order or forked chain): "
            f"expected {expected_prev!r}, got {prev_hash!r}")

    expected_hash = crypto.event_hash(event, prev_hash)
    if expected_hash != event["hash"]:
        raise ChainError("hash does not match event content")

    prepared = dict(event)
    prepared["server_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prepared["prev_hash"] = prev_hash
    prepared.setdefault("needs_review", 0)
    return prepared


def verify_chain_rows(rows) -> bool:
    """Verify a sequence of already-stored event rows (one device's chain,
    in server insertion order) is unbroken. Used by the "Verify Integrity"
    action and by tests — does not re-check signatures (those were checked
    at append time); this only confirms the hash chain itself wasn't
    altered after the fact (e.g. by direct DB edits).
    """
    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "project_id": row["project_id"],
            "device_fingerprint": row["device_fingerprint"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "op": row["op"],
            "payload_json": row["payload_json"],
            "client_time": row["client_time"],
            "signer_fingerprint": row["signer_fingerprint"],
            "signature": row["signature"],
            "hash": row["hash"],
            "prev_hash": row["prev_hash"],
        })
    return crypto.verify_chain(events)
