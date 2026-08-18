"""Admin-only user/project management. Requires the `admin` role."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from server import auth, rbac

router = APIRouter(tags=["admin"])


class CreateUserRequest(BaseModel):
    email: str
    display_name: str = ""
    role: str = "viewer"
    project_id: str | None = None


@router.post("/admin/users", dependencies=[Depends(rbac.require_role("admin"))])
def create_pending_user(body: CreateUserRequest, request: Request):
    conn = request.app.state.db.conn()
    user_id, code = auth.create_pending_user(
        conn, body.email, body.display_name,
        roles=[{"role": body.role, "project_id": body.project_id}],
    )
    return {"user_id": user_id, "enrollment_code": code}


class CreateProjectRequest(BaseModel):
    name: str
    site: str = ""


@router.post("/admin/projects", dependencies=[Depends(rbac.require_role("editor"))])
def create_project(body: CreateProjectRequest, request: Request,
                    user_id: str = Depends(rbac.get_current_user_id)):
    import time
    import uuid
    conn = request.app.state.db.conn()
    project_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects(id, name, site, created_by, created_at) VALUES (?,?,?,?,?)",
        (project_id, body.name, body.site, user_id, time.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    return {"project_id": project_id}


@router.get("/admin/projects")
def list_projects(request: Request, user_id: str = Depends(rbac.get_current_user_id)):
    conn = request.app.state.db.conn()
    rows = conn.execute("SELECT id, name, site FROM projects ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]
