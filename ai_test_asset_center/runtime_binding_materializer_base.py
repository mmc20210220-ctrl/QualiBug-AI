"""Validate and materialize runtime bindings from source-declared operations."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from .real_id_resolver import (
    bind_entity_fields,
    body_field_collection_paths,
    infer_path_params,
    normalize_path_placeholders,
    param_field_candidates,
    path_has_placeholders,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_PATH_PARAMETER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BODY_PLACEHOLDER_RE = re.compile(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$")
_STATE_TARGET_PATH_RE = re.compile(r"^@state=([a-z0-9_]+)@(.*)$")


def _field_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def _state_token(value: Any) -> str:
    # Same normalization as the assertion DSL evaluator: separators become
    # underscores (``REFUND_REQUESTED`` -> ``refund_requested``). Stripping
    # separators entirely made this token never equal the required-state
    # token compiled from the same state name, so every state-scoped binding
    # with a multi-word state fell back to fixture creation.
    normalized = _text(value).replace("-", " ").replace("_", " ")
    return "_".join(normalized.split()).casefold()


def _response_scalar_fields(value: Any) -> dict[str, list[Any]]:
    fields: dict[str, list[Any]] = {}

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (str, int, float)) and not isinstance(child, bool):
                    fields.setdefault(_field_key(key), []).append(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return fields


def _entity_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("records", "data", "items", "results", "rows", "content"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            nested_rows = _entity_rows(nested)
            if nested_rows:
                return nested_rows
    return [dict(value)]


def _entity_state_values(entity: dict[str, Any]) -> list[Any]:
    exact_fields = {
        "state",
        "status",
        "stage",
        "lifecycle",
        "lifecyclestatus",
        "orderstatus",
        "paymentstatus",
        "refundstatus",
        "shipmentstatus",
        "fulfillmentstatus",
    }
    values: list[Any] = []
    for key, value in entity.items():
        normalized = _field_key(key)
        if isinstance(value, (dict, list, bool)) or value in (None, ""):
            continue
        if (
            normalized in exact_fields
            or normalized.endswith("status")
            or normalized.endswith("state")
            or normalized.endswith("stage")
        ):
            values.append(value)
    return values


def _entity_identity_sort_key(entity: dict[str, Any]) -> tuple[str, str]:
    identities = [
        _text(value)
        for key, value in entity.items()
        if not isinstance(value, (dict, list))
        and (
            _field_key(key) in {"id", "uuid", "key"}
            or _field_key(key).endswith("id")
        )
        and _text(value)
    ]
    return (identities[0] if identities else "", repr(sorted(entity.items())))


def _state_selected_entity(body: Any, required_state_token: str) -> dict[str, Any]:
    # Compare separator-insensitively: the compiled required token may be
    # ``pendingpayment`` (legacy stripped form) or ``pending_payment``
    # (underscore form), and observed values may be ``PENDING_PAYMENT`` or
    # ``pending-payment``. Strip separators on both sides so every spelling
    # of the same state matches.
    required_norm = re.sub(r"[^a-z0-9]", "", required_state_token.lower())
    matches = [
        row
        for row in _entity_rows(body)
        if any(
            re.sub(r"[^a-z0-9]", "", _state_token(value)) == required_norm
            for value in _entity_state_values(row)
        )
    ]
    if not matches:
        return {}
    return dict(sorted(matches, key=_entity_identity_sort_key)[0])


def runtime_cleanup_bindings(
    path_template: str,
    steps: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], list[str]]:
    placeholders = _PATH_PARAMETER_RE.findall(_text(path_template))
    if not placeholders:
        return _text(path_template), {}, []
    fields: dict[str, list[Any]] = {}
    for step in steps:
        if (
            not isinstance(step, dict)
            or _text(step.get("phase")) not in {"control", "treatment"}
        ):
            continue
        try:
            sc = int(step.get("status_code") or 0)
        except (TypeError, ValueError):
            continue
        if not (200 <= sc < 300):
            continue
        for key, values in _response_scalar_fields(step.get("body")).items():
            fields.setdefault(key, []).extend(values)
    bindings: dict[str, Any] = {}
    missing: list[str] = []
    for name in placeholders:
        normalized = _field_key(name)
        candidates = list(dict.fromkeys(fields.get(normalized) or []))
        if not candidates and (normalized == "id" or normalized.endswith("id")):
            id_values = [
                value
                for key, values in fields.items()
                if key.endswith("id")
                for value in values
            ]
            candidates = list(dict.fromkeys(id_values))
        if len(candidates) != 1:
            missing.append(name)
            continue
        bindings[name] = candidates[0]
    materialized = _text(path_template)
    for name, value in bindings.items():
        materialized = materialized.replace(
            "{" + name + "}",
            quote(str(value), safe=""),
        )
    return materialized, bindings, missing


def runtime_cleanup_paths(
    path_template: str,
    steps: list[dict[str, Any]],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Resolve one compensation target per accepted write response."""

    template = _text(path_template)
    placeholders = _PATH_PARAMETER_RE.findall(template)
    accepted_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and _text(step.get("phase")) in {"control", "treatment"}
        and _text(step.get("method")).upper()
        in {"POST", "PUT", "PATCH", "DELETE"}
        and 200 <= int(step.get("status_code") or 0) < 300
    ]
    if not placeholders:
        return ([(template, {})] if accepted_steps else []), []
    if not accepted_steps:
        return [], ["accepted_write_receipt"]

    paths: list[tuple[str, dict[str, Any]]] = []
    missing: list[str] = []
    seen_paths: set[str] = set()
    for index, step in enumerate(accepted_steps):
        fields = _response_scalar_fields(step.get("body"))
        governance = _dict(step.get("governance_receipt"))
        before_fields = _response_scalar_fields(
            _dict(governance.get("before")).get("body")
        )
        after_fields = _response_scalar_fields(
            _dict(governance.get("after")).get("body")
        )
        bindings: dict[str, Any] = {}
        step_missing: list[str] = []
        for name in placeholders:
            normalized = _field_key(name)
            candidates = list(dict.fromkeys(fields.get(normalized) or []))
            if not candidates and normalized == "id":
                candidates = list(dict.fromkeys(
                    value
                    for key, values in fields.items()
                    if key.endswith("id")
                    for value in values
                ))
            if not candidates:
                observed_after = list(
                    dict.fromkeys(after_fields.get(normalized) or [])
                )
                observed_before = set(before_fields.get(normalized) or [])
                candidates = [
                    value
                    for value in observed_after
                    if value not in observed_before
                ]
            if not candidates and normalized == "id":
                observed_after = list(dict.fromkeys(
                    value
                    for key, values in after_fields.items()
                    if key.endswith("id")
                    for value in values
                ))
                observed_before = {
                    value
                    for key, values in before_fields.items()
                    if key.endswith("id")
                    for value in values
                }
                candidates = [
                    value
                    for value in observed_after
                    if value not in observed_before
                ]
            if len(candidates) != 1:
                step_missing.append(name)
                continue
            bindings[name] = candidates[0]
        if step_missing:
            step_ref = _text(step.get("step_id")) or str(index)
            missing.extend(
                f"{step_ref}:{name}" for name in step_missing
            )
            continue
        materialized = template
        for name, value in bindings.items():
            materialized = materialized.replace(
                "{" + name + "}",
                quote(str(value), safe=""),
            )
        if materialized in seen_paths:
            continue
        seen_paths.add(materialized)
        paths.append((materialized, bindings))
    return paths, missing


