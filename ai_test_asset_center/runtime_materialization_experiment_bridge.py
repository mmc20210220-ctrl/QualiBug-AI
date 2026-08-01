"""Bind governed Runtime Materialization drafts to the existing Experiment mainline.

This module does not create another compiler or executor.  It captures the enterprise knowledge
asset already built by the formal planning path, matches each compiled Experiment to exactly one
DRAFT_READY Runtime Materialization, freezes a secret-free authority fingerprint on the Experiment,
and extends the existing runtime preflight/finalizer with drift checks and lineage receipts.

Runtime Materialization remains a non-sendable draft.  Network calls, secret loading, fixture
creation, observers, assertions and cleanup continue to be owned by the existing Experiment
Executor after its normal runtime preflight succeeds.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import sys
from contextvars import ContextVar
from typing import Any, Callable
from urllib.parse import urlsplit

BRIDGE_SCHEMA = "qualibug.runtime-materialization-experiment-bridge.v1"
CONTRACT_SCHEMA = "qualibug.runtime-materialization-experiment-contract.v1"

_CAPTURED_ASSET: ContextVar[dict[str, Any] | None] = ContextVar(
    "qualibug_runtime_materialization_asset", default=None
)
_CAPTURE_INSTALL_MARKER = "__qualibug_runtime_materialization_capture_v1__"
_PREFLIGHT_INSTALL_MARKER = "__qualibug_runtime_materialization_preflight_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _normalized_path(value: Any) -> str:
    path = _text(value)
    if not path:
        return ""
    if "://" in path:
        path = urlsplit(path).path
    path = re.sub(r"<([^>]+)>", r"{\1}", path)
    path = re.sub(r"\{[^{}]+\}", "{}", path)
    path = re.sub(r"/+", "/", path)
    return "/" + path.lstrip("/")


def _operation_identity(method: Any, path: Any) -> tuple[str, str]:
    return _text(method).upper(), _normalized_path(path)


def _safe_asset_projection(project: str, asset: dict[str, Any]) -> dict[str, Any]:
    """Retain only fields needed for authority matching; never retain secret material here."""
    return {
        "project": project,
        "asset_id": _text(asset.get("asset_id")),
        "gate": dict(_dict(asset.get("runtime_materialization_gate"))),
        "materializations": [
            dict(row) for row in _list(asset.get("runtime_materializations")) if isinstance(row, dict)
        ],
        "unknowns": [
            dict(row)
            for row in _list(asset.get("runtime_materialization_unknowns"))
            if isinstance(row, dict)
        ],
        "governance": dict(_dict(asset.get("governance"))),
    }


def capture_enterprise_runtime_materializations(project: str, asset: Any) -> None:
    if not isinstance(asset, dict):
        return
    _CAPTURED_ASSET.set(_safe_asset_projection(_text(project), asset))


def install_enterprise_asset_capture() -> None:
    """Wrap the existing enterprise asset builder before discovery planning imports it."""
    try:
        from . import enterprise_knowledge_center as center
    except Exception:
        return
    original = getattr(center, "build_enterprise_business_knowledge_asset", None)
    if not callable(original) or getattr(original, _CAPTURE_INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(project: str, *args: Any, **kwargs: Any) -> Any:
        asset = original(project, *args, **kwargs)
        capture_enterprise_runtime_materializations(project, asset)
        return asset

    setattr(wrapped, _CAPTURE_INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    center.build_enterprise_business_knowledge_asset = wrapped


def _gate_passed(capture: dict[str, Any]) -> bool:
    gate = _dict(capture.get("gate"))
    return (
        _text(gate.get("status")).upper() == "PASS"
        and (gate.get("runtime_materialization_ready") is True or gate.get("entry_allowed") is True)
    )


def _bridge_required(capture: dict[str, Any]) -> bool:
    governance = _dict(capture.get("governance"))
    return bool(
        governance.get("legacy_probe_generation_requires_runtime_materialization_gate") is True
        or _list(capture.get("materializations"))
        or _dict(capture.get("gate"))
    )


def _candidate_lineage(row: dict[str, Any]) -> dict[str, str]:
    lineage = _dict(row.get("lineage"))
    return {
        "materialization_id": _text(row.get("materialization_id") or row.get("runtime_materialization_id")),
        "runtime_plan_id": _text(
            row.get("runtime_plan_ref") or row.get("runtime_plan_id") or lineage.get("runtime_plan_id")
        ),
        "scenario_ir_id": _text(
            row.get("scenario_ir_ref")
            or row.get("scenario_ir_id")
            or row.get("scenario_ref")
            or lineage.get("scenario_ir_id")
        ),
        "execution_contract_id": _text(
            row.get("scenario_execution_contract_ref")
            or row.get("execution_contract_ref")
            or row.get("execution_contract_id")
            or lineage.get("execution_contract_id")
        ),
    }


def _candidate_request_identity(row: dict[str, Any]) -> tuple[str, str]:
    request = _dict(row.get("request_draft"))
    action = _dict(row.get("action_entry"))
    method = request.get("method") or request.get("method_draft") or request.get("http_method")
    path = (
        request.get("path_draft")
        or request.get("path_template")
        or request.get("path")
        or request.get("url_draft")
    )
    if not method:
        method = action.get("method") or action.get("http_method")
    if not path:
        path = action.get("path") or action.get("endpoint") or action.get("url")
    return _operation_identity(method, path)


def _candidate_actor_refs(row: dict[str, Any]) -> set[str]:
    credentials = _dict(row.get("credential_binding"))
    return {
        _text(slot.get("actor_ref"))
        for slot in _list(credentials.get("credential_slots"))
        if isinstance(slot, dict) and _text(slot.get("actor_ref"))
    }


def _ready_candidates(capture: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(capture.get("materializations"))
        if isinstance(row, dict)
        and _text(row.get("status")).upper() == "DRAFT_READY"
        and row.get("formal_runtime_materialization") is not False
        and _text(row.get("materialization_id") or row.get("runtime_materialization_id"))
    ]


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(_dict(behavior_ir).get("operations")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        for value in (
            row.get("id"),
            row.get("node_id"),
            row.get("operation_id"),
            row.get("operationId"),
        ):
            key = _text(value)
            if key:
                result[key] = row
    return result


def _experiment_operation_identity(
    experiment: dict[str, Any], behavior_ir: dict[str, Any]
) -> tuple[str, str]:
    operations = _operation_index(behavior_ir)
    refs: list[str] = []
    for container in (
        experiment,
        _dict(experiment.get("compile_receipt")),
        *_list(experiment.get("treatment_plan")),
        *_list(experiment.get("control_plan")),
        *_list(experiment.get("observation_plan")),
    ):
        if not isinstance(container, dict):
            continue
        for key in ("operation_ref", "operation_id", "operationId", "action_ref"):
            ref = _text(container.get(key))
            if ref and ref not in refs:
                refs.append(ref)
        method = container.get("method") or container.get("http_method")
        path = container.get("path") or container.get("endpoint") or container.get("url")
        identity = _operation_identity(method, path)
        if all(identity):
            return identity
    for ref in refs:
        row = operations.get(ref)
        if row:
            identity = _operation_identity(
                row.get("method") or row.get("http_method"),
                row.get("path") or row.get("raw_path") or row.get("endpoint"),
            )
            if all(identity):
                return identity
    return "", ""


def _experiment_actor_refs(experiment: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    containers = [
        experiment,
        _dict(experiment.get("actor")),
        *_list(experiment.get("treatment_plan")),
        *_list(experiment.get("control_plan")),
        *_list(experiment.get("fixture_plan")),
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("actor_ref", "actor_id", "role", "role_ref", "credential_actor_ref"):
            value = _text(container.get(key))
            if value:
                result.add(value)
    return result


def _explicit_experiment_refs(experiment: dict[str, Any]) -> dict[str, set[str]]:
    result = {
        "materialization_id": set(),
        "runtime_plan_id": set(),
        "scenario_ir_id": set(),
        "execution_contract_id": set(),
    }
    containers = [experiment, _dict(experiment.get("compile_receipt"))]
    for container in containers:
        for key, target in (
            ("materialization_id", "materialization_id"),
            ("runtime_materialization_ref", "materialization_id"),
            ("runtime_plan_id", "runtime_plan_id"),
            ("runtime_plan_ref", "runtime_plan_id"),
            ("scenario_ir_id", "scenario_ir_id"),
            ("scenario_ir_ref", "scenario_ir_id"),
            ("scenario_ref", "scenario_ir_id"),
            ("execution_contract_id", "execution_contract_id"),
            ("execution_contract_ref", "execution_contract_id"),
            ("scenario_execution_contract_ref", "execution_contract_id"),
        ):
            value = _text(container.get(key))
            if value:
                result[target].add(value)
    return result


def _match_materialization(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    explicit = _explicit_experiment_refs(experiment)
    narrowed = list(candidates)
    if explicit["materialization_id"]:
        narrowed = [
            row
            for row in narrowed
            if _candidate_lineage(row)["materialization_id"] in explicit["materialization_id"]
        ]
    for key in ("runtime_plan_id", "scenario_ir_id", "execution_contract_id"):
        if explicit[key]:
            narrowed = [row for row in narrowed if _candidate_lineage(row)[key] in explicit[key]]
    method, path = _experiment_operation_identity(experiment, behavior_ir)
    if method and path:
        operation_matches = [row for row in narrowed if _candidate_request_identity(row) == (method, path)]
        narrowed = operation_matches
    actors = _experiment_actor_refs(experiment)
    if actors and len(narrowed) > 1:
        actor_matches = [row for row in narrowed if _candidate_actor_refs(row) & actors]
        if actor_matches:
            narrowed = actor_matches
    detail = {
        "experiment_id": _text(experiment.get("experiment_id")),
        "obligation_id": _text(experiment.get("obligation_id")),
        "method": method,
        "path": path,
        "candidate_count": len(narrowed),
    }
    if len(narrowed) == 1:
        return narrowed[0], "", detail
    if not narrowed:
        return None, "BLOCKED_RUNTIME_MATERIALIZATION_NOT_FOUND", detail
    detail["candidate_materialization_ids"] = [
        _candidate_lineage(row)["materialization_id"] for row in narrowed[:10]
    ]
    return None, "BLOCKED_RUNTIME_MATERIALIZATION_AMBIGUOUS", detail


def _credential_refs(row: dict[str, Any]) -> list[str]:
    credentials = _dict(row.get("credential_binding"))
    return sorted(
        {
            _text(slot.get("credential_ref"))
            for slot in _list(credentials.get("credential_slots"))
            if isinstance(slot, dict) and _text(slot.get("credential_ref"))
        }
    )


def _authority_contract(
    row: dict[str, Any], *, capture: dict[str, Any], behavior_ir: dict[str, Any]
) -> dict[str, Any]:
    lineage = _candidate_lineage(row)
    method, path = _candidate_request_identity(row)
    environment = _dict(row.get("environment_binding"))
    request = _dict(row.get("request_draft"))
    authority = {
        "schema_version": CONTRACT_SCHEMA,
        "knowledge_asset_id": _text(capture.get("asset_id")),
        "behavior_ir_id": _text(behavior_ir.get("model_id") or behavior_ir.get("behavior_ir_id")),
        "lineage": lineage,
        "materialization_status": _text(row.get("status")),
        "materialization_gate_status": _text(_dict(capture.get("gate")).get("status")),
        "request_identity": {"method": method, "path": path},
        "environment_binding": {
            "environment_ref": _text(environment.get("environment_ref")),
            "environment_kind": _text(environment.get("environment_kind")),
            "base_url_hash": _sha256(_text(environment.get("base_url")))
            if _text(environment.get("base_url"))
            else "",
            "non_production_proven": environment.get("non_production_proven") is True,
        },
        "credential_refs": _credential_refs(row),
        "request_binding_count": len(_list(row.get("request_value_bindings"))),
        "assertion_draft_count": len(_list(row.get("assertion_drafts"))),
        "cleanup_binding_resolved": _dict(row.get("cleanup_draft")).get("cleanup_binding_resolved")
        is True,
        "request_draft_compiled": request.get("draft_compiled") is True
        or bool(method and path),
        "activation_authority": "EXISTING_EXPERIMENT_EXECUTOR_AFTER_RUNTIME_PREFLIGHT",
        "source_of_truth": "existing_enterprise_business_knowledge_asset",
    }
    fingerprint = _sha256(authority)
    return {
        "schema_version": CONTRACT_SCHEMA,
        "authority": authority,
        "authority_fingerprint": fingerprint,
        "materialization_id": lineage["materialization_id"],
        "runtime_plan_id": lineage["runtime_plan_id"],
        "scenario_ir_id": lineage["scenario_ir_id"],
        "execution_contract_id": lineage["execution_contract_id"],
    }


def _blocked_experiment(
    experiment: dict[str, Any], reason: str, detail: dict[str, Any]
) -> dict[str, Any]:
    row = dict(experiment)
    receipt = dict(_dict(row.get("compile_receipt")))
    receipt.update({"status": "BLOCKED", "reason_code": reason, "detail": detail})
    row["compile_receipt"] = receipt
    row["compile_status"] = "BLOCKED"
    row["runtime_materialization_bridge_required"] = True
    return row


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = _text(_dict(row.get("compile_receipt")).get("reason_code")) or "UNKNOWN"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def bind_experiment_pack_to_captured_materializations(
    experiment_pack: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    obligations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Require every formally compiled Experiment to bind to one existing materialization."""
    pack = dict(experiment_pack)
    capture = _CAPTURED_ASSET.get()
    project = _text(behavior_ir.get("project_id") or behavior_ir.get("project"))
    if not isinstance(capture, dict) or not _bridge_required(capture):
        pack["runtime_materialization_bridge"] = {
            "schema_version": BRIDGE_SCHEMA,
            "status": "NOT_APPLICABLE",
            "reason_code": "NO_CAPTURED_FORMAL_RUNTIME_MATERIALIZATION_AUTHORITY",
        }
        return pack
    captured_project = _text(capture.get("project"))
    if project and captured_project and project != captured_project:
        pack["runtime_materialization_bridge"] = {
            "schema_version": BRIDGE_SCHEMA,
            "status": "BLOCKED",
            "reason_code": "BLOCKED_RUNTIME_MATERIALIZATION_PROJECT_MISMATCH",
            "expected_project": project,
            "captured_project": captured_project,
        }
        gate_ok = False
    else:
        gate_ok = _gate_passed(capture)

    compiled = [dict(row) for row in _list(pack.get("experiments")) if isinstance(row, dict)]
    blocked = [dict(row) for row in _list(pack.get("blocked_experiments")) if isinstance(row, dict)]
    bridged: list[dict[str, Any]] = []
    bridge_blocked: list[dict[str, Any]] = []
    candidates = _ready_candidates(capture) if gate_ok else []

    if not gate_ok:
        gate = _dict(capture.get("gate"))
        detail = {
            "gate_status": _text(gate.get("status")) or "NOT_BUILT",
            "runtime_materialization_ready": gate.get("runtime_materialization_ready") is True,
        }
        # Only block experiments that actually require a materialization binding.
        # Experiments whose operation has no matching materialization candidate
        # do not depend on the gate and must not be blanket-blocked.
        all_candidates = _ready_candidates(capture) or [
            row for row in _list(capture.get("materializations")) if isinstance(row, dict)
        ]
        for experiment in compiled:
            candidate, _reason, _detail = _match_materialization(
                experiment, behavior_ir=behavior_ir, candidates=all_candidates
            )
            if candidate is not None:
                bridge_blocked.append(_blocked_experiment(experiment, "BLOCKED_RUNTIME_MATERIALIZATION_GATE", detail))
            else:
                bridged.append(experiment)
    else:
        for experiment in compiled:
            candidate, reason, detail = _match_materialization(
                experiment, behavior_ir=behavior_ir, candidates=candidates
            )
            if candidate is None:
                bridge_blocked.append(_blocked_experiment(experiment, reason, detail))
                continue
            contract = _authority_contract(candidate, capture=capture, behavior_ir=behavior_ir)
            row = dict(experiment)
            receipt = dict(_dict(row.get("compile_receipt")))
            receipt.update(
                {
                    "runtime_materialization_id": contract["materialization_id"],
                    "runtime_materialization_fingerprint": contract["authority_fingerprint"],
                    "runtime_materialization_bridge_status": "BOUND",
                }
            )
            row.update(
                {
                    "compile_receipt": receipt,
                    "runtime_materialization_bridge_required": True,
                    "runtime_materialization_contract": contract,
                    "runtime_materialization_fingerprint": contract["authority_fingerprint"],
                }
            )
            bridged.append(row)

    all_blocked = [*blocked, *bridge_blocked]
    pack.update(
        {
            "compiled_count": len(bridged),
            "blocked_count": len(all_blocked),
            "experiments": bridged,
            "blocked_experiments": all_blocked,
            "block_reason_counts": _reason_counts(all_blocked),
            "runtime_materialization_bridge": {
                "schema_version": BRIDGE_SCHEMA,
                "status": "PASS" if not bridge_blocked and gate_ok else "BLOCKED",
                "gate_status": _text(_dict(capture.get("gate")).get("status")),
                "candidate_count": len(candidates),
                "bound_experiment_count": len(bridged),
                "blocked_experiment_count": len(bridge_blocked),
                "knowledge_asset_id": _text(capture.get("asset_id")),
                "second_compiler_created": False,
                "existing_experiment_executor_remains_authority": True,
            },
        }
    )

    if obligations is not None:
        blocked_by_obligation = {
            _text(row.get("obligation_id")): _text(
                _dict(row.get("compile_receipt")).get("reason_code")
            )
            for row in bridge_blocked
            if _text(row.get("obligation_id"))
        }
        bound_ids = {
            _text(row.get("obligation_id")) for row in bridged if _text(row.get("obligation_id"))
        }
        for obligation in obligations:
            if not isinstance(obligation, dict):
                continue
            oid = _text(obligation.get("obligation_id"))
            if oid in blocked_by_obligation:
                obligation["compile_status"] = "BLOCKED"
                obligation["block_reason"] = blocked_by_obligation[oid]
            elif oid in bound_ids:
                obligation["runtime_materialization_status"] = "BOUND"
    return pack


