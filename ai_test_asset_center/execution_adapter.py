"""Unified execution adapter capability declarations."""
from __future__ import annotations

from typing import Any, Protocol


class ExecutionAdapter(Protocol):
    name: str

    def capabilities(self) -> dict[str, Any]:
        ...


def http_adapter_capabilities() -> dict[str, Any]:
    return {
        "adapter": "http_api",
        "read": True,
        "write": True,
        "required_credentials": ["actor_secret_ref"],
        "receipts": ["request", "response", "timing"],
        "cleanup_semantics": "governed_sandbox_reverse_order",
        "available": True,
    }


def ui_adapter_capabilities(*, available: bool = False) -> dict[str, Any]:
    return {
        "adapter": "ui_browser",
        "read": True,
        "write": True,
        "required_credentials": ["ui_session_secret_ref"],
        "receipts": ["screenshot", "dom", "network"],
        "cleanup_semantics": "session_reset",
        "available": bool(available),
    }


def db_adapter_capabilities(*, available: bool = False) -> dict[str, Any]:
    return {
        "adapter": "db_snapshot",
        "read": True,
        "write": False,
        "required_credentials": ["db_secret_ref"],
        "receipts": ["query_result", "row_fingerprint"],
        "cleanup_semantics": "read_only",
        "available": bool(available),
    }


def log_adapter_capabilities(*, available: bool = False) -> dict[str, Any]:
    return {
        "adapter": "log_audit",
        "read": True,
        "write": False,
        "required_credentials": [],
        "receipts": ["log_window"],
        "cleanup_semantics": "read_only",
        "available": bool(available),
    }


def build_adapter_capability_matrix(
    *,
    ui_available: bool = False,
    db_available: bool = False,
    log_available: bool = False,
) -> dict[str, Any]:
    adapters = [
        http_adapter_capabilities(),
        ui_adapter_capabilities(available=ui_available),
        db_adapter_capabilities(available=db_available),
        log_adapter_capabilities(available=log_available),
    ]
    unavailable = [item["adapter"] for item in adapters if not item.get("available")]
    return {
        "schema_version": "qualibug.execution-adapter.v1",
        "adapters": adapters,
        "unavailable_surfaces": unavailable,
        "coverage_claim_rule": "unsupported_surface_must_emit_BLOCKED_UNSUPPORTED_ADAPTER",
    }
