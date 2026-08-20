"""Generic service-topology execution routing authority.

A Behavior IR may contain operations from several enterprise services while one
scan entrypoint carries only one ``base_url``.  Executing every compiled
experiment against that one URL fabricates cross-service 404s; dropping every
foreign-service obligation destroys Recall.  The source of truth is the
project-declared ``multi_service.services`` topology.

This authority therefore:

* routes a single-service experiment to that service's declared base URL;
* enriches graph-backed multi-service experiments with approved targets so the
  existing process-graph runtime can route every node through its governed
  target context;
* blocks a non-graph experiment that genuinely spans several services instead
  of sending requests to the wrong target; and
* preserves the historical single-base-url behavior when no multi-service
  topology is declared.

No port, product, industry or benchmark name is embedded here.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .project_runtime_config import load_real_project_config
from .target_policy import normalize_base_url


ROUTING_SCHEMA = "qualibug.service-topology-execution-routing.v1"
BLOCKED_CROSS_SERVICE_ROUTE_UNAVAILABLE = "BLOCKED_CROSS_SERVICE_ROUTE_UNAVAILABLE"
BLOCKED_SERVICE_ROUTE_UNAVAILABLE = "BLOCKED_SERVICE_ROUTE_UNAVAILABLE"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _service_url_row(value: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(value, str):
        return _text(value), {}
    row = _dict(value)
    url = _text(
        row.get("base_url")
        or row.get("approved_base_url")
        or row.get("url")
        or row.get("endpoint")
        or row.get("endpoint_ref")
    )
    return url, dict(row)


def build_service_topology(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize the project-declared service map without guessing identities."""

    multi = _dict(_dict(config).get("multi_service"))
    services = multi.get("services")
    if not isinstance(services, dict):
        return {}
    topology: dict[str, dict[str, Any]] = {}
    for raw_name, raw_value in services.items():
        name = _text(raw_name)
        url, metadata = _service_url_row(raw_value)
        if not name or not url:
            continue
        normalized = normalize_base_url(url)
        if not normalized:
            continue
        topology[name] = {
            **metadata,
            "service_name": name,
            "approved_base_url": normalized,
        }
    return topology


def load_project_service_topology(
    project: str,
    root: Path,
) -> dict[str, dict[str, Any]]:
    try:
        config = load_real_project_config(project, root)
    except Exception:
        return {}
    return build_service_topology(config)


def _operation_service_map(behavior_ir: dict[str, Any]) -> dict[str, str]:
    return {
        _text(row.get("id") or row.get("operation_id")): _text(
            row.get("_service_name") or row.get("service") or row.get("service_name")
        )
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
        and _text(row.get("_service_name") or row.get("service") or row.get("service_name"))
    }


