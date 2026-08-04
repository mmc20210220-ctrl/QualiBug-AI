"""Owner identity from caller-scoped owned-entity reads (zero-pollution).

When the Behavior IR declares no ``*/me`` operation, the isolation family
used to block with ``owner_identity_resolver_missing`` even though the
target exposes caller-scoped owned collections whose rows carry the owner
field. The compiler now binds the arm identity from a source-declared
owned-entity read instead — but only through a strict caller-scope chain:

* a source-declared ``owns`` relation must tie the control actor to the
  resolver operation (another actor's read is cross-contamination);
* the observed entity must declare the ownership field in its source
  fields (otherwise the value would be assumed, not observed);
* the runtime resolves with the owner's own credentials and binds only on
  owner-field consensus across every observed row — disagreement fails
  closed with ``owner_identity_conflict`` instead of arming the treatment
  step with a possibly foreign identity.
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
    _owned_entity_identity_resolver,
)
from ai_test_asset_center.experiment_fixture_materializer_core import (
    materialize_experiment_fixtures,
)
from ai_test_asset_center.experiment_runtime_support import (
    consensus_identity_value,
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


def _cart_get_operation() -> dict:
    return {
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
    }


def _owned_collection_ir(*, entity_fields: list | None = None) -> dict:
    """Owned collection read corpus WITHOUT any /me operation."""
    ir = empty_behavior_ir(project_id="owner-identity-owned-entity")
    ir.update({
        "operations": [
            _cart_get_operation(),
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
                "obs-get", "observes", "op-cart-get", "ent-cart-items",
                operation_ref="op-cart-get",
            ),
        ],
    })
    return ir


def _isolation_obligation(ir: dict) -> dict:
    return next(
        row
        for row in compile_obligations_from_behavior_ir(ir)["obligations"]
        if row["risk_family"] == "isolation"
        and row["property"]["operation_ref"] == "op-cart-get"
    )


# ── Compile gate: fallback binding ──────────────────────────────────────


def test_isolation_without_me_binds_identity_from_owned_entity_read() -> None:
    ir = _owned_collection_ir()
    obligation = _isolation_obligation(ir)
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    binding = next(
        row
        for row in experiment.get("binding_plan") or []
        if isinstance(row, dict) and row.get("target") == "user_id"
    )
    assert binding["source_priority"] == "owner_identity_owned_entity_read"
    assert binding["identity_extraction"] == "owner_field_consensus"
    control_ref = (
        obligation["property"].get("control_actor_ref")
        or obligation["property"].get("owner_actor_ref")
    )
    assert binding["fixture_owner_actor_ref"] == control_ref
    resolvers = binding["resolver_operations"]
    assert len(resolvers) == 1
    assert resolvers[0]["operation_ref"] == "op-cart-get"
    assert resolvers[0]["method"] == "GET"
    assert resolvers[0]["path"] == "/api/cart/items"
    assert resolvers[0]["declaring_actor_ref"] == control_ref
    assert resolvers[0]["binding_semantics"] == "caller_scoped"
    assert binding["arm_isolated_resolvers"]["control"] == resolvers


def test_isolation_without_me_or_owned_read_stays_visibly_blocked() -> None:
    """No owned entity declaring the owner field -> the historical
    owner_identity_resolver_missing block is preserved unchanged."""
    ir = _owned_collection_ir(entity_fields=["id", "sku", "qty"])
    obligation = _isolation_obligation(ir)
    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    receipt = experiment["compile_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "owner_identity_resolver_missing" in receipt.get("detail", "")


# ── Resolver unit: strict caller-scope validation ───────────────────────


def _resolver_kwargs(ir: dict, **overrides: object) -> dict:
    kwargs: dict = {
        "control_actor_ref": "actor-buyer-a",
        "identity_target": "user_id",
        "ownership_param": "userId",
        "behavior_ir": ir,
        "actors": {row["id"]: row for row in ir["actors"]},
        "preferred_operation_ref": "op-cart-get",
    }
    kwargs.update(overrides)
    return kwargs


def test_resolver_matches_camel_ownership_param_to_snake_entity_field() -> None:
    resolver = _owned_entity_identity_resolver(
        **_resolver_kwargs(_owned_collection_ir())
    )
    assert resolver["operation_ref"] == "op-cart-get"
    assert resolver["identity_extraction"] == "owner_field_consensus"
    assert resolver["binding_semantics"] == "caller_scoped"


def test_resolver_rejects_actor_outside_registry() -> None:
    assert _owned_entity_identity_resolver(
        **_resolver_kwargs(_owned_collection_ir(), control_actor_ref="actor-ghost")
    ) == {}


def test_resolver_rejects_operation_owned_only_by_another_actor() -> None:
    """An owned read belonging only to actor B must never serve actor A's
    arm identity — that is the cross-contamination boundary."""
    ir = _owned_collection_ir()
    ir["relations"] = [
        row for row in ir["relations"] if row["id"] != "owns-a-get"
    ]
    assert _owned_entity_identity_resolver(**_resolver_kwargs(ir)) == {}


def test_resolver_rejects_conflicting_owns_relation() -> None:
    ir = _owned_collection_ir()
    for row in ir["relations"]:
        if row["id"] == "owns-a-get":
            row["status"] = "conflicting"
    assert _owned_entity_identity_resolver(**_resolver_kwargs(ir)) == {}


def test_resolver_rejects_non_collection_operation() -> None:
    ir = _owned_collection_ir()
    for op in ir["operations"]:
        if op["id"] == "op-cart-get":
            op["path"] = "/api/cart/items/{id}"
            op["raw_path"] = "/api/cart/items/{id}"
    assert _owned_entity_identity_resolver(**_resolver_kwargs(ir)) == {}


def test_resolver_rejects_operation_without_source_refs() -> None:
    ir = _owned_collection_ir()
    for op in ir["operations"]:
        if op["id"] == "op-cart-get":
            op["source_refs"] = []
    assert _owned_entity_identity_resolver(**_resolver_kwargs(ir)) == {}


def test_resolver_rejects_operation_without_observed_entity() -> None:
    ir = _owned_collection_ir()
    ir["relations"] = [
        row for row in ir["relations"] if row["id"] != "obs-get"
    ]
    assert _owned_entity_identity_resolver(**_resolver_kwargs(ir)) == {}


def test_resolver_rejects_entity_without_ownership_field() -> None:
    ir = _owned_collection_ir(entity_fields=["id", "sku", "qty"])
    assert _owned_entity_identity_resolver(**_resolver_kwargs(ir)) == {}


def test_resolver_prefers_obligation_primary_operation() -> None:
    ir = _owned_collection_ir()
    second = _cart_get_operation()
    second["id"] = "op-aaa-first-by-id"
    ir["operations"].append(second)
    ir["relations"].extend([
        _relation(
            "owns-a-second", "owns", "actor-buyer-a", second["id"],
            operation_ref=second["id"], actor_ref="actor-buyer-a",
        ),
        _relation(
            "obs-second", "observes", second["id"], "ent-cart-items",
            operation_ref=second["id"],
        ),
    ])
    resolver = _owned_entity_identity_resolver(**_resolver_kwargs(ir))
    assert resolver["operation_ref"] == "op-cart-get"


# ── Runtime consensus extraction ────────────────────────────────────────


def test_consensus_identity_value_agreeing_rows_bind() -> None:
    body = {"items": [{"id": "c1", "user_id": "u-a"}, {"id": "c2", "userId": "u-a"}]}
    assert consensus_identity_value(body, "user_id") == ("u-a", "consensus")


def test_consensus_identity_value_single_row_binds() -> None:
    assert consensus_identity_value([{"user_id": "u-a"}], "user_id") == (
        "u-a",
        "consensus",
    )


def test_consensus_identity_value_disagreeing_rows_conflict() -> None:
    body = [{"id": "c1", "user_id": "u-a"}, {"id": "c2", "user_id": "u-b"}]
    assert consensus_identity_value(body, "user_id") == ("", "conflicted")


def test_consensus_identity_value_missing_field_is_absent_not_conflict() -> None:
    body = [{"id": "c1", "sku": "SKU-1"}, {"id": "c2"}]
    assert consensus_identity_value(body, "user_id") == ("", "absent")


def test_consensus_identity_value_ignores_generic_identity_fallbacks() -> None:
    """The entity's own id/sku/code fields name the RESOURCE, not the
    OWNER; they must never satisfy an owner-identity target."""
    body = [{"id": "c1", "sku": "SKU-1", "code": "C-1"}]
    assert consensus_identity_value(body, "user_id") == ("", "absent")


def test_consensus_identity_value_fieldless_rows_abstain() -> None:
    body = [{"user_id": "u-a"}, {"sku": "no-owner-field-here"}]
    assert consensus_identity_value(body, "user_id") == ("u-a", "consensus")


# ── Runtime materialization: caller-scoped resolution + conflict guard ──


def _identity_experiment() -> dict:
    return {
        "experiment_id": "exp_owner_identity",
        "obligation_id": "obl_owner_identity",
        "fixture_dag": {
            "status": "READY",
            "setup_order": ["fix_bind_user"],
            "nodes": [
                {
                    "node_id": "fix_bind_user",
                    "kind": "runtime_read_binding",
                    "target": "user_id",
                    "constructible": True,
                }
            ],
        },
        "binding_plan": [
            {
                "target": "user_id",
                "target_path": "/{user_id}",
                "status": "runtime_resolvable",
                "source_priority": "owner_identity_owned_entity_read",
                "identity_extraction": "owner_field_consensus",
                "fixture_owner_actor_ref": "actor-buyer-a",
                "resolver_operations": [
                    {
                        "operation_ref": "op-cart-get",
                        "method": "GET",
                        "path": "/api/cart/items",
                        "declaring_actor_ref": "actor-buyer-a",
                        "binding_semantics": "caller_scoped",
                        "identity_extraction": "owner_field_consensus",
                    }
                ],
            }
        ],
        "control_plan": [
            {
                "actor_ref": "actor-buyer-a",
                "operation_ref": "op-cart-get",
                "path": "/api/cart/items",
            }
        ],
        "treatment_plan": [
            {
                "actor_ref": "actor-buyer-b",
                "operation_ref": "op-cart-get",
                "path": "/api/cart/items",
            }
        ],
        "observers": [{"observer_id": "http_response", "surface": "http_api"}],
        "assertions": [{"kind": "authorization_denied"}],
        "safety_contract": {"governed_write": False, "cleanup_not_required": True},
        "compiled_adapters": ["http_api"],
    }


def _identity_inputs(**overrides: object) -> dict:
    binding = _identity_experiment()["binding_plan"][0]
    inputs: dict = {
        "exp": _identity_experiment(),
        "eid": "exp_owner_identity",
        "oid": "obl_owner_identity",
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
        },
        "tokens": {
            "secret:buyer_a": "token-a",
            "secret:buyer_b": "token-b",
            "buyer": "token-a",
        },
        "binding_plan": {"user_id": dict(binding)},
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


def _http_obs(path: str, token: str, body: object, method: str = "GET") -> dict:
    return {
        "method": method,
        "path": path,
        "status_code": 200,
        "body": body,
        "headers": {},
        "duration_ms": 1,
        "error": "",
        "raw": {},
    }


def test_materializer_binds_identity_with_owner_credentials_on_consensus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read must execute with the OWNER's token (fixture_owner_actor_ref),
    never with the treatment arm's resolver token — that is the runtime half
    of the caller-scope contract."""
    observed: list[tuple[str, str]] = []

    def fake_run_http_step(**kwargs: object) -> dict:
        path = str(kwargs.get("path") or "")
        token = str(kwargs.get("token") or "")
        observed.append((path, token))
        return _http_obs(
            path,
            token,
            [{"id": "c1", "user_id": "u-a"}, {"id": "c2", "user_id": "u-a"}],
        )

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    result = materialize_experiment_fixtures(**_identity_inputs())
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("user_id") == "u-a"
    assert ("/api/cart/items", "token-a") in observed
    assert all(token != "token-b" for _, token in observed)


