from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_resolution import (
    resolve_enterprise_identities,
)


def _span(source: str, quote: str) -> list[dict]:
    return [{"source_id": source, "locator": f"{source}#identity", "quote": quote, "quote_hash": f"hash-{source}-{quote}"}]


def _alias(fact_id: str, canonical: str, alias: str, statement: str | None = None) -> dict:
    raw = statement or f"{canonical}\uff08{alias}\uff09"
    return {
        "fact_id": fact_id,
        "kind": "TERM_ALIAS",
        "status": "ACCEPTED",
        "canonical_term": canonical,
        "alias": alias,
        "raw_statement": raw,
        "source_spans": _span(fact_id, raw),
    }


def _rule(fact_id: str, entity: str, action: str = "\u67e5\u770b") -> dict:
    statement = f"\u7ba1\u7406\u5458\u53ef\u4ee5{action}{entity}"
    return {
        "fact_id": fact_id,
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {"entity_refs": [entity], "actor_refs": ["\u7ba1\u7406\u5458"]},
        "object": {"entity_refs": [entity]},
        "action": {"canonical": action, "raw": action},
        "conditions": [],
        "condition_combinator": "",
        "state_effects": [],
        "postconditions": [],
        "data_effects": [],
        "exceptions": [],
        "scope": {},
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": _span(fact_id, statement),
    }


def _asset(facts: list[dict], **extra) -> dict:
    value = {
        "asset_id": "identity-test",
        "business_fact_ledger": {"schema": "qualibug.business-fact-ledger.v1", "items": facts},
        "business_objects": [],
        "roles": [],
        "permission_matrix": [],
        "data_tables": [],
        "field_dictionary": [],
        "state_machines": [],
        "entity_relations": [],
        "cross_document_conflicts": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
    }
    value.update(extra)
    return value


def test_multi_hop_source_aliases_form_one_identity_cluster() -> None:
    asset = _asset([
        _alias("a", "\u751f\u4ea7\u4efb\u52a1\u5355", "MO"),
        _alias("b", "MO", "ProductionOrder"),
        _rule("r", "ProductionOrder"),
    ])
    result = resolve_enterprise_identities(asset)
    assert len(result["clusters"]) == 1
    cluster = result["clusters"][0]
    assert set(cluster["labels"]) == {"\u751f\u4ea7\u4efb\u52a1\u5355", "MO", "ProductionOrder"}
    assert result["label_to_entity"]["MO"] == cluster["entity_id"]
    assert result["label_to_entity"]["ProductionOrder"] == cluster["entity_id"]


def test_business_entity_id_is_stable_when_canonical_label_changes() -> None:
    asset = _asset([_alias("a", "\u751f\u4ea7\u4efb\u52a1\u5355", "MO"), _rule("r", "MO")])
    first = resolve_enterprise_identities(asset)
    entity_id = first["clusters"][0]["entity_id"]
    asset["business_fact_ledger"]["items"] = [
        _alias("rename", "\u5236\u9020\u5de5\u5355", "\u751f\u4ea7\u4efb\u52a1\u5355", "\u751f\u4ea7\u4efb\u52a1\u5355\u66f4\u540d\u4e3a\u5236\u9020\u5de5\u5355"),
        _rule("r2", "\u5236\u9020\u5de5\u5355"),
    ]
    second = resolve_enterprise_identities(asset)
    assert second["clusters"][0]["entity_id"] == entity_id
    assert second["clusters"][0]["canonical_label"] == "\u5236\u9020\u5de5\u5355"


def test_definition_expression_is_not_promoted_to_same_as() -> None:
    asset = _asset([_alias("definition", "\u91d1\u989d", "\u5355\u4ef7\u00d7\u6570\u91cf", "\u91d1\u989d\u662f\u6307\u5355\u4ef7\u00d7\u6570\u91cf")])
    result = resolve_enterprise_identities(asset)
    edge = result["edges"][0]
    assert edge["evidence_class"] == "DEFINITION"
    assert edge["status"] == "CANDIDATE_ONLY"
    assert edge["automatic_union_allowed"] is False
    assert len(result["clusters"]) == 2


def test_technical_table_is_binding_not_business_object() -> None:
    asset = _asset(
        [_rule("r", "\u9500\u552e\u8ba2\u5355")],
        business_objects=[{"object": "\u9500\u552e\u8ba2\u5355"}],
        data_tables=[{
            "table_id": "table:sales-order",
            "name": "t_sales_order",
            "business_object": "\u9500\u552e\u8ba2\u5355",
            "source_id": "pdm",
            "source_locator": "pdm#t_sales_order",
            "statement": "t_sales_order implements \u9500\u552e\u8ba2\u5355",
        }],
    )
    model = build_enterprise_understanding_model(asset)
    assert [row["name"] for row in model["business_objects"]] == ["\u9500\u552e\u8ba2\u5355"]
    assert model["business_objects"][0]["object_id"].startswith("enterprise_entity:")
    assert len(model["identity_bindings"]) == 1
    assert model["identity_bindings"][0]["artifact_ref"] == "table:sales-order"
    assert model["identity_bindings"][0]["entity_id"] == model["business_objects"][0]["entity_id"]


def test_unbound_technical_asset_is_partial_not_silently_merged() -> None:
    asset = _asset(
        [_rule("r", "\u9500\u552e\u8ba2\u5355")],
        business_objects=[{"object": "\u9500\u552e\u8ba2\u5355"}],
        data_tables=[{"table_id": "table:orders", "name": "orders", "source_id": "db"}],
    )
    result = resolve_enterprise_identities(asset)
    assert result["gate"]["status"] == "PARTIAL_ENTERPRISE_IDENTITY_BINDING"
    assert result["gate"]["entry_allowed"] is True
    assert result["bindings"] == []
    assert result["unknowns"][0]["reason_code"] == "CROSS_SOURCE_IDENTITY_UNRESOLVED"


def test_conflicting_alias_blocks_formal_identity() -> None:
    asset = _asset([
        _alias("a", "\u751f\u4ea7\u4efb\u52a1\u5355", "MO"),
        _alias("b", "\u5236\u9020\u8ba2\u5355", "MO"),
        _rule("r1", "\u751f\u4ea7\u4efb\u52a1\u5355"),
        _rule("r2", "\u5236\u9020\u8ba2\u5355"),
    ])
    model = build_enterprise_understanding_model(asset)
    assert model["identity_gate"]["status"] == "BLOCKED_ENTERPRISE_IDENTITY_CONFLICT"
    assert model["gate"]["entry_allowed"] is False
    assert any(row["kind"] == "TERM_ALIAS_IDENTITY_CONFLICT" for row in model["identity_conflicts"])


def test_original_mentions_and_stable_refs_are_both_retained() -> None:
    fact = _rule("r", "\u751f\u4ea7\u4efb\u52a1\u5355")
    fact["subject"]["entity_mentions"] = ["MO"]
    fact["subject"]["resolution_evidence"] = [
        {"mention": "MO", "resolved_ref": "\u751f\u4ea7\u4efb\u52a1\u5355", "method": "source_backed_term_alias"}
    ]
    asset = _asset([_alias("a", "\u751f\u4ea7\u4efb\u52a1\u5355", "MO"), fact])
    result = resolve_enterprise_identities(asset)
    enriched_fact = asset["business_fact_ledger"]["items"][1]
    assert "MO" in enriched_fact["subject"]["entity_mentions"]
    assert "\u751f\u4ea7\u4efb\u52a1\u5355" in enriched_fact["subject"]["entity_mentions"]
    assert enriched_fact["subject"]["resolved_entity_refs"] == [result["clusters"][0]["entity_id"]]
