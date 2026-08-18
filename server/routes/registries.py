"""Read endpoints for the derived registry tables (see server/replay.py).

Writes don't go through this router at all — a registry entry is added or
edited by posting a signed `registry_add`/`registry_edit` event to
`POST /projects/{project_id}/events` (server/routes/events.py), the same
generic pipe every other event flows through. This router only serves the
fast, replayed current-state view for the UI to read.
"""

from fastapi import APIRouter, Depends, Request

from server import rbac

router = APIRouter(tags=["registries"])

_VIEWER = Depends(rbac.require_role("viewer", project_id_param="project_id"))


@router.get("/projects/{project_id}/drawings", dependencies=[_VIEWER])
def list_drawings(project_id: str, request: Request):
    conn = request.app.state.db.conn()
    rows = conn.execute(
        "SELECT name, title, rev, url, notes FROM drawings WHERE project_id=? ORDER BY name",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/projects/{project_id}/maintenance-standards", dependencies=[_VIEWER])
def list_maintenance_standards(project_id: str, request: Request):
    conn = request.app.state.db.conn()
    rows = conn.execute(
        "SELECT standard_id, title, revision, url_telecom, url_transmission, notes "
        "FROM maintenance_standards WHERE project_id=? ORDER BY standard_id",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/projects/{project_id}/engineering-standards", dependencies=[_VIEWER])
def list_engineering_standards(project_id: str, request: Request):
    conn = request.app.state.db.conn()
    rows = conn.execute(
        "SELECT standard_id, title, revision, standard_type, url, notes "
        "FROM engineering_standards WHERE project_id=? ORDER BY standard_id",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/projects/{project_id}/tailboards", dependencies=[_VIEWER])
def list_tailboard_refs(project_id: str, request: Request):
    conn = request.app.state.db.conn()
    rows = conn.execute(
        "SELECT kind, url FROM tailboard_refs WHERE project_id=? ORDER BY kind",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]
