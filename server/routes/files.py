"""Client-pushed file upload/list/download with revision history.

Upload is deliberately plain multipart (owner_type/owner_id identify what
the file belongs to — a drawing, a standard, a tailboard, a job, a form —
matching the project's subfolder-per-registry convention) rather than
flowing through the signed event log: file bytes can be large and don't
need per-byte signing, only the *fact* that a revision N of this file
exists needs an audit trail, which the `uploaded_by`/`uploaded_at` columns
already give it. A future pass can additionally emit a small
`file_uploaded` event (content_hash only, not the bytes) if file pushes
need to flow through the same real-time/offline-sync pipe as other
changes — not needed for Phase 2's scope.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from server import rbac

router = APIRouter(tags=["files"])


@router.post("/projects/{project_id}/files",
             dependencies=[Depends(rbac.require_role("editor", project_id_param="project_id"))])
async def upload_file(project_id: str, request: Request, file: UploadFile,
                       owner_type: str = Form(...), owner_id: str = Form(...),
                       user_id: str = Depends(rbac.get_current_user_id)):
    import time
    import uuid

    content = await file.read()
    storage = request.app.state.file_storage
    content_hash, storage_path = storage.save_blob(content)

    conn = request.app.state.db.conn()
    prior = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) AS max_rev FROM files "
        "WHERE project_id=? AND owner_type=? AND owner_id=? AND filename=?",
        (project_id, owner_type, owner_id, file.filename),
    ).fetchone()
    revision = prior["max_rev"] + 1

    file_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO files(id, project_id, owner_type, owner_id, filename, storage_path, "
        "content_hash, revision, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (file_id, project_id, owner_type, owner_id, file.filename, storage_path,
         content_hash, revision, user_id, time.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    return {"id": file_id, "filename": file.filename, "revision": revision,
            "content_hash": content_hash}


@router.get("/projects/{project_id}/files",
            dependencies=[Depends(rbac.require_role("viewer", project_id_param="project_id"))])
def list_files(project_id: str, request: Request,
               owner_type: str | None = None, owner_id: str | None = None):
    conn = request.app.state.db.conn()
    query = ("SELECT id, owner_type, owner_id, filename, content_hash, revision, "
              "uploaded_by, uploaded_at FROM files WHERE project_id=?")
    params = [project_id]
    if owner_type:
        query += " AND owner_type=?"
        params.append(owner_type)
    if owner_id:
        query += " AND owner_id=?"
        params.append(owner_id)
    query += " ORDER BY filename, revision DESC"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/projects/{project_id}/files/{file_id}/download",
            dependencies=[Depends(rbac.require_role("viewer", project_id_param="project_id"))])
def download_file(project_id: str, file_id: str, request: Request):
    conn = request.app.state.db.conn()
    row = conn.execute(
        "SELECT * FROM files WHERE id=? AND project_id=?", (file_id, project_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    storage = request.app.state.file_storage
    content = storage.read_blob(row["storage_path"])
    return Response(
        content=content, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )
