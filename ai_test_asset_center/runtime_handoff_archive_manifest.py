from __future__ import annotations

"""Phase93M: commercial handoff archive manifest + immutable run receipt.

Phase93J-L make the customer handoff bundle readable, acceptable and safe to
send.  Phase93M adds an audit trail: deterministic hashes over the probe plan,
key gates, handoff bundle, secret audit and generated artifacts so later reruns
can prove whether the same inputs and delivery package were used.

The final execution report cannot safely hash itself after embedding the receipt
without creating a circular hash.  This module therefore records a
``execution_report_payload_hash`` over the pre-receipt report payload and hashes
all other materialized artifact files that already exist on disk.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

_REQUIRED_KEYS = {
    "execution_report",
    "commercial_handoff_bundle_json",
    "commercial_handoff_acceptance_gate_json",
    "commercial_handoff_secret_audit_json",
    "runtime_evidence_readiness_sla_gate_json",
    "runtime_sla_execution_policy_json",
    "write_sandbox_approval_packet_json",
    "remediation_verification_json",
}

_PHASE_BY_KEY_PREFIX = [
    ("onboarding_preflight", "phase93a"),
    ("runtime_capability", "phase93b"),
    ("onboarding_remediation", "phase93c"),
    ("runtime_execution_runbook", "phase93d"),
    ("runtime_evidence_readiness", "phase93e"),
    ("runtime_sla_execution", "phase93f"),
    ("runtime_sla_gap", "phase93g"),
    ("onboarding_patch_safety", "phase93h"),
    ("write_sandbox_approval", "phase93i"),
    ("commercial_handoff_bundle", "phase93j"),
    ("commercial_handoff_acceptance", "phase93k"),
    ("commercial_handoff_secret", "phase93l"),
    ("handoff_archive_manifest", "phase93m"),
    ("immutable_run_receipt", "phase93m"),
    ("remediation_verification", "phase92z"),
    ("repro", "phase92w"),
    ("regression", "phase92w"),
    ("execution_report", "core_runtime"),
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload_hash(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _file_hash(path_text: str) -> tuple[bool, int, str | None, str | None]:
    path = Path(path_text) if path_text else Path("")
    if not path_text or not path.exists() or not path.is_file():
        return False, 0, None, "missing_or_not_a_file"
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return True, size, h.hexdigest(), None


def _phase_for_key(key: str) -> str:
    for prefix, phase in _PHASE_BY_KEY_PREFIX:
        if key.startswith(prefix):
            return phase
    return "unknown"


def _report_payload_for_hash(report: dict[str, Any]) -> dict[str, Any]:
    """Return the report shape used for the non-circular execution report hash."""

    excluded = {
        "handoff_archive_manifest",
        "immutable_run_receipt",
    }
    return {k: v for k, v in report.items() if k not in excluded}


def _artifact_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = _as_dict(report.get("outputs"))
    entries: list[dict[str, Any]] = []
    for key in sorted(outputs):
        path = str(outputs.get(key) or "")
        exists, size, sha, error = _file_hash(path)
        self_referential = key in {"execution_report", "handoff_archive_manifest_json", "handoff_archive_manifest_md", "immutable_run_receipt_json", "immutable_run_receipt_md"}
        entries.append({
            "artifact_key": key,
            "path": path,
            "exists": exists,
            "byte_size": size,
            "sha256": sha,
            "hash_status": "hashed" if sha else ("self_referential_or_pending" if self_referential else "missing"),
            "hash_error": None if sha or self_referential else error,
            "required_for_archive": key in _REQUIRED_KEYS,
            "phase": _phase_for_key(key),
        })
    return entries


def _hash_existing_artifacts(entries: list[dict[str, Any]]) -> str:
    material = [
        {
            "artifact_key": e.get("artifact_key"),
            "sha256": e.get("sha256"),
            "byte_size": e.get("byte_size"),
            "phase": e.get("phase"),
        }
        for e in entries
        if e.get("sha256")
    ]
    return _payload_hash(material)


def _stable_lineage_id(report: dict[str, Any], probe_plan_hash: str | None, gate_hash: str, bundle_hash: str) -> str:
    material = {
        "project_id": report.get("project_id"),
        "probe_plan_hash": probe_plan_hash,
        "base_url_configured": bool(report.get("base_url_configured")),
        "execute_readonly": bool(report.get("execute_readonly")),
        "allow_write_sandbox": bool(report.get("allow_write_sandbox")),
        "sla_gate_hash": gate_hash,
        "handoff_bundle_hash": bundle_hash,
    }
    return "qbrun-" + _payload_hash(material)[:20]


def build_handoff_archive_manifest(report: dict[str, Any]) -> dict[str, Any]:
    """Build an immutable archive manifest and run receipt for a handoff run."""

    payload_report_hash = _payload_hash(_report_payload_for_hash(report))
    probe_plan_path = str(report.get("probe_plan") or "")
    probe_plan_exists, probe_plan_size, probe_plan_hash, probe_plan_error = _file_hash(probe_plan_path)

    artifact_entries = _artifact_entries(report)
    missing_required = [e for e in artifact_entries if e.get("required_for_archive") and not e.get("sha256") and e.get("artifact_key") != "execution_report"]

    bundle_hash = _payload_hash(_as_dict(report.get("commercial_handoff_bundle")))
    acceptance_hash = _payload_hash(_as_dict(report.get("commercial_handoff_acceptance_gate")))
    secret_audit_hash = _payload_hash(_as_dict(report.get("commercial_handoff_secret_audit")))
    sla_gate_hash = _payload_hash(_as_dict(report.get("runtime_evidence_readiness_sla_gate")))
    sla_policy_hash = _payload_hash(_as_dict(report.get("runtime_sla_execution_policy")))
    remediation_hash = _payload_hash(_as_dict(report.get("remediation_verification_artifact")))
    aggregate_artifact_hash = _hash_existing_artifacts(artifact_entries)

    acceptance_gate = _as_dict(report.get("commercial_handoff_acceptance_gate"))
    secret_audit = _as_dict(report.get("commercial_handoff_secret_audit"))
    bundle = _as_dict(report.get("commercial_handoff_bundle"))
    sla_gate = _as_dict(report.get("runtime_evidence_readiness_sla_gate"))
    summary = _as_dict(report.get("summary"))
    run_lineage_id = _stable_lineage_id(report, probe_plan_hash, sla_gate_hash, bundle_hash)
    minimum_failures = [str(x) for x in _as_list(sla_gate.get("minimum_commercial_gate_failures")) if str(x)]
    commercial_blockers = [str(x) for x in _as_list(sla_gate.get("commercial_blocking_reasons")) if str(x)]
    acceptance_violations = [x for x in _as_list(acceptance_gate.get("violations")) if isinstance(x, dict)]

    if missing_required:
        status = "archive_receipt_with_missing_required_artifacts"
        recommendation = "Regenerate the handoff package so every required non-self-referential artifact can be hashed."
    else:
        status = "immutable_archive_receipt_ready"
        recommendation = "Store this manifest and receipt with the customer handoff archive; use the hashes to compare future reruns."

    receipt = {
        "engine": "runtime_immutable_run_receipt_v1_phase93m",
        "receipt_status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "run_lineage_id": run_lineage_id,
        "engine_version": report.get("engine"),
        "probe_plan_path": probe_plan_path,
        "probe_plan_hash": probe_plan_hash,
        "probe_plan_hash_status": "hashed" if probe_plan_hash else "missing",
        "probe_plan_byte_size": probe_plan_size if probe_plan_exists else 0,
        "probe_plan_error": None if probe_plan_hash else probe_plan_error,
        "execution_report_payload_hash": payload_report_hash,
        "artifact_archive_hash": aggregate_artifact_hash,
        "commercial_handoff_bundle_hash": bundle_hash,
        "commercial_handoff_acceptance_gate_hash": acceptance_hash,
        "commercial_handoff_secret_audit_hash": secret_audit_hash,
        "runtime_evidence_sla_gate_hash": sla_gate_hash,
        "runtime_sla_execution_policy_hash": sla_policy_hash,
        "remediation_verification_hash": remediation_hash,
        "customer_acceptance_status": acceptance_gate.get("status"),
        "customer_acceptance_gate_passed": bool(acceptance_gate.get("acceptance_gate_passed")),
        "customer_acceptance_violation_count": int(acceptance_gate.get("violation_count") or len(acceptance_violations)),
        "customer_acceptance_violation_ids": [str(v.get("violation_id")) for v in acceptance_violations if v.get("violation_id")],
        "handoff_status": bundle.get("status"),
        "secret_audit_status": secret_audit.get("status"),
        "safe_for_customer_handoff": bool(secret_audit.get("safe_for_customer_handoff")),
        "commercial_readiness_score": summary.get("runtime_evidence_readiness_score"),
        "minimum_commercial_gate_failures": minimum_failures,
        "commercial_blocking_reasons": commercial_blockers,
        "hash_scope_note": "execution_report_payload_hash is computed before embedding this Phase93M receipt to avoid a circular self-reference.",
    }

    return {
        "engine": "runtime_handoff_archive_manifest_v1_phase93m",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "run_lineage_id": run_lineage_id,
        "recommendation": recommendation,
        "immutable_run_receipt": receipt,
        "artifact_manifest": artifact_entries,
        "artifact_count": len(artifact_entries),
        "hashed_artifact_count": sum(1 for e in artifact_entries if e.get("sha256")),
        "missing_required_artifact_count": len(missing_required),
        "missing_required_artifacts": [
            {
                "artifact_key": e.get("artifact_key"),
                "path": e.get("path"),
                "phase": e.get("phase"),
                "reason": e.get("hash_error") or e.get("hash_status"),
            }
            for e in missing_required
        ],
        "hashes": {
            "probe_plan_hash": probe_plan_hash,
            "execution_report_payload_hash": payload_report_hash,
            "artifact_archive_hash": aggregate_artifact_hash,
            "commercial_handoff_bundle_hash": bundle_hash,
            "commercial_handoff_acceptance_gate_hash": acceptance_hash,
            "commercial_handoff_secret_audit_hash": secret_audit_hash,
            "runtime_evidence_sla_gate_hash": sla_gate_hash,
            "runtime_sla_execution_policy_hash": sla_policy_hash,
            "remediation_verification_hash": remediation_hash,
        },
        "comparison_keys_for_future_reruns": [
            "run_lineage_id",
            "probe_plan_hash",
            "runtime_evidence_sla_gate_hash",
            "commercial_handoff_bundle_hash",
            "commercial_handoff_secret_audit_hash",
            "artifact_archive_hash",
        ],
        "customer_safe_note": "The manifest stores hashes and paths only; it does not require raw credentials or customer secrets.",
    }


def render_handoff_archive_manifest_markdown(manifest: dict[str, Any]) -> str:
    receipt = _as_dict(manifest.get("immutable_run_receipt"))
    lines = [
        "# Commercial Handoff Archive Manifest",
        "",
        f"- engine: `{manifest.get('engine')}`",
        f"- status: `{manifest.get('status')}`",
        f"- project: `{manifest.get('project_id')}`",
        f"- run lineage id: `{manifest.get('run_lineage_id')}`",
        f"- artifact count: `{manifest.get('artifact_count')}`",
        f"- hashed artifacts: `{manifest.get('hashed_artifact_count')}`",
        f"- missing required artifacts: `{manifest.get('missing_required_artifact_count')}`",
        f"- recommendation: {manifest.get('recommendation')}",
        "",
        "## Immutable run receipt",
        "",
        f"- receipt engine: `{receipt.get('engine')}`",
        f"- engine version: `{receipt.get('engine_version')}`",
        f"- probe plan hash: `{receipt.get('probe_plan_hash')}`",
        f"- execution report payload hash: `{receipt.get('execution_report_payload_hash')}`",
        f"- artifact archive hash: `{receipt.get('artifact_archive_hash')}`",
        f"- handoff bundle hash: `{receipt.get('commercial_handoff_bundle_hash')}`",
        f"- secret audit hash: `{receipt.get('commercial_handoff_secret_audit_hash')}`",
        f"- customer acceptance status: `{receipt.get('customer_acceptance_status')}`",
        f"- customer acceptance violations: `{receipt.get('customer_acceptance_violation_count')}`",
        f"- minimum commercial gate failures: `{receipt.get('minimum_commercial_gate_failures')}`",
        f"- safe for customer handoff: `{receipt.get('safe_for_customer_handoff')}`",
        "",
    ]
    if manifest.get("missing_required_artifacts"):
        lines.extend(["## Missing required artifacts", ""])
        for item in _as_list(manifest.get("missing_required_artifacts")):
            if isinstance(item, dict):
                lines.append(f"- `{item.get('artifact_key')}` ({item.get('phase')}) — {item.get('reason')} — `{item.get('path')}`")
        lines.append("")
    lines.extend(["## Artifact hashes", ""])
    for item in _as_list(manifest.get("artifact_manifest")):
        if isinstance(item, dict):
            lines.append(f"- `{item.get('artifact_key')}` phase `{item.get('phase')}` status `{item.get('hash_status')}` sha256 `{item.get('sha256')}`")
    lines.extend(["", f"> {manifest.get('customer_safe_note')}"])
    return "\n".join(lines)


def render_immutable_run_receipt_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# Immutable Runtime Run Receipt",
        "",
        f"- engine: `{receipt.get('engine')}`",
        f"- status: `{receipt.get('receipt_status')}`",
        f"- project: `{receipt.get('project_id')}`",
        f"- created at: `{receipt.get('created_at')}`",
        f"- run lineage id: `{receipt.get('run_lineage_id')}`",
        f"- engine version: `{receipt.get('engine_version')}`",
        f"- probe plan hash: `{receipt.get('probe_plan_hash')}`",
        f"- execution report payload hash: `{receipt.get('execution_report_payload_hash')}`",
        f"- artifact archive hash: `{receipt.get('artifact_archive_hash')}`",
        f"- SLA gate hash: `{receipt.get('runtime_evidence_sla_gate_hash')}`",
        f"- handoff bundle hash: `{receipt.get('commercial_handoff_bundle_hash')}`",
        f"- secret audit hash: `{receipt.get('commercial_handoff_secret_audit_hash')}`",
        f"- customer acceptance status: `{receipt.get('customer_acceptance_status')}`",
        f"- customer acceptance violations: `{receipt.get('customer_acceptance_violation_count')}`",
        f"- handoff status: `{receipt.get('handoff_status')}`",
        f"- minimum commercial gate failures: `{receipt.get('minimum_commercial_gate_failures')}`",
        f"- commercial blocking reasons: `{receipt.get('commercial_blocking_reasons')}`",
        f"- safe for customer handoff: `{receipt.get('safe_for_customer_handoff')}`",
        "",
        f"> {receipt.get('hash_scope_note')}",
    ]
    return "\n".join(lines)
