from __future__ import annotations

"""Runtime repair for a narrow scan-result evidence downgrade failure.

Registers a first-class ``scan`` post-hook instead of replacing ``__main__.scan``.
"""

from pathlib import Path
from typing import Any

from ai_test_asset_center.scan_post_hooks import register_scan_post_hook

PATCH_SOURCE = "ai_test_asset_center.private_pilot_scan_result_repair_patch"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_known_scope_failure(result: dict[str, Any]) -> bool:
    evidence_bundle = _as_dict(result.get("evidence_bundle"))
    if str(evidence_bundle.get("status") or "") != "persistence_failed":
        return False
    reason = str(evidence_bundle.get("reason") or "")
    return reason in {"UnboundLocalError", "NameError"}


def _restore_marked_confirmed_findings(result: dict[str, Any], persisted_bundle: dict[str, Any]) -> None:
    candidates = [item for item in (result.get("candidate_findings") or []) if isinstance(item, dict)]
    restored: list[dict[str, Any]] = []
    remaining_candidates: list[dict[str, Any]] = []

    for item in candidates:
        if str(item.get("evidence_persistence_status") or "") == "failed":
            repaired = dict(item)
            repaired["evidence_persistence_status"] = "persisted"
            repaired["evidence_bundle_status"] = str(persisted_bundle.get("status") or "persisted")
            if persisted_bundle.get("bundle_id"):
                repaired["evidence_bundle_id"] = str(persisted_bundle.get("bundle_id"))
            if str(repaired.get("confirmation_status") or "").strip().lower() in {"", "inconclusive"}:
                repaired["confirmation_status"] = "confirmed"
            restored.append(repaired)
        else:
            remaining_candidates.append(item)

    if not restored:
        return

    existing = [item for item in (result.get("findings") or []) if isinstance(item, dict)]
    result["findings"] = existing + restored
    result["candidate_findings"] = remaining_candidates
    result["total_findings"] = len(result["findings"])
    result["total_candidates"] = len(remaining_candidates)

    layers = result.get("layers") if isinstance(result.get("layers"), dict) else {}
    source_layer = layers.get("source_grounded_discovery") if isinstance(layers.get("source_grounded_discovery"), dict) else {}
    if source_layer:
        source_layer["findings"] = len(result["findings"])
        source_layer["candidates"] = len(remaining_candidates)

    result["scan_result_repair"] = {
        "status": "repaired",
        "patch_source": PATCH_SOURCE,
        "reason": "ui_bridge_scope_failure_after_evidence_persist",
        "restored_confirmed_findings": len(restored),
        "evidence_bundle_id": str(persisted_bundle.get("bundle_id") or ""),
    }


def _repair_scan_result_if_needed(result: dict[str, Any], *, project: str, root: Path, scanner_module: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or not _is_known_scope_failure(result):
        return result
    if not project:
        return result

    v12 = _as_dict(result.get("v12"))
    campaign = _as_dict(result.get("campaign"))
    runtime_contract = _as_dict(result.get("runtime_contract"))
    scan_id = str(result.get("scan_id") or "").strip()
    execution_status = str(result.get("execution_status") or "not_executed")
    if not (v12 and campaign and runtime_contract and scan_id):
        return result

    persist = getattr(scanner_module, "_persist_execution_evidence", None)
    if not callable(persist):
        return result

    try:
        persisted_bundle = persist(project, root, scan_id, campaign, runtime_contract, execution_status, v12)
    except Exception as exc:
        result["scan_result_repair"] = {
            "status": "repair_failed",
            "patch_source": PATCH_SOURCE,
            "reason": type(exc).__name__,
        }
        return result

    if not isinstance(persisted_bundle, dict) or str(persisted_bundle.get("status") or "") != "persisted":
        result["scan_result_repair"] = {
            "status": "not_repaired",
            "patch_source": PATCH_SOURCE,
            "reason": "evidence_repersist_not_persisted",
            "evidence_bundle_status": str((persisted_bundle or {}).get("status") or ""),
        }
        return result

    result["evidence_bundle"] = persisted_bundle
    _restore_marked_confirmed_findings(result, persisted_bundle)
    return result


def _scan_result_repair_hook(result: dict[str, Any], *, project: str, root: Path) -> dict[str, Any]:
    from ai_test_asset_center import __main__ as scanner_module

    return _repair_scan_result_if_needed(
        result,
        project=project,
        root=root,
        scanner_module=scanner_module,
    )


def install_scan_result_repair_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    from ai_test_asset_center import __main__ as scanner_module

    if getattr(scanner_module, "_SCAN_RESULT_REPAIR_PATCHED", False):
        return
    register_scan_post_hook("scan_result_repair", _scan_result_repair_hook)
    scanner_module._ORIGINAL_SCAN_RESULT_REPAIR_SCAN = None  # type: ignore[attr-defined]
    scanner_module._SCAN_RESULT_REPAIR_PATCHED = True  # type: ignore[attr-defined]
    scanner_module._SCAN_RESULT_REPAIR_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_scan_result_repair_patch() -> None:
    from ai_test_asset_center import __main__ as scanner_module

    register_scan_post_hook("scan_result_repair", None)
    scanner_module._ORIGINAL_SCAN_RESULT_REPAIR_SCAN = None  # type: ignore[attr-defined]
    scanner_module._SCAN_RESULT_REPAIR_PATCHED = False  # type: ignore[attr-defined]
    scanner_module._SCAN_RESULT_REPAIR_PATCH_SOURCE = ""  # type: ignore[attr-defined]
