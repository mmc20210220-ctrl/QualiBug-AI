"""Map exact explicit-fact understanding to existing Bug dependency Ground Truth.

This module is evaluator-only. It consumes the existing ``bug_dependencies`` rows and
the deterministic business-fact slot measurement. It never changes product facts,
selects candidates, or treats non-slot dependencies as understood.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

EXPLICIT_FACT_BUG_DEPENDENCY_SCHEMA = (
    "qualibug.explicit-fact-bug-dependency-analysis.v1"
)
_EXACT_STATUSES = frozenset({"EXACT"})
_COVERED_STATUSES = frozenset({"EXACT", "PARTIAL"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return (
        [dict(row) for row in value if isinstance(row, dict)]
        if isinstance(value, list)
        else []
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def analyze_explicit_fact_bug_dependencies(
    ground_truth: dict[str, Any],
    fact_slot_measurement: dict[str, Any],
) -> dict[str, Any]:
    """Measure which Bug prerequisites are exact at the explicit-fact slot layer."""
    measurement_status = _text(fact_slot_measurement.get("status")).upper()
    alignments = _rows(fact_slot_measurement.get("alignments"))
    status_by_id = {
        _text(row.get("ground_truth_id")): _text(row.get("alignment_status")).upper()
        for row in alignments
        if _text(row.get("ground_truth_id"))
    }
    explicit_fact_ids = set(status_by_id)
    bug_rows = [
        row
        for row in _rows(ground_truth.get("bug_dependencies"))
        if _text(row.get("annotation_status") or "CONFIRMED").upper() == "CONFIRMED"
    ]

    if measurement_status != "PASS":
        return {
            "schema": EXPLICIT_FACT_BUG_DEPENDENCY_SCHEMA,
            "status": "BLOCKED_FACT_SLOT_MEASUREMENT_NOT_PASS",
            "fact_slot_measurement_status": measurement_status or "NOT_MEASURED",
            "metrics": {},
            "bugs": [],
            "alignment_authority": "EXISTING_DETERMINISTIC_FACT_SLOT_ALIGNMENTS",
            "model_writeback_allowed": False,
        }
    if not bug_rows:
        return {
            "schema": EXPLICIT_FACT_BUG_DEPENDENCY_SCHEMA,
            "status": "NOT_MEASURED_NO_BUG_DEPENDENCIES",
            "fact_slot_measurement_status": measurement_status,
            "metrics": {
                "bug_dependency_count": 0,
                "bugs_with_explicit_fact_dependencies": 0,
                "explicit_fact_dependency_exact_bug_count": 0,
                "explicit_fact_dependency_exact_rate": None,
                "bug_ready_from_explicit_fact_slots_only_count": 0,
                "bug_ready_from_explicit_fact_slots_only_rate": None,
            },
            "bugs": [],
            "alignment_authority": "EXISTING_DETERMINISTIC_FACT_SLOT_ALIGNMENTS",
            "model_writeback_allowed": False,
        }

    results: list[dict[str, Any]] = []
    bugs_with_explicit = 0
    exact_bug_count = 0
    ready_from_slots_only = 0
    missing_explicit_dependency_count = 0
    non_slot_dependency_count = 0

    for bug in bug_rows:
        required = [
            _text(value)
            for value in bug.get("required_ground_truth_ids") or []
            if _text(value)
        ]
        explicit_required = [value for value in required if value in explicit_fact_ids]
        non_slot_required = [value for value in required if value not in explicit_fact_ids]
        exact = [
            value
            for value in explicit_required
            if status_by_id.get(value) in _EXACT_STATUSES
        ]
        covered = [
            value
            for value in explicit_required
            if status_by_id.get(value) in _COVERED_STATUSES
        ]
        missing = [
            value
            for value in explicit_required
            if status_by_id.get(value) not in _COVERED_STATUSES
        ]
        wrong_or_partial = [
            value
            for value in explicit_required
            if status_by_id.get(value) != "EXACT"
        ]
        explicit_exact = bool(explicit_required) and len(exact) == len(explicit_required)
        all_dependencies_represented = bool(required) and not non_slot_required
        ready = explicit_exact and all_dependencies_represented

        bugs_with_explicit += int(bool(explicit_required))
        exact_bug_count += int(explicit_exact)
        ready_from_slots_only += int(ready)
        missing_explicit_dependency_count += len(missing)
        non_slot_dependency_count += len(non_slot_required)

        if not explicit_required:
            status = "NOT_APPLICABLE_NO_EXPLICIT_FACT_DEPENDENCY"
        elif explicit_exact:
            status = "EXPLICIT_FACT_DEPENDENCIES_EXACT"
        elif covered:
            status = "EXPLICIT_FACT_DEPENDENCIES_PARTIAL"
        else:
            status = "EXPLICIT_FACT_DEPENDENCIES_MISSING"

        results.append(
            {
                "bug_id": bug.get("bug_id"),
                "ground_truth_id": bug.get("ground_truth_id"),
                "criticality": bug.get("criticality"),
                "required_ground_truth_ids": required,
                "explicit_fact_dependency_ids": explicit_required,
                "non_fact_slot_dependency_ids": non_slot_required,
                "exact_explicit_fact_dependency_ids": exact,
                "covered_explicit_fact_dependency_ids": covered,
                "missing_explicit_fact_dependency_ids": missing,
                "partial_or_wrong_explicit_fact_dependency_ids": wrong_or_partial,
                "explicit_fact_dependency_status": status,
                "all_explicit_fact_dependencies_exact": explicit_exact,
                "all_required_dependencies_represented_by_fact_slots": (
                    all_dependencies_represented
                ),
                "bug_ready_from_explicit_fact_slots_only": ready,
                "explicit_fact_dependency_coverage": _ratio(
                    len(covered), len(explicit_required)
                ),
                "explicit_fact_dependency_exact_rate": _ratio(
                    len(exact), len(explicit_required)
                ),
            }
        )

    return {
        "schema": EXPLICIT_FACT_BUG_DEPENDENCY_SCHEMA,
        "status": "PASS",
        "fact_slot_measurement_status": measurement_status,
        "metrics": {
            "bug_dependency_count": len(results),
            "bugs_with_explicit_fact_dependencies": bugs_with_explicit,
            "explicit_fact_dependency_exact_bug_count": exact_bug_count,
            "explicit_fact_dependency_exact_rate": _ratio(
                exact_bug_count, bugs_with_explicit
            ),
            "bug_ready_from_explicit_fact_slots_only_count": ready_from_slots_only,
            "bug_ready_from_explicit_fact_slots_only_rate": _ratio(
                ready_from_slots_only, len(results)
            ),
            "missing_explicit_fact_dependency_count": (
                missing_explicit_dependency_count
            ),
            "non_fact_slot_dependency_count": non_slot_dependency_count,
        },
        "bugs": results,
        "alignment_authority": "EXISTING_DETERMINISTIC_FACT_SLOT_ALIGNMENTS",
        "partial_fact_is_bug_ready": False,
        "non_fact_slot_dependency_is_assumed_understood": False,
        "automatic_winner_used": False,
        "fuzzy_or_llm_alignment_used": False,
        "ground_truth_generated_from_product_output": False,
        "model_writeback_allowed": False,
    }


def _read_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map exact explicit-fact slot alignments to existing Bug dependencies."
    )
    parser.add_argument("--ground-truth", required=True)
    parser.add_argument("--measurement", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    result = analyze_explicit_fact_bug_dependencies(
        _read_json(args.ground_truth),
        _read_json(args.measurement),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") != "BLOCKED_FACT_SLOT_MEASUREMENT_NOT_PASS" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "EXPLICIT_FACT_BUG_DEPENDENCY_SCHEMA",
    "analyze_explicit_fact_bug_dependencies",
]
