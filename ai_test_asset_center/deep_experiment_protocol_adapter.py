"""Deep Experiment Plan-to-Protocol Adapter.

Minimal adaptation layer that enriches deep_experiment_planner output so it
passes the existing ``preflight_experiment_executable`` checks and enters the
existing ``execute_one_experiment`` path without modification.

Responsibilities (SPEC §8):
  - Resolve actor_ref to an actor with valid credential_secret_ref
  - Add binding_plan for operations with path placeholders
  - Validate operation_ref exists in Behavior IR
  - Add fixture_dag stub for multi-step sequences
  - Add safety_contract for governed write experiments
  - Add cleanup_plan for write operations

Does NOT:
  - Re-select mechanisms
  - Re-reason business rules
  - Modify mutations
  - Send HTTP requests
  - Create fixtures
  - Execute Oracle
  - Generate Findings
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    return str(v or "").strip()


# Path placeholder patterns: :paramName or {paramName}
_PATH_PARAM_RE = re.compile(r"(?::([a-zA-Z_]\w*)|\{([a-zA-Z_]\w*)\})")


def _extract_path_params(path: str) -> list[str]:
    """Extract placeholder parameter names from a path template."""
    params: list[str] = []
    for m in _PATH_PARAM_RE.finditer(path):
        params.append(m.group(1) or m.group(2))
    return params


def _is_template_actor(actor: dict[str, Any]) -> bool:
    """Detect actors with template/placeholder role or credential."""
    role = _text(actor.get("role"))
    secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    # Template patterns: {type: string}, {…}, empty
    if not role or role.startswith("{") or role.startswith(":"):
        return True
    if not secret or secret.startswith("{") or ":{" in secret:
        return True
    return False


def _body_placeholder_tokens(value: Any) -> list[str]:
    """Extract `<token>` / `{token}` placeholders from a request body template."""
    tokens: list[str] = []
    seen: set[str] = set()

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
        match = re.match(r"^\s*[<{]([A-Za-z_][A-Za-z0-9_]*)[>}]\s*$", node)
        if not match:
            return
        token = _text(match.group(1))
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)

    walk(value)
    return tokens


def _build_binding_plan(
    operation: dict[str, Any],
    ops: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    *,
    step_bodies: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build binding_plan for path and body placeholders on one operation.

    Path resolvers reuse the mainline structural authority
    (``declared_runtime_read_resolvers`` / collection parents). Body tokens
    reuse ``body_field_collection_paths`` so deep experiments get the same
    industry-neutral binding surface as compiled mainline experiments.
    """
    from .real_id_resolver import (
        body_field_collection_paths,
        collection_path,
        normalize_path_placeholders,
    )
    from .runtime_binding_graph import (
        _declared_fixture_setup,
        declared_runtime_read_resolvers,
    )

    path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    )
    params = _extract_path_params(path)
    bindings: list[dict[str, Any]] = []

    path_resolvers = declared_runtime_read_resolvers(
        operation,
        behavior_ir=behavior_ir,
        max_candidates=3,
    )
    primary_collection = normalize_path_placeholders(collection_path(path))
    for param in params:
        entity_hint = re.sub(r"(_?id|_?uuid)$", "", param, flags=re.IGNORECASE)
        if not entity_hint and primary_collection.startswith("/"):
            entity_hint = primary_collection.rstrip("/").rsplit("/", 1)[-1]
        if not entity_hint:
            entity_hint = param

        resolver_operations = [dict(row) for row in path_resolvers]
        # Bare ``{id}`` often needs the parent collection even when the param
        # name itself is not a useful entity hint for path substring search.
        if not resolver_operations and primary_collection.startswith("/"):
            for op in ops.values():
                if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
                    continue
                op_path = normalize_path_placeholders(
                    _text(op.get("path") or op.get("raw_path"))
                )
                if op_path == primary_collection and not _extract_path_params(op_path):
                    resolver_operations.append({
                        "operation_ref": _text(op.get("id")),
                        "method": _text(op.get("method")).upper(),
                        "path": op_path,
                    })
                    break

        binding: dict[str, Any] = {
            "target": param,
            "target_path": path if path.startswith("/") else f"/{{{param}}}",
            "strategy": "runtime_resolver",
            "entity_hint": entity_hint,
            "status": "runtime_resolvable" if resolver_operations else "declared",
            "source_priority": (
                "same_actor_list_read" if resolver_operations else "fixture_create_only"
            ),
            "resolver_operations": resolver_operations,
        }
        if not resolver_operations:
            fixture_setup = _declared_fixture_setup(
                operation,
                target=param,
                behavior_ir=behavior_ir,
            )
            if fixture_setup:
                binding["fixture_setup"] = fixture_setup
                binding["status"] = "runtime_resolvable"
                binding["source_priority"] = "fixture_create_only"
        bindings.append(binding)

    # Body placeholders from treatment/control request templates.
    api_prefix = "/" + path.strip("/").split("/")[0] if path.startswith("/") else "/api"
    body_tokens: list[str] = []
    for body in step_bodies or []:
        body_tokens.extend(_body_placeholder_tokens(body))
    for token in list(dict.fromkeys(body_tokens)):
        if any(b.get("target") == token for b in bindings):
            continue
        candidate_paths = body_field_collection_paths(token, api_prefix=api_prefix)
        resolver_operations: list[dict[str, Any]] = []
        for candidate in candidate_paths:
            for op in ops.values():
                if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
                    continue
                op_path = normalize_path_placeholders(
                    _text(op.get("path") or op.get("raw_path"))
                )
                if op_path == candidate and not _extract_path_params(op_path):
                    resolver_operations.append({
                        "operation_ref": _text(op.get("id")),
                        "method": _text(op.get("method")).upper(),
                        "path": op_path,
                    })
                    break
            if resolver_operations:
                break
        if not resolver_operations:
            continue
        bindings.append({
            "target": token,
            "target_path": f"/{{{token}}}",
            "strategy": "runtime_resolver",
            "status": "runtime_resolvable",
            "source_priority": "same_actor_list_read",
            "resolver_operations": resolver_operations,
            "body_template_paths": [token],
        })

    return bindings


