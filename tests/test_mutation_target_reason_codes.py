"""Legacy runtime mutation plans must fail closed before write transport."""

from __future__ import annotations

import pytest

from ai_test_asset_center.sandbox_write_executor_base import (
    _materialize_source_observed_mutation,
    _runtime_mutation_rows,
)


PLAN = {
    "schema_version": "qualibug.source-observed-mutation-plan.v1",
    "candidate_fields": ["status"],
    "identity_bindings": {"id": "resource-1"},
}


@pytest.mark.parametrize(
    "before_body",
    [
        None,
        {},
        {"id": "resource-1", "status": "PENDING"},
        [{"id": "resource-1", "status": "PENDING"}],
        [
            {"id": "resource-1", "status": "PENDING"},
            {"id": "resource-2", "status": "COMPLETE"},
        ],
    ],
)
def test_runtime_never_invents_a_write_body_from_observed_state(
    before_body,
) -> None:
    body, receipt, reason = _materialize_source_observed_mutation(
        before_body,
        dict(PLAN),
    )

    assert body == {}
    assert receipt == {}
    assert reason == "runtime_mutation_source_declared_body_required"


def test_schema_mismatch_is_reported_before_legacy_plan_rejection() -> None:
    reason = _materialize_source_observed_mutation(
        [{"a": 1}],
        {"schema_version": "wrong"},
    )[2]
    assert reason == "runtime_mutation_plan_schema_invalid"


def test_missing_candidate_fields_is_reported_before_legacy_plan_rejection() -> None:
    reason = _materialize_source_observed_mutation(
        [{"a": 1}],
        {
            "schema_version": "qualibug.source-observed-mutation-plan.v1",
            "candidate_fields": [],
        },
    )[2]
    assert reason == "runtime_mutation_candidate_fields_missing"


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
def test_runtime_observation_row_extraction_remains_available_for_diagnostics(
    value,
    count,
) -> None:
    assert len(_runtime_mutation_rows(value)) == count
