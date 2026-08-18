#!/usr/bin/env python3
"""Admin helper: create a pending user + enrollment code against a running server.

    python3 tools/create_pending_user.py --server http://127.0.0.1:8642 \\
        --token <admin session token> --email tech@example.com --role editor

Requires an existing admin session token (from the bootstrap admin printed
on first server startup, or another admin account). Prints the enrollment
code to hand to the new user.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://127.0.0.1:8642")
    p.add_argument("--token", required=True, help="admin session token")
    p.add_argument("--email", required=True)
    p.add_argument("--display-name", default="")
    p.add_argument("--role", default="viewer", choices=["admin", "editor", "approver", "viewer"])
    p.add_argument("--project-id", default=None, help="omit for a global role")
    args = p.parse_args()

    body = json.dumps({
        "email": args.email,
        "display_name": args.display_name,
        "role": args.role,
        "project_id": args.project_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{args.server}/admin/users", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.token}"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"Request failed ({e.code}): {e.read().decode('utf-8', 'replace')}", file=sys.stderr)
        raise SystemExit(1)

    print(f"user_id: {result['user_id']}")
    print(f"enrollment_code: {result['enrollment_code']}")


if __name__ == "__main__":
    main()
