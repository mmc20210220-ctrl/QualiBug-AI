"""V1.3.0-A: Database Cleanup Contract and Environment Restoration tests.

Verifies the full main-chain integration:
- Compile-time contract generation (cleanup_adapter_ladder)
- Dependency graph construction
- Cleanup authority resolution
- Pre-image plan resolution
- Runtime receipts (cleanup_execution_receipt)
- Lifecycle state machine (experiment_outcome_finalizer)
- Finding gate demotion when environment not restored
"""
from __future__ import annotations

import pytest
from typing import Any

from ai_test_asset_center.cleanup_adapter_ladder import (
    build_database_dependency_graph,
    build_database_cleanup_contract,
    resolve_cleanup_authority,
    resolve_preimage_plan,
    CONTRACT_RESOLVED,
    CONTRACT_INCOMPLETE,
    CONTRACT_NOT_DECLARED,
    STRATEGY_API_COMPENSATION,
    STRATEGY_DB_DELETE,
    STRATEGY_SNAPSHOT_RESTORE,
    STRATEGY_DB_RESTORE,
    MUTATION_CREATE,
    MUTATION_UPDATE,
    MUTATION_DELETE,
    DB_CLEANUP_AUTHORITY_NOT_DECLARED,
    DB_ROW_IDENTITY_NOT_BOUND,
    DB_DEPENDENCY_GRAPH_INCOMPLETE,
    ALL_DB_BREAKPOINT_CODES,
)
from ai_test_asset_center.cleanup_execution_receipt import (
    build_database_cleanup_receipt,
    build_environment_restoration_receipt,
    build_fixture_row_lineage,
    verify_cleanup_completion,
    RECEIPT_CLEANED,
    RECEIPT_RESTORED,
    RECEIPT_FAILED,
    RECEIPT_INDETERMINATE,
    RECEIPT_PARTIAL,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

ENTITIES = [
    {
        "name": "orders",
        "identity_fields": ["id"],
        "fields": ["id", "customer_id", "total", "status"],
    },
    {
        "name": "order_items",
        "identity_fields": ["id"],
        "fields": ["id", "order_id", "product_id", "quantity"],
    },
    {
        "name": "products",
        "identity_fields": ["id"],
        "fields": ["id", "name", "price", "stock"],
    },
    {
        "name": "customers",
        "identity_fields": ["id"],
        "fields": ["id", "name", "email"],
    },
]

DDL_SCHEMA = """
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    email VARCHAR(255)
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    total DECIMAL(10,2),
    status VARCHAR(50)
);

CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product_id INTEGER REFERENCES products(id),
    quantity INTEGER
);

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    price DECIMAL(10,2),
    stock INTEGER
);
"""

WRITE_OP_POST = {
    "id": "op_create_order",
    "method": "POST",
    "path": "/api/orders",
    "read_write": "write",
}

WRITE_OP_PUT = {
    "id": "op_update_order",
    "method": "PUT",
    "path": "/api/orders/{orderId}",
    "read_write": "write",
}

WRITE_OP_DELETE = {
    "id": "op_delete_order",
    "method": "DELETE",
    "path": "/api/orders/{orderId}",
    "read_write": "write",
}


# ─── Dependency Graph Tests ───────────────────────────────────────────────────

class TestDependencyGraph:
    def test_ddl_references(self):
        graph = build_database_dependency_graph(ENTITIES, schema_text=DDL_SCHEMA)
        assert graph["schema_version"] == "qualibug.database-dependency-graph.v1"
        assert graph["node_count"] == 4
        assert graph["edge_count"] >= 3  # orders→customers, order_items→orders, order_items→products
        # Check specific edge
        edges = graph["edges"]
        order_customer = [e for e in edges if e["child_table"] == "orders" and e["parent_table"] == "customers"]
        assert len(order_customer) == 1
        assert order_customer[0]["confidence"] == 1.0
        assert order_customer[0]["source"] == "ddl_references"

    def test_naming_convention(self):
        """Without DDL, naming convention detects customer_id → customers."""
        graph = build_database_dependency_graph(ENTITIES)
        edges = graph["edges"]
        # order_items has order_id and product_id
        fk_edges = [e for e in edges if e["source"] == "naming_convention"]
        assert len(fk_edges) >= 2

    def test_declared_foreign_keys(self):
        entities_with_fk = [
            {"name": "orders", "identity_fields": ["id"], "fields": ["id", "customer_id"], "foreign_keys": ["customers"]},
            {"name": "customers", "identity_fields": ["id"], "fields": ["id", "name"]},
        ]
        graph = build_database_dependency_graph(entities_with_fk)
        edges = graph["edges"]
        assert any(e["source"] == "declared_foreign_key" and e["parent_table"] == "customers" for e in edges)

    def test_topological_order_children_first(self):
        graph = build_database_dependency_graph(ENTITIES, schema_text=DDL_SCHEMA)
        topo = graph["topological_order"]
        assert len(topo) == 4
        # order_items depends on orders and products, so should come before them
        oi_idx = next(i for i, t in enumerate(topo) if t.lower() == "order_items")
        o_idx = next(i for i, t in enumerate(topo) if t.lower() == "orders")
        assert oi_idx < o_idx, "order_items (child) must be cleaned before orders (parent)"

    def test_empty_entities(self):
        graph = build_database_dependency_graph([])
        assert graph["node_count"] == 0
        assert graph["incomplete"] is False

    def test_incomplete_graph_cycle(self):
        """Entities with no relations produce a complete (trivial) graph."""
        entities = [{"name": "isolated", "identity_fields": ["id"], "fields": ["id"]}]
        graph = build_database_dependency_graph(entities)
        assert graph["incomplete"] is False


# ─── Cleanup Authority Tests ──────────────────────────────────────────────────

class TestCleanupAuthority:
    def test_source_declared_compensation(self):
        plan = [{"action": "source_declared_compensation", "operation_ref": "op_delete_order"}]
        result = resolve_cleanup_authority(WRITE_OP_POST, cleanup_plan=plan)
        assert result["status"] == CONTRACT_RESOLVED
        assert result["strategy_type"] == STRATEGY_API_COMPENSATION

    def test_restore_before_snapshot(self):
        plan = [{"action": "restore_before_snapshot", "operation_ref": "op_get_order"}]
        result = resolve_cleanup_authority(WRITE_OP_PUT, cleanup_plan=plan)
        assert result["status"] == CONTRACT_RESOLVED
        assert result["strategy_type"] == STRATEGY_SNAPSHOT_RESTORE

    def test_inverse_delta(self):
        plan = [{"action": "inverse_delta_compensation", "operation_ref": "op_patch"}]
        result = resolve_cleanup_authority(WRITE_OP_PUT, cleanup_plan=plan)
        assert result["strategy_type"] == STRATEGY_DB_RESTORE

    def test_db_sql_adapter(self):
        plan = [{"action": "declared_adapter_cleanup", "adapter": "db_sql", "table": "orders"}]
        result = resolve_cleanup_authority(WRITE_OP_POST, cleanup_plan=plan)
        assert result["strategy_type"] == STRATEGY_DB_DELETE
        assert result["status"] == CONTRACT_RESOLVED

    def test_best_effort_delete_is_not_cleanup_authority(self):
        plan = [{
            "action": "best_effort_delete",
            "operation_ref": "op_create_order",
        }]
        result = resolve_cleanup_authority(WRITE_OP_POST, cleanup_plan=plan)

        assert result["status"] == CONTRACT_NOT_DECLARED
        assert result["strategy_type"] == ""
        assert result["reason_code"] == DB_CLEANUP_AUTHORITY_NOT_DECLARED

    def test_runtime_contains_no_best_effort_cleanup_success_path(self):
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "ai_test_asset_center"
            / "experiment_cleanup_executor_core.py"
        ).read_text(encoding="utf-8")

        assert 'cleanup_action == "best_effort_delete"' in source
        assert "cleanup_authority_not_source_declared" in source
        assert '"best_effort": True' not in source

    def test_no_plan_no_adapter(self):
        result = resolve_cleanup_authority(WRITE_OP_POST, cleanup_plan=[])
        assert result["status"] == CONTRACT_NOT_DECLARED
        assert result["reason_code"] == DB_CLEANUP_AUTHORITY_NOT_DECLARED

    def test_no_plan_with_db_adapter(self):
        result = resolve_cleanup_authority(
            WRITE_OP_POST, cleanup_plan=[], available_adapters=["db_sql"]
        )
        assert result["status"] == CONTRACT_INCOMPLETE
        assert result["reason_code"] == DB_ROW_IDENTITY_NOT_BOUND


