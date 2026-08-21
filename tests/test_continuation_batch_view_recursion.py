"""ContinuationBatchView.get('results') must project without recursing."""

from __future__ import annotations

from ai_test_asset_center.continuation_selected_identity_authority import (
    ContinuationBatchView,
    _continuation_result_rows,
)


def _batch() -> dict:
    return {
        "campaign_id": "c1",
        "results": [
            {
                "obligation_id": "obl-1",
                "experiment_id": "exp-1",
                "status": "PASSED",
                "reason_code": "oracle_pass",
            },
            {
                "selected_obligation_id": "obl-selected-2",
                "executed_obligation_id": "obl-executed-2",
                "status": "HARNESS_FAILURE",
                "reason_code": "transport_failure",
            },
        ],
    }


def test_view_get_results_returns_selected_identity_projection() -> None:
    view = ContinuationBatchView(_batch())

    rows = view.get("results")

    assert len(rows) == 2
    # Selected identity becomes the obligation_id when present.
    assert rows[1]["obligation_id"] == "obl-selected-2"
    assert rows[1]["executed_obligation_id"] == "obl-executed-2"
    # Compatibility alias normalization.
    assert rows[1]["status"] == "HARNESS_FAILED"


def test_view_get_other_keys_uses_base_mapping() -> None:
    view = ContinuationBatchView(_batch())

    assert view.get("campaign_id") == "c1"
    assert view.get("missing", "fallback") == "fallback"


def test_projection_consumes_raw_results_without_recursion() -> None:
    view = ContinuationBatchView(_batch())
    # Directly exercising the projection against the view must terminate and
    # must not go through the overridden get again (RecursionError regression).
    rows = _continuation_result_rows(view)

    assert len(rows) == 2
    assert rows[0]["obligation_id"] == "obl-1"


def test_underlying_mapping_preserves_raw_results() -> None:
    view = ContinuationBatchView(_batch())

    # dict(view) keeps the executed identity/evidence payload byte-for-byte.
    raw = dict(view)
    assert raw["results"][1].get("obligation_id") is None
    assert raw["results"][1]["executed_obligation_id"] == "obl-executed-2"
