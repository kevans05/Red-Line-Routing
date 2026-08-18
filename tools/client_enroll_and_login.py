#!/usr/bin/env python3
"""Reference client: generate a keypair, enroll with a code, and log in.

    python3 tools/client_enroll_and_login.py --server http://127.0.0.1:8642 \\
        --code <enrollment code> --identity-file ~/.redlinerouting/identity

This is the same enrollment + challenge/response sequence client/main.py
will use once the local client process is built (Phase 4) — this script
is the minimal reference implementation, useful on its own for scripting
and for verifying a server deployment end-to-end without a browser.

The private key never leaves this machine: it's generated locally, stored
in --identity-file (created with mode 0600), and only signatures are sent
over the wire.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import crypto  # noqa: E402


def _post(server, path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{server}{path}", data=json.dumps(body).encode("utf-8"), method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"{path} failed ({e.code}): {e.read().decode('utf-8', 'replace')}", file=sys.stderr)
        raise SystemExit(1)


def _get(server, path):
    with urllib.request.urlopen(f"{server}{path}") as resp:
        return json.load(resp)


def load_or_create_identity(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bytes.fromhex(data["private_key_hex"]), bytes.fromhex(data["public_key_hex"])

    private_key, public_key = crypto.generate_keypair()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"private_key_hex": private_key.hex(), "public_key_hex": public_key.hex()}, f)
    os.chmod(path, 0o600)
    return private_key, public_key


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://127.0.0.1:8642")
    p.add_argument("--code", required=True, help="enrollment code from an admin")
    p.add_argument("--identity-file", default=os.path.expanduser("~/.redlinerouting/identity"))
    p.add_argument("--label", default="")
    args = p.parse_args()

    private_key, public_key = load_or_create_identity(args.identity_file)
    print(f"local identity: {args.identity_file} (fingerprint {crypto.fingerprint(public_key)})")

    enroll_result = _post(args.server, "/auth/enroll", {
        "code": args.code, "public_key_hex": public_key.hex(), "label": args.label,
    })
    print(f"enrolled: user_id={enroll_result['user_id']} fingerprint={enroll_result['fingerprint']}")

    challenge = _get(args.server, "/auth/challenge")
    nonce = challenge["nonce"]
    signature = crypto.sign(private_key, nonce.encode("utf-8"))
    verify_result = _post(args.server, "/auth/verify", {
        "nonce": nonce,
        "fingerprint": enroll_result["fingerprint"],
        "signature_hex": signature.hex(),
    })
    print(f"logged in: session token = {verify_result['token']}")


if __name__ == "__main__":
    main()
