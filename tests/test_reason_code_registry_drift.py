"""Reason-code registry drift guard.

Every terminal reason code emitted by an attempt-ledger producer module MUST be
registered in ``REASON_CODE_REGISTRY``. A single unregistered code that reaches
the ledger fails the funnel conservation/registry checks and downgrades the
whole discovery report to FAILED_SAFE (observed 2026-08-25:
BLOCKED_RUNTIME_ACTOR_PAIR_NOT_DISTINCT was emitted by the compiler for weeks
without registration, so every run ending with it reported FAILED_SAFE
regardless of thousands of successful executions).

This test statically scans the producer modules for BLOCKED_/DEFERRED_/HARNESS_
string literals and asserts registration, so a new code cannot skip the
registry without breaking CI.
"""
from __future__ import annotations

import re
from pathlib import Path

from ai_test_asset_center._blocker_attribution_mechanics import REASON_CODE_REGISTRY

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "ai_test_asset_center"

# Modules that write terminal reason_code values into the obligation-attempt
# ledger. Extend this list when a new producer is introduced.
EMITTER_MODULES = [
    "experiment_compiler_obligation_core.py",
    "experiment_compiler.py",
    "experiment_compiler_conflict_base.py",
    "discovery_runtime_planning.py",
    "adaptive_discovery_planner_base.py",
    "adaptive_discovery_planner.py",
    "_experiment_outcome_finalizer_core_mechanics.py",
    "experiment_outcome_finalizer.py",
    "experiment_outcome_finalizer_core.py",
    "experiment_batch_concurrent_scheduler.py",
    "_experiment_batch_executor_single_finding_mechanics_base.py",
    "small_scale_validation_gate.py",
    "experiment_executor_core.py",
    "experiment_executor.py",
]

_REASON_LITERAL = re.compile(r"[\"']((?:BLOCKED|DEFERRED|HARNESS)_[A-Z0-9_]+)[\"']")

# String literals that are module-attribute / identifier names, not reason
# codes emitted into the attempt ledger.
NON_REASON_IDENTIFIERS = frozenset({"HARNESS_FAILURE_SUBTYPES"})


def _unregistered_emitted_codes() -> dict[str, set[str]]:
    unregistered: dict[str, set[str]] = {}
    for name in EMITTER_MODULES:
        path = PACKAGE_ROOT / name
        assert path.exists(), f"emitter module missing: {name}"
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _REASON_LITERAL.finditer(text):
            code = match.group(1)
            if code in NON_REASON_IDENTIFIERS:
                continue
            if code not in REASON_CODE_REGISTRY:
                unregistered.setdefault(code, set()).add(name)
    return unregistered


def test_every_emitted_terminal_reason_code_is_registered() -> None:
    unregistered = _unregistered_emitted_codes()
    assert not unregistered, (
        "terminal reason codes emitted by ledger producers but missing from "
        f"REASON_CODE_REGISTRY (a run containing any of them reports FAILED_SAFE): "
        + ", ".join(sorted(unregistered))
    )


def test_registered_codes_carry_valid_family_and_recoverability() -> None:
    from ai_test_asset_center._blocker_attribution_mechanics import (
        ATTRIBUTION_CATEGORIES,
        RECOVERABILITY_VALUES,
    )

    for code, definition in REASON_CODE_REGISTRY.items():
        assert definition.get("reason_family") in ATTRIBUTION_CATEGORIES, (
            f"{code}: unknown reason_family {definition.get('reason_family')}"
        )
        recoverability = definition.get("recoverability")
        assert recoverability in RECOVERABILITY_VALUES, (
            f"{code}: unknown recoverability {recoverability}"
        )
