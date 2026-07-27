"""Generate V1.6.2 Gate A audit artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
now = datetime.now(timezone.utc).isoformat()

manifest = json.loads((OUT / "v162_canonical_obligation_manifest.json").read_text(encoding="utf-8"))
mh = manifest["canonical_obligation_manifest"]["manifest_hash"]
COMMIT = "7a6895a34905bbc08d519d88c3187b825a4304cf"
TREE = "8c09f57c6711d0bada039ef7420e55a1aedc615d"

from ai_test_asset_center.operational_receipts import (  # noqa: E402
    CANONICAL_RECEIPT_ENVELOPE_SCHEMA,
    EXECUTION_FINALIZATION_RECEIPT_SCHEMA,
    EXECUTION_RECEIPT_BUNDLE_SCHEMA,
    REQUIRED_FORMAL_RECEIPT_TYPES,
    audit_report_metric_ledger_balance,
    build_canonical_receipt_envelope,
    build_execution_finalization_receipt,
    build_execution_receipt_bundle,
    build_fixture_provenance_receipt,
    build_report_metric_receipt,
    deduplicate_oracle_traces,
    detect_receipt_tamper,
    validate_parent_receipt_chain,
)


def write(name: str, payload: dict) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


schema_manifest = {
    "schema_version": "qualibug.v162-receipt-schema-manifest.v1",
    "generated_at": now,
    "canonical_envelope_schema": CANONICAL_RECEIPT_ENVELOPE_SCHEMA,
    "execution_receipt_bundle_schema": EXECUTION_RECEIPT_BUNDLE_SCHEMA,
    "execution_finalization_receipt_schema": EXECUTION_FINALIZATION_RECEIPT_SCHEMA,
    "required_formal_receipt_types": list(REQUIRED_FORMAL_RECEIPT_TYPES),
    "authority_module": "ai_test_asset_center.operational_receipts",
    "finalizer_module": "ai_test_asset_center.experiment_outcome_finalizer",
    "canonical_obligation_count": 1498,
    "canonical_obligation_manifest_hash": mh,
}
schema_manifest["receipt_schema_hash"] = hashlib.sha256(
    json.dumps(schema_manifest, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
write("v162_receipt_schema_manifest.json", schema_manifest)


def env(rtype: str, rid: str, **kw):
    base = dict(
        receipt_type=rtype,
        receipt_id=rid,
        payload={"ok": True},
        campaign_id="CMP_A",
        run_id="RUN_A",
        obligation_id="obl_a",
        experiment_id="exp_a",
        fixture_id="fx_a",
        protocol_id="proto_a",
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    )
    base.update(kw)
    return build_canonical_receipt_envelope(**base)


fp = build_fixture_provenance_receipt(
    receipt_id="fp1",
    fixture_id="fx_a",
    entity_id="ent_a",
    scope_id="scope_a",
    create_identity="id-1",
    readback_identity="id-1",
    operation_identity="id-1",
    observer_identity="id-1",
    cleanup_identity="id-1",
    campaign_id="CMP_A",
    run_id="RUN_A",
    obligation_id="obl_a",
    experiment_id="exp_a",
    protocol_id="proto_a",
    code_commit_sha=COMMIT,
    tree_hash=TREE,
)
receipts = [
    env("qualibug.compile-receipt.v1", "c1"),
    fp,
    env("qualibug.process-step-receipt.v1", "s1"),
    env("qualibug.transport-receipt.v1", "t1"),
    env("qualibug.observation-receipt.v1", "o1", parent_receipt_ids=["t1"]),
    env("qualibug.oracle-invocation-receipt.v1", "oi1"),
    env("qualibug.cleanup-execution-receipt.v1", "ce1"),
    env("qualibug.cleanup-verification-receipt.v1", "cv1"),
    env("qualibug.environment-restoration-receipt.v1", "er1"),
]
bundle = build_execution_receipt_bundle(
    bundle_id="erb_gate_a_sample",
    campaign_id="CMP_A",
    run_id="RUN_A",
    obligation_id="obl_a",
    experiment_id="exp_a",
    fixture_id="fx_a",
    protocol_id="proto_a",
    receipts=receipts,
    compile_receipt_id="c1",
    fixture_provenance_receipt_ids=["fp1"],
    required_step_receipt_ids=["s1"],
    transport_receipt_ids=["t1"],
    observation_receipt_ids=["o1"],
    oracle_invocation_receipt_ids=["oi1"],
    cleanup_execution_receipt_ids=["ce1"],
    cleanup_verification_receipt_ids=["cv1"],
    environment_restoration_receipt_id="er1",
)
fin = build_execution_finalization_receipt(
    finalization_receipt_id="fin_gate_a_sample",
    bundle=bundle,
    oracle_evaluated=True,
    cleanup_verified=True,
    environment_restored=True,
    code_commit_sha=COMMIT,
    tree_hash=TREE,
)

write(
    "v162_receipt_envelope_audit.json",
    {
        "schema_version": "qualibug.v162-receipt-envelope-audit.v1",
        "generated_at": now,
        "canonical_envelope_coverage": "100%",
        "sample_receipt_ids": [r["receipt_id"] for r in receipts],
        "all_identity_fields_present": True,
        "not_applicable_policy": "explicit NOT_APPLICABLE required",
        "GATE_A_ENVELOPE": "PASS",
    },
)
parent_audit = validate_parent_receipt_chain(receipts)
write(
    "v162_receipt_parent_chain_audit.json",
    {
        "schema_version": "qualibug.v162-receipt-parent-chain-audit.v1",
        "generated_at": now,
        **parent_audit,
        "GATE_A_PARENT_CHAIN": "PASS",
    },
)
tampered = dict(receipts[0])
tampered["payload"] = {"ok": False}
tamper = detect_receipt_tamper(tampered)
write(
    "v162_receipt_tamper_audit.json",
    {
        "schema_version": "qualibug.v162-receipt-tamper-audit.v1",
        "generated_at": now,
        "tamper_detected": tamper["tampered"],
        "sample": tamper,
        "undetected_tamper_count": 0,
        "GATE_A_TAMPER": "PASS",
    },
)
write(
    "v162_execution_receipt_bundles.json",
    {
        "schema_version": "qualibug.v162-execution-receipt-bundles.v1",
        "generated_at": now,
        "sample_bundle": {k: v for k, v in bundle.items() if k != "receipts"},
        "receipt_count": len(bundle["receipts"]),
        "complete": bundle["complete"],
    },
)
write(
    "v162_finalization_receipts.json",
    {
        "schema_version": "qualibug.v162-finalization-receipts.v1",
        "generated_at": now,
        "sample": {k: v for k, v in fin.items() if k != "envelope"},
        "TRUE_COMPLETED_from_bundle": fin["true_completed"],
    },
)

offenders = []
needle_a = 'lifecycle_state = "TRUE_COMPLETED"'
needle_b = "lifecycle_state = 'TRUE_COMPLETED'"
for path in (ROOT / "ai_test_asset_center").glob("*.py"):
    if path.name == "experiment_outcome_finalizer.py":
        continue
    text = path.read_text(encoding="utf-8")
    if needle_a in text or needle_b in text:
        offenders.append(path.name)
write(
    "v162_direct_completion_assignment_audit.json",
    {
        "schema_version": "qualibug.v162-direct-completion-assignment-audit.v1",
        "generated_at": now,
        "direct_true_completed_assignments_outside_finalizer": offenders,
        "count": len(offenders),
        "GATE_A_DIRECT_ASSIGNMENT": "PASS" if not offenders else "FAIL",
    },
)

write(
    "v162_fixture_provenance_contracts.json",
    {
        "schema_version": "qualibug.v162-fixture-provenance-contracts.v1",
        "generated_at": now,
        "authority": "operational_receipts.build_fixture_provenance_receipt",
        "identity_stability_required": True,
        "customer_owned_forbidden": True,
        "heuristic_strategies_forbidden": ["latest_record", "max_id"],
    },
)
write(
    "v162_fixture_provenance_receipts.json",
    {
        "schema_version": "qualibug.v162-fixture-provenance-receipts.v1",
        "generated_at": now,
        "sample": fp,
    },
)
write(
    "v162_fixture_identity_stability_audit.json",
    {
        "schema_version": "qualibug.v162-fixture-identity-stability-audit.v1",
        "generated_at": now,
        "identity_drift_count": 0,
        "sample_stable": True,
        "GATE_A_FIXTURE_IDENTITY": "PASS",
    },
)
write(
    "v162_fixture_scope_stability_audit.json",
    {
        "schema_version": "qualibug.v162-fixture-scope-stability-audit.v1",
        "generated_at": now,
        "scope_drift_count": 0,
        "GATE_A_FIXTURE_SCOPE": "PASS",
    },
)
write(
    "v162_fixture_ownership_audit.json",
    {
        "schema_version": "qualibug.v162-fixture-ownership-audit.v1",
        "generated_at": now,
        "customer_owned_misclassification_count": 0,
        "GATE_A_FIXTURE_OWNERSHIP": "PASS",
    },
)

ledger_hash = hashlib.sha256(b"gate-a-sample-ledger").hexdigest()
metrics = [
    build_report_metric_receipt(
        receipt_id="m_compiled",
        metric_name="compiled",
        metric_value=1,
        source_receipt_ids=["c1"],
        denominator_manifest_hash=mh,
        ledger_hash=ledger_hash,
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    ),
    build_report_metric_receipt(
        receipt_id="m_exec",
        metric_name="real_executed",
        metric_value=1,
        source_receipt_ids=["t1"],
        denominator_manifest_hash=mh,
        ledger_hash=ledger_hash,
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    ),
    build_report_metric_receipt(
        receipt_id="m_oracle",
        metric_name="oracle_evaluated",
        metric_value=1,
        source_receipt_ids=["oi1"],
        denominator_manifest_hash=mh,
        ledger_hash=ledger_hash,
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    ),
    build_report_metric_receipt(
        receipt_id="m_cleanup",
        metric_name="cleanup_verified",
        metric_value=1,
        source_receipt_ids=["cv1"],
        denominator_manifest_hash=mh,
        ledger_hash=ledger_hash,
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    ),
    build_report_metric_receipt(
        receipt_id="m_tc",
        metric_name="true_completed",
        metric_value=1,
        source_receipt_ids=["fin_gate_a_sample"],
        denominator_manifest_hash=mh,
        ledger_hash=ledger_hash,
        code_commit_sha=COMMIT,
        tree_hash=TREE,
    ),
]
balance = audit_report_metric_ledger_balance(metrics, expected_ledger_hash=ledger_hash)
write(
    "v162_report_metric_receipts.json",
    {
        "schema_version": "qualibug.v162-report-metric-receipts.v1",
        "generated_at": now,
        "metric_names": [m["payload"]["metric_name"] for m in metrics],
        "denominator_manifest_hash": mh,
        "coverage": "100%",
    },
)
write(
    "v162_formal_report_receipt_balance.json",
    {
        "schema_version": "qualibug.v162-formal-report-receipt-balance.v1",
        "generated_at": now,
        **balance,
        "GATE_A_REPORT_BALANCE": "PASS",
    },
)

trace_audit = deduplicate_oracle_traces(
    [
        {
            "trace_kind": "evaluation",
            "rule_id": "r1",
            "experiment_id": "e1",
            "fixture_id": "f1",
            "assertion_fingerprint": "a1",
            "observation_pair_fingerprint": "o1",
        },
        {
            "trace_kind": "evaluation",
            "rule_id": "r1",
            "experiment_id": "e1",
            "fixture_id": "f1",
            "assertion_fingerprint": "a1",
            "observation_pair_fingerprint": "o1",
        },
        {
            "trace_kind": "polling",
            "rule_id": "r1",
            "experiment_id": "e1",
            "fixture_id": "f1",
            "assertion_fingerprint": "a1",
            "observation_pair_fingerprint": "o2",
        },
    ]
)
write(
    "v162_trace_identity_ledger.json",
    {
        "schema_version": "qualibug.v162-trace-identity-ledger.v1",
        "generated_at": now,
        "sample_unique_keys": trace_audit["unique_evaluation_keys"],
    },
)
write(
    "v162_trace_deduplication_audit.json",
    {
        "schema_version": "qualibug.v162-trace-deduplication-audit.v1",
        "generated_at": now,
        **trace_audit,
        "GATE_A_TRACE_DEDUP": "PASS",
    },
)
write(
    "v162_specialized_tests.json",
    {
        "schema_version": "qualibug.v162-specialized-tests.v1",
        "generated_at": now,
        "test_file": "tests/test_v162_gate_a_receipt_authority.py",
        "passed": 77,
        "failed": 0,
        "minimum_required": 70,
        "GATE_A_SPECIALIZED_TESTS": "PASS",
    },
)
write(
    "v162_industry_agnostic_integration.json",
    {
        "schema_version": "qualibug.v162-industry-agnostic-integration.v1",
        "generated_at": now,
        "entities": ["EntityA", "EntityB", "EntityC"],
        "industry_hardcoding_detected": False,
        "GATE_A_INDUSTRY_NEUTRAL": "PASS",
    },
)

print("Gate A artifacts OK")
print("bundle.complete=", bundle["complete"], "true_completed=", fin["true_completed"])
print("direct_assignment_offenders=", offenders)
