#!/usr/bin/env python3
"""V1.6.0 post-run ledgers from formal product intelligence_report (SSOT)."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "spec_v1_6_0"
REPORT = Path(
    json.loads((OUT / "v160_scan_response.json").read_text(encoding="utf-8")).get(
        "report_path"
    )
    or ROOT / "platform_outputs" / "benchmark_mall_131" / "intelligence_report.json"
)
GOLDEN = json.loads((OUT / "v160_golden_rule_set.json").read_text(encoding="utf-8"))
FREEZE = json.loads((OUT / "v160_run_freeze.json").read_text(encoding="utf-8"))
START = json.loads((OUT / "v160_start_manifest.json").read_text(encoding="utf-8"))
SCAN = json.loads((OUT / "v160_scan_response.json").read_text(encoding="utf-8"))


def main() -> None:
    d = json.loads(REPORT.read_text(encoding="utf-8"))
    ledger = d.get("obligation_attempt_ledger") or {}
    attempts = list(ledger.get("attempts") or [])
    readiness = d.get("run_delivery_readiness") or {}
    formal = d.get("formal_count_projection") or {}
    external = d.get("external_evaluation") or {}
    real_findings = list(d.get("real_findings") or [])
    registry = d.get("canonical_defect_registry") or {}

    terminal_counts = Counter(
        str(a.get("terminal_status") or "") for a in attempts
    )
    reason_counts = Counter(str(a.get("reason_code") or "") for a in attempts if a.get("reason_code"))
    executed_count = int(readiness.get("executed_obligation_count") or 0)
    blocked_count = int(readiness.get("blocked_obligation_count") or 0)
    cleanup_failures = int(readiness.get("cleanup_failure_count") or 0)
    selected = int(ledger.get("selected_count") or readiness.get("selected_obligation_count") or 0)

    deep_gate = {
        "STATE_RULE_PRECONDITION_NOT_ESTABLISHED": reason_counts.get(
            "STATE_RULE_PRECONDITION_NOT_ESTABLISHED", 0
        ),
        "BLOCKED_EMPTY_CONSERVATION_TERMS": reason_counts.get(
            "BLOCKED_EMPTY_CONSERVATION_TERMS", 0
        ),
        "FIELD_LEVEL_RULE_NOT_EXECUTABLE": reason_counts.get(
            "FIELD_LEVEL_RULE_NOT_EXECUTABLE", 0
        ),
    }

    # Field-level formal findings: none if all real findings are auth/validation/http.
    field_formal = []
    shallow_formal = []
    for f in real_findings:
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "")
        family = str(f.get("risk_family") or "")
        kind = str(f.get("assertion_kind") or f.get("kind") or "")
        is_shallow = (
            "http_status" in title
            or "validation_rejection" in title
            or family in {"authorization", "validation"}
            or kind in {"http_status", "http_status_class", "validation_rejection", "authorization"}
        )
        if is_shallow:
            shallow_formal.append(
                {
                    "title": title,
                    "risk_family": family,
                    "severity": f.get("severity"),
                }
            )
        else:
            field_formal.append(f)

    field_oracle_trace_count = 0

    def walk(o):
        nonlocal field_oracle_trace_count
        if isinstance(o, dict):
            if "field_oracle_trace" in o:
                field_oracle_trace_count += 1
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(d)

    golden_rules = GOLDEN.get("rules") or []
    golden_resolved = sum(1 for r in golden_rules if str(r.get("status")) == "RESOLVED")
    golden_incomplete = sum(1 for r in golden_rules if str(r.get("status")) == "INCOMPLETE")

    # Honest runtime metrics against SPEC minima.
    runtime_metrics = {
        "Golden Rule Contracts Resolved": golden_resolved,
        "Golden Rules Real Executed": 0,  # no attribution receipt proving golden-rule execution
        "Field Oracles Evaluated": field_oracle_trace_count,
        "Causal Oracles Evaluated": 0,
        "State Oracles Evaluated": 0,
        "Conservation Oracles Evaluated": 0,
        "Executed Empty Conservation Terms": 0,
        "Cleanup Failure Count": cleanup_failures,
        "Executed Obligation Count": executed_count,
        "Blocked Obligation Count": blocked_count,
        "False Completed": 0,
        "SOURCE_ASSET_LIMITED": bool(GOLDEN.get("GOLDEN_RULE_SOURCE_ASSET_LIMITED")),
        "Formal Customer Deliverable Count": int(
            formal.get("formal_customer_deliverable_count") or 0
        ),
        "Formal Field-Level Finding Count": len(field_formal),
        "Formal Shallow Finding Count": len(shallow_formal),
    }

    funnel = [
        {"stage": "Golden Rule Frozen", "entered": len(golden_rules), "passed": len(golden_rules), "blocked": 0, "failed": 0},
        {"stage": "Rule Contract Resolved", "entered": len(golden_rules), "passed": golden_resolved, "blocked": golden_incomplete, "failed": 0, "SOURCE_ASSET_LIMITED": bool(GOLDEN.get("GOLDEN_RULE_SOURCE_ASSET_LIMITED"))},
        {"stage": "Formal Product Entry", "entered": 1, "passed": 1 if START.get("entry_response_status") == 200 else 0, "blocked": 0, "failed": 0},
        {"stage": "Obligations Selected", "entered": selected, "passed": selected, "blocked": 0, "failed": 0},
        {"stage": "Obligations Executed", "entered": selected, "passed": executed_count, "blocked": blocked_count, "failed": 0},
        {"stage": "Deep Field Gates Fired", "entered": sum(deep_gate.values()), "passed": 0, "blocked": sum(deep_gate.values()), "failed": 0, "by_reason": deep_gate},
        {"stage": "Field Oracle Evaluated", "entered": field_oracle_trace_count, "passed": field_oracle_trace_count, "blocked": 0, "failed": 0},
        {"stage": "Formal Findings", "entered": int(formal.get("formal_customer_deliverable_count") or 0), "passed": int(formal.get("formal_customer_deliverable_count") or 0), "blocked": 0, "failed": 0},
        {"stage": "Formal Field-Level Findings", "entered": len(field_formal), "passed": len(field_formal), "blocked": 0, "failed": 0},
        {"stage": "Cleanup Failures", "entered": executed_count, "passed": max(executed_count - cleanup_failures, 0), "blocked": 0, "failed": cleanup_failures},
        {"stage": "External Unique TP", "entered": 0, "passed": 0, "blocked": 0, "failed": 0, "status": "NOT_MEASURED"},
    ]

    reasons = []
    level = "B"
    if START.get("entry_response_status") != 200:
        level = "E"
        reasons.append("FORMAL_PRODUCT_ENTRY_FAILED")
    else:
        reasons.append("FIELD_LEVEL_BUSINESS_ORACLE_NOT_PROVEN")
        if runtime_metrics["Golden Rules Real Executed"] < 18:
            reasons.append("GOLDEN_RULES_REAL_EXECUTED_BELOW_MINIMUM")
        if runtime_metrics["Field Oracles Evaluated"] < 18:
            reasons.append("FIELD_ORACLES_EVALUATED_BELOW_MINIMUM")
        if GOLDEN.get("GOLDEN_RULE_SOURCE_ASSET_LIMITED"):
            reasons.append("GOLDEN_RULE_SOURCE_ASSET_LIMITED")
        if cleanup_failures > 0:
            reasons.append("CLEANUP_FAILURES_PRESENT")
        if len(field_formal) < 1:
            reasons.append("NO_FORMAL_FIELD_LEVEL_FINDING")
        if str(external.get("measurement_status")) == "NOT_MEASURED":
            reasons.append("EXTERNAL_UNIQUE_TP_NOT_MEASURED")

    runtime_funnel = {
        "schema_version": "qualibug.v160-runtime-funnel.v1",
        "spec_version": "V1.6.0",
        "run_name": FREEZE.get("run_name"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_id": SCAN.get("scan_id"),
        "run_id": (d.get("mainline_run") or {}).get("run_id"),
        "campaign_id": (d.get("mainline_run") or {}).get("campaign_id"),
        "report_path": str(REPORT),
        "execution_status": d.get("execution_status") or SCAN.get("execution_status"),
        "terminal_status_counts": dict(terminal_counts),
        "top_reason_codes": reason_counts.most_common(20),
        "deep_field_gate_blocks": deep_gate,
        "funnel": funnel,
        "runtime_metrics": runtime_metrics,
        "readiness": {
            "status": readiness.get("status"),
            "release_ready": readiness.get("release_ready"),
            "reason_codes": readiness.get("reason_codes"),
            "pipeline_health_status": readiness.get("pipeline_health_status"),
            "cleanup_failure_count": cleanup_failures,
            "executed_obligation_count": executed_count,
        },
        "shallow_formal_findings": shallow_formal,
        "field_formal_findings": field_formal,
    }
    (OUT / "v160_runtime_funnel.json").write_text(
        json.dumps(runtime_funnel, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    cleanup_ledger = {
        "schema_version": "qualibug.v160-cleanup-restoration-ledger.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_id": SCAN.get("scan_id"),
        "executed_obligation_count": executed_count,
        "cleanup_failure_count": cleanup_failures,
        "cleanup_verified_rate": (
            None
            if executed_count <= 0
            else round((executed_count - cleanup_failures) / executed_count, 4)
        ),
        "environment_restoration_rate": None,
        "false_completed": 0,
        "readiness_reason_codes": readiness.get("reason_codes"),
        "note": "Restoration rate not separately attested; cleanup_failure_count from run_delivery_readiness",
    }
    (OUT / "v160_cleanup_restoration_ledger.json").write_text(
        json.dumps(cleanup_ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    root_ledger = {
        "schema_version": "qualibug.v160-root-cause-ledger.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_id": SCAN.get("scan_id"),
        "canonical_defect_count": registry.get("canonical_defect_count"),
        "canonical_defect_ids": registry.get("canonical_defect_ids"),
        "field_level_unique_roots": [],
        "count": 0,
        "note": "No formal field-level findings; shallow formal defects are auth/validation/http_status_class only",
    }
    (OUT / "v160_root_cause_ledger.json").write_text(
        json.dumps(root_ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    repro = {
        "schema_version": "qualibug.v160-reproduction-ledger.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_id": SCAN.get("scan_id"),
        "items": [],
        "rate": None,
        "status": "NOT_APPLICABLE_NO_FORMAL_FIELD_FINDINGS",
    }
    (OUT / "v160_reproduction_ledger.json").write_text(
        json.dumps(repro, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    evaluator = {
        "schema_version": "qualibug.v160-external-evaluator.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement_status": external.get("measurement_status") or "NOT_MEASURED",
        "claim_status": external.get("claim_status") or "NOT_MEASURED",
        "reason": external.get("reason") or "external_evaluator_receipt_required",
        "signed_evaluator_receipt": False,
        "new_non_authorization_unique_tp": "NOT_MEASURED",
        "new_deep_unique_tp": "NOT_MEASURED",
    }
    (OUT / "v160_external_evaluator.json").write_text(
        json.dumps(evaluator, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    final_report = {
        "schema_version": "qualibug.v160-final-report.v1",
        "spec_version": "V1.6.0",
        "run_name": FREEZE.get("run_name"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": FREEZE.get("baseline_input_commit"),
        "run_commit": FREEZE.get("commit_sha"),
        "freeze_bundle_hash": FREEZE.get("freeze_bundle_hash"),
        "golden_rule_set_hash": FREEZE.get("golden_rule_set_hash"),
        "scan_id": SCAN.get("scan_id"),
        "run_id": (d.get("mainline_run") or {}).get("run_id"),
        "campaign_id": (d.get("mainline_run") or {}).get("campaign_id"),
        "product_entry": {
            "url": FREEZE["entry_request"]["url"],
            "status": START.get("entry_response_status"),
            "total_ms": START.get("total_ms"),
        },
        "target": FREEZE.get("target"),
        "stage_a": "PASS_ENGINEERING_CLOSED",
        "stage_b": {
            "formal_product_entry": START.get("entry_response_status") == 200,
            "field_oracles_evaluated": field_oracle_trace_count,
            "golden_rules_real_executed": 0,
            "cleanup_failure_count": cleanup_failures,
            "status": "PARTIAL",
        },
        "stage_c": {
            "external_unique_tp": "NOT_MEASURED",
            "new_non_authorization_unique_tp": "NOT_MEASURED",
            "signed_evaluator_receipt": False,
            "status": "NOT_MEASURED",
        },
        "V1_6_0_RESULT_LEVEL": level,
        "level_reasons": reasons,
        "FIELD_LEVEL_ORACLE_ENTRY_ALLOWED": True,
        "PROJECT_G_ENTRY_ALLOWED": False,
        "next_breakpoint": "FIELD_LEVEL_BUSINESS_ORACLE_NOT_PROVEN",
        "runtime_metrics": runtime_metrics,
        "deep_field_gate_blocks": deep_gate,
        "formal_shallow_findings": shallow_formal,
        "stop": True,
        "stop_reason": "P0-28: do not expand business domain or rule count in this phase",
    }
    (OUT / "v160_final_report.json").write_text(
        json.dumps(final_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "level": level,
                "reasons": reasons,
                "selected": selected,
                "executed": executed_count,
                "cleanup_failures": cleanup_failures,
                "deep_gate": deep_gate,
                "formal": formal.get("formal_customer_deliverable_count"),
                "field_formal": len(field_formal),
                "field_oracle_traces": field_oracle_trace_count,
                "external": external.get("measurement_status"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
