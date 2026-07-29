"""Compile source-bound Job runtime invariants into the existing obligation mainline.

One accepted async Job invariant becomes one ordinary Test Obligation.  The existing
planner, protocol registry and experiment compiler remain authoritative.  This module also
extends the existing compile result with an immutable Asset→Experiment lineage receipt; it
does not introduce a second compiler or receipt store.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from . import discovery_runtime_planning as _planning
from .job_async_protocol import TEMPLATE_ASYNC_JOB_EXECUTION
from .source_job_contract_binding import INVARIANT_KIND
from .test_obligation import dedupe_obligations, make_obligation

OBLIGATION_BINDING_RECEIPT_SCHEMA = "qualibug.source-job-obligation-binding.v1"
LINEAGE_SCHEMA = "qualibug.async-job-lineage-receipt.v1"
_INSTALL_MARKER = "_qualibug_source_job_obligation_binding_installed"
_EXPERIMENT_INSTALL_MARKER = "_qualibug_job_lineage_compile_installed"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _fingerprint(value: Any) -> str:
    blob = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _job_invariants(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == INVARIANT_KIND
        and _text(row.get("status")) == "accepted"
    ]


def _index(behavior_ir: dict[str, Any], collection: str) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get(collection))
        if isinstance(row, dict) and _text(row.get("id"))
    }


def _relations_for(
    behavior_ir: dict[str, Any],
    *,
    invariant_ref: str,
    operation_ref: str,
    actor_ref: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict)
        and _text(row.get("relation_type")) == "observes"
        and _text(row.get("from_ref")) == operation_ref
        and _text(row.get("to_ref")) == invariant_ref
        and _text(row.get("operation_ref")) == operation_ref
        and _text(row.get("actor_ref")) == actor_ref
        and _text(row.get("status")) == "accepted"
    ]


def _gap(invariant: dict[str, Any], reason_code: str, detail: str = "") -> dict[str, Any]:
    return {
        "invariant_id": _text(invariant.get("id")),
        "source_job_asset_id": _text(invariant.get("source_job_asset_id")),
        "source_behavior_id": _text(invariant.get("source_behavior_id")),
        "reason_code": reason_code,
        "detail": detail,
    }


def _base_lineage(
    *,
    invariant: dict[str, Any],
    operation_ref: str,
    obligation_id: str,
) -> dict[str, Any]:
    payload = {
        "schema": LINEAGE_SCHEMA,
        "job_asset_id": _text(invariant.get("source_job_asset_id")),
        "operation_id": operation_ref,
        "behavior_id": _text(invariant.get("source_behavior_id")),
        "invariant_id": _text(invariant.get("id")),
        "obligation_id": obligation_id,
        "experiment_id": "",
        "protocol_id": f"process:{TEMPLATE_ASYNC_JOB_EXECUTION}",
        "source_receipt_ids": [
            _text(row.get("receipt_id"))
            for row in _list(invariant.get("source_refs"))
            if isinstance(row, dict) and _text(row.get("receipt_id"))
        ],
        "identity_complete": bool(
            _text(invariant.get("source_job_asset_id"))
            and operation_ref
            and _text(invariant.get("source_behavior_id"))
            and _text(invariant.get("id"))
            and obligation_id
        ),
        "identity_drift": False,
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def compile_job_obligations(
    behavior_ir: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Append one existing-schema process obligation per valid Job invariant."""
    result = dict(_dict(baseline))
    invariants = _job_invariants(behavior_ir)
    if not invariants:
        result["source_job_obligation_binding_receipt"] = {
            "schema_version": OBLIGATION_BINDING_RECEIPT_SCHEMA,
            "status": "NOT_REQUESTED",
            "job_invariant_count": 0,
            "compiled_obligation_count": 0,
            "coverage_gap_count": 0,
            "reason_counts": {},
        }
        return result

    operations = _index(behavior_ir, "operations")
    actors = _index(behavior_ir, "actors")
    invariant_ids = {_text(row.get("id")) for row in invariants}
    obligations = [
        dict(row)
        for row in _list(result.get("obligations"))
        if isinstance(row, dict)
        and _text(_dict(row.get("property")).get("invariant_ref")) not in invariant_ids
    ]
    gaps = [
        dict(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("invariant_id")) not in invariant_ids
    ]
    compiled = 0
    reason_counts: dict[str, int] = {}

    for invariant in invariants:
        invariant_ref = _text(invariant.get("id"))
        operation_refs = [
            _text(value) for value in _list(invariant.get("operation_refs")) if _text(value)
        ]
        actor_ref = _text(invariant.get("job_actor_ref"))
        contract = _dict(invariant.get("async_contract"))
        runtime = _dict(contract.get("runtime"))
        testability = _dict(contract.get("testability"))
        source_refs = [
            dict(row) for row in _list(invariant.get("source_refs")) if isinstance(row, dict)
        ]
        reason = ""
        detail = ""
        operation_ref = operation_refs[0] if len(operation_refs) == 1 else ""
        if len(operation_refs) != 1 or operation_ref not in operations:
            reason = "ASYNC_JOB_OPERATION_NOT_BOUND"
            detail = f"operation_refs={operation_refs}"
        elif not actor_ref or actor_ref not in actors:
            reason = "ASYNC_JOB_ACTOR_NOT_BOUND"
        elif len(_relations_for(
            behavior_ir,
            invariant_ref=invariant_ref,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
        )) != 1:
            reason = "ASYNC_JOB_RELATION_NOT_BOUND"
        elif not source_refs:
            reason = "ASYNC_JOB_SOURCE_EVIDENCE_MISSING"
        elif _text(testability.get("execution_status")) != "EXECUTION_READY":
            reason = "ASYNC_JOB_NOT_EXECUTION_READY"
        elif _text(testability.get("safety_level")) != "READ_ONLY" or _list(
            contract.get("write_set")
        ):
            reason = "ASYNC_JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED"
        elif not _text(contract.get("platform_job_id")):
            reason = "ASYNC_JOB_PLATFORM_JOB_ID_MISSING"
        elif not _text(contract.get("connector_id") or runtime.get("connector_id")):
            reason = "ASYNC_JOB_CONNECTOR_IDENTITY_UNRESOLVED"
        elif not _list(runtime.get("terminal_states")) or not _list(
            runtime.get("success_states")
        ):
            reason = "ASYNC_JOB_SUCCESS_STATE_CONTRACT_UNRESOLVED"

        if reason:
            gaps.append(_gap(invariant, reason, detail))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue

        relation = _relations_for(
            behavior_ir,
            invariant_ref=invariant_ref,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
        )[0]
        property_spec = {
            "template": TEMPLATE_ASYNC_JOB_EXECUTION,
            "invariant_ref": invariant_ref,
            "source_job_asset_id": _text(invariant.get("source_job_asset_id")),
            "source_behavior_id": _text(invariant.get("source_behavior_id")),
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "async_contract": dict(contract),
            "runtime_integrity_only": True,
            "formal_business_finding_eligible": False,
            "source_refs": source_refs,
        }
        obligation = make_obligation(
            risk_family="process",
            subject_refs=[invariant_ref],
            property_spec=property_spec,
            required_actors=[actor_ref],
            required_operations=[operation_ref],
            required_fixtures=[],
            required_observers=["http_response", "after_state"],
            cleanup_requirement={
                "required": False,
                "reason_code": "READ_ONLY_JOB_NO_BUSINESS_WRITE",
            },
            source_refs=source_refs,
            relation_refs=[_text(relation.get("id"))],
            confidence=min(
                float(invariant.get("confidence") or 1.0),
                float(operations[operation_ref].get("confidence") or 1.0),
            ),
        )
        lineage = _base_lineage(
            invariant=invariant,
            operation_ref=operation_ref,
            obligation_id=_text(obligation.get("obligation_id")),
        )
        obligation["async_job_lineage_receipt"] = lineage
        obligation["property"]["async_job_lineage_receipt"] = dict(lineage)
        obligations.append(obligation)
        compiled += 1

    obligations = dedupe_obligations(obligations)
    result["obligations"] = obligations
    result["coverage_gaps"] = gaps
    result["count"] = len(obligations)
    result["gap_count"] = len(gaps)
    by_family: dict[str, int] = {}
    for row in obligations:
        family = _text(row.get("risk_family")) or "unknown"
        by_family[family] = by_family.get(family, 0) + 1
    result["by_family"] = by_family
    result["source_job_obligation_binding_receipt"] = {
        "schema_version": OBLIGATION_BINDING_RECEIPT_SCHEMA,
        "status": "BOUND" if compiled else "BLOCKED",
        "job_invariant_count": len(invariants),
        "compiled_obligation_count": compiled,
        "coverage_gap_count": sum(reason_counts.values()),
        "reason_counts": dict(sorted(reason_counts.items())),
        "template": TEMPLATE_ASYNC_JOB_EXECUTION,
        "runtime_integrity_only": True,
        "formal_business_finding_eligible": False,
    }
    return result


