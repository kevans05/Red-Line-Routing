"""FastAPI app entry point.

Run with `python3 -m server.main --mode server`, or for development with
autoreload via uvicorn directly: `uvicorn server.main:create_app --factory`.

Phase 1 scope: this only stands up the server side of the architecture
(headless service). The `client`/`both` launch modes come with the client
local-process work in a later phase — see /root's architecture plan and
CLAUDE.md-adjacent docs for the full roadmap.
"""

import argparse
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server import auth
from server.db import Database
from server.realtime import RealtimeHub
from server.routes import admin, auth as auth_routes, events, files, registries
from server.storage import FileStorage

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def create_app(db_path=None, storage_dir=None):
    app = FastAPI(title="Red-Line-Routing Server")
    app.state.db = Database(db_path)
    app.state.realtime_hub = RealtimeHub()
    app.state.file_storage = FileStorage(storage_dir)

    app.include_router(auth_routes.router)
    app.include_router(admin.router)
    app.include_router(events.router)
    app.include_router(registries.router)
    app.include_router(files.router)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    _bootstrap_admin_if_needed(app.state.db)
    return app


def _bootstrap_admin_if_needed(db):
    """First-run onboarding: if there are no users yet, mint an admin
    enrollment code and print it, the same idea as Jupyter's first-launch
    token — solves the chicken-and-egg problem of every other user/role
    being created via an admin-role-gated route.
    """
    conn = db.conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if count:
        return
    email = os.environ.get("REDLINE_BOOTSTRAP_ADMIN_EMAIL", "admin@localhost")
    _user_id, code = auth.create_pending_user(
        conn, email, "Administrator", roles=[{"role": "admin", "project_id": None}],
    )
    print("=" * 72)
    print("No users exist yet. Bootstrap admin enrollment code (one hour to use):")
    print(f"  email: {email}")
    print(f"  code:  {code}")
    print("Open the server in a browser and use this code to enroll the first device.")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="Red-Line-Routing server")
    parser.add_argument("--mode", choices=["server", "client", "both"], default="server",
                         help="Phase 1 only implements 'server'.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--storage-dir", default=None)
    args = parser.parse_args()

    if args.mode != "server":
        raise SystemExit(
            f"--mode {args.mode} is not implemented yet (Phase 1 only builds the server). "
            "See the architecture roadmap for client/both.")

    import uvicorn
    uvicorn.run(create_app(args.db_path, args.storage_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
