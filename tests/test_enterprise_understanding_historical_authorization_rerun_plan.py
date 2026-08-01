"""Historical authorization rerun plans preserve safety and predecessor lineage."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_test_asset_center import historical_authorization_rerun_plan as planner
from ai_test_asset_center.historical_authorization_rerun_plan import (
    HistoricalAuthorizationRerunPlanError,
    build_historical_authorization_rerun_plan,
    main,
    validate_historical_authorization_rerun_plan,
)


def _inventory(*, action: str = "RERUN_REQUIRED") -> dict:
    return {
        "inventory_fingerprint": "a" * 64,
        "projects": [
            {
                "project_id": "alpha",
                "rerun_queue": [
                    {
                        "authority_scope_id": "ledger:" + "b" * 64,
                        "run_id": "run:old",
                        "campaign_id": "campaign:old",
                        "finding_id": "finding:auth",
                        "obligation_id": "obl:auth" if action == "RERUN_REQUIRED" else "",
                        "experiment_id": "exp:auth" if action == "RERUN_REQUIRED" else "",
                        "action": action,
                        "requirements": [
                            "customer_delivery_gate_v2",
                            "authorization_causality_receipt_v1",
                        ],
                        "quarantine_receipt_id": "auth_quarantine:1",
                    }
                ],
            }
        ],
    }


def _binding(*, runtime: str = "RESOLVED", source: str = "RESOLVED") -> dict:
    return {
        "scope_id": "scope:current" if runtime == "RESOLVED" else "",
        "environment_ref": "env:staging" if runtime == "RESOLVED" else "",
        "environment_type": "staging",
        "target_base_url": "https://staging.example.test" if runtime == "RESOLVED" else "",
        "execution_mode": "safe_read_only",
        "write_execution_allowed": False,
        "source_binding_status": source,
        "source_id": "source:api" if source == "RESOLVED" else "",
        "source_hash": "c" * 64 if source == "RESOLVED" else "",
        "source_candidate_count": 1 if source == "RESOLVED" else 2,
        "runtime_status": runtime,
        "missing_runtime_bindings": [] if runtime == "RESOLVED" else ["scope_id"],
        "reason": "",
    }


@pytest.fixture(autouse=True)
def _accept_inventory(monkeypatch) -> None:
    monkeypatch.setattr(
        planner,
        "validate_historical_authorization_inventory",
        lambda value: deepcopy(value),
    )


def test_current_binding_and_approval_produce_controlled_recompile_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(planner, "_runtime_binding", lambda project, root: _binding())
    monkeypatch.setattr(
        planner,
        "_approval_projection",
        lambda project, root, binding: {
            "status": "CURRENT_APPROVAL_FOUND",
            "approval_id": "eap_current",
            "code": "",
        },
    )

    plan = build_historical_authorization_rerun_plan(
        _inventory(),
        root=tmp_path,
        generated_at_utc="2026-08-01T01:00:00Z",
    )

    assert plan["status"] == "READY"
    assert plan["request_count"] == 1
    request = plan["projects"][0]["requests"][0]
    assert request["status"] == "READY_FOR_CONTROLLED_RECOMPILE"
    assert request["action"] == "RECOMPILE_AND_REEXECUTE_AUTHORIZATION_EXPERIMENT"
    assert request["predecessor"] == {
        "authority_scope_id": "ledger:" + "b" * 64,
        "run_id": "run:old",
        "campaign_id": "campaign:old",
        "finding_id": "finding:auth",
        "obligation_id": "obl:auth",
        "experiment_id": "exp:auth",
        "quarantine_receipt_id": "auth_quarantine:1",
    }
    policy = request["execution_policy"]
    assert policy["auto_execute"] is False
    assert policy["old_compiled_experiment_replay_allowed"] is False
    assert policy["new_run_id_required"] is True
    assert policy["new_campaign_id_required"] is True
    assert policy["new_execution_id_required"] is True
    assert policy["write_execution_allowed"] is False
    assert validate_historical_authorization_rerun_plan(plan) == plan


def test_missing_approval_stops_at_ready_for_approval(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(planner, "_runtime_binding", lambda project, root: _binding())
    monkeypatch.setattr(
        planner,
        "_approval_projection",
        lambda project, root, binding: {
            "status": "APPROVAL_REQUIRED",
            "approval_id": "",
            "code": "EXECUTION_APPROVAL_NOT_FOUND",
        },
    )

    plan = build_historical_authorization_rerun_plan(_inventory(), root=tmp_path)

    assert plan["status"] == "MANUAL_ACTION_REQUIRED"
    assert plan["projects"][0]["requests"][0]["status"] == "READY_FOR_APPROVAL"


def test_manual_recompile_request_never_becomes_execution_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(planner, "_runtime_binding", lambda project, root: _binding())
    monkeypatch.setattr(
        planner,
        "_approval_projection",
        lambda project, root, binding: {
            "status": "CURRENT_APPROVAL_FOUND",
            "approval_id": "eap_current",
            "code": "",
        },
    )

    plan = build_historical_authorization_rerun_plan(
        _inventory(action="MANUAL_RECOMPILE_REQUIRED"),
        root=tmp_path,
    )

    request = plan["projects"][0]["requests"][0]
    assert request["status"] == "MANUAL_RECOMPILE_REQUIRED"
    assert request["predecessor"]["obligation_id"] == ""
    assert request["predecessor"]["experiment_id"] == ""


@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        (_binding(runtime="INCOMPLETE"), "BLOCKED_RUNTIME_BINDING"),
        (_binding(source="AMBIGUOUS"), "BLOCKED_SOURCE_BINDING"),
    ],
)
def test_incomplete_current_binding_blocks_recompile(
    monkeypatch,
    tmp_path: Path,
    binding: dict,
    expected: str,
) -> None:
    monkeypatch.setattr(planner, "_runtime_binding", lambda project, root: deepcopy(binding))
    monkeypatch.setattr(
        planner,
        "_approval_projection",
        lambda project, root, binding: {
            "status": "NOT_RESOLVABLE",
            "approval_id": "",
            "code": "CURRENT_BINDING_INCOMPLETE",
        },
    )

    plan = build_historical_authorization_rerun_plan(_inventory(), root=tmp_path)

    assert plan["status"] == "BLOCKED"
    assert plan["projects"][0]["requests"][0]["status"] == expected


def test_request_policy_tamper_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(planner, "_runtime_binding", lambda project, root: _binding())
    monkeypatch.setattr(
        planner,
        "_approval_projection",
        lambda project, root, binding: {
            "status": "CURRENT_APPROVAL_FOUND",
            "approval_id": "eap_current",
            "code": "",
        },
    )
    plan = build_historical_authorization_rerun_plan(_inventory(), root=tmp_path)
    plan["projects"][0]["requests"][0]["execution_policy"]["auto_execute"] = True
    plan["plan_fingerprint"] = planner._fingerprint(
        {key: value for key, value in plan.items() if key != "plan_fingerprint"}
    )

    with pytest.raises(
        HistoricalAuthorizationRerunPlanError,
        match="historical_authorization_rerun_request_policy_invalid",
    ):
        validate_historical_authorization_rerun_plan(plan)


def test_cli_writes_plan_only_and_never_executes(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(_inventory()), encoding="utf-8")
    output = tmp_path / "rerun-plan.json"
    monkeypatch.setattr(planner, "_runtime_binding", lambda project, root: _binding())
    monkeypatch.setattr(
        planner,
        "_approval_projection",
        lambda project, root, binding: {
            "status": "APPROVAL_REQUIRED",
            "approval_id": "",
            "code": "EXECUTION_APPROVAL_NOT_FOUND",
        },
    )

    exit_code = main([
        "--root", str(tmp_path),
        "--inventory", str(inventory_path),
        "--output", str(output),
        "--compact",
    ])

    assert exit_code == 0
    assert output.is_file()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["auto_execute"] is False
    assert written["source_artifacts_modified"] is False
    assert json.loads(capsys.readouterr().out)["plan_fingerprint"] == written["plan_fingerprint"]
