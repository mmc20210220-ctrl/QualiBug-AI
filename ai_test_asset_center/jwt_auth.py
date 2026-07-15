"""
Lightweight JWT auth for QualiBug — no external dependencies.
Uses HMAC-SHA256 for signing, stores tenant_id + role in payload.

Import is side-effect free. The secret is validated lazily only when a token
operation (create / verify / extract) is first invoked, so offline CLI tools
and evaluation commands can import the package without a JWT secret.
"""
from __future__ import annotations
import hashlib, hmac, json, time, os, base64
from typing import Any

_SECRET_KEY = os.environ.get("QUALIBUG_JWT_SECRET", "")
_TOKEN_TTL = int(os.environ.get("QUALIBUG_JWT_TTL", "86400"))  # 24h

# Lazily resolved on first use so that package import stays side-effect free.
_cached_secret: bytes | None = None


def _ensure_secret() -> bytes:
    """Return the HMAC secret bytes, failing fast only on first actual use."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    key = os.environ.get("QUALIBUG_JWT_SECRET", "")
    if not key:
        raise RuntimeError(
            "QUALIBUG_JWT_SECRET 环境变量未设置。\n"
            "生产部署必须设置此密钥，例如:\n"
            "  export QUALIBUG_JWT_SECRET=$(openssl rand -hex 32)\n"
            "本地开发可设置: export QUALIBUG_JWT_SECRET=dev-mode-only"
        )
    _cached_secret = key.encode()
    return _cached_secret


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64decode(data: str) -> bytes:
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)

def create_token(tenant_id: str, role: str = "admin", ttl: int = _TOKEN_TTL) -> str:
    """Create a signed JWT token."""
    secret = _ensure_secret()
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64encode(json.dumps({
        "sub": tenant_id,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
    }, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"

def verify_token(token: str) -> dict[str, Any] | None:
    """Verify JWT and return payload dict, or None if invalid/expired."""
    secret = _ensure_secret()
    try:
        parts = token.split(".")
        if len(parts) != 3: return None
        header_b64, payload_b64, sig_b64 = parts
        expected_sig = _b64encode(hmac.new(secret, f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig_b64, expected_sig):
            return None
        payload = json.loads(_b64decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def extract_tenant_from_request(headers: dict, root: Any = None) -> str:
    """
    Extract tenant ID from request headers.
    Priority: Bearer JWT > X-API-Key > default
    """
    # 1. Bearer token
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        payload = verify_token(token)
        if payload:
            return str(payload.get("sub", ""))

    # 2. API Key header
    api_key = headers.get("X-API-Key") or headers.get("x-api-key") or ""
    if api_key and root:
        from . import db_persistence as dbp
        tid = dbp.verify_api_key(root, api_key)
        if tid:
            return tid

    # 3. Dev mode fallback
    if os.environ.get("QUALIBUG_AUTH_BYPASS", "0") == "1":
        # Auth bypass must never be active when the service is exposed to a
        # public network. Refuse rather than silently grant unauthenticated access.
        if os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1":
            raise RuntimeError(
                "QUALIBUG_AUTH_BYPASS=1 is forbidden when QUALIBUG_ALLOW_PUBLIC_BIND=1 "
                "(auth bypass cannot be enabled on a publicly-bound service)"
            )
        return os.environ.get("QUALIBUG_TENANT", "default")

    return ""
