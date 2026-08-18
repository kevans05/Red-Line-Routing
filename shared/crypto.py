"""Ed25519 signing + hash-chain primitives shared by server/ and client/.

Deliberately pure Python (RFC 8032 reference algorithm), no `cryptography`/
`pynacl` dependency: this project has already hit a broken Rust extension
in the `cryptography` package on its target machines once (see the pypdf
crypt-provider patch documented in CLAUDE.md), and identity/audit signing
is exactly the piece that must not silently fail to import. Pure Python is
slower per-operation but signing/verifying a job-step event is nowhere
near a hot loop.
"""

import hashlib
import json
import os

_B = 256
_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493


def _h(m):
    return hashlib.sha512(m).digest()


def _expmod(b, e, m):
    if e == 0:
        return 1
    t = _expmod(b, e // 2, m) ** 2 % m
    if e & 1:
        t = (t * b) % m
    return t


def _inv(x):
    return _expmod(x, _Q - 2, _Q)


_D = -121665 * _inv(121666) % _Q
_I = _expmod(2, (_Q - 1) // 4, _Q)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = _expmod(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


_BY = 4 * _inv(5)
_BX = _xrecover(_BY)
_BASE = (_BX % _Q, _BY % _Q)


def _edwards(p, q):
    x1, y1 = p
    x2, y2 = q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _D * x1 * x2 * y1 * y2)
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _D * x1 * x2 * y1 * y2)
    return (x3 % _Q, y3 % _Q)


def _scalarmult(p, e):
    if e == 0:
        return (0, 1)
    q = _scalarmult(p, e // 2)
    q = _edwards(q, q)
    if e & 1:
        q = _edwards(q, p)
    return q


def _encodeint(y):
    bits = [(y >> i) & 1 for i in range(_B)]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_B // 8))


def _encodepoint(p):
    x, y = p
    bits = [(y >> i) & 1 for i in range(_B - 1)] + [x & 1]
    return bytes(sum(bits[i * 8 + j] << j for j in range(8)) for i in range(_B // 8))


def _bit(h, i):
    return (h[i // 8] >> (i % 8)) & 1


def _decodeint(s):
    return sum(2 ** i * _bit(s, i) for i in range(_B))


def _decodepoint(s):
    y = sum(2 ** i * _bit(s, i) for i in range(_B - 1))
    x = _xrecover(y)
    if x & 1 != _bit(s, _B - 1):
        x = _Q - x
    p = (x, y)
    if not _isoncurve(p):
        raise ValueError("decoding point that is not on curve")
    return p


def _isoncurve(p):
    x, y = p
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _Q == 0


def _hint(m):
    h = _h(m)
    return sum(2 ** i * _bit(h, i) for i in range(2 * _B))


def _clamp_a(sk):
    h = _h(sk)
    a = 2 ** (_B - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _B - 2))
    return h, a


def _public_from_seed(seed):
    _, a = _clamp_a(seed)
    return _encodepoint(_scalarmult(_BASE, a))


# ── Public keypair API ──────────────────────────────────────────────

def generate_keypair():
    """Return (private_key_bytes, public_key_bytes), 32 bytes each.

    private_key_bytes is the raw 32-byte seed (as in RFC 8032) — this is
    what must be kept secret and never leaves the client.
    """
    seed = os.urandom(32)
    return seed, _public_from_seed(seed)


def public_key_from_private(private_key: bytes) -> bytes:
    return _public_from_seed(private_key)


def sign(private_key: bytes, message: bytes) -> bytes:
    """Sign message with a 32-byte Ed25519 private seed. Returns a 64-byte signature."""
    pk = _public_from_seed(private_key)
    h, a = _clamp_a(private_key)
    r = _hint(h[_B // 8:_B // 4] + message)
    r_point = _scalarmult(_BASE, r)
    r_enc = _encodepoint(r_point)
    s = (r + _hint(r_enc + pk + message) * a) % _L
    return r_enc + _encodeint(s)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return True iff signature is a valid Ed25519 signature of message by public_key."""
    if len(signature) != _B // 4 or len(public_key) != _B // 8:
        return False
    try:
        r_point = _decodepoint(signature[:_B // 8])
        a_point = _decodepoint(public_key)
    except (ValueError, Exception):
        return False
    s = _decodeint(signature[_B // 8:_B // 4])
    if s >= _L:
        return False
    h = _hint(_encodepoint(r_point) + public_key + message)
    left = _scalarmult(_BASE, s)
    right = _edwards(r_point, _scalarmult(a_point, h))
    return left == right


def fingerprint(public_key: bytes) -> str:
    """Short stable identifier for a public key, analogous to an SSH key fingerprint."""
    return hashlib.sha256(public_key).hexdigest()[:16]


# ── Canonical serialization + hash-chain primitives ─────────────────

def canonical_event_bytes(event: dict) -> bytes:
    """Deterministic byte encoding of an event's signable content.

    Only fields that are part of the event's meaning are included —
    `signature`, `hash`, and `prev_hash` themselves are excluded so
    signing/hashing never depends on their own output.
    """
    signable = {k: v for k, v in event.items()
                if k not in ("signature", "hash", "prev_hash", "server_time", "needs_review")}
    return json.dumps(signable, sort_keys=True, separators=(",", ":")).encode("utf-8")


def event_hash(event: dict, prev_hash: str) -> str:
    payload = (prev_hash or "").encode("utf-8") + canonical_event_bytes(event)
    return hashlib.sha256(payload).hexdigest()


def sign_event(private_key: bytes, event: dict) -> bytes:
    return sign(private_key, canonical_event_bytes(event))


def verify_event_signature(public_key: bytes, event: dict, signature: bytes) -> bool:
    return verify(public_key, canonical_event_bytes(event), signature)


def verify_chain(events: list) -> bool:
    """Walk an ordered list of event dicts (each carrying 'hash' and 'prev_hash')
    and confirm the hash chain is intact — i.e. no event was altered, reordered,
    or removed after the fact. Does NOT check signatures; use
    verify_event_signature per event for that (signature verification needs the
    signer's public key, which the caller looks up per event's signer_fingerprint).
    """
    prev = ""
    for event in events:
        expected = event_hash(event, prev)
        if expected != event.get("hash"):
            return False
        prev = event["hash"]
    return True
