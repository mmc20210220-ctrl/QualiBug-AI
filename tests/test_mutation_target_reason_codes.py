"""A block must name the condition that caused it.

``_materialize_source_observed_mutation`` returned
``runtime_mutation_target_ambiguous`` when it could not pick a row to mutate. It is
never ambiguity. Two lines below the return, genuine ambiguity is explicitly *not* a
block:

    # ── Degraded: use first row when multiple match instead of blocking ──
    row = rows[0]

so the only path to that return is ``not rows`` -- zero rows, not too many. On a live
target 70 of 114 ``BLOCKED_MISSING_BINDING`` attempts carried the ambiguity label while
the truth was an empty before-state read, which sends a reader looking for a
disambiguation rule that would not have helped.

The two real conditions are now distinguished: the before-state was absent entirely, or
it came back with content that contained no entity rows. Those need different fixes --
the first is a read that did not happen, the second a read whose shape was not
recognised -- and one label for both hides which.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.sandbox_write_executor_base import (
    _materialize_source_observed_mutation,
    _runtime_mutation_rows,
)

PLAN = {
    "schema_version": "qualibug.source-observed-mutation-plan.v1",
    "candidate_fields": ["status"],
    "identity_bindings": {},
}


def _reason(before_body, plan=None):
    return _materialize_source_observed_mutation(before_body, dict(plan or PLAN))[2]


# ── the mislabel ────────────────────────────────────────────────────────────

def test_an_absent_before_state_says_so() -> None:
    for empty in (None, "", []):
        assert _reason(empty) == "runtime_mutation_before_state_absent", empty


def test_an_empty_object_is_one_rowless_row_not_an_absent_read() -> None:
    """``{}`` extracts as a single empty row, so it fails later on field matching.

    That reason is accurate too -- the read happened and returned an object without the
    field -- so it is left alone rather than folded into the absent-read code.
    """
    assert _reason({}) == "runtime_mutation_supported_field_missing"


def test_content_without_entity_rows_says_so() -> None:
    """A response that arrived but held no objects is a different problem."""
    assert _reason([1, 2, 3]) == "runtime_mutation_before_state_has_no_entity_rows"


def test_the_ambiguity_label_is_gone() -> None:
    """It was never true, and it pointed every reader at the wrong fix."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "sandbox_write_executor_base.py"
    ).read_text(encoding="utf-8")
    assert 'return {}, {}, "runtime_mutation_target_ambiguous"' not in source


# ── genuine ambiguity is still not a block ──────────────────────────────────

def test_multiple_rows_do_not_block() -> None:
    """The documented behaviour: pick the first row rather than refuse.

    If this ever changes to a block, the ambiguity reason code becomes real again and
    the tests above need revisiting deliberately.
    """
    body, _patch, reason = _materialize_source_observed_mutation(
        [{"status": "A"}, {"status": "B"}], dict(PLAN)
    )
    assert reason != "runtime_mutation_before_state_absent"
    assert reason != "runtime_mutation_before_state_has_no_entity_rows"


def test_identity_bindings_narrow_but_never_empty_the_set() -> None:
    """A filter that matches nothing falls back to the unfiltered rows.

    Otherwise a wrong binding would masquerade as an empty read.
    """
    plan = dict(PLAN, identity_bindings={"id": "no-such-id"})
    reason = _reason([{"id": "real", "status": "A"}], plan)
    assert reason not in (
        "runtime_mutation_before_state_absent",
        "runtime_mutation_before_state_has_no_entity_rows",
    )


# ── row extraction shapes ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,count",
    [
        ([{"a": 1}, {"a": 2}], 2),
        ({"items": [{"a": 1}]}, 1),
        ({"data": {"a": 1}}, 1),
        ({"a": 1}, 1),
        (None, 0),
        ([1, 2, 3], 0),
        ("", 0),
    ],
)
def test_row_extraction(value, count) -> None:
    """A top-level array is the common REST shape and must not be dropped.

    GET /api/products on the benchmark target returns a bare array of 17 objects.
    """
    assert len(_runtime_mutation_rows(value)) == count


def test_a_schema_mismatch_is_still_reported_separately() -> None:
    """The plan-shape guard must not be swallowed by the new codes."""
    reason = _materialize_source_observed_mutation([{"a": 1}], {"schema_version": "wrong"})[2]
    assert reason == "runtime_mutation_plan_schema_invalid"


def test_missing_candidate_fields_is_still_reported_separately() -> None:
    plan = dict(PLAN, candidate_fields=[])
    assert _reason([{"a": 1}], plan) == "runtime_mutation_candidate_fields_missing"
