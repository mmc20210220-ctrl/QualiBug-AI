from __future__ import annotations

from ai_test_asset_center.hypothesis_slice_bridge import hypotheses_to_slices
from ai_test_asset_center.policy_registry import StrategyBundle
from ai_test_asset_center.policy_wiring import policy_strategy_override


def test_default_source_operation_id_strategy_binds_documented_operation() -> None:
    hypotheses = [{
        "hypothesis_id": "operation-bound",
        "title": "verify unrelated invariant",
        "operation_id": "createWidget",
        "family": "invariant",
    }]
    endpoints = [{
        "path": "/v9/widgets",
        "method": "POST",
        "entity": "widgets",
        "operation_id": "createWidget",
    }]

    slices, funnel = hypotheses_to_slices(hypotheses, api_endpoints=endpoints, origin="llm_reasoner")

    assert funnel["bound"] == 1
    assert slices[0]["_bound_path"] == "/v9/widgets"


def test_observed_operation_binding_candidate_changes_real_bridge_output() -> None:
    hypotheses = [{
        "hypothesis_id": "observed-bound",
        "title": "verify unrelated invariant",
        "family": "invariant",
        "observed_operation": {"method": "GET", "path": "/x9/z8"},
    }]
    endpoints = [{"path": "/x9/z8", "method": "GET", "entity": "resource"}]
    baseline = StrategyBundle()
    challenger = StrategyBundle()
    challenger.discovery.endpoint_binding_strategy.append("observed_operation_binding")

    with policy_strategy_override(baseline):
        _, baseline_funnel = hypotheses_to_slices(
            hypotheses, api_endpoints=endpoints, origin="llm_reasoner"
        )
    with policy_strategy_override(challenger):
        challenger_slices, challenger_funnel = hypotheses_to_slices(
            hypotheses, api_endpoints=endpoints, origin="llm_reasoner"
        )

    assert baseline_funnel["bound"] == 0
    assert challenger_funnel["bound"] == 1
    assert challenger_slices[0]["_bound_path"] == "/x9/z8"


def test_schema_parameter_compatibility_candidate_binds_without_keyword_guessing() -> None:
    hypotheses = [{
        "hypothesis_id": "parameter-bound",
        "title": "verify unrelated invariant",
        "family": "invariant",
        "method": "POST",
        "request_parameters": {"opaqueKey": "value"},
    }]
    endpoints = [{
        "path": "/a1/b2",
        "method": "POST",
        "entity": "resource",
        "parameters": [{"name": "opaqueKey", "in": "query"}],
    }]
    strategy = StrategyBundle()
    strategy.discovery.endpoint_binding_strategy.append("schema_parameter_compatibility")

    with policy_strategy_override(strategy):
        slices, funnel = hypotheses_to_slices(
            hypotheses, api_endpoints=endpoints, origin="analyzer"
        )

    assert funnel["bound"] == 1
    assert slices[0]["_bound_method"] == "POST"
