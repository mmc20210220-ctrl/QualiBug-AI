from __future__ import annotations

from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator


API_DOC = """
### GET /api/orders
List orders.

### POST /api/orders
Create an order.

Request:
```json
{"addressId": "<address_id>", "items": [{"sku": "<sku>", "qty": 1}]}
```

### POST /api/orders/:id/confirm
Confirm an order.
"""


def _slice(*, family: str, text: str) -> dict:
    return {
        "slice_id": f"BHV_{family}",
        "entity": "order",
        "kind": "invariant",
        "endpoints": ["/api/orders/:id/confirm"],
        "priority": 0.8,
        "source_refs": [{"kind": "hypothesis:llm_reasoner", "quote": text}],
        "_bound_method": "POST",
        "_bound_path": "/api/orders/:id/confirm",
        "_hypothesis_family": family,
        "_invariant_text": text,
    }


def test_bound_idempotency_hypothesis_executes_exact_documented_post_twice() -> None:
    scenario = SemanticScenarioGenerator()._fallback_active_slice(
        _slice(
            family="idempotency",
            text="repeating POST /api/orders/:id/confirm must not duplicate side effects",
        ),
        discovery_round=1,
        api_doc=API_DOC,
        allow_source_runtime=True,
    )

    assert scenario is not None
    assert scenario.execution_policy == "approved_sandbox_write"
    target_steps = [
        step
        for step in scenario.steps
        if step.api_path == "/api/orders/{id}/confirm"
    ]
    assert [step.api_method for step in target_steps] == ["POST", "POST"]
    assert [step.action for step in target_steps] == [
        "execute_bound_idempotency_write",
        "repeat_bound_idempotency_write",
    ]
    assert all(step.api_method != "GET" for step in target_steps)
    assert "IdempotencyOracle.duplicate_submit" in scenario.oracle_rules


def test_unmaterialized_bound_state_write_executes_documented_post_not_get() -> None:
    """Lifecycle-tagged slices must still execute documented POST when materializable.

    Historical bug: state_machine family skipped `_bound_write_scenario` and
    became empty plan_only, so selected mutation routes never reached HTTP.
    """
    scenario = SemanticScenarioGenerator()._fallback_active_slice(
        _slice(
            family="state_machine",
            text="a terminal order must not accept POST /api/orders/:id/confirm",
        ),
        discovery_round=1,
        api_doc=API_DOC,
        allow_source_runtime=True,
    )

    assert scenario is not None
    assert scenario.execution_policy == "approved_sandbox_write"
    target_steps = [
        step for step in scenario.steps if step.api_path == "/api/orders/{id}/confirm"
    ]
    assert target_steps
    assert target_steps[-1].api_method == "POST"
    assert target_steps[-1].action == "execute_bound_write"
    assert all(step.api_method != "GET" or step.api_path != "/api/orders/{id}/confirm" for step in scenario.steps)


def test_bound_write_without_source_endpoint_stays_plan_only_not_get() -> None:
    """If the mutation route is absent from api_doc, keep plan_only (never GET)."""
    thin_doc = """
### GET /api/orders
List orders.
"""
    scenario = SemanticScenarioGenerator()._fallback_active_slice(
        _slice(
            family="state_machine",
            text="a terminal order must not accept POST /api/orders/:id/confirm",
        ),
        discovery_round=1,
        api_doc=thin_doc,
        allow_source_runtime=True,
    )

    assert scenario is not None
    assert scenario.execution_policy == "plan_only_requires_fixture"
    assert scenario.steps == []
    assert scenario.evidence_gaps == ["BOUND_WRITE_PRECONDITION_CONTRACT_MISSING"]


def test_non_lifecycle_bound_write_keeps_documented_mutation_method() -> None:
    scenario = SemanticScenarioGenerator()._fallback_active_slice(
        _slice(
            family="cache",
            text="the documented POST /api/orders must preserve cache consistency",
        ),
        discovery_round=1,
        api_doc=API_DOC,
        allow_source_runtime=True,
    )

    assert scenario is not None
    assert scenario.execution_policy == "approved_sandbox_write"
    target_steps = [
        step for step in scenario.steps if step.api_path == "/api/orders/{id}/confirm"
    ]
    assert target_steps
    assert target_steps[-1].api_method == "POST"
    assert target_steps[-1].action == "execute_bound_write"
