from __future__ import annotations

from ai_test_asset_center import disposable_fixture_contract as legacy_fixture
from ai_test_asset_center import experiment_compiler_obligation as obligation
from ai_test_asset_center.experiment_compile_freezer import (
    _legacy_projection_input,
)


def test_public_obligation_compiler_scopes_flow_data_authority(monkeypatch) -> None:
    captured: dict = {}

    def fake_compile(
        obligation_row,
        *,
        behavior_ir,
        environment_type="",
        policy_version="",
        available_adapters=None,
    ):
        captured["obligation"] = obligation_row
        captured["behavior_ir"] = behavior_ir
        captured["environment_type"] = environment_type
        captured["policy_version"] = policy_version
        captured["available_adapters"] = available_adapters
        return {"compile_receipt": {"status": "COMPILED"}}

    monkeypatch.setattr(
        obligation._core,
        "compile_experiment_for_obligation",
        fake_compile,
    )
    source_ir = {"operations": [{"id": "op_1"}], "relations": []}

    result = obligation.compile_experiment_for_obligation(
        {"obligation_id": "obl_1"},
        behavior_ir=source_ir,
        environment_type="staging",
        policy_version="policy_1",
        available_adapters={"http_api"},
    )

    scoped = captured["behavior_ir"]
    assert result["compile_receipt"]["status"] == "COMPILED"
    assert scoped == source_ir
    assert scoped is not source_ir
    assert scoped.fixture_data_authority == "flow_data_requirement"
    assert not hasattr(source_ir, "fixture_data_authority")
    assert captured["environment_type"] == "staging"
    assert captured["policy_version"] == "policy_1"
    assert captured["available_adapters"] == {"http_api"}


def test_scoped_compile_never_runs_legacy_candidate_discovery(monkeypatch) -> None:
    def forbidden_discovery(*args, **kwargs):
        raise AssertionError("legacy discovery must not run in final-flow scope")

    monkeypatch.setattr(
        legacy_fixture._core,
        "discover_fixture_candidates",
        forbidden_discovery,
    )
    scoped = obligation._AuthorityScopedBehaviorIR(
        {"operations": [{"id": "op_create", "method": "POST"}]},
        fixture_data_authority=obligation.FLOW_DATA_AUTHORITY,
    )

    assert legacy_fixture.discover_fixture_candidates(scoped) == []


def test_plain_legacy_call_remains_compatible(monkeypatch) -> None:
    expected = [{"create_operation_id": "op_create", "status": "RESOLVED"}]
    captured: dict = {}

    def fake_discovery(behavior_ir, *, entity_ids=None):
        captured["behavior_ir"] = behavior_ir
        captured["entity_ids"] = entity_ids
        return expected

    monkeypatch.setattr(
        legacy_fixture._core,
        "discover_fixture_candidates",
        fake_discovery,
    )
    source = {"operations": []}

    result = legacy_fixture.discover_fixture_candidates(
        source,
        entity_ids=["entity_item"],
    )

    assert result is expected
    assert captured["behavior_ir"] is source
    assert captured["entity_ids"] == ["entity_item"]


def test_legacy_v15_contract_fields_are_normalized_for_projection_only() -> None:
    experiment = {
        "disposable_fixture_contract": {
            "fixture_id": "fixture_1",
            "primary_entity_id": "entity_item",
            "create_plan": [
                {
                    "step_id": "fixture_create_item",
                    "operation_ref": "op_create_item",
                }
            ],
        }
    }

    normalized = _legacy_projection_input(experiment)

    assert normalized is not experiment
    contract = normalized["disposable_fixture_contract"]
    assert contract["create_operation_ref"] == "op_create_item"
    assert contract["entity_ref"] == "entity_item"
    assert "create_operation_ref" not in experiment["disposable_fixture_contract"]
    assert "entity_ref" not in experiment["disposable_fixture_contract"]
