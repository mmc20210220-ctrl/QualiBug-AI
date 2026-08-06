from __future__ import annotations

from dataclasses import dataclass

import pytest

from ai_test_asset_center import discovery_runtime_semantic_binding as binding
from ai_test_asset_center.llm_reasoning import ReasoningConfig


@dataclass(frozen=True)
class _Inputs:
    campaign_context: dict


def _enabled_config() -> ReasoningConfig:
    return ReasoningConfig(
        base_url="https://provider.example/v1",
        api_key="test-key",
        model="test-model",
    )


def test_deep_scan_auto_enables_governed_semantic_linker_when_provider_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binding.ReasoningConfig,
        "from_env",
        classmethod(lambda cls: _enabled_config()),
    )

    inputs = _Inputs(campaign_context={
        "execution_mode": "approved_sandbox_write",
    })
    resolved = binding._planning_inputs_with_declared_adapters(inputs)

    assert resolved.campaign_context["agent_semantic_linking_enabled"] is True
    assert resolved.campaign_context[
        "agent_semantic_linking_enablement_basis"
    ] == "auto_enabled_configured_provider_approved_sandbox"


def test_read_only_scan_does_not_auto_spend_semantic_provider_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binding.ReasoningConfig,
        "from_env",
        classmethod(lambda cls: _enabled_config()),
    )

    resolved = binding._planning_inputs_with_declared_adapters(
        _Inputs(campaign_context={"execution_mode": "safe_read_only"})
    )

    assert resolved.campaign_context["agent_semantic_linking_enabled"] is False
    assert resolved.campaign_context[
        "agent_semantic_linking_enablement_basis"
    ] == "execution_mode_not_approved_sandbox"


def test_explicit_semantic_linker_kill_switch_always_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binding.ReasoningConfig,
        "from_env",
        classmethod(lambda cls: _enabled_config()),
    )

    resolved = binding._planning_inputs_with_declared_adapters(
        _Inputs(campaign_context={
            "execution_mode": "approved_sandbox_write",
            "agent_semantic_linking_enabled": False,
        })
    )

    assert resolved.campaign_context["agent_semantic_linking_enabled"] is False
    assert resolved.campaign_context[
        "agent_semantic_linking_enablement_basis"
    ] == "explicit_scan_control"


def test_missing_provider_keeps_source_only_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        binding.ReasoningConfig,
        "from_env",
        classmethod(lambda cls: ReasoningConfig()),
    )

    resolved = binding._planning_inputs_with_declared_adapters(
        _Inputs(campaign_context={"execution_mode": "approved_sandbox_write"})
    )

    assert resolved.campaign_context["agent_semantic_linking_enabled"] is False
    assert resolved.campaign_context[
        "agent_semantic_linking_enablement_basis"
    ] == "provider_not_configured"


def test_provider_failure_is_visible_and_preserves_source_only_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*args, **kwargs):
        raise binding.AgentSemanticLinkerError(
            "agent_semantic_provider_failed:TimeoutError:provider timeout"
        )

    monkeypatch.setattr(binding, "_governed_agent_semantic_linker", _fail)
    asset = {
        "asset_id": "asset-unfamiliar",
        "rule_library": [{"rule_id": "rule-1", "statement": "A valid rule"}],
        "interfaces": [{
            "interface_id": "api:POST:/policies/renew",
            "method": "POST",
            "path": "/policies/renew",
        }],
        "relationships": [],
    }

    preserved, receipt = binding._agent_semantic_linker_with_visible_failure(asset)

    assert preserved["rule_library"] == asset["rule_library"]
    assert preserved["interfaces"] == asset["interfaces"]
    assert preserved["relationships"] == []
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "agent_semantic_linking_failed"
    assert receipt["accepted_relationship_count"] == 0
    assert receipt["source_asset_preserved"] is True
    assert receipt["semantic_linking_degraded_to_source_only"] is True
    assert receipt["parallel_semantic_linker_created"] is False


def test_empty_semantic_inputs_are_not_a_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _empty(*args, **kwargs):
        raise binding.AgentSemanticLinkerError("agent_semantic_inputs_empty")

    monkeypatch.setattr(binding, "_governed_agent_semantic_linker", _empty)

    preserved, receipt = binding._agent_semantic_linker_with_visible_failure({})

    assert receipt["status"] == "NOT_APPLICABLE"
    assert receipt["reason_code"] == "agent_semantic_inputs_empty"
    assert preserved["agent_semantic_link_receipt"] == receipt


@pytest.mark.parametrize(
    "detail",
    [
        "evaluator_private_context_forbidden:$.ground_truth_ref",
        "agent_semantic_duplicate_identity:rule_library:rule-1",
        "knowledge_asset_not_object",
    ],
)
def test_source_integrity_failures_still_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    detail: str,
) -> None:
    def _fatal(*args, **kwargs):
        raise binding.AgentSemanticLinkerError(detail)

    monkeypatch.setattr(binding, "_governed_agent_semantic_linker", _fatal)

    with pytest.raises(binding.AgentSemanticLinkerError, match="^" + detail):
        binding._agent_semantic_linker_with_visible_failure({})


def test_planning_consumes_the_single_visible_failure_wrapper() -> None:
    assert (
        binding._planning.enrich_knowledge_asset_with_agent_relationships
        is binding._agent_semantic_linker_with_visible_failure
    )
