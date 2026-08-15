"""Static guardrails for the Stage 2 reasoner budget.

These tests read source text instead of importing the full product stack, so an
accidental regression is caught without pulling in LLM clients.  They parse the
assignments with ``ast`` rather than substring-matching them: the previous
version asserted literals like ``"MAX_HYPOTHESES = 15" in source``, which pinned
formatting instead of meaning and deadlocked the moment the product cap moved.

The AGENTS.md config table states these as *floors*, so they are asserted as
floors.  ``max_workers`` is the one true ceiling (higher hits provider rate
limits), and ``max_tokens`` is bounded on both sides.

The second test is the one that matters most: ``policy_wiring`` — not
``stage_reason_all_v2`` — is the runtime authority for the hypothesis cap, because
``_enforce_stage_reasoner_static_cap`` writes its value onto the reasoner module
and ``_clamp_reasoner_hypothesis_cap`` clamps to it.  The two constants silently
disagreed (source said 40, runtime ran 15), so a cross-check keeps them equal.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_REASONER = REPO_ROOT / "ai_test_asset_center" / "stage_reason_all_v2.py"
POLICY_WIRING = REPO_ROOT / "ai_test_asset_center" / "policy_wiring.py"

# AGENTS.md "Critical Configuration Guardrails" — floors that must not be lowered.
REQUIRED_HYPOTHESIS_CAP = 40
REQUIRED_REASONER_WORKERS = 4
MIN_TIMEOUT_SECONDS = 300
MIN_MAX_TOKENS = 32768


def _module_constants(path: Path) -> dict[str, int]:
    """Return every module-level ``NAME = <int literal>`` assignment."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, int):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = node.value.value
    return found


def test_stage_reasoner_source_caps_are_enterprise_safe() -> None:
    constants = _module_constants(STAGE_REASONER)

    missing = [
        name
        for name in (
            "MAX_HYPOTHESES",
            "MAX_HYPOTHESES_HARD_LIMIT",
            "MAX_REASONER_WORKERS",
            "MIN_REASONER_TIMEOUT_SECONDS",
            "MIN_REASONER_MAX_TOKENS",
            "MAX_REASONER_MAX_TOKENS",
        )
        if name not in constants
    ]
    assert not missing, f"reasoner guardrail constants missing from source: {missing}"

    # Hypothesis breadth: the package contract declares 40 as the static floor;
    # historical evaluator attribution is not required to prove this invariant.
    assert constants["MAX_HYPOTHESES"] >= REQUIRED_HYPOTHESIS_CAP
    assert constants["MAX_HYPOTHESES_HARD_LIMIT"] >= constants["MAX_HYPOTHESES"], (
        "the hard limit must not sit below the working cap, or the cap is unreachable"
    )

    # Concurrency is a ceiling, not a floor: higher trips provider rate limits.
    assert constants["MAX_REASONER_WORKERS"] == REQUIRED_REASONER_WORKERS

    # Timeout/token floors: below these the reader prompt and causality engine
    # truncate, which surfaces as a silent "engine crashed".
    assert constants["MIN_REASONER_TIMEOUT_SECONDS"] >= MIN_TIMEOUT_SECONDS
    assert constants["MIN_REASONER_MAX_TOKENS"] >= MIN_MAX_TOKENS
    assert constants["MAX_REASONER_MAX_TOKENS"] > constants["MIN_REASONER_MAX_TOKENS"]


def test_policy_wiring_runtime_cap_matches_static_guardrail() -> None:
    """The runtime authority must equal the reasoner module's own constant.

    ``policy_wiring._enforce_stage_reasoner_static_cap`` setattr()s its value onto
    ``stage_reason_all_v2``, so if these drift the source literal is dead and the
    product silently runs the policy_wiring number instead.
    """

    reasoner = _module_constants(STAGE_REASONER)
    wiring = _module_constants(POLICY_WIRING)

    assert "_REASONER_MAX_HYPOTHESES_PER_ENGINE" in wiring, (
        "policy_wiring must declare the runtime hypothesis cap it enforces"
    )
    runtime_cap = wiring["_REASONER_MAX_HYPOTHESES_PER_ENGINE"]

    assert runtime_cap >= REQUIRED_HYPOTHESIS_CAP
    assert runtime_cap == reasoner["MAX_HYPOTHESES"], (
        "policy_wiring._REASONER_MAX_HYPOTHESES_PER_ENGINE "
        f"({runtime_cap}) overwrites stage_reason_all_v2.MAX_HYPOTHESES "
        f"({reasoner['MAX_HYPOTHESES']}) at runtime; they must be equal or the "
        "source constant is dead code"
    )
    assert runtime_cap == reasoner["MAX_HYPOTHESES_HARD_LIMIT"], (
        "the same runtime cap is written onto MAX_HYPOTHESES_HARD_LIMIT"
    )
