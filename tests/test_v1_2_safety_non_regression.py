"""Safety non-regression tests — SPEC v1.2 §14.6.

Proves that v1.2 coverage recovery modules do NOT relax any v1.1/v1.1.1
safety invariants. All safety counters must remain at zero.
"""
import pytest
from ai_test_asset_center.observer_capability_resolver import (
    resolve_observer_capability,
)
from ai_test_asset_center.binding_coverage_graph import (
    FORBIDDEN_SOURCE_KINDS,
    build_binding_coverage_graph,
)
from ai_test_asset_center.compensation_relation_resolver import (
    resolve_compensation_relation,
)
from ai_test_asset_center.oracle_input_contract import (
    build_oracle_input_contract,
)
from ai_test_asset_center.fixture_dependency_dag import (
    validate_fixture_dag,
)


# ─── §14.6 Safety Invariant: 猜测 Observer path 数 = 0 ───────────────────────


class TestNoGuessedObserverPath:
    """Observer resolver must never invent/guess paths."""

    def test_guessed_path_rejected(self):
        """A GET path not in Behavior IR must not resolve."""
        ir = {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/orders", "entity": "order"},
            ],
            "actors": [],
        }
        result = resolve_observer_capability(
            observer_requirement="before_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
            required_entity_ref="order",
        )
        # No GET operation in IR → cannot resolve
        assert result["resolution_status"] == "BLOCKED"
        assert result["operation_ref"] == ""

    def test_only_ir_declared_get_used(self):
        """Resolver only uses GET/HEAD declared in IR."""
        ir = {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/orders", "entity": "order"},
                {"id": "op_get", "method": "GET", "path": "/orders/{orderId}", "entity": "order",
                 "identity_fields": ["orderId"]},
            ],
            "actors": [],
        }
        result = resolve_observer_capability(
            observer_requirement="after_state",
            primary_operation=ir["operations"][0],
            behavior_ir=ir,
            required_entity_ref="order",
        )
        if result["resolution_status"] == "RESOLVED":
            # Must reference the real IR operation
            assert result["operation_ref"] == "op_get"


# ─── §14.6 Safety Invariant: 假 Binding 数 = 0 ───────────────────────────────


class TestNoFakeBindings:
    """Binding graph must reject all forbidden source kinds."""

    def test_all_forbidden_sources_blocked(self):
        """Every FORBIDDEN_SOURCE_KIND must cause BLOCKED status."""
        for forbidden in FORBIDDEN_SOURCE_KINDS:
            exp = {
                "experiment_id": "exp_test",
                "obligation_id": "obl_test",
                "binding_plan": [
                    {"target": "entityId", "source_kind": forbidden, "status": "bound"},
                ],
                "treatment_plan": [{"path": "/x/{entityId}", "method": "PUT"}],
            }
            result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
            assert result["graph_status"] == "BLOCKED", (
                f"Forbidden source kind {forbidden} was not blocked"
            )
            assert result["forbidden_source_count"] >= 1

    def test_random_placeholder_never_allowed(self):
        exp = {
            "experiment_id": "exp_rnd",
            "binding_plan": [
                {"target": "id", "source_kind": "RANDOM_PLACEHOLDER", "status": "bound"},
            ],
        }
        result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert result["graph_status"] == "BLOCKED"

    def test_llm_invented_value_never_allowed(self):
        exp = {
            "experiment_id": "exp_llm",
            "binding_plan": [
                {"target": "id", "source_kind": "LLM_INVENTED_VALUE", "status": "bound"},
            ],
        }
        result = build_binding_coverage_graph(experiment=exp, behavior_ir={})
        assert result["graph_status"] == "BLOCKED"


# ─── §14.6 Safety Invariant: 明确 Actor 自动替换数 = 0 ────────────────────────


class TestNoActorSubstitution:
    """Fixture DAG must flag actor mismatches, not silently substitute."""

    def test_actor_mismatch_detected(self):
        result = validate_fixture_dag(
            fixtures=[
                {"fixture_id": "f1", "operation_ref": "op_1", "actor_ref": "actor_wrong",
                 "cleanup_contract_ref": "c1"},
            ],
            experiment={
                "actor_selection_contract": {
                    "control_actor_ref": "actor_admin",
                    "treatment_actor_ref": "actor_buyer",
                },
            },
            behavior_ir={},
        )
        # Must detect mismatch, not silently accept
        assert any(i["kind"] == "ACTOR_MISMATCH" for i in result["issues"])


# ─── §14.6 Safety Invariant: Compensation 不接受名称推断 ──────────────────────


