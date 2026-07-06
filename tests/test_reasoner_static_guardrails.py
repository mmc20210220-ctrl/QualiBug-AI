"""Static guardrails for the Stage 2 reasoner budget.

These tests intentionally read source text instead of importing the full product
stack.  The goal is to catch accidental regressions where large legacy defaults
such as 300/500 silently return and weaken the evidence gate.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_REASONER = REPO_ROOT / "ai_test_asset_center" / "stage_reason_all_v2.py"
POLICY_WIRING = REPO_ROOT / "ai_test_asset_center" / "policy_wiring.py"


def test_stage_reasoner_source_caps_are_enterprise_safe() -> None:
    source = STAGE_REASONER.read_text(encoding="utf-8")

    assert "MAX_HYPOTHESES = 15" in source
    assert "MAX_HYPOTHESES_HARD_LIMIT = 15" in source
    assert "MAX_REASONER_WORKERS = 4" in source
    assert "MIN_REASONER_TIMEOUT_SECONDS = 300" in source
    assert "MIN_REASONER_MAX_TOKENS = 32768" in source

    assert "MAX_HYPOTHESES = 300" not in source
    assert "MAX_HYPOTHESES_HARD_LIMIT = 500" not in source


def test_policy_wiring_runtime_cap_matches_static_guardrail() -> None:
    source = POLICY_WIRING.read_text(encoding="utf-8")

    assert "_REASONER_MAX_HYPOTHESES_PER_ENGINE = 15" in source
    assert "MAX_HYPOTHESES" in source
    assert "MAX_HYPOTHESES_HARD_LIMIT" in source
