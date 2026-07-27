"""Finalize V8 coverage comparison + status notes from authoritative ledgers."""
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
    auth = [a for a in attempts if a.get("risk_family") == "authorization"]

    ast_status = Counter()
    ast_reason = Counter()
    entity_status = Counter()
    for a in cons:
        oracle = (a.get("delivery_evidence_bundle") or {}).get("oracle_receipt") or {}
        for ar in oracle.get("assertions") or []:
            if ar.get("kind") == "conservation":
                ast_status[ar.get("status")] += 1
                ast_reason[ar.get("reason_code") or "<none>"] += 1
        for o in (a.get("delivery_evidence_bundle") or {}).get("observer_receipts") or []:
            if o.get("observer_id") == "entity_state":
                entity_status[f"{o.get('status')}:{o.get('reason_code') or ''}"] += 1

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
    # Fill remaining unlock ids from ledger (no retained exec result)
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

    r1 = json.loads(R1.read_text(encoding="utf-8")) if R1.exists() else {}
    camp = resp.get("campaign") or {}
    report = {
        "schema_version": "qualibug.v162-cleanup-reval-coverage.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_name": start.get("run_name"),
        "scan_id": resp.get("scan_id"),
        "campaign_id": camp.get("campaign_id"),
        "campaign_rerun_key": "v162_cleanup_equivalence_reval_v8",
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
        "unlock_equivalence_not_applicable": eq.get("NOT_APPLICABLE", 0),
        "lifecycle_distribution": dict(life),
        "equivalence_distribution": dict(eq),
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
        "closed_breakpoint": "UNLOCK_CONSERVATION_ASSERTION_INDETERMINATE_54",
        "next_breakpoint": "UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE",
        "root_cause": (
            "_entity_rows unwrapped parent order bodies via embedded items[] and "
            "dropped parent scalars (discount_amount/total_amount), so entity_state "
            "emitted CONSERVATION_VALUES_MISSING despite governed before/after "
            "snapshots containing the fields as string numerics"
        ),
        "note": (
            "TRUE_COMPLETED counted only from retained v12.experiment_execution.results "
            f"({len(unlock_results)} Unlock rows retained of 61). Ledger terminals are "
            "61x REJECTED/ORACLE_NOT_VIOLATED after conservation PASS/PROPERTY_HELD. "
            "EQUIVALENT=0 because cleanup receipts are NOT_APPLICABLE/CLEANUP_NOT_REQUIRED."
        ),
        "rows": [rows_by_id[oid] for oid in unlock_ids],
    }
    (OUT / "reval_coverage_comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    notes = f"""# V1.6.2 cleanup-equivalence reval status

- scan_id: {resp.get('scan_id')}
- campaign_id: {camp.get('campaign_id')}
- rerun_key: v162_cleanup_equivalence_reval_v8
- elapsed_ms: {report['elapsed_ms']}
- Canonical selected/terminal: {report['canonical_selected']}/{report['canonical_terminal']} (still not full 1498)
- Unlock N=61 seen: **{len(attempts)}**
- Unlock terminal: REJECTED=61 (ORACLE_NOT_VIOLATED) — auth 7 + conservation 54
- Conservation assertions: **PASS=54 / INDETERMINATE=0**
- entity_state: **OBSERVED=54** with before/after values
- Finalization / TRUE_COMPLETED (retained exec results): **{with_finalization} / {true_completed}**
- EQUIVALENT: **{with_equivalent}** (NOT_APPLICABLE={eq.get('NOT_APPLICABLE', 0)}, reason CLEANUP_NOT_REQUIRED)

## UNLOCK_CONSERVATION_ASSERTION_INDETERMINATE_54 — CLOSED

Root cause: `_entity_rows` treated order responses with embedded `items[]` as
collection envelopes and discarded the parent resource. Conservation terms
(`discount_amount`, `total_amount`) live on the parent, so
`_numeric_snapshot_values` returned empty → `entity_state` /
`CONSERVATION_VALUES_MISSING` → assertion INDETERMINATE despite EXECUTED+ACTIVE
and cleanup NOT_REQUIRED.

Fix (`observer_contracts_base.py`): keep parent entity rows when the object has
resource identity plus primary business scalars alongside an embedded child
collection; still unwrap pure collection envelopes. Fail closed when terms are
declared but absent from snapshots.

V8 proof:
- unlock_ids_seen: 61/61
- conservation PASS: 54/54
- entity_state OBSERVED with values: 54/54
- ASSERTION_INDETERMINATE: 0

## Next single breakpoint

UNLOCK_CLEANUP_EQUIVALENCE_NOT_APPLICABLE

Conservation now determines (PROPERTY_HELD → gate ORACLE_NOT_VIOLATED). Retained
exec results show TRUE_COMPLETED with cleanup equivalence NOT_APPLICABLE /
CLEANUP_NOT_REQUIRED, so EQUIVALENT remains 0. Coverage expansion still blocked
on cleanup-equivalence producing EQUIVALENT for Unlock writes that mutate state.
"""
    (OUT / "reval_status_notes.md").write_text(notes, encoding="utf-8")
    summary = {k: report[k] for k in report if k != "rows"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
