"""
Authentication — Google OAuth verification + JWT issuance.
Only allows whitelisted emails (comma-separated ALLOWED_EMAIL in .env).
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt

from config import settings

logger = logging.getLogger("broker.auth")

ALGORITHM = "HS256"
security = HTTPBearer(auto_error=False)


def verify_google_token(token: str) -> dict:
    """Verify a Google id_token and return its payload."""
    try:
        payload = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            settings.google_client_id,
        )
        return payload
    except ValueError as e:
        logger.warning("Invalid Google token: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")


def create_jwt(email: str) -> str:
    """Create a signed JWT valid for jwt_expiration_days days."""
    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_expiration_days)
    payload = {
        "sub": email,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def _allowed_emails() -> list[str]:
    return [e.strip() for e in settings.allowed_email.split(",") if e.strip()]


def verify_jwt(token: str) -> dict:
    """Verify and decode a JWT. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        email: str = payload.get("sub", "")
        if email not in _allowed_emails():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return payload
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """FastAPI dependency — protects REST routes with JWT auth."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")
    return verify_jwt(credentials.credentials)


def verify_ws_token(token: str | None) -> bool:
    """Verify a JWT for WebSocket connections (returns bool, no exception)."""
    if not token:
        return False
    try:
        verify_jwt(token)
        return True
    except HTTPException:
        return False
