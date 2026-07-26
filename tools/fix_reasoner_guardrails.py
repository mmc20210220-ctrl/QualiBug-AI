"""Patch Stage 2 reasoner guardrails and quality-report wiring in-place.

This script is intentionally conservative:
- it replaces only known legacy constants when present;
- it is idempotent, so it also succeeds when the target is already fixed;
- it wires executable-quality reporting into _last_engine_report when missing;
- it verifies the required guardrails and report fields after normalization;
- it parses the edited Python file with ast.parse before saving success.

Run from repository root:
    python tools/fix_reasoner_guardrails.py
"""

from __future__ import annotations

import ast
from pathlib import Path


TARGET = Path("ai_test_asset_center/stage_reason_all_v2.py")
# The canonical per-engine hypothesis cap.  Keep in sync with
# ai_test_asset_center/policy_wiring.py::_REASONER_MAX_HYPOTHESES_PER_ENGINE,
# which is the runtime authority; tests/test_reasoner_static_guardrails.py
# cross-checks the two.
HYPOTHESIS_CAP = 40
REPLACEMENTS = {
    "MAX_HYPOTHESES = 300": f"MAX_HYPOTHESES = {HYPOTHESIS_CAP}",
    "MAX_HYPOTHESES_HARD_LIMIT = 500": f"MAX_HYPOTHESES_HARD_LIMIT = {HYPOTHESIS_CAP}",
}
REQUIRED_AFTER = (
    f"MAX_HYPOTHESES = {HYPOTHESIS_CAP}",
    f"MAX_HYPOTHESES_HARD_LIMIT = {HYPOTHESIS_CAP}",
    "MAX_REASONER_WORKERS = 4",
    "MIN_REASONER_TIMEOUT_SECONDS = 300",
    "MIN_REASONER_MAX_TOKENS = 32768",
)
QUALITY_IMPORT = "from .reasoner_quality_report import build_executable_quality_report"
QUALITY_SNIPPET = """    executable_quality_report = build_executable_quality_report(
        results_by_engine,
        engine_names_for_report,
        all_hypotheses,
    )

"""
QUALITY_UPDATE_SNIPPET = """    self._last_engine_report.update(executable_quality_report)
"""
QUALITY_LOCAL_SNIPPET = """        local_executable_quality_report = build_executable_quality_report(
            {"local_bootstrap": {"hypotheses": local_hypotheses}},
            ["local_bootstrap"],
            local_hypotheses,
        )
        self._last_engine_report.update(local_executable_quality_report)
"""
QUALITY_FIELDS = (
    "executable_hypotheses",
    "non_executable_hypotheses",
    "executable_hypothesis_ratio",
    "per_engine_executable_hypotheses",
    "per_engine_non_executable_hypotheses",
    "per_engine_executable_ratio",
    "engines_with_no_executable_output",
)


def _wire_quality_report(source: str) -> str:
    updated = source
    if QUALITY_IMPORT not in updated:
        anchor = "from .console_output import safe_print as print\n"
        if anchor not in updated:
            raise SystemExit("Cannot wire quality report import: console_output import anchor missing")
        updated = updated.replace(anchor, anchor + QUALITY_IMPORT + "\n", 1)

    if QUALITY_SNIPPET not in updated:
        anchor = "    # ── Build engine health report ──\n"
        if anchor not in updated:
            raise SystemExit("Cannot wire executable quality report: engine health anchor missing")
        updated = updated.replace(anchor, QUALITY_SNIPPET + anchor, 1)

    if QUALITY_UPDATE_SNIPPET not in updated:
        anchor = "    print(f\"  [OK] 生成了 {len(all_hypotheses)} 条假设 (across {len(engines)} engines)\", flush=True)\n"
        if anchor not in updated:
            raise SystemExit("Cannot update _last_engine_report: final print anchor missing")
        updated = updated.replace(anchor, QUALITY_UPDATE_SNIPPET + "\n" + anchor, 1)

    if QUALITY_LOCAL_SNIPPET not in updated:
        anchor = "        print(f\"    [WARN] [local_bootstrap_only] {len(local_hypotheses)} read-only hypotheses\", flush=True)\n"
        if anchor not in updated:
            raise SystemExit("Cannot wire local_bootstrap quality report: local print anchor missing")
        updated = updated.replace(anchor, QUALITY_LOCAL_SNIPPET + anchor, 1)

    return updated


def main() -> int:
    if not TARGET.exists():
        raise SystemExit(f"Target file not found: {TARGET}")

    original = TARGET.read_text(encoding="utf-8")
    updated = original

    applied = []
    for old, new in REPLACEMENTS.items():
        if old in updated:
            updated = updated.replace(old, new, 1)
            applied.append(old)

    updated = _wire_quality_report(updated)

    missing_after = [item for item in REQUIRED_AFTER if item not in updated]
    if missing_after:
        raise SystemExit(f"Required guardrails missing after normalization: {missing_after}")

    for old in REPLACEMENTS:
        if old in updated:
            raise SystemExit(f"Forbidden legacy value still present after normalization: {old}")

    missing_quality = [field for field in QUALITY_FIELDS if field not in updated]
    if missing_quality:
        raise SystemExit(f"Executable quality report fields missing after wiring: {missing_quality}")

    ast.parse(updated, filename=str(TARGET))

    if updated != original:
        TARGET.write_text(updated, encoding="utf-8")
        print(
            "Patched reasoner guardrails and executable quality-report wiring"
        )
    else:
        print("Reasoner guardrails and quality-report wiring already normalized")

    if applied:
        print(f"Applied replacements: {len(applied)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