def validate_experiment_materialization_contract(
    experiment: dict[str, Any], *, behavior_ir: dict[str, Any]
) -> tuple[bool, str, Any]:
    if experiment.get("runtime_materialization_bridge_required") is not True:
        return True, "", ""
    contract = _dict(experiment.get("runtime_materialization_contract"))
    authority = _dict(contract.get("authority"))
    lineage = _dict(authority.get("lineage"))
    detail = {
        "materialization_id": _text(lineage.get("materialization_id")),
        "runtime_plan_id": _text(lineage.get("runtime_plan_id")),
        "scenario_ir_id": _text(lineage.get("scenario_ir_id")),
        "execution_contract_id": _text(lineage.get("execution_contract_id")),
    }
    if not contract or not authority or not detail["materialization_id"]:
        return False, "BLOCKED_RUNTIME_MATERIALIZATION_CONTRACT_MISSING", detail
    expected = _text(contract.get("authority_fingerprint"))
    actual = _sha256(authority)
    compile_expected = _text(
        _dict(experiment.get("compile_receipt")).get("runtime_materialization_fingerprint")
    )
    root_expected = _text(experiment.get("runtime_materialization_fingerprint"))
    if not expected or expected != actual or compile_expected != expected or root_expected != expected:
        detail.update(
            {
                "expected_fingerprint": expected,
                "actual_fingerprint": actual,
                "compile_fingerprint": compile_expected,
                "root_fingerprint": root_expected,
            }
        )
        return False, "BLOCKED_RUNTIME_MATERIALIZATION_CONTRACT_DRIFT", detail
    if _text(authority.get("materialization_status")).upper() != "DRAFT_READY":
        return False, "BLOCKED_RUNTIME_MATERIALIZATION_NOT_READY", detail
    expected_identity = _dict(authority.get("request_identity"))
    actual_method, actual_path = _experiment_operation_identity(experiment, behavior_ir)
    if (
        _text(expected_identity.get("method")).upper() != actual_method
        or _normalized_path(expected_identity.get("path")) != actual_path
    ):
        detail.update(
            {
                "expected_request_identity": dict(expected_identity),
                "actual_request_identity": {"method": actual_method, "path": actual_path},
            }
        )
        return False, "BLOCKED_RUNTIME_MATERIALIZATION_OPERATION_DRIFT", detail
    return True, "", detail


