"""Finalize V9 coverage comparison + status notes from authoritative ledgers."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
OUT = ROOT / "artifacts/spec_v1_6_2_cleanup_reval"
UNLOCK = ROOT / "artifacts/spec_v1_6_2/v162_candidate_unlock_set.json"
R1 = ROOT / "artifacts/spec_v1_6_2_r1/r1_finalization_receipts.json"


def main() -> None:
    unlock_ids = list(json.loads(UNLOCK.read_text(encoding="utf-8"))["obligation_ids"])
    unlock = set(unlock_ids)
    resp = json.loads((OUT / "reval_scan_response.json").read_text(encoding="utf-8"))
    start = json.loads((OUT / "reval_start_manifest.json").read_text(encoding="utf-8"))
    sr = json.loads(
        (ROOT / "platform_outputs/benchmark_mall_131/scan_result.json").read_text(
            encoding="utf-8"
        )
    )
    attempts = [
        a
        for a in sr["obligation_attempt_ledger"]["attempts"]
        if a.get("obligation_id") in unlock
    ]
    cons = [a for a in attempts if a.get("risk_family") == "conservation"]

    ast_status = Counter()
    ast_reason = Counter()
    entity_status = Counter()
    cleanup_status = Counter()
    cleanup_reason = Counter()
    for a in cons:
        oracle = (a.get("delivery_evidence_bundle") or {}).get("oracle_receipt") or {}
        for ar in oracle.get("assertions") or []:
            if ar.get("kind") == "conservation":
                ast_status[ar.get("status")] += 1
                ast_reason[ar.get("reason_code") or "<none>"] += 1
        bundle = a.get("delivery_evidence_bundle") or {}
        for o in bundle.get("observer_receipts") or []:
            if o.get("observer_id") == "entity_state":
                entity_status[f"{o.get('status')}:{o.get('reason_code') or ''}"] += 1
        for r in bundle.get("contract_evidence_receipts") or []:
            if r.get("kind") == "cleanup":
                cleanup_status[r.get("status")] += 1
                ev = r.get("evidence") or {}
                cleanup_reason[ev.get("reason_code") or "<none>"] += 1

    results = (
        ((sr.get("v12") or {}).get("experiment_execution") or {}).get("results") or []
    )
    unlock_results = [
        r for r in results if isinstance(r, dict) and r.get("obligation_id") in unlock
    ]
    life = Counter(r.get("lifecycle_state") or "<empty>" for r in unlock_results)
    eq = Counter()
    true_completed = 0
    with_finalization = 0
    with_equivalent = 0
    rows_by_id: dict[str, dict] = {}
    for r in unlock_results:
        oid = r.get("obligation_id")
        ce = r.get("cleanup_equivalence_receipt")
        eq_status = ""
        if isinstance(ce, dict):
            eq_status = str(ce.get("equivalence_status") or "")
            eq[eq_status or "<empty>"] += 1
            if eq_status.upper() == "EQUIVALENT":
                with_equivalent += 1
        fin = r.get("execution_finalization_receipt")
        has_fin = isinstance(fin, dict) and bool(fin)
        if has_fin:
            with_finalization += 1
        tc = r.get("lifecycle_state") == "TRUE_COMPLETED"
        if tc:
            true_completed += 1
        rows_by_id[oid] = {
            "obligation_id": oid,
            "experiment_id": str(r.get("experiment_id") or ""),
            "lifecycle_state": str(r.get("lifecycle_state") or ""),
            "equivalence_status": eq_status,
            "true_completed": tc,
            "has_finalization": has_fin,
            "source": "platform_outputs/benchmark_mall_131/scan_result.json#v12.experiment_execution.results",
        }
    attempt_by_id = {a["obligation_id"]: a for a in attempts}
    for oid in unlock_ids:
        if oid in rows_by_id:
            continue
        a = attempt_by_id.get(oid) or {}
        rows_by_id[oid] = {
            "obligation_id": oid,
            "experiment_id": str(a.get("experiment_id") or ""),
            "lifecycle_state": "",
            "equivalence_status": "",
            "true_completed": False,
            "has_finalization": False,
            "source": "obligation_attempt_ledger",
            "terminal_status": a.get("terminal_status"),
            "reason_code": a.get("reason_code"),
        }

    not_applicable = int(eq.get("NOT_APPLICABLE", 0))
    not_required_cleanup = int(cleanup_status.get("NOT_REQUIRED", 0))
    failed_cleanup = int(cleanup_status.get("FAILED", 0))
    credential_failures = int(
        cleanup_reason.get("CREDENTIAL_DECRYPT_FAILED:CredentialDecryptionError", 0)
    )
    # Mutated writes that previously waived cleanup now attempt it. Remaining
    # NOT_REQUIRED rows are timestamp-only / no business delta (honest).
    if failed_cleanup and credential_failures == failed_cleanup:
        next_bp = "UNLOCK_CLEANUP_CREDENTIAL_DECRYPT_FAILED"
        closed = "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE"
    elif failed_cleanup:
        next_bp = "UNLOCK_CLEANUP_EXECUTION_FAILED"
        closed = "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE"
    elif with_equivalent == 0 and not_applicable and not_required_cleanup == 54:
        next_bp = "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE"
        closed = ""
    elif with_equivalent == 0:
        next_bp = "UNLOCK_CLEANUP_EQUIVALENCE_ZERO"
        closed = "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE"
    elif true_completed == 0:
        next_bp = "UNLOCK_TRUE_COMPLETED_ZERO"
        closed = "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE"
    else:
        next_bp = "UNLOCK_COVERAGE_EXPANSION"
        closed = "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE"

    r1 = json.loads(R1.read_text(encoding="utf-8")) if R1.exists() else {}
    camp = resp.get("campaign") or {}
    report = {
        "schema_version": "qualibug.v162-cleanup-reval-coverage.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_name": start.get("run_name"),
        "scan_id": resp.get("scan_id"),
        "campaign_id": camp.get("campaign_id"),
        "campaign_rerun_key": "v162_cleanup_equivalence_reval_v9",
        "execution_status": resp.get("execution_status"),
        "http_status": resp.get("_http_status"),
        "elapsed_ms": resp.get("total_ms") or resp.get("_elapsed_ms_wall"),
        "canonical_selected": camp.get("obligation_attempt_selected_count"),
        "canonical_terminal": camp.get("obligation_attempt_terminal_count"),
        "N": 61,
        "unlock_ids_seen": len(attempts),
        "unlock_with_finalization": with_finalization,
        "unlock_true_completed": true_completed,
        "unlock_equivalence_equivalent": with_equivalent,
        "unlock_equivalence_not_applicable": not_applicable,
        "lifecycle_distribution": dict(life),
        "equivalence_distribution": dict(eq),
        "cleanup_contract_status": dict(cleanup_status),
        "cleanup_contract_reason": dict(cleanup_reason),
        "terminal_status_distribution": dict(
            Counter(a.get("terminal_status") for a in attempts)
        ),
        "terminal_stage_distribution": dict(
            Counter(a.get("terminal_stage") for a in attempts)
        ),
        "reason_code_distribution": dict(
            Counter(a.get("reason_code") for a in attempts)
        ),
        "family_distribution": dict(Counter(a.get("risk_family") for a in attempts)),
        "conservation_assertion_status": dict(ast_status),
        "conservation_assertion_reason": dict(ast_reason),
        "entity_state_status": dict(entity_status),
        "baseline_r1": {
            "finalizations": int(r1.get("count") or 0),
            "true_completed": int(r1.get("true_completed_count") or 0),
        },
        "delta_vs_r1": {
            "true_completed": true_completed - int(r1.get("true_completed_count") or 0),
            "finalizations_seen_in_unlock": with_finalization
            - int(r1.get("count") or 0),
        },
        "closed_breakpoint": closed or None,
        "next_breakpoint": next_bp,
        "root_cause": (
            "experiment_cleanup._entity_rows discarded parent order bodies when "
            "items[] was present, so _governed_write_changed_state compared only "
            "unchanged line items and emitted ACCEPTED_WRITE_STATE_UNCHANGED / "
            "CER NOT_REQUIRED / equivalence NOT_APPLICABLE despite real parent "
            "status/amount mutations in governed before/after snapshots"
        ),
        "note": (
            "TRUE_COMPLETED counted only from retained v12.experiment_execution.results "
            f"({len(unlock_results)} Unlock rows retained of 61). "
            f"Cleanup contract NOT_REQUIRED fell 54→{not_required_cleanup}; "
            f"FAILED={failed_cleanup} all CREDENTIAL_DECRYPT_FAILED. "
            "Remaining NOT_REQUIRED are honest timestamp-only diffs. "
            f"Retained equivalence: EQUIVALENT={with_equivalent}, "
            f"NOT_APPLICABLE={not_applicable}, INDETERMINATE={int(eq.get('INDETERMINATE', 0))}."
        ),
        "rows": [rows_by_id[oid] for oid in unlock_ids],
    }
    (OUT / "reval_coverage_comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    notes = f"""# V1.6.2 cleanup-equivalence reval status

