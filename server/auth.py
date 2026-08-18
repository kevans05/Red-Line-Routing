"""Keypair enrollment + SSH-style challenge/response authentication.

Two flows, both operating on a `sqlite3.Connection` from db.Database.conn()
so callers (routes/auth.py) control transaction boundaries:

1. Enrollment (admin-issued, like `ssh-copy-id` but token-mediated):
   `create_pending_user()` creates a user row + a one-time enrollment code.
   The client generates its own Ed25519 keypair locally and calls
   `enroll()` with its public key + the code to register that key.
2. Per-request auth (like SSH pubkey auth): `issue_challenge()` hands out a
   nonce, the client signs it with its private key, `verify_challenge()`
   checks the signature against a registered public key and issues a
   session token.

No private key ever crosses this boundary — only public keys and
signatures do.
"""

import json
import secrets
import time
import uuid

from shared import crypto

ENROLLMENT_CODE_TTL_SECONDS = 3600       # 1 hour to redeem an enrollment code
CHALLENGE_TTL_SECONDS = 120              # 2 minutes to answer a login challenge
SESSION_TTL_SECONDS = 12 * 3600          # 12 hour session


class AuthError(ValueError):
    """Raised for any enrollment/authentication failure."""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _expires_at(seconds_from_now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds_from_now))


def _is_expired(expires_at_str):
    return time.strptime(expires_at_str, "%Y-%m-%dT%H:%M:%SZ") < time.gmtime()


# ── Enrollment ───────────────────────────────────────────────────────

def create_pending_user(conn, email, display_name="", roles=None):
    """Admin action: create (or reuse) a user record and issue a one-time
    enrollment code for them to register a device's public key.

    `roles` is a list of {"role": ..., "project_id": ... | None} dicts,
    applied to the user as soon as enrollment completes.
    """
    roles = roles or []
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row is None:
        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users(id, email, display_name, created_at) VALUES (?,?,?,?)",
            (user_id, email, display_name, _now()),
        )
    else:
        user_id = row["id"]

    code = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO enrollment_codes(code, email, display_name, roles_json, expires_at) "
        "VALUES (?,?,?,?,?)",
        (code, email, display_name, json.dumps(roles), _expires_at(ENROLLMENT_CODE_TTL_SECONDS)),
    )
    return user_id, code


def enroll(conn, code, public_key_hex, label=""):
    """Client action: redeem a one-time enrollment code by registering a
    public key. Returns (user_id, fingerprint).
    """
    row = conn.execute(
        "SELECT * FROM enrollment_codes WHERE code=?", (code,)
    ).fetchone()
    if row is None:
        raise AuthError("unknown enrollment code")
    if row["used_at"]:
        raise AuthError("enrollment code already used")
    if _is_expired(row["expires_at"]):
        raise AuthError("enrollment code expired")

    try:
        public_key = bytes.fromhex(public_key_hex)
    except ValueError as e:
        raise AuthError(f"malformed public key: {e}") from e
    if len(public_key) != 32:
        raise AuthError("public key must be 32 bytes (Ed25519)")

    user_row = conn.execute("SELECT id FROM users WHERE email=?", (row["email"],)).fetchone()
    if user_row is None:
        raise AuthError("enrollment code has no matching user record")
    user_id = user_row["id"]

    fp = crypto.fingerprint(public_key)
    existing = conn.execute(
        "SELECT id FROM client_keys WHERE public_key=?", (public_key_hex,)
    ).fetchone()
    if existing is not None:
        raise AuthError("this public key is already registered")

    conn.execute(
        "INSERT INTO client_keys(id, user_id, public_key, fingerprint, label, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, public_key_hex, fp, label, _now()),
    )
    conn.execute("UPDATE enrollment_codes SET used_at=? WHERE code=?", (_now(), code))

    for r in json.loads(row["roles_json"] or "[]"):
        conn.execute(
            "INSERT OR IGNORE INTO user_roles(user_id, role, project_id) VALUES (?,?,?)",
            (user_id, r["role"], r.get("project_id")),
        )

    return user_id, fp


def revoke_key(conn, fingerprint):
    conn.execute(
        "UPDATE client_keys SET revoked_at=? WHERE fingerprint=? AND revoked_at IS NULL",
        (_now(), fingerprint),
    )


# ── Challenge/response login ────────────────────────────────────────

def issue_challenge(conn):
    nonce = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO challenges(nonce, created_at, expires_at) VALUES (?,?,?)",
        (nonce, _now(), _expires_at(CHALLENGE_TTL_SECONDS)),
    )
    return nonce


def verify_challenge(conn, nonce, signer_fingerprint, signature_hex):
    """Verify a signed challenge nonce and issue a session token.

    The signed message is the raw nonce string's UTF-8 bytes — simple and
    unambiguous, unlike an event (which has many fields to canonicalize).
    """
    row = conn.execute("SELECT * FROM challenges WHERE nonce=?", (nonce,)).fetchone()
    if row is None:
        raise AuthError("unknown challenge nonce")
    if row["used_at"]:
        raise AuthError("challenge already used")
    if _is_expired(row["expires_at"]):
        raise AuthError("challenge expired")

    key_row = conn.execute(
        "SELECT * FROM client_keys WHERE fingerprint=?", (signer_fingerprint,)
    ).fetchone()
    if key_row is None:
        raise AuthError("unknown signer fingerprint")
    if key_row["revoked_at"]:
        raise AuthError("key has been revoked")

    try:
        public_key = bytes.fromhex(key_row["public_key"])
        signature = bytes.fromhex(signature_hex)
    except ValueError as e:
        raise AuthError(f"malformed hex field: {e}") from e

    if not crypto.verify(public_key, nonce.encode("utf-8"), signature):
        raise AuthError("signature verification failed")

    conn.execute("UPDATE challenges SET used_at=? WHERE nonce=?", (_now(), nonce))

    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token, user_id, key_fingerprint, created_at, expires_at) "
        "VALUES (?,?,?,?,?)",
        (token, key_row["user_id"], signer_fingerprint, _now(), _expires_at(SESSION_TTL_SECONDS)),
    )
    return token


def resolve_session(conn, token):
    """Return the user_id for a valid, unexpired session token, or None."""
    if not token:
        return None
    row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if row is None:
        return None
    if _is_expired(row["expires_at"]):
        return None
    return row["user_id"]
