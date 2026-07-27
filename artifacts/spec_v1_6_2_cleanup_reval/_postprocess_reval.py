"""Post-process cleanup-equivalence formal revalidation against Unlock Set=61."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
UNLOCK = ROOT / "artifacts" / "spec_v1_6_2" / "v162_candidate_unlock_set.json"
R1_BASELINE = ROOT / "artifacts" / "spec_v1_6_2_r1" / "r1_finalization_receipts.json"


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                yield from _walk(item)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    unlock_ids = list(_load_json(UNLOCK)["obligation_ids"])
    unlock_set = set(unlock_ids)
    assert len(unlock_set) == 61

    resp = _load_json(OUT / "reval_scan_response.json")
    scan_id = str(resp.get("scan_id") or "")
    camp = resp.get("campaign") if isinstance(resp.get("campaign"), dict) else {}
    start_manifest = {}
    start_path = OUT / "reval_start_manifest.json"
    if start_path.exists():
        try:
            start_manifest = _load_json(start_path)
        except Exception:
            start_manifest = {}
    run_name = str(
        start_manifest.get("run_name")
        or resp.get("run_name")
        or "V1_6_2_CLEANUP_EQUIVALENCE_ROOTCAUSE_REVAL"
    )

    # Prefer platform scan_result / intelligence artifacts for this scan_id.
    artifact_paths = []
    mall = ROOT / "platform_outputs" / "benchmark_mall_131"
    if mall.exists():
        for path in mall.rglob("*.json"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if scan_id and scan_id not in text and scan_id not in path.name:
                continue
            if any(
                token in text
                for token in (
                    "execution_finalization_receipt",
                    "cleanup_equivalence",
                    "TRUE_COMPLETED",
                    "lifecycle_state",
                )
            ):
                artifact_paths.append(path)

    records = []
    sources = [OUT / "reval_scan_response.json", *artifact_paths]
    for path in sources:
        try:
            payload = _load_json(path)
        except Exception:
            continue
        for row in _walk(payload):
            oid = str(row.get("obligation_id") or "")
            if oid not in unlock_set:
                continue
            finalization = row.get("execution_finalization_receipt")
            if not isinstance(finalization, dict):
                finalization = row.get("finalization") if isinstance(row.get("finalization"), dict) else {}
            lifecycle = str(
                row.get("lifecycle_state")
                or finalization.get("derived_terminal_status")
                or finalization.get("lifecycle_state")
                or ""
            )
            equiv = ""
            equiv_receipt = row.get("cleanup_equivalence_receipt")
            if isinstance(equiv_receipt, dict):
                equiv = str(equiv_receipt.get("equivalence_status") or "")
            if not equiv:
                equiv = str(row.get("cleanup_equivalence_status") or "")
            true_completed = bool(
                finalization.get("true_completed") is True
                or lifecycle == "TRUE_COMPLETED"
                or str(finalization.get("derived_terminal_status") or "") == "TRUE_COMPLETED"
            )
            records.append(
                {
                    "obligation_id": oid,
                    "experiment_id": str(row.get("experiment_id") or ""),
                    "lifecycle_state": lifecycle,
                    "equivalence_status": equiv,
                    "true_completed": true_completed,
                    "has_finalization": bool(finalization),
                    "source": str(path.relative_to(ROOT)),
                }
            )

    # Deduplicate by obligation_id preferring rows with finalization/true_completed.
    best: dict[str, dict] = {}
    for row in records:
        oid = row["obligation_id"]
        prev = best.get(oid)
        if prev is None:
            best[oid] = row
            continue
        score = (
            int(row["true_completed"]),
            int(row["has_finalization"]),
            int(bool(row["lifecycle_state"])),
            int(bool(row["equivalence_status"])),
        )
        prev_score = (
            int(prev["true_completed"]),
            int(prev["has_finalization"]),
            int(bool(prev["lifecycle_state"])),
            int(bool(prev["equivalence_status"])),
        )
        if score > prev_score:
            best[oid] = row

    lifecycle_counts = Counter(r["lifecycle_state"] or "<empty>" for r in best.values())
    equiv_counts = Counter(r["equivalence_status"] or "<empty>" for r in best.values())
    true_completed = sum(1 for r in best.values() if r["true_completed"])
    with_finalization = sum(1 for r in best.values() if r["has_finalization"])
    with_equivalent = sum(
        1 for r in best.values() if str(r["equivalence_status"]).upper() == "EQUIVALENT"
    )
    with_indeterminate = sum(
        1
        for r in best.values()
        if str(r["equivalence_status"]).upper() == "INDETERMINATE"
    )

    # Baseline R1: 8 finalizations, 0 TRUE_COMPLETED
    r1 = _load_json(R1_BASELINE) if R1_BASELINE.exists() else {}
    baseline_true = int(r1.get("true_completed_count") or 0)
    baseline_finalizations = int(r1.get("count") or 0)

    report = {
        "schema_version": "qualibug.v162-cleanup-reval-coverage.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_name": run_name,
        "scan_id": scan_id,
        "execution_status": resp.get("execution_status"),
        "http_status": resp.get("_http_status"),
        "elapsed_ms": resp.get("total_ms") or resp.get("_elapsed_ms_wall"),
        "canonical_selected": camp.get("obligation_attempt_selected_count"),
        "canonical_terminal": camp.get("obligation_attempt_terminal_count"),
        "N": 61,
        "unlock_ids_seen": len(best),
        "unlock_with_finalization": with_finalization,
        "unlock_true_completed": true_completed,
        "unlock_equivalence_equivalent": with_equivalent,
        "unlock_equivalence_indeterminate": with_indeterminate,
        "lifecycle_distribution": dict(lifecycle_counts),
        "equivalence_distribution": dict(equiv_counts),
        "baseline_r1": {
            "finalizations": baseline_finalizations,
            "true_completed": baseline_true,
        },
        "delta_vs_r1": {
            "true_completed": true_completed - baseline_true,
            "finalizations_seen_in_unlock": with_finalization - baseline_finalizations,
        },
        "thresholds": {
            "need_D_ge_49": False,  # D is new real-executed; filled if measurable
            "note": "Coverage expansion D uses receipt-verified real executed delta vs R1 baseline",
        },
        "artifact_sources": [str(p.relative_to(ROOT)) for p in artifact_paths[:30]],
        "rows": [best[oid] for oid in unlock_ids if oid in best],
    }

    (OUT / "reval_coverage_comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: report[k] for k in report if k != "rows"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
