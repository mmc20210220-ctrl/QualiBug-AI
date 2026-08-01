"""Public source-authoritative process-graph async wait authority.

The bounded polling compiler/runtime remains in
``process_graph_wait_contract_core``. This facade freezes exact observer
transport and optionally attaches source-declared message/callback semantics to
that same wait. Direct broker plugins are admitted only through a compile-frozen
read-only adapter receipt; they reuse the same bounded event scheduler and never
own a second consumer loop, ledger, Oracle, or finalizer.

Runtime accepts only a list/index/runtime triple whose content fingerprints,
edge identity, event transition fingerprints, and optional broker adapter
fingerprints all still match.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_broker_adapter as _adapter
from . import process_graph_event_transition as _event
from . import process_graph_wait_contract_core as _core
from .real_id_resolver import normalize_path_placeholders


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

EVENT_TRANSITION_INVALID = _event.EVENT_TRANSITION_INVALID
EVENT_CORRELATION_UNRESOLVED = _event.EVENT_CORRELATION_UNRESOLVED
EVENT_OBSERVATION_INCOMPLETE = _event.EVENT_OBSERVATION_INCOMPLETE
EVENT_DELIVERY_COUNT_BELOW_MINIMUM = _event.EVENT_DELIVERY_COUNT_BELOW_MINIMUM
EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM = _event.EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM
EVENT_ID_REUSE_CONFLICT = _event.EVENT_ID_REUSE_CONFLICT
EVENT_IDEMPOTENCY_KEY_MISMATCH = _event.EVENT_IDEMPOTENCY_KEY_MISMATCH
EVENT_CORRELATION_IDENTITY_MISMATCH = (
    _event.EVENT_CORRELATION_IDENTITY_MISMATCH
)
EVENT_IDENTITY_TYPE_CONFLICT = _event.EVENT_IDENTITY_TYPE_CONFLICT
EVENT_RETRY_LIMIT_EXCEEDED = _event.EVENT_RETRY_LIMIT_EXCEEDED
BROKER_ADAPTER_INVALID = _adapter.BROKER_ADAPTER_INVALID
BROKER_ADAPTER_UNAVAILABLE = _adapter.BROKER_ADAPTER_UNAVAILABLE
BROKER_ADAPTER_RECEIPT_INVALID = _adapter.BROKER_ADAPTER_RECEIPT_INVALID
BROKER_ADAPTER_CAPABILITY_MISSING = _adapter.BROKER_ADAPTER_CAPABILITY_MISSING
BROKER_ADAPTER_SCOPE_MISMATCH = _adapter.BROKER_ADAPTER_SCOPE_MISMATCH
BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION = (
    _adapter.BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION
)
BROKER_ADAPTER_RECORD_LIMIT_EXCEEDED = (
    _adapter.BROKER_ADAPTER_RECORD_LIMIT_EXCEEDED
)
BROKER_ADAPTER_RECORD_INVALID = _adapter.BROKER_ADAPTER_RECORD_INVALID

_ADAPTER_ACTOR_REF = "__qualibug_broker_adapter_actor__"
_ADAPTER_QUERY_PARAMETER = "__correlation"
_ADAPTER_BRIDGE_PREFIX = "__qualibug_broker_adapter__"
_ADAPTER_BRIDGE_PATH = "/__qualibug_internal__/broker-adapter"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _operations(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _nodes(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("node_id") or row.get("step_id")): dict(row)
        for row in _list(_dict(graph).get("nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id") or row.get("step_id"))
    }


def _operation_system(operation: dict[str, Any]) -> str:
    return _text(
        operation.get("system_ref")
        or operation.get("target_system_ref")
        or operation.get("service_ref")
    )


def _wait_id(raw: dict[str, Any], index: int) -> str:
    return _text(raw.get("wait_id") or raw.get("contract_id")) or f"wait_{index + 1}"


def _edge_scope(
    graph: dict[str, Any],
    *,
    source_node_id: str,
    target_node_id: str,
) -> tuple[dict[str, Any], str]:
    matches = [
        deepcopy(_dict(edge))
        for edge in _list(graph.get("edges"))
        if isinstance(edge, dict)
        and _text(edge.get("source_node_id")) == source_node_id
        and _text(edge.get("target_node_id")) == target_node_id
    ]
    if len(matches) != 1:
        return {}, (
            "event_edge_identity_ambiguous:"
            f"{source_node_id}->{target_node_id}:count={len(matches)}"
        )
    edge = matches[0]
    if not _text(edge.get("edge_id")) or not _text(edge.get("relation_type")):
        return {}, "event_edge_identity_incomplete"
    return edge, ""



_EVENT_CORRELATION_BINDING_SCHEMA = (
    "qualibug.process-graph-event-correlation-binding.v1"
)


def _binding_source_field(row: dict[str, Any]) -> str:
    return _text(
        row.get("producer_output_field")
        or row.get("source_field")
        or row.get("canonical_field_id")
        or row.get("output_field")
        or row.get("field")
    )


def _binding_target(row: dict[str, Any]) -> str:
    return _text(
        row.get("target")
        or row.get("consumer_target")
        or row.get("target_location")
        or _binding_source_field(row)
    )


def _node_output_fields(node: dict[str, Any]) -> set[str]:
    return {
        _binding_source_field(_dict(row))
        for row in _list(node.get("output_binding_specs"))
        if isinstance(row, dict) and _binding_source_field(_dict(row))
    }


def _node_input_binding_rows(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _dict(row)
        for row in _list(node.get("input_binding_refs"))
        if isinstance(row, dict)
    ]


def _correlation_binding_proof(
    *,
    graph: dict[str, Any],
    edge: dict[str, Any],
    event_contract: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Bind event correlation to one exact executable data handoff.

    Runtime bindings are graph-scoped and may contain same-named values from
    fixtures or other branches.  A cross-system event may therefore use a
    binding only when the source node produces it and the selected edge hands
    that exact field to the target node.  No name/path inference is allowed.
    """
    source_node_id = _text(event_contract.get("source_node_id"))
    target_node_id = _text(event_contract.get("target_node_id"))
    declared = _text(event_contract.get("correlation_binding"))
    nodes = _nodes(graph)
    source_node = _dict(nodes.get(source_node_id))
    target_node = _dict(nodes.get(target_node_id))
    if not source_node or not target_node:
        return {}, "event_correlation_node_scope_unresolved"

    candidates: list[tuple[dict[str, Any], str, str]] = []
    for raw in _list(edge.get("binding_refs")):
        if not isinstance(raw, dict):
            continue
        row = _dict(raw)
        producer = _text(
            row.get("producer_node_id")
            or row.get("source_node_id")
            or source_node_id
        )
        consumer = _text(
            row.get("consumer_node_id")
            or row.get("target_node_id")
            or target_node_id
        )
        source_field = _binding_source_field(row)
        target = _binding_target(row)
        if (
            producer == source_node_id
            and consumer == target_node_id
            and source_field
            and target
            and declared in {source_field, target}
        ):
            candidates.append((row, source_field, target))
    if not candidates:
        return {}, f"event_correlation_handoff_unresolved:{declared}"
    if len(candidates) != 1:
        return {}, (
            "event_correlation_handoff_ambiguous:"
            f"{declared}:count={len(candidates)}"
        )
    _binding, source_field, consumer_target = candidates[0]

    if source_field not in _node_output_fields(source_node):
        return {}, (
            "event_correlation_producer_output_unresolved:"
            f"{source_node_id}.{source_field}"
        )

    matching_inputs = [
        row
        for row in _node_input_binding_rows(target_node)
        if _text(
            row.get("producer_node_id")
            or row.get("source_node_id")
            or row.get("producer_step_id")
        )
        == source_node_id
        and _binding_source_field(row) == source_field
        and _binding_target(row) == consumer_target
    ]
    if len(matching_inputs) != 1:
        return {}, (
            "event_correlation_consumer_input_unresolved:"
            f"{target_node_id}:{source_field}->{consumer_target}:"
            f"count={len(matching_inputs)}"
        )

    payload = {
        "schema_version": _EVENT_CORRELATION_BINDING_SCHEMA,
        "edge_id": _text(edge.get("edge_id")),
        "producer_node_id": source_node_id,
        "consumer_node_id": target_node_id,
        "producer_output_field": source_field,
        "consumer_target": consumer_target,
        "source_system_ref": _text(
            edge.get("source_system_ref") or source_node.get("system_ref")
        ),
        "target_system_ref": _text(
            edge.get("target_system_ref") or target_node.get("system_ref")
        ),
    }
    payload["contract_fingerprint"] = _core._fingerprint(payload)
    return payload, ""


