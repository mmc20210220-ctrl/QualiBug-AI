from __future__ import annotations

from ai_test_asset_center import formal_event_surface as events
from ai_test_asset_center.formal_event_capability_guard import _stamp
from ai_test_asset_center.formal_event_pre_cleanup import _event_assertion
from ai_test_asset_center.observer_contracts_base import build_observer_receipt


def test_pre_cleanup_selects_the_event_assertion_not_the_first_assertion() -> None:
    event_assertion = {
        "assertion_id": "event-assertion",
        "kind": events.ASSERTION_KIND,
        "property": {"event_contract": {"contract_id": "event-1"}},
    }
    experiment = {
        "assertions": [
            {"assertion_id": "state-first", "kind": "state_transition"},
            event_assertion,
        ],
    }

    assert _event_assertion(experiment) == event_assertion


def test_pre_cleanup_refuses_ambiguous_event_assertion_identity() -> None:
    experiment = {
        "assertions": [
            {"assertion_id": "event-1", "kind": events.ASSERTION_KIND},
            {"assertion_id": "event-2", "kind": events.ASSERTION_KIND},
        ],
    }

    assert _event_assertion(experiment) == {}


def test_event_receipt_states_exact_capability_boundary() -> None:
    # validate_observer_receipt is strict (content-addressed receipt with
    # receipt_id/campaign_id/execution_id); build a fully valid receipt first,
    # then stamp it through the capability-boundary guard.
    receipt = _stamp(build_observer_receipt(
        observer_id=events.OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        campaign_id="camp-1",
        execution_id="exec-1",
        evidence={
            events.EVIDENCE_KEY: {
                "observed_correlated_count": 1,
                "event_id_fingerprints": ["fingerprint-only"],
                "raw_event_payloads_included": False,
            },
        },
    ))

    evidence = receipt["evidence"][events.EVIDENCE_KEY]
    assert evidence["count_semantics"] == (
        "unique_stable_event_ids_within_full_window"
    )
    assert evidence[
        "duplicate_physical_delivery_of_same_event_id_provable"
    ] is False
    assert evidence["ordering_contract_supported"] is False
    assert evidence["direct_broker_protocol_supported"] is False
    assert evidence["observation_adapter"] == (
        "approved_target_relative_http_get"
    )
    assert evidence["raw_event_payloads_included"] is False
