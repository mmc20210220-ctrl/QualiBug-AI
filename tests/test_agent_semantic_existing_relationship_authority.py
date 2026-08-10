from __future__ import annotations

from ai_test_asset_center.agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships,
)
from tests.test_agent_semantic_linker import FakeAgentClient, _asset, _linked_response


def test_statusless_existing_edge_does_not_suppress_source_backed_agent_edge() -> None:
    asset = _asset()
    asset["relationships"] = [
        {
            "from": "rule-conservation",
            "to": "api:POST:/transfers",
            "relation": "rule_to_interface",
            "derivation": "legacy_unknown",
        }
    ]
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=FakeAgentClient(_linked_response()),
    )

    assert receipt["existing_relationship_count"] == 0
    assert receipt["accepted_relationship_count"] == 2
    assert receipt["ungoverned_existing_relationship_count"] == 1
    assert len(enriched["relationships"]) == 3
    authoritative = [
        row for row in enriched["relationships"] if row.get("status") == "accepted"
    ]
    assert len(authoritative) == 2
    assert authoritative[0]["evidence"]["supporting_fact_refs"]


def test_governed_existing_edge_still_suppresses_duplicate_generation() -> None:
    asset = _asset()
    asset["relationships"] = [
        {
            "edge_id": "edge:existing",
            "from": "rule-conservation",
            "to": "api:POST:/transfers",
            "relation": "rule_to_interface",
            "status": "accepted",
            "derivation": "exact_source_section",
            "evidence_gate": "exact_source_section",
            "evidence": {
                "source_id": "prd-source",
                "source_locator": "section:1",
            },
        }
    ]
    enriched, receipt = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=FakeAgentClient(_linked_response()),
    )

    assert receipt["existing_relationship_count"] == 1
    # The governed existing rule edge suppresses the duplicate rule proposal;
    # the state-transition proposal is a distinct edge and is accepted.
    assert receipt["accepted_relationship_count"] == 1
    assert receipt["ungoverned_existing_relationship_count"] == 0
    assert len(enriched["relationships"]) == 2
    assert any(
        row.get("relation") == "state_transition_to_interface"
        for row in enriched["relationships"]
    )


def test_discovery_composition_installs_governed_agent_linker() -> None:
    from ai_test_asset_center import discovery_runtime_planning
    from ai_test_asset_center import discovery_runtime_semantic_binding as binding  # noqa: F401
    from ai_test_asset_center.agent_semantic_linker_authority import (
        enrich_knowledge_asset_with_agent_relationships as governed,
    )

    # The composition installs the visible-failure wrapper on planning, and
    # that wrapper delegates to the governed authority linker (single semantic
    # mapping authority). The identity assertion targets the wrapper: it is the
    # symbol planning resolves, and its sole delegation target is ``governed``.
    installed = discovery_runtime_planning.enrich_knowledge_asset_with_agent_relationships
    assert installed is binding._agent_semantic_linker_with_visible_failure
    assert binding._governed_agent_semantic_linker is governed