# ─── Pre-image Plan Tests ─────────────────────────────────────────────────────

class TestPreimagePlan:
    def test_put_requires_preimage(self):
        result = resolve_preimage_plan(WRITE_OP_PUT, entities=ENTITIES)
        assert result["required"] is True
        assert result["reason"] == "modification_requires_preimage"
        assert result["observer_id"] == "before_state"

    def test_patch_requires_preimage(self):
        op = {"method": "PATCH", "path": "/api/orders/{id}"}
        result = resolve_preimage_plan(op, entities=ENTITIES)
        assert result["required"] is True

    def test_delete_requires_preimage(self):
        result = resolve_preimage_plan(WRITE_OP_DELETE, entities=ENTITIES)
        assert result["required"] is True
        assert result["reason"] == "deletion_requires_full_snapshot"

    def test_post_no_preimage(self):
        result = resolve_preimage_plan(WRITE_OP_POST, entities=ENTITIES)
        assert result["required"] is False
        assert result["reason"] == "create_uses_row_lineage"


# ─── Compile-Time Contract Tests ──────────────────────────────────────────────

class TestDatabaseCleanupContract:
    def test_full_contract_post(self):
        contract = build_database_cleanup_contract(
            experiment_id="exp_001",
            campaign_id="camp_001",
            write_operation=WRITE_OP_POST,
            entities=ENTITIES,
            cleanup_plan=[{"action": "source_declared_compensation", "operation_ref": "op_delete"}],
            environment_type="test",
        )
        assert contract["schema_version"] == "qualibug.database-cleanup-contract.v1"
        assert contract["experiment_id"] == "exp_001"
        assert contract["campaign_id"] == "camp_001"
        assert contract["mutation_type"] == MUTATION_CREATE
        assert contract["status"] == CONTRACT_RESOLVED
        assert contract["owned_by_campaign"] is True
        assert contract["pre_existing_customer_data"] is False
        assert contract["contract_id"].startswith("dbc_")

    def test_contract_put_mutation_type(self):
        contract = build_database_cleanup_contract(
            experiment_id="exp_002",
            campaign_id="camp_001",
            write_operation=WRITE_OP_PUT,
            entities=ENTITIES,
            cleanup_plan=[{"action": "restore_before_snapshot", "operation_ref": "op_get"}],
        )
        assert contract["mutation_type"] == MUTATION_UPDATE
        assert contract["preimage_plan"]["required"] is True

    def test_contract_delete_mutation_type(self):
        contract = build_database_cleanup_contract(
            experiment_id="exp_003",
            campaign_id="camp_001",
            write_operation=WRITE_OP_DELETE,
            entities=ENTITIES,
            cleanup_plan=[{"action": "restore_before_snapshot", "operation_ref": "op_get"}],
        )
        assert contract["mutation_type"] == MUTATION_DELETE

    def test_contract_no_authority_not_declared(self):
        contract = build_database_cleanup_contract(
            experiment_id="exp_004",
            campaign_id="camp_001",
            write_operation=WRITE_OP_POST,
            entities=ENTITIES,
            cleanup_plan=[],
        )
        assert contract["status"] == CONTRACT_NOT_DECLARED
        assert DB_CLEANUP_AUTHORITY_NOT_DECLARED in contract["reason_codes"]

    def test_contract_target_entities(self):
        contract = build_database_cleanup_contract(
            experiment_id="exp_005",
            campaign_id="camp_001",
            write_operation=WRITE_OP_POST,
            entities=ENTITIES,
            cleanup_plan=[{"action": "source_declared_compensation", "operation_ref": "op_del"}],
        )
        assert len(contract["target_entities"]) == 1
        assert contract["target_entities"][0]["table"] == "orders"

    def test_contract_dependency_order(self):
        contract = build_database_cleanup_contract(
            experiment_id="exp_006",
            campaign_id="camp_001",
            write_operation=WRITE_OP_POST,
            entities=ENTITIES,
            cleanup_plan=[{"action": "source_declared_compensation", "operation_ref": "op_del"}],
        )
        assert isinstance(contract["dependency_order"], list)