def adapt_deep_experiments_for_execution(
    deep_experiments: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Adapt deep planner output for the existing executor.

    Returns:
        {
            "adapted": [experiment, ...],
            "by_obligation": {oid: experiment},
            "adapted_count": int,
            "blocked_count": int,
            "blocked_reasons": {reason: count},
            "actor_resolution": {...},
        }
    """
    ir = _dict(behavior_ir)
    ir_actors = {
        _text(a.get("id")): a
        for a in _list(ir.get("actors"))
        if isinstance(a, dict) and _text(a.get("id"))
    }
    ir_ops = {
        _text(o.get("id")): o
        for o in _list(ir.get("operations"))
        if isinstance(o, dict) and _text(o.get("id"))
    }

    adapted: list[dict[str, Any]] = []
    by_obligation: dict[str, dict[str, Any]] = {}
    blocked_reasons: dict[str, int] = {}
    actor_resolutions: list[dict[str, Any]] = []

    for exp in deep_experiments:
        if not isinstance(exp, dict):
            continue

        exp = deepcopy(exp)
        oid = _text(exp.get("obligation_id"))
        eid = _text(exp.get("experiment_id"))

        # ── Step 1: Validate exact source-declared actor references ──
        resolved_actor_refs: list[str] = []
        steps = _list(exp.get("control_plan")) + _list(exp.get("treatment_plan"))
        actor_invalid = not steps
        for step in steps:
            if not isinstance(step, dict):
                actor_invalid = True
                continue
            actor_ref = _text(step.get("actor_ref"))
            actor = ir_actors.get(actor_ref)
            if not actor or _is_template_actor(actor):
                actor_invalid = True
                continue
            resolved_actor_refs.append(actor_ref)

        if actor_invalid:
            blocked_reasons["BLOCKED_MISSING_ACTOR"] = (
                blocked_reasons.get("BLOCKED_MISSING_ACTOR", 0) + 1
            )
            continue

        actor_resolutions.append({
            "experiment_id": eid,
            "obligation_id": oid,
            "declared_actor_refs": list(dict.fromkeys(resolved_actor_refs)),
            "resolved": True,
        })

        # ── Step 2: Validate operation references and build binding_plan ──
        all_bindings: list[dict[str, Any]] = []
        operation_invalid = False
        for step in steps:
            if not isinstance(step, dict):
                operation_invalid = True
                continue
            op_ref = _text(step.get("operation_ref"))
            op = ir_ops.get(op_ref)
            if not op:
                operation_invalid = True
                continue
            step_bodies = [
                step.get("body"),
                _dict(step.get("request")).get("body"),
                _dict(op).get("request_example"),
            ]
            # Build bindings for path params and body placeholders.
            bindings = _build_binding_plan(
                op,
                ir_ops,
                ir,
                step_bodies=step_bodies,
            )
            for b in bindings:
                # Deduplicate by target
                if not any(eb.get("target") == b.get("target") for eb in all_bindings):
                    all_bindings.append(b)

        if operation_invalid:
            blocked_reasons["BLOCKED_MISSING_OPERATION"] = (
                blocked_reasons.get("BLOCKED_MISSING_OPERATION", 0) + 1
            )
            continue

        # Add binding_plan to experiment
        if all_bindings:
            exp["binding_plan"] = all_bindings

        # ── Step 3: Build fixture_dag with runtime_read_binding nodes ──
        # runtime_binding_contract_ready requires dag_targets to contain each
        # binding target with kind="runtime_read_binding"
        treatment = _list(exp.get("treatment_plan"))
        dag_nodes: list[dict[str, Any]] = []
        # Add runtime_read_binding nodes for each binding target
        for b in all_bindings:
            _target = _text(b.get("target"))
            if not _target:
                continue
            if not (
                _list(b.get("resolver_operations"))
                or _dict(b.get("fixture_setup"))
            ):
                continue
            dag_nodes.append({
                "node_id": f"bind_{_target}",
                "kind": "runtime_read_binding",
                "target": _target,
                "constructible": True,
                "resolver_operations": _list(b.get("resolver_operations")),
                "source_priority": _text(b.get("source_priority")),
            })
        # Add step dependency nodes for multi-step treatments
        if len(treatment) > 1:
            for i, step in enumerate(treatment):
                if not isinstance(step, dict):
                    continue
                dag_nodes.append({
                    "node_id": _text(step.get("step_id")) or f"step_{i}",
                    "step_id": _text(step.get("step_id")) or f"step_{i}",
                    "operation_ref": _text(step.get("operation_ref")),
                    "dependencies": [
                        _text(treatment[i - 1].get("step_id")) or f"step_{i-1}"
                    ] if i > 0 else [],
                })
        if dag_nodes:
            exp["fixture_dag"] = {
                "status": "READY",
                "nodes": dag_nodes,
                "setup_order": [_text(n.get("node_id")) for n in dag_nodes if _text(n.get("node_id"))],
            }

        # ── Step 4: Add safety_contract for write operations ──
        has_write = any(
            isinstance(step, dict)
            and _text(ir_ops.get(_text(step.get("operation_ref")), {}).get("method")).upper()
            in {"POST", "PUT", "PATCH", "DELETE"}
            for step in steps
        )
        if has_write:
            from .experiment_compiler_obligation import _is_ephemeral_session_path
            from .real_id_resolver import normalize_path_placeholders

            write_ops = [
                ir_ops.get(_text(step.get("operation_ref"))) or {}
                for step in steps
                if isinstance(step, dict)
                and _text(
                    (ir_ops.get(_text(step.get("operation_ref"))) or {}).get("method")
                ).upper()
                in {"POST", "PUT", "PATCH", "DELETE"}
            ]
            ephemeral_write = bool(write_ops) and all(
                _is_ephemeral_session_path(
                    normalize_path_placeholders(
                        _text(op.get("path") or op.get("raw_path"))
                    )
                )
                for op in write_ops
                if op
            )
            exp["safety_contract"] = {
                "governed_write": True,
                "require_receipt": True,
                "require_cleanup": not ephemeral_write,
                "cleanup_not_required": ephemeral_write,
            }
            if not _list(exp.get("cleanup_plan")) and not ephemeral_write:
                blocked_reasons["BLOCKED_NON_REVERSIBLE_WRITE"] = (
                    blocked_reasons.get("BLOCKED_NON_REVERSIBLE_WRITE", 0) + 1
                )
                continue

        # ── Step 5: Ensure assertion is in assertions list format ──
        # The executor's outcome finalizer reads exp["assertions"] (plural)
        assertion = _dict(exp.get("assertion"))
        if assertion and not _list(exp.get("assertions")):
            if _text(assertion.get("kind")) == "deep_mechanism_contrast":
                blocked_reasons["BLOCKED_MISSING_SOURCE_ASSERTION"] = (
                    blocked_reasons.get("BLOCKED_MISSING_SOURCE_ASSERTION", 0) + 1
                )
                continue
            exp["assertions"] = [assertion]

        # ── Step 5b: Fix observers for read-only experiments ──
        # entity_state and business_effect require write steps with
        # governance_receipt. For GET-only experiments they always return
        # INDETERMINATE, blocking oracle activation.
        _all_step_ops = [
            ir_ops.get(_text(s.get("operation_ref"))) or {}
            for s in _list(exp.get("control_plan")) + _list(exp.get("treatment_plan"))
            if isinstance(s, dict)
        ]
        _has_write_op = any(
            _text(op.get("method")).upper() in {"POST", "PUT", "PATCH", "DELETE"}
            for op in _all_step_ops
        )
        if not _has_write_op:
            exp["observers"] = [
                obs for obs in _list(exp.get("observers"))
                if isinstance(obs, dict)
                and _text(obs.get("observer_id")) not in {"entity_state", "business_effect"}
            ]

        # ── Step 6: Mark as adapted ──
        exp["_protocol_adapted"] = True
        exp["_adaptation_receipt"] = {
            "actor_refs_validated": list(dict.fromkeys(resolved_actor_refs)),
            "binding_count": len(all_bindings),
            "has_fixture_dag": bool(exp.get("fixture_dag")),
            "has_safety_contract": bool(exp.get("safety_contract")),
            "has_cleanup": bool(_list(exp.get("cleanup_plan"))),
        }

        adapted.append(exp)
        if oid:
            by_obligation[oid] = exp

    return {
        "schema_version": "qualibug.deep-experiment-protocol-adaptation.v1",
        "adapted": adapted,
        "by_obligation": by_obligation,
        "adapted_count": len(adapted),
        "blocked_count": sum(blocked_reasons.values()),
        "blocked_reasons": blocked_reasons,
        "actor_resolution": {
            "total": len(actor_resolutions),
            "resolved": sum(1 for r in actor_resolutions if r.get("resolved")),
            "default_actor": "",
        },
    }
