"""Observed write body: schema-declared bodies sourced from real entities.

A write operation that declares a request schema but no request example
used to block at compile time with ``source_declared_request_body_missing``
(89 obligations in the benchmark funnel). The compiler now falls back to a
caller-scoped collection GET on the write's own collection: the runtime
projects the schema-declared fields from one observed row into the request
body — the environment's own test data as evidence, never synthesized
values like ``"test_value"``.

Fail-closed chain: the entity observed by the resolver must declare every
required body field (compile time) -> the runtime projects only schema-
declared fields from the best-coverage row -> the pre-transport required-
field gate still blocks visibly when the observed data cannot satisfy the
schema.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.experiment_compiler_obligation import (
    compile_experiment_for_obligation,
)
from ai_test_asset_center.experiment_compiler_support import (
    _observed_write_body_resolver,
)
from ai_test_asset_center.experiment_fixture_materializer_core import (
    materialize_experiment_fixtures,
)
from ai_test_asset_center.experiment_plan_step_executor_core import (
    execute_non_barrier_plans,
)
from ai_test_asset_center.experiment_runtime_support import (
    project_observed_body,
)
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


def _relation(
    relation_id: str,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    *,
    operation_ref: str = "",
    actor_ref: str = "",
) -> dict:
    return {
        "id": relation_id,
        "relation_type": relation_type,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "operation_ref": operation_ref or to_ref,
        "actor_ref": actor_ref or from_ref,
        "preconditions": [],
        "effects": [],
        "status": "accepted",
        "confidence": 0.9,
        "source_refs": [
            {"source_id": "test", "locator": relation_id, "kind": "relation"}
        ],
    }


def _write_ir(*, entity_fields: list | None = None) -> dict:
    """Write op with schema but no example + same-collection owned GET."""
    ir = empty_behavior_ir(project_id="observed-write-body")
    ir.update({
        "operations": [
            {
                "id": "op-cart-get",
                "method": "GET",
                "path": "/api/cart/items",
                "read_write": "read",
                "summary": "list cart items",
                "tags": ["api"],
                "source_refs": [
                    {
                        "source_id": "api_spec",
                        "locator": "GET /api/cart/items",
                        "kind": "api_operation",
                    }
                ],
            },
            {
                "id": "op-cart-post",
                "method": "POST",
                "path": "/api/cart/items",
                "read_write": "write",
                # No request_example: the body must come from observed data.
                "request_schema": {
                    "type": "object",
                    "required": ["sku", "qty", "userId"],
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer"},
                        "userId": {"type": "string"},
                    },
                },
                "tags": ["api"],
                "source_refs": [
                    {
                        "source_id": "api_spec",
                        "locator": "POST /api/cart/items",
                        "kind": "api_operation",
                    }
                ],
            },
            {
                "id": "op-cart-delete",
                "method": "DELETE",
                "path": "/api/cart/items/{id}",
                "read_write": "write",
                "tags": ["api"],
                "source_refs": [
                    {
                        "source_id": "api_spec",
                        "locator": "DELETE /api/cart/items/{id}",
                        "kind": "api_operation",
                    }
                ],
            },
            {
                "id": "op-addresses",
                "method": "GET",
                "path": "/api/users/addresses",
                "read_write": "read",
                "summary": "list caller addresses, ownership checked via userId",
                "tags": ["api", "userId"],
                "source_refs": [
                    {
                        "source_id": "api_spec",
                        "locator": "GET /api/users/addresses",
                        "kind": "api_operation",
                    }
                ],
            },
        ],
        "entities": [
            {
                "id": "ent-cart-items",
                "name": "cart_items",
                "fields": (
                    entity_fields
                    if entity_fields is not None
                    else ["id", "sku", "qty", "user_id"]
                ),
                "status": "accepted",
            },
        ],
        "actors": [
            {
                "id": "actor-buyer-a",
                "role": "buyer",
                "account_ref": "buyer_a",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:buyer_a",
            },
            {
                "id": "actor-buyer-b",
                "role": "buyer",
                "account_ref": "buyer_b",
                "account_status": "active",
                "credential_secret_ref": "secret_ref:test_accounts:buyer_b",
            },
        ],
        "relations": [
            _relation(
                "owns-a-get", "owns", "actor-buyer-a", "op-cart-get",
                operation_ref="op-cart-get", actor_ref="actor-buyer-a",
            ),
            _relation(
                "owns-b-get", "owns", "actor-buyer-b", "op-cart-get",
                operation_ref="op-cart-get", actor_ref="actor-buyer-b",
            ),
            _relation(
                "owns-a-post", "owns", "actor-buyer-a", "op-cart-post",
                operation_ref="op-cart-post", actor_ref="actor-buyer-a",
            ),
            _relation(
                "owns-b-post", "owns", "actor-buyer-b", "op-cart-post",
                operation_ref="op-cart-post", actor_ref="actor-buyer-b",
            ),
            _relation(
                "obs-get", "observes", "op-cart-get", "ent-cart-items",
                operation_ref="op-cart-get",
            ),
            _relation(
                "prod-post", "produces", "op-cart-post", "ent-cart-items",
                operation_ref="op-cart-post",
            ),
            _relation(
                "comp-del", "compensates", "op-cart-delete", "op-cart-post",
                operation_ref="op-cart-delete",
            ),
        ],
    })
    return ir


# ── Resolver unit: strict fail-closed validation ────────────────────────


def _resolver_kwargs(**overrides: object) -> dict:
    ir = _write_ir()
    kwargs: dict = {
        "operation": next(
            op for op in ir["operations"] if op["id"] == "op-cart-post"
        ),
        "behavior_ir": ir,
        "required_fields": ["sku", "qty", "userId"],
        "projection_fields": ["sku", "qty", "userId"],
    }
    kwargs.update(overrides)
    return kwargs


def test_resolver_matches_collection_get_with_full_required_coverage() -> None:
    resolver = _observed_write_body_resolver(**_resolver_kwargs())
    assert resolver["operation_ref"] == "op-cart-get"
    assert resolver["method"] == "GET"
    assert resolver["path"] == "/api/cart/items"
    assert resolver["projection_fields"] == ["sku", "qty", "userId"]
    assert resolver["binding_semantics"] == "caller_scoped"


def test_resolver_derives_collection_from_member_write_path() -> None:
    kwargs = _resolver_kwargs()
    kwargs["operation"] = {
        "id": "op-cart-put",
        "method": "PUT",
        "path": "/api/cart/items/{id}",
        "request_schema": {
            "type": "object",
            "required": ["sku"],
            "properties": {"sku": {"type": "string"}},
        },
    }
    kwargs["required_fields"] = ["sku"]
    kwargs["projection_fields"] = ["sku"]
    resolver = _observed_write_body_resolver(**kwargs)
    assert resolver["operation_ref"] == "op-cart-get"


def test_resolver_rejects_empty_projection_fields() -> None:
    assert _observed_write_body_resolver(
        **_resolver_kwargs(projection_fields=[])
    ) == {}


def test_resolver_rejects_entity_missing_required_field() -> None:
    ir = _write_ir(entity_fields=["id", "qty", "user_id"])  # no sku
    assert _observed_write_body_resolver(
        **_resolver_kwargs(behavior_ir=ir)
    ) == {}


def test_resolver_rejects_entity_without_declared_fields() -> None:
    ir = _write_ir(entity_fields=[])
    assert _observed_write_body_resolver(
        **_resolver_kwargs(behavior_ir=ir)
    ) == {}


def test_resolver_rejects_missing_collection_get() -> None:
    ir = _write_ir()
    ir["operations"] = [
        op for op in ir["operations"] if op["id"] != "op-cart-get"
    ]
    assert _observed_write_body_resolver(
        **_resolver_kwargs(behavior_ir=ir)
    ) == {}


def test_resolver_rejects_get_without_source_refs() -> None:
    ir = _write_ir()
    for op in ir["operations"]:
        if op["id"] == "op-cart-get":
            op["source_refs"] = []
    assert _observed_write_body_resolver(
        **_resolver_kwargs(behavior_ir=ir)
    ) == {}


def test_resolver_rejects_member_get_with_placeholders() -> None:
    ir = _write_ir()
    for op in ir["operations"]:
        if op["id"] == "op-cart-get":
            op["path"] = "/api/cart/items/{id}"
            op["raw_path"] = "/api/cart/items/{id}"
    assert _observed_write_body_resolver(
        **_resolver_kwargs(behavior_ir=ir)
    ) == {}


def test_resolver_rejects_get_without_observed_entity() -> None:
    ir = _write_ir()
    ir["relations"] = [
        row for row in ir["relations"] if row["id"] != "obs-get"
    ]
    assert _observed_write_body_resolver(
        **_resolver_kwargs(behavior_ir=ir)
    ) == {}


# ── Runtime projection semantics ────────────────────────────────────────


def test_projection_uses_schema_names_and_matches_snake_case_rows() -> None:
    body = project_observed_body(
        [{"sku": "SKU-1", "qty": 2, "user_id": "u-a"}],
        ["sku", "qty", "userId"],
    )
    assert body == {"sku": "SKU-1", "qty": 2, "userId": "u-a"}


def test_projection_picks_best_coverage_row_for_coherence() -> None:
    rows = [
        {"id": "c1", "sku": "SKU-1"},
        {"id": "c2", "sku": "SKU-9", "qty": 3, "user_id": "u-a"},
    ]
    body = project_observed_body(rows, ["sku", "qty", "userId"])
    assert body == {"sku": "SKU-9", "qty": 3, "userId": "u-a"}


def test_projection_skips_empty_values_and_copies_complex_ones() -> None:
    body = project_observed_body(
        [{"sku": "", "qty": None, "items": [{"sku": "SKU-1", "qty": 1}]}],
        ["sku", "qty", "items"],
    )
    assert body == {"items": [{"sku": "SKU-1", "qty": 1}]}


def test_projection_returns_empty_when_no_row_matches() -> None:
    assert project_observed_body([{"id": "c1"}], ["sku"]) == {}
    assert project_observed_body([], ["sku"]) == {}
    assert project_observed_body([{"sku": "S"}], []) == {}


# ── Compile gate: fallback binding + synthetic body suppression ─────────


def test_write_without_example_compiles_with_observed_body_binding() -> None:
    ir = _write_ir()
    obligation = next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-post"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    binding = next(
        row
        for row in experiment.get("binding_plan") or []
        if isinstance(row, dict) and row.get("target") == "__observed_body"
    )
    assert binding["source_priority"] == "observed_entity_write_body"
    assert binding["status"] == "runtime_resolvable"
    assert binding["body_projection_fields"] == ["sku", "qty", "userId"]
    resolvers = binding["resolver_operations"]
    assert len(resolvers) == 1
    assert resolvers[0]["operation_ref"] == "op-cart-get"
    assert resolvers[0]["path"] == "/api/cart/items"
    control_ref = (
        obligation["property"].get("control_actor_ref")
        or obligation["property"].get("owner_actor_ref")
    )
    assert binding["fixture_owner_actor_ref"] == control_ref
    # The ownership override stays step-compiled; no synthetic "test_value"
    # body may appear anywhere in the plans.
    treatment = experiment["treatment_plan"][0]
    assert treatment.get("body") == {"userId": "{user_id}"}
    plan_text = str(experiment.get("control_plan")) + str(
        experiment.get("treatment_plan")
    )
    assert "test_value" not in plan_text


def test_write_without_example_or_covering_entity_stays_blocked() -> None:
    ir = _write_ir(entity_fields=["id", "qty", "user_id"])  # no sku
    obligation = next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-post"
    )
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    receipt = experiment["compile_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "source_declared_request_body_missing" in receipt.get("detail", "")


# ── Runtime materialization: projection from observed rows ──────────────


def _body_experiment() -> dict:
    return {
        "experiment_id": "exp_observed_body",
        "obligation_id": "obl_observed_body",
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_bind_body"],
            "nodes": [
                {
                    "node_id": "fix_bind_body",
                    "kind": "runtime_read_binding",
                    "target": "__observed_body",
                    "constructible": True,
                }
            ],
        },
        "binding_plan": [
            {
                "target": "__observed_body",
                "target_path": "/{__observed_body}",
                "status": "runtime_resolvable",
                "source_priority": "observed_entity_write_body",
                "fixture_owner_actor_ref": "actor-buyer-a",
                "body_projection_fields": ["sku", "qty", "userId"],
                "resolver_operations": [
                    {
                        "operation_ref": "op-cart-get",
                        "method": "GET",
                        "path": "/api/cart/items",
                        "binding_semantics": "caller_scoped",
                    }
                ],
            }
        ],
        "control_plan": [
            {
                "actor_ref": "actor-buyer-a",
                "operation_ref": "op-cart-post",
                "path": "/api/cart/items",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "actor-buyer-b",
                "operation_ref": "op-cart-post",
                "path": "/api/cart/items",
            }
        ],
        "observers": [{"observer_id": "http_response", "surface": "http_api"}],
        "assertions": [{"kind": "authorization_denied"}],
        "safety_contract": {"governed_write": True, "cleanup_not_required": True},
        "compiled_adapters": ["http_api"],
    }


def _body_inputs(**overrides: object) -> dict:
    binding = _body_experiment()["binding_plan"][0]
    inputs: dict = {
        "exp": _body_experiment(),
        "eid": "exp_observed_body",
        "oid": "obl_observed_body",
        "resolved_campaign_id": "CMP_test",
        "resolved_execution_id": "EXEC_test",
        "started": time.time(),
        "actors": {
            "actor-buyer-a": {
                "id": "actor-buyer-a",
                "role": "buyer",
                "credential_secret_ref": "secret:buyer_a",
            },
            "actor-buyer-b": {
                "id": "actor-buyer-b",
                "role": "buyer",
                "credential_secret_ref": "secret:buyer_b",
            },
        },
        "ops": {
            "op-cart-get": {
                "id": "op-cart-get",
                "method": "GET",
                "path": "/api/cart/items",
            },
            "op-cart-post": {
                "id": "op-cart-post",
                "method": "POST",
                "path": "/api/cart/items",
            },
        },
        "tokens": {
            "secret:buyer_a": "token-a",
            "secret:buyer_b": "token-b",
            "buyer": "token-a",
        },
        "binding_plan": {"__observed_body": dict(binding)},
        "resolver_actor_ref": "actor-buyer-b",
        "resolver_token": "token-b",
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


def test_materializer_projects_body_from_best_coverage_row_with_writer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, str]] = []

    def fake_run_http_step(**kwargs: object) -> dict:
        path = str(kwargs.get("path") or "")
        token = str(kwargs.get("token") or "")
        observed.append((path, token))
        return {
            "method": "GET",
            "path": path,
            "status_code": 200,
            "body": [
                {"id": "c1", "sku": "SKU-1"},
                {"id": "c2", "sku": "SKU-9", "qty": 3, "user_id": "u-a"},
            ],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    result = materialize_experiment_fixtures(**_body_inputs())
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("__observed_body") == {
        "sku": "SKU-9",
        "qty": 3,
        "userId": "u-a",
    }
    assert ("/api/cart/items", "token-a") in observed
    assert all(token != "token-b" for _, token in observed)


def test_materializer_stays_blocked_when_no_row_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_http_step(**kwargs: object) -> dict:
        return {
            "method": "GET",
            "path": str(kwargs.get("path") or ""),
            "status_code": 200,
            "body": [{"id": "c1", "other": "no-projection-fields"}],
            "headers": {},
            "duration_ms": 1,
            "error": "",
            "raw": {},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    result = materialize_experiment_fixtures(**_body_inputs())
    assert result["status"] == "terminal"
    assert result["result"]["status"] == "BLOCKED"
    assert result["result"]["reason_code"] == "BLOCKED_MISSING_BINDING"


# ── Executor: observed body fills the write, required gate stays honest ──


def _executor_op() -> dict:
    return {
        "id": "op-1",
        "method": "POST",
        "path": "/api/cart/items",
        "request_schema": {
            "type": "object",
            "required": ["sku", "qty"],
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
            },
        },
    }


def _executor_inputs(**overrides: object) -> dict:
    inputs: dict = {
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "method": "POST",
                "operation_ref": "op-1",
                "actor_ref": "actor-1",
                "path": "/api/cart/items",
            }
        ],
        "consumed_barrier_steps": set(),
        "actors": {"actor-1": {"actor_id": "actor-1", "role": "buyer"}},
        "ops": {"op-1": _executor_op()},
        "tokens": {},
        "runtime_bindings": {},
        "activation_requirements": {"control": [], "treatment": ["treatment_1"]},
        "observations": {},
        "eid": "exp-1",
        "oid": "obl-1",
        "resolved_campaign_id": "CMP-1",
        "resolved_execution_id": "exec-1",
        "campaign_id": "CMP-1",
        "root": Path("/tmp"),
        "project": "proj-1",
        "base_url": "http://target.invalid",
        "runtime_contract": {},
    }
    inputs.update(overrides)
    return inputs


def test_executor_merges_observed_body_and_reports_only_real_gaps() -> None:
    """Observed body supplies sku; qty stays honestly missing — the gate
    reports exactly the field the observed data could not supply."""
    result = execute_non_barrier_plans(**_executor_inputs(
        runtime_bindings={"__observed_body": {"sku": "SKU-1"}},
    ))
    blocked = [
        step
        for step in result["steps"]
        if str(step.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert blocked, result
    reason = blocked[0]["skipped_reason"]
    assert "qty" in reason
    assert "sku" not in reason


def test_executor_without_observed_body_blocks_all_required_fields() -> None:
    result = execute_non_barrier_plans(**_executor_inputs())
    blocked = [
        step
        for step in result["steps"]
        if str(step.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert blocked, result
    assert "sku" in blocked[0]["skipped_reason"]
    assert "qty" in blocked[0]["skipped_reason"]


def test_executor_observed_body_satisfying_schema_passes_required_gate() -> None:
    result = execute_non_barrier_plans(**_executor_inputs(
        runtime_bindings={"__observed_body": {"sku": "SKU-1", "qty": 2}},
    ))
    missing_blocks = [
        step
        for step in result["steps"]
        if str(step.get("skipped_reason", "")).startswith(
            "BLOCKED_MISSING_REQUIRED_BODY_FIELDS"
        )
    ]
    assert not missing_blocks, result
