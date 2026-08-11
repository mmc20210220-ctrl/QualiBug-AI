"""Single authority for whether one binding target can reach runtime materialization.

A target name in ``binding_plan`` is intent, not evidence that the current
executors can materialize a value. This authority is shared by compile freeze
and runtime preflight so a ``blocked``/empty binding can never be upgraded to an
initially available flow value merely because its target key exists.

Credential values are a special case: modern compilation projects the opaque
``credential_secret_ref`` directly into the request before freeze. Therefore a
credential target that still appears as an initial runtime binding is an old or
incomplete artifact and must fail closed rather than rely on a nonexistent
fixture/read materializer.

No value is discovered here and no request is sent.
"""
from __future__ import annotations

from typing import Any

from .runtime_binding_materializer_base import validated_runtime_resolvers

SCHEMA_VERSION = "qualibug.binding-target-materialization-authority.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _binding_rows(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(experiment).get("binding_plan")
    if isinstance(raw, dict):
        rows: list[dict[str, Any]] = []
        for target, value in raw.items():
            row = dict(value) if isinstance(value, dict) else {}
            row.setdefault("target", _text(target))
            rows.append(row)
        return rows
    return [dict(row) for row in _list(raw) if isinstance(row, dict)]


def _runtime_binding_node_ids(
    experiment: dict[str, Any], target: str
) -> list[str]:
    dag = _dict(_dict(experiment).get("fixture_dag"))
    return [
        _text(node.get("node_id"))
        for node in _list(dag.get("nodes"))
        if isinstance(node, dict)
        and _text(node.get("kind")) == "runtime_read_binding"
        and _text(node.get("target")) == _text(target)
        and node.get("constructible") is not False
        and _text(node.get("node_id"))
    ]


def _flow_channel_proves_target(
    target: str,
    flow_execution_contract: dict[str, Any] | None,
) -> bool:
    contract = _dict(flow_execution_contract)
    if _text(contract.get("status")).upper() != "FROZEN":
        return False
    wanted = _text(target)
    for raw in _list(contract.get("step_contracts")):
        step = _dict(raw)
        if wanted in {
            _text(value)
            for value in _list(step.get("sequential_identity_targets"))
            if _text(value)
        }:
            return True
        for ref in _list(step.get("input_bindings")):
            row = _dict(ref)
            if (
                _text(row.get("status")) == "RESOLVED"
                and _text(row.get("consumer_target")) == wanted
            ):
                return True
    return False


def _fixture_setup_proven(row: dict[str, Any]) -> bool:
    setup = _dict(row.get("fixture_setup"))
    if not setup:
        return False
    cleanup_operations = [
        _dict(item)
        for item in _list(setup.get("cleanup_operations"))
        if isinstance(item, dict)
    ]
    cleanup_receipt = _dict(setup.get("cleanup_operation_authority_receipt"))
    return bool(
        _text(setup.get("operation_ref") or setup.get("create_operation_ref"))
        and _text(setup.get("method")).upper() == "POST"
        and _text(setup.get("path")).startswith("/")
        and len(cleanup_operations) == 1
        and _text(cleanup_receipt.get("status")) == "RESOLVED"
    )


def resolve_binding_target_materialization(
    target: str,
    *,
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    flow_execution_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return whether one target has a currently executable materialization channel."""

    wanted = _text(target)
    rows = [
        row
        for row in _binding_rows(experiment)
        if _text(row.get("target")) == wanted
    ]
    base = {
        "schema_version": SCHEMA_VERSION,
        "target": wanted,
        "status": "UNRESOLVED",
        "reason_code": "",
        "source_priority": "",
        "binding_status": "",
        "runtime_binding_node_ids": _runtime_binding_node_ids(experiment, wanted),
        "source_order_selection_allowed": False,
    }
    if not wanted:
        return {**base, "reason_code": "BINDING_TARGET_MISSING"}
    if len(rows) != 1:
        return {
            **base,
            "reason_code": (
                "BINDING_TARGET_MISSING"
                if not rows
                else "BINDING_TARGET_AMBIGUOUS"
            ),
            "binding_row_count": len(rows),
        }

    row = rows[0]
    status = _text(row.get("status")).lower()
    source_priority = _text(row.get("source_priority"))
    base.update(
        {
            "binding_status": status,
            "source_priority": source_priority,
            "blocked_reason": _text(row.get("blocked_reason")),
        }
    )

    if status == "blocked":
        return {
            **base,
            "reason_code": _text(row.get("blocked_reason"))
            or "BINDING_TARGET_EXPLICITLY_BLOCKED",
        }

    # Values sealed into the binding plan are immediately materializable only
    # when the value itself survives. A fingerprint without the value proves
    # integrity of something, not that the executor can reconstruct it.
    if status == "bound" and row.get("materialized_value") not in (
        None,
        "",
        [],
        {},
    ):
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "authority": "sealed_materialized_value",
        }

    # A precondition/graph output is not an initial value. It is valid only when
    # the already-frozen flow execution contract proves the producer/consumer
    # channel. This prevents name-only produced bindings from becoming facts.
    if row.get("precondition_provided") is True or source_priority == "sequential_output_binding":
        if _flow_channel_proves_target(wanted, flow_execution_contract):
            return {
                **base,
                "status": "RESOLVED",
                "reason_code": "",
                "authority": "frozen_flow_producer_consumer",
            }
        return {
            **base,
            "reason_code": "BINDING_FLOW_PRODUCER_CONTRACT_MISSING",
        }

    # Ownership identities are deliberately resolved from the exact runtime
    # actor identity, never from a business collection read.
    if status == "runtime_resolvable" and source_priority == "ownership_identity_param":
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "authority": "runtime_actor_identity_channel",
        }

    # Modern compilation replaces {password}/<password> with the opaque exact
    # secret ref before freeze. If this target is still present in the frozen
    # initial-binding set, projection did not happen (old artifact, actor
    # mismatch, or compile drift). The fixture materializer has no credential
    # value channel and must not be treated as if it did.
    if source_priority == "actor_credential_secret":
        return {
            **base,
            "reason_code": "BINDING_CREDENTIAL_REQUIRES_SECRET_REF_PROJECTION",
            "credential_actor_ref": _text(row.get("actor_ref")),
            "credential_secret_ref_present": bool(
                _text(row.get("credential_secret_ref"))
            ),
            "secret_value_persisted": False,
        }

    operations = _operation_index(behavior_ir)
    resolvers = validated_runtime_resolvers(row, operations)
    if status in {"runtime_resolvable", "bound"} and resolvers:
        if not base["runtime_binding_node_ids"]:
            return {
                **base,
                "reason_code": "BINDING_RUNTIME_DAG_NODE_MISSING",
                "validated_resolver_count": len(resolvers),
            }
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "authority": "validated_runtime_resolver",
            "validated_resolver_count": len(resolvers),
        }

    if status == "runtime_resolvable" and _fixture_setup_proven(row):
        if not base["runtime_binding_node_ids"]:
            return {
                **base,
                "reason_code": "BINDING_RUNTIME_DAG_NODE_MISSING",
            }
        return {
            **base,
            "status": "RESOLVED",
            "reason_code": "",
            "authority": "validated_fixture_create_cleanup",
        }

    return {
        **base,
        "reason_code": _text(row.get("blocked_reason"))
        or "BINDING_TARGET_HAS_NO_EXECUTABLE_MATERIALIZATION_CHANNEL",
    }


__all__ = [
    "SCHEMA_VERSION",
    "resolve_binding_target_materialization",
]
