from __future__ import annotations

"""Phase94A V3: input-grounded business state-machine exploration.

This module expands the runtime search space with illegal state-path probes, but
it deliberately avoids global business templates such as "rejected -> pay".  A
state-path probe is generated only when both sides are grounded in customer
input:

* the state must be observed in an OpenAPI schema enum or in a source quote for
  the same business resource; and
* the action must be observed in an existing document-grounded probe endpoint or
  in the customer OpenAPI operation for the same resource.

The output is still a probe, not a finding.  Phase92 runtime before/after
invariants decide whether the target system really has a bug.
"""

import re
from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
STATE_FIELD_RE = re.compile(r"(?:^|_)(status|state|stage|phase)(?:$|_)", re.I)
ACTION_VERB_RE = re.compile(
    r"(?:submit|approve|reject|cancel|pay|paid|ship|deliver|complete|close|reopen|refund|release|freeze|unfreeze|archive|restore|transition|callback)",
    re.I,
)
# This is a lexical detector, not a generation template.  A value from this set
# is usable only if it was found in customer input for the relevant resource.
TERMINAL_STATES = {"cancelled", "canceled", "rejected", "completed", "closed", "refunded", "failed", "expired", "voided", "archived"}
ORDERED_STATE_HINTS = [
    "created",
    "draft",
    "submitted",
    "pending",
    "approved",
    "paid",
    "shipped",
    "delivered",
    "completed",
]
KNOWN_STATE_LEXICON = sorted(set(ORDERED_STATE_HINTS) | TERMINAL_STATES | {"open", "active", "inactive", "processing", "reviewing", "released", "frozen", "reopened"})
ACTION_TARGET_STATE = {
    "submit": "submitted",
    "approve": "approved",
    "reject": "rejected",
    "cancel": "cancelled",
    "pay": "paid",
    "paid": "paid",
    "ship": "shipped",
    "deliver": "delivered",
    "complete": "completed",
    "close": "closed",
    "reopen": "reopened",
    "refund": "refunded",
    "release": "released",
    "freeze": "frozen",
    "unfreeze": "active",
    "archive": "archived",
    "restore": "active",
    "transition": "transitioned",
    "callback": "paid",
}
NEGATIVE_EXPECTED = [400, 403, 409, 422]
RESOURCE_SUFFIX_RE = re.compile(r"(?:input|request|response|dto|model|entity|schema)$", re.I)
CAMEL_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _paths(spec: dict[str, Any]) -> dict[str, Any]:
    paths = spec.get("paths") if isinstance(spec, dict) else {}
    return paths if isinstance(paths, dict) else {}


def _normalize_resource_token(value: str) -> str:
    value = str(value or "").strip().lower().replace("-", "_")
    if not value:
        return "resource"
    value = RESOURCE_SUFFIX_RE.sub("", value).strip("_") or value
    # Conservative singularization keeps /orders and OrderInput aligned without
    # trying to be a full English inflector.
    if value.endswith("ies") and len(value) > 4:
        value = value[:-3] + "y"
    elif value.endswith("s") and len(value) > 3 and not value.endswith("ss"):
        value = value[:-1]
    return value or "resource"


def _resource_aliases(value: str) -> set[str]:
    raw = str(value or "")
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", " ".join(CAMEL_SPLIT_RE.split(raw))) if p]
    aliases = {_normalize_resource_token(raw)}
    if parts:
        aliases.add(_normalize_resource_token(parts[0]))
        aliases.add(_normalize_resource_token(parts[-1]))
        aliases.add(_normalize_resource_token("_".join(parts)))
    return {a for a in aliases if a and a != "resource"}


def _schema_refs(spec: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    components = ((spec.get("components") or {}).get("schemas") or {}) if isinstance(spec, dict) else {}
    if isinstance(components, dict):
        out.extend((str(k), v) for k, v in components.items() if isinstance(v, dict))
    for path, path_item in _paths(spec).items():
        if not isinstance(path_item, dict):
            continue
        for method_l, op in path_item.items():
            if not isinstance(op, dict):
                continue
            op_name = f"{str(method_l).upper()} {path}"
            content = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {})
            schema = content.get("schema") if isinstance(content, dict) else {}
            if isinstance(schema, dict):
                out.append((op_name, schema))
            for resp in ((op.get("responses") or {}).values() if isinstance(op.get("responses"), dict) else []):
                if isinstance(resp, dict):
                    rcontent = (((resp.get("content") or {}).get("application/json") or {}))
                    rschema = rcontent.get("schema") if isinstance(rcontent, dict) else {}
                    if isinstance(rschema, dict):
                        out.append((op_name, rschema))
    return out


