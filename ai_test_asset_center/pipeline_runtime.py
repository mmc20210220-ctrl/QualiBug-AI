"""Pipeline runtime contract and evidence persistence utilities.
Extracted from v12_pipeline.py.
"""
from __future__ import annotations

import hashlib, json, re
from pathlib import Path
from typing import Any

from .target_policy import build_target_policy_decision

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.I)


def _source_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _source_manifest_details(context: dict[str, Any], source_text: Any) -> tuple[dict[str, str], list[str]]:
    manifest = _dict(context.get("source_manifest"))
    source_id = str(manifest.get("source_id") or "").strip()
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    actual_hash = hashlib.sha256(_source_text(source_text).encode("utf-8")).hexdigest()
    issues: list[str] = []
    if not source_id or not source_hash:
        issues.append("SOURCE_PROVENANCE_MISSING")
    elif not _SHA256_RE.fullmatch(source_hash):
        issues.append("SOURCE_HASH_INVALID")
    elif source_hash != actual_hash:
        issues.append("SOURCE_HASH_MISMATCH")
    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "source_origin": str(manifest.get("source_origin") or "declared_manifest")[:80],
        "source_version_id": str(manifest.get("source_version_id") or "")[:80],
    }, issues


def _declared_adapters(context: dict[str, Any]) -> list[str]:
    """Normalize only campaign-declared adapters; never infer from URLs or drivers."""
    raw = context.get("declared_adapters")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("declared_adapters_not_list")
    adapters: list[str] = []
    for value in raw:
        name = str(value or "").strip()
        if not name:
            continue
        if len(name) > 80:
            raise ValueError("declared_adapter_name_too_long")
        if name not in adapters:
            adapters.append(name)
    return adapters


