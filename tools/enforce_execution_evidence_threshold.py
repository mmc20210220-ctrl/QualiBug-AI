"""Fail fast when verification evidence metrics fall below policy.

This gate complements the executable-hypothesis threshold. A hypothesis can be
well-structured and executable while still producing no observed runtime proof.
This script enforces evidence-backed verification quality from a rendered JSON
report or any report containing the execution-evidence fields.

Example:
    python tools/enforce_execution_evidence_threshold.py \
        --input evidence_quality.json \
        --min-evidence-ratio 0.50 \
        --max-no-evidence-engines 0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def enforce_evidence_thresholds(
    report: dict[str, Any],
    *,
    min_evidence_ratio: float,
    max_no_evidence_engines: int,
    min_per_engine_evidence_ratio: float | None = None,
) -> dict[str, Any]:
    """Return a pass report or raise ValueError when evidence thresholds fail."""
    if not isinstance(report, dict):
        raise TypeError("report must be a dict")

    failures: list[str] = []
    evidence_ratio = _safe_float(report.get("evidence_backed_ratio"), 0.0)
    if evidence_ratio < float(min_evidence_ratio):
        failures.append(
            f"evidence-backed ratio {evidence_ratio:.4f} below required {float(min_evidence_ratio):.4f}"
        )

    no_evidence_engines = list(report.get("engines_with_no_evidence_backed_output") or [])
    if len(no_evidence_engines) > int(max_no_evidence_engines):
        failures.append(
            "too many engines with no evidence-backed output: "
            f"{len(no_evidence_engines)} > {int(max_no_evidence_engines)} ({no_evidence_engines})"
        )

    weak_engines: dict[str, float] = {}
    if min_per_engine_evidence_ratio is not None:
        ratios = report.get("per_engine_evidence_backed_ratio", {})
        if not isinstance(ratios, dict):
            failures.append("per_engine_evidence_backed_ratio must be a dict")
        else:
            for engine, ratio in ratios.items():
                numeric = _safe_float(ratio, 0.0)
                if numeric < float(min_per_engine_evidence_ratio):
                    weak_engines[str(engine)] = numeric
            if weak_engines:
                failures.append(
                    "engines below per-engine evidence-backed ratio "
                    f"{float(min_per_engine_evidence_ratio):.4f}: {weak_engines}"
                )

    result = {
        "status": "passed" if not failures else "failed",
        "evidence_ratio": evidence_ratio,
        "min_evidence_ratio": float(min_evidence_ratio),
        "no_evidence_engines": no_evidence_engines,
        "max_no_evidence_engines": int(max_no_evidence_engines),
        "min_per_engine_evidence_ratio": min_per_engine_evidence_ratio,
        "weak_engines": weak_engines,
        "failures": failures,
    }
    if failures:
        raise ValueError(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce execution-evidence quality thresholds")
    parser.add_argument("--input", required=True, help="Path to execution evidence quality JSON")
    parser.add_argument("--min-evidence-ratio", type=float, default=0.50)
    parser.add_argument("--max-no-evidence-engines", type=int, default=0)
    parser.add_argument("--min-per-engine-evidence-ratio", type=float, default=None)
    args = parser.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = enforce_evidence_thresholds(
        report,
        min_evidence_ratio=args.min_evidence_ratio,
        max_no_evidence_engines=args.max_no_evidence_engines,
        min_per_engine_evidence_ratio=args.min_per_engine_evidence_ratio,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
