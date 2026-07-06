from __future__ import annotations

"""Patch installer for private-pilot deployment health/version routes."""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_health_contract import build_private_pilot_health_payload
from ai_test_asset_center.version import CANONICAL_HEALTH_PATH, LEGACY_HEALTH_PATH


def health_payload(handler: Any, *, patch_source: str, fallback_root: Path | None = None) -> dict[str, Any]:
    return build_private_pilot_health_payload(
        handler,
        fallback_root=fallback_root or _service._root(),
        patch_source=patch_source,
    )


def install_deployment_contract_patch(*, patch_source: str, fallback_root: Path | None = None) -> None:
    """Normalize health/version behavior for patched deployments."""
    if getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False):
        return
    original_do_get = getattr(_service.PrivatePilotHandler, "do_GET")

    def _do_get_with_deployment_contract(self: Any) -> Any:
        parsed = urlparse(self.path)
        if parsed.path in {CANONICAL_HEALTH_PATH, LEGACY_HEALTH_PATH}:
            return self._json(health_payload(self, patch_source=patch_source, fallback_root=fallback_root))
        return original_do_get(self)

    _service.PrivatePilotHandler.do_GET = _do_get_with_deployment_contract
    _service._ORIGINAL_DEPLOYMENT_DO_GET = original_do_get  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = True  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_deployment_contract_patch() -> None:
    original_do_get = getattr(_service, "_ORIGINAL_DEPLOYMENT_DO_GET", None)
    if original_do_get is not None:
        _service.PrivatePilotHandler.do_GET = original_do_get
    _service._ORIGINAL_DEPLOYMENT_DO_GET = None  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = False  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