# ─── Runtime Receipt Tests ────────────────────────────────────────────────────

class TestDatabaseCleanupReceipt:
    def test_successful_cleanup(self):
        receipt = build_database_cleanup_receipt(
            experiment_id="exp_001",
            table="orders",
            primary_key_fingerprint="fp_abc",
            cleanup_strategy="SOURCE_DECLARED_DB_DELETE",
            authority_source="op_delete_order",
            cleanup_execution={"attempted": True, "affected_rows": 1, "error": ""},
            verification={"passed": True},
        )
        assert receipt["schema_version"] == "qualibug.database-cleanup-receipt.v1"
        assert receipt["final_status"] == RECEIPT_CLEANED
        assert receipt["receipt_id"].startswith("dbcr_")

    def test_failed_cleanup(self):
        receipt = build_database_cleanup_receipt(
            experiment_id="exp_002",
            table="orders",
            primary_key_fingerprint="fp_abc",
            cleanup_strategy="SOURCE_DECLARED_DB_DELETE",
            authority_source="op_delete",
            cleanup_execution={"attempted": True, "affected_rows": 0, "error": "FK violation"},
            verification={"passed": False},
        )
        assert receipt["final_status"] == RECEIPT_FAILED

    def test_not_attempted(self):
        receipt = build_database_cleanup_receipt(
            experiment_id="exp_003",
            table="orders",
            primary_key_fingerprint="fp_abc",
            cleanup_strategy="SOURCE_DECLARED_DB_DELETE",
            authority_source="op_delete",
            cleanup_execution={"attempted": False},
        )
        assert receipt["final_status"] == RECEIPT_INDETERMINATE

    def test_restore_strategy(self):
        receipt = build_database_cleanup_receipt(
            experiment_id="exp_004",
            table="orders",
            primary_key_fingerprint="fp_abc",
            cleanup_strategy="restore",
            authority_source="op_restore",
            cleanup_execution={"attempted": True, "affected_rows": 1, "error": ""},
            verification={"passed": True},
        )
        assert receipt["final_status"] == RECEIPT_RESTORED