def _correlation_binding_proof_valid(
    *,
    graph: dict[str, Any],
    edge: dict[str, Any],
    event_contract: dict[str, Any],
) -> bool:
    proof = deepcopy(_dict(event_contract.get("correlation_binding_contract")))
    attached = _text(proof.pop("contract_fingerprint", ""))
    if (
        not attached
        or attached != _core._fingerprint(proof)
        or _text(proof.get("schema_version"))
        != _EVENT_CORRELATION_BINDING_SCHEMA
        or _text(event_contract.get("correlation_binding"))
        != _text(proof.get("consumer_target"))
    ):
        return False
    expected, error = _correlation_binding_proof(
        graph=graph,
        edge=edge,
        event_contract={
            **event_contract,
            "correlation_binding": _text(
                event_contract.get("declared_correlation_binding")
                or event_contract.get("correlation_binding")
            ),
        },
    )
    return bool(
        not error
        and _text(expected.get("contract_fingerprint")) == attached
        and expected == _dict(
            event_contract.get("correlation_binding_contract")
        )
    )


def _fingerprint_valid(row: dict[str, Any]) -> bool:
    value = deepcopy(_dict(row))
    attached = _text(value.pop("contract_fingerprint", ""))
    return bool(attached and attached == _core._fingerprint(value))


