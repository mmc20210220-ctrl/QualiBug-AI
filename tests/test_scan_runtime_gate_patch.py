from __future__ import annotations

from pathlib import Path


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
    import ai_test_asset_center  # noqa: F401 - package import installs the patch
    from ai_test_asset_center.scan_runtime_gate_patch import _RUNTIME_CONTRACT
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    token = _RUNTIME_CONTRACT.set(_read_contract())
    try:
        scenarios = SemanticScenarioGenerator().generate({})
    finally:
        _RUNTIME_CONTRACT.reset(token)

    assert any(item.id == "read-orders" for item in scenarios)
    scenario = next(item for item in scenarios if item.id == "read-orders")
    assert scenario.execution_policy == "safe_read_only"
    assert scenario.behavior_slice_kind == "runtime_contract"
    assert scenario.steps[0].api_method == "GET"
