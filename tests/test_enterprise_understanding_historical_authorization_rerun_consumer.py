"""Controlled historical authorization reruns preserve the current mainline."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from ai_test_asset_center import historical_authorization_rerun_consumer as consumer
from ai_test_asset_center.discovery_mainline import (
    DiscoveryMainlineInputs,
    DiscoveryPlanningBundle,
)
from ai_test_asset_center.historical_authorization_rerun_consumer import (
    HistoricalAuthorizationRerunConsumptionError,
    consume_historical_authorization_rerun_plan,
    validate_historical_authorization_rerun_consumption,
    write_historical_authorization_rerun_consumption,
)


def _binding(**overrides) -> dict:
    value = {
        "scope_id": "scope:current",
        "environment_ref": "env:staging",
        "environment_type": "staging",
        "target_base_url": "https://staging.example.test",
        "execution_mode": "safe_read_only",
        "write_execution_allowed": False,
        "source_binding_status": "RESOLVED",
        "source_id": "source:api",
        "source_hash": "a" * 64,
        "source_candidate_count": 1,
        "runtime_status": "RESOLVED",
        "missing_runtime_bindings": [],
        "reason": "",
    }
    value.update(overrides)
    return value


def _request(*, status: str = "READY_FOR_CONTROLLED_RECOMPILE") -> dict:
    return {
        "schema_version": "qualibug.historical-authorization-rerun-request.v1",
        "status": status,
        "action": "RECOMPILE_AND_REEXECUTE_AUTHORIZATION_EXPERIMENT",
        "project_id": "alpha",
        "predecessor": {
            "authority_scope_id": "ledger:" + "b" * 64,
            "run_id": "run:old",
            "campaign_id": "campaign:old",
            "finding_id": "finding:old",
            "obligation_id": "obl:auth",
            "experiment_id": "exp:old",
            "quarantine_receipt_id": "auth_quarantine:1",
        },
        "requirements": ["customer_delivery_gate_v2"],
        "current_runtime_binding": _binding(),
        "approval": {
            "status": "CURRENT_APPROVAL_FOUND",
            "approval_id": "eap_current",
            "code": "",
        },
        "execution_policy": {
            "auto_execute": False,
            "old_compiled_experiment_replay_allowed": False,
            "new_run_id_required": True,
            "new_campaign_id_required": True,
            "new_execution_id_required": True,
            "current_source_revalidation_required": True,
            "current_authorization_comparison_contract_required": True,
            "current_causality_receipt_required": True,
            "current_binding_identity_receipts_required": True,
            "customer_delivery_gate_v2_required": True,
            "execution_mode": "safe_read_only",
            "write_execution_allowed": False,
        },
        "request_id": "auth_rerun_123",
        "request_fingerprint": "c" * 64,
    }


def _plan(*, request: dict | None = None) -> dict:
    item = request or _request()
    return {
        "plan_fingerprint": "d" * 64,
        "projects": [{"project_id": "alpha", "requests": [item]}],
    }


@pytest.fixture(autouse=True)
def _accept_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        consumer,
        "validate_historical_authorization_rerun_plan",
        lambda value: deepcopy(value),
    )


def _fresh(monkeypatch, *, binding: dict | None = None, approval: dict | None = None, drift=None) -> None:
    monkeypatch.setattr(
        consumer,
        "_fresh_authority",
        lambda project_id, root, request: (
            deepcopy(binding or _binding()),
            deepcopy(approval or {
                "status": "CURRENT_APPROVAL_FOUND",
                "approval_id": "eap_current",
                "code": "",
            }),
            list(drift or []),
        ),
    )


def test_without_execute_flag_never_calls_mainline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: pytest.fail("mainline must not run"),
    )

    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=False,
        generated_at_utc="2026-08-01T02:00:00Z",
    )

    assert report["status"] == "NOT_EXECUTED"
    assert report["execution_requested"] is False
    receipt = report["receipts"][0]
    assert receipt["status"] == "NOT_EXECUTED"
    assert receipt["historical_quarantine_supersession_allowed"] is False
    assert receipt["historical_finding_republication_allowed"] is False


def test_not_ready_request_is_skipped_even_when_execute_requested(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: pytest.fail("mainline must not run"),
    )

    report = consume_historical_authorization_rerun_plan(
        _plan(request=_request(status="READY_FOR_APPROVAL")),
        root=tmp_path,
        execute=True,
    )

    assert report["status"] == "NOT_EXECUTED"
    assert report["receipts"][0]["status"] == "SKIPPED_NOT_READY"


def test_binding_drift_blocks_before_execution(monkeypatch, tmp_path: Path) -> None:
    _fresh(monkeypatch, binding=_binding(source_hash="e" * 64), drift=["source_hash"])
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: pytest.fail("mainline must not run"),
    )

    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=True,
    )

    receipt = report["receipts"][0]
    assert report["status"] == "BLOCKED"
    assert receipt["status"] == "BLOCKED_BINDING_DRIFT"
    assert receipt["reason"] == "CURRENT_AUTHORITY_DRIFT:source_hash"


def test_missing_or_expired_approval_is_not_misreported_as_binding_drift(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _fresh(
        monkeypatch,
        approval={
            "status": "APPROVAL_REQUIRED",
            "approval_id": "",
            "code": "EXECUTION_APPROVAL_EXPIRED",
        },
    )
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: pytest.fail("mainline must not run"),
    )

    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=True,
    )

    receipt = report["receipts"][0]
    assert receipt["status"] == "BLOCKED_APPROVAL"
    assert receipt["reason"] == "EXECUTION_APPROVAL_EXPIRED"


def _install_successor_validators(monkeypatch, *, terminal: str) -> None:
    finding_id = "finding:new" if terminal == "DELIVERABLE" else ""
    monkeypatch.setattr(
        consumer,
        "validate_mainline_run_contract",
        lambda value: {
            "run_id": "run:new",
            "campaign_id": "campaign:new",
            "contract_fingerprint": "f" * 64,
        },
    )
    monkeypatch.setattr(
        consumer,
        "validate_obligation_attempt_ledger",
        lambda value: {
            "selected_count": 1,
            "terminal_count": 1,
            "ledger_fingerprint": "1" * 64,
            "attempts": [{
                "obligation_id": "obl:auth",
                "experiment_id": "exp:new",
                "execution_id": "execution:new",
                "terminal_stage": "gate",
                "terminal_status": terminal,
                "reason_code": (
                    "" if terminal == "DELIVERABLE" else "HYPOTHESIS_REJECTED"
                ),
                "gate_receipt_id": "gate:new",
                "finding_id": finding_id,
                "gate_receipt": {
                    "schema_version":
                        consumer.CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA,
                },
            }],
        },
    )


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        ("DELIVERABLE", "CURRENT_DEFECT_REPRODUCED"),
        ("REJECTED", "CURRENT_DEFECT_NOT_REPRODUCED"),
    ],
)
def test_gate_v2_terminal_supersedes_quarantine_without_republishing_history(
    monkeypatch,
    tmp_path: Path,
    terminal: str,
    expected: str,
) -> None:
    _fresh(monkeypatch)
    _install_successor_validators(monkeypatch, terminal=terminal)
    finding_ids = ["finding:new"] if terminal == "DELIVERABLE" else []
    canonical_ids = ["defect:new"] if terminal == "DELIVERABLE" else []
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: {
            "mainline_run": {},
            "obligation_attempt_ledger": {},
            "formal_count_projection": {
                "delivery_occurrence_finding_ids": finding_ids,
            },
            "canonical_defect_registry": {
                "canonical_defect_ids": canonical_ids,
            },
        },
    )

    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=True,
    )

    assert report["status"] == "COMPLETED"
    receipt = report["receipts"][0]
    assert receipt["status"] == expected
    assert receipt["historical_quarantine_supersession_allowed"] is True
    assert receipt["historical_finding_republication_allowed"] is False
    assert receipt["successor"]["run_id"] == "run:new"
    assert receipt["successor"]["campaign_id"] == "campaign:new"
    assert receipt["successor"]["experiment_id"] == "exp:new"


def test_compile_terminal_does_not_supersede_quarantine(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _fresh(monkeypatch)
    monkeypatch.setattr(
        consumer,
        "validate_mainline_run_contract",
        lambda value: {
            "run_id": "run:new",
            "campaign_id": "campaign:new",
            "contract_fingerprint": "f" * 64,
        },
    )
    monkeypatch.setattr(
        consumer,
        "validate_obligation_attempt_ledger",
        lambda value: {
            "selected_count": 1,
            "terminal_count": 1,
            "ledger_fingerprint": "1" * 64,
            "attempts": [{
                "obligation_id": "obl:auth",
                "experiment_id": "exp:new",
                "execution_id": "",
                "terminal_stage": "compile",
                "terminal_status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "gate_receipt_id": "",
                "finding_id": "",
                "gate_receipt": {},
            }],
        },
    )
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: {
            "mainline_run": {},
            "obligation_attempt_ledger": {},
            "formal_count_projection": {},
            "canonical_defect_registry": {},
        },
    )

    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=True,
    )

    receipt = report["receipts"][0]
    assert report["status"] == "BLOCKED"
    assert receipt["status"] == "RECOMPILE_BLOCKED"
    assert receipt["historical_quarantine_supersession_allowed"] is False


def test_targeted_bundle_keeps_only_current_target_obligation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    mainline = {"run_id": "run:new"}
    target = {
        "obligation_id": "obl:auth",
        "risk_family": "authorization",
        "required_operations": ["op:read"],
        "required_actors": [],
        "relation_refs": [],
    }
    other = {
        "obligation_id": "obl:other",
        "risk_family": "validation",
        "required_operations": ["op:other"],
        "required_actors": [],
        "relation_refs": [],
    }
    target_exp = {
        "obligation_id": "obl:auth",
        "experiment_id": "exp:new",
        "compile_receipt": {"status": "COMPILED"},
    }
    other_exp = {
        "obligation_id": "obl:other",
        "experiment_id": "exp:other",
        "compile_receipt": {"status": "COMPILED"},
    }
    full = DiscoveryPlanningBundle(
        mainline_run=mainline,
        behavior_ir={"model_id": "ir:1", "operations": [], "actors": [], "relations": []},
        obligations={"obligations": [target, other]},
        experiments={
            "experiments": [target_exp, other_exp],
            "blocked_experiments": [],
            "all_experiments": [target_exp, other_exp],
            "by_obligation": {
                "obl:auth": target_exp,
                "obl:other": other_exp,
            },
            "obligation_plan": {
                "selected": [
                    {"obligation_id": "obl:auth", "score": 0.8},
                    {"obligation_id": "obl:other", "score": 0.7},
                ],
                "pending_next_round": [],
            },
            "runtime_contract": {
                "status": "approved",
                "approved_base_url": "https://staging.example.test",
            },
        },
    )
    inputs = DiscoveryMainlineInputs(
        project="alpha",
        root=tmp_path,
        prd_text="",
        api_spec_text="{}",
        db_schema_text="",
        approved_base_url="https://staging.example.test",
        campaign_context={},
    )
    monkeypatch.setattr(
        consumer,
        "build_agent_intent_plan",
        lambda plan, obligations, experiments_by_obligation, behavior_ir: {
            "status": "VERIFIED",
            "intents": [{
                "obligation_id": "obl:auth",
                "experiment_id": "exp:new",
            }],
        },
    )
    monkeypatch.setattr(
        consumer,
        "run_environment_preflight",
        lambda **kwargs: {"status": "READY"},
    )
    monkeypatch.setattr(
        consumer,
        "build_planning_budget_receipt",
        lambda budget: {"budget": budget},
    )
    monkeypatch.setattr(
        consumer,
        "finalize_planning_budget_receipt",
        lambda receipt, consumed_budget, stop_condition: {
            **receipt,
            "consumed_budget": consumed_budget,
            "stop_condition": stop_condition,
        },
    )

    targeted = consumer._targeted_planning_bundle(
        full,
        required_obligation_ids=["obl:auth"],
        inputs=inputs,
    )

    assert [row["obligation_id"] for row in targeted.obligations["obligations"]] == [
        "obl:auth"
    ]
    assert set(targeted.experiments["by_obligation"]) == {"obl:auth"}
    assert targeted.experiments["obligation_plan"]["selected_count"] == 1
    assert targeted.experiments["runtime_interface_discovery_enabled"] is False
    selection = targeted.experiments[
        "historical_authorization_remediation_selection"
    ]
    assert selection["other_obligation_execution_allowed"] is False


def test_target_missing_from_current_model_fails_closed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    full = DiscoveryPlanningBundle(
        mainline_run={"run_id": "run:new"},
        behavior_ir={},
        obligations={"obligations": []},
        experiments={"by_obligation": {}},
    )
    inputs = DiscoveryMainlineInputs(
        project="alpha",
        root=tmp_path,
        prd_text="",
        api_spec_text="{}",
        db_schema_text="",
        approved_base_url="",
        campaign_context={},
    )

    with pytest.raises(
        HistoricalAuthorizationRerunConsumptionError,
        match="current_target_obligation_not_found:obl:auth",
    ):
        consumer._targeted_planning_bundle(
            full,
            required_obligation_ids=["obl:auth"],
            inputs=inputs,
        )


def test_resigned_terminal_scope_tamper_is_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _fresh(monkeypatch)
    _install_successor_validators(monkeypatch, terminal="DELIVERABLE")
    monkeypatch.setattr(
        consumer,
        "_run_targeted_mainline",
        lambda **kwargs: {
            "mainline_run": {},
            "obligation_attempt_ledger": {},
            "formal_count_projection": {
                "delivery_occurrence_finding_ids": ["finding:new"],
            },
            "canonical_defect_registry": {
                "canonical_defect_ids": ["defect:new"],
            },
        },
    )
    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=True,
    )
    receipt = report["receipts"][0]
    receipt["successor"]["terminal_status"] = "REJECTED"
    receipt["receipt_fingerprint"] = consumer._fingerprint({
        key: value for key, value in receipt.items()
        if key != "receipt_fingerprint"
    })
    report["consumption_fingerprint"] = consumer._fingerprint({
        key: value for key, value in report.items()
        if key != "consumption_fingerprint"
    })

    with pytest.raises(
        HistoricalAuthorizationRerunConsumptionError,
        match="historical_authorization_remediation_reproduced_scope_invalid",
    ):
        validate_historical_authorization_rerun_consumption(report)


def test_writer_preserves_ordinary_scan_result_and_uses_isolated_receipt_path(
    tmp_path: Path,
) -> None:
    report = consume_historical_authorization_rerun_plan(
        _plan(),
        root=tmp_path,
        execute=False,
        generated_at_utc="2026-08-01T02:00:00Z",
    )
    scan_result = tmp_path / "platform_outputs" / "alpha" / "scan_result.json"
    scan_result.parent.mkdir(parents=True, exist_ok=True)
    scan_result.write_bytes(b'{"ordinary":true}\n')
    before = scan_result.read_bytes()
    output = tmp_path / "reports" / "consumption.json"

    written = write_historical_authorization_rerun_consumption(
        report,
        output=output,
        root=tmp_path,
    )

    assert written == output
    assert scan_result.read_bytes() == before
    receipt_path = (
        tmp_path
        / "platform_outputs"
        / "alpha"
        / "historical_authorization_remediation"
        / "auth_rerun_123"
        / "remediation_receipt.json"
    )
    assert receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["ordinary_scan_result_modified"] is False
