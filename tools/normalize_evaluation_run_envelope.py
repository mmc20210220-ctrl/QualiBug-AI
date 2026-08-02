"""Normalize a funnel/live submission into an evaluator run envelope."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_test_asset_center.artifact_redactor import write_json_redacted
from ai_test_asset_center.canonical_defect_registry import (
    CanonicalDefectRegistryError,
    canonical_representative_findings,
    validate_canonical_defect_registry,
    validate_defect_identity_consistency,
)
from ai_test_asset_center.discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from ai_test_asset_center.discovery_evaluation_contract import (
    EVALUATION_RUN_ENVELOPE_SCHEMA,
)
from ai_test_asset_center.formal_delivery_authority import (
    FormalDeliveryAuthorityError,
    build_formal_delivery_authority_receipt,
    validate_formal_delivery_authority_receipt,
)
from ai_test_asset_center.discovery_quality_projection import (
    build_formal_count_projection,
)


REQUIRED_OPS = (
    "wall_clock_seconds",
    "estimated_cost_usd",
    "request_count",
    "production_http_requests",
    "cleanup_failures",
    "safety_incidents",
    "dirty_test_environments",
    "execution_success_rate",
    "engine_success_rate",
    "duplicate_rate",
)


def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_envelope(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("run envelope must be an object")
    run_id = str(raw.get("run_id") or "").strip()
    policy_id = str(raw.get("policy_id") or "").strip()
    evaluation_mode = str(raw.get("evaluation_mode") or "").strip()
    for field, value in (
        ("run_id", run_id),
        ("policy_id", policy_id),
        ("evaluation_mode", evaluation_mode),
    ):
        if not value:
            raise ValueError(f"{field} is required; identity is never invented")
    if not isinstance(raw.get("mainline_run"), dict):
        raise ValueError("mainline_run must be an object")
    try:
        mainline_run = validate_mainline_run_contract(raw["mainline_run"])
    except MainlineContractError as exc:
        raise ValueError(f"mainline_run invalid: {exc}") from exc
    expected_mainline = {
        "run_id": run_id,
        "evaluation_mode": evaluation_mode,
    }
    for field, value in expected_mainline.items():
        if mainline_run[field] != value:
            raise ValueError(f"{field} does not match mainline_run")
    campaign_id = str(raw.get("campaign_id") or "").strip()
    if not campaign_id or campaign_id != mainline_run["campaign_id"]:
        raise ValueError("campaign_id does not match mainline_run")

    scan = raw.get("scan_result")
    if not isinstance(scan, dict):
        raise ValueError("scan_result must be an object")
    findings = scan.get("findings")
    delivery_occurrences = scan.get("delivery_occurrences")
    candidates = scan.get("candidate_findings")
    if not isinstance(findings, list):
        raise ValueError("scan_result.findings must be a list")
    if not isinstance(candidates, list):
        raise ValueError("scan_result.candidate_findings must be a list")
    if not isinstance(delivery_occurrences, list):
        raise ValueError("scan_result.delivery_occurrences must be a list")
    attempt_ledger = scan.get("obligation_attempt_ledger")
    if not isinstance(attempt_ledger, dict):
        raise ValueError("scan_result.obligation_attempt_ledger must be an object")
    formal_projection = raw.get("formal_count_projection")
    canonical_registry = raw.get("canonical_defect_registry")
    defect_identity_consistency = raw.get("defect_identity_consistency")
    formal_authority = raw.get("formal_delivery_authority")
    for field, value in (
        ("formal_count_projection", formal_projection),
        ("canonical_defect_registry", canonical_registry),
        ("defect_identity_consistency", defect_identity_consistency),
        ("formal_delivery_authority", formal_authority),
    ):
        if not isinstance(value, dict):
            raise ValueError(f"{field} must be an object")
        if scan.get(field) != value:
            raise ValueError(f"scan_result.{field} must match envelope authority")
    try:
        validated_authority = validate_formal_delivery_authority_receipt(
            formal_authority
        )
        rebuilt_authority = build_formal_delivery_authority_receipt(
            mainline_run=mainline_run,
            findings=delivery_occurrences,
            obligation_attempt_ledger=attempt_ledger,
        )
    except FormalDeliveryAuthorityError as exc:
        raise ValueError(f"formal_delivery_authority invalid: {exc}") from exc
    if validated_authority != rebuilt_authority:
        raise ValueError("formal_delivery_authority does not match Gate ledger")
    try:
        validated_registry = validate_canonical_defect_registry(
            canonical_registry,
            mainline_run=mainline_run,
            deliverable_occurrences=delivery_occurrences,
            obligation_attempt_ledger=attempt_ledger,
        )
        if canonical_representative_findings(
            validated_registry,
            deliverable_occurrences=delivery_occurrences,
        ) != findings:
            raise CanonicalDefectRegistryError(
                "normalized_canonical_findings_mismatch"
            )
        validated_consistency = validate_defect_identity_consistency(
            defect_identity_consistency,
            required_occurrence_scopes={
                "delivery_gate_ids",
                "formal_authority_occurrence_ids",
                "registry_occurrence_ids",
                "evaluator_submission_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "evaluator_submission_ids",
            },
        )
    except CanonicalDefectRegistryError as exc:
        raise ValueError(f"canonical authority invalid: {exc}") from exc
    rebuilt_projection = build_formal_count_projection(
        findings=delivery_occurrences,
        candidate_findings=[],
        # funnel_validated_bug_count comes from the run's own discovery funnel;
        # without it the rebuild counts 0 and can never match the archived
        # projection, so the envelope is rejected as internally inconsistent.
        discovery_funnel=raw.get("discovery_funnel") or scan.get("discovery_funnel"),
        obligation_attempt_ledger=attempt_ledger,
        mainline_run=mainline_run,
        canonical_defect_registry=validated_registry,
    )
    if formal_projection != rebuilt_projection:
        raise ValueError("formal_count_projection does not match authority")

    ops = dict(raw.get("operational_metrics") or {})
    # Map diagnostic aliases → required contract fields without inventing success.
    if ops.get("wall_clock_seconds") is None and ops.get("elapsed_seconds") is not None:
        ops["wall_clock_seconds"] = _num(ops.get("elapsed_seconds"))
    # Unknown cost/usage must stay null → aggregate will mark incomplete (honest).
    for key in REQUIRED_OPS:
        if key not in ops:
            ops[key] = None
    envelope = {
        "schema_version": EVALUATION_RUN_ENVELOPE_SCHEMA,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "policy_id": policy_id,
        "evaluation_mode": evaluation_mode,
        "pipeline_health": dict(raw.get("pipeline_health") or {}),
        "operational_metrics": ops,
        "mainline_run": mainline_run,
        "canonical_defect_registry": validated_registry,
        "formal_count_projection": dict(formal_projection),
        "defect_identity_consistency": validated_consistency,
        "formal_delivery_authority": validated_authority,
        "scan_result": {
            "findings": list(findings),
            "delivery_occurrences": list(delivery_occurrences),
            "candidate_findings": list(candidates),
            "obligation_attempt_ledger": attempt_ledger,
            "canonical_defect_registry": validated_registry,
            "formal_count_projection": dict(formal_projection),
            "defect_identity_consistency": validated_consistency,
            "formal_delivery_authority": validated_authority,
        },
    }
    if isinstance(raw.get("fixture_governance"), dict):
        envelope["fixture_governance"] = raw["fixture_governance"]
    process_boundary = scan.get("process_boundary") or raw.get(
        "process_boundary"
    )
    if process_boundary is not None:
        if not isinstance(process_boundary, dict):
            raise ValueError("process_boundary must be an object when supplied")
        outer_boundary = raw.get("process_boundary")
        if outer_boundary is not None and outer_boundary != process_boundary:
            raise ValueError("scan_result.process_boundary does not match envelope")
        envelope["scan_result"]["process_boundary"] = dict(process_boundary)
        envelope["process_boundary"] = dict(process_boundary)
    execution_attestation = raw.get("execution_attestation")
    if execution_attestation is None:
        execution_attestation = scan.get("execution_attestation")
    if execution_attestation is not None:
        if not isinstance(execution_attestation, dict):
            raise ValueError(
                "execution_attestation must be an object when supplied"
            )
        scan_attestation = scan.get("execution_attestation")
        if scan_attestation is not None and scan_attestation != (
            execution_attestation
        ):
            raise ValueError(
                "scan_result.execution_attestation does not match envelope"
            )
        envelope["execution_attestation"] = dict(execution_attestation)
    trace_ledger = scan.get("trace_ledger") or raw.get("trace_ledger")
    if trace_ledger is not None:
        if not isinstance(trace_ledger, dict):
            raise ValueError("trace_ledger must be an object when supplied")
        envelope["scan_result"]["trace_ledger"] = trace_ledger
    return envelope


def validate_normalized_envelope(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("normalized run envelope must be an object")
    if value.get("schema_version") != EVALUATION_RUN_ENVELOPE_SCHEMA:
        raise ValueError("normalized run envelope schema is invalid")
    # Redaction rewrites sensitive values inside the ledger / occurrences, so a
    # content-addressed receipt archived BEFORE redaction cannot be rebuilt
    # verbatim from the redacted copy. Re-running full normalize here would
    # reject every redacted envelope as non-canonical (authority fingerprint
    # mismatch). Structural validation only: the canonical identity checks and
    # authority rebuild already ran on the unredacted input.
    for key in ("run_id", "campaign_id", "policy_id", "evaluation_mode"):
        if not str(value.get(key) or "").strip():
            raise ValueError(f"normalized run envelope {key} is missing")
    scan = value.get("scan_result")
    if not isinstance(scan, dict):
        raise ValueError("normalized run envelope scan_result must be an object")
    for key in ("findings", "candidate_findings", "delivery_occurrences"):
        if not isinstance(scan.get(key), list):
            raise ValueError(
                f"normalized run envelope scan_result.{key} must be a list"
            )
    if not isinstance(scan.get("obligation_attempt_ledger"), dict):
        raise ValueError(
            "normalized run envelope obligation_attempt_ledger must be an object"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    envelope = normalize_envelope(raw)
    write_json_redacted(
        Path(args.output),
        envelope,
        post_redaction_validator=validate_normalized_envelope,
    )
    missing = [k for k in REQUIRED_OPS if envelope["operational_metrics"].get(k) is None]
    print(json.dumps({
        "output": args.output,
        "missing_operational_fields": missing,
        "findings": len(envelope["scan_result"]["findings"]),
        "pipeline_health": (envelope.get("pipeline_health") or {}).get("status"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