def _event_fingerprint_valid(contract: dict[str, Any]) -> bool:
    value = deepcopy(_dict(contract))
    attached = _text(value.pop("contract_fingerprint", ""))
    return bool(attached and attached == _event._fingerprint(value))


def _base_wait_fingerprint(wait: dict[str, Any]) -> str:
    value = deepcopy(_dict(wait))
    value.pop("contract_fingerprint", None)
    value.pop("transition_kind", None)
    value.pop("event_transition_contract", None)
    return _core._fingerprint(value)


def _direct_adapter_contract(event: dict[str, Any]) -> dict[str, Any]:
    return _dict(event.get("broker_read_adapter_contract"))


def _validate_event_wait_runtime(
    graph: dict[str, Any],
    wait: dict[str, Any],
) -> str:
    event = _dict(wait.get("event_transition_contract"))
    if not event:
        return "event_transition_contract_missing"
    if (
        _text(event.get("schema_version")) != _event.CONTRACT_SCHEMA_VERSION
        or _text(event.get("status")) != _event.STATUS_COMPILED
        or not _text(event.get("edge_id"))
        or not _text(event.get("wait_contract_fingerprint"))
        or not _list(event.get("source_refs"))
        or not _event_fingerprint_valid(event)
    ):
        return "event_transition_contract_drift"
    adapter_contract = _direct_adapter_contract(event)
    if adapter_contract:
        if (
            not _adapter.contract_fingerprint_valid(adapter_contract)
            or _text(wait.get("observer_transport_kind")) != "broker_adapter"
            or _text(wait.get("observer_adapter_ref"))
            != _text(adapter_contract.get("adapter_ref"))
            or _text(event.get("observer_transport_kind")) != "broker_adapter"
        ):
            return "broker_adapter_contract_drift"
    elif _text(wait.get("observer_transport_kind")) == "broker_adapter":
        return "broker_adapter_contract_missing"
    if _text(event.get("wait_contract_fingerprint")) != _base_wait_fingerprint(wait):
        return "event_base_wait_contract_drift"
    edge, error = _edge_scope(
        graph,
        source_node_id=_text(wait.get("source_node_id")),
        target_node_id=_text(wait.get("target_node_id")),
    )
    if error:
        return error
    if (
        _text(event.get("edge_id")) != _text(edge.get("edge_id"))
        or _text(event.get("relation_type"))
        != _text(edge.get("relation_type")).upper()
    ):
        return "event_edge_contract_drift"
    if not _correlation_binding_proof_valid(
        graph=graph,
        edge=edge,
        event_contract=event,
    ):
        return "event_correlation_binding_contract_drift"
    return ""


def _raw_adapter_spec(event_spec: dict[str, Any]) -> dict[str, Any]:
    return _dict(
        event_spec.get("direct_broker_adapter")
        or event_spec.get("broker_read_adapter")
        or event_spec.get("broker_adapter")
    )


