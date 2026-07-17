"""Permit-only auth skip comparison + partial trace identity fallback."""
from __future__ import annotations

from ai_test_asset_center.campaign_api_contract import _trace_occurrence_ids
from ai_test_asset_center.observer_contracts_base import compile_observer_requirements


def test_permit_only_compile_skips_authorization_comparison() -> None:
    with_comparison, reason, detail = compile_observer_requirements(
        ["http_response"],
        risk_family="authorization",
        available_adapters={"http_api"},
        require_authorization_comparison=True,
    )
    assert reason == ""
    assert {row["observer_id"] for row in with_comparison} >= {
        "http_response",
        "authorization_comparison",
    }

    without, reason2, detail2 = compile_observer_requirements(
        ["http_response"],
        risk_family="authorization",
        available_adapters={"http_api"},
        require_authorization_comparison=False,
    )
    assert reason2 == ""
    assert detail2 == ""
    assert {row["observer_id"] for row in without} == {"http_response"}


def test_trace_occurrence_ids_rejects_partial_coverage() -> None:
    projected = {
        "experiment_execution": {
            "selected_count": 1,
            "results": [
                {
                    "obligation_id": "obl-1",
                    "finding": {"finding_id": "find-a", "id": "find-a"},
                }
            ],
        },
        "findings": [
            {"finding_id": "find-a", "id": "find-a"},
            {"finding_id": "find-b", "id": "find-b"},
        ],
    }
    # Only find-a is reachable via selected experiments; formal has both.
    # Partial must fall back to the full formal occurrence set.
    assert _trace_occurrence_ids(projected, ["find-a", "find-b"]) == [
        "find-a",
        "find-b",
    ]


def test_trace_occurrence_ids_keeps_exact_match() -> None:
    projected = {
        "experiment_execution": {
            "selected_count": 2,
            "results": [
                {"obligation_id": "obl-1", "finding": {"finding_id": "find-a"}},
                {"obligation_id": "obl-2", "finding": {"finding_id": "find-b"}},
            ],
        }
    }
    assert _trace_occurrence_ids(projected, ["find-a", "find-b"]) == [
        "find-a",
        "find-b",
    ]
