"""Final compile freeze for executable experiments.

The existing obligation compiler remains the semantic compiler. This module
runs only after protocol, precondition, control/treatment, observer, binding,
and cleanup plans exist. It freezes one cross-plan requirement view and binds
source-declared readback policies to the exact steps that execute them.

No endpoint, identity, observer, retry policy, or compensation is invented.
Ambiguous or incomplete asynchronous bindings block compilation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "qualibug.experiment-compile-freeze.v1"
BLOCKED_READBACK_CONTRACT_AMBIGUOUS = "BLOCKED_READBACK_CONTRACT_AMBIGUOUS"
BLOCKED_READBACK_CONTRACT_INCOMPLETE = "BLOCKED_READBACK_CONTRACT_INCOMPLETE"
BLOCKED_FLOW_REQUIREMENTS_INVALID = "BLOCKED_FLOW_REQUIREMENTS_INVALID"

_EFFECT_OBSERVERS = frozenset(
    {
        "after_state",
        "before_state",
        "business_effect",
        "entity_state",
        "final_state",
    }
)
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unique_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        marker = _fingerprint(row)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(row)
    return result


def _block(
    experiment: dict[str, Any],
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    result = deepcopy(experiment)
    receipt = _dict(result.get("compile_receipt"))
    result["compile_receipt"] = {
        **receipt,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
        "compile_freeze_status": "BLOCKED",
    }
    result["compile_freeze_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "detail": detail,
    }
    return result


def _operation_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }


def _is_ui_surface_step(step: dict[str, Any]) -> bool:
    """Whether a plan step is a browser page observation on the ui_browser
    surface.

    UI/UX rules constrain a rendered page, not an HTTP operation: the step
    carries the declared page URL (``ui_url``) and is executed by the
    ``ui_browser`` observer through a real browser navigation — there is no
    HTTP operation identity to resolve. Recognising the step structurally
    (protocol intent plus the URL field) keeps the freezer surface-aware for
    any UI plan, not just one protocol branch.
    """
    return (
        _text(step.get("protocol_step")) == "ui_open"
        or _text(step.get("intent")) == "ui_page_observation"
        or (
            _text(step.get("ui_url"))
            and not _text(step.get("operation_ref"))
        )
    )


def _ui_surface_operation_ref(step: dict[str, Any]) -> str:
    """Stable virtual operation identity for a browser page observation step.

    The page URL is the step's semantic identity; the virtual ref mirrors the
    URL so the flow-requirements ledger stays a stable, replayable identity
    without pretending the browser step is an HTTP operation.
    """
    raw = _text(step.get("ui_url"))
    return "ui_page:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _primary_operation_ref(experiment: dict[str, Any]) -> str:
    for assertion in _list(experiment.get("assertions")):
        row = _dict(assertion)
        prop = _dict(row.get("property"))
        binding = _dict(row.get("field_rule_binding"))
        value = _text(
            row.get("operation_ref")
            or prop.get("operation_ref")
            or binding.get("operation_id")
        )
        if value:
            return value
    operation_refs = {
        _text(_dict(step).get("operation_ref"))
        for phase in ("control", "treatment")
        for step in _list(experiment.get(f"{phase}_plan"))
        if _text(_dict(step).get("operation_ref"))
    }
    return next(iter(operation_refs)) if len(operation_refs) == 1 else ""


def _observer_id(observer: dict[str, Any]) -> str:
    return _text(observer.get("observer_id") or observer.get("id"))


def _explicit_observer_operation_scope(observer: dict[str, Any]) -> set[str]:
    scope: set[str] = set()
    for key in (
        "source_operation_ref",
        "write_operation_ref",
        "observed_operation_ref",
        "target_operation_ref",
    ):
        value = _text(observer.get(key))
        if value:
            scope.add(value)
    for key in (
        "source_operation_refs",
        "write_operation_refs",
        "observed_operation_refs",
        "target_operation_refs",
    ):
        scope.update(
            _text(value)
            for value in _list(observer.get(key))
            if _text(value)
        )
    return scope


def _step_observer_requirements(step: dict[str, Any]) -> tuple[set[str], list[dict[str, Any]]]:
    ids: set[str] = set()
    inline: list[dict[str, Any]] = []
    for raw in _list(step.get("observer_requirements")):
        if isinstance(raw, str):
            if _text(raw):
                ids.add(_text(raw))
        elif isinstance(raw, dict):
            observer = dict(raw)
            observer_id = _observer_id(observer)
            if observer_id:
                ids.add(observer_id)
            inline.append(observer)
    return ids, inline


def _applicable_observers(
    *,
    step: dict[str, Any],
    observers: list[dict[str, Any]],
    primary_operation_ref: str,
    explicitly_claimed_observer_ids: set[str],
) -> list[dict[str, Any]]:
    operation_ref = _text(step.get("operation_ref"))
    required_ids, inline = _step_observer_requirements(step)
    applicable = list(inline)
    for observer in observers:
        observer_id = _observer_id(observer)
        explicit_scope = _explicit_observer_operation_scope(observer)
        if required_ids and observer_id in required_ids:
            applicable.append(observer)
            continue
        if explicit_scope and operation_ref in explicit_scope:
            applicable.append(observer)
            continue
        if (
            not explicit_scope
            and operation_ref
            and operation_ref == primary_operation_ref
            and observer_id not in explicitly_claimed_observer_ids
            and (
                observer_id in _EFFECT_OBSERVERS
                or bool(observer.get("readback_contract_id"))
                or bool(observer.get("resolver_operations"))
            )
        ):
            applicable.append(observer)
    return _unique_dicts(applicable)


def _normalize_async_policy(value: Any) -> dict[str, Any]:
    raw = _dict(value)
    if not raw:
        return {
            "enabled": False,
            "expected_max_delay_ms": 0,
            "poll_interval_ms": 0,
            "max_attempts": 1,
            "terminal_condition": "immediate",
        }
    enabled = raw.get("enabled") is True
    return {
        "enabled": enabled,
        "expected_max_delay_ms": int(raw.get("expected_max_delay_ms") or 0),
        "poll_interval_ms": int(raw.get("poll_interval_ms") or 0),
        "max_attempts": int(raw.get("max_attempts") or 1),
        "required_stable_observations": int(
            raw.get("required_stable_observations")
            or raw.get("stable_observations")
            or 1
        ),
        "terminal_condition": _text(
            raw.get("terminal_condition") or "immediate"
        ),
    }


def _resolver_operations(observers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for observer in observers:
        for raw in _list(observer.get("resolver_operations")):
            row = _dict(raw)
            method = _text(row.get("method")).upper()
            path = _text(row.get("path") or row.get("endpoint_template"))
            operation_ref = _text(
                row.get("operation_ref")
                or row.get("operation_id")
                or row.get("id")
            )
            if method not in _READ_METHODS or not path.startswith("/"):
                continue
            rows.append(
                {
                    "operation_ref": operation_ref,
                    "method": method,
                    "path": path,
                    "readback_contract_id": _text(
                        row.get("readback_contract_id")
                    ),
                    "readback_surface_type": _text(
                        row.get("readback_surface_type")
                    ),
                }
            )
    return _unique_dicts(rows)


def _observer_contract(
    observers: list[dict[str, Any]],
    resolver_operations: list[dict[str, Any]],
    async_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "observer_ids": [
            observer_id
            for observer_id in (_observer_id(row) for row in observers)
            if observer_id
        ],
        "readback_contract_ids": [
            contract_id
            for contract_id in (
                _text(row.get("readback_contract_id")) for row in observers
            )
            if contract_id
        ],
        "resolver_operations": resolver_operations,
        "identity_bindings": [
            _dict(row.get("identity_bindings"))
            for row in observers
            if _dict(row.get("identity_bindings"))
        ],
        "required_fields": sorted(
            {
                _text(field.get("field") if isinstance(field, dict) else field)
                for row in observers
                for field in _list(row.get("required_fields"))
                if _text(field.get("field") if isinstance(field, dict) else field)
            }
        ),
        "scope_validations": [
            _dict(row.get("scope_validation"))
            for row in observers
            if _dict(row.get("scope_validation"))
        ],
        "async_policy": async_policy,
        "provenance_fingerprints": [
            value
            for value in (
                _text(row.get("provenance_fingerprint")) for row in observers
            )
            if value
        ],
    }


def _freeze_plan_step(
    *,
    step: dict[str, Any],
    observers: list[dict[str, Any]],
    primary_operation_ref: str,
    explicitly_claimed_observer_ids: set[str],
) -> tuple[dict[str, Any] | None, str, str, dict[str, Any]]:
    frozen = deepcopy(step)
    applicable = _applicable_observers(
        step=frozen,
        observers=observers,
        primary_operation_ref=primary_operation_ref,
        explicitly_claimed_observer_ids=explicitly_claimed_observer_ids,
    )
    policies = _unique_dicts(
        [
            _normalize_async_policy(row.get("async_policy"))
            for row in applicable
            if isinstance(row.get("async_policy"), dict)
        ]
    )
    if len(policies) > 1:
        return (
            None,
            BLOCKED_READBACK_CONTRACT_AMBIGUOUS,
            f"step={_text(frozen.get('step_id'))}:async_policy_count={len(policies)}",
            {},
        )
    policy = policies[0] if policies else _normalize_async_policy(None)
    resolver_operations = _resolver_operations(applicable)
    if policy["enabled"] and len(resolver_operations) != 1:
        return (
            None,
            (
                BLOCKED_READBACK_CONTRACT_INCOMPLETE
                if not resolver_operations
                else BLOCKED_READBACK_CONTRACT_AMBIGUOUS
            ),
            (
                f"step={_text(frozen.get('step_id'))}:"
                f"resolver_operation_count={len(resolver_operations)}"
            ),
            {},
        )

    contract = _observer_contract(
        applicable,
        resolver_operations,
        policy,
    )
    if applicable:
        runtime_body_plan = deepcopy(_dict(frozen.get("runtime_body_plan")))
        runtime_body_plan["readback_contract"] = contract
        runtime_body_plan["async_policy"] = policy
        frozen["runtime_body_plan"] = runtime_body_plan
        frozen["readback_contract"] = contract
        frozen["async_policy"] = policy
    binding = {
        "step_id": _text(frozen.get("step_id") or frozen.get("id")),
        "operation_ref": _text(frozen.get("operation_ref")),
        "observer_ids": contract["observer_ids"],
        "resolver_operation_refs": [
            _text(row.get("operation_ref")) for row in resolver_operations
        ],
        "async_policy": policy,
        "bound": bool(applicable),
    }
    return frozen, "", "", binding


def freeze_compiled_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Freeze all flow requirements after the existing compiler succeeds."""
    source = deepcopy(_dict(experiment))
    if _text(_dict(source.get("compile_receipt")).get("status")) != "COMPILED":
        return source

    observers = [
        dict(row)
        for row in _list(source.get("observers"))
        if isinstance(row, dict)
    ]
    primary_operation_ref = _primary_operation_ref(source)
    operations = _operation_index(behavior_ir)
    explicitly_claimed_observer_ids: set[str] = set()
    for phase in ("precondition", "control", "treatment"):
        for raw_step in _list(source.get(f"{phase}_plan")):
            if not isinstance(raw_step, dict):
                continue
            claimed_ids, _ = _step_observer_requirements(raw_step)
            explicitly_claimed_observer_ids.update(claimed_ids)
    frozen_plans: dict[str, list[dict[str, Any]]] = {}
    step_bindings: list[dict[str, Any]] = []
    required_step_ids: list[str] = []
    write_step_ids: list[str] = []
    operation_refs: list[str] = []

    for phase in ("precondition", "control", "treatment"):
        key = f"{phase}_plan"
        frozen_rows: list[dict[str, Any]] = []
        seen_step_ids: set[str] = set()
        for raw in _list(source.get(key)):
            if not isinstance(raw, dict):
                continue
            step = dict(raw)
            step_id = _text(step.get("step_id") or step.get("id"))
            if not step_id or step_id in seen_step_ids:
                return _block(
                    source,
                    BLOCKED_FLOW_REQUIREMENTS_INVALID,
                    f"{phase}:invalid_step_id:{step_id or 'missing'}",
                )
            seen_step_ids.add(step_id)
            # Browser page-observation steps (protocol_step=ui_open) carry no
            # HTTP operation: the ui_browser observer navigates the declared
            # URL directly. Demanding an IR operation index entry for them was
            # a structural break — every UI rule died at freeze time as
            # operation_unresolved:missing before its observer ever ran. The
            # step's stable virtual ref keeps the flow ledger intact.
            ui_surface_step = _is_ui_surface_step(step)
            operation_ref = _text(step.get("operation_ref"))
            if ui_surface_step:
                operation_ref = _ui_surface_operation_ref(step)
                step = dict(step)
                step["operation_ref"] = operation_ref
            elif not operation_ref or operation_ref not in operations:
                return _block(
                    source,
                    BLOCKED_FLOW_REQUIREMENTS_INVALID,
                    f"{phase}:{step_id}:operation_unresolved:{operation_ref or 'missing'}",
                )
            frozen, reason, detail, binding = _freeze_plan_step(
                step=step,
                observers=observers,
                primary_operation_ref=primary_operation_ref,
                explicitly_claimed_observer_ids=explicitly_claimed_observer_ids,
            )
            if frozen is None:
                return _block(source, reason, detail)
            frozen_rows.append(frozen)
            step_bindings.append({"phase": phase, **binding})
            if phase in {"control", "treatment"}:
                required_step_ids.append(step_id)
            operation_refs.append(operation_ref)
            method = _text(step.get("method")).upper()
            if not method and not ui_surface_step:
                method = _text(
                    _dict(operations.get(operation_ref)).get("method")
                ).upper()
            if method in _WRITE_METHODS:
                write_step_ids.append(step_id)
        frozen_plans[key] = frozen_rows

    flow_requirements = {
        "required_step_ids": required_step_ids,
        "precondition_step_ids": [
            _text(row.get("step_id"))
            for row in frozen_plans["precondition_plan"]
        ],
        "write_step_ids": write_step_ids,
        "operation_refs": list(dict.fromkeys(operation_refs)),
        "observer_bindings": step_bindings,
        "cleanup_source_step_ids": [
            _text(row.get("source_step_id") or row.get("compensates_step_id"))
            for row in _list(source.get("cleanup_plan"))
            if isinstance(row, dict)
            and _text(row.get("source_step_id") or row.get("compensates_step_id"))
        ],
    }
    freeze_payload = {
        "experiment_id": _text(source.get("experiment_id")),
        "obligation_id": _text(source.get("obligation_id")),
        "primary_operation_ref": primary_operation_ref,
        "flow_requirements": flow_requirements,
    }
    freeze_fingerprint = _fingerprint(freeze_payload)
    source.update(frozen_plans)
    source["flow_requirements"] = flow_requirements
    source["compile_freeze_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "status": "FROZEN",
        "freeze_fingerprint": freeze_fingerprint,
        **freeze_payload,
    }
    source["compile_receipt"] = {
        **_dict(source.get("compile_receipt")),
        "compile_freeze_status": "FROZEN",
        "compile_freeze_fingerprint": freeze_fingerprint,
    }
    return source