def _collect_state_enums_from_schema(schema: dict[str, Any], seen: set[int] | None = None) -> set[str]:
    seen = seen or set()
    if not isinstance(schema, dict) or id(schema) in seen:
        return set()
    seen.add(id(schema))
    states: set[str] = set()
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        if STATE_FIELD_RE.search(str(name)) and isinstance(prop.get("enum"), list):
            states.update(str(x).strip().lower() for x in prop.get("enum") if x not in (None, ""))
        states.update(_collect_state_enums_from_schema(prop, seen))
    if isinstance(schema.get("items"), dict):
        states.update(_collect_state_enums_from_schema(schema.get("items") or {}, seen))
    return states


def _extract_action(text: str) -> str:
    text = str(text or "").lower()
    for verb in ACTION_TARGET_STATE:
        if re.search(rf"(?:^|[^a-z]){re.escape(verb)}(?:[^a-z]|$)", text):
            return verb
    hit = ACTION_VERB_RE.search(text)
    return hit.group(0).lower() if hit else ""


def _resource_from_path(path: str) -> str:
    parts = [p for p in str(path or "").strip("/").split("/") if p]
    parts = [p for p in parts if not p.startswith("{")]
    if not parts:
        return "resource"
    for part in reversed(parts):
        if _extract_action(part):
            continue
        if part.lower() in {"api", "v1", "v2", "v3"}:
            continue
        return _normalize_resource_token(part)
    return _normalize_resource_token(parts[-1])


def _states_from_quote(text: str) -> set[str]:
    lower = str(text or "").lower()
    found = set()
    for state in KNOWN_STATE_LEXICON:
        if re.search(rf"(?:^|[^a-z0-9_]){re.escape(state)}(?:[^a-z0-9_]|$)", lower):
            found.add(state)
    return found


def _normalize_source_refs(probe: dict[str, Any], path: str, action: str) -> list[dict[str, Any]]:
    refs = [r for r in (probe.get("source_refs") or []) if isinstance(r, dict)]
    if refs:
        return refs[:8]
    return [{
        "file": "OpenAPI",
        "section": path,
        "quote": f"OpenAPI exposes write state action `{action}` at `{path}`.",
        "kind": "endpoint_contract",
    }]


def _collect_resource_state_enums(plan: dict[str, Any], spec: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, list[dict[str, Any]]]]:
    states_by_resource: dict[str, set[str]] = {}
    state_refs_by_resource: dict[str, list[dict[str, Any]]] = {}

    def add(resource: str, states: set[str], ref: dict[str, Any]) -> None:
        resource = _normalize_resource_token(resource)
        if not states:
            return
        states_by_resource.setdefault(resource, set()).update(states)
        state_refs_by_resource.setdefault(resource, []).append(ref)

    components = ((spec.get("components") or {}).get("schemas") or {}) if isinstance(spec, dict) else {}
    for name, schema in components.items() if isinstance(components, dict) else []:
        states = _collect_state_enums_from_schema(schema if isinstance(schema, dict) else {})
        for alias in _resource_aliases(str(name)):
            add(alias, states, {"source": "openapi_schema_enum", "schema": str(name), "states": sorted(states)})

    for label, schema in _schema_refs(spec):
        states = _collect_state_enums_from_schema(schema)
        if not states:
            continue
        path = label.split(" ", 1)[1] if " " in label else label
        add(_resource_from_path(path), states, {"source": "openapi_operation_schema_enum", "section": label, "states": sorted(states)})

    for probe in plan.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        path = str((probe.get("endpoint") or {}).get("path") or "")
        resource = _resource_from_path(path)
        for ref in (probe.get("source_refs") or []):
            if not isinstance(ref, dict):
                continue
            states = _states_from_quote(str(ref.get("quote") or ""))
            add(resource, states, {"source": "source_quote_state", "section": ref.get("section") or path, "states": sorted(states)})

    # If there is exactly one write resource and schemas were too generic to map,
    # allow globally collected schema enums to attach to that resource.  This is
    # still customer-grounded and avoids the old hard-coded fallback.
    write_resources = {
        _resource_from_path(str((p.get("endpoint") or {}).get("path") or ""))
        for p in (plan.get("probes") or [])
        if isinstance(p, dict) and str((p.get("endpoint") or {}).get("method") or "").upper() in WRITE_METHODS
    }
    global_schema_states: set[str] = set()
    for _, schema in _schema_refs(spec):
        global_schema_states.update(_collect_state_enums_from_schema(schema))
    if len(write_resources) == 1 and global_schema_states:
        add(next(iter(write_resources)), global_schema_states, {"source": "single_resource_openapi_schema_enum", "states": sorted(global_schema_states)})

    return states_by_resource, state_refs_by_resource


