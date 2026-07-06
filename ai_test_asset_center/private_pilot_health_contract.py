from __future__ import annotations

"""Health contract for patched private-pilot deployments."""

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from ai_test_asset_center.version import (
    CANONICAL_HEALTH_PATH,
    DEFAULT_PRIVATE_PILOT_PORT,
    LEGACY_HEALTH_PATH,
    PRODUCT_CHANNEL,
    PRODUCT_NAME,
    PRODUCT_PHASE,
    PRODUCT_VERSION,
)


def int_env(name: str, fallback: int) -> int:
    try:
        return int(os.environ.get(name, "") or fallback)
    except Exception:
        return fallback


def pattern_library_count(root: Path) -> int:
    for candidate in (
        root / "pattern_library" / "patterns.json",
        root / "platform_workspace" / "pattern_library" / "patterns.json",
    ):
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8") or "{}")
                patterns = data.get("patterns") if isinstance(data, dict) else []
                return len(patterns) if isinstance(patterns, list) else 0
        except Exception:
            continue
    return 0


def browser_ui_status() -> dict[str, Any]:
    try:
        from ai_test_asset_center.browser_ui_smoke import is_browser_ui_enabled

        enabled = is_browser_ui_enabled()
    except Exception:
        enabled = False
    return {
        "enabled": enabled,
        "env_flag": "QUALIBUG_BROWSER_UI_SMOKE",
        "mode": "smoke" if enabled else "disabled",
        "evidence": ["page_reachability", "console_errors", "network_errors", "screenshots", "har"],
    }


def _handler_root(handler: Any, fallback_root: Path) -> Path:
    try:
        return handler._root()
    except Exception:
        return fallback_root


def _handler_llm_health(handler: Any) -> dict[str, Any]:
    try:
        return handler._llm_health()
    except Exception as exc:
        return {
            "available": False,
            "status": "offline",
            "label": "offline",
            "error": str(exc)[:300],
        }


def build_private_pilot_health_payload(
    handler: Any,
    *,
    fallback_root: Path,
    patch_source: str,
) -> dict[str, Any]:
    root = _handler_root(handler, fallback_root)
    llm_health = _handler_llm_health(handler)
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
        "port": int_env("QUALIBUG_PORT", DEFAULT_PRIVATE_PILOT_PORT),
        "canonical_health_path": CANONICAL_HEALTH_PATH,
        "legacy_health_path": LEGACY_HEALTH_PATH,
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "llm_available": bool(llm_health.get("available")),
        "llm_status": llm_health,
        "browser_ui_smoke": browser_ui_status(),
        "pattern_library_patterns": pattern_library_count(root),
        "deployment_contract_patch": {
            "patched": True,
            "source": patch_source,
            "port_contract": f"container:{DEFAULT_PRIVATE_PILOT_PORT}",
            "health_contract": CANONICAL_HEALTH_PATH,
        },
    }
