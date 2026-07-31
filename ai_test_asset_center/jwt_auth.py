"""Dependency-free HMAC-SHA256 session tokens.

Tokens carry tenant identity, username, role and a server-side session version.
Signature verification alone is not authorization: request authentication also
compares these claims with the current tenant auth state in SQLite.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

_TOKEN_TTL = int(os.environ.get("QUALIBUG_JWT_TTL", "86400"))
_cached_secret: bytes | None = None


def _ensure_secret() -> bytes:
    global _cached_secret
    key = os.environ.get("QUALIBUG_JWT_SECRET", "").strip()
    if not key:
        raise RuntimeError("QUALIBUG_JWT_SECRET is required for token operations")
    encoded = key.encode("utf-8")
    if _cached_secret != encoded:
        _cached_secret = encoded
    return encoded


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = (-len(data)) % 4
    return base64.urlsafe_b64decode(data + ("=" * padding))


def create_token(
    tenant_id: str,
    role: str = "viewer",
    ttl: int = _TOKEN_TTL,
    *,
    username: str = "",
    session_version: int = 1,
) -> str:
    secret = _ensure_secret()
    now = int(time.time())
    header = _b64encode(
        json.dumps(
            {"alg": "HS256", "typ": "JWT"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    payload = _b64encode(
        json.dumps(
            {
                "sub": str(tenant_id),
                "username": str(username or tenant_id),
                "role": str(role or "viewer"),
                "ver": max(1, int(session_version or 1)),
                "iat": now,
                "exp": now + max(1, int(ttl)),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = _b64encode(
        hmac.new(
            secret,
            f"{header}.{payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
    )
    return f"{header}.{payload}.{signature}"


def verify_token(token: str) -> dict[str, Any] | None:
    try:
        parts = str(token or "").split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, signature_b64 = parts
        secret = _ensure_secret()
        expected = _b64encode(
            hmac.new(
                secret,
                f"{header_b64}.{payload_b64}".encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(signature_b64, expected):
            return None
        header = json.loads(_b64decode(header_b64))
        payload = json.loads(_b64decode(payload_b64))
        if not isinstance(header, dict) or header != {"alg": "HS256", "typ": "JWT"}:
            return None
        if not isinstance(payload, dict):
            return None
        now = time.time()
        issued_at = float(payload.get("iat") or 0)
        expires_at = float(payload.get("exp") or 0)
        if issued_at <= 0 or issued_at > now + 60 or expires_at <= now:
            return None
        if not str(payload.get("sub") or "").strip():
            return None
        if not str(payload.get("role") or "").strip():
            return None
        if int(payload.get("ver") or 0) < 1:
            return None
        return payload
    except Exception:
        return None


def extract_tenant_from_request(headers: dict, root: Any = None) -> str:
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if str(auth).startswith("Bearer "):
        payload = verify_token(str(auth)[7:].strip())
        if payload:
            return str(payload.get("sub") or "")
    api_key = headers.get("X-API-Key") or headers.get("x-api-key") or ""
    if api_key and root:
        from . import db_persistence as dbp

        return str(dbp.verify_api_key(root, str(api_key)) or "")
    return ""


__all__ = ["create_token", "extract_tenant_from_request", "verify_token"]