def _install_preflight_guard() -> None:
    try:
        from . import experiment_runtime_support as support
    except Exception:
        return
    original = getattr(support, "preflight_experiment_executable", None)
    if not callable(original) or getattr(original, _PREFLIGHT_INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(experiment: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        behavior_ir = kwargs.get("behavior_ir")
        if not isinstance(behavior_ir, dict) and args:
            behavior_ir = args[0] if isinstance(args[0], dict) else {}
        ok, reason, detail = validate_experiment_materialization_contract(
            _dict(experiment), behavior_ir=_dict(behavior_ir)
        )
        if not ok:
            return False, reason, detail
        return original(experiment, *args, **kwargs)

    setattr(wrapped, _PREFLIGHT_INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    support.preflight_experiment_executable = wrapped
    executor = sys.modules.get(f"{__package__}.experiment_executor")
    if executor is not None:
        executor.preflight_experiment_executable = wrapped


def _lineage_receipt(experiment: dict[str, Any]) -> dict[str, Any]:
    contract = _dict(experiment.get("runtime_materialization_contract"))
    authority = _dict(contract.get("authority"))
    if not authority:
        return {}
    return {
        "schema_version": BRIDGE_SCHEMA,
        "status": "BOUND_AND_VERIFIED",
        "authority_fingerprint": _text(contract.get("authority_fingerprint")),
        "knowledge_asset_id": _text(authority.get("knowledge_asset_id")),
        "behavior_ir_id": _text(authority.get("behavior_ir_id")),
        **dict(_dict(authority.get("lineage"))),
    }


def attach_materialization_lineage_to_result(
    result: Any, *, experiment: dict[str, Any]
) -> Any:
    if not isinstance(result, dict):
        return result
    lineage = _lineage_receipt(experiment)
    if not lineage:
        return result
    output = dict(result)
    output["runtime_materialization_lineage"] = lineage
    for key in ("execution_receipt", "cleanup_receipt", "evidence_receipt"):
        if isinstance(output.get(key), dict):
            output[key] = {**output[key], "runtime_materialization_lineage": lineage}
    if isinstance(output.get("finding"), dict):
        output["finding"] = {
            **output["finding"],
            "runtime_materialization_lineage": lineage,
        }
    return output


def _materialization_finalizer_hook(
    next_call: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    result = next_call(args, kwargs)
    experiment = kwargs.get("exp") or kwargs.get("experiment")
    if not isinstance(experiment, dict):
        experiment = next(
            (
                arg
                for arg in args
                if isinstance(arg, dict) and arg.get("experiment_id")
            ),
            {},
        )
    return attach_materialization_lineage_to_result(
        result,
        experiment=_dict(experiment),
    )


def _install_finalizer_receipt() -> None:
    from . import experiment_outcome_finalizer as finalizer

    finalizer.register_finalizer_hook(
        "runtime_materialization_lineage",
        _materialization_finalizer_hook,
    )


def install_runtime_materialization_execution_bridge() -> None:
    install_enterprise_asset_capture()
    _install_preflight_guard()
    _install_finalizer_receipt()


__all__ = [
    "BRIDGE_SCHEMA",
    "CONTRACT_SCHEMA",
    "attach_materialization_lineage_to_result",
    "bind_experiment_pack_to_captured_materializations",
    "capture_enterprise_runtime_materializations",
    "install_enterprise_asset_capture",
    "install_runtime_materialization_execution_bridge",
    "validate_experiment_materialization_contract",
]