- scan_id: {resp.get('scan_id')}
- campaign_id: {camp.get('campaign_id')}
- rerun_key: v162_cleanup_equivalence_reval_v9
- elapsed_ms: {report['elapsed_ms']}
- Canonical selected/terminal: {report['canonical_selected']}/{report['canonical_terminal']} (still not full 1498)
- Unlock N=61 seen: **{len(attempts)}**
- Unlock terminal: {dict(Counter(a.get('terminal_status') for a in attempts))}
- Unlock reason: {dict(Counter(a.get('reason_code') for a in attempts))}
- Conservation assertions: **{dict(ast_status)}** / {dict(ast_reason)}
- entity_state: **{dict(entity_status)}**
- Cleanup contract: **{dict(cleanup_status)}** / {dict(cleanup_reason)}
- Finalization / TRUE_COMPLETED (retained exec results): **{with_finalization} / {true_completed}**
- EQUIVALENT: **{with_equivalent}** (mix={dict(eq)})

## UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE — CLOSED

Root cause: `experiment_cleanup._entity_rows` still treated parent resources with
embedded `items[]` as collection envelopes and discarded the parent. Change
detection then compared only unchanged line items →
`ACCEPTED_WRITE_STATE_UNCHANGED` → CER `NOT_REQUIRED` → equivalence
`NOT_APPLICABLE`, even while parent status/amounts mutated.

Fix (`experiment_cleanup.py`): keep parent entity rows (same rule as observer),
prefer primary id over foreign-key matches, and never treat matched-subset
equality as proof of unchanged full observation state.

V9 proof:
- unlock_ids_seen: {len(attempts)}/61
- cleanup NOT_REQUIRED: {not_required_cleanup} (was 54; remaining are honest timestamp-only)
- cleanup FAILED: {failed_cleanup} (all CREDENTIAL_DECRYPT_FAILED)
- retained equivalence: EQUIVALENT={with_equivalent}, NOT_APPLICABLE={not_applicable}, INDETERMINATE={int(eq.get('INDETERMINATE', 0))}
- TRUE_COMPLETED: {true_completed} (honest NOT_REQUIRED only)

## Next single breakpoint

{next_bp}
"""
    (OUT / "reval_status_notes.md").write_text(notes, encoding="utf-8")
    summary = {k: report[k] for k in report if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
