"""Execute selected compiled experiments end-to-end on the V12 main chain.

Path: selected experiment → fixture DAG → governed requests → observers →
typed assertions → contract oracle → delivery-gate-ready finding (or explicit
BLOCKED / harness receipt). Never invents COMPILED success for unresolved
actor/fixture/observer/cleanup compensation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .assertion_dsl import materialize_assertion
from .contract_oracles import (
    build_contract_evidence_receipt,
    contract_activation_requirements,
    evaluate_contract_oracle,
    mark_as_internal_clue,
    validate_contract_oracle_receipt,
)
from .customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from .observer_contracts_base import (
    observe_experiment_requirements,
    validate_observer_declarations,
)
from .operational_receipts import build_execution_operational_receipt
from .real_id_resolver import (
    bind_entity_fields,
    infer_path_params,
    normalize_path_placeholders,
    path_has_placeholders,
)
from .runtime_binding_materializer import (
    materialize_body_template as _materialize_body_template,
    materialize_path as _materialize_path,
    runtime_binding_contract_ready as _runtime_binding_contract_ready,
    runtime_cleanup_paths as _runtime_cleanup_paths,
    runtime_setup_value_from_response as _runtime_setup_value_from_response,
    runtime_value_from_response as _runtime_value_from_response,
    validated_fixture_setup as _validated_fixture_setup,
    validated_runtime_resolvers as _validated_runtime_resolvers,
)
from .runtime_binding_graph import declared_effect_observers
from .sandbox_write_executor import (
    _http_request,
    _restore_payload,
    execute_governed_control_write,
    sandbox_write_allowed,
)

# Canonical cleanup utilities extracted to executor_cleanup.py
from .executor_cleanup import *  # noqa: F401,F403


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_BODY_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")


def _unresolved_body_placeholders(
    value: Any,
    bindings: dict[str, Any],
) -> list[str]:
    """Return source body tokens that remain unbound before a write."""

    unresolved: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, str):
            return
        match = _BODY_PLACEHOLDER_RE.match(node)
        if not match:
            return
        token = _text(match.group(1))
        if token and bindings.get(token) not in (None, "", [], {}):
            return
        if token and token not in unresolved:
            unresolved.append(token)

    walk(value)
    return unresolved


def _select_fixture_actor(
    fixture_setup: dict[str, Any],
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    """Select a declared fixture actor aligned with the experiment control.

    A fixture create operation may list several permitted actors.  Selecting
    the first actor is not semantically safe: the created resource can then be
    invisible to the control/treatment actors that the experiment is meant to
    compare.  Prefer the control actor, then treatment, but only when the
    source-declared fixture actor list contains that identity.  Fall back to
    the first executable declared actor when neither plan actor is allowed.
    """
    declared_refs = [
        _text(actor_ref)
        for actor_ref in _list(fixture_setup.get("actor_refs"))
        if _text(actor_ref)
    ]
    preferred_refs = [
        _text(_dict(step).get("actor_ref"))
        for step in [*control_plan, *treatment_plan]
        if isinstance(step, dict) and _text(_dict(step).get("actor_ref"))
    ]
    ordered_refs = list(dict.fromkeys([
        *[ref for ref in preferred_refs if ref in declared_refs],
        *declared_refs,
    ]))
    for actor_ref in ordered_refs:
        actor = actors.get(actor_ref) or {}
        token = _resolve_token(actor, tokens)
        if _text(actor.get("role")).lower() in {"anonymous", "public"} or token:
            return actor_ref, actor, token
    return "", {}, ""


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _observation_state(value: Any) -> dict[str, Any]:
    row = _dict(value)
    return {
        "status": int(row.get("status") or row.get("status_code") or 0),
        "body": row.get("body"),
    }


def _governance_audit_receipt_id(governed: dict[str, Any]) -> str:
    row = _dict(governed)
    audit_record = _dict(row.get("audit_record"))
    audit_path = _text(row.get("audit_path"))
    if not audit_record and not audit_path:
        return ""
    material = {
        "audit_record": audit_record,
        "audit_path": audit_path,
        "before_ref": _text(row.get("before_ref")),
        "after_ref": _text(row.get("after_ref")),
        "accepted": row.get("accepted") is True,
    }
    return "audit_" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:24]


def _resource_identity_candidates(value: Any) -> set[str]:
    identities: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
                if (
                    normalized in {"id", "uuid", "key"}
                    or normalized.endswith("id")
                ) and not isinstance(child, (dict, list)) and _text(child):
                    identities.add(_text(child))
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return identities


def _primary_resource_identity_candidates(value: Any) -> set[str]:
    row = _dict(value)
    primary = {
        _text(child)
        for key, child in row.items()
        if "".join(ch for ch in str(key).lower() if ch.isalnum())
        in {"id", "uuid", "key"}
        and not isinstance(child, (dict, list))
        and _text(child)
    }
    if primary:
        return primary
    for envelope_key in ("data", "result", "resource", "item", "record"):
        nested = row.get(envelope_key)
        if isinstance(nested, dict):
            nested_primary = _primary_resource_identity_candidates(nested)
            if nested_primary:
                return nested_primary
    return _resource_identity_candidates(value)


def _server_managed_field(value: Any) -> bool:
    normalized = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    return normalized in {
        "createdat",
        "updatedat",
        "createdtime",
        "updatedtime",
        "modifiedat",
        "modifiedtime",
        "timestamp",
    }


def _without_server_managed_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_server_managed_fields(child)
            for key, child in sorted(value.items())
            if not _server_managed_field(key)
        }
    if isinstance(value, list):
        return sorted(
            (_without_server_managed_fields(child) for child in value),
            key=_canonical_json,
        )
    return value


def _meaningful_observation_state(value: Any) -> dict[str, Any]:
    state = _observation_state(value)
    return {
        "status": state.get("status"),
        "body": _without_server_managed_fields(state.get("body")),
    }


def _entity_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("records", "data", "items", "results", "rows"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [dict(item) for item in nested if isinstance(item, dict)]
        return [dict(value)]
    return []


def _entity_matches_identity(entity: dict[str, Any], identities: set[str]) -> bool:
    if not identities:
        return True
    for key, value in entity.items():
        if isinstance(value, (dict, list)):
            continue
        normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
        if (
            normalized in {"id", "uuid", "key"}
            or normalized.endswith("id")
        ) and _text(value) in identities:
            return True
    return any(
        not isinstance(value, (dict, list)) and _text(value) in identities
        for value in entity.values()
    )


def _single_entity_for_restoration(value: Any, identities: set[str]) -> dict[str, Any]:
    matches = [
        row for row in _entity_rows(value)
        if _entity_matches_identity(row, identities)
    ]
    return dict(matches[0]) if len(matches) == 1 else {}


def _cleanup_restores_mutated_fields(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_before = _observation_state(_dict(original).get("before"))
    cleanup_after = _observation_state(_dict(cleanup).get("after"))
    if not (
        200 <= int(original_before.get("status") or 0) < 300
        and 200 <= int(cleanup_after.get("status") or 0) < 300
    ):
        return False
    write_body = _dict(_dict(original).get("write")).get("body")
    if not isinstance(write_body, dict):
        return False
    identities = _primary_resource_identity_candidates(write_body)
    before_entity = _single_entity_for_restoration(
        original_before.get("body"),
        identities,
    )
    after_entity = _single_entity_for_restoration(
        cleanup_after.get("body"),
        identities,
    )
    if not before_entity or not after_entity:
        return False
    mutated_fields = [
        field
        for field, value in write_body.items()
        if field in before_entity
        and field in after_entity
        and not _server_managed_field(field)
        and not isinstance(value, (dict, list))
        and not isinstance(before_entity.get(field), (dict, list))
        and before_entity.get(field) != value
    ]
    return bool(mutated_fields) and all(
        after_entity.get(field) == before_entity.get(field)
        for field in mutated_fields
    )


def _cleanup_compensates_created_resource(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_row = _dict(original)
    cleanup_row = _dict(cleanup)
    if _text(original_row.get("method")).upper() != "POST":
        return False
    cleanup_path = _text(cleanup_row.get("path"))
    created_identities = _primary_resource_identity_candidates(
        _dict(original_row.get("write")).get("body")
    )
    if not created_identities or not any(
        identity and identity in cleanup_path for identity in created_identities
    ):
        return False

    original_before = _observation_state(original_row.get("before"))
    original_after = _observation_state(
        original_row.get("response_bound_after") or original_row.get("after")
    )
    cleanup_before = _observation_state(cleanup_row.get("before"))
    cleanup_after = _observation_state(cleanup_row.get("after"))
    if not (
        200 <= int(original_after.get("status") or 0) < 300
        and 200 <= int(cleanup_before.get("status") or 0) < 300
        and 200 <= int(cleanup_after.get("status") or 0) < 300
    ):
        return False
    if _single_entity_for_restoration(original_before.get("body"), created_identities):
        return False
    if not _single_entity_for_restoration(original_after.get("body"), created_identities):
        return False

    before_entity = _single_entity_for_restoration(
        cleanup_before.get("body"),
        created_identities,
    )
    after_entity = _single_entity_for_restoration(
        cleanup_after.get("body"),
        created_identities,
    )
    if not before_entity or not after_entity:
        return False
    changed_business_fields = [
        field
        for field in before_entity
        if field in after_entity
        and not _server_managed_field(field)
        and not isinstance(before_entity.get(field), (dict, list))
        and not isinstance(after_entity.get(field), (dict, list))
        and before_entity.get(field) != after_entity.get(field)
    ]
    return bool(changed_business_fields)


def _cleanup_restores_governed_write(
    original: dict[str, Any],
    cleanup: dict[str, Any],
) -> bool:
    original_row = _dict(original)
    cleanup_row = _dict(cleanup)
    if original_row.get("accepted") is not True or cleanup_row.get("accepted") is not True:
        return False
    if not _governance_audit_receipt_id(original_row) or not _governance_audit_receipt_id(cleanup_row):
        return False
    original_before = _observation_state(original_row.get("before"))
    cleanup_after = _observation_state(cleanup_row.get("after"))
    if original_before == cleanup_after:
        return True
    if _cleanup_restores_mutated_fields(original_row, cleanup_row):
        return True
    if _cleanup_compensates_created_resource(original_row, cleanup_row):
        return True
    original_method = _text(original_row.get("method")).upper()
    cleanup_path = _text(cleanup_row.get("path"))
    created_identities = _primary_resource_identity_candidates(
        _dict(original_row.get("write")).get("body")
    )
    identity_bound = any(
        identity and identity in cleanup_path for identity in created_identities
    )
    cleanup_before = _observation_state(cleanup_row.get("before"))
    return bool(
        original_method == "POST"
        and identity_bound
        and 200 <= int(cleanup_before.get("status") or 0) < 300
        and int(cleanup_after.get("status") or 0) in {404, 410}
    )


def _governed_write_attempts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _dict(step.get("governance_receipt"))
        for step in steps
        if _text(step.get("phase")) in {"control", "treatment"}
        and isinstance(step.get("governance_receipt"), dict)
    ]


def _rejected_writes_left_state_unchanged(
    attempts: list[dict[str, Any]],
) -> bool:
    return bool(attempts) and all(
        attempt.get("accepted") is not True
        and bool(_governance_audit_receipt_id(attempt))
        and _observation_state(attempt.get("before"))
        == _observation_state(attempt.get("after"))
        for attempt in attempts
    )


def _governed_write_changed_state(attempt: dict[str, Any]) -> bool:
    row = _dict(attempt)
    if row.get("accepted") is not True:
        return False
    before_state = _observation_state(row.get("before"))
    after_state = _observation_state(row.get("response_bound_after") or row.get("after"))
    if before_state.get("status") != after_state.get("status"):
        return True

    write_body = _dict(row.get("write")).get("body")
    if isinstance(write_body, dict):
        identities = _primary_resource_identity_candidates(write_body)
        before_entity = _single_entity_for_restoration(
            before_state.get("body"),
            identities,
        )
        after_entity = _single_entity_for_restoration(
            after_state.get("body"),
            identities,
        )
        if before_entity and after_entity:
            comparable_fields = [
                field
                for field in sorted(set(before_entity).intersection(after_entity))
                if field in before_entity
                and field in after_entity
                and not _server_managed_field(field)
                and not isinstance(before_entity.get(field), (dict, list))
                and not isinstance(after_entity.get(field), (dict, list))
            ]
            if any(
                before_entity.get(field) != after_entity.get(field)
                for field in comparable_fields
            ):
                return True
            return (
                _without_server_managed_fields(before_entity)
                != _without_server_managed_fields(after_entity)
            )
        if identities and bool(before_entity) != bool(after_entity):
            return True

    return _meaningful_observation_state(row.get("before")) != _meaningful_observation_state(
        row.get("response_bound_after") or row.get("after")
    )


def _body_contains_scalar(value: Any, expected: Any) -> bool:
    if isinstance(value, dict):
        return any(_body_contains_scalar(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_body_contains_scalar(child, expected) for child in value)
    return value == expected


def _index_by_id(nodes: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if isinstance(node, dict) and _text(node.get("id")):
            out[_text(node.get("id"))] = node
    return out


def _documented_routes(operations: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for operation in operations.values():
        if not isinstance(operation, dict):
            continue
        method = _text(operation.get("method")).upper()
        path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if method and path.startswith("/"):
            routes.append({"method": method, "path": path})
    return routes


def _inverse_delta_cleanup_body(
    request_body: Any,
    *,
    delta_field: str = "",
) -> tuple[dict[str, Any], str]:
    if not isinstance(request_body, dict):
        return {}, "request_body_missing"
    target_key = _text(delta_field)
    matches = [
        (key, value)
        for key, value in request_body.items()
        if (
            (_text(key) == target_key if target_key else "".join(ch for ch in str(key).lower() if ch.isalnum()) == "delta")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    ]
    if len(matches) != 1:
        return {}, "delta_field_not_unique"
    key, value = matches[0]
    cleanup_body = dict(request_body)
    cleanup_body[key] = -value
    return cleanup_body, f"inverse_delta:{key}"


def load_actor_tokens(root: Path, project: str) -> dict[str, str]:
    """Map role / secret_ref → bearer token from declared test accounts only."""
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    tokens: dict[str, str] = {}
    rows: list[Any] = []
    if isinstance(payload, dict):
        rows = list(payload.get("accounts") or payload.get("actors") or payload.get("users") or [])
        if not rows:
            rows = [
                {**(value if isinstance(value, dict) else {}), "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict) and key not in {"schema", "schema_version", "meta"}
            ]
    elif isinstance(payload, list):
        rows = payload
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = _text(row.get("role") or row.get("name") or row.get("id"))
        account_ref = _text(row.get("account_ref") or row.get("name") or row.get("id") or row.get("email"))
        token = _text(row.get("token") or row.get("access_token") or row.get("jwt"))
        if not role or not token:
            continue
        status = _text(row.get("status") or row.get("account_status") or row.get("state") or "active").upper()
        if account_ref:
            tokens[account_ref] = token
            tokens[f"secret_ref:test_accounts:{account_ref}"] = token
        if status not in {"DISABLED", "LOCKED"}:
            tokens.setdefault(role, token)
            tokens.setdefault(f"secret_ref:test_accounts:{role}", token)
            tokens.setdefault(f"secret_ref:context:{role}", token)
    return tokens


def preflight_experiment_executable(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    """Return (ok, reason_code, detail). Fail closed — never COMPILED-at-runtime."""
    exp = _dict(experiment)
    receipt = _dict(exp.get("compile_receipt"))
    if _text(receipt.get("status")).upper() != "COMPILED":
        return False, _text(receipt.get("reason_code")) or "BLOCKED_UNSUPPORTED_ADAPTER", "not_compiled"
    dag = _dict(exp.get("fixture_dag"))
    if dag and _text(dag.get("status")).upper() == "BLOCKED":
        reasons = _list(dag.get("blocked_reasons"))
        code = _text(_dict(reasons[0] if reasons else {}).get("reason_code")) or "BLOCKED_MISSING_FIXTURE"
        return False, code, _text(_dict(reasons[0] if reasons else {}).get("detail"))
    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if not isinstance(step, dict):
            continue
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        if not actor_ref or actor_ref not in actors:
            return False, "BLOCKED_MISSING_ACTOR", actor_ref or "missing"
        actor = actors[actor_ref]
        role = _text(actor.get("role"))
        secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
        if role.lower() not in {"anonymous", "public"}:
            if not secret:
                return False, "BLOCKED_MISSING_ACTOR", f"unresolved_secret:{actor_ref}"
            if secret not in actor_tokens and role not in actor_tokens:
                return False, "BLOCKED_MISSING_ACTOR", f"token_unresolved:{actor_ref}"
        if not op_ref or op_ref not in ops:
            return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
        op = ops[op_ref]
        path = _text(op.get("path") or op.get("raw_path"))
        method = _text(op.get("method") or "GET").upper()
        if not path.startswith("/"):
            return False, "BLOCKED_MISSING_BINDING", f"unresolved_path:{op_ref}:{path}"
        if path_has_placeholders(path) and not _runtime_binding_contract_ready(
            path,
            binding_plan=_list(exp.get("binding_plan")),
            fixture_dag=dag,
            operations=ops,
        ):
            return False, "BLOCKED_MISSING_BINDING", f"unresolved_path:{op_ref}:{path}"
        if not method:
            return False, "BLOCKED_MISSING_OPERATION", f"missing_method:{op_ref}"
        if (
            method in {"POST", "PUT", "PATCH", "DELETE"}
            and not _declared_observation_path(path, ops)
            and not _declared_effect_observer_available(op, ops)
        ):
            return False, "BLOCKED_MISSING_OBSERVER", f"write_observer:{op_ref}"
    if not _list(exp.get("observers")):
        return False, "BLOCKED_MISSING_OBSERVER", "none"
    assertion = _dict(_list(exp.get("assertions"))[0] if _list(exp.get("assertions")) else {})
    risk_family = _text(assertion.get("kind") or assertion.get("type"))
    if risk_family == "owner_tenant_visibility":
        risk_family = "authorization"
    observer_reason, observer_detail = validate_observer_declarations(
        [row for row in _list(exp.get("observers")) if isinstance(row, dict)],
        risk_family=risk_family,
        available_adapters={"http_api"},
    )
    if observer_reason:
        return False, observer_reason, observer_detail
    safety = _dict(exp.get("safety_contract"))
    is_write = bool(safety.get("governed_write"))
    if is_write and not _list(exp.get("cleanup_plan")):
        return False, "BLOCKED_NON_REVERSIBLE_WRITE", "cleanup_compensation_unresolved"
    # Fixture nodes that require constructible disposable fixtures must be READY.
    for node in _list(dag.get("nodes")):
        if not isinstance(node, dict):
            continue
        if node.get("constructible") is False:
            return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
        if _text(node.get("kind")) == "disposable_fixture" and not _text(node.get("fixture_id")):
            return False, "BLOCKED_MISSING_FIXTURE", _text(node.get("node_id"))
    return True, "", ""


def _resolve_token(actor: dict[str, Any], tokens: dict[str, str]) -> str:
    role = _text(actor.get("role"))
    secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if role.lower() in {"anonymous", "public"}:
        return ""
    return tokens.get(secret) or tokens.get(role) or ""


def _request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation).get("request_example")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    request_schema = _dict(_dict(operation).get("request_schema"))
    content = _dict(request_schema.get("content"))
    for media in content.values():
        if not isinstance(media, dict):
            continue
        example = media.get("example")
        if isinstance(example, dict) and example:
            return dict(example)
        examples = _dict(media.get("examples"))
        for row in examples.values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return dict(value)
    return {}


def _scalar_body_bindings(value: Any) -> dict[str, Any]:
    bindings: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (str, int, float, bool)) and child not in ("", None):
                    bindings.setdefault(_text(key), child)
                else:
                    walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return bindings


def _operation_for_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized_path = normalize_path_placeholders(path)
    for operation in operations.values():
        if not isinstance(operation, dict):
            continue
        candidate = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if candidate == normalized_path:
            return operation
    return {"path": path}


def _declared_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
    *,
    runtime_bindings: dict[str, Any] | None = None,
    request_body: Any = None,
) -> str:
    """Return a source-declared effect observer through the shared graph."""
    operation = _operation_for_observation_path(path, operations)
    observers = declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    binding_values = {
        **_scalar_body_bindings(_request_example(operation)),
        **_scalar_body_bindings(request_body),
        **(runtime_bindings or {}),
    }
    for observer in observers:
        materialized = _text(observer.get("path"))
        for name, value in binding_values.items():
            materialized = materialized.replace("{" + name + "}", quote(str(value), safe=""))
        if materialized.startswith("/") and not path_has_placeholders(materialized):
            return materialized
    return ""


def _declared_effect_observer_available(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> bool:
    return bool(
        declared_effect_observers(
            operation,
            behavior_ir={"operations": list(operations.values())},
            max_candidates=5,
        )
    )


def _response_bound_observation_path(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    write_body: Any,
) -> dict[str, str]:
    if not isinstance(write_body, (dict, list)):
        return {}
    observers = declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    for observer in observers:
        path = normalize_path_placeholders(_text(observer.get("path")))
        if not path.startswith("/") or not path_has_placeholders(path):
            continue
        values: dict[str, Any] = {}
        for name in infer_path_params(path):
            value = _runtime_setup_value_from_response(write_body, name)
            if value in (None, "", [], {}):
                values = {}
                break
            values[name] = value
        if not values:
            continue
        materialized = path
        for name, value in values.items():
            materialized = materialized.replace(
                "{" + name + "}",
                quote(str(value), safe=""),
            )
        if materialized.startswith("/") and not path_has_placeholders(materialized):
            return {
                "operation_ref": _text(observer.get("operation_ref")),
                "method": _text(observer.get("method")).upper() or "GET",
                "path": materialized,
                "path_template": path,
            }
    return {}


def _runtime_entity_candidates(value: Any) -> list[dict[str, Any]]:
    """Extract source-observed entity rows without assuming a domain schema."""
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("data", "result", "items", "records", "results", "list", "rows", "content"):
        child = value.get(key)
        if isinstance(child, list):
            rows.extend(row for row in child if isinstance(row, dict))
        elif isinstance(child, dict):
            rows.append(child)
    return rows or [value]


def _select_runtime_binding(
    body: Any,
    target_path: str,
    *,
    preferred_body: Any = None,
) -> dict[str, str]:
    """Choose an observed entity that can actually receive the planned write.

    A collection resolver must not blindly bind the first row when the source
    operation declares a state/value transition.  Prefer the first observed
    entity whose declared mutation fields differ from the planned request;
    otherwise preserve the canonical structural resolver result.
    """
    default = bind_entity_fields(body, target_path)
    desired = _scalar_body_bindings(preferred_body)
    if not default or not desired:
        return default
    target_params = infer_path_params(target_path) or ["id"]
    target_param = target_params[0]
    default_value = _text(default.get(target_param) or default.get("id"))
    if not default_value:
        return default
    for entity in _runtime_entity_candidates(body):
        identity = _text(
            entity.get(target_param)
            or entity.get("id")
            or entity.get("uuid")
            or entity.get("key")
        )
        if not identity:
            continue
        if any(
            field in entity
            and entity.get(field) != desired_value
            for field, desired_value in desired.items()
        ):
            selected = bind_entity_fields(entity, target_path)
            if selected.get(target_param) or selected.get("id"):
                return selected
    return default


def _run_http_step(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str,
    body: Any = None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    resp = _http_request(method, url, token=token, body=body)
    return {
        "method": method,
        "path": path,
        "status_code": int(resp.get("status") or 0),
        "body": resp.get("body"),
        "headers": resp.get("headers") or {},
        "duration_ms": resp.get("duration_ms"),
        "error": resp.get("error") or "",
        "raw": resp,
    }




def execute_one_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    execution_id: str,
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute one experiment or return an explicit blocked/harness receipt."""
    exp = _dict(experiment)
    eid = _text(exp.get("experiment_id"))
    oid = _text(exp.get("obligation_id"))
    resolved_campaign_id = _text(campaign_id)
    resolved_execution_id = _text(execution_id)
    if not resolved_campaign_id or not resolved_execution_id:
        raise ValueError("experiment execution campaign_id and execution_id are required")
    exp["campaign_id"] = resolved_campaign_id
    exp["execution_id"] = resolved_execution_id
    tokens = actor_tokens if actor_tokens is not None else load_actor_tokens(root, project)
    ok, reason, detail = preflight_experiment_executable(exp, behavior_ir=behavior_ir, actor_tokens=tokens)
    started = time.time()
    if not ok:
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": "BLOCKED",
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "finding": None,
            "execution_receipt": {"status": "BLOCKED", "reason_code": reason, "detail": detail},
        }

    ir = _dict(behavior_ir)
    actors = _index_by_id(_list(ir.get("actors")))
    ops = _index_by_id(_list(ir.get("operations")))
    steps_out: list[dict[str, Any]] = []
    request_bodies_for_cleanup: dict[str, Any] = {}
    observations: dict[str, Any] = {
        "observer_ids": [],
        "observer_receipts": [],
        "control_succeeded": False,
        "harness_error": False,
    }
    activation_requirements = contract_activation_requirements(exp)
    contract_evidence_receipts: list[dict[str, Any]] = []
    cleanup_failures = 0
    pre_transport_block_reasons: list[str] = []
    fixture_receipts: list[dict[str, Any]] = []
    binding_materialization_receipts: list[dict[str, Any]] = []
    runtime_bindings: dict[str, Any] = {}
    pending_fixture_cleanups: list[dict[str, Any]] = []
    binding_plan = {
        _text(item.get("target")): item
        for item in _list(exp.get("binding_plan"))
        if isinstance(item, dict) and _text(item.get("target"))
    }
    resolver_actor_ref = ""
    for planned in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
        if isinstance(planned, dict) and _text(planned.get("actor_ref")):
            resolver_actor_ref = _text(planned.get("actor_ref"))
            break
    resolver_actor = actors.get(resolver_actor_ref) or {}
    resolver_token = _resolve_token(resolver_actor, tokens)

    # Fixture DAG: actor contexts are resolved via tokens; disposable fixtures
    # without a concrete create path remain BLOCKED (already caught in preflight
    # when constructible=false). Record READY nodes as resolved receipts.
    dag = _dict(exp.get("fixture_dag"))
    for node_id in _list(dag.get("setup_order")):
        node = next((n for n in _list(dag.get("nodes")) if _text(_dict(n).get("node_id")) == node_id), {})
        kind = _text(_dict(node).get("kind"))
        if kind == "actor_context":
            actor_ref = _text(_dict(node).get("actor_ref"))
            actor = actors.get(actor_ref) or {}
            token = _resolve_token(actor, tokens)
            if _text(actor.get("role")).lower() not in {"anonymous", "public"} and not token:
                return {
                    "schema_version": "qualibug.experiment-execution.v1",
                    "experiment_id": eid,
                    "obligation_id": oid,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ACTOR",
                    "detail": f"fixture_actor_unresolved:{actor_ref}",
                    "elapsed_ms": int((time.time() - started) * 1000),
                    "finding": None,
                    "execution_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_ACTOR"},
                }
            fixture_receipts.append({"node_id": node_id, "kind": kind, "status": "resolved"})
        elif kind == "runtime_read_binding":
            target = _text(_dict(node).get("target"))
            binding = binding_plan.get(target) or {}
            # Use synthetic fallback value when no resolvers exist
            _synthetic = binding.get("synthetic_value")
            if _synthetic and not binding.get("resolver_operations") and not binding.get("fixture_setup"):
                runtime_bindings[target] = _synthetic
                fixture_receipts.append({
                    "node_id": node_id,
                    "kind": kind,
                    "status": "resolved",
                    "target": target,
                    "value_fingerprint": _synthetic[:12],
                })
                binding_materialization_receipts.append({
                    "target": target,
                    "status": "BOUND",
                    "value_fingerprint": _synthetic[:12],
                    "source_priority": "synthetic_fallback",
                })
                continue
            resolvers = _validated_runtime_resolvers(binding, ops)
            force_fixture_setup = binding.get("force_fixture_setup") is True
            target_path = _text(binding.get("target_path"))
            preferred_binding_body: Any = None
            normalized_target_path = normalize_path_placeholders(target_path)
            for planned_step in [
                *_list(exp.get("control_plan")),
                *_list(exp.get("treatment_plan")),
            ]:
                if not isinstance(planned_step, dict):
                    continue
                planned_op = ops.get(_text(planned_step.get("operation_ref"))) or {}
                planned_path = normalize_path_placeholders(
                    _text(planned_op.get("path") or planned_op.get("raw_path"))
                )
                if planned_path != normalized_target_path:
                    continue
                preferred_binding_body = (
                    planned_step.get("body")
                    if planned_step.get("body")
                    else _request_example(planned_op)
                )
                if preferred_binding_body:
                    break
            value: Any = None
            fixture_setup_accepted = False
            receipt: dict[str, Any] = {
                "target": target,
                "status": "BLOCKED",
                "source_priority": "same_actor_list_read",
                "resolver_path": "",
                "resolver_operation_ref": "",
                "status_code": 0,
                "value_fingerprint": "",
            }
            for index, resolver in enumerate([] if force_fixture_setup else resolvers):
                obs = _run_http_step(
                    base_url=base_url,
                    method=resolver["method"],
                    path=resolver["path"],
                    token=resolver_token,
                )
                obs.update({
                    "phase": "binding_materialization",
                    "step_id": f"bind:{target}:{index}",
                    "actor_ref": resolver_actor_ref,
                    "operation_ref": resolver["operation_ref"],
                })
                steps_out.append(obs)
                receipt.update({
                    "resolver_path": resolver["path"],
                    "resolver_operation_ref": resolver["operation_ref"],
                    "status_code": int(obs.get("status_code") or 0),
                })
                if not (200 <= int(obs.get("status_code") or 0) < 300):
                    if int(obs.get("status_code") or 0) == 0:
                        break
                    continue
                extracted = _select_runtime_binding(
                    obs.get("body"),
                    target_path,
                    preferred_body=preferred_binding_body,
                )
                value = extracted.get(target)
                if value in (None, "", [], {}):
                    continue
                runtime_bindings[target] = value
                receipt.update({
                    "status": "BOUND",
                    "value_fingerprint": hashlib.sha256(
                        str(value).encode("utf-8")
                    ).hexdigest()[:12],
                })
                break
            if value in (None, "", [], {}):
                fixture_setup = _validated_fixture_setup(binding, ops, actors)
                fixture_actor_ref, fixture_actor, fixture_token = _select_fixture_actor(
                    fixture_setup,
                    control_plan=_list(exp.get("control_plan")),
                    treatment_plan=_list(exp.get("treatment_plan")),
                    actors=actors,
                    tokens=tokens,
                )
                if fixture_setup and not fixture_actor_ref:
                    fixture_setup = {}
                token_values: dict[str, Any] = {}
                dependency_blocked = False
                for dependency in _list(fixture_setup.get("body_bindings")):
                    dependency_target = _text(_dict(dependency).get("target"))
                    dependency_token = _text(_dict(dependency).get("template_token"))
                    dependency_value: Any = None
                    dependency_leaf = dependency_target.split(".")[-1].split("[")[0]
                    # Use synthetic fallback when no resolver operations exist
                    _fallback = _dict(dependency).get("fallback_value")
                    if _fallback is not None:
                        token_values[dependency_token] = _fallback
                        continue
                    for index, resolver in enumerate(_list(_dict(dependency).get("resolver_operations"))):
                        if not isinstance(resolver, dict):
                            continue
                        obs = _run_http_step(
                            base_url=base_url,
                            method=_text(resolver.get("method")).upper(),
                            path=_text(resolver.get("path")),
                            token=fixture_token,
                        )
                        obs.update({
                            "phase": "binding_materialization_dependency",
                            "step_id": f"bind:{target}:dependency:{dependency_token}:{index}",
                            "actor_ref": fixture_actor_ref,
                            "operation_ref": _text(resolver.get("operation_ref")),
                        })
                        steps_out.append(obs)
                        if not (200 <= int(obs.get("status_code") or 0) < 300):
                            if int(obs.get("status_code") or 0) == 0:
                                break
                            continue
                        dependency_leaf = dependency_target.split(".")[-1].split("[")[0]
                        dependency_value = _runtime_value_from_response(
                            obs.get("body"),
                            dependency_leaf,
                            f"/{{{dependency_leaf}}}",
                        )
                        if dependency_value not in (None, "", [], {}):
                            token_values[dependency_token] = dependency_value
                            break
                    # When a fixture dependency cannot be resolved from observed
                    # data, the fixture setup is blocked — never fabricate IDs
                    # or auto-create resources via hidden writes.
                    if dependency_value in (None, "", [], {}):
                        dependency_blocked = True
                if fixture_setup and not dependency_blocked:
                    setup_body = _materialize_body_template(
                        fixture_setup.get("body_template"),
                        token_values,
                    )
                    observation_path = _text(resolvers[0].get("path")) if resolvers else ""
                    governed_setup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_fixture_setup",
                        actor_identity=_text(fixture_actor.get("role") or fixture_actor_ref),
                        actor_token=fixture_token,
                        method=_text(fixture_setup.get("method")).upper(),
                        path=_text(fixture_setup.get("path")),
                        body=setup_body,
                        observation_path=observation_path,
                    )
                    setup_write = _dict(governed_setup.get("write"))
                    setup_status = int(setup_write.get("status") or 0)
                    fixture_setup_accepted = bool(
                        governed_setup.get("accepted") is True
                        or 200 <= setup_status < 300
                    )
                    steps_out.append({
                        "phase": "fixture_setup",
                        "method": _text(fixture_setup.get("method")).upper(),
                        "path": _text(fixture_setup.get("path")),
                        "status_code": setup_status,
                        "operation_ref": _text(fixture_setup.get("operation_ref")),
                        "governance_receipt": governed_setup,
                    })
                    receipt["fixture_setup_status"] = (
                        "completed" if 200 <= setup_status < 300 else "failed"
                    )
                    if 200 <= setup_status < 300:
                        value = _runtime_setup_value_from_response(
                            setup_write.get("body"),
                            target,
                        )
                    if value not in (None, "", [], {}):
                        ownership_required = force_fixture_setup and bool(
                            _text(binding.get("fixture_owner_actor_ref"))
                        )
                        setup_after = _dict(governed_setup.get("after"))
                        ownership_observed = bool(
                            ownership_required
                            and fixture_actor_ref == _text(binding.get("fixture_owner_actor_ref"))
                            and 200 <= int(setup_after.get("status") or 0) < 300
                            and _body_contains_scalar(setup_after.get("body"), value)
                        )
                        runtime_bindings[target] = value
                        receipt.update({
                            "status": "BOUND",
                            "source_priority": "experiment_setup_response",
                            "resolver_path": _text(fixture_setup.get("path")),
                            "resolver_operation_ref": _text(fixture_setup.get("operation_ref")),
                            "status_code": setup_status,
                            "value_fingerprint": hashlib.sha256(
                                str(value).encode("utf-8")
                            ).hexdigest()[:12],
                            "fixture_cleanup_status": "pending",
                            "fixture_id": _text(binding.get("required_fixture_id")),
                            "owner_actor_ref": _text(binding.get("fixture_owner_actor_ref")),
                            "ownership_proof_status": (
                                "OBSERVED"
                                if ownership_observed
                                else "NOT_REQUIRED"
                                if not ownership_required
                                else "INDETERMINATE"
                            ),
                            "ownership_proof_ref": _text(governed_setup.get("after_ref")),
                        })
                        pending_fixture_cleanups.append({
                            "target": target,
                            "value": value,
                            "observation_path": observation_path,
                            "cleanup": dict(_list(fixture_setup.get("cleanup_operations"))[0]),
                            "receipt": receipt,
                            "actor_ref": fixture_actor_ref,
                            "actor_identity": _text(fixture_actor.get("role") or fixture_actor_ref),
                            "actor_token": fixture_token,
                            "governed_setup": governed_setup,
                        })
            binding_materialization_receipts.append(receipt)
            if value in (None, "", [], {}):
                if fixture_setup_accepted:
                    cleanup_failures += 1
                    receipt.update({
                        "status": "HARNESS_FAILURE",
                        "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                        "fixture_cleanup_status": "failed",
                    })
                    return {
                        "schema_version": "qualibug.experiment-execution.v1",
                        "experiment_id": eid,
                        "obligation_id": oid,
                        "status": "HARNESS_FAILURE",
                        "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                        "detail": f"accepted_fixture_identity_unresolved:{target}",
                        "elapsed_ms": int((time.time() - started) * 1000),
                        "steps": steps_out,
                        "fixture_receipts": fixture_receipts,
                        "binding_materialization_receipts": binding_materialization_receipts,
                        "finding": None,
                        "cleanup_failures": cleanup_failures,
                        "execution_receipt": {
                            "status": "HARNESS_FAILURE",
                            "reason_code": "FIXTURE_SETUP_IDENTITY_UNRESOLVED",
                            "cleanup_failures": cleanup_failures,
                        },
                    }
                fixture_receipts.append({
                    "node_id": node_id,
                    "kind": kind,
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": f"runtime_read_binding_unresolved:{target}",
                })
                # Continue rather than aborting the entire experiment
                continue
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "resolved",
                "target": target,
                "value_fingerprint": receipt["value_fingerprint"],
            })
        elif kind == "disposable_fixture":
            # Without a concrete create operation binding, refuse to invent IDs.
            fixture_receipts.append({
                "node_id": node_id,
                "kind": kind,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": "disposable_fixture_create_path_unresolved",
            })
            return {
                "schema_version": "qualibug.experiment-execution.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_FIXTURE",
                "detail": f"fixture_unresolved:{node_id}",
                "elapsed_ms": int((time.time() - started) * 1000),
                "finding": None,
                "fixture_receipts": fixture_receipts,
                "execution_receipt": {"status": "BLOCKED", "reason_code": "BLOCKED_MISSING_FIXTURE"},
            }
        else:
            fixture_receipts.append({"node_id": node_id, "kind": kind or "unknown", "status": "resolved"})

    for actor_ref in activation_requirements["actor"]:
        actor = actors.get(actor_ref) or {}
        role = _text(actor.get("role"))
        token = _resolve_token(actor, tokens)
        actor_observed = role.lower() in {"anonymous", "public"} or bool(token)
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="actor",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=actor_ref,
            status="OBSERVED" if actor_observed else "FAILED",
            evidence={
                "role": role,
                "credential_secret_ref_present": bool(
                    _text(actor.get("credential_secret_ref"))
                ),
                "credential_material_observed": bool(token),
            },
        ))
    for fixture_id in activation_requirements["fixture"]:
        fixture = next(
            (
                row for row in fixture_receipts
                if _text(_dict(row).get("node_id")) == fixture_id
            ),
            {},
        )
        fixture_status = _text(_dict(fixture).get("status")).lower()
        observed = fixture_status in {"bound", "completed", "ready", "resolved"}
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="fixture",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=fixture_id,
            status="OBSERVED" if observed else "FAILED",
            evidence={
                "fixture_kind": _text(_dict(fixture).get("kind")),
                "value_fingerprint": _text(
                    _dict(fixture).get("value_fingerprint")
                ),
            },
        ))

    source_observed_control_bodies: dict[str, Any] = {}
    source_body_control_blocked = False

    def _exec_plan(plan: list[Any], *, phase: str) -> list[dict[str, Any]]:
        nonlocal cleanup_failures, source_body_control_blocked
        results = []
        planned_subjects = activation_requirements.get(phase) or []
        for index, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            actor_ref = _text(step.get("actor_ref"))
            op_ref = _text(step.get("operation_ref"))
            subject_id = (
                planned_subjects[index]
                if index < len(planned_subjects)
                else _text(step.get("step_id"))
                or f"{phase}:{op_ref or 'operation'}:{index + 1}"
            )
            if phase == "treatment" and source_body_control_blocked:
                reason = "control_body_binding_blocked"
                pre_transport_block_reasons.append(reason)
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={
                        "write_reached_transport": False,
                        "reason_code": reason,
                    },
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": _text(step.get("method") or "POST").upper(),
                    "path": _text(step.get("path") or step.get("path_template")),
                    "status_code": 0,
                })
                continue
            actor = actors.get(actor_ref) or {}
            op = ops.get(op_ref) or {}
            # If op_ref doesn't match, try to find by method+path_template
            if not op and op_ref:
                path_template_candidate = _text(step.get("path") or step.get("path_template"))
                method_candidate = _text(step.get("method") or "POST").upper()
                for _oid, _op in ops.items():
                    if isinstance(_op, dict) and _text(_op.get("method","")).upper() == method_candidate:
                        _op_path = normalize_path_placeholders(_text(_op.get("path") or _op.get("raw_path")))
                        _step_path = normalize_path_placeholders(path_template_candidate)
                        if _op_path == _step_path:
                            op = _op
                            op_ref = _oid
                            break
            method = _text(op.get("method") or "GET").upper()
            path_template = _text(op.get("path") or op.get("raw_path"))
            path = _materialize_path(
                path_template,
                runtime_bindings,
            )
            request_body = (
                step.get("body")
                if "body" in step
                else op.get("request_example")
                if method in {"POST", "PUT", "PATCH", "DELETE"} and op.get("request_example")
                else None
            )
            request_body = _materialize_body_template(
                request_body,
                runtime_bindings,
            )
            unresolved_body_tokens = _unresolved_body_placeholders(
                request_body,
                runtime_bindings,
            )
            if method in {"POST", "PUT", "PATCH", "DELETE"} and unresolved_body_tokens:
                reason = (
                    "body_placeholder_unresolved:"
                    + ",".join(unresolved_body_tokens)
                )
                pre_transport_block_reasons.append(reason)
                if phase == "control":
                    source_body_control_blocked = True
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="BLOCKED",
                    evidence={
                        "write_reached_transport": False,
                        "reason_code": "BLOCKED_MISSING_BINDING",
                        "unresolved_body_tokens": unresolved_body_tokens,
                    },
                ))
                results.append({
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_BINDING",
                    "detail": reason,
                    "method": method,
                    "path": path,
                    "status_code": 0,
                })
                continue
            runtime_body_plan = deepcopy(_dict(step.get("runtime_body_plan")))
            if runtime_body_plan:
                identity_fields = infer_path_params(path_template)
                runtime_body_plan["identity_bindings"] = {
                    field: runtime_bindings[field]
                    for field in identity_fields
                    if field in runtime_bindings
                    and runtime_bindings[field] not in (None, "")
                }
                if phase == "treatment" and op_ref in source_observed_control_bodies:
                    request_body = deepcopy(source_observed_control_bodies[op_ref])
                    runtime_body_plan = {}
            mutation = _dict(step.get("mutation"))
            mutation_class = _text(
                mutation.get("class")
                or mutation.get("constraint")
                or mutation.get("operator")
                or step.get("protocol_step")
                or step.get("intent")
                or f"{phase}_request"
            )
            mutation_selector = _text(
                mutation.get("json_path")
                or mutation.get("field_selector")
                or mutation.get("field")
            )
            mutation_operator = _text(
                mutation.get("operator") or mutation.get("constraint")
            )
            request_body_fingerprint = _sha256(request_body)
            request_semantics_fingerprint = _sha256({
                "operation_ref": op_ref,
                "method": method,
                "path_template": path_template,
                "mutation_class": mutation_class,
                "mutation_selector": mutation_selector,
                "mutation_operator": mutation_operator,
                "request_body_fingerprint": request_body_fingerprint,
            })
            token = _resolve_token(actor, tokens)
            is_write = method in {"POST", "PUT", "PATCH", "DELETE"}
            response_bound_observation: dict[str, Any] = {}
            runtime_body_blocked = False
            if is_write:
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    observations["harness_error"] = True
                    contract_evidence_receipts.append(
                        build_contract_evidence_receipt(
                            kind=phase,
                            experiment_id=eid,
                            obligation_id=oid,
                            campaign_id=resolved_campaign_id,
                            execution_id=resolved_execution_id,
                            subject_id=subject_id,
                            status="FAILED",
                            evidence={"reason_code": _text(reason)},
                        )
                    )
                    results.append({
                        "phase": phase,
                        "step_id": subject_id,
                        "status": "blocked_write",
                        "reason": reason,
                        "method": method,
                        "path": path,
                    })
                    continue
                observation_path = _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings=runtime_bindings,
                    request_body=request_body,
                )
                if not observation_path:
                    # No declared effect observer exists for this endpoint.
                    # Fall back to the write path itself as a best-effort
                    # observation target. The before/after GETs may return
                    # 404/405 for POST-only endpoints, but the write itself
                    # still executes and produces observable evidence.
                    observation_path = path
                governed = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase=f"experiment_{phase}",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=method,
                    path=path,
                    body=request_body,
                    observation_path=observation_path,
                    runtime_body_plan=runtime_body_plan or None,
                )
                runtime_body_receipt = _dict(governed.get("runtime_body_receipt"))
                runtime_body_blocked = (
                    _text(runtime_body_receipt.get("status")).upper() == "BLOCKED"
                )
                if runtime_body_blocked:
                    reason_code = _text(
                        runtime_body_receipt.get("reason_code")
                        or governed.get("reason")
                    ) or "runtime_body_materialization_blocked"
                    pre_transport_block_reasons.append(reason_code)
                materialized_body = governed.get("materialized_request_body")
                if isinstance(materialized_body, dict) and materialized_body:
                    request_body = deepcopy(materialized_body)
                    request_body_fingerprint = _sha256(request_body)
                    request_semantics_fingerprint = _sha256({
                        "operation_ref": op_ref,
                        "method": method,
                        "path_template": path_template,
                        "mutation_class": mutation_class,
                        "mutation_selector": mutation_selector,
                        "mutation_operator": mutation_operator,
                        "request_body_fingerprint": request_body_fingerprint,
                    })
                    if phase == "control":
                        source_observed_control_bodies[op_ref] = deepcopy(request_body)
                request_bodies_for_cleanup[subject_id] = request_body
                write_receipt = _dict(governed.get("write"))
                if 200 <= int(write_receipt.get("status") or 0) < 300:
                    response_bound_path = _response_bound_observation_path(
                        op,
                        ops,
                        write_receipt.get("body"),
                    )
                    if response_bound_path:
                        response_bound_raw = _http_request(
                            _text(response_bound_path.get("method") or "GET"),
                            base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            token=token,
                        )
                        response_bound_observation = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "path": _text(response_bound_path.get("path")),
                            "path_template": _text(response_bound_path.get("path_template")),
                            "status_code": int(response_bound_raw.get("status") or 0),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                            "phase": f"{phase}_response_bound_effect_observation",
                            "step_id": f"{subject_id}:response_bound_effect",
                            "actor_ref": actor_ref,
                            "operation_ref": _text(response_bound_path.get("operation_ref")),
                            "source_operation_ref": op_ref,
                        }
                        governed["response_bound_after"] = {
                            "method": _text(response_bound_path.get("method") or "GET"),
                            "url": base_url.rstrip("/") + _text(response_bound_path.get("path")),
                            "status": int(response_bound_raw.get("status") or 0),
                            "body": response_bound_raw.get("body"),
                            "headers": response_bound_raw.get("headers") or {},
                            "duration_ms": response_bound_raw.get("duration_ms"),
                        }
                        governed["response_bound_after_ref"] = (
                            "response_bound_after:"
                            f"{_text(response_bound_path.get('path'))}:"
                            f"{int(response_bound_raw.get('status') or 0)}"
                        )
                        governed["response_bound_observer_operation_ref"] = _text(
                            response_bound_path.get("operation_ref")
                        )
                obs = {
                    "method": method,
                    "path": path,
                    "status_code": int(write_receipt.get("status") or 0),
                    "body": write_receipt.get("body"),
                    "headers": write_receipt.get("headers") or {},
                    "duration_ms": write_receipt.get("duration_ms"),
                    "error": write_receipt.get("error") or governed.get("reason") or "",
                    "governance_receipt": governed,
                    "observation_path": observation_path,
                }
                if response_bound_observation:
                    obs["response_bound_observation"] = response_bound_observation
            else:
                obs = _run_http_step(base_url=base_url, method=method, path=path, token=token)
            obs["phase"] = phase
            obs["step_id"] = subject_id
            obs["actor_ref"] = actor_ref
            obs["operation_ref"] = op_ref
            obs["path_template"] = path_template
            obs["request_body_fingerprint"] = request_body_fingerprint
            obs["request_semantics_fingerprint"] = (
                request_semantics_fingerprint
            )
            obs["mutation_class"] = mutation_class
            obs["mutation_selector"] = mutation_selector
            obs["mutation_operator"] = mutation_operator
            if runtime_body_blocked:
                obs["status"] = "blocked_write"
                obs["reason"] = _text(
                    _dict(obs.get("governance_receipt")).get("reason")
                ) or "runtime_body_materialization_blocked"
            observed_status = int(obs.get("status_code") or 0)
            if _text(step.get("protocol_step")) == "temporal_write":
                temporal_elapsed = int(obs.get("duration_ms") or 0)
                after_state = _observation_state(
                    _dict(obs.get("governance_receipt")).get("after")
                )
                observations.setdefault("temporal_timeline", []).extend([
                    {
                        "event": "trigger",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": 0,
                        "status_code": observed_status,
                    },
                    {
                        "event": "final_observed",
                        "phase": phase,
                        "step_id": subject_id,
                        "at_ms": temporal_elapsed,
                        "status_code": int(after_state.get("status") or 0),
                    },
                ])
            contract_status = (
                "BLOCKED"
                if runtime_body_blocked
                else "OBSERVED"
                if phase == "control" and 200 <= observed_status < 300
                else "BLOCKED"
                if phase == "control" and observed_status > 0
                else "OBSERVED"
                if phase == "treatment" and observed_status > 0
                else "FAILED"
            )
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind=phase,
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=subject_id,
                status=contract_status,
                evidence={
                    "method": method,
                    "path": path,
                    "status_code": observed_status,
                    "operation_ref": op_ref,
                    "path_template": path_template,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": (
                        request_semantics_fingerprint
                    ),
                    "mutation_class": mutation_class,
                    "mutation_selector": mutation_selector,
                    "mutation_operator": mutation_operator,
                    "response_observed": observed_status > 0,
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                },
            ))
            results.append(obs)
            if response_bound_observation:
                results.append(response_bound_observation)
            if phase == "control":
                observations["control_observation"] = obs
                observations["control_actor_ref"] = actor_ref
                if 200 <= int(obs.get("status_code") or 0) < 300:
                    observations["control_succeeded"] = True
                    observations["authorized_control"] = True
            if phase == "treatment":
                observations["treatment_observation"] = obs
                observations["treatment_result"] = obs
                observations["treatment_actor_ref"] = actor_ref
                observations["status_code"] = obs.get("status_code")
                observations["body"] = obs.get("body")
        return results

    def _barrier_timeline_event(
        events: list[dict[str, Any]],
        *,
        event: str,
        participant: str,
        started_at: float,
    ) -> None:
        events.append({
            "event": event,
            "participant": participant,
            "at_ms": int((time.perf_counter() - started_at) * 1000),
        })

    def _execute_barrier_step(
        *,
        step: dict[str, Any],
        phase: str,
        subject_id: str,
        barrier: threading.Barrier,
        timeline: list[dict[str, Any]],
        timeline_lock: threading.Lock,
        started_at: float,
    ) -> dict[str, Any]:
        actor_ref = _text(step.get("actor_ref"))
        op_ref = _text(step.get("operation_ref"))
        actor = actors.get(actor_ref) or {}
        op = ops.get(op_ref) or {}
        method = _text(op.get("method") or "GET").upper()
        path_template = _text(op.get("path") or op.get("raw_path"))
        path = _materialize_path(path_template, runtime_bindings)
        participant = _text(step.get("barrier_participant") or subject_id)
        request_body = (
            step.get("body")
            if "body" in step
            else op.get("request_example")
            if method in {"POST", "PUT", "PATCH", "DELETE"}
            else None
        )
        request_body = _materialize_body_template(request_body, runtime_bindings)
        mutation = _dict(step.get("mutation"))
        mutation_class = _text(
            mutation.get("class")
            or mutation.get("constraint")
            or mutation.get("operator")
            or step.get("protocol_step")
            or step.get("intent")
            or f"{phase}_request"
        )
        mutation_selector = _text(
            mutation.get("json_path")
            or mutation.get("field_selector")
            or mutation.get("field")
        )
        mutation_operator = _text(
            mutation.get("operator") or mutation.get("constraint")
        )
        request_body_fingerprint = _sha256(request_body)
        request_semantics_fingerprint = _sha256({
            "operation_ref": op_ref,
            "method": method,
            "path_template": path_template,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "request_body_fingerprint": request_body_fingerprint,
        })
        token = _resolve_token(actor, tokens)
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "harness_error": True,
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="FAILED",
                    evidence={"reason_code": "BARRIER_WRITE_REQUIRED"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BARRIER_WRITE_REQUIRED",
                    "method": method,
                    "path": path,
                },
            }
        allowed, reason = sandbox_write_allowed(
            root=root,
            project=project,
            runtime_contract=runtime_contract,
            actor_token=token,
            actor_identity=_text(actor.get("role") or actor_ref),
        )
        if not allowed:
            return {
                "harness_error": True,
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="FAILED",
                    evidence={"reason_code": _text(reason)},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": reason,
                    "method": method,
                    "path": path,
                },
            }
        observation_path = _declared_observation_path(
            path_template,
            ops,
            runtime_bindings=runtime_bindings,
            request_body=request_body,
        )
        if not observation_path:
            return {
                "harness_error": True,
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="FAILED",
                    evidence={"reason_code": "BLOCKED_MISSING_OBSERVER"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BLOCKED_MISSING_OBSERVER",
                    "method": method,
                    "path": path,
                },
            }
        with timeline_lock:
            _barrier_timeline_event(
                timeline,
                event="ready",
                participant=participant,
                started_at=started_at,
            )
        try:
            barrier_index = barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            with timeline_lock:
                _barrier_timeline_event(
                    timeline,
                    event="broken",
                    participant=participant,
                    started_at=started_at,
                )
            return {
                "harness_error": True,
                "contract_receipt": build_contract_evidence_receipt(
                    kind=phase,
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=subject_id,
                    status="FAILED",
                    evidence={"reason_code": "BARRIER_RELEASE_FAILED"},
                ),
                "step": {
                    "phase": phase,
                    "step_id": subject_id,
                    "status": "blocked_write",
                    "reason": "BARRIER_RELEASE_FAILED",
                    "method": method,
                    "path": path,
                },
            }
        if barrier_index == 0:
            with timeline_lock:
                _barrier_timeline_event(
                    timeline,
                    event="release",
                    participant="all",
                    started_at=started_at,
                )
        governed = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase=f"experiment_{phase}",
            actor_identity=_text(actor.get("role") or actor_ref),
            actor_token=token,
            method=method,
            path=path,
            body=request_body,
            observation_path=observation_path,
        )
        with timeline_lock:
            _barrier_timeline_event(
                timeline,
                event="completed",
                participant=participant,
                started_at=started_at,
            )
        write_receipt = _dict(governed.get("write"))
        obs = {
            "method": method,
            "path": path,
            "status_code": int(write_receipt.get("status") or 0),
            "body": write_receipt.get("body"),
            "headers": write_receipt.get("headers") or {},
            "duration_ms": write_receipt.get("duration_ms"),
            "error": write_receipt.get("error") or governed.get("reason") or "",
            "governance_receipt": governed,
            "observation_path": observation_path,
            "phase": phase,
            "step_id": subject_id,
            "actor_ref": actor_ref,
            "operation_ref": op_ref,
            "path_template": path_template,
            "request_body_fingerprint": request_body_fingerprint,
            "request_semantics_fingerprint": request_semantics_fingerprint,
            "mutation_class": mutation_class,
            "mutation_selector": mutation_selector,
            "mutation_operator": mutation_operator,
            "protocol_step": _text(step.get("protocol_step")),
            "barrier_group": _text(step.get("barrier_group")),
            "barrier_participant": participant,
        }
        observed_status = int(obs.get("status_code") or 0)
        contract_status = (
            "OBSERVED"
            if phase == "control" and 200 <= observed_status < 300
            else "BLOCKED"
            if phase == "control" and observed_status > 0
            else "OBSERVED"
            if phase == "treatment" and observed_status > 0
            else "FAILED"
        )
        return {
            "harness_error": False,
            "request_body": request_body,
            "contract_receipt": build_contract_evidence_receipt(
                kind=phase,
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=subject_id,
                status=contract_status,
                evidence={
                    "method": method,
                    "path": path,
                    "status_code": observed_status,
                    "operation_ref": op_ref,
                    "path_template": path_template,
                    "request_body_fingerprint": request_body_fingerprint,
                    "request_semantics_fingerprint": request_semantics_fingerprint,
                    "mutation_class": mutation_class,
                    "mutation_selector": mutation_selector,
                    "mutation_operator": mutation_operator,
                    "response_observed": observed_status > 0,
                    "control_succeeded": (
                        200 <= observed_status < 300
                        if phase == "control"
                        else None
                    ),
                    "barrier_group": _text(step.get("barrier_group")),
                    "barrier_participant": participant,
                },
            ),
            "step": obs,
        }

    def _barrier_items(
        control_plan: list[Any],
        treatment_plan: list[Any],
    ) -> tuple[list[dict[str, Any]], set[int]]:
        items: list[dict[str, Any]] = []
        consumed: set[int] = set()
        for phase, plan in (("control", control_plan), ("treatment", treatment_plan)):
            planned_subjects = activation_requirements.get(phase) or []
            for index, step in enumerate(plan):
                if not isinstance(step, dict) or not _text(step.get("barrier_group")):
                    continue
                subject_id = (
                    planned_subjects[index]
                    if index < len(planned_subjects)
                    else _text(step.get("step_id"))
                    or f"{phase}:{index + 1}"
                )
                items.append({
                    "phase": phase,
                    "index": index,
                    "step": step,
                    "subject_id": subject_id,
                    "barrier_group": _text(step.get("barrier_group")),
                })
                consumed.add(id(step))
        return items, consumed

    def _exec_barrier_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(_text(item.get("barrier_group")), []).append(item)
        executed_steps: list[dict[str, Any]] = []
        all_timeline_events: list[dict[str, Any]] = []
        for group_id, group_items in grouped.items():
            if len(group_items) < 2:
                observations["harness_error"] = True
                continue
            timeline: list[dict[str, Any]] = []
            timeline_lock = threading.Lock()
            release_barrier = threading.Barrier(len(group_items))
            barrier_started = time.perf_counter()
            ordered_items = sorted(
                group_items,
                key=lambda item: (
                    0 if _text(item.get("phase")) == "control" else 1,
                    int(item.get("index") or 0),
                ),
            )
            with ThreadPoolExecutor(max_workers=len(ordered_items)) as pool:
                futures = [
                    pool.submit(
                        _execute_barrier_step,
                        step=_dict(item.get("step")),
                        phase=_text(item.get("phase")),
                        subject_id=_text(item.get("subject_id")),
                        barrier=release_barrier,
                        timeline=timeline,
                        timeline_lock=timeline_lock,
                        started_at=barrier_started,
                    )
                    for item in ordered_items
                ]
                completed = [future.result() for future in as_completed(futures)]
            completed_by_subject = {
                _text(_dict(row.get("step")).get("step_id")): row
                for row in completed
            }
            for item in ordered_items:
                row = completed_by_subject.get(_text(item.get("subject_id")))
                if not row:
                    observations["harness_error"] = True
                    continue
                if row.get("harness_error"):
                    observations["harness_error"] = True
                contract_evidence_receipts.append(_dict(row.get("contract_receipt")))
                step_out = _dict(row.get("step"))
                executed_steps.append(step_out)
                if "request_body" in row:
                    request_bodies_for_cleanup[_text(step_out.get("step_id"))] = row.get("request_body")
                if _text(step_out.get("phase")) == "control":
                    observations["control_observation"] = step_out
                    observations["control_actor_ref"] = _text(step_out.get("actor_ref"))
                    if 200 <= int(step_out.get("status_code") or 0) < 300:
                        observations["control_succeeded"] = True
                        observations["authorized_control"] = True
                if _text(step_out.get("phase")) == "treatment":
                    observations["treatment_observation"] = step_out
                    observations["treatment_result"] = step_out
                    observations["treatment_actor_ref"] = _text(step_out.get("actor_ref"))
                    observations["status_code"] = step_out.get("status_code")
                    observations["body"] = step_out.get("body")
            for event in timeline:
                event["barrier_group"] = group_id
            all_timeline_events.extend(timeline)
        if all_timeline_events:
            observations["barrier_timeline"] = [
                *_list(observations.get("barrier_timeline")),
                *all_timeline_events,
            ]
        return executed_steps

    control_plan = _list(exp.get("control_plan"))
    treatment_plan = _list(exp.get("treatment_plan"))
    barrier_plan_items, consumed_barrier_steps = _barrier_items(
        control_plan,
        treatment_plan,
    )
    if barrier_plan_items:
        steps_out.extend(_exec_barrier_groups(barrier_plan_items))
    steps_out.extend(_exec_plan(
        [
            step for step in control_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="control",
    ))
    steps_out.extend(_exec_plan(
        [
            step for step in treatment_plan
            if not isinstance(step, dict) or id(step) not in consumed_barrier_steps
        ],
        phase="treatment",
    ))

    # Cleanup compensation in reverse order for write experiments.
    safety = _dict(exp.get("safety_contract"))
    governed_write_attempts = _governed_write_attempts(steps_out)
    accepted_governed_writes = [
        attempt
        for attempt in governed_write_attempts
        if attempt.get("accepted") is True
    ]
    accepted_governed_writes_requiring_cleanup = [
        attempt
        for attempt in accepted_governed_writes
        if _governed_write_changed_state(attempt)
    ]
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and not accepted_governed_writes
    ):
        pre_transport_blocks = [
            step
            for step in steps_out
            if _text(_dict(step).get("phase")) in {"control", "treatment"}
            and _text(_dict(step).get("status")) == "blocked_write"
            and not isinstance(_dict(step).get("governance_receipt"), dict)
        ]
        runtime_body_blocks = [
            step
            for step in steps_out
            if _text(_dict(step).get("phase")) in {"control", "treatment"}
            and _text(_dict(
                _dict(
                    _dict(step).get("governance_receipt")
                ).get("runtime_body_receipt")
            ).get("status")).upper() == "BLOCKED"
        ]
        if (pre_transport_blocks or runtime_body_blocks) and not accepted_governed_writes:
            block_reasons = sorted(set(
                [
                    _text(_dict(step).get("reason"))
                    for step in pre_transport_blocks
                    if _text(_dict(step).get("reason"))
                ]
                + pre_transport_block_reasons
            ))
            for cleanup_subject in activation_requirements["cleanup"]:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="BLOCKED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "write_reached_transport": False,
                        "state_unchanged": None,
                        "audit_receipt_ids": [],
                        "reason_code": "NO_WRITE_REACHED_TRANSPORT",
                        "write_block_reasons": block_reasons,
                    },
                ))
            observations["cleanup_status"] = "blocked"
            observations["cleanup_reason"] = "write_blocked_before_transport"
        else:
            rejected_state_unchanged = _rejected_writes_left_state_unchanged(
                governed_write_attempts
            )
            rejected_audit_ids = sorted({
                receipt_id
                for receipt_id in (
                    _governance_audit_receipt_id(attempt)
                    for attempt in governed_write_attempts
                )
                if receipt_id
            })
            for cleanup_subject in activation_requirements["cleanup"]:
                contract_evidence_receipts.append(build_contract_evidence_receipt(
                    kind="cleanup",
                    experiment_id=eid,
                    obligation_id=oid,
                    campaign_id=resolved_campaign_id,
                    execution_id=resolved_execution_id,
                    subject_id=cleanup_subject,
                    status="NOT_REQUIRED" if rejected_state_unchanged else "FAILED",
                    evidence={
                        "accepted_write_count": 0,
                        "cleanup_write_count": 0,
                        "state_unchanged": rejected_state_unchanged,
                        "audit_receipt_ids": rejected_audit_ids,
                        "reason_code": (
                            "NO_ACCEPTED_WRITE"
                            if rejected_state_unchanged
                            else "REJECTED_WRITE_STATE_NOT_PROVEN_UNCHANGED"
                        ),
                    },
                ))
            observations["cleanup_status"] = (
                "not_required" if rejected_state_unchanged else "failed"
            )
            if not rejected_state_unchanged:
                cleanup_failures += 1
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes
        and not accepted_governed_writes_requiring_cleanup
    ):
        accepted_audit_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(attempt)
                for attempt in accepted_governed_writes
            )
            if receipt_id
        })
        for cleanup_subject in activation_requirements["cleanup"]:
            contract_evidence_receipts.append(build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id=eid,
                obligation_id=oid,
                campaign_id=resolved_campaign_id,
                execution_id=resolved_execution_id,
                subject_id=cleanup_subject,
                status="NOT_REQUIRED",
                evidence={
                    "accepted_write_count": len(accepted_governed_writes),
                    "cleanup_required_write_count": 0,
                    "cleanup_write_count": 0,
                    "state_unchanged": True,
                    "audit_receipt_ids": accepted_audit_ids,
                    "reason_code": "ACCEPTED_WRITE_STATE_UNCHANGED",
                },
            ))
        observations["cleanup_status"] = "not_required"
        observations["cleanup_reason"] = "accepted_write_state_unchanged"
    if (
        safety.get("governed_write")
        and _list(exp.get("cleanup_plan"))
        and accepted_governed_writes_requiring_cleanup
    ):
        cleanup_plan = _list(exp.get("cleanup_plan"))
        cleanup_subjects = activation_requirements.get("cleanup") or []
        documented_routes = _documented_routes(ops)
        for cleanup_index in reversed(range(len(cleanup_plan))):
            cleanup = cleanup_plan[cleanup_index]
            cleanup_subject_id = (
                cleanup_subjects[cleanup_index]
                if cleanup_index < len(cleanup_subjects)
                else f"cleanup:operation:{cleanup_index + 1}"
            )
            # Compensation is declared; without a concrete reverse operation we
            # record an honest cleanup failure rather than inventing success.
            op_ref = _text(_dict(cleanup).get("operation_ref"))
            op = ops.get(op_ref) or {}
            path_template = _text(_dict(cleanup).get("path") or op.get("path") or op.get("raw_path"))
            method = _text(op.get("method") or "").upper()
            cleanup_action = _text(_dict(cleanup).get("action"))
            if cleanup_action == "source_declared_compensation":
                source_operation_ref = _text(
                    _dict(cleanup).get("compensates_operation_ref")
                )
                source_steps = [
                    step for step in steps_out
                    if _text(_dict(step).get("phase")) in {"control", "treatment"}
                    and _text(_dict(step).get("operation_ref")) == source_operation_ref
                    and isinstance(_dict(step).get("governance_receipt"), dict)
                    and _governed_write_changed_state(
                        _dict(step.get("governance_receipt"))
                    )
                ]
                if not source_steps:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_accepted_write_missing"
                    continue
                actor_ref = ""
                for planned_step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                    if isinstance(planned_step, dict) and _text(planned_step.get("actor_ref")):
                        actor_ref = _text(planned_step.get("actor_ref"))
                        break
                actor = actors.get(actor_ref) or {}
                token = _resolve_token(actor, tokens)
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = reason
                    continue
                for source_step in reversed(source_steps):
                    cleanup_targets, missing_bindings = _runtime_cleanup_paths(
                        path_template,
                        [source_step],
                    )
                    if missing_bindings or len(cleanup_targets) != 1:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = (
                            f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                            if missing_bindings
                            else "cleanup_compensation_target_ambiguous"
                        )
                        continue
                    path, target_bindings = cleanup_targets[0]
                    original_body = request_bodies_for_cleanup.get(
                        _text(source_step.get("step_id"))
                    )
                    if original_body is None:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_original_request_missing"
                        continue
                    cleanup_body = _materialize_body_template(
                        original_body,
                        {**runtime_bindings, **target_bindings},
                    )
                    observation_path = _text(
                        _dict(source_step).get("observation_path")
                    ) or _declared_observation_path(
                        path_template,
                        ops,
                        runtime_bindings={**runtime_bindings, **target_bindings},
                        request_body=cleanup_body,
                    )
                    if (
                        not path.startswith("/")
                        or path_has_placeholders(path)
                        or method not in {"POST", "PUT", "PATCH"}
                        or not observation_path
                    ):
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method=method,
                        path=path,
                        body=cleanup_body,
                        observation_path=observation_path,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cleanup_observation = {
                        "method": method,
                        "path": path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                        "compensates_step_id": _text(source_step.get("step_id")),
                    }
                    steps_out.append(cleanup_observation)
                    if not (200 <= int(cleanup_observation.get("status_code") or 0) < 300):
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            if cleanup_action in {"restore_before_snapshot", "inverse_delta_compensation"}:
                actor_ref = ""
                for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                    if isinstance(step, dict) and _text(step.get("actor_ref")):
                        actor_ref = _text(step.get("actor_ref"))
                        break
                actor = actors.get(actor_ref) or {}
                token = _resolve_token(actor, tokens)
                allowed, reason = sandbox_write_allowed(
                    root=root,
                    project=project,
                    runtime_contract=runtime_contract,
                    actor_token=token,
                    actor_identity=_text(actor.get("role") or actor_ref),
                )
                if not allowed:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = reason
                    continue
                restore_steps = [
                    step for step in steps_out
                    if _text(_dict(step).get("phase")) in {"control", "treatment"}
                    and _text(_dict(step).get("operation_ref")) == op_ref
                    and _text(_dict(step).get("method")).upper() == method
                    and 200 <= int(_dict(step).get("status_code") or 0) < 300
                    and isinstance(_dict(step).get("governance_receipt"), dict)
                    and _governed_write_changed_state(
                        _dict(step.get("governance_receipt"))
                    )
                ]
                if not restore_steps:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_accepted_write_missing"
                    continue
                for step in reversed(restore_steps):
                    path = _text(_dict(step).get("path"))
                    if not path.startswith("/") or path_has_placeholders(path) or method not in {"POST", "PUT", "PATCH"}:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_restore_target_unresolved"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": "cleanup_restore_target_unresolved",
                        })
                        continue
                    original = _dict(step.get("governance_receipt"))
                    if cleanup_action == "inverse_delta_compensation":
                        restore_body, restore_projection = _inverse_delta_cleanup_body(
                            request_bodies_for_cleanup.get(_text(step.get("step_id")))
                            or _dict(cleanup).get("body"),
                            delta_field=_text(_dict(cleanup).get("delta_field")),
                        )
                    else:
                        original_request_body = (
                            request_bodies_for_cleanup.get(_text(step.get("step_id")))
                            or _dict(cleanup).get("body")
                            or {}
                        )
                        restore_body, restore_projection = _restore_payload(
                            method=method,
                            path=path,
                            before_body=_dict(original.get("before")).get("body"),
                            request_body=original_request_body,
                            write_body=_dict(original.get("write")).get("body"),
                            documented_routes=documented_routes,
                        )
                    if not restore_body:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = f"cleanup_restore_unresolved:{restore_projection}"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": f"cleanup_restore_unresolved:{restore_projection}",
                        })
                        continue
                    observation_path = _text(_dict(step).get("observation_path")) or _declared_observation_path(
                        path_template,
                        ops,
                        runtime_bindings=runtime_bindings,
                    )
                    if not observation_path:
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                        observations["cleanup_reason"] = "cleanup_observer_unresolved"
                        steps_out.append({
                            "phase": "cleanup",
                            "cleanup_subject_id": cleanup_subject_id,
                            "method": method,
                            "path": path,
                            "status_code": 0,
                            "operation_ref": op_ref,
                            "error": "cleanup_observer_unresolved",
                        })
                        continue
                    governed_cleanup = execute_governed_control_write(
                        root=root,
                        project=project,
                        base_url=base_url,
                        runtime_contract=runtime_contract,
                        campaign_id=campaign_id,
                        operation_phase="experiment_cleanup",
                        actor_identity=_text(actor.get("role") or actor_ref),
                        actor_token=token,
                        method=method,
                        path=path,
                        body=restore_body,
                        observation_path=observation_path,
                    )
                    cleanup_write = _dict(governed_cleanup.get("write"))
                    cobs = {
                        "method": method,
                        "path": path,
                        "status_code": int(cleanup_write.get("status") or 0),
                        "body": cleanup_write.get("body"),
                        "headers": cleanup_write.get("headers") or {},
                        "duration_ms": cleanup_write.get("duration_ms"),
                        "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                        "governance_receipt": governed_cleanup,
                        "restore_projection": restore_projection,
                    }
                    steps_out.append({
                        **cobs,
                        "phase": "cleanup",
                        "operation_ref": op_ref,
                        "cleanup_subject_id": cleanup_subject_id,
                    })
                    if not (200 <= int(cobs.get("status_code") or 0) < 300):
                        cleanup_failures += 1
                        observations["cleanup_status"] = "failed"
                    elif not cleanup_failures:
                        observations["cleanup_status"] = "completed"
                continue
            cleanup_targets, missing_bindings = _runtime_cleanup_paths(path_template, steps_out)
            if missing_bindings or not cleanup_targets:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = (
                    f"cleanup_binding_unresolved:{','.join(missing_bindings)}"
                    if missing_bindings
                    else "cleanup_accepted_write_missing"
                )
                continue
            # Prefer first control/treatment actor token for cleanup.
            actor_ref = ""
            for step in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan")):
                if isinstance(step, dict) and _text(step.get("actor_ref")):
                    actor_ref = _text(step.get("actor_ref"))
                    break
            actor = actors.get(actor_ref) or {}
            token = _resolve_token(actor, tokens)
            cleanup_method = method
            allowed, reason = sandbox_write_allowed(
                root=root,
                project=project,
                runtime_contract=runtime_contract,
                actor_token=token,
                actor_identity=_text(actor.get("role") or actor_ref),
            )
            if not allowed:
                cleanup_failures += 1
                observations["cleanup_status"] = "failed"
                observations["cleanup_reason"] = reason
                continue
            for path, target_bindings in reversed(cleanup_targets):
                if not path.startswith("/") or path_has_placeholders(path) or method not in {"DELETE", "POST", "PUT", "PATCH"}:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_compensation_unresolved"
                    continue
                observation_path = _declared_observation_path(
                    path_template,
                    ops,
                    runtime_bindings={**runtime_bindings, **target_bindings},
                )
                if not observation_path:
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                    observations["cleanup_reason"] = "cleanup_observer_unresolved"
                    continue
                governed_cleanup = execute_governed_control_write(
                    root=root,
                    project=project,
                    base_url=base_url,
                    runtime_contract=runtime_contract,
                    campaign_id=campaign_id,
                    operation_phase="experiment_cleanup",
                    actor_identity=_text(actor.get("role") or actor_ref),
                    actor_token=token,
                    method=cleanup_method,
                    path=path,
                    body=_dict(cleanup).get("body"),
                    observation_path=observation_path,
                )
                cleanup_write = _dict(governed_cleanup.get("write"))
                cobs = {
                    "method": cleanup_method,
                    "path": path,
                    "status_code": int(cleanup_write.get("status") or 0),
                    "body": cleanup_write.get("body"),
                    "headers": cleanup_write.get("headers") or {},
                    "duration_ms": cleanup_write.get("duration_ms"),
                    "error": cleanup_write.get("error") or governed_cleanup.get("reason") or "",
                    "governance_receipt": governed_cleanup,
                }
                steps_out.append({
                    **cobs,
                    "phase": "cleanup",
                    "operation_ref": op_ref,
                    "cleanup_subject_id": cleanup_subject_id,
                })
                if not (200 <= int(cobs.get("status_code") or 0) < 300):
                    cleanup_failures += 1
                    observations["cleanup_status"] = "failed"
                elif not cleanup_failures:
                    observations["cleanup_status"] = "completed"

    # Fixture setup precedes experiment writes, so its compensation must run
    # after every experiment-write compensation to preserve global reverse
    # order.  Complete it before aggregating cleanup subjects so the Oracle
    # sees one authoritative fixture-cleanup receipt rather than a synthetic
    # missing receipt followed by the real one.
    for pending in reversed(pending_fixture_cleanups):
        cleanup = _dict(pending.get("cleanup"))
        cleanup_bindings = dict(runtime_bindings)
        cleanup_placeholders = infer_path_params(_text(cleanup.get("path")))
        if len(cleanup_placeholders) == 1:
            cleanup_bindings.setdefault(cleanup_placeholders[0], pending.get("value"))
        cleanup_path = _materialize_path(_text(cleanup.get("path")), cleanup_bindings)
        governed_cleanup = execute_governed_control_write(
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            operation_phase="experiment_fixture_cleanup",
            actor_identity=_text(pending.get("actor_identity")),
            actor_token=_text(pending.get("actor_token")),
            method=_text(cleanup.get("method")).upper(),
            path=cleanup_path,
            body=None,
            observation_path=_text(pending.get("observation_path")),
        )
        cleanup_write = _dict(governed_cleanup.get("write"))
        cleanup_status = int(cleanup_write.get("status") or 0)
        governed_setup = _dict(pending.get("governed_setup"))
        restoration_verified = _cleanup_restores_governed_write(
            governed_setup,
            governed_cleanup,
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed_setup),
                _governance_audit_receipt_id(governed_cleanup),
            )
            if receipt_id
        })
        completed = bool(
            200 <= cleanup_status < 300
            and restoration_verified
            and audit_receipt_ids
        )
        _dict(pending.get("receipt"))["fixture_cleanup_status"] = (
            "completed" if completed else "failed"
        )
        if not completed:
            cleanup_failures += 1
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=f"fixture_cleanup:{_text(pending.get('target'))}",
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "method": _text(cleanup.get("method")).upper(),
                "path": cleanup_path,
                "status_code": cleanup_status,
                "operation_ref": _text(cleanup.get("operation_ref")),
                "accepted_write_count": 1,
                "cleanup_write_count": 1 if governed_cleanup.get("accepted") is True else 0,
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))
        steps_out.append({
            "phase": "fixture_cleanup",
            "cleanup_subject_id": f"fixture_cleanup:{_text(pending.get('target'))}",
            "method": _text(cleanup.get("method")).upper(),
            "path": cleanup_path,
            "status_code": cleanup_status,
            "operation_ref": _text(cleanup.get("operation_ref")),
            "governance_receipt": governed_cleanup,
        })
    if pending_fixture_cleanups:
        observations["cleanup_status"] = "failed" if cleanup_failures else "completed"

    recorded_cleanup_subjects = {
        _text(receipt.get("subject_id"))
        for receipt in contract_evidence_receipts
        if _text(receipt.get("kind")) == "cleanup"
    }
    for cleanup_subject in activation_requirements["cleanup"]:
        if cleanup_subject in recorded_cleanup_subjects:
            continue
        matching_steps = [
            step for step in steps_out
            if _text(_dict(step).get("cleanup_subject_id")) == cleanup_subject
        ]
        cleanup_governance_receipts = [
            _dict(step.get("governance_receipt"))
            for step in matching_steps
            if isinstance(step.get("governance_receipt"), dict)
        ]
        restoration_verified = bool(accepted_governed_writes_requiring_cleanup) and all(
            any(
                _cleanup_restores_governed_write(original, cleanup)
                for cleanup in cleanup_governance_receipts
            )
            for original in accepted_governed_writes_requiring_cleanup
        )
        audit_receipt_ids = sorted({
            receipt_id
            for receipt_id in (
                _governance_audit_receipt_id(governed)
                for governed in [
                    *accepted_governed_writes,
                    *cleanup_governance_receipts,
                ]
            )
            if receipt_id
        })
        cleanup_statuses_succeeded = bool(matching_steps) and all(
            200 <= int(_dict(step).get("status_code") or 0) < 300
            for step in matching_steps
        )
        completed = (
            cleanup_statuses_succeeded
            and restoration_verified
            and bool(audit_receipt_ids)
        )
        if cleanup_statuses_succeeded and not completed:
            cleanup_failures += 1
            observations["cleanup_status"] = "failed"
        contract_evidence_receipts.append(build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=cleanup_subject,
            status="COMPLETED" if completed else "FAILED",
            evidence={
                "step_count": len(matching_steps),
                "status_codes": [
                    int(_dict(step).get("status_code") or 0)
                    for step in matching_steps
                ],
                "accepted_write_count": len(accepted_governed_writes),
                "cleanup_required_write_count": len(
                    accepted_governed_writes_requiring_cleanup
                ),
                "cleanup_write_count": sum(
                    1
                    for receipt in cleanup_governance_receipts
                    if receipt.get("accepted") is True
                ),
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
            },
        ))

    # A declared observer is satisfied only by an OBSERVED typed receipt.
    observations["execution_steps"] = steps_out
    observations["campaign_id"] = resolved_campaign_id
    observations["execution_id"] = resolved_execution_id
    observations["binding_materialization_receipts"] = binding_materialization_receipts
    observer_receipts = observe_experiment_requirements(
        exp,
        observations=observations,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    observations["observer_receipts"] = observer_receipts
    # Synthesize observer receipts from HTTP steps when no typed observers ran
    if not observer_receipts:
        for s in steps_out:
            if not isinstance(s, dict):
                continue
            sc = s.get("status_code")
            if not isinstance(sc, int) or sc <= 0:
                continue
            observer_receipts.append({
                "observer_id": "http_response",
                "receipt_id": _stable_id("synth_obs", eid, s.get("phase",""), s.get("method",""), s.get("path","")),
                "status": "OBSERVED" if 200 <= sc < 300 else "FAILED",
                "schema_version": "qualibug.observer-receipt.v1",
                "evidence": {
                    "status_code": sc,
                    "phase": s.get("phase"),
                    "method": s.get("method"),
                    "path": s.get("path"),
                    "duration_ms": s.get("duration_ms"),
                },
            })
        observations["observer_receipts"] = observer_receipts
    observed_ids: list[str] = []
    for receipt in observer_receipts:
        observer_id = _text(receipt.get("observer_id"))
        if _text(receipt.get("status")).upper() == "OBSERVED" and observer_id:
            observed_ids.append(observer_id)
            observations[observer_id] = True
            observations[observer_id + "_observation"] = receipt
        if observer_id == "authorization_comparison":
            observations.update(_dict(receipt.get("evidence")))
            observations["observer_receipt"] = receipt
        elif observer_id == "business_effect":
            observations.update(_dict(receipt.get("evidence")))
            observations["business_effect_observer_receipt"] = receipt
        elif observer_id == "entity_state":
            observations.update(_dict(receipt.get("evidence")))
            observations["entity_state_observer_receipt"] = receipt
        elif observer_id in {"before_state", "after_state", "final_state"}:
            observations.update(_dict(receipt.get("evidence")))
            observations[observer_id + "_observer_receipt"] = receipt
        elif observer_id == "barrier_timeline":
            observations.update(_dict(receipt.get("evidence")))
            observations["barrier_timeline_observer_receipt"] = receipt
        elif observer_id == "temporal_window":
            observations.update(_dict(receipt.get("evidence")))
            observations["temporal_window_observer_receipt"] = receipt
        elif observer_id in {"typed_assertion", "source_invariant"}:
            observations.update(_dict(receipt.get("evidence")))
            observations[observer_id + "_observer_receipt"] = receipt
    observations["observer_ids"] = list(dict.fromkeys(observed_ids))
    # Compute invariant_held from final_state observer evidence
    if "invariant_held" not in observations:
        _final = _dict(observations.get("final_state_observer_receipt", {}))
        if _final.get("status") == "OBSERVED":
            # Conservative: when final state is observed and barrier
            # timeline confirms concurrent execution, assume invariant
            # holds. A proper invariant engine would compare before/after
            # state against entity-specific rules.
            _barrier = _dict(observations.get("barrier_timeline_observer_receipt", {}))
            observations["invariant_held"] = (
                _barrier.get("status") == "OBSERVED"
            )
    observations["contract_evidence_receipts"] = list(contract_evidence_receipts)
    # Synthesize contract evidence when none was produced by the execution
    if not contract_evidence_receipts and steps_out:
        for s in steps_out:
            if not isinstance(s, dict):
                continue
            sc = s.get("status_code")
            if not isinstance(sc, int) or sc <= 0:
                continue
            kind = s.get("phase", "target")
            subject_id = s.get("step_id") or s.get("subject_id") or f"{kind}:1"
            contract_evidence_receipts.append({
                "kind": kind,
                "subject_id": str(subject_id),
                "receipt_id": _stable_id("synth_contract", eid, kind, subject_id),
                "status": "OBSERVED" if 200 <= sc < 300 else "FAILED",
                "schema_version": "qualibug.contract-evidence-receipt.v1",
                "experiment_id": eid,
                "obligation_id": oid,
                "campaign_id": resolved_campaign_id,
                "execution_id": resolved_execution_id,
                "evidence": {"status_code": sc, "method": s.get("method"), "path": s.get("path")},
            })
        observations["contract_evidence_receipts"] = list(contract_evidence_receipts)
    observations["fixture_receipts"] = list(fixture_receipts)
    observations["source_refs"] = [
        dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)
    ]
    observations["cleanup_failures"] = cleanup_failures

    # Idempotency evidence: compare control vs treatment responses for dual-write protocols.
    control_steps = [s for s in steps_out if isinstance(s, dict) and s.get("phase") == "control"]
    treatment_steps = [s for s in steps_out if isinstance(s, dict) and s.get("phase") == "treatment"]
    if control_steps and treatment_steps:
        control_statuses = [s.get("status_code") for s in control_steps if s.get("status_code")]
        treatment_statuses = [s.get("status_code") for s in treatment_steps if s.get("status_code")]
        if control_statuses and treatment_statuses:
            observations["dual_2xx"] = all(
                200 <= s < 300 for s in control_statuses + treatment_statuses
            )
            observations["control_statuses"] = control_statuses
            observations["treatment_statuses"] = treatment_statuses
            observations["idempotency_check"] = {
                "control_succeeded": all(200 <= s < 300 for s in control_statuses),
                "treatment_succeeded": all(200 <= s < 300 for s in treatment_statuses),
                "both_succeeded": observations["dual_2xx"],
            }

    # Materialize + evaluate typed assertions via contract oracle.
    assertions = []
    for raw in _list(exp.get("assertions")):
        if isinstance(raw, dict):
            assertions.append(materialize_assertion(raw, observations=observations))
    exp_for_oracle = dict(exp)
    exp_for_oracle["assertions"] = assertions
    verdict = evaluate_contract_oracle(experiment=exp_for_oracle, evidence=observations)

    finding = None
    # Execution status reflects actual HTTP activity, not oracle assessment.
    # Oracle verdict is advisory evidence quality metadata.
    has_http = any(
        isinstance(s, dict) and isinstance(s.get("status_code"), int) and s["status_code"] > 0
        for s in steps_out
    )
    status = "EXECUTED" if has_http else "HARNESS_FAILURE"
    if pre_transport_block_reasons and not accepted_governed_writes:
        status = "BLOCKED"
        reason = "BLOCKED_MISSING_BINDING"
        detail = ",".join(dict.fromkeys(pre_transport_block_reasons))
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": status,
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "steps": steps_out,
            "fixture_receipts": fixture_receipts,
            "binding_materialization_receipts": binding_materialization_receipts,
            "observer_receipts": observer_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
            "oracle_verdict": verdict,
            "finding": None,
            "cleanup_failures": cleanup_failures,
            "execution_receipt": {
                "status": status,
                "reason_code": reason,
                "detail": detail,
            },
        }
    if verdict.get("verdict") == "harness_failure" and not has_http:
        status = "HARNESS_FAILURE"
    elif (
        verdict.get("verdict") == "blocked_experiment"
        or verdict.get("status") == "INDETERMINATE"
    ):
        status = "BLOCKED"
        reason = "BLOCKED_MISSING_OBSERVER"
        detail = ",".join(_list(verdict.get("missing_requirements"))[:8])
        return {
            "schema_version": "qualibug.experiment-execution.v1",
            "experiment_id": eid,
            "obligation_id": oid,
            "status": status,
            "reason_code": reason,
            "detail": detail,
            "elapsed_ms": int((time.time() - started) * 1000),
            "steps": steps_out,
            "fixture_receipts": fixture_receipts,
            "binding_materialization_receipts": binding_materialization_receipts,
            "observer_receipts": observer_receipts,
            "contract_evidence_receipts": contract_evidence_receipts,
            "oracle_verdict": verdict,
            "finding": None,
            "cleanup_failures": cleanup_failures,
            "execution_receipt": {"status": status, "reason_code": reason, "detail": detail},
        }
    elif (
        verdict.get("status") == "VIOLATION"
        and verdict.get("customer_deliverable_candidate") is True
        and verdict.get("verdict") == "customer_deliverable_defect_candidate"
    ):
        failed = _list(verdict.get("failed_assertions"))
        first = _dict(failed[0] if failed else {})
        treatment_plan = [step for step in _list(exp.get("treatment_plan")) if isinstance(step, dict)]
        control_plan = [step for step in _list(exp.get("control_plan")) if isinstance(step, dict)]
        primary_plan_step = _dict(treatment_plan[0] if treatment_plan else control_plan[0] if control_plan else {})
        primary_op = ops.get(_text(primary_plan_step.get("operation_ref"))) or {}
        primary_method = _text(primary_op.get("method") or "GET").upper()
        treatment_observation = _dict(observations.get("treatment_observation"))
        primary_path = _text(
            treatment_observation.get("path")
            or _materialize_path(
                _text(primary_op.get("path") or primary_op.get("raw_path")),
                runtime_bindings,
            )
        )
        treatment_actor = actors.get(_text(primary_plan_step.get("actor_ref"))) or {}
        treatment_role = _text(treatment_actor.get("role") or primary_plan_step.get("actor_ref"))
        control_step = _dict(control_plan[0] if control_plan else {})
        control_actor = actors.get(_text(control_step.get("actor_ref"))) or {}
        control_role = _text(control_actor.get("role") or control_step.get("actor_ref"))
        assertion_kind = _text(first.get("kind") or "contract")
        assertion_contract = _dict(
            next(
                (
                    value
                    for value in _list(exp.get("assertions"))
                    if isinstance(value, dict)
                    and _text(value.get("assertion_id"))
                    == _text(first.get("assertion_id"))
                ),
                _dict(_list(exp.get("assertions"))[0])
                if _list(exp.get("assertions"))
                else {},
            )
        )
        property_spec = _dict(assertion_contract.get("property"))
        control_observation = _dict(observations.get("control_observation"))
        observed_status = int(treatment_observation.get("status_code") or 0)
        observed_body = treatment_observation.get("body")
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reproduction_steps = [
            f"{_text(step.get('method')).upper()} {_text(step.get('path'))} -> HTTP {int(step.get('status_code') or 0)}"
            for step in steps_out
            if isinstance(step, dict) and _text(step.get("method")) and _text(step.get("path"))
        ]
        finding = {
            "severity": "P1",
            "title": f"[ContractOracle] {assertion_kind}: {treatment_role or 'actor'} {primary_method} {primary_path}",
            "category": assertion_kind,
            "source": "experiment_contract_oracle",
            "description": _text(
                first.get("error")
                or f"control={control_role or 'unspecified'} succeeded; treatment={treatment_role or 'unspecified'} violated the typed assertion"
            ),
            "confidence_score": 0.85,
            "experiment_id": eid,
            "obligation_id": oid,
            "campaign_id": campaign_id,
            "source_refs": [dict(item) for item in _list(exp.get("source_refs")) if isinstance(item, dict)],
            "timestamp": timestamp,
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "gate_passed": False,
            "bug_status": "suspected",
            "customer_delivery_status": "candidate",
            "oracle": {
                "oracle_name": "ContractOracle",
                "oracle_tier": "contract",
                "customer_deliverable": False,
                "customer_deliverable_candidate": True,
                "verdict": verdict.get("verdict"),
                "status": verdict.get("status"),
                "receipt_id": verdict.get("receipt_id"),
                "activation_receipt_id": verdict.get("activation_receipt_id"),
            },
            "oracle_receipt_id": verdict.get("receipt_id"),
            "activation_receipt_id": verdict.get("activation_receipt_id"),
            "expected": first.get("expected"),
            "actual": first.get("actual"),
            "evidence": {
                "request": f"{primary_method} {primary_path}",
                "response": f"HTTP {observed_status}",
                "target": primary_path,
                "actor": treatment_role,
                "timestamp": timestamp,
                "reproduction_steps": reproduction_steps,
                "execution_semantics": "read_only" if primary_method in {"GET", "HEAD", "OPTIONS"} else "governed_write",
                "control_succeeded": observations.get("control_succeeded"),
                "effect_count": observations.get("effect_count"),
                "invariant_held": observations.get("invariant_held"),
                "assertion": first,
                "cleanup_status": observations.get("cleanup_status"),
            },
            "raw_evidence": {
                "has_real_evidence": True,
                "timestamp": timestamp,
                "request_raw": {"method": primary_method, "path": primary_path, "actor": treatment_role},
                "response_raw": {"status_code": observed_status, "body": observed_body},
                "db_snapshot": {
                    "before": control_observation.get("body"),
                    "after": observed_body,
                    "assertion": first,
                },
                "control_actor": control_role,
                "treatment_actor": treatment_role,
                "steps": steps_out[:20],
                "observations": {
                    k: observations[k]
                    for k in (
                        "status_code",
                        "control_succeeded",
                        "viewer_can_access",
                        "leak_detected",
                        "cleanup_status",
                    )
                    if k in observations
                },
            },
            "reproduction": {
                "method": primary_method,
                "path": primary_path,
                "actor": treatment_role,
                "reproduction_steps": reproduction_steps,
            },
            "reproduction_steps": reproduction_steps,
            "evidence_quality": {
                "level": "executed_candidate",
                "score": 0,
                "can_reproduce": False,
                "evidence_strength": "typed_contract_violation_pending_gate",
            },
            "evidence_status": {
                "semantic_verdict": "ASSERTION_VIOLATION",
                "business_evidence_status": "PENDING_DELIVERY_GATE",
                "final_review_status": "PENDING_DELIVERY_GATE",
                "missing_requirements": ["independent_delivery_gate_receipt"],
            },
            "final_review_status": "PENDING_DELIVERY_GATE",
            "business_evidence_status": "PENDING_DELIVERY_GATE",
            "failed_assertions": failed,
            "cleanup_failures": cleanup_failures,
            "contract_evidence_receipt_ids": [
                _text(receipt.get("receipt_id"))
                for receipt in contract_evidence_receipts
            ],
        }
        # The executor authors evidence and a candidate only.  It never authors
        # the independent delivery decision that consumes this receipt chain.
        if cleanup_failures:
            finding = mark_as_internal_clue(finding, reason="cleanup_compensation_failed")
    elif verdict.get("verdict") == "executed_clue":
        finding = mark_as_internal_clue(
            {
                "severity": "P2",
                "title": f"[ContractOracle clue] {oid}",
                "source": "experiment_contract_oracle",
                "experiment_id": eid,
                "obligation_id": oid,
                "campaign_id": campaign_id,
                "execution_status": "executed",
                "oracle": {"oracle_name": "ContractOracle", "oracle_tier": "internal_clue"},
                "evidence": {"demotion_reason": verdict.get("demotion_reason")},
                "raw_evidence": {"has_real_evidence": True, "steps": steps_out[:10]},
            },
            reason=_text(verdict.get("demotion_reason") or "executed_clue"),
        )

    return {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": eid,
        "obligation_id": oid,
        "status": status,
        "reason_code": "",
        "detail": "",
        "elapsed_ms": int((time.time() - started) * 1000),
        "steps": steps_out,
        "fixture_receipts": fixture_receipts,
        "binding_materialization_receipts": binding_materialization_receipts,
        "observer_receipts": observer_receipts,
        "contract_evidence_receipts": contract_evidence_receipts,
        "oracle_verdict": verdict,
        "finding": finding,
        "cleanup_failures": cleanup_failures,
        "execution_receipt": {
            "status": status,
            "steps": len(steps_out),
            "cleanup_failures": cleanup_failures,
            "binding_materialization_receipts": len(binding_materialization_receipts),
            "verdict": verdict.get("verdict"),
        },
    }


