"""Dependency db-read failure must not masquerade as a fixture-generation gap.

When a create-body foreign key (e.g. ``addressId``) has no declared HTTP
list-read, the materializer falls back to a source-declared persistence read
(``db_sql`` leg). If that read channel itself fails for environmental reasons
(credential decrypt, environment gate, transport), the blocked detail must
name the environmental cause — ``dependency_db_read_unavailable`` — instead of
collapsing into ``dependency_fixture_setup_not_generated``, which sends
diagnosis toward a phantom capability gap.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_fixture_materializer_core import (
    materialize_experiment_fixtures,
)


def _orders_ir() -> dict:
    """Order create whose body references addressId; no address endpoints."""
    return {
        "operations": [
            {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
                "source_refs": [{"source_id": "api_spec"}],
            },
            {
                "id": "op-create-order",
                "method": "POST",
                "path": "/api/orders",
                "request_example": {
                    "items": [{"sku": "SKU-1", "qty": 1}],
                    "addressId": "<address_id>",
                },
                "source_refs": [{"source_id": "api_spec"}],
            },
            {
                "id": "op-cancel-order",
                "method": "POST",
                "path": "/api/orders/{id}/cancel",
                "source_refs": [{"source_id": "api_spec"}],
            },
        ],
        "actors": [],
        "relations": [
            {
                "relation_type": "compensates",
                "from_ref": "op-cancel-order",
                "to_ref": "op-create-order",
                "operation_ref": "op-cancel-order",
                "status": "accepted",
                "source_refs": [{"source_id": "api_spec"}],
            }
        ],
        "entities": [
            {
                "name": "addresses",
                "kind": "resource",
                "table": "addresses",
                "identity_fields": ["id"],
            }
        ],
    }


def _binding() -> dict:
    return {
        "target": "order_id",
        "target_path": "/{order_id}",
        "status": "runtime_resolvable",
        "resolver_operations": [
            {
                "operation_ref": "op-list-orders",
                "method": "GET",
                "path": "/api/orders",
            }
        ],
    }


def _inputs(ir: dict, binding: dict) -> dict:
    experiment = {
        "experiment_id": "exp_db_read_unavailable",
        "obligation_id": "obl_db_read_unavailable",
        "environment_type": "test",
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_bind_order"],
            "nodes": [
                {
                    "node_id": "fix_bind_order",
                    "kind": "runtime_read_binding",
                    "target": binding["target"],
                    "constructible": True,
                }
            ],
        },
        "binding_plan": [binding],
        "control_plan": [
            {
                "actor_ref": "actor-buyer-a",
                "operation_ref": "op-list-orders",
                "path": "/api/orders",
            }
        ],
        "treatment_plan": [],
        "observers": [{"observer_id": "http_response", "surface": "http_api"}],
        "assertions": [{"kind": "state_transition"}],
        "safety_contract": {"governed_write": True, "cleanup_not_required": False},
        "compiled_adapters": ["http_api"],
    }
    return {
        "exp": experiment,
        "eid": "exp_db_read_unavailable",
        "oid": "obl_db_read_unavailable",
        "resolved_campaign_id": "CMP_test",
        "resolved_execution_id": "EXEC_test",
        "started": time.time(),
        "actors": {
            "actor-buyer-a": {
                "id": "actor-buyer-a",
                "role": "buyer",
                "credential_secret_ref": "secret:buyer_a",
            }
        },
        "ops": {row["id"]: row for row in ir["operations"]},
        "tokens": {"secret:buyer_a": "token-a", "buyer": "token-a"},
        "binding_plan": {binding["target"]: dict(binding)},
        "resolver_actor_ref": "actor-buyer-a",
        "resolver_token": "token-a",
        "activation_requirements": {"actor": [], "fixture": [], "cleanup": []},
        "root": Path("."),
        "project": "test-project",
        "base_url": "http://target.test",
        "runtime_contract": {
            "status": "approved",
            "approved_base_url": "http://target.test",
        },
        "campaign_id": "CMP_test",
        "behavior_ir": ir,
    }


def _patch_empty_list_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_http_step(**kwargs: object) -> dict:
        return {
            "method": str(kwargs.get("method") or "GET"),
            "path": str(kwargs.get("path") or ""),
            "status_code": 200,
            "body": [],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )


def _patch_db_read(monkeypatch: pytest.MonkeyPatch, reason_code: str) -> None:
    def fake_db_read(**kwargs: object) -> dict:
        return {
            "adapter": "db_sql",
            "method": "DB_READ",
            "table": "addresses",
            "identity_column": "id",
            "status_code": 0,
            "value": None,
            "reason_code": reason_code,
            "row_count": 0,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core."
        "_run_declared_db_identity_read",
        fake_db_read,
    )


def test_db_read_config_invalid_named_in_blocked_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decrypt/gate failure -> dependency_db_read_unavailable, never the
    fixture-generation misnomer."""
    _patch_empty_list_read(monkeypatch)
    _patch_db_read(
        monkeypatch,
        "persistence_config_invalid:declared_db_password_decrypt_failed:"
        "gateway:CredentialDecryptionError",
    )
    result = materialize_experiment_fixtures(**_inputs(_orders_ir(), _binding()))
    assert result["status"] == "terminal"
    payload = result["result"]
    assert payload["status"] == "BLOCKED"
    assert payload["reason_code"] == "BLOCKED_MISSING_BINDING"
    detail = payload["detail"]
    assert "dependency_db_read_unavailable:addressId" in detail
    assert "persistence_config_invalid" in detail
    assert "dependency_fixture_setup_not_generated" not in detail
    blocked = [
        row for row in payload["fixture_receipts"] if row["status"] == "BLOCKED"
    ]
    assert blocked, "expected a BLOCKED fixture receipt"
    failures = blocked[0]["dependency_db_read_failures"]
    assert failures
    assert any("persistence_config_invalid" in row for row in failures)


