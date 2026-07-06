"""Fail fast when Reasoner executable-quality metrics fall below policy.

This script checks a JSON quality report rendered from _last_engine_report.
It is intentionally dependency-free so it can run in CI, local smoke tests,
and customer-private deployments without touching LLM providers or target APIs.

Example:
    python tools/enforce_reasoner_quality_threshold.py \
        --input quality_report.json \
        --min-overall-ratio 0.6 \
        --max-zero-output-engines 0
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


def enforce_thresholds(
    report: dict[str, Any],
    *,
    min_overall_ratio: float,
    max_zero_output_engines: int,
    min_per_engine_ratio: float | None = None,
) -> dict[str, Any]:
    """Return a pass report or raise ValueError when quality thresholds fail."""
    if not isinstance(report, dict):
        raise TypeError("report must be a dict")

    failures: list[str] = []
    overall_ratio = _safe_float(report.get("executable_hypothesis_ratio"), 0.0)
    if overall_ratio < float(min_overall_ratio):
        failures.append(
            f"overall executable ratio {overall_ratio:.4f} below required {float(min_overall_ratio):.4f}"
        )

    zero_output_engines = list(report.get("engines_with_no_executable_output") or [])
    if len(zero_output_engines) > int(max_zero_output_engines):
        failures.append(
            "too many engines with no executable output: "
            f"{len(zero_output_engines)} > {int(max_zero_output_engines)} ({zero_output_engines})"
        )

    weak_engines: dict[str, float] = {}
    if min_per_engine_ratio is not None:
        ratios = report.get("per_engine_executable_ratio", {})
        if not isinstance(ratios, dict):
            failures.append("per_engine_executable_ratio must be a dict")
        else:
            for engine, ratio in ratios.items():
                numeric = _safe_float(ratio, 0.0)
                if numeric < float(min_per_engine_ratio):
                    weak_engines[str(engine)] = numeric
            if weak_engines:
                failures.append(
                    f"engines below per-engine executable ratio {float(min_per_engine_ratio):.4f}: {weak_engines}"
                )

    result = {
        "status": "passed" if not failures else "failed",
        "overall_ratio": overall_ratio,
        "min_overall_ratio": float(min_overall_ratio),
        "zero_output_engines": zero_output_engines,
        "max_zero_output_engines": int(max_zero_output_engines),
        "min_per_engine_ratio": min_per_engine_ratio,
        "weak_engines": weak_engines,
        "failures": failures,
    }
    if failures:
        raise ValueError(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce Reasoner executable-quality thresholds")
    parser.add_argument("--input", required=True, help="Path to rendered reasoner quality JSON")
    parser.add_argument("--min-overall-ratio", type=float, default=0.60)
    parser.add_argument("--max-zero-output-engines", type=int, default=0)
    parser.add_argument("--min-per-engine-ratio", type=float, default=None)
    args = parser.parse_args()

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = enforce_thresholds(
        report,
        min_overall_ratio=args.min_overall_ratio,
        max_zero_output_engines=args.max_zero_output_engines,
        min_per_engine_ratio=args.min_per_engine_ratio,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
