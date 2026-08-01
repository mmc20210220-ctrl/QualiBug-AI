from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _linking
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_behavior_implementation_bindings,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    implementation_binding as implementation_binding_core,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    implementation_binding_authority,
)


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


def test_package_import_installs_shared_relationship_authority() -> None:
    assert (
        implementation_binding_core._authoritative_relationship
        is _linking._relationship_is_authoritative
    )
    assert (
        implementation_binding_authority.build_behavior_implementation_bindings
        is build_behavior_implementation_bindings
    )


def test_statusless_relationship_cannot_bind_implementation() -> None:
    edge = _edge()
    edge.pop("status")
    assert implementation_binding_core._authoritative_relationship(edge) is False


def test_accepted_relationship_without_evidence_cannot_bind_implementation() -> None:
    assert implementation_binding_core._authoritative_relationship(
        _edge(evidence={})
    ) is False


def test_exact_source_relationship_remains_bindable() -> None:
    assert implementation_binding_core._authoritative_relationship(_edge()) is True