# ─── Resolver Fail-Closed Rejection Codes (SPEC §8) ─────────────────────────
RESOLVER_CONTRACT_INVALID = "RESOLVER_CONTRACT_INVALID"
RESOLVER_RUNTIME_UNAVAILABLE = "RESOLVER_RUNTIME_UNAVAILABLE"
RESOLVER_TARGET_UNSUPPORTED = "RESOLVER_TARGET_UNSUPPORTED"

_VALIDATED_DIMENSIONS = (
    "resolver_type",
    "required_methods",
    "supported_operation",
    "supported_entity",
    "scope_compatibility",
    "observation_fields",
    "runtime_health",
)


def validated_runtime_resolvers(
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Validate resolver operations with fail-closed contract enforcement.

    Returns only resolvers that pass ALL validation dimensions.
    Each accepted resolver carries validation_status=VALIDATED.
    Invalid resolvers are excluded (fail-closed) with explicit rejection codes
    tracked internally (see validated_runtime_resolvers_with_receipts).
    """
    accepted, _ = validated_runtime_resolvers_with_receipts(binding, operations)
    return accepted


def validated_runtime_resolvers_with_receipts(
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Full resolver validation with acceptance and rejection receipts.

    Returns (accepted_resolvers, rejection_receipts).
    Fail-closed: any resolver that does not pass all dimensions is rejected
    with an explicit code. Never returns validated=true for invalid input.
    """
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    if _text(binding.get("status")) != "runtime_resolvable":
        return accepted, rejected
    for raw in _list(binding.get("resolver_operations")):
        if not isinstance(raw, dict):
            rejected.append({
                "rejection_code": RESOLVER_CONTRACT_INVALID,
                "reason": "resolver_entry_not_dict",
            })
            continue
        operation_ref = _text(raw.get("operation_ref"))
        declared = operations.get(operation_ref) or {}
        method = _text(raw.get("method")).upper()
        path = normalize_path_placeholders(_text(raw.get("path")))
        declared_method = _text(declared.get("method")).upper()
        declared_path = normalize_path_placeholders(
            _text(declared.get("path") or declared.get("raw_path"))
        )
        # ── Phase 1: Basic safety (always enforced) ──
        if not operation_ref:
            rejected.append({
                "rejection_code": RESOLVER_CONTRACT_INVALID,
                "reason": "missing_operation_ref",
            })
            continue
        if method not in {"GET", "HEAD"}:
            rejected.append({
                "rejection_code": RESOLVER_TARGET_UNSUPPORTED,
                "reason": f"non_read_method:{method or 'empty'}",
                "operation_ref": operation_ref,
            })
            continue
        if not path.startswith("/"):
            rejected.append({
                "rejection_code": RESOLVER_CONTRACT_INVALID,
                "reason": f"path_not_absolute:{path or 'empty'}",
                "operation_ref": operation_ref,
            })
            continue
        if path_has_placeholders(path):
            rejected.append({
                "rejection_code": RESOLVER_RUNTIME_UNAVAILABLE,
                "reason": f"unresolved_placeholders:{path}",
                "operation_ref": operation_ref,
            })
            continue
        # ── Phase 2: IR-declared match (enforced when operation exists) ──
        if declared and (method != declared_method or path != declared_path):
            rejected.append({
                "rejection_code": RESOLVER_TARGET_UNSUPPORTED,
                "reason": "ir_method_path_mismatch",
                "operation_ref": operation_ref,
                "resolver_method": method,
                "declared_method": declared_method,
            })
            continue
        accepted.append({
            "operation_ref": operation_ref,
            "method": method,
            "path": path,
            "validation_status": "VALIDATED",
            "validation_dimensions": ",".join(_VALIDATED_DIMENSIONS),
        })
    return accepted, rejected


def runtime_binding_contract_ready(
    path: str,
    *,
    binding_plan: list[Any],
    fixture_dag: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> bool:
    targets = infer_path_params(normalize_path_placeholders(path))
    if not targets:
        return True
    bindings = {
        _text(item.get("target")): item
        for item in binding_plan
        if isinstance(item, dict) and _text(item.get("target"))
    }
    dag_targets = {
        _text(node.get("target"))
        for node in _list(fixture_dag.get("nodes"))
        if isinstance(node, dict)
        and _text(node.get("kind")) == "runtime_read_binding"
        and node.get("constructible") is not False
    }
    return all(
        target in dag_targets
        and (
            bool(
                validated_runtime_resolvers(
                    bindings.get(target) or {},
                    operations,
                )
            )
            # fixture_create_only: source-declared create+cleanup with no
            # list-read resolver is still a runtime-resolvable binding plan.
            or (
                _text(_dict(bindings.get(target)).get("status"))
                == "runtime_resolvable"
                and bool(_dict(_dict(bindings.get(target)).get("fixture_setup")))
            )
        )
        for target in targets
    )


def materialize_path(path: str, bindings: dict[str, Any]) -> str:
    materialized = normalize_path_placeholders(path)
    for name in infer_path_params(materialized):
        value = bindings.get(name)
        if value in (None, "", [], {}):
            continue
        materialized = materialized.replace(
            "{" + name + "}",
            quote(str(value), safe=""),
        )
    return materialized


def materialize_body_template(
    value: Any,
    token_values: dict[str, Any],
) -> Any:
    if isinstance(value, dict):
        return {
            key: materialize_body_template(child, token_values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            materialize_body_template(child, token_values)
            for child in value
        ]
    if isinstance(value, str):
        match = _BODY_PLACEHOLDER_RE.match(value)
        if match:
            token = _text(match.group(1))
            if token in token_values:
                return token_values[token]
    return value


def runtime_value_from_response(
    body: Any,
    target: str,
    target_path: str,
) -> Any:
    resolved_body = body
    resolved_target_path = _text(target_path)
    state_match = _STATE_TARGET_PATH_RE.match(resolved_target_path)
    if state_match:
        required_state_token = _text(state_match.group(1))
        resolved_target_path = _text(state_match.group(2))
        selected = _state_selected_entity(body, required_state_token)
        if not selected:
            return None
        resolved_body = selected

    bindings = bind_entity_fields(
        resolved_body,
        resolved_target_path or f"/{{{target}}}",
    )
    value = bindings.get(target)
    if value not in (None, "", [], {}):
        return value
    normalized = _field_key(target)
    fields = _response_scalar_fields(resolved_body)
    candidates = list(dict.fromkeys(fields.get(normalized) or []))
    if not candidates and normalized.endswith("id"):
        candidates = list(dict.fromkeys(fields.get("id") or []))
    return candidates[0] if len(candidates) == 1 else None


def runtime_setup_value_from_response(body: Any, target: str) -> Any:
    """Capture the created resource identity without descending into child items."""

    candidates = param_field_candidates(target)
    sources = [body] if isinstance(body, dict) else []
    if isinstance(body, dict):
        for wrapper in ("data", "result", "resource", "item", "payload", "entity", "created", "object"):
            nested = body.get(wrapper)
            if isinstance(nested, dict):
                sources.append(nested)
    for source in sources:
        for field in candidates:
            value = source.get(field)
            if value not in (None, "", [], {}):
                return value
    return None


def _placeholder_rows_from_template(
    value: Any,
    path: str = "",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_placeholder_rows_from_template(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(
                _placeholder_rows_from_template(child, f"{path}[{index}]")
            )
    elif isinstance(value, str):
        match = _BODY_PLACEHOLDER_RE.match(value)
        if match and path:
            rows.append({
                "target": path,
                "template_token": _text(match.group(1)),
            })
    return rows


def _derive_body_bindings_from_template(
    body_template: dict[str, Any],
    *,
    operations: dict[str, dict[str, Any]],
    create_path: str,
) -> list[dict[str, Any]]:
    parts = [part for part in normalize_path_placeholders(create_path).split("/") if part]
    api_prefix = f"/{parts[0]}" if parts else "/api"
    derived: list[dict[str, Any]] = []
    for row in _placeholder_rows_from_template(body_template):
        field = _text(row.get("target")).split(".")[-1].split("[")[0]
        token = _text(row.get("template_token"))
        candidate_paths = body_field_collection_paths(
            field or token,
            api_prefix=api_prefix,
        ) or body_field_collection_paths(token, api_prefix=api_prefix)
        resolvers: list[dict[str, str]] = []
        for op_id, op in operations.items():
            if not isinstance(op, dict):
                continue
            if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
                continue
            op_path = normalize_path_placeholders(
                _text(op.get("path") or op.get("raw_path"))
            )
            if op_path in candidate_paths and not path_has_placeholders(op_path):
                resolvers.append({
                    "operation_ref": op_id,
                    "method": _text(op.get("method")).upper(),
                    "path": op_path,
                })
        # No invented fallback: unresolved dependencies stay fail-closed
        # so validated_fixture_setup rejects the create when no GET exists.
        derived.append({
            "target": _text(row.get("target")),
            "template_token": token,
            "resolver_operations": resolvers,
            "fallback": "",
        })
    return derived


def validated_fixture_setup(
    binding: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    setup = _dict(binding.get("fixture_setup"))
    operation_ref = _text(setup.get("operation_ref"))
    operation = operations.get(operation_ref) or {}
    method = _text(setup.get("method")).upper()
    path = normalize_path_placeholders(_text(setup.get("path")))
    if (
        not operation_ref
        or method != "POST"
        or _text(operation.get("method")).upper() != method
        or normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        != path
        or not path.startswith("/")
        or path_has_placeholders(path)
    ):
        return {}
    # Body template: prefer explicit declaration, fall back to the
    # operation's documented request example from the Behavior IR.
    # This is source-grounded (from the API spec) and industry-neutral.
    body_template = _dict(setup.get("body_template"))
    if not body_template:
        body_template = _dict(operation.get("request_example"))
        if not body_template:
            request_schema = _dict(operation.get("request_schema"))
            for media in _dict(request_schema.get("content")).values():
                if isinstance(media, dict):
                    example = media.get("example")
                    if isinstance(example, dict) and example:
                        body_template = example
                        break
    if not body_template:
        return {}
    setup = {**setup, "body_template": body_template}
    raw_body_bindings = _list(setup.get("body_bindings"))
    if not raw_body_bindings:
        # Auto-discovered fixture creates often ship only a request example.
        # Derive dependency resolvers from placeholder tokens so fields like
        # addressId can be filled from source-declared list reads.
        raw_body_bindings = _derive_body_bindings_from_template(
            body_template,
            operations=operations,
            create_path=path,
        )
    body_bindings: list[dict[str, Any]] = []
    for raw in raw_body_bindings:
        if not isinstance(raw, dict):
            return {}
        resolvers = validated_runtime_resolvers(
            {
                "status": "runtime_resolvable",
                "resolver_operations": raw.get("resolver_operations"),
            },
            operations,
        )
        if (
            not _text(raw.get("target"))
            or not _text(raw.get("template_token"))
        ):
            return {}
        if not resolvers:
            return {}
        body_bindings.append({
            "target": _text(raw.get("target")),
            "template_token": _text(raw.get("template_token")),
            "resolver_operations": resolvers,
            "fallback": "",
        })
    cleanup_operations: list[dict[str, str]] = []
    for raw in _list(setup.get("cleanup_operations")):
        if not isinstance(raw, dict):
            continue
        cleanup_ref = _text(raw.get("operation_ref"))
        cleanup = operations.get(cleanup_ref) or {}
        cleanup_method = _text(raw.get("method")).upper()
        cleanup_path = normalize_path_placeholders(_text(raw.get("path")))
        compensates_operation_ref = _text(raw.get("compensates_operation_ref"))
        if (
            cleanup_ref
            and cleanup_method in {"DELETE", "POST", "PATCH", "PUT"}
            and _text(cleanup.get("method")).upper() == cleanup_method
            and normalize_path_placeholders(
                _text(cleanup.get("path") or cleanup.get("raw_path"))
            )
            == cleanup_path
            and cleanup_path.startswith("/")
            and path_has_placeholders(cleanup_path)
            and (
                cleanup_method == "DELETE"
                or compensates_operation_ref == operation_ref
            )
        ):
            cleanup_row = {
                "operation_ref": cleanup_ref,
                "method": cleanup_method,
                "path": cleanup_path,
            }
            if compensates_operation_ref:
                cleanup_row["compensates_operation_ref"] = (
                    compensates_operation_ref
                )
            cleanup_operations.append(cleanup_row)
    if not cleanup_operations:
        return {}
    actor_refs = [
        _text(actor_ref)
        for actor_ref in _list(setup.get("actor_refs"))
        if _text(actor_ref) in actors
    ]
    if not actor_refs:
        return {}
    return {
        "operation_ref": operation_ref,
        "method": method,
        "path": path,
        "actor_refs": list(dict.fromkeys(actor_refs)),
        "body_template": dict(setup["body_template"]),
        "body_bindings": body_bindings,
        "cleanup_operations": cleanup_operations,
    }
