"""Final post-persistent guards and coverage for complex UI interactions."""
from __future__ import annotations

import copy
import sys
from collections import Counter
from typing import Any

from . import professional_ui_complex_interactions as _complex
from . import professional_ui_coverage_projection as _coverage
from . import professional_ui_interaction_cleanup as _interaction
from .professional_ui_complex_origin_guard import (
    install_professional_ui_complex_origin_guard,
)

_INSTALL_MARKER = "_qualibug_complex_interaction_finalizer_installed"
_ORIGINAL_PROBE_VALIDATOR = "_qualibug_probe_validator_before_complex_finalizer"
_ORIGINAL_COVERAGE = "_qualibug_coverage_before_complex_interactions"
_PERSISTENT_PROPERTY = "http_json_pointer"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _complex_projection(payload: dict[str, Any]) -> dict[str, Any]:
    treatment = Counter({
        _text(key): int(value or 0)
        for key, value in _dict(
            payload.get("declared_treatment_interaction_action_counts")
        ).items()
        if _text(key) in _complex.COMPLEX_INTERACTIVE_ACTIONS
    })
    cleanup = Counter({
        _text(key): int(value or 0)
        for key, value in _dict(
            payload.get("declared_cleanup_interaction_action_counts")
        ).items()
        if _text(key) in _complex.COMPLEX_INTERACTIVE_ACTIONS
    })
    return {
        "schema_version": "qualibug.ui-complex-interaction-coverage.v1",
        "supported_actions": sorted(_complex.COMPLEX_INTERACTIVE_ACTIONS),
        "declared_treatment_action_counts": dict(sorted(treatment.items())),
        "declared_cleanup_action_counts": dict(sorted(cleanup.items())),
        "declared_treatment_count": sum(treatment.values()),
        "declared_cleanup_count": sum(cleanup.values()),
        "file_upload_binding_authority": "runtime_contract.ui_file_bindings",
        "upload_literal_paths_supported": False,
        "upload_project_scoped_files_required": True,
        "upload_sha256_identity_required": True,
        "upload_symlink_components_supported": False,
        "download_evidence_policy": "sha256_size_filename_fingerprint_delete",
        "download_raw_content_persisted": False,
        "popup_final_url_source_declared": True,
        "popup_close_after_observation_required": True,
        "iframe_unique_target_required": True,
        "iframe_exact_approved_origin_required": True,
        "complex_actions_require_approved_sandbox_write": True,
        "persistent_cleanup_equivalence_required": True,
        "download_or_popup_mismatch_is_formal_violation_v1": False,
    }


def install_professional_ui_complex_interaction_finalizer() -> None:
    install_professional_ui_complex_origin_guard()
    if getattr(_interaction, _INSTALL_MARKER, False):
        return
    original_probe = getattr(
        _interaction,
        _ORIGINAL_PROBE_VALIDATOR,
        _interaction._validate_probe,
    )
    original_coverage = getattr(
        _coverage,
        _ORIGINAL_COVERAGE,
        _coverage.build_professional_ui_coverage,
    )
    setattr(_interaction, _ORIGINAL_PROBE_VALIDATOR, original_probe)
    setattr(_coverage, _ORIGINAL_COVERAGE, original_coverage)

    def validate_probe_with_frame_boundary(
        raw: dict[str, Any],
        seen: set[str],
    ) -> dict[str, Any]:
        prop = _text(_dict(raw).get("property")).lower()
        has_frame = bool(raw.get("frame_selector") or raw.get("frame_origin"))
        if prop == _PERSISTENT_PROPERTY and has_frame:
            raise _interaction._browser.BrowserExecutionError(
                "browser_persistent_probe_frame_scope_forbidden"
            )
        if has_frame:
            _complex._validate_frame_scope(raw)
        return original_probe(raw, seen)

    def build_coverage_with_complex_interactions(
        result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = copy.deepcopy(original_coverage(result))
        payload["complex_interactions"] = _complex_projection(payload)
        boundary = _dict(payload.get("capability_boundary"))
        boundary.update({
            "governed_file_upload_supported": True,
            "upload_runtime_file_binding_required": True,
            "upload_literal_file_path_supported": False,
            "upload_sha256_identity_required": True,
            "upload_symlink_supported": False,
            "governed_download_observation_supported": True,
            "download_raw_content_persisted": False,
            "download_deleted_after_observation": True,
            "governed_popup_observation_supported": True,
            "popup_closed_after_observation": True,
            "iframe_scoped_interaction_supported": True,
            "iframe_exact_approved_origin_required": True,
            "complex_interaction_cleanup_equivalence_required": True,
            "complex_interaction_mismatch_is_formal_violation_v1": False,
        })
        payload["capability_boundary"] = boundary
        return payload

    _interaction._validate_probe = validate_probe_with_frame_boundary
    _coverage.build_professional_ui_coverage = build_coverage_with_complex_interactions
    loss_module = sys.modules.get("ai_test_asset_center.discovery_ui_loss_projection")
    if loss_module is not None and getattr(
        loss_module,
        "build_professional_ui_coverage",
        None,
    ) is original_coverage:
        loss_module.build_professional_ui_coverage = (
            build_coverage_with_complex_interactions
        )
    setattr(_interaction, _INSTALL_MARKER, True)


__all__ = [
    "install_professional_ui_complex_interaction_finalizer",
]