def _bridge_operation(
    *,
    wait_id: str,
    target_node: dict[str, Any],
    event_spec: dict[str, Any],
) -> dict[str, Any]:
    adapter_ref = _adapter.direct_broker_adapter_ref(event_spec)
    return {
        "id": f"{_ADAPTER_BRIDGE_PREFIX}:{wait_id}",
        "method": "GET",
        "path": f"{_ADAPTER_BRIDGE_PATH}/{_core._fingerprint(wait_id)[:16]}",
        "system_ref": _text(target_node.get("system_ref")),
        "read_write": "read",
        "internal_transport_kind": "broker_adapter_compile_bridge",
        "adapter_ref": adapter_ref,
        "source_refs": _list(_raw_adapter_spec(event_spec).get("source_refs")),
    }


def _prepare_direct_adapter_wait(
    raw: dict[str, Any],
    *,
    wait_id: str,
    target_node: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    event_spec = _event._event_spec(raw)
    if not _adapter.has_direct_broker_adapter(event_spec):
        return raw, {}, ""
    if any(
        _text(raw.get(name))
        for name in (
            "observer_operation_ref",
            "read_operation_ref",
            "operation_ref",
            "method",
            "path",
            "path_template",
        )
    ):
        return raw, {}, "direct_broker_adapter_and_http_observer_are_ambiguous"
    if not _adapter.direct_broker_adapter_ref(event_spec):
        return raw, {}, "direct_broker_adapter_ref_missing"

    prepared = deepcopy(raw)
    bridge = _bridge_operation(
        wait_id=wait_id,
        target_node=target_node,
        event_spec=event_spec,
    )
    prepared["observer_operation_ref"] = bridge["id"]
    prepared["method"] = bridge["method"]
    prepared["path"] = bridge["path"]
    prepared["predicate"] = {"status_codes": [200]}
    event_for_compile = deepcopy(event_spec)
    event_for_compile["correlation_query_parameter"] = (
        _text(event_for_compile.get("correlation_query_parameter"))
        or _ADAPTER_QUERY_PARAMETER
    )
    prepared["event_transition"] = event_for_compile
    prepared.pop("event_contract", None)
    prepared.pop("delivery_contract", None)
    return prepared, bridge, ""


def _make_wait_adapter_truthful(
    wait: dict[str, Any],
    adapter_contract: dict[str, Any],
) -> str:
    wait["observer_transport_kind"] = "broker_adapter"
    wait["observer_adapter_ref"] = _text(adapter_contract.get("adapter_ref"))
    wait["observer_operation_ref"] = ""
    wait["method"] = ""
    wait["path_template"] = ""
    wait["predicate"] = {}
    wait.pop("contract_fingerprint", None)
    wait["contract_fingerprint"] = _core._fingerprint(wait)
    return wait["contract_fingerprint"]


def _make_event_adapter_truthful(
    event_contract: dict[str, Any],
    adapter_contract: dict[str, Any],
) -> None:
    event_contract["observer_transport_kind"] = "broker_adapter"
    event_contract["broker_read_adapter_contract"] = deepcopy(adapter_contract)
    event_contract["observer_operation_ref"] = ""
    event_contract["observer_method"] = ""
    event_contract["observer_path_template"] = ""
    event_contract["actor_ref"] = ""
    event_contract["correlation_query_parameter"] = ""


def compile_process_graph_wait_contracts(
    graph: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze HTTP or direct-broker observation into one bounded wait gate."""
    source = deepcopy(_dict(graph))
    compile_ir = deepcopy(_dict(behavior_ir))
    compile_ir["operations"] = [
        deepcopy(row)
        for row in _list(compile_ir.get("operations"))
        if isinstance(row, dict)
    ]
    operations = _operations(compile_ir)
    nodes = _nodes(source)
    issues: list[str] = []
    raw_by_wait_id: dict[str, dict[str, Any]] = {}
    adapter_event_specs: dict[str, dict[str, Any]] = {}

    for index, raw_value in enumerate(_list(source.get("wait_contracts"))):
        raw = deepcopy(_dict(raw_value))
        wait_id = _wait_id(raw, index)
        raw["wait_id"] = wait_id
        target_node_id = _text(
            raw.get("target_node_id")
            or raw.get("before_node_id")
            or raw.get("consumer_node_id")
        )
        target_node = _dict(nodes.get(target_node_id))
        original_event_spec = _event._event_spec(raw)
        prepared, bridge, adapter_error = _prepare_direct_adapter_wait(
            raw,
            wait_id=wait_id,
            target_node=target_node,
        )
        if adapter_error:
            issues.append(f"{wait_id}:{adapter_error}")
            source["wait_contracts"][index] = raw
            raw_by_wait_id[wait_id] = deepcopy(raw)
            continue
        raw = prepared
        if bridge:
            compile_ir["operations"].append(bridge)
            operations[bridge["id"]] = deepcopy(bridge)
            adapter_event_specs[wait_id] = original_event_spec

        observer_ref = _text(
            raw.get("observer_operation_ref")
            or raw.get("read_operation_ref")
            or raw.get("operation_ref")
        )
        operation = _dict(operations.get(observer_ref))
        if operation:
            declared_method = _text(operation.get("method")).upper()
            requested_method = _text(raw.get("method")).upper()
            if requested_method and requested_method != declared_method:
                issues.append(
                    f"{wait_id}:observer_method_override_forbidden:"
                    f"{requested_method}!={declared_method}"
                )
            declared_path = normalize_path_placeholders(
                _text(
                    operation.get("path")
                    or operation.get("raw_path")
                    or operation.get("path_template")
                )
            )
            requested_path = normalize_path_placeholders(
                _text(raw.get("path") or raw.get("path_template"))
            )
            if requested_path and requested_path != declared_path:
                issues.append(
                    f"{wait_id}:observer_path_override_forbidden:"
                    f"{requested_path}!={declared_path}"
                )
            target_system = _text(target_node.get("system_ref"))
            operation_system = _operation_system(operation)
            if target_system:
                if not operation_system:
                    issues.append(
                        f"{wait_id}:observer_operation_system_unbound:"
                        f"target={target_system}"
                    )
                elif operation_system != target_system:
                    issues.append(
                        f"{wait_id}:observer_operation_system_mismatch:"
                        f"{operation_system}!={target_system}"
                    )
            elif operation_system:
                issues.append(
                    f"{wait_id}:observer_operation_system_mismatch:"
                    f"{operation_system}!=primary"
                )
            raw["method"] = declared_method
            raw["path"] = declared_path
            raw.pop("path_template", None)
        source["wait_contracts"][index] = raw
        raw_by_wait_id[wait_id] = deepcopy(raw)

    if issues:
        return {
            "status": _core.STATUS_BLOCKED,
            "reason_code": _core.WAIT_CONTRACT_INVALID,
            "detail": ";".join(issues[:16]),
            "issues": issues,
        }

    result = _core.compile_process_graph_wait_contracts(source, behavior_ir=compile_ir)
    if _text(result.get("status")) != _core.STATUS_COMPILED:
        return result

    compiled_graph = deepcopy(_dict(result.get("graph")))
    compiled_waits = [
        deepcopy(row)
        for row in _list(compiled_graph.get("wait_contracts"))
        if isinstance(row, dict)
    ]
    event_fingerprints: list[str] = []
    adapter_fingerprints: list[str] = []
    event_issues: list[str] = []
    for wait in compiled_waits:
        wait_id = _text(wait.get("wait_id"))
        raw = _dict(raw_by_wait_id.get(wait_id))
        if not _event.has_event_transition(raw):
            continue
        event_spec = _event._event_spec(raw)
        event_source_refs = [
            deepcopy(value)
            for value in _list(event_spec.get("source_refs"))
            if isinstance(value, dict)
        ]
        if not event_source_refs:
            event_issues.append(f"{wait_id}:event_source_refs_missing")
            continue
        edge, edge_error = _edge_scope(
            compiled_graph,
            source_node_id=_text(wait.get("source_node_id")),
            target_node_id=_text(wait.get("target_node_id")),
        )
        if edge_error:
            event_issues.append(f"{wait_id}:{edge_error}")
            continue
        event_contract, event_error = _event.compile_event_transition_contract(
            raw_wait=raw,
            compiled_wait=wait,
            relation_type=_text(edge.get("relation_type")),
        )
        if event_error:
            event_issues.append(f"{wait_id}:{event_error}")
            continue
        declared_correlation_binding = _text(
            event_contract.get("correlation_binding")
        )
        correlation_proof, correlation_error = _correlation_binding_proof(
            graph=compiled_graph,
            edge=edge,
            event_contract=event_contract,
        )
        if correlation_error:
            event_issues.append(f"{wait_id}:{correlation_error}")
            continue
        event_contract["declared_correlation_binding"] = (
            declared_correlation_binding
        )
        event_contract["correlation_binding"] = _text(
            correlation_proof.get("consumer_target")
        )
        event_contract["correlation_binding_contract"] = correlation_proof

        original_adapter_event = _dict(adapter_event_specs.get(wait_id))
        if original_adapter_event:
            adapter_contract, adapter_error = (
                _adapter.compile_broker_read_adapter_contract(
                    original_adapter_event,
                    broker_contract=_dict(
                        event_contract.get("broker_delivery_contract")
                    ),
                )
            )
            if adapter_error or not adapter_contract:
                event_issues.append(
                    f"{wait_id}:broker_adapter_invalid:"
                    f"{adapter_error or 'adapter_contract_missing'}"
                )
                continue
            base_wait_fingerprint = _make_wait_adapter_truthful(
                wait,
                adapter_contract,
            )
            _make_event_adapter_truthful(event_contract, adapter_contract)
            adapter_fingerprints.append(
                _text(adapter_contract.get("contract_fingerprint"))
            )
        else:
            base_wait_fingerprint = _text(wait.get("contract_fingerprint"))

        event_contract.update(
            {
                "edge_id": _text(edge.get("edge_id")),
                "wait_contract_fingerprint": base_wait_fingerprint,
                "source_refs": event_source_refs,
            }
        )
        event_contract.pop("contract_fingerprint", None)
        event_contract["contract_fingerprint"] = _event._fingerprint(event_contract)
        wait["transition_kind"] = "event_delivery"
        wait["event_transition_contract"] = event_contract
        wait.pop("contract_fingerprint", None)
        wait["contract_fingerprint"] = _core._fingerprint(wait)
        event_fingerprints.append(event_contract["contract_fingerprint"])

    if event_issues:
        return {
            "status": _core.STATUS_BLOCKED,
            "reason_code": EVENT_TRANSITION_INVALID,
            "detail": ";".join(event_issues[:16]),
            "issues": event_issues,
        }

    compiled_graph["wait_contracts"] = compiled_waits
    compiled_graph["wait_contracts_by_target"] = {
        _text(row.get("target_node_id")): deepcopy(row) for row in compiled_waits
    }
    runtime = deepcopy(_dict(compiled_graph.get("wait_runtime_contract")))
    runtime.update(
        {
            "contract_fingerprints": [
                _text(row.get("contract_fingerprint")) for row in compiled_waits
            ],
            "event_transition_count": len(event_fingerprints),
            "event_transition_fingerprints": event_fingerprints,
            "broker_adapter_count": len(adapter_fingerprints),
            "broker_adapter_fingerprints": adapter_fingerprints,
        }
    )
    compiled_graph["wait_runtime_contract"] = runtime
    return {**result, "graph": compiled_graph, "wait_contracts": compiled_waits}


def compiled_wait_runtime_ready(graph: dict[str, Any]) -> tuple[bool, str]:
    ready, detail = _core.compiled_wait_runtime_ready(graph)
    if not ready:
        return ready, detail
    source = _dict(graph)
    waits = [
        _dict(row) for row in _list(source.get("wait_contracts")) if isinstance(row, dict)
    ]
    by_target = _dict(source.get("wait_contracts_by_target"))
    expected_targets = [_text(row.get("target_node_id")) for row in waits]
    if set(by_target) != set(expected_targets):
        return False, "wait_contract_target_index_scope_mismatch"
    for wait in waits:
        target = _text(wait.get("target_node_id"))
        if _dict(by_target.get(target)) != wait:
            return False, "wait_contract_target_index_content_mismatch"
        if not _fingerprint_valid(wait):
            return False, "wait_contract_fingerprint_drift"
        if _text(wait.get("transition_kind")) == "event_delivery":
            event_error = _validate_event_wait_runtime(source, wait)
            if event_error:
                return False, event_error

    runtime = _dict(source.get("wait_runtime_contract"))
    attached_wait_fingerprints = [
        _text(row.get("contract_fingerprint")) for row in waits
    ]
    if list(runtime.get("contract_fingerprints") or []) != attached_wait_fingerprints:
        return False, "wait_runtime_contract_fingerprint_scope_mismatch"
    event_contracts = [
        _dict(row.get("event_transition_contract"))
        for row in waits
        if _text(row.get("transition_kind")) == "event_delivery"
    ]
    if int(runtime.get("event_transition_count") or 0) != len(event_contracts):
        return False, "event_transition_count_mismatch"
    if list(runtime.get("event_transition_fingerprints") or []) != [
        _text(row.get("contract_fingerprint")) for row in event_contracts
    ]:
        return False, "event_transition_fingerprint_scope_mismatch"
    adapter_contracts = [
        _direct_adapter_contract(event)
        for event in event_contracts
        if _direct_adapter_contract(event)
    ]
    if int(runtime.get("broker_adapter_count") or 0) != len(adapter_contracts):
        return False, "broker_adapter_count_mismatch"
    if list(runtime.get("broker_adapter_fingerprints") or []) != [
        _text(row.get("contract_fingerprint")) for row in adapter_contracts
    ]:
        return False, "broker_adapter_fingerprint_scope_mismatch"
    return True, ""


def _aggregate_adapter_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "observation_count": len(rows),
        "record_count": 0,
        "checkpoint_receipt_count": 0,
        "delivery_confirmation_receipt_count": 0,
        "dlq_receipt_count": 0,
        "rebalance_receipt_count": 0,
        "ack_count": 0,
        "nack_count": 0,
        "requeue_count": 0,
    }
    fingerprint_fields = (
        "adapter_receipt_fingerprint",
        "capability_fingerprints",
        "record_fingerprints",
        "checkpoint_receipt_fingerprints",
        "delivery_confirmation_receipt_fingerprints",
        "dlq_receipt_fingerprints",
        "rebalance_receipt_fingerprints",
    )
    for field in fingerprint_fields:
        result[field] = []
    for row in rows:
        for field in (
            "record_count",
            "checkpoint_receipt_count",
            "delivery_confirmation_receipt_count",
            "dlq_receipt_count",
            "rebalance_receipt_count",
            "ack_count",
            "nack_count",
            "requeue_count",
        ):
            result[field] += int(row.get(field) or 0)
        for field in fingerprint_fields:
            value = row.get(field)
            if isinstance(value, list):
                result[field].extend(_text(item) for item in value if _text(item))
            elif _text(value):
                result[field].append(_text(value))
    for field in fingerprint_fields:
        result[field] = sorted(set(result[field]))
    if rows:
        first = rows[0]
        result.update(
            {
                "adapter_contract_fingerprint": _text(
                    first.get("adapter_contract_fingerprint")
                ),
                "adapter_kind": _text(first.get("adapter_kind")),
                "adapter_ref_fingerprint": _text(
                    first.get("adapter_ref_fingerprint")
                ),
                "runtime_capability_ref_fingerprint": _text(
                    first.get("runtime_capability_ref_fingerprint")
                ),
            }
        )
    return result


def _adapter_receipt_projection(
    receipt: dict[str, Any],
    *,
    adapter_contract: dict[str, Any],
    evidence: list[dict[str, Any]],
    errors: list[tuple[str, str]],
) -> dict[str, Any]:
    result = deepcopy(receipt)
    reason_codes = list(
        dict.fromkeys(_text(reason) for reason, _ in errors if _text(reason))
    )
    if reason_codes:
        result.update(
            {
                "status": _event.STATUS_BLOCKED,
                "semantic_status": "INDETERMINATE",
                "reason_code": reason_codes[0],
                "semantic_reason_codes": reason_codes,
                "coverage_complete": False,
                "converged": False,
                "timed_out": True,
            }
        )
    result.update(
        {
            "observer_transport_kind": "broker_adapter",
            "observer_operation_ref": "",
            "observer_method": "",
            "observer_path_template": "",
            "actor_ref": "",
            "broker_adapter_contract_fingerprint": _text(
                adapter_contract.get("contract_fingerprint")
            ),
            "broker_adapter_status": (
                "INDETERMINATE" if reason_codes else "OBSERVED"
            ),
            "broker_adapter_reason_codes": reason_codes,
            "broker_adapter_error_fingerprints": sorted(
                _event._fingerprint({"reason": reason, "detail": detail})
                for reason, detail in errors
            ),
            "broker_adapter_evidence": _aggregate_adapter_evidence(evidence),
        }
    )
    result["receipt_id"] = "event_wait_" + _event._fingerprint(result)[:24]
    return result


def _execute_direct_broker_event(
    *,
    event_contract: dict[str, Any],
    context: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    read_once: Any,
    sleep: Any,
    monotonic: Any,
) -> dict[str, Any]:
    adapter_contract = _direct_adapter_contract(event_contract)
    reader = read_once or _dict(context).get("broker_read_once")
    if not callable(reader):
        blocked = _event._blocked_receipt(
            event_contract,
            BROKER_ADAPTER_UNAVAILABLE,
            detail="broker_read_once_unavailable",
        )
        return _adapter_receipt_projection(
            blocked,
            adapter_contract=adapter_contract,
            evidence=[],
            errors=[(BROKER_ADAPTER_UNAVAILABLE, "broker_read_once_unavailable")],
        )

    evidence: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []

    def normalized_read_once() -> dict[str, Any]:
        try:
            raw_receipt = reader()
        except Exception as exc:
            detail = "adapter_read_exception:" + exc.__class__.__name__
            errors.append((BROKER_ADAPTER_UNAVAILABLE, detail))
            return {"status_code": 0, "body": {}}
        body, row_evidence, reason, detail = (
            _adapter.normalize_broker_read_receipt(
                raw_receipt,
                contract=adapter_contract,
            )
        )
        if reason:
            errors.append((reason, detail))
            return {"status_code": 0, "body": {}}
        evidence.append(row_evidence)
        return {"status_code": 200, "body": body}

    runtime_contract = deepcopy(event_contract)
    runtime_contract.update(
        {
            "observer_operation_ref": _ADAPTER_BRIDGE_PREFIX,
            "observer_method": "GET",
            "observer_path_template": _ADAPTER_BRIDGE_PATH,
            "actor_ref": _ADAPTER_ACTOR_REF,
            "correlation_query_parameter": _ADAPTER_QUERY_PARAMETER,
        }
    )
    runtime_actors = deepcopy(_dict(actors))
    runtime_actors[_ADAPTER_ACTOR_REF] = {"role": "public"}
    kwargs: dict[str, Any] = {
        "contract": runtime_contract,
        "context": context,
        "actors": runtime_actors,
        "tokens": tokens,
        "read_once": normalized_read_once,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    receipt = _event.execute_event_transition(**kwargs)
    receipt["contract_fingerprint"] = _text(
        event_contract.get("contract_fingerprint")
    )
    return _adapter_receipt_projection(
        receipt,
        adapter_contract=adapter_contract,
        evidence=evidence,
        errors=errors,
    )


def execute_process_graph_wait(
    *,
    graph: dict[str, Any],
    step: dict[str, Any],
    context: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    read_once: Any = None,
    sleep: Any = None,
    monotonic: Any = None,
) -> dict[str, Any]:
    """Execute state, HTTP-event, or direct-broker observation through one gate."""
    step_row = deepcopy(_dict(step))
    step_id = _text(step_row.get("step_id") or step_row.get("node_id"))
    if step_id and not _text(step_row.get("step_id")):
        step_row["step_id"] = step_id
    contract = _dict(
        _dict(_dict(graph).get("wait_contracts_by_target")).get(step_id)
    )
    event_contract = _dict(contract.get("event_transition_contract"))
    if event_contract:
        if _direct_adapter_contract(event_contract):
            receipt = _execute_direct_broker_event(
                event_contract=event_contract,
                context=context,
                actors=actors,
                tokens=tokens,
                read_once=read_once,
                sleep=sleep,
                monotonic=monotonic,
            )
        else:
            kwargs: dict[str, Any] = {
                "contract": event_contract,
                "context": context,
                "actors": actors,
                "tokens": tokens,
                "read_once": read_once,
            }
            if sleep is not None:
                kwargs["sleep"] = sleep
            if monotonic is not None:
                kwargs["monotonic"] = monotonic
            receipt = _event.execute_event_transition(**kwargs)
        receipt.setdefault("step_id", step_id)
        return receipt

    kwargs = {
        "graph": graph,
        "step": step_row,
        "context": context,
        "actors": actors,
        "tokens": tokens,
        "read_once": read_once,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    return _core.execute_process_graph_wait(**kwargs)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {"_adapter", "_core", "_event", "_name"}
)
