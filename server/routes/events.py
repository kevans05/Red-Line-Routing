"""Demo event append (REST) + real-time broadcast (WebSocket).

Phase 1 only proves the mechanism generically — Phase 2/3 add the actual
job/registry mutations as typed events flowing through this same pipe.
`POST /projects/{project_id}/events` accepts any pre-signed, pre-hashed
event (the client does all crypto locally, per shared/crypto.py and its JS
port), verifies it via server/chain.py, stores it, and broadcasts it to
any other browser tabs/devices connected to that project's websocket.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from server import auth, chain, rbac, replay

router = APIRouter(tags=["events"])


class EventIn(BaseModel):
    id: str
    project_id: str
    device_fingerprint: str
    entity_type: str
    entity_id: str
    op: str
    payload_json: str
    client_time: str
    signer_fingerprint: str
    signature: str
    hash: str
    prev_hash: str = ""


@router.post("/projects/{project_id}/events",
             dependencies=[Depends(rbac.require_role("editor", project_id_param="project_id"))])
async def post_event(project_id: str, body: EventIn, request: Request):
    if body.project_id != project_id:
        raise HTTPException(status_code=400, detail="project_id mismatch between path and body")
    conn = request.app.state.db.conn()
    try:
        prepared = chain.verify_and_prepare(conn, body.model_dump())
    except chain.ChainError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        replay.validate_payload(prepared)
    except replay.ReplayError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    conn.execute(
        "INSERT INTO events(id, project_id, device_fingerprint, entity_type, entity_id, op, "
        "payload_json, client_time, server_time, prev_event_id, needs_review, signature, "
        "signer_fingerprint, hash, prev_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (prepared["id"], prepared["project_id"], prepared["device_fingerprint"],
         prepared["entity_type"], prepared["entity_id"], prepared["op"],
         prepared["payload_json"], prepared["client_time"], prepared["server_time"],
         None, prepared["needs_review"], prepared["signature"],
         prepared["signer_fingerprint"], prepared["hash"], prepared["prev_hash"]),
    )

    replay.apply_event(conn, prepared)

    hub = request.app.state.realtime_hub
    await hub.broadcast_event(project_id, prepared)
    return prepared


@router.get("/projects/{project_id}/events",
            dependencies=[Depends(rbac.require_role("viewer", project_id_param="project_id"))])
def list_events(project_id: str, request: Request):
    conn = request.app.state.db.conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE project_id=? ORDER BY server_time ASC, rowid ASC",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.websocket("/ws/events/{project_id}")
async def events_ws(websocket: WebSocket, project_id: str):
    token = websocket.query_params.get("token", "")
    conn = websocket.app.state.db.conn()
    user_id = auth.resolve_session(conn, token)
    if user_id is None or not rbac.has_role(conn, user_id, "viewer", project_id):
        await websocket.close(code=4401)
        return

    hub = websocket.app.state.realtime_hub
    await hub.connect(project_id, websocket)
    try:
        while True:
            # Clients don't send anything over this socket today; just keep
            # the connection open and drain any pings the browser sends.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(project_id, websocket)
