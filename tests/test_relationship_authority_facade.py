from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _linking
from ai_test_asset_center.enterprise_knowledge_center import _linking_impl


def _edge(**overrides):
    row = {
        "from": "rule:1",
        "to": "api:1",
        "relation": "rule_to_interface",
        "status": "accepted",
        "derivation": "exact_source_section",
        "evidence_gate": "exact_source_section",
        "evidence": {"source_id": "prd.md", "source_locator": "section:1"},
    }
    row.update(overrides)
    return row


def test_statusless_relationship_is_not_authoritative() -> None:
    edge = _edge()
    edge.pop("status")
    assert _linking._relationship_is_authoritative(edge) is False
    assert _linking_impl._relationship_is_authoritative(edge) is False


def test_explicit_accepted_relationship_requires_structured_evidence() -> None:
    assert _linking._relationship_is_authoritative(_edge(evidence={})) is False


def test_exact_source_relationship_remains_authoritative() -> None:
    edge = _edge()
    assert _linking._relationship_is_authoritative(edge) is True
    assert _linking_impl._relationship_is_authoritative(edge) is True


def test_token_overlap_never_becomes_authoritative_even_if_accepted() -> None:
    edge = _edge(
        derivation="token_overlap",
        evidence_gate="token_overlap_only_requires_explicit_source_relation",
        evidence={"token_overlap": ["order"]},
    )
    assert _linking._relationship_is_authoritative(edge) is False
