"""Authentication simulator. Public persona selection is NOT a production login."""

import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.db import Database, utc_now


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class AuthContext:
    session_hash: str | None = None
    user_id: str | None = None

    def require_customer(self) -> str:
        if not self.user_id:
            raise HTTPException(401, "Select a demo customer to access private account information.")
        return self.user_id


def issue_session(db: Database, persona: str, ttl: int) -> dict:
    token = secrets.token_urlsafe(32)
    expires = time.time() + ttl
    with db.transaction(write=True) as conn:
        user = (
            None
            if persona == "guest"
            else conn.execute("SELECT id, name FROM users WHERE persona = ?", (persona,)).fetchone()
        )
        if persona != "guest" and not user:
            raise HTTPException(400, "Unknown demo persona.")
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (digest(token), user["id"] if user else None, expires, utc_now()),
        )
    return {
        "access_token": token,
        "token_type": "bearer",
        "mode": "demo",
        "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
        "customer": dict(user) if user else None,
    }


def get_context(request: Request) -> AuthContext:
    header = request.headers.get("Authorization")
    if header is None:
        return AuthContext()
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not 20 <= len(token) <= 200:
        raise HTTPException(401, "Invalid or expired session.")
    token_hash = digest(token)
    with request.app.state.db.transaction() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    if row is None or row["expires_at"] <= time.time():
        raise HTTPException(401, "Invalid or expired session.")
    return AuthContext(session_hash=token_hash, user_id=row["user_id"])


def require_reviewer(request: Request):
    expected = request.app.state.settings.reviewer_api_key
    provided = request.headers.get("X-Reviewer-Key", "")
    if not expected:
        raise HTTPException(503, "Reviewer access is disabled. Configure REVIEWER_API_KEY on the server.")
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(403, "Reviewer access required.")
