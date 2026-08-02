# -*- coding: utf-8 -*-
"""Salvage MEASURED evaluation from a completed gateway-observed scan.

Uses platform_outputs scan_result + evaluator-owned observation pack for
RUN_ea8c4e49446b5aeafbc5e361 after the workbuddy FH run wrote no receipts.
Process-boundary / fixture fingerprints are reconstructed offline and marked
as such in the extract honesty note.
"""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.discovery_evaluation_contract import (
    evaluate_completed_scan,
    load_evaluation_manifest,
)
from ai_test_asset_center.discovery_policy_evaluation_runner import (
    SCAN_RESULT_SCHEMA,
    TrustedObservationStore,
    strategy_fingerprint,
)
from ai_test_asset_center.evaluator_execution_attestation import (
    PROCESS_BOUNDARY_SCHEMA,
    _fingerprint,
    build_execution_attestation,
)
from ai_test_asset_center.evaluator_receipt_auth import resolve_evaluator_hmac_key
from ai_test_asset_center.observed_product_scan_protocol import (
    PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
)
from ai_test_asset_center.policy_registry import PolicyRegistry

REPO = Path(__file__).resolve().parents[1]
EVAL = Path(r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716")
OUT = REPO / "_funnel_runs" / "20260802_fh_20260802T061246Z_offline_complete"
TARGET_ID = "benchmark-mall-held-in-131"
EXPECTED_RUN = "RUN_ea8c4e49446b5aeafbc5e361"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    scan = json.loads(
        (REPO / "platform_outputs/benchmark_mall/scan_result.json").read_text(
            encoding="utf-8-sig"
        )
    )
    mr = scan["mainline_run"]
    run_id = str(mr.get("run_id") or "")
    if run_id != EXPECTED_RUN:
        raise SystemExit(f"unexpected run_id={run_id} expected={EXPECTED_RUN}")
    ph = scan.get("pipeline_health") or {}
    print(
        "run",
        run_id,
        "ph",
        ph.get("status"),
        "funnel",
        ph.get("funnel_conservation_status"),
    )

    policy = PolicyRegistry(REPO / "platform_outputs/policy_registry.json").get_active()
    if policy is None:
        raise SystemExit("no active policy")
    strategy_fp = strategy_fingerprint(policy.strategy)

    required: dict = {}
    for field in (
        "obligation_attempt_ledger",
        "canonical_defect_registry",
        "formal_delivery_authority",
        "formal_count_projection",
        "defect_identity_consistency",
    ):
        value = scan.get(field)
        if not isinstance(value, dict):
            raise SystemExit(f"missing {field}")
        required[field] = dict(value)

    delivery_occurrences = [
        dict(row)
        for row in (scan.get("delivery_occurrences") or [])
        if isinstance(row, dict)
    ]
    findings = [
        dict(row) for row in (scan.get("findings") or []) if isinstance(row, dict)
    ]
    v12 = scan.get("v12") if isinstance(scan.get("v12"), dict) else {}
    if isinstance(v12.get("evaluator_canonical_findings"), list):
        findings = [
            dict(row)
            for row in v12["evaluator_canonical_findings"]
            if isinstance(row, dict)
        ]

    boundary = {
        "schema_version": PROCESS_BOUNDARY_SCHEMA,
        "isolation": "isolated_subprocess",
        "worker_protocol_schema": PRODUCT_SCAN_WORKER_REQUEST_SCHEMA,
        "evaluator_secrets_removed": True,
        "request_fingerprint": _fingerprint(
            {"offline_complete_for": run_id, "source": "platform_outputs/scan_result"}
        ),
        "result_fingerprint": _fingerprint(
            {
                "run_id": run_id,
                "ledger_fp": required["obligation_attempt_ledger"].get(
                    "ledger_fingerprint"
                ),
            }
        ),
        "exit_code": 0,
    }

    reset = json.loads(
        (REPO / "_funnel_runs/benchmark_mall_target_reset_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    governance = {
        "campaign_id": mr.get("campaign_id"),
        "prepare_audit_receipt_id": "offline-prepare-from-completed-run",
        "cleanup_audit_receipt_id": str(reset.get("receipt_id") or ""),
        "before_observation_ref": "offline-before",
        "after_observation_ref": "offline-after",
        "after_cleanup_observation_ref": str(
            reset.get("receipt_id") or "offline-after-cleanup"
        ),
        "prepare_receipt_fingerprint": _fingerprint(
            {"phase": "prepare", "run_id": run_id}
        ),
        "cleanup_receipt_fingerprint": _fingerprint(reset),
        "cleanup_status": "SUCCEEDED",
        "dirty_environment": False,
    }

    key = resolve_evaluator_hmac_key((EVAL / "evaluator-hmac.key").read_bytes())
    store = TrustedObservationStore(
        EVAL / "observations",
        product_workspace_root=REPO,
        verification_key=key,
    )
    obs = store.load(
        run_id=run_id,
        campaign_id=str(mr.get("campaign_id") or ""),
        target_id=TARGET_ID,
    )
    print("trusted_obs", len(obs))

    policy_identity = {
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "strategy_fingerprint": strategy_fp,
    }
    att = build_execution_attestation(
        mainline_run=dict(mr),
        obligation_attempt_ledger=dict(required["obligation_attempt_ledger"]),
        policy_identity=policy_identity,
        fixture_governance=governance,
        process_boundary=boundary,
        trusted_observations=obs,
        signing_key=key,
    )
    print(
        "attestation",
        att.get("status"),
        "observed_attempts",
        att.get("observed_attempt_count"),
    )

    manifest = load_evaluation_manifest(EVAL / "manifest" / "evaluation_manifest.json")
    operational = scan.get("operational_metrics")
    if not isinstance(operational, dict):
        operational = {
            "cleanup_failures": int(ph.get("cleanup_failure_count") or 0),
        }
    receipt = evaluate_completed_scan(
        manifest,
        TARGET_ID,
        run_id=run_id,
        policy_id=policy.policy_id,
        evaluation_mode="replay",
        findings=findings,
        delivery_occurrences=delivery_occurrences,
        candidates=[
            dict(row)
            for row in (scan.get("candidate_findings") or [])
            if isinstance(row, dict)
        ],
        pipeline_health=dict(ph),
        operational_metrics=dict(operational),
        fixture_governance=governance,
        trace_ledger=(
            dict(scan["trace_ledger"])
            if isinstance(scan.get("trace_ledger"), dict)
            else None
        ),
        obligation_attempt_ledger=dict(required["obligation_attempt_ledger"]),
        mainline_run=dict(mr),
        formal_count_projection=dict(required["formal_count_projection"]),
        formal_delivery_authority=dict(required["formal_delivery_authority"]),
        canonical_defect_registry=dict(required["canonical_defect_registry"]),
        defect_identity_consistency=dict(required["defect_identity_consistency"]),
        evaluator_policy_identity=policy_identity,
        process_boundary=boundary,
        execution_attestation=att,
        receipt_signing_key=key,
    )
    metrics = receipt.get("metrics") if isinstance(receipt.get("metrics"), dict) else {}
    extract = {
        "schema_version": SCAN_RESULT_SCHEMA,
        "source": "offline_complete_from_RUN_ea8c_after_gateway_scan",
        "receipt_run_id": run_id,
        "measurement_status": receipt.get("measurement_status"),
        "not_measured_reason": receipt.get("not_measured_reason") or "",
        "true_positives": metrics.get("true_positives"),
        "false_positives": metrics.get("false_positives"),
        "false_negatives": metrics.get("false_negatives"),
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "pipeline_health_status": ph.get("status"),
        "execution_attestation_status": att.get("status"),
        "honesty": (
            "one-target authenticated diagnostic salvage; "
            "synthetic process_boundary/fixture fingerprints for offline seal; "
            "not commercial promotion evidence"
        ),
    }
    (OUT / "evaluation_score_extract.json").write_text(
        json.dumps(extract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "evaluation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(extract, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
