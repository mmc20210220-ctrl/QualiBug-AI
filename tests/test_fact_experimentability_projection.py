from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.fact_experimentability_projection import (
    RECEIPT_SCHEMA,
    LEDGER_SCHEMA,
    project_fact_experimentability,
)


def _base_fact(**overrides):
    fact = {
        "fact_id": "fact:rule-visibility-draft",
        "kind": "RULE",
        "fact_type": "BUSINESS_RULE",
        "status": "ACCEPTED",
        "critical": True,
        "modality": "MUST",
        "subject": {"actor_refs": ["actor_owner"], "entity_refs": ["object_item"]},
        "object": {"entity_refs": ["object_item"]},
        "data_effects": [],
        "state_effects": [],
        "compensations": [],
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.md#1",
                "quote": "draft items are owner-visible only",
            }
        ],
    }
    fact.update(overrides)
    return fact


def _project(facts, *, behaviors=None, bindings=None, operations=None):
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": facts,
        },
        "summary": {},
        "governance": {},
    }
    model = {
        "model_id": "model:test",
        "business_behaviors": behaviors or [],
        "behavior_implementation_bindings": bindings or [],
        "operations": operations or [],
        "metrics": {},
    }
    project_fact_experimentability(asset, model)
    return asset, model


def test_ready_receipt_for_bound_critical_fact() -> None:
    fact = _base_fact(
        data_effects=[{"statement": "create item", "entity": "object_item"}],
        compensations=["delete item"],
    )
    behaviors = [
        {
            "behavior_id": "behavior:draft-visibility",
            "source_refs": ["fact:rule-visibility-draft"],
            "operation_ref": "op:create-item",
            "actor_refs": ["actor_owner", "actor_non_owner"],
        }
    ]
    bindings = [
        {
            "binding_id": "binding:create-item",
            "behavior_ref": "behavior:draft-visibility",
            "operation_ref": "op:create-item",
        }
    ]
    operations = [
        {
            "operation_id": "op:create-item",
            "method": "POST",
            "object_refs": ["object_item"],
        },
        {
            "operation_id": "op:delete-item",
            "method": "DELETE",
            "object_refs": ["object_item"],
            "name": "delete item",
        },
    ]
    asset, _ = _project(
        [fact],
        behaviors=behaviors,
        bindings=bindings,
        operations=operations,
    )
    ledger = asset["fact_experimentability_ledger"]
    assert ledger["schema_version"] == LEDGER_SCHEMA
    assert ledger["receipt_count"] == 1
    assert ledger["silent_drop_count"] == 0
    receipt = ledger["items"][0]
    assert receipt["schema_version"] == RECEIPT_SCHEMA
    assert receipt["status"] == "READY"
    assert receipt["fact_ref"] == "fact:rule-visibility-draft"
    assert receipt["required_operation_refs"] == ["op:create-item"]
    assert "business_effect" in receipt["observer_refs"] or "http_response" in receipt["observer_refs"]


def test_missing_primary_operation() -> None:
    asset, _ = _project([_base_fact()])
    assert asset["fact_experimentability_ledger"]["items"][0]["status"] == (
        "MISSING_PRIMARY_OPERATION"
    )


def test_ambiguous_operation_without_binding() -> None:
    fact = _base_fact()
    behaviors = [
        {
            "behavior_id": "behavior:a",
            "source_refs": ["fact:rule-visibility-draft"],
            "operation_ref": "op:a",
            "actor_refs": ["actor_owner"],
        },
        {
            "behavior_id": "behavior:b",
            "source_refs": ["fact:rule-visibility-draft"],
            "operation_ref": "op:b",
            "actor_refs": ["actor_owner"],
        },
    ]
    asset, _ = _project([fact], behaviors=behaviors)
    assert asset["fact_experimentability_ledger"]["items"][0]["status"] == (
        "AMBIGUOUS_OPERATION"
    )


