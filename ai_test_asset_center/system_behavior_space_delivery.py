"""System Behavior Space finding and regression delivery helpers.

Owns regression-contract projection and confirmed-finding enrichment used
by first-class finding/regression hooks. The private-pilot patch module
remains the thin installer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
)

def _system_behavior_regression_contract(hints: dict[str, Any]) -> dict[str, Any]:
    if not hints or not str(hints.get("promise_id") or ""):
        return {}
    return {
        "contract_type": "system_behavior_promise_regression",
        "system_behavior_space_version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "system_behavior_space": hints,
        "promise_id": str(hints.get("promise_id") or ""),
        "probe_id": str(hints.get("probe_id") or ""),
        "dimensions": [str(item) for item in hints.get("dimensions") or [] if str(item)],
        "surface_plan": [str(item) for item in hints.get("surface_plan") or [] if str(item)],
        "required_assets": [str(item) for item in hints.get("required_assets") or [] if str(item)],
        "source_slice_id": str(hints.get("source_slice_id") or ""),
        "source_family": str(hints.get("source_family") or ""),
    }


def _contract_from_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    contract = row.get("regression_contract") if isinstance(row.get("regression_contract"), dict) else {}
    if isinstance(contract.get("system_behavior_space"), dict) and str(contract.get("promise_id") or contract["system_behavior_space"].get("promise_id") or ""):
        hints = dict(contract.get("system_behavior_space") or {})
        if not str(hints.get("promise_id") or ""):
            hints["promise_id"] = str(contract.get("promise_id") or "")
        return _system_behavior_regression_contract(hints)
    hints = row.get("system_behavior_space_evidence") if isinstance(row.get("system_behavior_space_evidence"), dict) else {}
    if hints:
        return _system_behavior_regression_contract(hints)
    return {}


def _attach_regression_contract_fields(target: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict) or not contract:
        return target
    target["regression_contract"] = contract
    target["system_promise_id"] = str(contract.get("promise_id") or "")
    target["system_behavior_space_evidence"] = dict(contract.get("system_behavior_space") or {})
    target["system_behavior_dimensions"] = list(contract.get("dimensions") or [])
    target["system_behavior_surface_plan"] = list(contract.get("surface_plan") or [])
    target["system_behavior_required_assets"] = list(contract.get("required_assets") or [])
    target["system_behavior_source_family"] = str(contract.get("source_family") or "")
    return target


def _attach_system_behavior_to_finding(finding: dict[str, Any], hints: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(finding, dict) or not hints:
        return finding
    promise_id = str(hints.get("promise_id") or "").strip()
    if not promise_id:
        return finding
    regression_contract = _system_behavior_regression_contract(hints)
    _attach_regression_contract_fields(finding, regression_contract)
    finding["learning_signal"] = {"source": "system_behavior_space", "promise_id": promise_id, "dimensions": regression_contract.get("dimensions", []), "surfaces": regression_contract.get("surface_plan", []), "entity": str(finding.get("category") or scenario.get("entity") or "system")}
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence["system_promise_id"] = promise_id
    evidence["system_behavior_space"] = hints
    finding["evidence"] = evidence
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    raw["system_behavior_space"] = hints
    raw["regression_contract"] = regression_contract
    finding["raw_evidence"] = raw
    status = finding.get("evidence_status") if isinstance(finding.get("evidence_status"), dict) else {}
    status["system_promise_verdict"] = "SYSTEM_PROMISE_CONFIRMED" if finding.get("gate_passed") is True else "SYSTEM_PROMISE_CANDIDATE"
    finding["evidence_status"] = status
    return finding


def _system_behavior_learning_refresh_summary(project: str, root: Path) -> dict[str, Any]:
    try:
        from ai_test_asset_center.risk_clue_pool import get_platform_learning, refresh_project_learning
        project_learning = refresh_project_learning(project, root)
        platform_learning = get_platform_learning(root)
        return {
            "status": "refreshed",
            "project_learning_version": str(project_learning.get("version") or ""),
            "project_signal_count": int(project_learning.get("signal_count") or 0),
            "project_system_promise_signal_count": int(project_learning.get("system_promise_signal_count") or 0),
            "platform_learning_version": str(platform_learning.get("version") or ""),
            "platform_signal_count": int(platform_learning.get("signal_count") or 0),
        }
    except Exception as exc:
        return {"status": "refresh_failed", "reason": type(exc).__name__}