# ─── Environment Restoration Receipt Tests ────────────────────────────────────

class TestEnvironmentRestorationReceipt:
    def test_fully_restored(self):
        receipt = build_environment_restoration_receipt(
            experiment_id="exp_001",
            campaign_id="camp_001",
            database_cleanup_receipt_ids=["dbcr_1"],
            created_rows_remaining=0,
            modified_rows_not_restored=0,
            deleted_rows_not_restored=0,
            cleanup_failures=[],
            baseline_comparison={"relevant_tables_match": True, "relevant_fields_match": True},
        )
        assert receipt["schema_version"] == "qualibug.environment-restoration-receipt.v1"
        assert receipt["environment_restored"] is True
        assert receipt["final_status"] == "ENVIRONMENT_RESTORED"

    def test_dirty_environment(self):
        receipt = build_environment_restoration_receipt(
            experiment_id="exp_002",
            campaign_id="camp_001",
            created_rows_remaining=2,
            cleanup_failures=[],
            baseline_comparison={"relevant_tables_match": False, "relevant_fields_match": True},
        )
        assert receipt["environment_restored"] is False
        assert receipt["final_status"] == "ENVIRONMENT_DIRTY"

    def test_cleanup_failed(self):
        receipt = build_environment_restoration_receipt(
            experiment_id="exp_003",
            campaign_id="camp_001",
            cleanup_failures=[{"reason": "timeout"}],
        )
        assert receipt["environment_restored"] is False
        assert receipt["final_status"] == "CLEANUP_FAILED"


# ─── Fixture Row Lineage Tests ────────────────────────────────────────────────

class TestFixtureRowLineage:
    def test_basic_lineage(self):
        lineage = build_fixture_row_lineage(
            campaign_id="camp_001",
            experiment_id="exp_001",
            fixture_id="fix_001",
            table="orders",
            primary_key="12345",
        )
        assert lineage["schema_version"] == "qualibug.fixture-row-lineage.v1"
        assert lineage["created_by_qualibug"] is True
        assert lineage["customer_preexisting"] is False
        assert lineage["lineage_id"].startswith("frl_")

    def test_lineage_with_relations(self):
        lineage = build_fixture_row_lineage(
            campaign_id="camp_001",
            experiment_id="exp_001",
            fixture_id="fix_002",
            table="order_items",
            primary_key="99",
            parent_keys=["order:12345"],
            child_keys=[],
        )
        assert lineage["parent_keys"] == ["order:12345"]


# ─── Cleanup Verification Tests ───────────────────────────────────────────────

