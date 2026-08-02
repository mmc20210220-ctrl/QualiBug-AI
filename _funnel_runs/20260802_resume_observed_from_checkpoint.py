# -*- coding: utf-8 -*-
"""Resume MEASURED evaluate from a post-scan checkpoint (no re-scan)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ai_test_asset_center.discovery_evaluation_contract import (  # noqa: E402
    aggregate_evaluation_receipts,
    evaluate_completed_scan,
    load_evaluation_manifest,
    persist_evaluation_receipt,
    persist_evaluation_report,
)
from ai_test_asset_center.discovery_policy_evaluation_runner import (  # noqa: E402
    TrustedObservationStore,
)
from ai_test_asset_center.evaluator_execution_attestation import (  # noqa: E402
    ExecutionAttestationError,
    build_execution_attestation,
)
from ai_test_asset_center.evaluator_receipt_auth import (  # noqa: E402
    resolve_evaluator_hmac_key,
)
from ai_test_asset_center.policy_registry import PolicyRegistry  # noqa: E402

EVAL_ROOT = Path(r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716")
MANIFEST = EVAL_ROOT / "manifest" / "evaluation_manifest.json"
HMAC_KEY = EVAL_ROOT / "evaluator-hmac.key"
OBSERVATION_ROOT = EVAL_ROOT / "observations"
REGISTRY = REPO / "platform_outputs" / "policy_registry.json"
CHECKPOINT = (
    REPO
    / "_funnel_runs"
    / "20260802_fact_to_experiment_observed_20260802T064251Z"
    / "fact-to-experiment-observed-20260802T064251Z"
    / "checkpoints"
    / "benchmark-mall-held-in-131"
    / "post_scan_checkpoint.json"
)
OUTPUT_ROOT = (
    REPO
    / "_funnel_runs"
    / "20260802_fact_to_experiment_observed_20260802T064251Z"
)


def main() -> int:
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    evaluation_id = str(checkpoint["evaluation_id"])
    evaluation_mode = str(checkpoint["evaluation_mode"])
    target_id = str(checkpoint["target_id"])
    scan_output = dict(checkpoint["scan_output"])
    governance = dict(checkpoint["fixture_governance"])
    execution_policy_identity = dict(checkpoint["execution_policy_identity"])
    additional_request_attempts = list(
        checkpoint.get("additional_request_attempts") or []
    )
    process_boundary = scan_output.get("process_boundary")
    if not isinstance(process_boundary, dict):
        raise SystemExit("checkpoint missing process_boundary")

    signing_key = resolve_evaluator_hmac_key(HMAC_KEY.read_bytes())
    policy = PolicyRegistry(REGISTRY).get_active()
    if policy is None:
        raise SystemExit("no active policy")
    store = TrustedObservationStore(
        OBSERVATION_ROOT,
        product_workspace_root=REPO,
        verification_key=signing_key,
    )
    trusted_observations = store.load(
        run_id=str(scan_output["run_id"]),
        campaign_id=str(dict(scan_output["mainline_run"]).get("campaign_id") or ""),
        target_id=target_id,
    )
    checkpoint_dir = CHECKPOINT.parent
    try:
        execution_attestation = build_execution_attestation(
            mainline_run=dict(scan_output["mainline_run"]),
            obligation_attempt_ledger=dict(scan_output["obligation_attempt_ledger"]),
            policy_identity=execution_policy_identity,
            fixture_governance=governance,
            process_boundary=process_boundary,
            trusted_observations=trusted_observations,
            additional_request_attempts=additional_request_attempts,
            signing_key=signing_key,
        )
        (checkpoint_dir / "attestation_error.txt").unlink(missing_ok=True)
    except ExecutionAttestationError as exc:
        (checkpoint_dir / "attestation_error.txt").write_text(
            f"{type(exc).__name__}:{exc}\n", encoding="utf-8"
        )
        execution_attestation = None
        print(f"attestation_failed: {exc}", flush=True)

    manifest = load_evaluation_manifest(MANIFEST)
    receipt = evaluate_completed_scan(
        manifest,
        target_id,
        run_id=str(scan_output["run_id"]),
        policy_id=policy.policy_id,
        evaluation_mode=evaluation_mode,
        findings=list(scan_output["findings"]),
        delivery_occurrences=list(scan_output["delivery_occurrences"]),
        candidates=list(scan_output["candidates"]),
        pipeline_health=dict(scan_output["pipeline_health"]),
        operational_metrics=dict(scan_output["operational_metrics"]),
        fixture_governance=governance,
        trace_ledger=(
            dict(scan_output["trace_ledger"])
            if isinstance(scan_output.get("trace_ledger"), dict)
            else None
        ),
        obligation_attempt_ledger=dict(scan_output["obligation_attempt_ledger"]),
        mainline_run=dict(scan_output["mainline_run"]),
        formal_count_projection=dict(scan_output["formal_count_projection"]),
        formal_delivery_authority=dict(scan_output["formal_delivery_authority"]),
        canonical_defect_registry=dict(scan_output["canonical_defect_registry"]),
        defect_identity_consistency=dict(scan_output["defect_identity_consistency"]),
        evaluator_policy_identity=execution_policy_identity,
        process_boundary=process_boundary,
        execution_attestation=execution_attestation,
        additional_request_attempts=additional_request_attempts,
        receipt_signing_key=signing_key,
    )
    persist_evaluation_receipt(
        receipt,
        OUTPUT_ROOT / evaluation_id / "receipts",
        receipt_signing_key=signing_key,
    )
    report = aggregate_evaluation_receipts(
        manifest,
        [receipt],
        receipt_signing_key=signing_key,
    )
    report_path = (
        OUTPUT_ROOT
        / evaluation_id
        / "reports"
        / f"{policy.policy_id}.{evaluation_mode}.json"
    )
    persist_evaluation_report(
        report,
        report_path,
        receipt_signing_key=signing_key,
    )
    held_in = report.get("held_in") or {}
    summary = {
        "claim_status": report.get("claim_status"),
        "run_id": scan_output.get("run_id"),
        "attestation_sealed": execution_attestation is not None,
        "held_in_true_positives": held_in.get("true_positives"),
        "held_in_false_positives": held_in.get("false_positives"),
        "held_in_false_negatives": held_in.get("false_negatives"),
        "not_measured_targets": report.get("not_measured_targets"),
        "report_path": str(report_path),
    }
    (OUTPUT_ROOT / "resume_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if execution_attestation is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
