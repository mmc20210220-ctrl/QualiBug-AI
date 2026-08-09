"""Task 29 — parameter-bound performance contracts (REPORT-008 class).

Verifies the full four-link chain for source-declared parameter-scale
performance contracts, offline (no target, no scan):

1. Contract derivation from visible source materials (OpenAPI integer query
   parameter minimum/maximum, parameter-description ranges, verbatim text
   statements) — extraction only, every skip receipted.
2. Scan-context overlay normalization (fail-closed gaps).
3. Exact Behavior IR binding (one invariant + observes relation per contract).
4. Obligation compilation through the installed wrapper chain, including the
   generic resource-protection degradation channel for unbounded integer
   query parameters (receipted cap, runtime-observed verdicts only).
5. Registered protocol compiler answers COMPILED with escalating probe steps
   (parameter boundary injection).
6. Observer summarizes governed execution steps (response-time / status
   series, single-attempt duration discipline).
7. Assertion evaluator verdicts: bound-not-enforced, latency-budget-exceeded,
   resource exhaustion at magnitude, unbounded scaling observed, clean pass.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Importing the semantic-binding module installs the four capability
# registries (observer / assertion / family / protocol) exactly like the
# production mainline does.  The explicit installers below make this module
# self-contained: on a mainline that already carries the task-29 wiring they
# are idempotent no-ops; on one that does not, they register the parameter-
# scale surface and compile wrapper so the suite runs in either state.
from ai_test_asset_center import discovery_runtime_planning as _planning  # noqa: E402
from ai_test_asset_center import discovery_runtime_semantic_binding  # noqa: E402,F401
from ai_test_asset_center.formal_parameter_scale_surface import (  # noqa: E402
    install_formal_parameter_scale_surface,
)
from ai_test_asset_center.source_parameter_obligation_binding import (  # noqa: E402
    install_source_parameter_obligation_binding,
)

install_formal_parameter_scale_surface()
install_source_parameter_obligation_binding()
from ai_test_asset_center.behavior_ir_core import empty_behavior_ir  # noqa: E402
from ai_test_asset_center.experiment_protocols import compile_family_protocol  # noqa: E402
from ai_test_asset_center.observer_contracts_base import (  # noqa: E402
    OBSERVER_REGISTRY,
)
from ai_test_asset_center.source_parameter_bound_contracts import (  # noqa: E402
    bind_source_parameter_contracts,
    derive_parameter_bound_contracts,
    overlay_scan_parameter_contracts,
)
from ai_test_asset_center.source_parameter_obligation_binding import (  # noqa: E402
    compile_obligations_with_source_parameter_scale,
)
from ai_test_asset_center.test_obligation import canonical_risk_families  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ACTOR = {
    "id": "actor-auditor",
    "role": "auditor",
    "account_ref": "auditor_a",
    "credential_secret_ref": "secret_ref:auditor_a",
    "status": "accepted",
    "derivation": "explicit",
    "confidence": 0.9,
}

_REPORT_OPERATION = {
    "id": "get_slow_orders",
    "operation_id": "getSlowOrders",
    "method": "GET",
    "path": "/api/reports/slow/orders",
    "read_write": "read",
    "summary": "慢查询报表",
    "description": "查询慢查询报表；测试重复次数，1~100",
    "status": "accepted",
    "derivation": "explicit",
    "confidence": 0.8,
    "required_roles": ["auditor"],
    "parameters": [{
        "name": "repeat",
        "in": "query",
        "required": False,
        "description": "测试重复次数，1~100",
        "schema": {"type": "integer", "format": "int32", "minimum": 1, "maximum": 100, "default": 10},
        "example": 10,
    }],
    "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "GET /api/reports/slow/orders"}],
}

_LIST_OPERATION = {
    "id": "list_orders",
    "operation_id": "listOrders",
    "method": "GET",
    "path": "/api/orders",
    "read_write": "read",
    "summary": "订单列表",
    "description": "分页查询订单",
    "status": "accepted",
    "derivation": "explicit",
    "confidence": 0.8,
    "required_roles": ["auditor"],
    # limit declared in the description, not the schema.
    "parameters": [{
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "每页条数，最大 50 条",
        "schema": {"type": "integer"},
    }],
    "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "GET /api/orders"}],
}

_UNBOUNDED_OPERATION = {
    "id": "search_logs",
    "operation_id": "searchLogs",
    "method": "GET",
    "path": "/api/logs",
    "read_write": "read",
    "summary": "日志检索",
    "description": "按关键字检索日志",
    "status": "accepted",
    "derivation": "explicit",
    "confidence": 0.8,
    "required_roles": ["auditor"],
    # integer query parameter with NO declared upper bound anywhere.
    "parameters": [{
        "name": "max_rows",
        "in": "query",
        "required": False,
        "schema": {"type": "integer"},
    }],
    "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "GET /api/logs"}],
}

_WRITE_OPERATION = {
    "id": "create_report",
    "method": "POST",
    "path": "/api/reports",
    "read_write": "write",
    "status": "accepted",
    "derivation": "explicit",
    "confidence": 0.8,
    "parameters": [{
        "name": "repeat",
        "in": "query",
        "schema": {"type": "integer", "minimum": 1, "maximum": 100},
    }],
    "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "POST /api/reports"}],
}

_API_SPEC_TEXT = (
    '{"openapi":"3.0.0","paths":{"/api/reports/slow/orders":{"get":{"operationId":'
    '"getSlowOrders","description":"查询慢查询报表；测试重复次数，1~100",'
    '"parameters":[{"name":"repeat","in":"query","description":"测试重复次数，1~100",'
    '"schema":{"type":"integer","minimum":1,"maximum":100}}]}}}}'
)


def _param_contract(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": "qualibug.formal-performance-contract.v1",
        "contract_kind": "parameter_scale_budget",
        "contract_id": "psb-test-1",
        "source_refs": [{
            "source_id": "api",
            "locator": "GET /api/reports/slow/orders",
            "kind": "formal_performance_contract",
            "quote": "测试重复次数，1~100",
        }],
        "source_id": "api",
        "source_locator": "GET /api/reports/slow/orders",
        "method": "GET",
        "operation_path": "/api/reports/slow/orders",
        "actor_role": "auditor",
        "parameter_name": "repeat",
        "declared_min": 1,
        "declared_max": 100,
        "status": "accepted",
        "derivation": "auto_detected_from_source",
        "origin": "source_parameter_bound_contracts",
        "confidence": 1.0,
    }
    row.update(overrides)
    return row


def _ir(operations: list[dict[str, Any]], contracts: list[dict[str, Any]]) -> dict[str, Any]:
    model = empty_behavior_ir(project_id="task29-parameter-scale")
    model["actors"] = [_ACTOR]
    model["operations"] = operations
    model["invariants"] = []
    model["relations"] = []
    model["performance_parameter_contracts"] = contracts
    return model


def _step(
    step_id: str,
    value: int,
    *,
    status: int = 200,
    duration_ms: int = 50,
    attempts: int = 1,
    role: str = "escalation",
    path: str | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "phase": "treatment",
        "step_id": step_id,
        "method": "GET",
        "path": path or f"/api/reports/slow/orders?repeat={value}",
        "status_code": status,
        "duration_ms": duration_ms,
        "error": error,
        "raw": {"_attempts": attempts},
        "probe_role": role,
    }


def _probe(observation: dict[str, Any], value: int) -> dict[str, Any]:
    return next(row for row in observation["probes"] if row["value"] == value)


# ---------------------------------------------------------------------------
# 1. Derivation
# ---------------------------------------------------------------------------


class TestDerivation:
    def test_openapi_schema_bounds_become_contract(self) -> None:
        asset, receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_REPORT_OPERATION],
            runtime_actors=[_ACTOR],
            api_spec_text=_API_SPEC_TEXT,
        )
        assert receipt["status"] == "CONSUMED"
        rows = asset["performance_parameter_contracts"]
        assert len(rows) == 1
        row = rows[0]
        assert row["parameter_name"] == "repeat"
        assert row["declared_min"] == 1
        assert row["declared_max"] == 100
        assert row["method"] == "GET"
        assert row["operation_path"] == "/api/reports/slow/orders"
        assert row["actor_role"] == "auditor"
        assert row["contract_kind"] == "parameter_scale_budget"
        # Verbatim quote anchored in the source spec text.
        assert row["source_refs"][0]["quote"] == "测试重复次数，1~100"

    def test_parameter_description_range_without_schema_bounds(self) -> None:
        asset, receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_LIST_OPERATION],
            runtime_actors=[_ACTOR],
        )
        rows = asset.get("performance_parameter_contracts") or []
        assert len(rows) == 1
        assert rows[0]["parameter_name"] == "limit"
        assert rows[0]["declared_max"] == 50
        # The description declares only an upper bound ("最大 50 条"); the min
        # is not a source fact and must stay None (no defaults masquerade).
        assert rows[0]["declared_min"] is None

    def test_unbounded_parameter_derives_nothing(self) -> None:
        asset, receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_UNBOUNDED_OPERATION],
            runtime_actors=[_ACTOR],
        )
        assert not (asset.get("performance_parameter_contracts") or [])
        reasons = {row["reason"] for row in receipt["skipped"]}
        assert "no_declared_upper_bound" in reasons

    def test_write_operation_never_derives(self) -> None:
        asset, _receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_WRITE_OPERATION],
            runtime_actors=[_ACTOR],
        )
        assert not (asset.get("performance_parameter_contracts") or [])

    def test_text_statement_pass_binds_by_path_and_parameter(self) -> None:
        prd_text = (
            "报表模块：GET /api/reports/slow/orders 查询慢查询报表，"
            "参数 repeat 取值范围 1~100，接口必须稳定。"
        )
        asset, receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_REPORT_OPERATION],
            runtime_actors=[_ACTOR],
            prd_text=prd_text,
        )
        rows = asset.get("performance_parameter_contracts") or []
        assert len(rows) == 1
        assert rows[0]["parameter_name"] == "repeat"
        assert rows[0]["declared_min"] == 1
        assert rows[0]["declared_max"] == 100
        assert rows[0]["source_refs"][0]["quote"] != ""

    def test_dedup_against_declared_contract(self) -> None:
        existing = _param_contract()
        asset, receipt = derive_parameter_bound_contracts(
            {"performance_parameter_contracts": [existing]},
            api_operations=[_REPORT_OPERATION],
            runtime_actors=[_ACTOR],
        )
        rows = asset["performance_parameter_contracts"]
        assert len(rows) == 1
        assert rows[0]["contract_id"] == "psb-test-1"
        assert any(
            row.get("reason") == "already_declared_contract"
            for row in receipt["skipped"]
        )


# ---------------------------------------------------------------------------
# 2. Overlay
# ---------------------------------------------------------------------------


class TestOverlay:
    def test_normalized_contract_overlaid(self) -> None:
        asset, receipt = overlay_scan_parameter_contracts(
            {},
            campaign_context={
                "performance_parameter_contracts": [_param_contract()],
            },
        )
        assert receipt["status"] == "OVERLAID"
        rows = asset["performance_parameter_contracts"]
        assert len(rows) == 1
        assert rows[0]["declared_max"] == 100
        assert rows[0]["derivation"] == "explicit"

    def test_invalid_bounds_fail_closed(self) -> None:
        asset, receipt = overlay_scan_parameter_contracts(
            {},
            campaign_context={
                "performance_parameter_contracts": [
                    _param_contract(declared_min=200, declared_max=100),
                ],
            },
        )
        assert receipt["status"] == "BLOCKED"
        assert not (asset.get("performance_parameter_contracts") or [])
        assert any(
            gap.get("reason_code") == "PARAMETER_CONTRACT_BOUND_INVALID"
            for gap in asset.get("coverage_gaps") or []
        )

    def test_missing_actor_fail_closed(self) -> None:
        asset, _receipt = overlay_scan_parameter_contracts(
            {},
            campaign_context={
                "performance_parameter_contracts": [
                    _param_contract(actor_role=""),
                ],
            },
        )
        assert not (asset.get("performance_parameter_contracts") or [])
        assert any(
            gap.get("reason_code") == "PARAMETER_CONTRACT_ACTOR_IDENTITY_MISSING"
            for gap in asset.get("coverage_gaps") or []
        )

    def test_upper_bound_required(self) -> None:
        asset, _receipt = overlay_scan_parameter_contracts(
            {},
            campaign_context={
                "performance_parameter_contracts": [
                    _param_contract(declared_max=None),
                ],
            },
        )
        assert not (asset.get("performance_parameter_contracts") or [])
        assert any(
            gap.get("reason_code") == "PARAMETER_CONTRACT_UPPER_BOUND_REQUIRED"
            for gap in asset.get("coverage_gaps") or []
        )


# ---------------------------------------------------------------------------
# 3. IR binding
# ---------------------------------------------------------------------------


class TestBinding:
    def test_contract_binds_exact_invariant_and_relation(self) -> None:
        ir, receipt = bind_source_parameter_contracts(
            _ir([_REPORT_OPERATION], [_param_contract()]),
            {"performance_parameter_contracts": [_param_contract()]},
        )
        assert receipt["status"] == "BOUND"
        invariants = [
            row
            for row in ir["invariants"]
            if row.get("performance_contract_id") == "psb-test-1"
        ]
        assert len(invariants) == 1
        assert invariants[0]["expression"]["kind"] == "parameter_scale_budget_contract"
        assert invariants[0]["operation_refs"] == ["get_slow_orders"]
        assert invariants[0]["performance_actor_ref"] == "actor-auditor"
        assert any(
            row.get("relation_type") == "observes"
            and row.get("to_ref") == "get_slow_orders"
            for row in ir["relations"]
        )

    def test_unbound_actor_produces_gap(self) -> None:
        contract = _param_contract(actor_role="finance")
        ir, receipt = bind_source_parameter_contracts(
            _ir([_REPORT_OPERATION], [contract]),
            {"performance_parameter_contracts": [contract]},
        )
        assert receipt["status"] == "BLOCKED"
        assert any(
            gap.get("reason_code") == "PARAMETER_CONTRACT_ACTOR_NOT_FOUND"
            for gap in ir.get("coverage_gaps") or []
        )


# ---------------------------------------------------------------------------
# 4. Obligation compilation (wrapper chain + degradation channel)
# ---------------------------------------------------------------------------


class TestObligationCompilation:
    def test_parameter_contract_compiles_into_obligation(self) -> None:
        # Mirror the mainline order: derive contracts from the operations
        # first (repeat and limit both declare bounds), then bind, then compile.
        asset, _derive_receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_REPORT_OPERATION, _LIST_OPERATION, _UNBOUNDED_OPERATION],
            runtime_actors=[_ACTOR],
        )
        ir = _ir([_REPORT_OPERATION, _LIST_OPERATION, _UNBOUNDED_OPERATION], [])
        ir, _receipt = bind_source_parameter_contracts(ir, asset)
        pack = _planning.compile_obligations_from_behavior_ir(
            ir,
            root=str(ROOT),
            project="task29",
        )
        perf = [
            row
            for row in pack["obligations"]
            if row.get("risk_family") == "performance_latency"
        ]
        param_obligations = [
            row
            for row in perf
            if row["property"].get("template") == "source_declared_parameter_scale_budget"
        ]
        # repeat + limit contracts -> two source-bound obligations; the
        # unbounded max_rows gets exactly one generic channel obligation.
        assert len(param_obligations) == 3
        contract = param_obligations[0]["property"]["performance_contract"]
        assert contract["parameter_name"] == "repeat"
        assert contract["declared_max"] == 100
        receipt = pack["source_parameter_obligation_receipt"]
        assert receipt["obligation_count"] == 2
        assert receipt["generic_resource_protection_obligation_count"] == 1

    def test_generic_channel_covers_unbounded_parameter_only(self) -> None:
        asset, _derive_receipt = derive_parameter_bound_contracts(
            {},
            api_operations=[_REPORT_OPERATION, _LIST_OPERATION, _UNBOUNDED_OPERATION],
            runtime_actors=[_ACTOR],
        )
        ir = _ir([_REPORT_OPERATION, _LIST_OPERATION, _UNBOUNDED_OPERATION], [])
        ir, _receipt = bind_source_parameter_contracts(ir, asset)
        pack = _planning.compile_obligations_from_behavior_ir(
            ir,
            root=str(ROOT),
            project="task29",
        )
        generic = [
            row
            for row in pack["obligations"]
            if (row.get("property") or {}).get("claim_derivation")
            == "generic_resource_protection"
        ]
        # Only the genuinely unbounded /api/logs max_rows parameter qualifies;
        # repeat and limit are covered by derived contracts.
        assert len(generic) == 1
        assert generic[0]["property"]["operation_ref"] == "search_logs"
        assert generic[0]["property"]["performance_contract"]["parameter_name"] == "max_rows"
        assert generic[0]["property"]["performance_contract"]["declared_max"] is None
        assert generic[0]["confidence"] == 0.6
        # Receipted skip accounting (never a silent truncation).
        receipt = pack["source_parameter_obligation_receipt"]
        assert receipt["generic_channel_skip_count"] >= 2
        assert receipt["generic_channel_skip_reason_counts"].get(
            "GENERIC_CHANNEL_OPERATION_ALREADY_COVERED", 0
        ) == 2

    def test_generic_channel_cap_is_receipted(self) -> None:
        many = [
            {
                "id": f"op_{index}",
                "method": "GET",
                "path": f"/api/x/{index}",
                "read_write": "read",
                "status": "accepted",
                "derivation": "explicit",
                "confidence": 0.8,
                "required_roles": ["auditor"],
                "parameters": [{
                    "name": "n",
                    "in": "query",
                    "schema": {"type": "integer"},
                }],
                "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": f"GET /api/x/{index}"}],
            }
            for index in range(10)
        ]
        ir = _ir(many, [])
        pack = _planning.compile_obligations_from_behavior_ir(
            ir,
            root=str(ROOT),
            project="task29",
        )
        receipt = pack["source_parameter_obligation_receipt"]
        assert receipt["generic_resource_protection_obligation_count"] <= 4
        assert receipt["generic_channel_budget"]["cap"] == 4


# ---------------------------------------------------------------------------
# 5. Protocol compiler (parameter boundary injection)
# ---------------------------------------------------------------------------


class TestProtocolCompilation:
    def _compile(self, contract: dict[str, Any]) -> dict[str, Any]:
        return compile_family_protocol(
            risk_family="performance_latency",
            operation=_REPORT_OPERATION,
            operation_ref="get_slow_orders",
            control_actor_ref="actor-auditor",
            treatment_actor_ref="actor-auditor",
            property_spec={
                "template": "source_declared_parameter_scale_budget",
                "operation_ref": "get_slow_orders",
                "actor_ref": "actor-auditor",
                "performance_contract": contract,
            },
        )

    def test_compile_emits_escalating_probe_steps(self) -> None:
        plan = self._compile(_param_contract())
        assert plan["status"] == "COMPILED"
        steps = plan["treatment_plan"]
        assert [step["probe_value"] for step in steps] == [1, 100, 101, 1000]
        assert [step["probe_role"] for step in steps] == [
            "baseline", "declared_max", "above_bound", "escalation",
        ]
        for step in steps:
            assert step["query"] == {"repeat": str(step["probe_value"])}
            assert step["protocol_step"] == "parameter_scale_probe"
            assert step["operation_ref"] == "get_slow_orders"
        assert plan["observers"] == [{"observer_id": "source_http_parameter_scale_reader"}]
        assert plan["assertion"]["kind"] == "source_parameter_scale_budget"

    def test_generic_contract_compiles_generic_magnitudes(self) -> None:
        generic = _param_contract(
            contract_id="generic_psb_1",
            declared_min=None,
            declared_max=None,
            derivation="generic_resource_protection",
        )
        plan = self._compile(generic)
        assert plan["status"] == "COMPILED"
        steps = plan["treatment_plan"]
        assert [step["probe_value"] for step in steps] == [1, 10, 100, 1000, 10000]
        assert steps[0]["probe_role"] == "baseline"
        assert all(
            step["probe_role"] == "generic_escalation" for step in steps[1:]
        )

    def test_compile_blocks_without_parameter(self) -> None:
        plan = self._compile(_param_contract(parameter_name=""))
        assert plan["status"] == "BLOCKED"
        assert plan["reason_code"] == "BLOCKED_MISSING_ASSERTION"


# ---------------------------------------------------------------------------
# 6. Observer
# ---------------------------------------------------------------------------


class TestObserver:
    def _observe(self, steps: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
        from ai_test_asset_center.observer_contracts_base import (
            observe_experiment_requirements,
        )

        receipts = observe_experiment_requirements(
            {
                "assertions": [
                    {
                        "kind": "source_parameter_scale_budget",
                        "property": {"performance_contract": contract},
                    },
                ],
                "observers": [{"observer_id": "source_http_parameter_scale_reader"}],
                "source_refs": [],
            },
            observations={"execution_steps": steps},
        )
        return receipts[0]

    def _series_steps(self) -> list[dict[str, Any]]:
        return [
            _step("param_scale_probe_1", 1, duration_ms=40, role="baseline"),
            _step("param_scale_probe_2", 100, duration_ms=400, role="declared_max"),
            _step("param_scale_probe_3", 101, status=400, duration_ms=5, role="above_bound"),
            _step("param_scale_probe_4", 1000, status=400, duration_ms=5, role="escalation"),
        ]

    def test_observed_series(self) -> None:
        result = self._observe(self._series_steps(), _param_contract())
        assert result["status"] == "OBSERVED"
        observation = result["evidence"]["source_http_parameter_scale"]
        assert observation["coverage_complete"] is True
        assert len(observation["probes"]) == 4
        assert _probe(observation, 1)["duration_ms"] == 40
        assert _probe(observation, 101)["accepted"] is False
        assert _probe(observation, 1000)["status_code"] == 400

    def test_retried_transport_is_indeterminate_for_duration(self) -> None:
        steps = self._series_steps()
        steps[3] = _step("param_scale_probe_4", 1000, status=200, duration_ms=5000, attempts=3, role="escalation")
        result = self._observe(steps, _param_contract())
        assert result["status"] == "OBSERVED"
        observation = result["evidence"]["source_http_parameter_scale"]
        assert _probe(observation, 1000)["duration_ms"] is None
        assert _probe(observation, 1000)["retried"] is True

    def test_incomplete_sample_set_is_indeterminate(self) -> None:
        steps = self._series_steps()[:3]
        result = self._observe(steps, _param_contract())
        assert result["status"] == "INDETERMINATE"
        assert result["reason_code"] == "PARAMETER_SCALE_SAMPLE_SET_INCOMPLETE"


# ---------------------------------------------------------------------------
# 7. Assertion evaluator
# ---------------------------------------------------------------------------


def _verdict(observation: dict[str, Any]) -> dict[str, Any]:
    from ai_test_asset_center.formal_parameter_scale_surface import (
        _evaluate_parameter_scale_budget,
    )

    return _evaluate_parameter_scale_budget({
        "observations": {"source_http_parameter_scale": observation},
    })


def _observation(
    probes: list[dict[str, Any]],
    *,
    declared_min: int | None = 1,
    declared_max: int | None = 100,
    max_latency_ms: float | None = None,
) -> dict[str, Any]:
    return {
        "parameter_name": "repeat",
        "declared_min": declared_min,
        "declared_max": declared_max,
        "max_latency_ms": max_latency_ms,
        "expected_probe_count": len(probes),
        "coverage_complete": True,
        "measurement_semantics": "sequential_get_or_head_parameter_scale_probes_single_attempt",
        "probes": probes,
    }


def _p(value: int, *, status: int = 200, duration: float = 50.0, role: str = "escalation") -> dict[str, Any]:
    return {
        "value": value,
        "role": role,
        "status_code": status,
        "accepted": 200 <= status < 300,
        "transport_failed": status <= 0,
        "duration_ms": duration,
        "retried": False,
    }


class TestEvaluator:
    def test_bound_not_enforced_fails(self) -> None:
        verdict = _verdict(_observation([
            _p(1, duration=40, role="baseline"),
            _p(100, duration=400, role="declared_max"),
            _p(101, status=200, duration=900, role="above_bound"),
            _p(1000, status=200, duration=8000, role="escalation"),
        ]))
        assert verdict["passed"] is False
        assert verdict["reason_code"] == "PARAMETER_BOUND_NOT_ENFORCED"
        assert verdict["actual"]["accepted_above_bound_values"][0]["value"] == 101

    def test_bound_enforced_passes(self) -> None:
        verdict = _verdict(_observation([
            _p(1, duration=40, role="baseline"),
            _p(100, duration=400, role="declared_max"),
            _p(101, status=422, duration=5, role="above_bound"),
            _p(1000, status=422, duration=5, role="escalation"),
        ]))
        assert verdict["passed"] is True
        assert verdict["reason_code"] == "PARAMETER_BOUND_ENFORCED"

    def test_latency_budget_exceeded_fails(self) -> None:
        verdict = _verdict(_observation(
            [
                _p(1, duration=40, role="baseline"),
                _p(100, duration=2500, role="declared_max"),
                _p(101, status=422, duration=5, role="above_bound"),
                _p(1000, status=422, duration=5, role="escalation"),
            ],
            max_latency_ms=2000,
        ))
        assert verdict["passed"] is False
        assert verdict["reason_code"] == "PARAMETER_LATENCY_BUDGET_EXCEEDED"
        assert verdict["actual"]["latency_budget_exceeded_at_value"] == 100

    def test_generic_timeout_is_resource_exhaustion(self) -> None:
        verdict = _verdict(_observation(
            [
                _p(1, duration=40, role="baseline"),
                _p(10, status=0, duration=34000, role="generic_escalation"),
            ],
            declared_min=None,
            declared_max=None,
        ))
        assert verdict["passed"] is False
        assert verdict["reason_code"] == "RESOURCE_EXHAUSTION_AT_INPUT_MAGNITUDE"
        assert verdict["actual"]["claim_derivation"] == "generic_resource_protection"

    def test_generic_unbounded_scaling_observed(self) -> None:
        verdict = _verdict(_observation(
            [
                _p(1, duration=40, role="baseline"),
                _p(10, duration=60, role="generic_escalation"),
                _p(100, duration=400, role="generic_escalation"),
                _p(1000, duration=1500, role="generic_escalation"),
                _p(10000, status=200, duration=12000, role="generic_escalation"),
            ],
            declared_min=None,
            declared_max=None,
        ))
        assert verdict["passed"] is False
        assert verdict["reason_code"] == "UNBOUNDED_PARAMETER_SCALING_OBSERVED"
        assert verdict["actual"]["largest_probe_value"] == 10000
        assert verdict["actual"]["baseline_duration_ms"] == 40

    def test_generic_clean_series_passes(self) -> None:
        verdict = _verdict(_observation(
            [
                _p(1, duration=40, role="baseline"),
                _p(10, duration=45, role="generic_escalation"),
                _p(100, duration=48, role="generic_escalation"),
                _p(1000, duration=60, role="generic_escalation"),
                _p(10000, status=422, duration=5, role="generic_escalation"),
            ],
            declared_min=None,
            declared_max=None,
        ))
        assert verdict["passed"] is True
        assert verdict["reason_code"] == "NO_SCALING_ANOMALY_OBSERVED"

    def test_generic_baseline_missing_is_indeterminate(self) -> None:
        verdict = _verdict(_observation(
            [_p(1, status=500, duration=40, role="baseline")],
            declared_min=None,
            declared_max=None,
        ))
        assert verdict["passed"] is None
        assert verdict["reason_code"] == "PARAMETER_SCALE_BASELINE_NOT_ESTABLISHED"

    def test_incomplete_coverage_is_indeterminate(self) -> None:
        observation = _observation([_p(1, role="baseline")])
        observation["coverage_complete"] = False
        verdict = _verdict(observation)
        assert verdict["passed"] is None
        assert verdict["reason_code"] == "PARAMETER_SCALE_MEASUREMENT_INCOMPLETE"


# ---------------------------------------------------------------------------
# 8. Planning-level offline verification on a real spec shape
# ---------------------------------------------------------------------------


class TestPlanningLevel:
    def test_real_spec_shape_reaches_obligation(self) -> None:
        """The exact REPORT-008 spec shape (repeat 1~100) flows end to end."""
        from ai_test_asset_center.universal_api_parser import (
            build_api_operations_from_text,
        )

        spec_text = open(
            ROOT / "platform_inputs" / "benchmark_binding_fix_blind" / "openapi.json",
            encoding="utf-8",
        ).read()
        operations = build_api_operations_from_text(spec_text)
        report_ops = [
            op
            for op in operations
            if op.get("path") == "/api/reports/slow/orders"
        ]
        assert len(report_ops) == 1
        assert any(
            (p.get("schema") or {}).get("maximum") == 100
            for p in report_ops[0].get("parameters") or []
        )
        # The mainline wraps parsed operations into IR fact nodes; mirror that
        # node shape here so the behavior-IR validator accepts the operation.
        parsed_op = dict(report_ops[0])
        report_ops = [{
            **parsed_op,
            "id": "get_slow_orders",
            "read_write": "read",
            "status": "accepted",
            "derivation": "explicit",
            "confidence": 0.8,
            "required_roles": ["auditor"],
            "source_refs": [{
                "source_id": "api_spec",
                "kind": "api_operation",
                "locator": "GET /api/reports/slow/orders",
            }],
        }]
        asset, _receipt = derive_parameter_bound_contracts(
            {},
            api_operations=report_ops,
            runtime_actors=[_ACTOR],
            api_spec_text=spec_text,
        )
        rows = asset.get("performance_parameter_contracts") or []
        assert len(rows) == 1
        assert rows[0]["parameter_name"] == "repeat"
        assert rows[0]["declared_max"] == 100

        ir = _ir(report_ops, rows)
        ir, _bind_receipt = bind_source_parameter_contracts(ir, asset)
        pack = _planning.compile_obligations_from_behavior_ir(
            ir,
            root=str(ROOT),
            project="task29",
        )
        param_obligations = [
            row
            for row in pack["obligations"]
            if (row.get("property") or {}).get("template")
            == "source_declared_parameter_scale_budget"
        ]
        assert len(param_obligations) == 1
        plan = compile_family_protocol(
            risk_family="performance_latency",
            operation=report_ops[0],
            operation_ref=param_obligations[0]["property"]["operation_ref"],
            control_actor_ref="actor-auditor",
            treatment_actor_ref="actor-auditor",
            property_spec=param_obligations[0]["property"],
        )
        assert plan["status"] == "COMPILED"
        steps = plan["treatment_plan"]
        assert [step["probe_value"] for step in steps] == [1, 100, 101, 1000]

    def test_planner_chain_includes_parameter_receipts(self) -> None:
        """The mainline IR builder records the new receipts on the job IR."""
        ops = [
            {
                "id": "get_logs",
                "operation_id": "getLogs",
                "method": "GET",
                "path": "/api/logs",
                "read_write": "read",
                "status": "accepted",
                "derivation": "explicit",
                "confidence": 0.8,
                "required_roles": ["auditor"],
                "parameters": [{
                    "name": "max_rows",
                    "in": "query",
                    "schema": {"type": "integer"},
                }],
                "source_refs": [{"source_id": "api", "kind": "api_operation", "locator": "GET /api/logs"}],
            },
        ]
        ir = _ir(ops, [])
        job_ir, _receipt = bind_source_parameter_contracts(ir, {})
        pack = _planning.compile_obligations_from_behavior_ir(
            job_ir,
            root=str(ROOT),
            project="task29",
        )
        assert "source_parameter_obligation_receipt" in pack
        assert pack["source_parameter_obligation_receipt"]["status"] == "COMPILED"