class TestCleanupVerification:
    def test_all_successful(self):
        receipts = [
            {"receipt_id": "r1", "table": "order_items", "final_status": RECEIPT_CLEANED},
            {"receipt_id": "r2", "table": "orders", "final_status": RECEIPT_CLEANED},
        ]
        result = verify_cleanup_completion(cleanup_receipts=receipts)
        assert result["environment_restored"] is True
        assert result["verification_passed"] is True
        assert result["successful_count"] == 2

    def test_partial_failure(self):
        receipts = [
            {"receipt_id": "r1", "table": "orders", "final_status": RECEIPT_CLEANED},
            {"receipt_id": "r2", "table": "order_items", "final_status": RECEIPT_FAILED},
        ]
        result = verify_cleanup_completion(cleanup_receipts=receipts)
        assert result["environment_restored"] is False
        assert len(result["failures"]) == 1

    def test_empty_receipts(self):
        result = verify_cleanup_completion(cleanup_receipts=[])
        assert result["environment_restored"] is True
        assert result["receipt_count"] == 0


# ─── Breakpoint Code Tests ────────────────────────────────────────────────────

class TestBreakpointCodes:
    def test_all_14_codes_defined(self):
        assert len(ALL_DB_BREAKPOINT_CODES) == 14

    def test_parent_breakpoint(self):
        from ai_test_asset_center.cleanup_adapter_ladder import PARENT_BREAKPOINT
        assert PARENT_BREAKPOINT == "DB_CLEANUP_AND_ENVIRONMENT_RESTORATION_NOT_CLOSED"

    def test_codes_in_block_reasons(self):
        from ai_test_asset_center.experiment_compiler_obligation import BLOCK_REASONS
        assert "DB_CLEANUP_AUTHORITY_NOT_DECLARED" in BLOCK_REASONS
        assert "DB_ROW_IDENTITY_NOT_BOUND" in BLOCK_REASONS


# ─── Lifecycle State Machine Tests ────────────────────────────────────────────

class TestLifecycleStateMachine:
    def test_constants_defined(self):
        from ai_test_asset_center.experiment_outcome_finalizer import (
            LIFECYCLE_EXPERIMENT_COMPLETED,
            LIFECYCLE_EXECUTED_BUT_NOT_RESTORED,
            LIFECYCLE_CLEANUP_FAILED,
            LIFECYCLE_ENVIRONMENT_DIRTY,
            _TERMINAL_NON_COMPLETE,
        )
        assert LIFECYCLE_EXPERIMENT_COMPLETED == "EXPERIMENT_COMPLETED"
        assert LIFECYCLE_EXECUTED_BUT_NOT_RESTORED in _TERMINAL_NON_COMPLETE
        assert LIFECYCLE_CLEANUP_FAILED in _TERMINAL_NON_COMPLETE
        assert LIFECYCLE_ENVIRONMENT_DIRTY in _TERMINAL_NON_COMPLETE

    def test_batch_executor_accepts_new_status(self):
        """EXECUTED_BUT_NOT_RESTORED must be a valid status in batch executor."""
        import ast
        import pathlib
        src = pathlib.Path("ai_test_asset_center/_experiment_batch_executor_single_finding_mechanics.py").read_text(encoding="utf-8")
        assert "EXECUTED_BUT_NOT_RESTORED" in src


# ─── Prohibited Pattern Tests ─────────────────────────────────────────────────

class TestProhibitedPatterns:
    def test_truncate_detected(self):
        from ai_test_asset_center.cleanup_adapter_ladder import _PROHIBITED_SQL_RE
        assert _PROHIBITED_SQL_RE.search("TRUNCATE TABLE orders")

    def test_drop_table_detected(self):
        from ai_test_asset_center.cleanup_adapter_ladder import _PROHIBITED_SQL_RE
        assert _PROHIBITED_SQL_RE.search("DROP TABLE orders")

    def test_fk_checks_disabled_detected(self):
        from ai_test_asset_center.cleanup_adapter_ladder import _PROHIBITED_SQL_RE
        assert _PROHIBITED_SQL_RE.search("SET FOREIGN_KEY_CHECKS = 0")

    def test_unbounded_delete_detected(self):
        from ai_test_asset_center.cleanup_adapter_ladder import _PROHIBITED_SQL_RE
        assert _PROHIBITED_SQL_RE.search("DELETE FROM orders;")

    def test_max_id_guess_detected(self):
        from ai_test_asset_center.cleanup_adapter_ladder import _PROHIBITED_SQL_RE
        assert _PROHIBITED_SQL_RE.search("MAX(id)")

    def test_legitimate_delete_not_flagged(self):
        from ai_test_asset_center.cleanup_adapter_ladder import _PROHIBITED_SQL_RE
        assert not _PROHIBITED_SQL_RE.search("DELETE FROM orders WHERE id = %s")
