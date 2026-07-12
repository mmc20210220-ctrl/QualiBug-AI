from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import inspect

import pytest

from ai_test_asset_center.discovery_mainline_contract import (
    MainlineContractError,
    build_mainline_run_contract,
)


def _inputs(authority: str):
    from ai_test_asset_center.discovery_mainline import DiscoveryMainlineInputs

    return DiscoveryMainlineInputs(
        project="PROJECT-1",
        root=Path("."),
        prd_text="requirement",
        api_spec_text="GET /resources",
        db_schema_text="",
        approved_base_url="http://127.0.0.1:8080",
        campaign_context={"mainline_authority": authority},
    )


def _contract(authority: str, *, campaign_id: str = "CMP-1") -> dict:
    return build_mainline_run_contract(
        mainline_authority=authority,
        run_id="RUN-1",
        campaign_id=campaign_id,
        target_id="TARGET-1",
        environment_id="ENV-1",
        policy_version="v1" if authority == "legacy_champion" else "v2",
        evaluation_mode="replay",
    )


def test_campaign_identity_exists_before_planning_and_execution() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    events: list[str] = []
    contract = _contract("legacy_champion")

    result = run_discovery_mainline(
        _inputs("legacy_champion"),
        build_campaign=lambda _: events.append("campaign") or SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: events.append("plan") or SimpleNamespace(mainline_run=contract),
        legacy_runner=lambda *_: events.append("legacy") or {"mainline_run": contract},
        experiment_runner=lambda *_: events.append("experiment") or {"mainline_run": contract},
    )

    assert events == ["campaign", "plan", "legacy"]
    assert result["mainline_run"]["campaign_id"] == "CMP-1"


def test_one_run_never_invokes_both_runners() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    calls = {"legacy": 0, "experiment": 0}
    contract = _contract("experiment_candidate")

    run_discovery_mainline(
        _inputs("experiment_candidate"),
        build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
        build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
        legacy_runner=lambda *_: calls.__setitem__("legacy", calls["legacy"] + 1) or {"mainline_run": contract},
        experiment_runner=lambda *_: calls.__setitem__("experiment", calls["experiment"] + 1) or {"mainline_run": contract},
    )

    assert calls == {"legacy": 0, "experiment": 1}


def test_runner_failure_never_falls_back_to_other_authority() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    calls = {"legacy": 0, "experiment": 0}
    contract = _contract("experiment_candidate")

    def fail_experiment(*_):
        calls["experiment"] += 1
        raise RuntimeError("candidate failed")

    with pytest.raises(RuntimeError, match="candidate failed"):
        run_discovery_mainline(
            _inputs("experiment_candidate"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            legacy_runner=lambda *_: calls.__setitem__("legacy", calls["legacy"] + 1) or {"mainline_run": contract},
            experiment_runner=fail_experiment,
        )

    assert calls == {"legacy": 0, "experiment": 1}


def test_coordinator_rejects_campaign_or_result_identity_mismatch() -> None:
    from ai_test_asset_center.discovery_mainline import run_discovery_mainline

    contract = _contract("legacy_champion")
    with pytest.raises(MainlineContractError, match="mainline_campaign_identity_mismatch"):
        run_discovery_mainline(
            _inputs("legacy_champion"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-OTHER"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            legacy_runner=lambda *_: {"mainline_run": contract},
            experiment_runner=lambda *_: {"mainline_run": contract},
        )

    wrong_result = _contract("legacy_champion", campaign_id="CMP-OTHER")
    with pytest.raises(MainlineContractError, match="mainline_result_authority_mismatch"):
        run_discovery_mainline(
            _inputs("legacy_champion"),
            build_campaign=lambda _: SimpleNamespace(campaign_id="CMP-1"),
            build_plan=lambda *_: SimpleNamespace(mainline_run=contract),
            legacy_runner=lambda *_: {"mainline_run": wrong_result},
            experiment_runner=lambda *_: {"mainline_run": contract},
        )


def test_v12_establishes_campaign_before_behavior_ir_and_experiment_execution() -> None:
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline

    source = inspect.getsource(run_v12_pipeline)
    campaign_index = source.index("_campaign_context(")
    behavior_ir_index = source.index("build_behavior_ir_from_knowledge_asset(")
    experiment_index = source.index("execute_selected_experiments(")

    assert campaign_index < behavior_ir_index < experiment_index
    assert '["campaign_id"] = campaign.campaign_id' not in source
