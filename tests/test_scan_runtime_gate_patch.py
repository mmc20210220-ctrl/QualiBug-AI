from __future__ import annotations

from pathlib import Path

import pytest


OPENAPI_TEXT = """
openapi: 3.0.0
info:
  title: Runtime Gate API
  version: 1.0.0
paths:
  /api/orders:
    get:
      responses:
        '200': {description: ok}
    post:
      responses:
        '201': {description: created}
""".strip()


def _source_manifest(tmp_path: Path) -> dict[str, str]:
    from ai_test_asset_center.enterprise_source_registry import register_source_asset

    return register_source_asset(
        "runtime_gate_project",
        "runtime-gate-openapi",
        OPENAPI_TEXT,
        source_type="openapi",
        root=tmp_path,
        actor={"name": "qa_lead", "role": "qa"},
    )


def _write_contract_without_cleanup() -> dict:
    return {
        "execution_policy": "approved_sandbox_write",
        "actor": {"id": "qa_lead"},
        "scenarios": [
            {
                "id": "create-order-no-cleanup",
                "entity": "orders",
                "steps": [{"method": "POST", "path": "/api/orders", "expected_status": 201}],
            }
        ],
    }


def _read_contract() -> dict:
    return {
        "execution_policy": "safe_read_only",
        "actor": {"id": "qa_lead"},
        "scenarios": [
            {
                "id": "read-orders",
                "entity": "orders",
                "steps": [{"method": "GET", "path": "/api/orders", "expected_status": 200}],
            }
        ],
    }


def test_scan_entry_blocks_write_contract_before_runtime_traffic(tmp_path: Path) -> None:
    from ai_test_asset_center.__main__ import scan

    manifest = _source_manifest(tmp_path)
    result = scan(
        "runtime_gate_project",
        root=tmp_path,
        api_doc_text=OPENAPI_TEXT,
        base_url="http://127.0.0.1:9",
        campaign_context={
            "source_manifest": manifest,
            "scope_id": "orders-scope",
            "environment_ref": "staging",
            "test_data_contract": {"strategy": "create_disposable", "write_approved": False},
            "runtime_scenario_contract": _write_contract_without_cleanup(),
        },
    )

    codes = {item.get("code") for item in result.get("coverage_gaps", []) if isinstance(item, dict)}
    assert result["success"] is True
    assert result["grade"] == "blocked"
    assert result["execution_status"] == "blocked"
    assert result["runtime_contract"]["reason"] == "runtime_scenario_contract_blocked"
    assert "WRITE_APPROVAL_MISSING" in result["runtime_contract"]["missing_requirements"]
    assert "CLEANUP_CONTRACT_MISSING" in result["runtime_contract"]["missing_requirements"]
    assert "WRITE_APPROVAL_MISSING" in codes
    assert "CLEANUP_CONTRACT_MISSING" in codes
    assert result["auto_har"]["status"] == "no_traffic"


def test_runtime_contract_is_injected_into_generator_for_current_scan() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    scenarios = compile_runtime_scenarios(
        {"runtime_scenario_contract": _read_contract()},
        discovery_round=2,
    )

    assert any(item.id == "read-orders" for item in scenarios)
    scenario = next(item for item in scenarios if item.id == "read-orders")
    assert scenario.execution_policy == "safe_read_only"
    assert scenario.behavior_slice_kind == "runtime_contract"
    assert scenario.steps[0].api_method == "GET"
    assert scenario.discovery_round == 2


def test_runtime_contract_rejects_empty_scenario_actor() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    contract = _read_contract()
    contract.pop("actor")
    contract["scenarios"][0]["actors"] = [{}]

    with pytest.raises(
        ValueError,
        match="RUNTIME_SCENARIO_ACTOR_MISSING",
    ):
        compile_runtime_scenarios({"runtime_scenario_contract": contract})


def test_runtime_contract_rejects_explicit_empty_step_actor() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    contract = _read_contract()
    contract["scenarios"][0]["steps"][0]["actor"] = {}

    with pytest.raises(ValueError, match="STEP_ACTOR_ID_MISSING"):
        compile_runtime_scenarios({"runtime_scenario_contract": contract})


def test_runtime_contract_rejects_any_anonymous_or_duplicate_scenario_actor() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    anonymous = _read_contract()
    anonymous["scenarios"][0]["actors"] = [{"id": "admin"}, {}]
    with pytest.raises(ValueError, match="SCENARIO_ACTOR_ID_MISSING"):
        compile_runtime_scenarios({"runtime_scenario_contract": anonymous})

    duplicate = _read_contract()
    duplicate["scenarios"][0]["actors"] = ["admin", {"id": "admin"}]
    with pytest.raises(ValueError, match="SCENARIO_ACTOR_DUPLICATE"):
        compile_runtime_scenarios({"runtime_scenario_contract": duplicate})


def test_runtime_contract_requires_an_actor_for_each_scenario() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    contract = _read_contract()
    contract.pop("actor")
    contract["scenarios"] = [
        {
            "id": "with-actor",
            "actors": [{"id": "buyer"}],
            "steps": [{"method": "GET", "path": "/api/orders"}],
        },
        {
            "id": "anonymous",
            "steps": [{"method": "GET", "path": "/api/orders"}],
        },
    ]

    with pytest.raises(ValueError, match="SCENARIO_ACTOR_MISSING"):
        compile_runtime_scenarios({"runtime_scenario_contract": contract})


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("expected_status", True, "STEP_EXPECTED_STATUS_INVALID"),
        ("expected_status", "200", "STEP_EXPECTED_STATUS_INVALID"),
        ("expected_status", 99, "STEP_EXPECTED_STATUS_INVALID"),
        ("expected_status", 600, "STEP_EXPECTED_STATUS_INVALID"),
        ("order", True, "STEP_ORDER_INVALID"),
        ("order", 0, "STEP_ORDER_INVALID"),
        ("order", -1, "STEP_ORDER_INVALID"),
    ],
)
def test_runtime_contract_rejects_invalid_step_scalar_contracts(
    field: str,
    value: object,
    code: str,
) -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    contract = _read_contract()
    contract["scenarios"][0]["steps"][0][field] = value

    with pytest.raises(ValueError, match=code):
        compile_runtime_scenarios({"runtime_scenario_contract": contract})


def test_runtime_contract_rejects_duplicate_step_order() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    contract = _read_contract()
    contract["scenarios"][0]["steps"] = [
        {"order": 1, "method": "GET", "path": "/api/orders"},
        {"order": 1, "method": "GET", "path": "/api/orders"},
    ]

    with pytest.raises(ValueError, match="STEP_ORDER_DUPLICATE"):
        compile_runtime_scenarios({"runtime_scenario_contract": contract})


@pytest.mark.parametrize("confidence", [True, "0.9", -0.1, 1.1, float("inf"), float("nan")])
def test_runtime_contract_rejects_invalid_confidence(confidence: object) -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import (
        compile_runtime_scenarios,
    )

    contract = _read_contract()
    contract["scenarios"][0]["confidence"] = confidence

    with pytest.raises(ValueError, match="SCENARIO_CONFIDENCE_INVALID"):
        compile_runtime_scenarios({"runtime_scenario_contract": contract})
