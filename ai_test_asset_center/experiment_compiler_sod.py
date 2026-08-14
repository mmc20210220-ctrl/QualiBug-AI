"""Attach source-governed SoD fixture ownership to an existing experiment."""
from __future__ import annotations

import re
from typing import Any

from .fixture_dag import build_fixture_dag_for_experiment
from .real_id_resolver import normalize_path_placeholders
from .runtime_binding_graph import _declared_fixture_setup


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def attach_sod_fixture_owner_binding(
    experiment: dict[str, Any],
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Bind the setup-role actor through the existing declared-fixture authority."""
    result = dict(experiment)
    if _text(_dict(result.get("compile_receipt")).get("status")).upper() != "COMPILED":
        return result
    prop = _dict(obligation.get("property"))
    fixture_owner = _text(prop.get("fixture_owner_actor_ref"))
    setup_operation_ref = _text(prop.get("setup_operation_ref"))
    if not fixture_owner or not setup_operation_ref or "owned_resource" not in _list(obligation.get("required_fixtures")):
        return result
    operations = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    operation_ref = _text(prop.get("operation_ref")) or next(
        (_text(value) for value in _list(obligation.get("required_operations")) if _text(value)),
        "",
    )
    primary_op = _dict(operations.get(operation_ref))
    if not primary_op:
        return result
    placeholder_targets = re.findall(
        r"\{([A-Za-z_][A-Za-z0-9_]*)\}|:([A-Za-z_][A-Za-z0-9_]*)",
        normalize_path_placeholders(_text(primary_op.get("path") or primary_op.get("raw_path"))),
    )
    target = next((left or right for left, right in placeholder_targets if left or right), "id")
    setup = _declared_fixture_setup(primary_op, target=target, behavior_ir=behavior_ir)
    if (
        _text(setup.get("operation_ref")) != setup_operation_ref
        or fixture_owner not in {_text(value) for value in _list(setup.get("actor_refs"))}
    ):
        return result
    bindings = [
        dict(row)
        for row in _list(result.get("binding_plan"))
        if isinstance(row, dict)
        and _text(row.get("target")) not in {target, "fixture:owned_resource"}
    ]
    bindings.extend([
        {
            "target": target,
            "target_path": f"/{{{target}}}",
            "status": "runtime_resolvable",
            "source_priority": "source_declared_sod_fixture",
            "resolver_operations": [],
            "fixture_setup": setup,
            "force_fixture_setup": True,
            "required_fixture_id": "owned_resource",
            "fixture_owner_actor_ref": fixture_owner,
            "value_fingerprint": "",
        },
        {
            "target": "fixture:owned_resource",
            "fixture_id": "owned_resource",
            "status": "fixture_proof",
            "source_priority": "experiment_setup_response",
            "binding_target": target,
            "owner_actor_ref": fixture_owner,
            "create_operation_ref": _text(setup.get("operation_ref")),
            "create_path": _text(setup.get("path")),
            "proof_operation_ref": _text(primary_op.get("id")),
            "cleanup_operations": [
                dict(row) for row in _list(setup.get("cleanup_operations"))
                if isinstance(row, dict)
            ],
            "value_fingerprint": "",
        },
    ])
    result["binding_plan"] = bindings
    # The pre-freeze runtime-read resolver is being replaced by the source-declared
    # SoD fixture. Re-run the fixture-cleanup authority so the setup carries the
    # resolved cleanup receipt, then re-freeze the request-build contract against
    # the new binding_plan/fixture_dag. Otherwise the runtime request-build rebuild
    # sees a different materialization channel than the frozen contract and blocks
    # the experiment as REQUEST_BUILD_CONTRACT_DRIFT.
    from .runtime_binding_graph_mainline_base import _govern_fixture_cleanup_authority
    from .experiment_compile_freezer import freeze_compiled_experiment

    result["binding_plan"] = _govern_fixture_cleanup_authority(
        bindings, behavior_ir=behavior_ir
    )
    result["fixture_dag"] = build_fixture_dag_for_experiment(result, behavior_ir=behavior_ir)
    result = freeze_compiled_experiment(result, behavior_ir=behavior_ir)
    receipt = dict(_dict(result.get("compile_receipt")))
    receipt["sod_fixture_owner_bound"] = True
    receipt["sod_fixture_owner_actor_ref"] = fixture_owner
    receipt["fixture_dag_id"] = _text(_dict(result.get("fixture_dag")).get("fixture_dag_id"))
    receipt["fixture_dag_status"] = _text(_dict(result.get("fixture_dag")).get("status"))
    result["compile_receipt"] = receipt
    return result


__all__ = ["attach_sod_fixture_owner_binding"]