def test_missing_binding_for_single_semantic_candidate() -> None:
    fact = _base_fact()
    behaviors = [
        {
            "behavior_id": "behavior:one",
            "source_refs": ["fact:rule-visibility-draft"],
            "operation_ref": "op:read",
            "actor_refs": ["actor_owner"],
        }
    ]
    asset, _ = _project([fact], behaviors=behaviors)
    assert asset["fact_experimentability_ledger"]["items"][0]["status"] == "MISSING_BINDING"


def test_missing_observer_when_registry_empty(monkeypatch) -> None:
    import ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.fact_experimentability_projection as mod

    monkeypatch.setattr(mod, "_implemented_observer_ids", lambda: [])
    fact = _base_fact(compensations=["delete"])
    behaviors = [
        {
            "behavior_id": "behavior:one",
            "source_refs": ["fact:rule-visibility-draft"],
            "operation_ref": "op:read",
            "actor_refs": ["actor_owner"],
        }
    ]
    bindings = [
        {
            "binding_id": "binding:read",
            "behavior_ref": "behavior:one",
            "operation_ref": "op:read",
        }
    ]
    operations = [{"operation_id": "op:read", "method": "GET"}]
    asset, _ = _project(
        [fact],
        behaviors=behaviors,
        bindings=bindings,
        operations=operations,
    )
    assert asset["fact_experimentability_ledger"]["items"][0]["status"] == "MISSING_OBSERVER"


def test_non_reversible_write_without_cleanup() -> None:
    fact = _base_fact(
        data_effects=[{"statement": "create", "entity": "object_item"}],
        compensations=[],
    )
    behaviors = [
        {
            "behavior_id": "behavior:write",
            "source_refs": ["fact:rule-visibility-draft"],
            "operation_ref": "op:create",
            "actor_refs": ["actor_owner"],
        }
    ]
    bindings = [
        {
            "binding_id": "binding:create",
            "behavior_ref": "behavior:write",
            "operation_ref": "op:create",
        }
    ]
    operations = [
        {
            "operation_id": "op:create",
            "method": "POST",
            "object_refs": ["object_item"],
        }
    ]
    asset, _ = _project(
        [fact],
        behaviors=behaviors,
        bindings=bindings,
        operations=operations,
    )
    status = asset["fact_experimentability_ledger"]["items"][0]["status"]
    assert status == "NON_REVERSIBLE_WRITE"


def test_not_test_worthy_still_emits_receipt() -> None:
    fact = _base_fact(
        fact_id="fact:term-glossary",
        kind="TERM",
        fact_type="TERM",
        critical=False,
        modality="",
        subject={},
        object={},
    )
    asset, _ = _project([fact])
    receipt = asset["fact_experimentability_ledger"]["items"][0]
    assert receipt["status"] == "NOT_TEST_WORTHY"
    assert asset["fact_experimentability_ledger"]["silent_drop_count"] == 0


def test_receipt_id_stable_across_runs() -> None:
    fact = _base_fact()
    first, _ = _project([fact])
    second, _ = _project([deepcopy(fact)])
    assert (
        first["fact_experimentability_ledger"]["items"][0]["receipt_id"]
        == second["fact_experimentability_ledger"]["items"][0]["receipt_id"]
    )


def test_accepted_fact_coverage_no_silent_drop() -> None:
    facts = [
        _base_fact(fact_id="fact:a"),
        _base_fact(fact_id="fact:b", kind="TERM", fact_type="TERM", critical=False),
        _base_fact(fact_id="fact:pending", status="PENDING"),
    ]
    asset, _ = _project(facts)
    ledger = asset["fact_experimentability_ledger"]
    assert ledger["accepted_fact_count"] == 2
    assert ledger["receipt_count"] == 2
    assert ledger["silent_drop_count"] == 0
    assert {row["fact_ref"] for row in ledger["items"]} == {"fact:a", "fact:b"}
