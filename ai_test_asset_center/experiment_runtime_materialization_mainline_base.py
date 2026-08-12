"""Runtime materialization of ABSTRACT experiments before concrete compile.

Planning-stage authority: establish fixture/actor/state/observer/cleanup
capabilities (and prerequisite plans) before re-invoking the concrete compiler.
Reuses disposable fixture contracts, prerequisite planner, and cleanup ladder.
Does not create a parallel discovery pipeline.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .abstract_experiment import (
    MATERIALIZATION_SCHEMA,
    attach_passthrough_materialization,
    build_materialization_receipt,
    is_capability_gap_reason,
    promote_blocked_to_abstract,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in _list(values):
        item = _text(value)
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _index_ops(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(behavior_ir.get("operations"))
        if isinstance(row, dict) and _text(row.get("id") or row.get("operation_id"))
    }


def _index_actors(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("id") or row.get("actor_id")): row
        for row in _list(behavior_ir.get("actors"))
        if isinstance(row, dict) and _text(row.get("id") or row.get("actor_id"))
    }


def _op_path(operation: dict[str, Any]) -> str:
    return _text(operation.get("path") or operation.get("raw_path"))


def _op_method(operation: dict[str, Any]) -> str:
    return _text(operation.get("method") or operation.get("http_method")).upper()


def hashlib_fingerprint(value: str) -> str:
    import hashlib

    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()[:16]


def _fixture_create_candidates(
    fixture_name: str,
    ops_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    token = fixture_name.lower().replace("fixture:", "").replace("_", "")
    candidates: list[dict[str, Any]] = []
    for op_id, operation in ops_by_id.items():
        method = _op_method(operation)
        path = _op_path(operation)
        if method != "POST" or not path.startswith("/") or "{" in path:
            continue
        hay = f"{op_id} {path} {_text(operation.get('name'))}".lower().replace("_", "")
        path_compact = path.lower().replace("_", "").replace("-", "").replace("/", "")
        if token and token not in hay and token not in path_compact:
            continue
        candidates.append(
            {
                "fixture_id": fixture_name,
                "name": fixture_name,
                "target": f"fixture:{fixture_name}",
                "create_path": path,
                "create_operation_ref": op_id,
                "method": method,
                "materialization_source": "behavior_ir_post_operation",
                "established_before_concrete_compile": True,
            }
        )
    return candidates


def _match_disposable_candidate(
    fixture_name: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    token = fixture_name.lower().replace("fixture:", "").replace("_", "-")
    compact = token.replace("-", "").replace("_", "")
    for row in candidates:
        if not isinstance(row, dict):
            continue
        entity = _text(row.get("entity_id") or row.get("entity") or row.get("collection"))
        entity_compact = entity.lower().replace("_", "").replace("-", "")
        if entity and (
            entity.lower() == token or compact in entity_compact or entity_compact in compact
        ):
            return row
        create_op = _text(row.get("create_operation_id") or row.get("create_operation_ref"))
        if compact and compact in create_op.lower().replace("_", "").replace("-", ""):
            return row
    if len(candidates) == 1:
        return candidates[0]
    return None


def _build_state_establishment_steps(obligation: dict[str, Any]) -> list[dict[str, Any]]:
    prop = _dict(obligation.get("property"))
    steps: list[dict[str, Any]] = []
    for key in (
        "required_pre_state",
        "pre_state",
        "from_state",
        "source_state",
        "target_pre_state",
    ):
        value = prop.get(key)
        if isinstance(value, dict) and value:
            steps.append(
                {
                    "kind": "state_establishment",
                    "source_field": key,
                    "target_state": value,
                    "status": "PLANNED",
                    "established_before_concrete_compile": True,
                }
            )
        elif _text(value):
            steps.append(
                {
                    "kind": "state_establishment",
                    "source_field": key,
                    "target_state": {"state": _text(value)},
                    "status": "PLANNED",
                    "established_before_concrete_compile": True,
                }
            )
    for row in _list(prop.get("state_establishment_steps") or obligation.get("preconditions")):
        if isinstance(row, dict):
            steps.append(
                {
                    **row,
                    "status": _text(row.get("status")) or "PLANNED",
                    "established_before_concrete_compile": True,
                }
            )
    return steps


def _resolve_cleanup_authority(
    *,
    obligation: dict[str, Any],
    ops: dict[str, dict[str, Any]],
    available_adapters: Any,
    reason: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    unresolved: list[dict[str, Any]] = []
    cleanup_req = obligation.get("cleanup_requirement")
    required = bool(_dict(cleanup_req).get("required")) or _text(cleanup_req).lower() in {
        "required",
        "true",
        "1",
        "yes",
    }
    if reason in {
        "BLOCKED_NON_REVERSIBLE_WRITE",
        "BLOCKED_INVALID_CLEANUP_PLAN",
        "BLOCKED_STEP_CLEANUP_UNCOVERED",
    }:
        required = True
    if not required and reason not in {
        "BLOCKED_NON_REVERSIBLE_WRITE",
        "BLOCKED_INVALID_CLEANUP_PLAN",
        "BLOCKED_STEP_CLEANUP_UNCOVERED",
    }:
        return {"required": False, "authority_resolved": True, "tier": ""}, unresolved

    delete_ops = [
        {
            "operation_ref": op_id,
            "method": "DELETE",
            "path": _op_path(operation),
        }
        for op_id, operation in ops.items()
        if _op_method(operation) == "DELETE" and _op_path(operation).startswith("/")
    ]
    api_compensator = delete_ops[0] if delete_ops else None
    try:
        from .cleanup_adapter_ladder import resolve_cleanup_adapter

        ladder = resolve_cleanup_adapter(
            available_adapters=available_adapters or {"http_api"},
            api_compensator=api_compensator,
            availability_only=True,
            target_write_approved=True,
        )
    except Exception as exc:
        unresolved.append(
            {
                "kind": "cleanup",
                "ref": _text(obligation.get("obligation_id")),
                "reason": f"CLEANUP_LADDER_ERROR:{type(exc).__name__}",
            }
        )
        return {"required": True, "authority_resolved": False}, unresolved

    if _text(ladder.get("tier")):
        return {
            "required": True,
            "authority_resolved": True,
            "tier": _text(ladder.get("tier")),
            "plan": _dict(ladder.get("plan")),
            "cleanup_operation_refs": [
                _text(row.get("operation_ref"))
                for row in delete_ops
                if _text(row.get("operation_ref"))
            ][:5],
            "availability_only": True,
            "established_before_concrete_compile": True,
        }, unresolved

    if not required:
        return {"required": False, "authority_resolved": True, "tier": ""}, unresolved

    unresolved.append(
        {
            "kind": "cleanup",
            "ref": _text(obligation.get("obligation_id")),
            "reason": _text(ladder.get("reason_code")) or "CLEANUP_AUTHORITY_UNRESOLVED",
            "detail": _text(ladder.get("detail")),
        }
    )
    return {
        "required": True,
        "authority_resolved": False,
        "reason_code": _text(ladder.get("reason_code")),
    }, unresolved


def _resolve_fixture_binding(
    *,
    fixture_name: str,
    obligation: dict[str, Any],
    abstract_experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    ops: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    unresolved: list[dict[str, Any]] = []
    extras: list[dict[str, Any]] = []
    discovered: list[dict[str, Any]] = []
    matched: dict[str, Any] | None = None
    try:
        from . import disposable_fixture_contract_core as fixture_core

        entity_hint = fixture_name.replace("fixture:", "")
        discovered = fixture_core.discover_fixture_candidates(
            behavior_ir,
            entity_ids=[entity_hint],
        )
        if not discovered:
            discovered = fixture_core.discover_fixture_candidates(behavior_ir)
        matched = _match_disposable_candidate(fixture_name, discovered)
    except Exception:
        matched = None

    if matched:
        create_op_id = _text(
            matched.get("create_operation_id") or matched.get("create_operation_ref")
        )
        create_op = _dict(ops.get(create_op_id))
        create_path = _op_path(create_op)
        if create_path.startswith("/") and "{" not in create_path:
            try:
                from . import disposable_fixture_contract_core as fixture_core

                contract = fixture_core.build_disposable_fixture_contract(
                    obligation_id=_text(obligation.get("obligation_id")),
                    experiment_id=_text(abstract_experiment.get("experiment_id")),
                    campaign_id="",
                    candidate=matched,
                    behavior_ir=behavior_ir,
                    actor_ref=_text((_unique(obligation.get("required_actors")) or [""])[0]),
                )
            except Exception:
                contract = {}
            prereq: dict[str, Any] = {}
            if create_op:
                try:
                    from .enterprise_test_data_constructor import plan_prerequisite_data

                    prereq = plan_prerequisite_data(create_op, behavior_ir)
                except Exception as exc:
                    prereq = {"status": "PLAN_FAILED", "error": type(exc).__name__}
            binding = {
                "fixture_id": fixture_name,
                "name": fixture_name,
                "target": f"fixture:{fixture_name}",
                "create_path": create_path,
                "create_operation_ref": create_op_id,
                "method": _op_method(create_op) or "POST",
                "materialization_source": "disposable_fixture_contract",
                "disposable_fixture_contract_id": _text(contract.get("contract_id")),
                "prerequisite_plan": {
                    "total_prerequisites": prereq.get("total_prerequisites"),
                    "execution_plan_steps": len(_list(prereq.get("execution_plan"))),
                    "cleanup_plan_steps": len(_list(prereq.get("cleanup_plan"))),
                    "status": _text(prereq.get("status")) or "PLANNED",
                },
                "established_before_concrete_compile": True,
            }
            extras.append(binding)
            return binding, extras, unresolved

    heuristic = _fixture_create_candidates(fixture_name, ops)
    if heuristic:
        chosen = dict(heuristic[0])
        create_op = _dict(ops.get(_text(chosen.get("create_operation_ref"))))
        if create_op:
            try:
                from .enterprise_test_data_constructor import plan_prerequisite_data

                prereq = plan_prerequisite_data(create_op, behavior_ir)
                chosen["prerequisite_plan"] = {
                    "total_prerequisites": prereq.get("total_prerequisites"),
                    "execution_plan_steps": len(_list(prereq.get("execution_plan"))),
                    "cleanup_plan_steps": len(_list(prereq.get("cleanup_plan"))),
                    "status": "PLANNED",
                }
            except Exception:
                pass
        extras.append(chosen)
        return chosen, extras, unresolved

    unresolved.append(
        {
            "kind": "fixture",
            "ref": fixture_name,
            "reason": "NO_CONCRETE_CREATE_OPERATION",
            "discovered_candidate_count": len(discovered),
        }
    )
    return None, extras, unresolved


def _resolve_actor_credentials(
    *,
    actor_id: str,
    actor: dict[str, Any],
    planning_context: dict[str, Any],
    _actor_tokens: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    unresolved: list[dict[str, Any]] = []
    role = _text(actor.get("role")).lower()
    actor_binding = {
        "actor_ref": actor_id,
        "role": role,
        "account_ref": _text(actor.get("account_ref")),
        "established_before_concrete_compile": True,
    }
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if role in {"anonymous", "public"}:
        return actor_binding, {"kind": "anonymous", "present": True}, unresolved
    if not secret_ref:
        unresolved.append(
            {
                "kind": "credential",
                "ref": actor_id,
                "reason": "MISSING_CREDENTIAL_SECRET_REF",
            }
        )
        return actor_binding, {}, unresolved

    credential = {
        "kind": "secret_ref",
        "present": True,
        "secret_ref_fingerprint": hashlib_fingerprint(secret_ref),
        "token_loaded": False,
    }
    root = planning_context.get("root")
    project = _text(planning_context.get("project"))
    base_url = _text(planning_context.get("base_url"))
    if root and project:
        try:
            if _actor_tokens is None:
                # SPEC-11 4.3: the rescue batch loads the token catalog ONCE and
                # threads the map through every abstract row; a per-row load here
                # is the fallback for direct callers (file parse + possible HTTP
                # login per actor per row otherwise).
                from .experiment_runtime_support import load_actor_tokens

                _actor_tokens = load_actor_tokens(
                    Path(root), project, base_url=base_url
                )
            tokens = _actor_tokens
            account_ref = _text(actor.get("account_ref"))
            # Never persist token values — only presence for materialization gate.
            token_present = bool(
                tokens.get(actor_id)
                or tokens.get(account_ref)
                or any(_text(key) for key in tokens)
            )
            credential["token_loaded"] = bool(
                tokens.get(actor_id) or (account_ref and tokens.get(account_ref))
            )
            credential["token_catalog_nonempty"] = token_present
        except Exception as exc:
            credential["token_load_error"] = type(exc).__name__
    return actor_binding, credential, unresolved


def _resolve_planning_materialization(
    *,
    obligation: dict[str, Any],
    abstract_experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
    planning_context: dict[str, Any] | None = None,
    _actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    context = _dict(planning_context)
    ops = _index_ops(behavior_ir)
    actors = _index_actors(behavior_ir)
    reason = _text(_dict(abstract_experiment.get("compile_receipt")).get("reason_code"))
    capabilities = _dict(
        _dict(abstract_experiment.get("abstract_experiment")).get("required_capabilities")
    )
    if not capabilities:
        capabilities = {
            "operations": _unique(obligation.get("required_operations")),
            "actors": _unique(obligation.get("required_actors")),
            "fixtures": _unique(obligation.get("required_fixtures")),
            "observers": _unique(obligation.get("required_observers")),
            "cleanup": [],
        }

    unresolved: list[dict[str, Any]] = []
    actor_bindings: dict[str, Any] = {}
    credential_bindings: dict[str, Any] = {}
    fixture_bindings: dict[str, Any] = {}
    operation_bindings: dict[str, Any] = {}
    observer_bindings: dict[str, Any] = {}
    binding_plan_extras: list[dict[str, Any]] = []
    state_steps = _build_state_establishment_steps(obligation)

    for op_id in _unique(capabilities.get("operations")):
        if op_id in ops:
            operation_bindings[op_id] = {
                "operation_ref": op_id,
                "path": _op_path(ops[op_id]),
                "method": _op_method(ops[op_id]),
                "established_before_concrete_compile": True,
            }
        else:
            unresolved.append(
                {"kind": "operation", "ref": op_id, "reason": "OPERATION_NOT_IN_BEHAVIOR_IR"}
            )

    for actor_id in _unique(capabilities.get("actors")):
        actor = _dict(actors.get(actor_id))
        if not actor:
            unresolved.append(
                {"kind": "actor", "ref": actor_id, "reason": "ACTOR_NOT_IN_BEHAVIOR_IR"}
            )
            continue
        actor_binding, credential, actor_unresolved = _resolve_actor_credentials(
            actor_id=actor_id,
            actor=actor,
            planning_context=context,
            _actor_tokens=_actor_tokens,
        )
        actor_bindings[actor_id] = actor_binding
        if credential:
            credential_bindings[actor_id] = credential
        unresolved.extend(actor_unresolved)

    for fixture in _unique(capabilities.get("fixtures")):
        binding, extras, fixture_unresolved = _resolve_fixture_binding(
            fixture_name=fixture,
            obligation=obligation,
            abstract_experiment=abstract_experiment,
            behavior_ir=behavior_ir,
            ops=ops,
        )
        unresolved.extend(fixture_unresolved)
        binding_plan_extras.extend(extras)
        if binding:
            fixture_bindings[fixture] = binding

    try:
        from .observer_contracts_base import OBSERVER_REGISTRY
    except Exception:
        OBSERVER_REGISTRY = {}
    for observer_id in _unique(capabilities.get("observers")):
        contract = _dict(OBSERVER_REGISTRY.get(observer_id))
        if contract.get("implemented") is True:
            observer_bindings[observer_id] = {
                "observer_id": observer_id,
                "implemented": True,
                "adapter": contract.get("adapter"),
                "established_before_concrete_compile": True,
            }
        else:
            unresolved.append(
                {
                    "kind": "observer",
                    "ref": observer_id,
                    "reason": "OBSERVER_NOT_IMPLEMENTED",
                }
            )

    cleanup_plan, cleanup_unresolved = _resolve_cleanup_authority(
        obligation=obligation,
        ops=ops,
        available_adapters=context.get("available_adapters"),
        reason=reason,
    )
    unresolved.extend(cleanup_unresolved)

    if reason in {"BLOCKED_PRECONDITION_UNREACHABLE", "STATE_RULE_PRECONDITION_NOT_ESTABLISHED"}:
        if not state_steps:
            unresolved.append(
                {
                    "kind": "precondition",
                    "ref": _text(obligation.get("obligation_id")),
                    "reason": reason,
                }
            )

    blocker_covered = False
    if reason == "BLOCKED_MISSING_FIXTURE":
        blocker_covered = bool(binding_plan_extras) and not any(
            _text(row.get("kind")) == "fixture" for row in unresolved
        )
    elif reason == "BLOCKED_MISSING_ACTOR":
        blocker_covered = bool(actor_bindings) and not any(
            _text(row.get("kind")) in {"actor", "credential"} for row in unresolved
        )
    elif reason in {"BLOCKED_MISSING_OBSERVER", "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE"}:
        blocker_covered = bool(observer_bindings) and not any(
            _text(row.get("kind")) == "observer" for row in unresolved
        )
    # BLOCKED_CONTROL_ARM_NOT_PROVEN is deliberately absent: binding an observer
    # does not prove a control arm succeeded, so it must not count as covered.
    elif reason == "BLOCKED_MISSING_BINDING":
        blocker_covered = bool(binding_plan_extras or operation_bindings) and not any(
            _text(row.get("kind")) in {"operation", "fixture"} for row in unresolved
        )
    elif reason == "BLOCKED_MISSING_OPERATION":
        blocker_covered = bool(operation_bindings) and not any(
            _text(row.get("kind")) == "operation" for row in unresolved
        )
    elif reason in {
        "BLOCKED_NON_REVERSIBLE_WRITE",
        "BLOCKED_INVALID_CLEANUP_PLAN",
        "BLOCKED_STEP_CLEANUP_UNCOVERED",
    }:
        blocker_covered = bool(cleanup_plan.get("authority_resolved")) and not any(
            _text(row.get("kind")) == "cleanup" for row in unresolved
        )
    elif reason in {
        "BLOCKED_PRECONDITION_UNREACHABLE",
        "STATE_RULE_PRECONDITION_NOT_ESTABLISHED",
    }:
        blocker_covered = bool(state_steps) and not any(
            _text(row.get("kind")) == "precondition" for row in unresolved
        )
    elif not unresolved:
        blocker_covered = True

    receipt_unresolved = unresolved
    if blocker_covered and reason == "BLOCKED_MISSING_FIXTURE":
        receipt_unresolved = [
            row for row in unresolved if _text(row.get("kind")) != "fixture"
        ]

    receipt = build_materialization_receipt(
        experiment_id=_text(abstract_experiment.get("experiment_id")),
        obligation_id=_text(
            abstract_experiment.get("obligation_id") or obligation.get("obligation_id")
        ),
        status="MATERIALIZED" if blocker_covered else "NOT_MATERIALIZED",
        actor_bindings=actor_bindings,
        credential_bindings=credential_bindings,
        fixture_bindings=fixture_bindings,
        state_establishment_steps=state_steps,
        operation_bindings=operation_bindings,
        observer_bindings=observer_bindings,
        cleanup_plan=cleanup_plan,
        unresolved_requirements=receipt_unresolved if blocker_covered else unresolved,
        source_blocker=reason,
    )
    if blocker_covered:
        receipt["status"] = "MATERIALIZED"
    receipt["capabilities_established_before_concrete_compile"] = True

    return {
        "materialization_receipt": receipt,
        "binding_plan_extras": binding_plan_extras,
        "can_recompile": bool(blocker_covered) and _text(receipt.get("status")) == "MATERIALIZED",
        "schema_version": MATERIALIZATION_SCHEMA,
    }


def materialize_and_recompile_abstract_pack(
    pack: dict[str, Any],
    *,
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
    compile_one: Callable[..., dict[str, Any]],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: Any = None,
    planning_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize ABSTRACT experiments and recompile when capabilities resolve."""

    context = dict(_dict(planning_context))
    if available_adapters is not None:
        context.setdefault("available_adapters", available_adapters)

    result = deepcopy(_dict(pack))
    compiled = [
        attach_passthrough_materialization(row)
        for row in _list(result.get("experiments"))
        if isinstance(row, dict)
    ]
    blocked = [row for row in _list(result.get("blocked_experiments")) if isinstance(row, dict)]
    abstract = [row for row in _list(result.get("abstract_experiments")) if isinstance(row, dict)]

    remaining_blocked: list[dict[str, Any]] = []
    obligations_by_id = {
        _text(row.get("obligation_id")): row
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    for row in blocked:
        reason = _text(_dict(row.get("compile_receipt")).get("reason_code"))
        if is_capability_gap_reason(reason) and _text(
            _dict(row.get("compile_receipt")).get("status")
        ).upper() != "ABSTRACT":
            oid = _text(row.get("obligation_id"))
            abstract.append(promote_blocked_to_abstract(row, obligations_by_id.get(oid)))
        elif _text(_dict(row.get("compile_receipt")).get("status")).upper() == "ABSTRACT":
            abstract.append(row)
        else:
            remaining_blocked.append(row)

    materialization_receipts: list[dict[str, Any]] = []
    still_abstract: list[dict[str, Any]] = []
    recompiled = 0

    # SPEC-11 4.3: the token catalog is loaded ONCE per rescue batch and shared
    # by every actor resolution (per-row loads meant file parses and possible
    # HTTP logins per actor per row). Load failures fall back to per-row loads.
    _actor_tokens: dict[str, str] | None = None
    _root = context.get("root")
    _project = _text(context.get("project"))
    if _root and _project:
        try:
            from .experiment_runtime_support import load_actor_tokens

            _actor_tokens = load_actor_tokens(
                Path(_root), _project, base_url=_text(context.get("base_url"))
            )
        except Exception as exc:  # noqa: BLE001 - per-row fallback
            logger.warning("rescue token catalog load failed: %s", exc)

    # SPEC-11 4.2: the rescue loop re-invokes compile_one per abstract row, so
    # it activates the same per-batch index bundle the compile phase used.
    from .compile_batch_context import (
        build_batch_indexes,
        reset_batch_indexes,
        set_batch_indexes,
    )

    _indexes_token = set_batch_indexes(build_batch_indexes(behavior_ir))

    def _rescue_loop() -> None:
        nonlocal recompiled
        for abstract_exp in abstract:
            oid = _text(abstract_exp.get("obligation_id"))
            obligation = deepcopy(_dict(obligations_by_id.get(oid)))
            if not obligation:
                still_abstract.append(abstract_exp)
                continue
            resolution = _resolve_planning_materialization(
                obligation=obligation,
                abstract_experiment=abstract_exp,
                behavior_ir=behavior_ir,
                planning_context=context,
                _actor_tokens=_actor_tokens,
            )
            receipt = dict(resolution["materialization_receipt"])
            materialization_receipts.append(receipt)
            enriched = dict(abstract_exp)
            enriched["materialization_receipt"] = receipt

            if not resolution.get("can_recompile"):
                enriched["compile_receipt"] = {
                    **_dict(enriched.get("compile_receipt")),
                    "status": "ABSTRACT",
                    "awaiting_materialization": True,
                    "materialization_status": receipt.get("status"),
                }
                still_abstract.append(enriched)
                continue

            obligation = dict(obligation)
            existing_bindings = [
                dict(row)
                for row in _list(obligation.get("binding_plan"))
                if isinstance(row, dict)
            ]
            obligation["_planning_materialization"] = {
                "schema_version": MATERIALIZATION_SCHEMA,
                "receipt": receipt,
                "binding_plan_extras": list(
                    resolution.get("binding_plan_extras") or []
                ),
                "state_establishment_steps": list(
                    _list(receipt.get("state_establishment_steps"))
                ),
                "cleanup_plan": _dict(receipt.get("cleanup_plan")),
            }
            obligation["binding_plan"] = existing_bindings + list(
                resolution.get("binding_plan_extras") or []
            )
            prop = dict(_dict(obligation.get("property")))
            prop["planning_materialization_bindings"] = list(
                resolution.get("binding_plan_extras") or []
            )
            if receipt.get("state_establishment_steps"):
                prop["state_establishment_steps"] = list(
                    receipt.get("state_establishment_steps") or []
                )
            obligation["property"] = prop

            concrete = compile_one(
                obligation,
                behavior_ir=behavior_ir,
                environment_type=environment_type,
                policy_version=policy_version,
                available_adapters=available_adapters,
            )
            concrete_receipt = _dict(concrete.get("compile_receipt"))
            concrete_status = _text(concrete_receipt.get("status")).upper()
            if concrete_status == "COMPILED":
                concrete = dict(concrete)
                concrete["materialization_receipt"] = {
                    **receipt,
                    "status": "MATERIALIZED",
                    "recompiled": True,
                }
                concrete["experiment_phase"] = "CONCRETE"
                concrete["abstract_experiment"] = _dict(
                    abstract_exp.get("abstract_experiment")
                )
                compiled.append(concrete)
                recompiled += 1
                obl_row = obligations_by_id.get(oid)
                if isinstance(obl_row, dict):
                    obl_row["compile_status"] = "COMPILED"
                    obl_row["block_reason"] = ""
            elif is_capability_gap_reason(concrete_receipt.get("reason_code")):
                retained = promote_blocked_to_abstract(concrete, obligation)
                retained["materialization_receipt"] = {
                    **receipt,
                    "status": "NOT_MATERIALIZED",
                    "recompile_reason_code": concrete_receipt.get("reason_code"),
                    "recompile_detail": concrete_receipt.get("detail"),
                }
                still_abstract.append(retained)
            else:
                concrete = dict(concrete)
                concrete["materialization_receipt"] = receipt
                remaining_blocked.append(concrete)

    try:
        _rescue_loop()
    finally:
        reset_batch_indexes(_indexes_token)

    result["experiments"] = compiled
    result["blocked_experiments"] = remaining_blocked
    result["abstract_experiments"] = still_abstract
    result["compiled_count"] = len(compiled)
    result["blocked_count"] = len(remaining_blocked)
    result["abstract_count"] = len(still_abstract)
    result["materialization_receipts"] = materialization_receipts
    result["materialization_summary"] = {
        "schema_version": "qualibug.experiment-materialization-summary.v1",
        "abstract_input_count": len(abstract),
        "recompiled_count": recompiled,
        "still_abstract_count": len(still_abstract),
        "materialized_receipt_count": sum(
            1
            for row in materialization_receipts
            if _text(row.get("status")) == "MATERIALIZED"
        ),
        "not_materialized_receipt_count": sum(
            1
            for row in materialization_receipts
            if _text(row.get("status")) != "MATERIALIZED"
        ),
        "fixture_actor_state_observer_cleanup_front_loaded": True,
    }
    counts: dict[str, int] = {}
    for item in remaining_blocked + still_abstract:
        code = _text(_dict(item.get("compile_receipt")).get("reason_code")) or "UNKNOWN"
        counts[code] = counts.get(code, 0) + 1
    result["block_reason_counts"] = counts
    return result


__all__ = [
    "materialize_and_recompile_abstract_pack",
    "_resolve_planning_materialization",
]
