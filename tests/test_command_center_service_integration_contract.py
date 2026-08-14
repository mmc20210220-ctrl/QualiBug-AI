from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "ai_test_asset_center" / "private_pilot_service.py"
ADAPTER = ROOT / "ai_test_asset_center" / "command_center_delivery_contract.py"


def test_command_center_delivery_normalization_adapter_available() -> None:
    adapter_source = ADAPTER.read_text(encoding="utf-8")

    assert "def normalize_command_center_delivery" in adapter_source
    assert "data[\"risks\"] = defects" in adapter_source
    assert "_sync_command_center_counters" in adapter_source


def test_command_center_adapter_does_not_re_judge_delivery_membership() -> None:
    """The adapter normalizes shape, it is not a second delivery authority.

    It previously re-ran the v1 field gate over data.defects. That is fatal, not
    redundant: by the time this adapter runs, data.defects holds the v2 authority's
    published canonical representatives, which legitimately carry
    bug_status='suspected' / confirmation_status='candidate' because the v2 chain
    proves delivery through sealed receipts. Measured on
    platform_outputs/benchmark_mall/scan_result.json, the v1 gate rejected 10 of 10
    receipt-backed delivery_occurrences that each carried
    delivery_gate_receipt.status == 'DELIVERABLE'.

    Asserted behaviourally rather than by grepping for the import name, because a
    source-text assertion passes as soon as the symbol is mentioned in a comment.
    """
    from ai_test_asset_center import command_center_delivery_contract as adapter

    # Any callable that decides membership must be absent from the module namespace.
    assert not hasattr(adapter, "split_customer_delivery_tracks")
    assert not hasattr(adapter, "customer_delivery_rejection_reasons")
    # _collect_candidate_items folded data.risks / data.findings back into the defect
    # pool, which could resurrect rows the authority never published.
    assert not hasattr(adapter, "_collect_candidate_items")

    receipt_backed = {
        "finding_id": "FINDING-1",
        "title": "receipt-backed defect",
        "severity": "P1",
        # The v1 vocabulary that the old gate rejected on.
        "bug_status": "suspected",
        "confirmation_status": "candidate",
        "delivery_gate_receipt": {"status": "DELIVERABLE"},
    }
    out = adapter.normalize_command_center_delivery(
        {"data": {"defects": [receipt_backed], "clues": []}}
    )["data"]

    assert out["defects"] == [receipt_backed]
    assert out["risks"] == [receipt_backed]
    assert out["delivery_contract"]["ready_bug_count"] == 1
    assert out["delivery_contract"]["source"] == "backend_formal_delivery_authority"


def test_command_center_adapter_never_derives_defects_from_legacy_aliases() -> None:
    """A non-list defects key must fail closed, not fall back to risks/findings."""
    from ai_test_asset_center.command_center_delivery_contract import (
        normalize_command_center_delivery,
    )

    row = {"finding_id": "FINDING-1", "title": "t", "severity": "P0"}
    out = normalize_command_center_delivery(
        {"data": {"defects": None, "clues": None, "risks": [row], "findings": [row]}}
    )["data"]

    assert out["defects"] == []
    assert out["risks"] == []
    assert out["ready_bug_count"] == 0
    assert "defects_not_a_list_coerced_empty" in out["delivery_contract"]["normalization_notes"]


def test_command_center_service_calls_existing_envelope_normalizer_before_response() -> None:
    service_source = SERVICE.read_text(encoding="utf-8")
    routing_source = (ROOT / "ai_test_asset_center" / "private_pilot_http_routing.py").read_text(encoding="utf-8")

    assert "def _normalize_command_center_envelope" in service_source
    assert "normalized = _normalize_command_center_envelope(sanitized)" in routing_source
    assert "return self._json(normalized)" in routing_source


def test_command_center_service_imports_the_canonical_gate_directly() -> None:
    from ai_test_asset_center import private_pilot_service
    from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks

    assert private_pilot_service._partition_delivery_tracks is split_customer_delivery_tracks


def test_command_center_normalizer_does_not_re_partition_published_defects() -> None:
    """Replaces test_command_center_normalizer_rechecks_prefilled_defects.

    That test asserted the envelope demotes a prefilled defect to a clue by re-running
    the v1 field gate. It enshrined the exact behaviour that zeroed every real result:
    receipt-backed rows carry bug_status='suspected', so re-judging them on v1 display
    fields rejected 10 of 10 real delivery_occurrences.

    The protection it was reaching for is real, but it belongs upstream and is enforced
    differently: a row reaches data.defects only because the v2 authority published it
    (formal_delivery_scope -> canonical_defect_registry -> discovery_quality_projection),
    and an unpublishable row never gets there. The envelope's job is not to second-guess
    that decision. See tests/test_delivery_authority_unverifiable.py for the guarantee
    that an unprovable authority publishes nothing at all.
    """
    from ai_test_asset_center.private_pilot_service import _normalize_command_center_envelope

    published = {
        "finding_id": "FINDING-1",
        "title": "receipt-backed defect",
        "severity": "P1",
        "bug_status": "suspected",
        "confirmation_status": "candidate",
        "delivery_gate_receipt": {"status": "DELIVERABLE"},
    }

    normalized = _normalize_command_center_envelope({
        "data": {"defects": [published], "clues": [], "risks": [published]},
    })

    defects = normalized["data"]["defects"]
    assert len(defects) == 1
    assert defects[0]["finding_id"] == "FINDING-1"
    assert normalized["data"]["risks"] == defects
    assert normalized["data"]["clues"] == []


def test_command_center_envelope_fails_closed_without_authoritative_lists() -> None:
    """Absent delivery lists publish nothing and say so; never derive from risks."""
    from ai_test_asset_center.private_pilot_command_center_envelope import (
        _normalize_command_center_envelope_base,
    )

    row = {"finding_id": "FINDING-1", "title": "t", "severity": "P0"}
    normalized = _normalize_command_center_envelope_base({
        "data": {"defects": None, "clues": None, "risks": [row]},
    })

    assert normalized["data"]["defects"] == []
    assert normalized["data"]["risks"] == []