def _runtime_contract(context: dict[str, Any], base_url: str, source_text: Any) -> dict[str, Any]:
    verification_text = context.get("_source_verification_text", source_text)
    manifest, source_issues = _source_manifest_details(context, verification_text)
    environment_ref = str(context.get("environment_ref") or context.get("target_environment") or "").strip()
    environment_kind = str(
        context.get("environment_kind")
        or context.get("environment_type")
        or context.get("environment_class")
        or ""
    ).strip().lower()
    execution_mode = str(context.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    declared_adapters = _declared_adapters(context)
    scenario_gap_codes: list[str] = []
    if context.get("runtime_scenario_contract"):
        from .runtime_scenario_contract_gate import runtime_scenario_contract_gaps
        scenario_gap_codes = [
            str(item.get("code") or "")
            for item in runtime_scenario_contract_gaps(context)
            if str(item.get("code") or "")
        ]
    if not base_url:
        return {
            "status": "blocked" if scenario_gap_codes else "plan_only",
            "reason": "runtime_scenario_contract_blocked" if scenario_gap_codes else "runtime_target_missing",
            "missing_requirements": sorted(set(scenario_gap_codes)),
            "approved_base_url": "",
            "environment_ref": environment_ref,
            "environment_kind": environment_kind,
            "execution_mode": execution_mode,
            "declared_adapters": declared_adapters,
            "source_manifest": manifest,
            "source_issues": source_issues,
        }
    missing = list(source_issues) + scenario_gap_codes
    if not str(context.get("scope_id") or "").strip():
        missing.append("CAMPAIGN_SCOPE_MISSING")
    if not environment_ref:
        missing.append("ENVIRONMENT_REFERENCE_MISSING")
    if execution_mode == "approved_sandbox_write" and not environment_kind:
        missing.append("UNKNOWN_ENVIRONMENT")
    explicitly_approved_url = str(
        context.get("approved_base_url")
        or _dict(context.get("target_policy")).get("approved_base_url")
        or (base_url if not missing else "")
    ).strip()
    decision = build_target_policy_decision(
        requested_base_url=base_url,
        approved_base_url=explicitly_approved_url,
        environment_type=environment_kind,
        environment_ref=environment_ref,
        execution_mode=execution_mode,
        runtime_status="approved" if not missing else "blocked",
    )
    if execution_mode == "approved_sandbox_write" and not decision.get("write_allowed"):
        missing.extend(str(code) for code in decision.get("blocking_codes") or [])
    elif not decision.get("read_allowed"):
        missing.extend(str(code) for code in decision.get("blocking_codes") or [])
    if missing:
        return {
            "status": "blocked",
            "reason": "runtime_scenario_contract_blocked" if scenario_gap_codes else "runtime_contract_missing",
            "missing_requirements": sorted(set(missing)),
            "approved_base_url": "",
            "environment_ref": environment_ref,
            "environment_kind": environment_kind,
            "execution_mode": execution_mode,
            "declared_adapters": declared_adapters,
            "source_manifest": manifest,
            "target_policy_decision": decision,
        }
    contract: dict[str, Any] = {
        "status": "approved",
        "reason": "",
        "missing_requirements": [],
        "requested_base_url": str(base_url).rstrip("/"),
        "approved_base_url": explicitly_approved_url.rstrip("/"),
        "environment_ref": environment_ref,
        "environment_kind": environment_kind,
        "execution_mode": execution_mode,
        "declared_adapters": declared_adapters,
        "source_manifest": manifest,
        "target_policy_decision": decision,
    }
    # Propagate validation_phase so downstream budget enforcement respects it.
    _vp = str(context.get("validation_phase") or "").strip().lower()
    if _vp:
        contract["validation_phase"] = _vp
    # Propagate the family-fair execution quota (per-family minimum
    # experiments per batch) so the batch executor's budget construction and
    # the prioritizer's family tier respect the operator-declared quota.
    _fq = context.get("family_execution_quota")
    if _fq:
        contract["family_execution_quota"] = int(_fq)
    # Propagate the operator-declared per-batch experiment budget. Without
    # this the batch executor falls back to the phase default (~100), so a
    # declared 250 budget silently executes only ~116 obligations (run26b
    # measured: SELECTED 1150 → 116 attempted, 1034 DEFERRED_BUDGET).
    _eb = context.get("experiment_budget")
    if _eb:
        contract["experiment_budget"] = int(_eb)
    return contract


def _slice_ledger_path(root: Path, project: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "v12_behavior_slice_ledger.json"



def source_snapshot_hash(prd_text: str, api_spec_text: str, db_schema_text: str, scope_id: str, environment_ref: str) -> str:
    import hashlib
    material = "|".join([prd_text, api_spec_text, db_schema_text, scope_id, environment_ref])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _active_policy_version() -> str:
    try:
        from .policy_wiring import get_policy_value
        return str(get_policy_value("discovery", "policy_version", "v1.0.0-baseline"))
    except Exception:
        return "v1.0.0-baseline"






def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _evidence_chain_path(root: Path, project: str, evidence_id: str) -> Path:
    return root / "platform_workspace" / str(project) / "defect_discovery" / "evidence_chains" / f"{evidence_id}.json"


def _persist_evidence_chain(root: Path, project: str, evidence: dict[str, Any]) -> str:
    """主链 7: land a collected evidence chain on disk keyed by its (stable)
    evidence_id so it can be retrieved for regression (主链 9) and delivery
    (主链 8). Returns the written path, or '' when the evidence has no id."""
    evidence_id = str(evidence.get("evidence_id") or "").strip()
    if not evidence_id:
        raise ValueError("EVIDENCE_ID_MISSING")
    path = _evidence_chain_path(root, project, evidence_id)
    try:
        from .artifact_redactor import write_json_redacted

        write_json_redacted(path, evidence)
    except Exception as exc:
        raise RuntimeError(f"EVIDENCE_CHAIN_PERSIST_FAILED:{evidence_id}:{type(exc).__name__}") from exc
    return str(path)


def _confirmed_findings_path(root: Path, project: str) -> Path:
    """主链 9 Gap B1: location of the persistable confirmed-defect ledger. The
    regression runner reads this file to re-verify already-confirmed defects
    after a fix — closing the loop between 主链 6/7 and 主链 9.

    Uses the same ``platform_workspace/<project>/defect_discovery`` base as the
    evidence chains (主链 7) so 主链 9 can read both products from one place.
    """
    return root / "platform_workspace" / str(project) / "defect_discovery" / "confirmed_findings.json"