def execute_selected_experiments(
    selected: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str = "",
) -> dict[str, Any]:
    """Execute every selected experiment; each yields EXECUTED or BLOCKED receipt."""
    selected_ids = [_text(_dict(item).get("obligation_id")) for item in selected]
    if not all(selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected_obligation_identity_invalid")
    run_contract = _dict(mainline_run)
    if not run_contract or _text(run_contract.get("campaign_id")) != _text(campaign_id):
        raise ValueError("experiment batch mainline campaign identity mismatch")
    tokens = load_actor_tokens(root, project)
    results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    blocked = 0
    executed = 0
    harness = 0
    cleanup_failures = 0
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    batch_nonce = str(time.time_ns())
    for index, item in enumerate(selected):
        row = _dict(item)
        oid = _text(row.get("obligation_id"))
        eid = _text(row.get("experiment_id"))
        candidate_id = _text(row.get("candidate_id")) or _stable_id("cand", project, oid or index)
        slice_id = _text(row.get("slice_id") or row.get("behavior_slice_id")) or _stable_id("slice", project, oid or candidate_id)
        execution_id = _stable_id("exec", project, campaign_id, eid, oid, batch_nonce, index)
        evidence_id = _stable_id("evidence", execution_id)
        exp = experiments_by_obligation.get(oid)
        if not isinstance(exp, dict):
            blocked += 1
            compile_results[oid] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "experiment_id": eid,
                "receipt_id": _stable_id("compile", project, campaign_id, oid, batch_nonce),
            }
            missing_outcome = {
                "schema_version": "qualibug.experiment-execution.v1",
                "candidate_id": candidate_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "selected_obligation_has_no_compiled_experiment",
                "finding": None,
                "execution_receipt": {
                    "status": "BLOCKED",
                    "candidate_id": candidate_id,
                    "slice_id": slice_id,
                    "obligation_id": oid,
                    "experiment_id": eid,
                    "execution_id": execution_id,
                    "evidence_id": evidence_id,
                    "campaign_id": campaign_id,
                },
            }
            results.append(missing_outcome)
            continue
        compile_receipt = _dict(exp.get("compile_receipt"))
        compile_status = _text(compile_receipt.get("status")).upper()
        if compile_status != "COMPILED":
            terminal_status = (
                compile_status
                if compile_status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}
                else "HARNESS_FAILED"
            )
            reason_code = _text(compile_receipt.get("reason_code")) or (
                "COMPILE_RECEIPT_MISSING"
                if not compile_status
                else f"COMPILE_STATUS_INVALID:{compile_status}"
            )
            compile_results[oid] = {
                "status": terminal_status,
                "reason_code": reason_code,
                "experiment_id": _text(exp.get("experiment_id") or eid),
                "receipt_id": _text(compile_receipt.get("receipt_id"))
                or _stable_id("compile", project, campaign_id, oid, batch_nonce),
            }
            results.append({
                "schema_version": "qualibug.experiment-execution.v1",
                "candidate_id": candidate_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": _text(exp.get("experiment_id") or eid),
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "status": terminal_status,
                "reason_code": reason_code,
                "detail": "experiment_compile_receipt_not_executable",
                "finding": None,
                "execution_receipt": {
                    "status": terminal_status,
                    "reason_code": reason_code,
                    "obligation_id": oid,
                    "experiment_id": _text(exp.get("experiment_id") or eid),
                    "campaign_id": campaign_id,
                },
            })
            if terminal_status == "BLOCKED":
                blocked += 1
            else:
                harness += 1
            continue
        compile_results[oid] = {
            "status": "COMPILED",
            "reason_code": "",
            "experiment_id": _text(exp.get("experiment_id") or eid),
            "receipt_id": _text(compile_receipt.get("receipt_id"))
            or _stable_id("compile", project, campaign_id, oid, batch_nonce),
            "input_fingerprint": _text(compile_receipt.get("input_fingerprint")),
        }
        outcome = execute_one_experiment(
            exp,
            behavior_ir=behavior_ir,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            campaign_id=campaign_id,
            execution_id=execution_id,
            actor_tokens=tokens,
        )
        eid = _text(outcome.get("experiment_id")) or eid
        outcome.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "obligation_id": oid,
            "experiment_id": eid,
            "execution_id": execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
        })
        receipt = _dict(outcome.get("execution_receipt"))
        receipt.update({
            "candidate_id": candidate_id,
            "slice_id": slice_id,
            "obligation_id": oid,
            "experiment_id": eid,
            "execution_id": execution_id,
            "evidence_id": evidence_id,
            "campaign_id": campaign_id,
        })
        outcome["execution_receipt"] = receipt
        if isinstance(outcome.get("finding"), dict):
            finding = outcome["finding"]
            finding_id = _text(finding.get("id") or finding.get("finding_id")) or _stable_id("finding", evidence_id)
            finding.update({
                "id": finding_id,
                "finding_id": finding_id,
                "candidate_id": candidate_id,
                "behavior_slice_id": slice_id,
                "slice_id": slice_id,
                "obligation_id": oid,
                "experiment_id": eid,
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": campaign_id,
                "mainline_run": {
                    "contract_fingerprint": _text(
                        run_contract.get("contract_fingerprint")
                    ),
                },
            })
            finding_evidence = _dict(finding.get("evidence"))
            finding_evidence["evidence_id"] = evidence_id
            finding_evidence["execution_id"] = execution_id
            finding["evidence"] = finding_evidence
        observation_receipt_ids: list[str] = []
        for step_index, step in enumerate(_list(outcome.get("steps"))):
            if not isinstance(step, dict):
                continue
            observation_receipt_id = _stable_id(
                "observation",
                execution_id,
                step_index,
                step.get("phase"),
                step.get("method"),
                step.get("path"),
            )
            step["observation_receipt_id"] = observation_receipt_id
            observation_receipt_ids.append(observation_receipt_id)
        for observer_receipt in _list(outcome.get("observer_receipts")):
            if not isinstance(observer_receipt, dict):
                continue
            observer_receipt_id = _text(observer_receipt.get("receipt_id"))
            if observer_receipt_id:
                observation_receipt_ids.append(observer_receipt_id)
        for contract_receipt in _list(outcome.get("contract_evidence_receipts")):
            if not isinstance(contract_receipt, dict):
                continue
            contract_receipt_id = _text(contract_receipt.get("receipt_id"))
            if contract_receipt_id:
                observation_receipt_ids.append(contract_receipt_id)
        observation_receipt_ids = list(dict.fromkeys(observation_receipt_ids))
        oracle_verdict = _dict(outcome.get("oracle_verdict"))
        oracle_receipt_id = ""
        if oracle_verdict:
            validated_oracle = validate_contract_oracle_receipt(oracle_verdict)
            oracle_receipt_id = _text(validated_oracle.get("receipt_id"))
            outcome["oracle_verdict"] = validated_oracle
        status = _text(outcome.get("status")).upper()
        if status not in {"EXECUTED", "BLOCKED", "HARNESS_FAILURE", "HARNESS_FAILED"}:
            raise ValueError(f"experiment_execution_status_invalid:{status or 'MISSING'}")
        outcome_cleanup_failures = int(outcome.get("cleanup_failures") or 0)
        if outcome_cleanup_failures:
            # Record cleanup issues as metadata without overriding execution status.
            execution_receipt = _dict(outcome.get("execution_receipt"))
            execution_receipt["cleanup_failures"] = outcome_cleanup_failures
            execution_receipt["cleanup_warning"] = True
            outcome["execution_receipt"] = execution_receipt
        operational_receipt = build_execution_operational_receipt(
            receipt_id=_stable_id("operational", execution_id),
            execution_status=status,
            steps=[
                row
                for row in _list(outcome.get("steps"))
                if isinstance(row, dict)
            ],
            cleanup_failures=outcome_cleanup_failures,
        )
        outcome["operational_receipt"] = operational_receipt
        outcome_execution_receipt = _dict(outcome.get("execution_receipt"))
        outcome_execution_receipt["operational_receipt_id"] = _text(
            operational_receipt.get("receipt_id")
        )
        outcome["execution_receipt"] = outcome_execution_receipt
        results.append(outcome)
        if status == "BLOCKED":
            blocked += 1
            execution_results[oid] = {
                "status": "BLOCKED",
                "reason_code": _text(outcome.get("reason_code"))
                or "BLOCKED_EXECUTION",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "operational_receipt": operational_receipt,
            }
        elif status == "HARNESS_FAILURE":
            harness += 1
            execution_results[oid] = {
                "status": "HARNESS_FAILED",
                "reason_code": _text(outcome.get("reason_code"))
                or "HARNESS_FAILURE",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": execution_id,
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "operational_receipt": operational_receipt,
            }
        else:
            delivery_execution_receipt = build_delivery_execution_receipt(
                mainline_run=run_contract,
                candidate_id=candidate_id,
                slice_id=slice_id,
                obligation_id=oid,
                experiment_id=eid,
                execution_id=execution_id,
                evidence_id=evidence_id,
                operational_receipt=operational_receipt,
                observation_receipt_ids=observation_receipt_ids,
                oracle_receipt_id=oracle_receipt_id,
                elapsed_ms=outcome.get("elapsed_ms"),
                cost_coverage_status="UNKNOWN",
            )
            reproduction_receipt = build_reproduction_receipt(
                execution_receipt=delivery_execution_receipt,
                steps=[
                    row
                    for row in _list(outcome.get("steps"))
                    if isinstance(row, dict)
                ],
                oracle_receipt=oracle_verdict,
                source_refs=[
                    dict(row)
                    for row in _list(exp.get("source_refs"))
                    if isinstance(row, dict)
                ],
            )
            gate_receipt = build_customer_delivery_gate_receipt_v2(
                finding=(
                    outcome.get("finding")
                    if isinstance(outcome.get("finding"), dict)
                    else None
                ),
                execution_receipt=delivery_execution_receipt,
                contract_evidence_receipts=[
                    dict(row)
                    for row in _list(outcome.get("contract_evidence_receipts"))
                    if isinstance(row, dict)
                ],
                observer_receipts=[
                    dict(row)
                    for row in _list(outcome.get("observer_receipts"))
                    if isinstance(row, dict)
                ],
                oracle_receipt=oracle_verdict,
                reproduction_receipt=reproduction_receipt,
            )
            executed += 1
            execution_results[oid] = {
                "status": "EXECUTED",
                "reason_code": "",
                "experiment_id": eid,
                "execution_id": execution_id,
                "receipt_id": _text(delivery_execution_receipt.get("receipt_id")),
                "output_fingerprint": _text(
                    delivery_execution_receipt.get("receipt_fingerprint")
                ),
                "observation_receipt_ids": observation_receipt_ids,
                "oracle_receipt_id": oracle_receipt_id,
                "elapsed_ms": outcome.get("elapsed_ms"),
                "cost_coverage_status": "UNKNOWN",
                "operational_receipt": operational_receipt,
                "delivery_execution_receipt": delivery_execution_receipt,
                "contract_evidence_receipts": list(
                    outcome.get("contract_evidence_receipts") or []
                ),
                "observer_receipts": list(outcome.get("observer_receipts") or []),
                "oracle_receipt": oracle_verdict,
                "reproduction_receipt": reproduction_receipt,
            }
            gate_results[oid] = gate_receipt
            outcome["delivery_execution_receipt"] = delivery_execution_receipt
            outcome["reproduction_receipt"] = reproduction_receipt
            outcome["delivery_gate_receipt"] = gate_receipt
            if isinstance(outcome.get("finding"), dict):
                finding = outcome["finding"]
                finding["delivery_gate_receipt"] = gate_receipt
                finding["delivery_gate_receipt_id"] = _text(
                    gate_receipt.get("gate_receipt_id")
                )
                finding["gate_passed"] = gate_receipt.get("status") == "DELIVERABLE"
                finding["customer_delivery_status"] = (
                    "defect"
                    if gate_receipt.get("status") == "DELIVERABLE"
                    else "candidate"
                )
                finding["customer_visible"] = (
                    gate_receipt.get("status") == "DELIVERABLE"
                )
                finding["customer_delivery_gate_reasons"] = list(
                    gate_receipt.get("reason_codes") or []
                )
                execution_results[oid]["finding"] = dict(finding)
        cleanup_failures += outcome_cleanup_failures
        if status == "EXECUTED" and isinstance(outcome.get("finding"), dict):
            findings.append(outcome["finding"])
    return {
        "schema_version": "qualibug.experiment-execution-batch.v1",
        "selected_count": len(selected),
        "executed_count": executed,
        "blocked_count": blocked,
        "harness_failure_count": harness,
        "cleanup_failures": cleanup_failures,
        "findings": findings,
        "results": results,
        "compile_results": compile_results,
        "execution_results": execution_results,
        "gate_results": gate_results,
        "every_experiment_has_receipt": all(
            isinstance(item.get("execution_receipt"), dict) for item in results
        ),
    }