def test_materializer_fails_closed_on_owner_field_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed owners in the observed collection = contamination signal. The
    binding must block observably and must NOT attempt fixture creation to
    paper over it."""
    observed_methods: list[str] = []

    def fake_run_http_step(**kwargs: object) -> dict:
        method = str(kwargs.get("method") or "GET")
        observed_methods.append(method)
        return _http_obs(
            str(kwargs.get("path") or ""),
            str(kwargs.get("token") or ""),
            [{"id": "c1", "user_id": "u-a"}, {"id": "c2", "user_id": "u-b"}],
            method=method,
        )

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    result = materialize_experiment_fixtures(**_identity_inputs())
    assert result["status"] == "terminal"
    terminal = result["result"]
    assert terminal["status"] == "BLOCKED"
    conflict_receipts = [
        row
        for row in terminal.get("fixture_receipts") or []
        if "owner_identity_conflict" in str(row.get("detail") or "")
    ]
    assert conflict_receipts, "conflict must stay observable on the receipt"
    assert "POST" not in observed_methods, "no fixture create may be attempted"


def test_materializer_without_consensus_marker_uses_legacy_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bindings without the consensus marker keep the legacy first-match
    extraction — the stricter path is opt-in via the compiled marker only."""

    def fake_run_http_step(**kwargs: object) -> dict:
        return _http_obs(
            str(kwargs.get("path") or ""),
            str(kwargs.get("token") or ""),
            [{"id": "c1", "user_id": "u-a"}, {"id": "c2", "user_id": "u-b"}],
        )

    monkeypatch.setattr(
        "ai_test_asset_center.experiment_fixture_materializer_core._run_http_step",
        fake_run_http_step,
    )
    inputs = _identity_inputs()
    for binding in inputs["exp"]["binding_plan"]:
        binding.pop("identity_extraction", None)
    inputs["binding_plan"]["user_id"].pop("identity_extraction", None)
    result = materialize_experiment_fixtures(**inputs)
    assert result["status"] == "ready"
    assert result["runtime_bindings"].get("user_id") == "u-a"
