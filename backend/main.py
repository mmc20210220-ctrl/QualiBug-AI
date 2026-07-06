from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from core.engine import Engine

app = FastAPI(title="QualiBug V11 Production Final")
engine = Engine()


def _expected_api_token() -> str:
    return os.environ.get("QUALIBUG_API_TOKEN", "").strip()


def _extract_bearer_token(authorization: str | None) -> str:
    token = (authorization or "").strip()
    return token.split(" ", 1)[1].strip() if token.lower().startswith("bearer ") else token


def require_api_token(authorization: str | None = Header(None)) -> str:
    expected = _expected_api_token()
    token = _extract_bearer_token(authorization)
    if not expected:
        raise HTTPException(status_code=503, detail="api token is not configured")
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="authentication required")
    return token


def _component(status: str, *, reason: str = "", checked_at: str = "") -> dict[str, Any]:
    return {"status": status, "reason": reason, "checked_at": checked_at}


@app.get("/run")
def run(q: str, authorization: str = Header(None)) -> dict[str, Any]:
    return engine.run(q, require_api_token(authorization))


@app.get("/replay")
def replay(q: str, authorization: str = Header(None)) -> dict[str, Any]:
    return engine.replay(q, require_api_token(authorization))


@app.get("/metrics")
def metrics(authorization: str = Header(None)) -> dict[str, Any]:
    require_api_token(authorization)
    return engine.metrics


@app.get("/graph")
def graph(authorization: str = Header(None)) -> dict[str, Any]:
    tenant = engine.auth.verify(require_api_token(authorization))
    return engine.graph.get(tenant, {})


@app.get("/logs")
def logs(authorization: str = Header(None)) -> list[dict[str, Any]]:
    require_api_token(authorization)
    return engine.logs


@app.get("/health")
def health() -> dict[str, Any]:
    """Expose verified status, not optimistic configuration status.

    This lightweight API owns an in-memory compatibility engine and does not
    make provider or database network calls. Those integrations therefore stay
    ``configured_unverified`` or ``not_configured`` rather than ``healthy``.
    """
    auth_configured = bool(_expected_api_token())
    return {
        "status": "healthy",
        "version": engine.version,
        "components": {
            "api": _component("healthy"),
            "authentication": _component(
                "configured_unverified" if auth_configured else "not_configured",
                reason="token_presence_checked_only",
            ),
            "execution_engine": _component(
                "configured_unverified",
                reason="in_memory_simulation_has_no_external_target_health_check",
            ),
            "llm": _component("not_configured", reason="no_provider_health_check_registered"),
            "external_services": _component("not_configured", reason="no_external_service_health_check_registered"),
        },
        "auth_configured": auth_configured,
        "execution_source": "memory_simulation",
    }