def test_db_read_gate_refusal_named_in_blocked_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_empty_list_read(monkeypatch)
    _patch_db_read(
        monkeypatch, "persistence_read_not_permitted:environment_kind_undeclared"
    )
    result = materialize_experiment_fixtures(**_inputs(_orders_ir(), _binding()))
    payload = result["result"]
    assert payload["status"] == "BLOCKED"
    assert "dependency_db_read_unavailable:addressId" in payload["detail"]
    assert "environment_kind_undeclared" in payload["detail"]


def test_empty_table_read_keeps_fixture_generation_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy read over an empty table is a genuine construction gap:
    the fixture-generation reason stays (no environmental mislabel)."""
    _patch_empty_list_read(monkeypatch)
    _patch_db_read(monkeypatch, "persistence_identity_not_observed")
    result = materialize_experiment_fixtures(**_inputs(_orders_ir(), _binding()))
    payload = result["result"]
    assert payload["status"] == "BLOCKED"
    assert "dependency_fixture_setup_not_generated:addressId" in payload["detail"]
    blocked = [
        row for row in payload["fixture_receipts"] if row["status"] == "BLOCKED"
    ]
    assert blocked
    assert blocked[0]["dependency_db_read_failures"] == []


def test_db_read_success_binds_dependency_and_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the persistence read returns a row identity, the dependency binds
    and the fixture create fires with the materialized body."""
    _patch_empty_list_read(monkeypatch)

    def fake_db_read(**kwargs: object) -> dict:
        return {
            "adapter": "db_sql",
            "method": "DB_READ",
            "table": "addresses",
            "identity_column": "id",
            "status_code": 200,
            "value": "addr-observed-1",
            "reason_code": "",
            "row_count": 2,
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core."
        "_run_declared_db_identity_read",
        fake_db_read,
    )
    governed_bodies: list[object] = []

    def fake_governed_write(**kwargs: object) -> dict:
        governed_bodies.append(kwargs.get("body"))
        return {
            "accepted": True,
            "write": {
                "status": 201,
                "body": {"id": "order-created-1"},
                "path": str(kwargs.get("path") or ""),
            },
            "after": {"status": 200, "body": [{"id": "order-created-1"}]},
            "after_ref": "after-ref-1",
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core."
        "execute_governed_control_write",
        fake_governed_write,
    )
    result = materialize_experiment_fixtures(**_inputs(_orders_ir(), _binding()))
    # The order create must have fired exactly once, carrying the observed
    # address identity in the materialized body.
    assert len(governed_bodies) == 1
    body = governed_bodies[0]
    assert isinstance(body, dict)
    assert body.get("addressId") == "addr-observed-1"
    assert result["status"] != "terminal" or (
        result["result"].get("status") != "BLOCKED"
    )