def experiment_operation_refs(experiment: dict[str, Any]) -> list[str]:
    """Return exact operation identities that may reach transport/cleanup."""

    exp = _dict(experiment)
    refs: list[str] = []
    for value in _list(exp.get("operation_refs")) + _list(exp.get("required_operations")):
        ref = _text(value)
        if ref and ref not in refs:
            refs.append(ref)
    for plan_name in (
        "precondition_plan",
        "control_plan",
        "treatment_plan",
        "cleanup_plan",
    ):
        for step in _list(exp.get(plan_name)):
            if not isinstance(step, dict):
                continue
            ref = _text(step.get("operation_ref") or step.get("operation_id"))
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def experiment_service_refs(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[str]:
    service_by_operation = _operation_service_map(behavior_ir)
    return sorted({
        service_by_operation[ref]
        for ref in experiment_operation_refs(experiment)
        if ref in service_by_operation and service_by_operation[ref]
    })


def _is_graph_backed(experiment: dict[str, Any]) -> bool:
    graphs = [
        _dict(step.get("_execution_graph"))
        for step in _list(_dict(experiment).get("treatment_plan"))
        if isinstance(step, dict) and _dict(step.get("_execution_graph"))
    ]
    return bool(graphs)


def _approved_target_row(
    service_name: str,
    service: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    url = _text(service.get("approved_base_url"))
    row = {
        key: deepcopy(value)
        for key, value in service.items()
        if key not in {"service_name", "base_url", "url", "endpoint", "endpoint_ref"}
    }
    row.update({
        "system_ref": service_name,
        "target_id": _text(row.get("target_id")) or service_name,
        "approved_target_ref": _text(row.get("approved_target_ref")) or service_name,
        "environment_ref": _text(row.get("environment_ref")) or service_name,
        "approved_base_url": url,
        "requested_base_url": _text(row.get("requested_base_url")) or url,
        "environment_type": _text(
            row.get("environment_type")
            or runtime_contract.get("environment_type")
            or runtime_contract.get("environment_kind")
            or runtime_contract.get("target_environment")
        ),
        "execution_mode": _text(row.get("execution_mode") or runtime_contract.get("execution_mode")),
        "status": _text(row.get("status") or runtime_contract.get("status")) or "ready",
    })
    return row


def enrich_runtime_contract_with_topology(
    runtime_contract: dict[str, Any],
    topology: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Add declared services as graph approved targets without weakening policy."""

    contract = deepcopy(_dict(runtime_contract))
    existing = contract.get("approved_targets")
    if isinstance(existing, dict):
        approved = {str(key): deepcopy(value) for key, value in existing.items() if isinstance(value, dict)}
    elif isinstance(existing, list):
        approved = {
            _text(row.get("system_ref") or row.get("target_id") or row.get("approved_target_ref")): deepcopy(row)
            for row in existing
            if isinstance(row, dict)
            and _text(row.get("system_ref") or row.get("target_id") or row.get("approved_target_ref"))
        }
    else:
        approved = {}
    for service_name, service in topology.items():
        approved.setdefault(
            service_name,
            _approved_target_row(service_name, service, contract),
        )
    contract["approved_targets"] = approved
    return contract


def resolve_experiment_execution_route(
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    base_url: str,
    runtime_contract: dict[str, Any],
    topology: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Resolve the one governed execution route for a compiled experiment."""

    original_base = normalize_base_url(base_url) or _text(base_url)
    services = experiment_service_refs(experiment, behavior_ir)
    enriched = enrich_runtime_contract_with_topology(runtime_contract, topology)

    if not topology:
        return {
            "schema_version": ROUTING_SCHEMA,
            "status": "READY",
            "mode": "single_target_legacy",
            "service_refs": services,
            "base_url": original_base,
            "runtime_contract": enriched,
            "reason_code": "",
        }

    if not services:
        return {
            "schema_version": ROUTING_SCHEMA,
            "status": "READY",
            "mode": "service_agnostic",
            "service_refs": [],
            "base_url": original_base,
            "runtime_contract": enriched,
            "reason_code": "",
        }

    missing = [service for service in services if service not in topology]
    if missing:
        return {
            "schema_version": ROUTING_SCHEMA,
            "status": "BLOCKED",
            "mode": "route_missing",
            "service_refs": services,
            "missing_service_refs": missing,
            "base_url": original_base,
            "runtime_contract": enriched,
            "reason_code": BLOCKED_SERVICE_ROUTE_UNAVAILABLE,
            "detail": "declared_service_route_missing:" + ",".join(missing),
        }

    if len(services) == 1:
        service_name = services[0]
        routed = _text(topology[service_name].get("approved_base_url"))
        return {
            "schema_version": ROUTING_SCHEMA,
            "status": "READY",
            "mode": "single_service_routed",
            "service_refs": services,
            "routed_service_ref": service_name,
            "base_url": routed,
            "runtime_contract": enriched,
            "reason_code": "",
        }

    if _is_graph_backed(experiment):
        return {
            "schema_version": ROUTING_SCHEMA,
            "status": "READY",
            "mode": "process_graph_multi_service",
            "service_refs": services,
            "base_url": original_base,
            "runtime_contract": enriched,
            "reason_code": "",
        }

    return {
        "schema_version": ROUTING_SCHEMA,
        "status": "BLOCKED",
        "mode": "cross_service_without_graph",
        "service_refs": services,
        "base_url": original_base,
        "runtime_contract": enriched,
        "reason_code": BLOCKED_CROSS_SERVICE_ROUTE_UNAVAILABLE,
        "detail": (
            "multi_service_experiment_requires_process_graph_route:"
            + ",".join(services)
        ),
    }


def blocked_routing_result(
    experiment: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    exp = _dict(experiment)
    reason = _text(route.get("reason_code")) or BLOCKED_CROSS_SERVICE_ROUTE_UNAVAILABLE
    detail = _text(route.get("detail"))
    return {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "status": "BLOCKED",
        "reason_code": reason,
        "detail": detail,
        "steps": [],
        "finding": None,
        "cleanup_failures": 0,
        "service_topology_routing_receipt": {
            key: deepcopy(value)
            for key, value in route.items()
            if key != "runtime_contract"
        },
        "execution_receipt": {
            "status": "BLOCKED",
            "reason_code": reason,
            "detail": detail,
            "request_reached_transport": False,
            "write_request_attempt_count": 0,
            "cleanup_failures": 0,
        },
    }
