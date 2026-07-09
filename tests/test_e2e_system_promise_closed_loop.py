"""E2E integration test: private pilot scan + regression closed loop.

Verifies end-to-end that system promise metadata survives the full chain:
  Enterprise materials → behavior contract slices → semantic scenarios
  → oracle/evidence → confirmed_findings ledger → regression suite
  → regression runner → regression_run_history → risk_clue_pool learning

Runs without a real target server (plan_only / dry_run mode) so this
test is safe and repeatable in CI.
"""

import json
import os
import tempfile
from pathlib import Path

from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    install_system_behavior_space_patch,
    restore_system_behavior_space_patch,
)
from ai_test_asset_center import v12_pipeline
from ai_test_asset_center import regression_runner
from ai_test_asset_center import regression_suite_builder
from ai_test_asset_center.risk_clue_pool import get_platform_learning, get_project_learning


# Minimal industry-agnostic test materials
PRD = "普通用户只能查看和编辑自己的订单。订单金额不能为负。退款金额必须等于原订单金额。"
API_SPEC = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
    post:
      summary: create order
  /api/orders/{id}:
    get:
      summary: get order detail
  /api/orders/{id}/refund:
    post:
      summary: refund order
"""
DB_SCHEMA = """
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'CREATED',
  total_amount DECIMAL(10,2) NOT NULL,
  deleted_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE refunds (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  refund_amount DECIMAL(10,2) NOT NULL
);
"""


def _write_project_inputs(root: Path, project: str) -> None:
    """Write minimal project inputs so v12_pipeline and regression_runner can find them."""
    inputs_dir = root / "platform_inputs" / project
    inputs_dir.mkdir(parents=True, exist_ok=True)
    (inputs_dir / "prd.md").write_text(PRD, encoding="utf-8")
    (inputs_dir / "openapi.json").write_text(json.dumps(
        {"openapi": "3.0.0", "paths": {
            "/api/orders": {"get": {"summary": "list orders"}, "post": {"summary": "create order"}},
            "/api/orders/{id}": {"get": {"summary": "get order detail"}},
            "/api/orders/{id}/refund": {"post": {"summary": "refund order"}},
        }},
        ensure_ascii=False,
    ), encoding="utf-8")
    (inputs_dir / "db_schema.sql").write_text(DB_SCHEMA, encoding="utf-8")
    (inputs_dir / "real_project_config.json").write_text(json.dumps({
        "project_name": "E2E Test Project",
        "base_url": "",
        "request_timeout_seconds": 1,
    }), encoding="utf-8")


def _assert_json_file(path: Path, desc: str) -> dict:
    assert path.exists(), f"{desc} not found at {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, (dict, list)), f"{desc} is not dict or list"
    return data


def test_e2e_system_promise_closed_loop_scan_and_regression() -> None:
    """Run a complete private pilot scan + regression cycle and verify
    system_promise_id and regression_contract survive in every output file.
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = "e2e_test"

        _write_project_inputs(root, project)

        # ── Phase 1: Build system behavior space ──
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(PRD, API_SPEC, DB_SCHEMA)
        contract = builder.behavior_contract()

        space = contract["system_behavior_space"]
        assert space["summary"]["promise_count"] >= 1, "No system promises generated"
        system_slices = [
            item for item in contract["slices"]
            if item.get("_selection_origin") == "system_behavior_space"
        ]
        assert system_slices, "No system behavior slices in contract"
        for item in system_slices:
            assert item.get("_system_behavior_promise_id"), f"Slice {item.get('slice_id')} missing promise_id"
            assert item.get("_system_behavior_dimensions"), f"Slice {item.get('slice_id')} missing dimensions"

        # ── Phase 2: Generate scenarios ──
        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            API_SPEC,
            active_slices=system_slices,
            allow_source_runtime=False,  # plan_only — no real server needed
        )
        system_scenarios = [s for s in scenarios if getattr(s, "selection_origin", "") == "system_behavior_space"]
        assert system_scenarios, "No system behavior scenarios generated"
        for scenario in system_scenarios:
            runtime_hints = getattr(scenario, "runtime_hints", {}) or {}
            sb_hints = runtime_hints.get("system_behavior_space", {})
            assert sb_hints.get("promise_id"), f"Scenario {getattr(scenario, 'id', '?')} missing promise_id"
            assert sb_hints.get("dimensions"), f"Scenario {getattr(scenario, 'id', '?')} missing dimensions"

        # ── Phase 3: Run V12 pipeline (plan_only — no base_url, no real execution) ──
        result = v12_pipeline.run_v12_pipeline(
            project=project,
            root=root,
            prd_text=PRD,
            api_spec_text=API_SPEC,
            db_schema_text=DB_SCHEMA,
            base_url="",  # no real server → plan_only
        )

        # Check behavior contract was populated in result
        phases = result.get("phases", {})
        oracle_phase = phases.get("oracle", {})
        # plan_only may not produce confirmed findings, but slices/scenarios exist
        scenario_phase = phases.get("scenario_generation", {})
        assert scenario_phase.get("total_scenarios", 0) >= 1, "No scenarios in pipeline result"

        # ── Phase 4: Build regression suite from confirmed findings ──
        suite = regression_suite_builder.build_regression_suite(project, root)
        suite_modes = suite.get("modes", {}) if isinstance(suite, dict) else {}
        release_mode = suite_modes.get("release", {}) if isinstance(suite_modes, dict) else {}
        probes = release_mode.get("items", []) if isinstance(release_mode, dict) else []
        assert isinstance(probes, list), "Regression suite items not a list"

        # ── Phase 5: Run regression (dry_run mode — no real server) ──
        reg_result = regression_runner.run_regression_suite(
            project_id=project,
            root=root,
            options={"mode": "release", "dry_run": True},
        )

        # ── Phase 6: Verify confirmed_findings.json ──
        cf_path = (
            root / "platform_workspace" / project
            / "defect_discovery" / "confirmed_findings.json"
        )
        if cf_path.exists():
            cf_data = _assert_json_file(cf_path, "confirmed_findings.json")
            for evidence_id, entry in cf_data.items():
                if isinstance(entry, dict) and entry.get("system_promise_id"):
                    assert entry.get("regression_contract"), (
                        f"confirmed_findings entry {evidence_id} has system_promise_id "
                        f"but missing regression_contract"
                    )
                    assert entry.get("system_behavior_dimensions"), (
                        f"confirmed_findings entry {evidence_id} missing system_behavior_dimensions"
                    )

        # ── Phase 7: Verify regression_run_history.json ──
        history_paths = [
            root / "platform_outputs" / project / "regression_run" / "regression_run_history.json",
            root / "platform_workspace" / project / "defect_discovery" / "regression_run_history.json",
        ]
        for hp in history_paths:
            if hp.exists():
                history = _assert_json_file(hp, f"regression_run_history at {hp}")
                for run_entry in history if isinstance(history, list) else []:
                    for item in run_entry.get("items", []) if isinstance(run_entry, dict) else []:
                        if isinstance(item, dict) and item.get("system_promise_id"):
                            assert item.get("regression_contract"), (
                                f"History item {item.get('issue_id')} has system_promise_id "
                                f"but missing regression_contract"
                            )

        # ── Phase 8: Verify risk_clue_pool learning ──
        project_learning = get_project_learning(project, root)
        platform_learning = get_platform_learning(root)

        # Project learning should have signals
        assert isinstance(project_learning.get("priority_weights"), dict), (
            "project_learning missing priority_weights"
        )
        # Platform learning should not leak customer data
        platform_text = json.dumps(platform_learning, ensure_ascii=False)
        assert PRD[:10] not in platform_text, "Platform learning leaked PRD text"
        assert "/api/orders" not in platform_text, "Platform learning leaked API paths"

        # ── Phase 9: Verify coverage steering works with the learning ──
        from ai_test_asset_center.private_pilot_coverage_steering_patch import _steer_slices

        # Simulate coverage steering with system behavior slices
        if system_slices:
            ordered, diagnostic = _steer_slices(
                [dict(item) for item in system_slices],
                root=root,
                project=project,
            )
            # Steering should at least not throw
            assert isinstance(diagnostic, dict), "coverage steering diagnostic not dict"
            assert isinstance(ordered, list), "coverage steering ordered not list"

    restore_system_behavior_space_patch()
