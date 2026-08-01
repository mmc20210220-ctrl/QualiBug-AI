"""Regression: pre-resolved body-placeholder bindings reach transport.

The batch pre-resolution step resolved body placeholders (e.g. an order
body's ``addressId``) from the binding's own declared owner-scoped list read
(``GET /api/users/addresses``) and merged them into the binding plan as
``status=bound`` + ``materialized_value``. The fixture materializer then
discarded that value because its identity-proof shortcut only derived proof
routes from path context (``/{address_id}`` has none), so every order write
blocked at execution with ``runtime_read_binding_unresolved:address_id``.

The materializer now re-observes the pre-resolved value on the binding's own
declared list read before transport and keeps the value blocked when the read
does not contain it (fail closed, no invented identifiers).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.experiment_fixture_materializer_core import (
    materialize_experiment_fixtures,
)


def _experiment(**overrides: object) -> dict:
    experiment: dict = {
        "experiment_id": "exp_body_binding",
        "obligation_id": "obl_body_binding",
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_bind_address"],
            "nodes": [
                {
                    "node_id": "fix_bind_address",
                    "kind": "runtime_read_binding",
                    "target": "address_id",
                    "constructible": True,
                }
            ],
        },
        "binding_plan": [
            {
                "target": "address_id",
                "target_path": "/{address_id}",
                "status": "bound",
                "source_priority": "same_actor_list_read",
                "materialized_value": "addr-123",
                "body_template_paths": ["addressId"],
                "resolver_operations": [
                    {
                        "operation_ref": "op_addresses",
                        "method": "GET",
                        "path": "/api/users/addresses",
                    }
                ],
            }
        ],
        "control_plan": [],
        "treatment_plan": [
            {
                "actor_ref": "actor_buyer",
                "operation_ref": "op_orders",
                "path": "/api/orders",
            }
        ],
        "observers": [
            {"observer_id": "http_response", "surface": "http_api"}
        ],
        "assertions": [{"kind": "validation_rejection"}],
        "safety_contract": {"governed_write": True, "cleanup_not_required": True},
        "compiled_adapters": ["http_api"],
    }
    experiment.update(overrides)
    return experiment


def _materializer_inputs(**overrides: object) -> dict:
    inputs: dict = {
        "exp": _experiment(),
        "eid": "exp_body_binding",
        "oid": "obl_body_binding",
        "resolved_campaign_id": "CMP_test",
        "resolved_execution_id": "EXEC_test",
        "started": time.time(),
        "actors": {
            "actor_buyer": {
                "id": "actor_buyer",
                "role": "buyer",
                "credential_secret_ref": "secret:buyer",
            }
        },
        "ops": {
            "op_orders": {
                "id": "op_orders",
                "method": "POST",
                "path": "/api/orders",
            },
            "op_addresses": {
                "id": "op_addresses",
                "method": "GET",
                "path": "/api/users/addresses",
            },
        },
        "tokens": {"secret:buyer": "token-buyer", "buyer": "token-buyer"},
        "binding_plan": {
            "address_id": {
                "target": "address_id",
                "target_path": "/{address_id}",
                "status": "bound",
                "source_priority": "same_actor_list_read",
                "materialized_value": "addr-123",
                "body_template_paths": ["addressId"],
                "resolver_operations": [
                    {
                        "operation_ref": "op_addresses",
                        "method": "GET",
                        "path": "/api/users/addresses",
                    }
                ],
            }
        },
        "resolver_actor_ref": "actor_buyer",
        "resolver_token": "token-buyer",
        "activation_requirements": {"actor": [], "fixture": [], "cleanup": []},
        "root": Path("."),
        "project": "test-project",
        "base_url": "http://target.test",
        "runtime_contract": {
            "status": "approved",
            "approved_base_url": "http://target.test",
        },
        "campaign_id": "CMP_test",
    }
    inputs.update(overrides)
    return inputs


def test_pre_resolved_body_value_reobserved_on_declared_list_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, str]] = []

    def fake_run_http_step(**kwargs: object) -> dict:
        path = str(kwargs.get("path") or "")
        observed.append((path, str(kwargs.get("token") or "")))
        return {
            "method": "GET",
            "path": path,
            "status_code": 200,
            "body": [{"id": "addr-123", "is_default": True}],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    result = materialize_experiment_fixtures(**_materializer_inputs())
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("address_id") == "addr-123"
    assert ("/api/users/addresses", "token-buyer") in observed
    proof = next(
        row
        for row in result["fixture_receipts"]
        if row.get("target") == "address_id"
    )
    assert proof["status"] == "resolved"
    assert proof.get("proof_source") == "/api/users/addresses"


def test_pre_resolved_body_value_kept_blocked_when_not_reobserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_http_step(**kwargs: object) -> dict:
        return {
            "method": "GET",
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
    result = materialize_experiment_fixtures(**_materializer_inputs())
    assert result["status"] == "terminal"
    terminal = result["result"]
    assert terminal["status"] == "BLOCKED"
    assert terminal["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "address_id" in terminal["detail"]


def test_pre_resolved_body_value_rejected_on_wrong_collection_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value observed on a different collection must not be accepted."""

    def fake_run_http_step(**kwargs: object) -> dict:
        return {
            "method": "GET",
            "path": str(kwargs.get("path") or ""),
            "status_code": 200,
            "body": [{"id": "OTHER-ID", "is_default": False}],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    result = materialize_experiment_fixtures(**_materializer_inputs())
    assert result["status"] == "terminal"
    assert result["result"]["reason_code"] == "BLOCKED_MISSING_BINDING"
