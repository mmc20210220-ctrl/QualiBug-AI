from __future__ import annotations

"""Deployment health/version runtime-support status for private-pilot.

Health payload construction is first-class in ``HttpRoutingMixin.do_GET``.
This module only records compatibility status for health surfaces.
"""

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_health_contract import build_private_pilot_health_payload


def health_payload(handler: Any, *, patch_source: str, fallback_root: Path | None = None) -> dict[str, Any]:
    return build_private_pilot_health_payload(
        handler,
        fallback_root=fallback_root or _service._root(),
        patch_source=patch_source,
    )


def install_deployment_contract_patch(*, patch_source: str, fallback_root: Path | None = None) -> None:
    """Mark the deployment health contract as installed (handlers are first-class)."""
    if getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False):
        return
    _service._ORIGINAL_DEPLOYMENT_DO_GET = None  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = True  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    if fallback_root is not None:
        _service._DEPLOYMENT_CONTRACT_FALLBACK_ROOT = fallback_root  # type: ignore[attr-defined]


def restore_deployment_contract_patch() -> None:
    _service._ORIGINAL_DEPLOYMENT_DO_GET = None  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = False  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_service, "_DEPLOYMENT_CONTRACT_FALLBACK_ROOT"):
        delattr(_service, "_DEPLOYMENT_CONTRACT_FALLBACK_ROOT")
