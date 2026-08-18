"""Role-based access control: role storage/lookup plus FastAPI dependencies.

Roles are a small fixed set (shared.models.ROLES) rather than a dynamic
permission system, matching the scale of this tool. A role can be global
(project_id NULL) or scoped to one project; `has_role` checks both and
treats roles as cumulative in privilege via shared.models.ROLE_RANK, so
"requires editor" is satisfied by an editor OR an admin.
"""

from fastapi import Depends, Header, HTTPException, Request

from server import auth
from shared.models import ROLE_RANK, ROLES


class RBACError(ValueError):
    pass


def grant_role(conn, user_id, role, project_id=None):
    if role not in ROLES:
        raise RBACError(f"unknown role: {role}")
    conn.execute(
        "INSERT OR IGNORE INTO user_roles(user_id, role, project_id) VALUES (?,?,?)",
        (user_id, role, project_id),
    )


def revoke_role(conn, user_id, role, project_id=None):
    conn.execute(
        "DELETE FROM user_roles WHERE user_id=? AND role=? AND project_id IS ?",
        (user_id, role, project_id),
    )


def user_roles(conn, user_id):
    """Return [(role, project_id_or_None), ...] for a user."""
    rows = conn.execute(
        "SELECT role, project_id FROM user_roles WHERE user_id=?", (user_id,)
    ).fetchall()
    return [(r["role"], r["project_id"]) for r in rows]


def best_role(conn, user_id, project_id=None):
    """Highest-ranked role the user holds that applies to `project_id`
    (a global role always applies; a project-scoped role only applies to
    that same project_id). Returns None if the user holds no applicable role.
    """
    best = None
    for role, scoped_project in user_roles(conn, user_id):
        if scoped_project is not None and scoped_project != project_id:
            continue
        if best is None or ROLE_RANK[role] > ROLE_RANK[best]:
            best = role
    return best


def has_role(conn, user_id, min_role, project_id=None):
    role = best_role(conn, user_id, project_id)
    if role is None:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[min_role]


# ── FastAPI wiring ───────────────────────────────────────────────────
# `app.state.db` (a server.db.Database) is expected to be set by server/main.py.

def get_current_user_id(request: Request, authorization: str = Header(default="")):
    token = authorization[len("Bearer "):] if authorization.startswith("Bearer ") else authorization
    conn = request.app.state.db.conn()
    user_id = auth.resolve_session(conn, token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="missing or invalid session token")
    return user_id


def require_role(min_role, project_id_param=None):
    """FastAPI dependency factory: `Depends(require_role("editor"))`.

    If `project_id_param` is given, the dependency reads that name from
    the request's path/query params to check a project-scoped role;
    otherwise only global roles are checked.
    """
    def _dependency(request: Request, user_id: str = Depends(get_current_user_id)):
        project_id = None
        if project_id_param:
            project_id = request.path_params.get(project_id_param) or \
                request.query_params.get(project_id_param)
        conn = request.app.state.db.conn()
        if not has_role(conn, user_id, min_role, project_id):
            raise HTTPException(status_code=403, detail=f"requires role: {min_role}+")
        return user_id
    return _dependency
