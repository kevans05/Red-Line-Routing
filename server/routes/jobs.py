"""Read endpoints for jobs and job_steps (see server/replay.py).

Writes don't go through this router — a job is created and its steps are
added/assigned/completed by posting signed events (`job_created`,
`step_added`, `step_assigned`, `step_completed`) to
`POST /projects/{project_id}/events`, the same generic pipe every other
event flows through. This router only serves the replayed current-state
view for the UI to read.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from server import rbac

router = APIRouter(tags=["jobs"])

_VIEWER = Depends(rbac.require_role("viewer", project_id_param="project_id"))


@router.get("/projects/{project_id}/jobs", dependencies=[_VIEWER])
def list_jobs(project_id: str, request: Request):
    conn = request.app.state.db.conn()
    rows = conn.execute(
        "SELECT id, type, data_json, created_by, created_at FROM jobs "
        "WHERE project_id=? ORDER BY created_at",
        (project_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.pop("data_json"))
        result.append(d)
    return result


@router.get("/projects/{project_id}/jobs/{job_id}/steps", dependencies=[_VIEWER])
def list_job_steps(project_id: str, job_id: str, request: Request):
    conn = request.app.state.db.conn()
    job = conn.execute(
        "SELECT id FROM jobs WHERE id=? AND project_id=?", (job_id, project_id),
    ).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    rows = conn.execute(
        "SELECT id, description, status, added_by, added_at, assigned_to, "
        "completed_by, completed_at FROM job_steps WHERE job_id=? ORDER BY added_at",
        (job_id,),
    ).fetchall()
    return [dict(r) for r in rows]
