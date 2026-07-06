"""Render the latest Reasoner executable-quality report as JSON.

This utility is intentionally small and dependency-free.  It can be used by
local smoke tests, demos, and CI to turn an engine report dictionary into a
stable JSON artifact without invoking LLM providers or target APIs.

Example:
    python tools/render_reasoner_quality_report.py --input report.json --output quality.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

QUALITY_FIELDS = (
    "executable_hypotheses",
    "non_executable_hypotheses",
    "executable_hypothesis_ratio",
    "per_engine_executable_hypotheses",
    "per_engine_non_executable_hypotheses",
    "per_engine_executable_ratio",
    "engines_with_no_executable_output",
)


def extract_reasoner_quality_report(engine_report: dict[str, Any]) -> dict[str, Any]:
    """Return only the stable executable-quality subset of a reasoner report."""
    if not isinstance(engine_report, dict):
        raise TypeError("engine_report must be a dict")
    missing = [field for field in QUALITY_FIELDS if field not in engine_report]
    if missing:
        raise ValueError(f"reasoner quality fields missing: {missing}")
    return {field: engine_report[field] for field in QUALITY_FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Reasoner executable-quality report JSON")
    parser.add_argument("--input", required=True, help="Path to a JSON file containing _last_engine_report")
    parser.add_argument("--output", help="Optional output JSON path. Prints to stdout when omitted.")
    args = parser.parse_args()

    source = json.loads(Path(args.input).read_text(encoding="utf-8"))
    report = extract_reasoner_quality_report(source)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)

    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
