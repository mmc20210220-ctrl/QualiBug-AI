"""V1.5.0 Disposable Fixture Contract unit tests.

Covers: fixture candidate discovery, contract building, DAG construction,
scope validation, materialization receipt, reverse cleanup plan.
Industry-neutral: all fixtures use generic entity/operation names.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.disposable_fixture_contract import (
    CONTRACT_SCHEMA,
    DAG_SCHEMA,
    STATUS_RESOLVED,
    STATUS_INCOMPLETE,
    discover_fixture_candidates,
    build_disposable_fixture_contract,
    build_fixture_dag,
    validate_fixture_scope,
    build_fixture_materialization_receipt,
    build_reverse_cleanup_plan,
)


# ─── Fixtures: Generic Behavior IR (industry-neutral) ─────────────────────────


def _minimal_ir(
    *,
    create_op_id: str = "op_create_order",
    get_op_id: str = "op_get_orders",
    delete_op_id: str = "op_delete_order",
    entity_id: str = "entity_order",
    path: str = "/api/orders",
) -> dict:
    """Minimal Behavior IR with one create/read/delete cycle."""
    return {
        "operations": [
            {
                "id": create_op_id,
                "method": "POST",
                "path": path,
                "read_write": "write",
                "source_refs": [{"source_id": "src_1", "doc_ref": "API Spec §3"}],
                "entity_refs": [entity_id],
                "response_schema": {
                    "properties": {
                        "id": {"type": "integer"},
                        "order_number": {"type": "string"},
                    }
                },
            },
            {
                "id": get_op_id,
                "method": "GET",
                "path": f"{path}/{{id}}",
                "read_write": "read",
                "entity_refs": [entity_id],
            },
            {
                "id": delete_op_id,
                "method": "DELETE",
                "path": f"{path}/{{id}}",
                "read_write": "write",
                "entity_refs": [entity_id],
            },
        ],
        "entities": [
            {
                "id": entity_id,
                "scope_fields": {
                    "tenant_field": "tenant_id",
                    "owner_field": "user_id",
                    "permission_scope": "owner",
                },
            }
        ],
        "relations": [],
    }


# ─── §9: Fixture Candidate Discovery ──────────────────────────────────────────


class TestDiscoverFixtureCandidates:
    def test_discovers_post_with_readback_and_cleanup(self):
        ir = _minimal_ir()
        candidates = discover_fixture_candidates(ir)
        assert len(candidates) == 1
        c = candidates[0]
        assert c["create_operation_id"] == "op_create_order"
        assert c["status"] == STATUS_RESOLVED
        assert "op_get_orders" in c["readback_candidate_ids"]
        assert "op_delete_order" in c["cleanup_candidate_ids"]

    def test_ignores_get_operations(self):
        ir = _minimal_ir()
        ir["operations"] = [op for op in ir["operations"] if op["method"] != "POST"]
        candidates = discover_fixture_candidates(ir)
        assert candidates == []

    def test_ignores_post_without_source_refs(self):
        ir = _minimal_ir()
        ir["operations"][0]["source_refs"] = []
        candidates = discover_fixture_candidates(ir)
        assert candidates == []

    def test_incomplete_when_no_readback(self):
        ir = _minimal_ir()
        ir["operations"] = [op for op in ir["operations"] if op["method"] != "GET"]
        candidates = discover_fixture_candidates(ir)
        assert len(candidates) == 1
        assert candidates[0]["status"] == STATUS_INCOMPLETE

    def test_incomplete_when_no_cleanup(self):
        ir = _minimal_ir()
        ir["operations"] = [op for op in ir["operations"] if op["method"] != "DELETE"]
        candidates = discover_fixture_candidates(ir)
        assert len(candidates) == 1
        assert candidates[0]["status"] == STATUS_INCOMPLETE

    def test_entity_filter(self):
        ir = _minimal_ir()
        candidates = discover_fixture_candidates(ir, entity_ids=["nonexistent"])
        assert candidates == []

    def test_entity_filter_match(self):
        ir = _minimal_ir()
        candidates = discover_fixture_candidates(ir, entity_ids=["entity_order"])
        assert len(candidates) == 1

    def test_identity_sources_extracted(self):
        ir = _minimal_ir()
        candidates = discover_fixture_candidates(ir)
        sources = candidates[0]["identity_sources"]
        fields = {s["field"] for s in sources}
        assert "id" in fields
        assert "order_number" in fields

    def test_scope_sources_extracted(self):
        ir = _minimal_ir()
        candidates = discover_fixture_candidates(ir)
        scope = candidates[0]["scope_sources"]
        assert scope["tenant_field"] == "tenant_id"
        assert scope["owner_field"] == "user_id"

    def test_compensates_relation_as_cleanup(self):
        ir = _minimal_ir()
        # Remove DELETE, add compensates relation
        ir["operations"] = [op for op in ir["operations"] if op["method"] != "DELETE"]
        ir["operations"].append({
            "id": "op_cancel_order",
            "method": "POST",
            "path": "/api/orders/{id}/cancel",
            "read_write": "write",
            "entity_refs": ["entity_order"],
        })
        ir["relations"].append({
            "kind": "compensates",
            "source": "op_cancel_order",
            "target": "op_create_order",
        })
        candidates = discover_fixture_candidates(ir)
        assert len(candidates) == 1
        assert "op_cancel_order" in candidates[0]["cleanup_candidate_ids"]

    def test_multiple_create_ops_distinct_collections(self):
        ir = _minimal_ir()
        ir["operations"].append({
            "id": "op_create_invoice",
            "method": "POST",
            "path": "/api/invoices",
            "read_write": "write",
            "source_refs": [{"source_id": "src_2"}],
            "entity_refs": ["entity_invoice"],
        })
        ir["operations"].append({
            "id": "op_get_invoices",
            "method": "GET",
            "path": "/api/invoices/{id}",
            "read_write": "read",
        })
        ir["operations"].append({
            "id": "op_delete_invoice",
            "method": "DELETE",
            "path": "/api/invoices/{id}",
            "read_write": "write",
        })
        candidates = discover_fixture_candidates(ir)
        assert len(candidates) == 2
        ids = {c["create_operation_id"] for c in candidates}
        assert ids == {"op_create_order", "op_create_invoice"}


# ─── §8: Contract Builder ─────────────────────────────────────────────────────


class TestBuildContract:
    def _candidate(self, ir=None):
        ir = ir or _minimal_ir()
        return discover_fixture_candidates(ir)[0]

    def test_contract_schema(self):
        ir = _minimal_ir()
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=self._candidate(ir),
            behavior_ir=ir,
            actor_ref="actor_admin",
        )
        assert contract["schema_version"] == CONTRACT_SCHEMA
        assert contract["status"] == STATUS_RESOLVED

    def test_contract_identity(self):
        ir = _minimal_ir()
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=self._candidate(ir),
            behavior_ir=ir,
        )
        assert contract["fixture_id"].startswith("fix_")
        assert contract["obligation_id"] == "obl_1"
        assert contract["campaign_id"] == "camp_1"
        assert contract["primary_entity_id"] == "entity_order"

    def test_contract_create_plan(self):
        ir = _minimal_ir()
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=self._candidate(ir),
            behavior_ir=ir,
            actor_ref="actor_admin",
        )
        plan = contract["create_plan"]
        assert len(plan) == 1
        assert plan[0]["operation_ref"] == "op_create_order"
        assert plan[0]["actor_ref"] == "actor_admin"

    def test_contract_cleanup_plan(self):
        ir = _minimal_ir()
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=self._candidate(ir),
            behavior_ir=ir,
        )
        cleanup = contract["cleanup_plan"]
        assert "op_delete_order" in cleanup["cleanup_contract_ids"]
        assert cleanup["environment_restoration_required"] is True

    def test_contract_ownership(self):
        ir = _minimal_ir()
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=self._candidate(ir),
            behavior_ir=ir,
        )
        assert contract["ownership"]["campaign_owned"] is True
        assert contract["ownership"]["customer_preexisting"] is False

    def test_contract_provenance_fingerprint(self):
        ir = _minimal_ir()
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=self._candidate(ir),
            behavior_ir=ir,
        )
        assert contract["provenance_fingerprint"].startswith("dfc_")
        assert len(contract["provenance_fingerprint"]) == 24  # dfc_ + 20 hex


# ─── §13: Fixture DAG ─────────────────────────────────────────────────────────


class TestFixtureDAG:
    def test_single_node_dag(self):
        ir = _minimal_ir()
        candidate = discover_fixture_candidates(ir)[0]
        contract = build_disposable_fixture_contract(
            obligation_id="obl_1",
            experiment_id="exp_1",
            campaign_id="camp_1",
            candidate=candidate,
            behavior_ir=ir,
        )
        dag = build_fixture_dag([contract], behavior_ir=ir)
        assert dag["schema_version"] == DAG_SCHEMA
        assert len(dag["nodes"]) == 1
        assert dag["status"] == STATUS_RESOLVED
        assert len(dag["creation_order"]) == 1

    def test_two_node_dag_with_dependency(self):
        ir = _minimal_ir()
        # Add child entity
        ir["operations"].extend([
            {
                "id": "op_create_item",
                "method": "POST",
                "path": "/api/items",
                "read_write": "write",
                "source_refs": [{"source_id": "src_3"}],
                "entity_refs": ["entity_item"],
            },
            {"id": "op_get_item", "method": "GET", "path": "/api/items/{id}", "read_write": "read"},
            {"id": "op_delete_item", "method": "DELETE", "path": "/api/items/{id}", "read_write": "write"},
        ])
        ir["entities"].append({"id": "entity_item"})
        ir["relations"].append({
            "kind": "owns",
            "source": "entity_order",
            "target": "entity_item",
        })
        candidates = discover_fixture_candidates(ir)
        contracts = [
            build_disposable_fixture_contract(
                obligation_id=f"obl_{c['entity_id']}",
                experiment_id="exp_1",
                campaign_id="camp_1",
                candidate=c,
                behavior_ir=ir,
            )
            for c in candidates
        ]
        dag = build_fixture_dag(contracts, behavior_ir=ir)
        assert len(dag["nodes"]) == 2
        assert len(dag["edges"]) == 1
        # Creation order: parent (order) before child (item)
        order_idx = dag["creation_order"].index("node_entity_order")
        item_idx = dag["creation_order"].index("node_entity_item")
        assert order_idx < item_idx
        # Cleanup order: reversed
        assert dag["cleanup_order"][0] == "node_entity_item"

    def test_empty_contracts_incomplete(self):
        ir = _minimal_ir()
        dag = build_fixture_dag([], behavior_ir=ir)
        assert dag["status"] == STATUS_INCOMPLETE


# ─── §12: Scope Validation ────────────────────────────────────────────────────


class TestValidateFixtureScope:
    def test_scope_valid(self):
        contract = {
            "fixture_id": "fix_abc",
            "campaign_id": "camp_1",
            "experiment_id": "exp_1",
            "ownership": {"campaign_owned": True, "customer_preexisting": False},
            "scope": {
                "tenant_field": "tenant_id",
                "tenant_value_ref": "runtime_actor_tenant",
                "owner_field": "user_id",
                "owner_value_ref": "runtime_actor_owner",
            },
            "primary_entity_id": "entity_order",
        }
        result = validate_fixture_scope(
            contract, campaign_id="camp_1", experiment_id="exp_1"
        )
        assert result["status"] == "VALID"

    def test_scope_campaign_mismatch(self):
        contract = {
            "fixture_id": "fix_abc",
            "campaign_id": "camp_OTHER",
            "experiment_id": "exp_1",
            "ownership": {"campaign_owned": True, "customer_preexisting": False},
            "primary_entity_id": "entity_order",
        }
        result = validate_fixture_scope(
            contract, campaign_id="camp_1", experiment_id="exp_1"
        )
        assert result["status"] == "FIXTURE_SCOPE_MISMATCH"
        assert "campaign_match" in result["mismatched_fields"]

    def test_scope_customer_preexisting_rejected(self):
        contract = {
            "fixture_id": "fix_abc",
            "campaign_id": "camp_1",
            "experiment_id": "exp_1",
            "ownership": {"campaign_owned": True, "customer_preexisting": True},
            "primary_entity_id": "entity_order",
        }
        result = validate_fixture_scope(
            contract, campaign_id="camp_1", experiment_id="exp_1"
        )
        assert result["status"] == "FIXTURE_SCOPE_MISMATCH"


# ─── §14: Materialization Receipt ─────────────────────────────────────────────


class TestMaterializationReceipt:
    def test_receipt_success(self):
        contract = {
            "fixture_id": "fix_abc",
            "campaign_id": "camp_1",
            "experiment_id": "exp_1",
            "provenance_fingerprint": "dfc_abc123",
        }
        receipt = build_fixture_materialization_receipt(
            contract=contract,
            created_entities=[{"entity_id": "order", "identity_value": "42"}],
            cleanup_contract_ids=["op_delete_order"],
        )
        assert receipt["fixture_id"] == "fix_abc"
        assert receipt["final_status"] == "MATERIALIZED"
        assert receipt["created_entities"][0]["identity_value"] == "42"
        assert receipt["provenance"]["match"] is True

    def test_receipt_failed_status(self):
        contract = {
            "fixture_id": "fix_abc",
            "campaign_id": "camp_1",
            "experiment_id": "exp_1",
            "provenance_fingerprint": "dfc_abc123",
        }
        receipt = build_fixture_materialization_receipt(
            contract=contract,
            created_entities=[],
            final_status="MATERIALIZATION_FAILED",
        )
        assert receipt["final_status"] == "MATERIALIZATION_FAILED"
        assert receipt["created_entities"] == []


# ─── §29: Reverse Cleanup Plan ────────────────────────────────────────────────


class TestReverseCleanupPlan:
    def test_reverse_order(self):
        plan = build_reverse_cleanup_plan(
            experiment_id="exp_1",
            fixture_id="fix_abc",
            write_steps=[
                {"step_id": "treatment_1", "operation_ref": "op_create_order", "cleanup_contract_id": "op_delete_order"},
                {"step_id": "treatment_2", "operation_ref": "op_create_item", "cleanup_contract_id": "op_delete_item"},
            ],
        )
        assert plan["status"] == STATUS_RESOLVED
        # Execution order: reverse of dependency rank (item first, then order)
        assert plan["execution_order"][0]["cleanup_contract_id"] == "op_delete_item"
        assert plan["execution_order"][1]["cleanup_contract_id"] == "op_delete_order"

    def test_incomplete_when_no_cleanup_contract_or_op_ref(self):
        plan = build_reverse_cleanup_plan(
            experiment_id="exp_1",
            fixture_id="fix_abc",
            write_steps=[
                {"step_id": "treatment_1", "operation_ref": "", "cleanup_contract_id": ""},
            ],
        )
        assert plan["status"] == STATUS_INCOMPLETE

    def test_empty_steps_incomplete(self):
        plan = build_reverse_cleanup_plan(
            experiment_id="exp_1",
            fixture_id="fix_abc",
            write_steps=[],
        )
        assert plan["status"] == STATUS_INCOMPLETE
