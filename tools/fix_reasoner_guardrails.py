"""Patch Stage 2 reasoner guardrail constants in-place.

This script is intentionally tiny and conservative:
- it only replaces the two known legacy constants;
- it verifies that both old values existed before editing;
- it parses the edited Python file with ast.parse before saving success.

Run from repository root:
    python tools/fix_reasoner_guardrails.py
"""

from __future__ import annotations

import ast
from pathlib import Path


TARGET = Path("ai_test_asset_center/stage_reason_all_v2.py")
REPLACEMENTS = {
    "MAX_HYPOTHESES = 300": "MAX_HYPOTHESES = 15",
    "MAX_HYPOTHESES_HARD_LIMIT = 500": "MAX_HYPOTHESES_HARD_LIMIT = 15",
}
REQUIRED_AFTER = (
    "MAX_HYPOTHESES = 15",
    "MAX_HYPOTHESES_HARD_LIMIT = 15",
    "MAX_REASONER_WORKERS = 4",
    "MIN_REASONER_TIMEOUT_SECONDS = 300",
    "MIN_REASONER_MAX_TOKENS = 32768",
)


def main() -> int:
    if not TARGET.exists():
        raise SystemExit(f"Target file not found: {TARGET}")

    original = TARGET.read_text(encoding="utf-8")
    updated = original

    missing_old = [old for old in REPLACEMENTS if old not in original]
    if missing_old:
        raise SystemExit(f"Expected legacy constants not found: {missing_old}")

    for old, new in REPLACEMENTS.items():
        updated = updated.replace(old, new, 1)

    missing_after = [item for item in REQUIRED_AFTER if item not in updated]
    if missing_after:
        raise SystemExit(f"Required guardrails missing after patch: {missing_after}")

    for old in REPLACEMENTS:
        if old in updated:
            raise SystemExit(f"Forbidden legacy value still present after patch: {old}")

    ast.parse(updated, filename=str(TARGET))
    TARGET.write_text(updated, encoding="utf-8")
    print("Patched reasoner guardrails: MAX_HYPOTHESES=15, MAX_HYPOTHESES_HARD_LIMIT=15")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
