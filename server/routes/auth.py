"""HTTP surface for enrollment and challenge/response login."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from server import auth

router = APIRouter(prefix="/auth", tags=["auth"])


class EnrollRequest(BaseModel):
    code: str
    public_key_hex: str
    label: str = ""


class VerifyRequest(BaseModel):
    nonce: str
    fingerprint: str
    signature_hex: str


@router.post("/enroll")
def enroll(body: EnrollRequest, request: Request):
    conn = request.app.state.db.conn()
    try:
        user_id, fp = auth.enroll(conn, body.code, body.public_key_hex, body.label)
    except auth.AuthError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"user_id": user_id, "fingerprint": fp}


@router.get("/challenge")
def challenge(request: Request):
    conn = request.app.state.db.conn()
    nonce = auth.issue_challenge(conn)
    return {"nonce": nonce}


@router.post("/verify")
def verify(body: VerifyRequest, request: Request):
    conn = request.app.state.db.conn()
    try:
        token = auth.verify_challenge(conn, body.nonce, body.fingerprint, body.signature_hex)
    except auth.AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    user_id = auth.resolve_session(conn, token)
    return {"token": token, "user_id": user_id}