def _attach_experiment_lineage(
    experiments_result: Any,
    obligations: list[dict[str, Any]],
) -> Any:
    if not isinstance(experiments_result, dict):
        return experiments_result
    result = dict(experiments_result)
    by_id = {
        _text(row.get("obligation_id")): row
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    experiments = []
    for raw in _list(result.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = dict(raw)
        obligation = _dict(by_id.get(_text(experiment.get("obligation_id"))))
        lineage = _dict(obligation.get("async_job_lineage_receipt"))
        if lineage:
            sealed = {
                **lineage,
                "experiment_id": _text(experiment.get("experiment_id")),
                "protocol_id": f"process:{TEMPLATE_ASYNC_JOB_EXECUTION}",
                "identity_complete": bool(
                    lineage.get("job_asset_id")
                    and lineage.get("operation_id")
                    and lineage.get("behavior_id")
                    and lineage.get("invariant_id")
                    and lineage.get("obligation_id")
                    and _text(experiment.get("experiment_id"))
                ),
                "identity_drift": _text(experiment.get("obligation_id"))
                != _text(lineage.get("obligation_id")),
            }
            sealed.pop("fingerprint", None)
            sealed["fingerprint"] = _fingerprint(sealed)
            experiment["async_job_lineage_receipt"] = sealed
            receipt = _dict(experiment.get("compile_receipt"))
            experiment["compile_receipt"] = {
                **receipt,
                "async_job_lineage_fingerprint": sealed["fingerprint"],
            }
        experiments.append(experiment)
    result["experiments"] = experiments
    return result


def install_source_job_obligation_binding() -> Callable[..., dict[str, Any]]:
    """Install Job obligation and lineage facades on the existing mainline."""
    from . import obligation_compiler as compiler_module

    current = _planning.compile_obligations_from_behavior_ir
    if getattr(current, _INSTALL_MARKER, False):
        obligation_wrapper = current
    else:
        original = current

        def obligation_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            behavior_ir = args[0] if args else kwargs.get("behavior_ir")
            baseline = original(*args, **kwargs)
            return compile_job_obligations(_dict(behavior_ir), _dict(baseline))

        setattr(obligation_wrapper, _INSTALL_MARKER, True)
        obligation_wrapper._qualibug_original_compiler = original  # type: ignore[attr-defined]
        _planning.compile_obligations_from_behavior_ir = obligation_wrapper
        compiler_module.compile_obligations_from_behavior_ir = obligation_wrapper

    from . import experiment_compiler as experiment_module

    experiment_current = _planning.compile_experiments
    if not getattr(experiment_current, _EXPERIMENT_INSTALL_MARKER, False):
        experiment_original = experiment_current

        def experiment_wrapper(*args: Any, **kwargs: Any) -> Any:
            obligations = args[0] if args else kwargs.get("obligations")
            result = experiment_original(*args, **kwargs)
            return _attach_experiment_lineage(
                result,
                [row for row in _list(obligations) if isinstance(row, dict)],
            )

        setattr(experiment_wrapper, _EXPERIMENT_INSTALL_MARKER, True)
        experiment_wrapper._qualibug_original_compiler = experiment_original  # type: ignore[attr-defined]
        _planning.compile_experiments = experiment_wrapper
        experiment_module.compile_experiments = experiment_wrapper

    return obligation_wrapper


__all__ = [
    "OBLIGATION_BINDING_RECEIPT_SCHEMA",
    "LINEAGE_SCHEMA",
    "compile_job_obligations",
    "install_source_job_obligation_binding",
]
