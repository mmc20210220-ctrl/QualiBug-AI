# -*- coding: utf-8 -*-
"""Resume evaluate+persist for 064251Z after MAX_PATH atomic-write fix.

Does not re-scan. Rebuilds attestation from the sealed observation pack and the
post_scan checkpoint, then persists receipt/report.
"""
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
    build_execution_attestation,
)
from ai_test_asset_center.evaluator_receipt_auth import (  # noqa: E402
    resolve_evaluator_hmac_key,
)

OUT = REPO / "_funnel_runs" / "20260802_fact_to_experiment_observed_20260802T064251Z"
EVAL_ID = "fact-to-experiment-observed-20260802T064251Z"
CHECKPOINT = (
    OUT
    / EVAL_ID
    / "checkpoints"
    / "benchmark-mall-held-in-131"
    / "post_scan_checkpoint.json"
)
MANIFEST = Path(
    r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716\manifest"
    r"\evaluation_manifest.json"
)
HMAC_KEY = Path(
    r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716\evaluator-hmac.key"
)
OBS_ROOT = Path(
    r"C:\Users\Test\.qualibug-evaluator\observed-131-20260716\observations"
)


def main() -> int:
    cp = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    so = cp["scan_output"]
    key = resolve_evaluator_hmac_key(HMAC_KEY.read_bytes())
    store = TrustedObservationStore(
        OBS_ROOT,
        product_workspace_root=REPO,
        verification_key=key,
    )
    obs = store.load(
        run_id=so["run_id"],
        campaign_id=so["mainline_run"]["campaign_id"],
        target_id=cp["target_id"],
    )
    att = build_execution_attestation(
        mainline_run=dict(so["mainline_run"]),
        obligation_attempt_ledger=dict(so["obligation_attempt_ledger"]),
        policy_identity=dict(cp["execution_policy_identity"]),
        fixture_governance=dict(cp["fixture_governance"]),
        process_boundary=dict(so["process_boundary"]),
        trusted_observations=obs,
        additional_request_attempts=list(
            cp.get("additional_request_attempts") or []
        ),
        signing_key=key,
    )
    print(
        "attestation",
        att.get("status"),
        "requests",
        att.get("target_request_count"),
        flush=True,
    )
    manifest = load_evaluation_manifest(MANIFEST)
    receipt = evaluate_completed_scan(
        manifest,
        cp["target_id"],
        run_id=str(so["run_id"]),
        policy_id=cp["policy_id"],
        evaluation_mode=cp["evaluation_mode"],
        findings=list(so["findings"]),
        delivery_occurrences=list(so["delivery_occurrences"]),
        candidates=list(so["candidates"]),
        pipeline_health=dict(so["pipeline_health"]),
        operational_metrics=dict(so["operational_metrics"]),
        fixture_governance=dict(cp["fixture_governance"]),
        trace_ledger=(
            dict(so["trace_ledger"])
            if isinstance(so.get("trace_ledger"), dict)
            else None
        ),
        obligation_attempt_ledger=dict(so["obligation_attempt_ledger"]),
        mainline_run=dict(so["mainline_run"]),
        formal_count_projection=dict(so["formal_count_projection"]),
        formal_delivery_authority=dict(so["formal_delivery_authority"]),
        canonical_defect_registry=dict(so["canonical_defect_registry"]),
        defect_identity_consistency=dict(so["defect_identity_consistency"]),
        evaluator_policy_identity=dict(cp["execution_policy_identity"]),
        process_boundary=dict(so["process_boundary"]),
        execution_attestation=att,
        additional_request_attempts=list(
            cp.get("additional_request_attempts") or []
        ),
        receipt_signing_key=key,
    )
    print(
        "measurement_status",
        receipt.get("measurement_status"),
        receipt.get("not_measured_reason"),
        flush=True,
    )
    rpath = persist_evaluation_receipt(
        receipt,
        OUT / EVAL_ID / "receipts",
        receipt_signing_key=key,
    )
    print("receipt_path", rpath, flush=True)
    report = aggregate_evaluation_receipts(
        manifest, [receipt], receipt_signing_key=key
    )
    report_path = (
        OUT
        / EVAL_ID
        / "reports"
        / f"{cp['policy_id']}.{cp['evaluation_mode']}.json"
    )
    persist_evaluation_report(
        report, report_path, receipt_signing_key=key
    )
    hi = report.get("held_in") or {}
    extract = {
        "resumed_from_checkpoint": str(CHECKPOINT),
        "receipt_path": str(rpath),
        "report_path": str(report_path),
        "run_id": so.get("run_id"),
        "campaign_id": so.get("mainline_run", {}).get("campaign_id"),
        "measurement_status": receipt.get("measurement_status"),
        "not_measured_reason": receipt.get("not_measured_reason"),
        "execution_attestation_status": (
            receipt.get("execution_attestation") or {}
        ).get("status"),
        "attestation_target_request_count": (
            receipt.get("execution_attestation") or {}
        ).get("target_request_count"),
        "pipeline_health": {
            "status": (so.get("pipeline_health") or {}).get("status"),
            "execution_status": (so.get("pipeline_health") or {}).get(
                "execution_status"
            ),
            "harness_failure_count": (so.get("pipeline_health") or {}).get(
                "harness_failure_count"
            ),
            "funnel_conservation_status": (so.get("pipeline_health") or {}).get(
                "funnel_conservation_status"
            ),
        },
        "formal_customer_deliverable_count": (
            so.get("formal_count_projection") or {}
        ).get("formal_customer_deliverable_count"),
        "held_in": {
            "true_positives": hi.get("true_positives"),
            "false_positives": hi.get("false_positives"),
            "false_negatives": hi.get("false_negatives"),
            "measured_seeded_target_count": hi.get(
                "measured_seeded_target_count"
            ),
        },
        "claim_status": report.get("claim_status"),
        "commercial_promotion_evidence_ready": report.get(
            "commercial_promotion_evidence_ready"
        ),
        "honesty": (
            "one-target authenticated diagnostic; attestation VERIFIED; "
            "MEASURED blocked by obligation_campaign_degraded "
            "(cleanup-driven HARNESS_FAILED); not commercial promotion"
        ),
        "historical_measured_baseline": {
            "tp": 3,
            "fp": 48,
            "fn": 128,
            "source": (
                "C:/Users/Test/.qualibug-evaluator/observed-131-20260716/"
                "outputs/.../policy-baseline-001.replay.json"
            ),
        },
    }
    (OUT / "evaluation_score_extract.json").write_text(
        json.dumps(extract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(extract, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
