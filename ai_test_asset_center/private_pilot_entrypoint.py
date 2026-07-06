from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It installs the runtime patches from ``private_pilot_server``
and normalizes the health contract before delegating to the legacy HTTP server.
"""

import os
import platform
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.version import (
    CANONICAL_HEALTH_PATH,
    DEFAULT_PRIVATE_PILOT_PORT,
    LEGACY_HEALTH_PATH,
    PRODUCT_CHANNEL,
    PRODUCT_NAME,
    PRODUCT_PHASE,
    PRODUCT_VERSION,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"


def _int_env(name: str, fallback: int) -> int:
    try:
        return int(os.environ.get(name, "") or fallback)
    except Exception:
        return fallback


def _pattern_library_count(root: Path) -> int:
    for candidate in (
        root / "pattern_library" / "patterns.json",
        root / "platform_workspace" / "pattern_library" / "patterns.json",
    ):
        try:
            if candidate.exists():
                import json

                data = json.loads(candidate.read_text(encoding="utf-8") or "{}")
                patterns = data.get("patterns") if isinstance(data, dict) else []
                return len(patterns) if isinstance(patterns, list) else 0
        except Exception:
            continue
    return 0


def _health_payload(handler: Any) -> dict[str, Any]:
    try:
        root = handler._root()
    except Exception:
        root = _service._root()
    try:
        llm_health = handler._llm_health()
    except Exception as exc:
        llm_health = {
            "available": False,
            "status": "offline",
            "label": "offline",
            "error": str(exc)[:300],
        }
    return {
        "ok": True,
        "service": "qualibug_private_pilot",
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "product_version": PRODUCT_VERSION,
        "phase": PRODUCT_PHASE,
        "channel": PRODUCT_CHANNEL,
        "api_version": "v1",
        "private_root": str(root),
        "private_root_exists": root.exists(),
        "public_bind_allowed": os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
        "bind_host": os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1"),
        "port": _int_env("QUALIBUG_PORT", DEFAULT_PRIVATE_PILOT_PORT),
        "canonical_health_path": CANONICAL_HEALTH_PATH,
        "legacy_health_path": LEGACY_HEALTH_PATH,
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "llm_available": bool(llm_health.get("available")),
        "llm_status": llm_health,
        "pattern_library_patterns": _pattern_library_count(root),
        "deployment_contract_patch": {
            "patched": True,
            "source": PATCH_SOURCE,
            "port_contract": f"container:{DEFAULT_PRIVATE_PILOT_PORT}",
            "health_contract": CANONICAL_HEALTH_PATH,
        },
    }


def install_deployment_contract_patch() -> None:
    """Normalize health/version behavior for patched deployments."""
    if getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False):
        return
    original_do_get = getattr(_service.PrivatePilotHandler, "do_GET")

    def _do_get_with_deployment_contract(self: Any) -> Any:
        parsed = urlparse(self.path)
        if parsed.path in {CANONICAL_HEALTH_PATH, LEGACY_HEALTH_PATH}:
            return self._json(_health_payload(self))
        return original_do_get(self)

    _service.PrivatePilotHandler.do_GET = _do_get_with_deployment_contract
    _service._ORIGINAL_DEPLOYMENT_DO_GET = original_do_get  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = True  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_deployment_contract_patch() -> None:
    original_do_get = getattr(_service, "_ORIGINAL_DEPLOYMENT_DO_GET", None)
    if original_do_get is not None:
        _service.PrivatePilotHandler.do_GET = original_do_get
    _service._ORIGINAL_DEPLOYMENT_DO_GET = None  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = False  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = ""  # type: ignore[attr-defined]


def run_server() -> None:
    install_customer_delivery_gate_patch()
    install_deployment_contract_patch()
    _service.run_server()


if __name__ == "__main__":
    run_server()