def _operation_from_probe(probe: dict[str, Any]) -> dict[str, Any] | None:
    ep = probe.get("endpoint") or {}
    method = str(ep.get("method") or "").upper()
    path = str(ep.get("path") or "")
    action = _extract_action(" ".join([path, str(probe.get("risk_type") or ""), str((probe.get("probe_plan") or {}).get("action") or "")]))
    if method in WRITE_METHODS and action:
        return {
            "method": method,
            "path": path,
            "action": action,
            "resource": _resource_from_path(path),
            "source_refs": _normalize_source_refs(probe, path, action),
        }
    return None


def _operation_from_openapi(path: str, method: str, op: dict[str, Any], plan_probe_by_endpoint: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if method not in WRITE_METHODS or not isinstance(op, dict):
        return None
    text = " ".join([path, str(op.get("operationId") or ""), str(op.get("summary") or ""), str(op.get("description") or "")])
    action = _extract_action(text)
    if not action:
        return None
    key = f"{method} {path}"
    probe = plan_probe_by_endpoint.get(key, {})
    return {
        "method": method,
        "path": path,
        "action": action,
        "resource": _resource_from_path(path),
        "source_refs": _normalize_source_refs(probe, path, action),
    }


def discover_business_state_machines(plan: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = spec or {}
    states_by_resource, state_refs_by_resource = _collect_resource_state_enums(plan, spec)

    operations: list[dict[str, Any]] = []
    plan_probe_by_endpoint: dict[str, dict[str, Any]] = {}
    for probe in plan.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        ep = probe.get("endpoint") or {}
        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        plan_probe_by_endpoint[f"{method} {path}"] = probe
        op = _operation_from_probe(probe)
        if op:
            operations.append(op)

    for path, ops in _paths(spec).items():
        if not isinstance(ops, dict):
            continue
        for method_l, op in ops.items():
            operation = _operation_from_openapi(path, str(method_l).upper(), op, plan_probe_by_endpoint)
            if operation:
                operations.append(operation)

    deduped_ops: list[dict[str, Any]] = []
    seen_ops: set[tuple[str, str, str]] = set()
    for op in operations:
        key = (str(op.get("method")), str(op.get("path")), str(op.get("action")))
        if key in seen_ops:
            continue
        seen_ops.add(key)
        deduped_ops.append(op)

    machines: dict[str, dict[str, Any]] = {}
    for op in deduped_ops:
        res = str(op.get("resource") or "resource")
        states = set(states_by_resource.get(res) or set())
        if not states:
            # V3 hardening: no customer-grounded states for this resource means
            # no state-machine inference for this operation.  Do not fall back to
            # generic created/submitted/rejected/paid templates.
            continue
        target_hint = ACTION_TARGET_STATE.get(str(op.get("action") or ""), "")
        target_state = target_hint if target_hint in states else ""
        op = {**op, "target_state": target_state, "target_state_hint": target_hint, "target_state_grounded": bool(target_state)}
        terminals = sorted(states & TERMINAL_STATES)
        if not terminals:
            continue
        machine = machines.setdefault(res, {
            "resource": res,
            "states": sorted(states),
            "terminal_states": terminals,
            "actions": [],
            "state_grounding_refs": state_refs_by_resource.get(res, [])[:8],
        })
        machine["actions"].append(op)

    return {
        "engine": "business_state_machine_explorer_v3_phase94a_grounded",
        "state_machine_count": len(machines),
        "operation_count": sum(len(m.get("actions") or []) for m in machines.values()),
        "global_states": sorted({s for states in states_by_resource.values() for s in states}),
        "terminal_states": sorted({s for states in states_by_resource.values() for s in (states & TERMINAL_STATES)}),
        "state_machines": list(machines.values()),
        "grounding_policy": {
            "no_default_state_fallback": True,
            "require_customer_grounded_state": True,
            "require_customer_grounded_action_endpoint": True,
            "target_state_is_hint_unless_in_customer_state_enum": True,
        },
    }


def _probe_key(probe: dict[str, Any]) -> tuple[str, str, str, str]:
    ep = probe.get("endpoint") or {}
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    illegal = plan.get("illegal_transition") if isinstance(plan.get("illegal_transition"), dict) else {}
    return (str(probe.get("risk_type") or ""), str(ep.get("method") or ""), str(ep.get("path") or ""), str(illegal.get("from_state") or "") + "->" + str(illegal.get("attempt_action") or ""))


def generate_illegal_state_path_probes(plan: dict[str, Any], spec: dict[str, Any] | None = None, *, max_probes: int = 40) -> dict[str, Any]:
    discovery = discover_business_state_machines(plan, spec)
    existing_keys = {_probe_key(p) for p in (plan.get("probes") or []) if isinstance(p, dict)}
    probes: list[dict[str, Any]] = []
    counter = 1
    for machine in discovery.get("state_machines") or []:
        terminals = [str(s) for s in (machine.get("terminal_states") or [])]
        actions = [a for a in (machine.get("actions") or []) if isinstance(a, dict)]
        if not actions or not terminals:
            continue
        for action_op in actions:
            action = str(action_op.get("action") or "")
            if action in {"cancel", "reject", "close", "archive"}:
                priority = "P1"
            else:
                priority = "P0" if action in {"pay", "approve", "ship", "complete", "refund", "reopen"} else "P1"
            for terminal in terminals[:4]:
                if action_op.get("target_state_grounded") and terminal == str(action_op.get("target_state") or ""):
                    continue
                probe = {
                    "candidate_id": f"QBSM-94A-{counter:04d}",
                    "risk_type": "state_transition_probe",
                    "endpoint": {"method": action_op.get("method"), "path": action_op.get("path")},
                    "execution_policy": "disposable_sandbox_required",
                    "probe_plan": {
                        "phase": "94A",
                        "phase_version": "v3_grounded",
                        "strategy": "illegal_terminal_state_transition_runtime_probe",
                        "state_machine_resource": machine.get("resource"),
                        "illegal_transition": {
                            "from_state": terminal,
                            "attempt_action": action,
                            "attempt_target_state": action_op.get("target_state") or None,
                            "attempt_target_state_hint": action_op.get("target_state_hint") or None,
                            "attempt_target_state_grounded": bool(action_op.get("target_state_grounded")),
                        },
                        "terminal_states": terminals,
                        "customer_grounded_states": machine.get("states") or [],
                        "state_grounding_refs": machine.get("state_grounding_refs") or [],
                        "expected_status": NEGATIVE_EXPECTED,
                        "bug_discovery_value": priority,
                    },
                    "required_evidence": ["before_after_snapshot", "observer_graph_delta", "state_transition_rejection_or_no_side_effect"],
                    "source_refs": action_op.get("source_refs") or [],
                    "grounding_basis": {
                        "endpoint_contract_refs": 1,
                        "supporting_requirement_refs": 1,
                        "phase94a_state_machine_inference": 1,
                        "phase94a_customer_state_refs": len(machine.get("state_grounding_refs") or []),
                        "phase94a_no_default_state_fallback": 1,
                    },
                }
                key = _probe_key(probe)
                if key in existing_keys:
                    continue
                probes.append(probe)
                existing_keys.add(key)
                counter += 1
                if len(probes) >= max_probes:
                    break
            if len(probes) >= max_probes:
                break
        if len(probes) >= max_probes:
            break
    by_value: dict[str, int] = {}
    for p in probes:
        val = str(((p.get("probe_plan") or {}).get("bug_discovery_value") or "P1"))
        by_value[val] = by_value.get(val, 0) + 1
    return {
        "engine": "illegal_state_path_probe_generator_v3_phase94a_grounded",
        "state_machine_discovery": discovery,
        "generated_probe_count": len(probes),
        "generated_by_bug_value": by_value,
        "probes": probes,
        "improvement_claim": {
            "baseline_probe_count": len(plan.get("probes") or []),
            "expanded_probe_count": len(plan.get("probes") or []) + len(probes),
            "added_high_value_state_transition_probe_count": len(probes),
            "dead_rule_fallback_removed": True,
        },
    }
