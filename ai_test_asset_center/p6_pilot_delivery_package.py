from __future__ import annotations

"""P6 customer pilot delivery package.

P6 consolidates benchmark, scorecard, readout and evidence-story outputs into a
customer-safe delivery package index. It does not archive raw evidence or create
binary artifacts; it tells the delivery team what can be shared and what still
needs approval or hardening.
"""

from typing import Any


_REQUIRED_CORE = [
    "p3_seed_bug_benchmark",
    "p4_customer_value_scorecard",
    "p4_pilot_success_gate",
    "p5_executive_readout_pack",
    "p5_evidence_story_pack",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_text(value: Any, limit: int = 260) -> str:
    return str(value or "").strip()[:limit]


def _present(result: dict[str, Any], key: str) -> bool:
    value = result.get(key)
    return isinstance(value, dict) and bool(value)


def _artifact(name: str, key: str, description: str, shareable: bool, reason: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "result_key": key,
        "description": description,
        "customer_shareable": shareable,
        "reason": reason,
    }


def _delivery_artifacts(result: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = [
        _artifact(
            "Seed bug benchmark summary",
            "p3_seed_bug_benchmark",
            "Detection-rate summary for known seed defects.",
            _present(result, "p3_seed_bug_benchmark"),
            "Share only aggregate benchmark results and customer-safe defect IDs.",
        ),
        _artifact(
            "Customer value scorecard",
            "p4_customer_value_scorecard",
            "Management-level value proof with board metrics.",
            _present(result, "p4_customer_value_scorecard"),
            "Designed to be customer safe.",
        ),
        _artifact(
            "Pilot success gate",
            "p4_pilot_success_gate",
            "Decision on executive readout and procurement readiness.",
            _present(result, "p4_pilot_success_gate"),
            "Use as internal and customer-success decision record.",
        ),
        _artifact(
            "Executive readout pack",
            "p5_executive_readout_pack",
            "Customer executive readout structure and agenda.",
            _present(result, "p5_executive_readout_pack"),
            "Customer-facing structure; review before sending.",
        ),
        _artifact(
            "Evidence story pack",
            "p5_evidence_story_pack",
            "Problem-impact-evidence-action stories for P0/P1 findings.",
            _present(result, "p5_evidence_story_pack"),
            "No raw request/response payloads included.",
        ),
    ]
    evidence = _as_dict(result.get("evidence_bundle"))
    artifacts.append(
        _artifact(
            "Raw evidence bundle reference",
            "evidence_bundle",
            "Internal reference to persisted evidence bundle.",
            False,
            "Do not send raw evidence externally without customer approval and redaction review." if evidence else "Evidence bundle missing.",
        )
    )
    return artifacts


def _missing_outputs(result: dict[str, Any]) -> list[str]:
    return [key for key in _REQUIRED_CORE if not _present(result, key)]


def _external_blockers(result: dict[str, Any], missing: list[str]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if missing:
        blockers.append({"code": "DELIVERY_CORE_OUTPUTS_MISSING", "detail": "Missing required delivery outputs: " + ", ".join(missing)})
    gate = _as_dict(result.get("p4_pilot_success_gate"))
    if gate and gate.get("executive_readout_ready") is not True:
        blockers.append({"code": "EXECUTIVE_READOUT_NOT_READY", "detail": "Pilot gate does not allow executive readout."})
    story_pack = _as_dict(result.get("p5_evidence_story_pack"))
    if story_pack and story_pack.get("customer_safe") is not True:
        blockers.append({"code": "STORY_PACK_NOT_CUSTOMER_SAFE", "detail": "Evidence story pack is not marked customer_safe."})
    readout = _as_dict(result.get("p5_executive_readout_pack"))
    if readout and readout.get("customer_safe") is not True:
        blockers.append({"code": "READOUT_PACK_NOT_CUSTOMER_SAFE", "detail": "Executive readout pack is not marked customer_safe."})
    return blockers


def _internal_warnings(result: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    gate = _as_dict(result.get("p4_pilot_success_gate"))
    for row in _as_list(gate.get("warnings")):
        if isinstance(row, dict):
            warnings.append({"code": _safe_text(row.get("code"), 120), "detail": _safe_text(row.get("detail"), 260)})
    evidence = _as_dict(result.get("evidence_bundle"))
    status = _safe_text(evidence.get("status"), 80)
    if status and status not in {"persisted", "verified"}:
        warnings.append({"code": "EVIDENCE_BUNDLE_NOT_READY", "detail": "Evidence bundle is not persisted or verified."})
    release_gate = _as_dict(result.get("release_gate"))
    verdict = _safe_text(release_gate.get("verdict") or release_gate.get("status"), 80)
    if verdict and verdict not in {"approved", "review_required", "pass", "passed"}:
        warnings.append({"code": "RELEASE_GATE_NOT_READY", "detail": "Release gate is not ready for customer delivery."})
    return warnings


def _delivery_decision(blockers: list[dict[str, str]], gate: dict[str, Any]) -> str:
    if blockers:
        return "not_deliverable"
    if gate.get("procurement_motion_ready") is True:
        return "deliverable_for_procurement"
    if gate.get("executive_readout_ready") is True:
        return "deliverable_for_executive_readout"
    return "internal_only"


def _next_steps(decision: str, blockers: list[dict[str, str]], warnings: list[dict[str, str]]) -> list[str]:
    if decision == "deliverable_for_procurement":
        steps = [
            "Package customer-safe scorecard, readout pack and evidence stories for executive review.",
            "Keep raw evidence bundle internal unless customer approves redacted export.",
            "Start procurement scope and deployment-model discussion.",
        ]
    elif decision == "deliverable_for_executive_readout":
        steps = [
            "Share executive readout and evidence stories with customer stakeholders.",
            "Resolve evidence warnings before procurement motion.",
        ]
    elif blockers:
        steps = [
            "Do not deliver externally yet.",
            "Resolve blockers: " + ", ".join(item["code"] for item in blockers[:4]),
            "Regenerate P6 package after P3/P4/P5 outputs are complete.",
        ]
    else:
        steps = ["Keep package internal until executive-readout gate is ready."]
    if warnings and decision != "not_deliverable":
        steps.append("Review warnings before sending customer-facing material.")
    return steps[:5]


def build_p6_pilot_delivery_package(scan_result: dict[str, Any]) -> dict[str, Any]:
    result = _as_dict(scan_result)
    missing = _missing_outputs(result)
    blockers = _external_blockers(result, missing)
    warnings = _internal_warnings(result)
    gate = _as_dict(result.get("p4_pilot_success_gate"))
    decision = _delivery_decision(blockers, gate)
    artifacts = _delivery_artifacts(result)
    return {
        "schema_version": "p6-pilot-delivery-package-v1",
        "customer_safe": True,
        "project": _safe_text(result.get("project"), 120),
        "delivery_decision": decision,
        "external_delivery_allowed": decision in {"deliverable_for_procurement", "deliverable_for_executive_readout"},
        "procurement_package": decision == "deliverable_for_procurement",
        "executive_readout_package": decision in {"deliverable_for_procurement", "deliverable_for_executive_readout"},
        "missing_outputs": missing,
        "blockers": blockers,
        "warnings": warnings,
        "artifacts": artifacts,
        "customer_shareable_keys": [item["result_key"] for item in artifacts if item.get("customer_shareable") is True],
        "internal_only_keys": [item["result_key"] for item in artifacts if item.get("customer_shareable") is not True],
        "next_steps": _next_steps(decision, blockers, warnings),
        "non_goals": [
            "Do not include raw evidence bundle content in the customer package by default.",
            "Do not send materials when external_delivery_allowed is false.",
            "Do not claim procurement readiness unless procurement_package is true.",
        ],
    }