class TestNoNameBasedCompensation:
    """Compensation resolver must reject name-only antonym inference."""

    def test_name_antonym_create_cancel_rejected(self):
        ir = {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/items", "entity": "item"},
                {"id": "op_cancel", "method": "POST", "path": "/items/{id}/cancel", "entity": "item"},
            ],
            "relations": [],
            "actors": [],
        }
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert result["accepted"] is False

    def test_name_antonym_create_delete_rejected(self):
        """Even create/delete pair rejected without explicit relation."""
        ir = {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/resources", "entity": "resource"},
                {"id": "op_delete", "method": "DELETE", "path": "/resources/{id}", "entity": "resource"},
            ],
            "relations": [],  # No explicit compensates relation
            "actors": [],
        }
        result = resolve_compensation_relation(
            primary_operation=ir["operations"][0],
            candidate_operation=ir["operations"][1],
            behavior_ir=ir,
        )
        assert result["accepted"] is False


# ─── §14.6 Safety Invariant: Oracle 缺输入不放行 ──────────────────────────────


class TestOracleInputNotWaived:
    """Oracle input contract must block when observers are insufficient."""

    def test_write_without_before_observer_incomplete(self):
        """Governed write + state_transition without before → INCOMPLETE."""
        exp = {
            "experiment_id": "exp_w",
            "obligation_id": "obl_w",
            "safety_contract": {"governed_write": True},
            "assertions": [{"assertion_id": "a1", "kind": "state_transition"}],
            "observers": [{"observer_id": "obs_a", "kind": "after_state"}],
            "control_plan": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert result["reason_code"] == "BLOCKED_MISSING_OBSERVER"

    def test_authorization_without_control_incomplete(self):
        exp = {
            "experiment_id": "exp_auth",
            "obligation_id": "obl_auth",
            "safety_contract": {"governed_write": False},
            "assertions": [{"assertion_id": "a_auth", "kind": "authorization"}],
            "observers": [{"observer_id": "o", "kind": "state_read"}],
            "control_plan": [],
        }
        result = build_oracle_input_contract(experiment=exp, behavior_ir={})
        assert result["overall_status"] == "INCOMPLETE"
        assert "control_plan_missing" in result["missing_inputs"]


# ─── §14.6 Aggregate: All safety counters = 0 ────────────────────────────────


class TestAggregateSafetyCounters:
    """Simulate a batch of experiments and verify all safety counters = 0."""

    def test_safety_counters_zero(self):
        """Run a representative batch through v1.2 modules, count violations."""
        guessed_observer_paths = 0
        fake_bindings = 0
        name_based_compensations = 0
        oracle_waived = 0

        # Batch of IR scenarios
        scenarios = [
            # Scenario 1: No GET in IR
            {
                "ir": {"operations": [{"id": "op1", "method": "POST", "path": "/a", "entity": "x"}], "actors": []},
                "primary": {"id": "op1", "method": "POST", "path": "/a", "entity": "x"},
            },
            # Scenario 2: Entity mismatch
            {
                "ir": {"operations": [
                    {"id": "op1", "method": "POST", "path": "/a", "entity": "x"},
                    {"id": "op2", "method": "GET", "path": "/b/{id}", "entity": "y"},
                ], "actors": []},
                "primary": {"id": "op1", "method": "POST", "path": "/a", "entity": "x"},
            },
        ]

        for sc in scenarios:
            # Observer check
            res = resolve_observer_capability(
                observer_requirement="before_state",
                primary_operation=sc["primary"],
                behavior_ir=sc["ir"],
                required_entity_ref=sc["primary"].get("entity", ""),
            )
            # If resolved, must have a real operation_ref from IR
            if res["resolution_status"] == "RESOLVED" and not res["operation_ref"]:
                guessed_observer_paths += 1

        # Binding check
        for forbidden in FORBIDDEN_SOURCE_KINDS:
            exp = {"binding_plan": [{"target": "x", "source_kind": forbidden, "status": "bound"}]}
            res = build_binding_coverage_graph(experiment=exp, behavior_ir={})
            if res["graph_status"] != "BLOCKED":
                fake_bindings += 1

        # Compensation check
        ir_no_rel = {
            "operations": [
                {"id": "c", "method": "POST", "path": "/x", "entity": "e"},
                {"id": "d", "method": "DELETE", "path": "/x/{id}", "entity": "e"},
            ],
            "relations": [],
            "actors": [],
        }
        res = resolve_compensation_relation(
            primary_operation=ir_no_rel["operations"][0],
            candidate_operation=ir_no_rel["operations"][1],
            behavior_ir=ir_no_rel,
        )
        if res["accepted"]:
            name_based_compensations += 1

        # Oracle check
        exp_incomplete = {
            "safety_contract": {"governed_write": True},
            "assertions": [{"kind": "conservation"}],
            "observers": [],
            "control_plan": [],
        }
        res = build_oracle_input_contract(experiment=exp_incomplete, behavior_ir={})
        if res["overall_status"] == "COMPLETE":
            oracle_waived += 1

        # ── All safety counters MUST be 0 ──
        assert guessed_observer_paths == 0, f"Guessed observer paths: {guessed_observer_paths}"
        assert fake_bindings == 0, f"Fake bindings accepted: {fake_bindings}"
        assert name_based_compensations == 0, f"Name-based compensations: {name_based_compensations}"
        assert oracle_waived == 0, f"Oracle input waived: {oracle_waived}"
