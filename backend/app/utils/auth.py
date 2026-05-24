from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException

from app.config import get_settings


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _json_b64url(value: str) -> dict[str, Any]:
    try:
        return json.loads(_b64url_decode(value))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid auth token.") from exc


def verify_supabase_jwt(token: str) -> CurrentUser:
    secret = get_settings().supabase_jwt_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Supabase JWT verification is not configured.")
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid auth token.")
    header = _json_b64url(parts[0])
    if header.get("alg") != "HS256":
        raise HTTPException(status_code=401, detail="Unsupported auth token algorithm.")
    signed = f"{parts[0]}.{parts[1]}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
    try:
        provided = _b64url_decode(parts[2])
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid auth token signature.") from exc
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid auth token signature.")
    payload = _json_b64url(parts[1])
    if int(payload.get("exp") or 0) <= int(time.time()):
        raise HTTPException(status_code=401, detail="Auth token expired.")
    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Auth token is missing a user id.")
    email = payload.get("email")
    return CurrentUser(id=user_id, email=str(email) if email else None)


def current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in required.")
    return verify_supabase_jwt(authorization.split(" ", 1)[1].strip())
