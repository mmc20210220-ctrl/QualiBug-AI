"""Compile one TestObligation into an ExecutableExperiment or block.

Extracted from ``experiment_compiler_base``. ``compile_experiment_for_obligation``
remains the single-obligation compile authority. Symbols are re-exported from
``experiment_compiler_base`` for star-import compatibility.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from .behavior_ir import source_identity_fields_for_operation
from .experiment_protocols import compile_family_protocol
from .observer_contracts_base import compile_observer_requirements
from .real_id_resolver import collection_path, normalize_path_placeholders

# Risk-family labels are not assertion kinds. Map only when the protocol did not
# already emit a concrete DSL kind.
_FAMILY_ASSERTION_KIND = {
    "authorization": "authorization",
    "isolation": "isolation",
    "visibility": "visibility",
    "privacy": "privacy",
    "validation": "validation_rejection",
    "state_integrity": "state_transition",
    "lifecycle": "state_transition",
    "consistency": "cross_surface_consistency",
    "invariant": "state_transition",
    "conservation": "conservation",
    "concurrency": "concurrency",
    "idempotency": "idempotency",
    "temporal": "temporal",
}
from .runtime_binding_graph import (
    blocked_binding_reasons,
    build_binding_plan,
    declared_action_compensators,
    declared_effect_observers,
    unresolved_placeholders,
)
from .experiment_compiler_support import (
    _actor_is_executable,
    _compensates_create_operation,
    _field_key,
    _index_by_id,
    _inverse_delta_cleanup_spec,
    _is_unresolvable_actor_secret_ref,
    _operation_entity_refs,
    _resolve_state_compile_context,
    _source_declared_control_fixture_binding,
    _source_request_example,
    _state_match_token,
    _state_semantic_value,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity_bound_delete_refs(
    *,
    primary_op_id: str,
    primary_path: str,
    ops: dict[str, dict[str, Any]],
) -> list[str]:
    """Return unique source-declared DELETE ops on the same collection identity."""

    op_collection = normalize_path_placeholders(collection_path(primary_path))
    if not op_collection.startswith("/"):
        return []
    delete_candidates: list[str] = []
    for cand_id, cand_op in ops.items():
        if cand_id == primary_op_id or not isinstance(cand_op, dict):
            continue
        if _text(cand_op.get("method")).upper() != "DELETE":
            continue
        cand_path = normalize_path_placeholders(
            _text(cand_op.get("path") or cand_op.get("raw_path"))
        )
        cand_collection = normalize_path_placeholders(collection_path(cand_path))
        identity_suffix = cand_path[len(op_collection) :]
        if (
            cand_collection == op_collection
            and re.fullmatch(r"/\{[A-Za-z_]\w*\}", identity_suffix)
        ):
            delete_candidates.append(cand_id)
    return delete_candidates


_EPHEMERAL_SESSION_SEGMENTS = frozenset({
    "login",
    "logout",
    "token",
    "refresh",
    "session",
    "otp",
    "captcha",
    "webhook",
    "callback",
})


def _is_ephemeral_session_path(path: str) -> bool:
    """True only for session/token style posts with no identity binding."""

    normalized = normalize_path_placeholders(_text(path)).lower().rstrip("/")
    if not normalized.startswith("/") or "{" in normalized:
        return False
    segments = [seg for seg in normalized.split("/") if seg]
    if not segments:
        return False
    return segments[-1] in _EPHEMERAL_SESSION_SEGMENTS


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: set[str] | None = None,
) -> dict[str, Any]:
    """Compile one obligation. Never silently degrade to a different probe."""

    obl = _dict(obligation)
    ir = _dict(behavior_ir)
    oid = _text(obl.get("obligation_id")) or "unknown_obligation"
    ops = _index_by_id(_list(ir.get("operations")))
    actors = _index_by_id(_list(ir.get("actors")))
    adapters = {"http_api"} if available_adapters is None else set(available_adapters)
    env = _text(environment_type).lower()
    if env in {"", "production", "prod", "live", "unknown"}:
        return blocked_experiment(
            oid,
            "BLOCKED_UNSUPPORTED_ADAPTER",
            "non_production_environment_required",
        )

    prop = _dict(obl.get("property"))
    family = _text(obl.get("risk_family"))
    required_ops = [
        _text(x) for x in _list(obl.get("required_operations")) if _text(x)
    ]
    required_actors = [
        _text(x) for x in _list(obl.get("required_actors")) if _text(x)
    ]
    required_fixtures = [
        _text(x) for x in _list(obl.get("required_fixtures")) if _text(x)
    ]
    required_observers = [
        _text(x) for x in _list(obl.get("required_observers")) if _text(x)
    ]
    primary_op_id = required_ops[0] if required_ops else _text(prop.get("operation_ref"))

    if family == "state":
        prop, required_actors, required_fixtures, state_reason = (
            _resolve_state_compile_context(
                behavior_ir=ir,
                property_spec=prop,
                operation_ref=primary_op_id,
                required_actors=required_actors,
                required_fixtures=required_fixtures,
            )
        )
        if state_reason:
            # ── Enhanced: try actor degradation for state family ──
            if "actor" in state_reason:
                _fallback = next(
                    (
                        aid for aid, a in actors.items()
                        if isinstance(a, dict)
                        and _actor_is_executable(a)
                        and _text(a.get("credential_secret_ref") or a.get("secret_ref"))
                    ),
                    None,
                )
                if _fallback:
                    required_actors = [_fallback]
                    prop = {**prop, "actor_ref": _fallback, "_actor_degraded": True}
                    state_reason = ""  # Clear the reason to proceed
            if state_reason:
                return blocked_experiment(
                    oid,
                    "BLOCKED_MISSING_BINDING"
                    if "actor" not in state_reason
                    else "BLOCKED_MISSING_ACTOR",
                    state_reason,
                )
        obl = {
            **obl,
            "property": prop,
            "required_actors": required_actors,
            "required_fixtures": required_fixtures,
        }

    # Resolve operation IDs that don't match IR IDs by method+path lookup
    _source_locators = [
        _text(s.get("locator")) for s in _list(obl.get("source_refs"))
        if isinstance(s, dict) and _text(s.get("kind")) == "api_operation"
        and _text(s.get("locator"))
    ]
    for i, op_id in enumerate(list(required_ops)):
        if op_id not in ops:
            # Try to find by method+path from source locators
            for loc in _source_locators:
                parts = loc.split(None, 1)
                if len(parts) == 2:
                    loc_method, loc_path = parts[0].upper(), parts[1].strip()
                    for ir_id, ir_op in ops.items():
                        if (isinstance(ir_op, dict)
                            and _text(ir_op.get("method")).upper() == loc_method
                            and normalize_path_placeholders(_text(ir_op.get("path") or ir_op.get("raw_path")))
                            == normalize_path_placeholders(loc_path)):
                            required_ops[i] = ir_id
                            break
                if required_ops[i] != op_id:
                    break
    for op_id in required_ops:
        if op_id not in ops:
            # Filter to only valid operations; update primary if needed.
            required_ops = [oid for oid in required_ops if oid in ops]
            if not required_ops:
                return blocked_experiment(oid, "BLOCKED_MISSING_OPERATION", "no_valid_operations")
            primary_op_id = required_ops[0]
            break
    if not required_actors and primary_op_id and family not in {"authorization", "isolation", "visibility"}:
        permitted_actor_ids = {
            _text(relation.get("actor_ref") or relation.get("from_ref"))
            for relation in _list(ir.get("relations"))
            if isinstance(relation, dict)
            and _text(relation.get("relation_type")) == "permits"
            and _text(relation.get("operation_ref")) == _text(primary_op_id)
            and _text(relation.get("status")) == "accepted"
            and _text(relation.get("actor_ref") or relation.get("from_ref"))
        }
        ranked_actors = sorted(
            (
                (
                    0 if _text(actors[actor_id].get("account_ref")) else 1,
                    0 if actors[actor_id].get("runtime_bound") is True else 1,
                    actor_id,
                )
                for actor_id in permitted_actor_ids
                if actor_id in actors and _actor_is_executable(actors[actor_id])
            )
        )
        if not ranked_actors:
            # Source permits may be absent for invariant-bound writes. Fall back
            # to executable runtime-bound test actors already present in IR.
            ranked_actors = sorted(
                (
                    (
                        0 if _text(actor.get("account_ref")) else 1,
                        0 if actor.get("runtime_bound") is True else 1,
                        actor_id,
                    )
                    for actor_id, actor in actors.items()
                    if isinstance(actor, dict) and _actor_is_executable(actor)
                )
            )
        if ranked_actors:
            required_actors = [ranked_actors[0][2]]
            prop = {
                **prop,
                "actor_ref": required_actors[0],
            }
    for actor_id in required_actors:
        if actor_id not in actors:
            # ── Enhanced: try fallback actor instead of hard-blocking ──
            _fallback = next(
                (
                    aid for aid, a in actors.items()
                    if isinstance(a, dict)
                    and _actor_is_executable(a)
                    and _text(a.get("role")).lower() in {"admin", "administrator", "superuser"}
                ),
                None,
            ) or next(
                (
                    aid for aid, a in actors.items()
                    if isinstance(a, dict) and _actor_is_executable(a)
                ),
                None,
            )
            if _fallback:
                required_actors = [_fallback if a == actor_id else a for a in required_actors]
                prop = {**prop, "actor_ref": _fallback, "_actor_degraded": True, "_original_actor": actor_id}
                actor_id = _fallback
            else:
                return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", actor_id)
        secret_ref = _text(
            actors[actor_id].get("credential_secret_ref")
            or actors[actor_id].get("secret_ref")
        )
        if (
            not secret_ref
            and _text(actors[actor_id].get("role")).lower()
            not in {"anonymous", "public"}
        ):
            # ── Enhanced: try fallback actor with valid secret ──
            _fallback = next(
                (
                    aid for aid, a in actors.items()
                    if isinstance(a, dict)
                    and _actor_is_executable(a)
                    and _text(a.get("credential_secret_ref") or a.get("secret_ref"))
                ),
                None,
            )
            if _fallback:
                required_actors = [_fallback if a == actor_id else a for a in required_actors]
                prop = {**prop, "actor_ref": _fallback, "_actor_degraded": True, "_original_actor": actor_id}
                actor_id = _fallback
                secret_ref = _text(
                    actors[actor_id].get("credential_secret_ref")
                    or actors[actor_id].get("secret_ref")
                )
            else:
                return blocked_experiment(
                    oid,
                    "BLOCKED_MISSING_ACTOR",
                    f"missing_secret_ref:{actor_id}",
                )
        if _is_unresolvable_actor_secret_ref(secret_ref):
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_ACTOR",
                f"unresolved_secret_ref:{actor_id}",
            )

    if "http_api" not in adapters:
        return blocked_experiment(oid, "BLOCKED_UNSUPPORTED_ADAPTER", "http_api")

    for gap in _list(ir.get("conflicts")):
        if isinstance(gap, dict) and _text(gap.get("status")) == "conflicting":
            # Only block if this obligation references the conflicting operation
            _conflict_op = _text(gap.get("operation_ref"))
            if _conflict_op and _conflict_op in _list(obl.get("subject_refs") or obl.get("required_operations") or []):
                return blocked_experiment(
                    oid,
                    "BLOCKED_CONFLICTING_SOURCE",
                    _text(gap.get("id")),
                )

    if not primary_op_id or primary_op_id not in ops:
        # Skip obligations whose primary operation is not available
        return make_experiment(
            obligation_id=oid,
            risk_family=family,
            compile_receipt={"status": "DEFERRED", "reason_code": "MISSING_PRIMARY_OPERATION", "detail": primary_op_id or "none"},
        )
    primary_op = ops[primary_op_id]
    is_write = _text(primary_op.get("read_write")) == "write"
    write_observers = (
        declared_effect_observers(primary_op, behavior_ir=ir)
        if is_write
        else []
    )
    # ── Filter write-only observers for read-only operations ──
    # entity_state, before_state, after_state, final_state, business_effect
    # require write steps with governance receipts. For read-only operations,
    # these observers would always return INDETERMINATE, blocking activation.
    _WRITE_ONLY_OBSERVERS = frozenset({
        "entity_state", "before_state", "after_state", "final_state", "business_effect",
    })
    if not is_write:
        required_observers = [
            obs for obs in required_observers
            if obs not in _WRITE_ONLY_OBSERVERS
        ]
    binding_plan = build_binding_plan(
        operation=primary_op,
        obligation=obl,
        actors=[actors[a] for a in required_actors if a in actors],
        behavior_ir=ir,
    )
    # ── Placeholder interception: block if any binding is unresolvable ──
    _blocked_reasons = blocked_binding_reasons(binding_plan)
    if _blocked_reasons:
        return blocked_experiment(
            oid,
            "BLOCKED_MISSING_BINDING",
            f"unresolvable_path_placeholders:{';'.join(_blocked_reasons[:4])}",
        )
    if (
        not is_write
        and family in {"authorization", "isolation", "visibility"}
        and (
            prop.get("require_same_resource") is True
            or (
                family == "isolation"
                and _text(prop.get("ownership_param"))
            )
        )
    ):
        control_fixture_binding = _source_declared_control_fixture_binding(
            operation=primary_op,
            operation_ref=primary_op_id,
            control_actor_ref=_text(
                prop.get("control_actor_ref")
                or prop.get("owner_actor_ref")
                or prop.get("actor_ref")
                or (required_actors[0] if required_actors else "")
            ),
            behavior_ir=ir,
        )
        if control_fixture_binding and not any(
            _text(row.get("target")) == _text(control_fixture_binding.get("target"))
            for row in binding_plan
            if isinstance(row, dict)
        ):
            binding_plan.append(control_fixture_binding)
    if family == "isolation" and _text(prop.get("ownership_param")):
        identity_target = _text(prop.get("identity_binding_target")) or "user_id"
        if identity_target and not any(
            _text(row.get("target")) == identity_target
            for row in binding_plan
            if isinstance(row, dict)
        ):
            identity_resolvers = [
                {
                    "operation_ref": _text(candidate.get("id")),
                    "method": "GET",
                    "path": normalize_path_placeholders(
                        _text(candidate.get("path") or candidate.get("raw_path"))
                    ),
                }
                for candidate in _list(ir.get("operations"))
                if isinstance(candidate, dict)
                and _text(candidate.get("method")).upper() in {"GET", "HEAD"}
                and normalize_path_placeholders(
                    _text(candidate.get("path") or candidate.get("raw_path"))
                ).rstrip("/").endswith("/me")
                and _text(candidate.get("id"))
            ]
            if identity_resolvers:
                binding_plan.append({
                    "target": identity_target,
                    "target_path": "/{" + identity_target + "}",
                    "status": "runtime_resolvable",
                    "source_priority": "owner_identity_read",
                    "resolver_operations": identity_resolvers[:2],
                    "value_fingerprint": "",
                })
            else:
                # No /me endpoint — use generated identity value as fallback.
                # The API will apply authorization rules based on this identity;
                # a 404 response is a valid test outcome, not a failure.
                import uuid as _uuid
                _fallback_id = str(_uuid.uuid4())
                binding_plan.append({
                    "target": identity_target,
                    "target_path": "/{" + identity_target + "}",
                    "status": "bound",
                    "source_priority": "generated_identity",
                    "value_fingerprint": _fallback_id,
                    "generated_value": _fallback_id,
                })
    if family == "state":
        state_token = _state_match_token(prop.get("from_state"))
        normalized_path = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        for binding in binding_plan:
            if not isinstance(binding, dict):
                continue
            target = _text(binding.get("target"))
            if (
                state_token
                and _text(binding.get("status")) == "runtime_resolvable"
                and (
                    "{" + target + "}" in normalized_path
                    or ":" + target in normalized_path
                )
            ):
                target_path = _text(binding.get("target_path")) or f"/{{{target}}}"
                binding["target_path"] = f"@state={state_token}@{target_path}"
                binding["selection_semantics"] = "source_state_precondition"
                binding["required_state"] = _text(prop.get("from_state"))
    unresolved = unresolved_placeholders(primary_op, binding_plan)
    prereq_plan = None
    if unresolved:
        # ── Try unified enterprise test data constructor ──
        # If placeholders cannot be resolved from existing data, build a
        # full prerequisite chain: detect FK dependencies recursively,
        # classify values, plan creation order. Store the plan for
        # execution at runtime by the fixture materializer.
        prereq_plan = None
        try:
            from .enterprise_test_data_constructor import plan_prerequisite_data
            prereq_plan = plan_prerequisite_data(primary_op, ir, max_depth=10)
            if prereq_plan and prereq_plan["total_prerequisites"] > 0:
                # Check if the plan covers our unresolved placeholders
                planned_entities = {
                    step.get("entity", "").lower().rstrip("s")
                    for step in prereq_plan["execution_plan"]
                }
                resolved_placeholders = [
                    p for p in unresolved
                    if p.lower().rstrip("s") in planned_entities
                    or any(p.lower() in e for e in planned_entities)
                ]
                if resolved_placeholders:
                    unresolved = [p for p in unresolved if p not in resolved_placeholders]
        except Exception as exc:
            logger.debug("prerequisite plan failed for obligation: %s", exc)
            prereq_plan = None

        if unresolved:
            # ── Placeholder interception: BLOCK instead of generating fake IDs ──
            # Unresolved path placeholders mean no real entity exists. Executing
            # with placeholder IDs (qb_test_*) wastes compute and produces 100%
            # failed requests. Block the experiment until real fixture data is
            # materialized by the governed Bootstrap flow.
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_BINDING",
                f"unresolved_placeholders_no_fixture:{';'.join(unresolved[:6])}",
            )

    # Store prerequisite plan for runtime execution — attach to binding_plan
    # since the experiment dict is built later by make_experiment().
    if prereq_plan and prereq_plan.get("total_prerequisites", 0) > 0:
        binding_plan.append({
            "target": "__prerequisite_plan__",
            "status": "bound",
            "source_priority": "enterprise_test_data_constructor",
            "prerequisite_plan": prereq_plan,
        })
    # ── Substitute generated values into operation path ──
    # When the binding plan contains best-effort generated values, substitute
    # them into the operation's path so that runtime preflight doesn't reject
    # the still-present {placeholder} tokens.
    for binding in binding_plan:
        if not isinstance(binding, dict):
            continue
        gen_val = binding.get("generated_value")
        if gen_val is not None:
            target = _text(binding.get("target"))
            if target:
                primary_op["path"] = _text(primary_op.get("path", "")).replace(
                    "{" + target + "}", str(gen_val)
                ).replace(
                    ":" + target, str(gen_val)
                )
    if is_write and not write_observers:
        # A synthesized observer observes nothing. Without a source-declared
        # effect read there is no way to tell whether the write took effect.
        return blocked_experiment(
            oid,
            "BLOCKED_MISSING_OBSERVER",
            "write_observer",
        )

    for fixture in required_fixtures:
        concrete = next(
            (
                item
                for item in binding_plan
                if isinstance(item, dict)
                and (
                    _text(item.get("fixture_id")) == fixture
                    or _text(item.get("name")) == fixture
                    or _text(item.get("target")) == f"fixture:{fixture}"
                )
                and _text(
                    item.get("create_path")
                    or item.get("create_operation_ref")
                ).startswith("/")
                and "{" not in _text(
                    item.get("create_path")
                    or item.get("create_operation_ref")
                )
            ),
            None,
        )
        if concrete is None:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_FIXTURE",
                fixture or "required_fixture",
            )

    if not required_observers:
        return blocked_experiment(oid, "BLOCKED_MISSING_OBSERVER", "none")

    raw_cleanup_req = obl.get("cleanup_requirement")
    if isinstance(raw_cleanup_req, str):
        # Handle string format: "required" or "not_required"
        cleanup_req = {"required": raw_cleanup_req.strip().lower() != "not_required"}
    else:
        cleanup_req = _dict(raw_cleanup_req)
    cleanup_plan: list[dict[str, Any]] = []
    cleanup_explicitly_not_required = False
    if is_write:
        primary_path_for_cleanup = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        # Fail closed: a write may waive cleanup only for ephemeral session
        # posts. Matrix/coverage strings like "not_required" on entity writes
        # previously compiled accepted creates with no compensator.
        if (
            cleanup_req.get("required") is False
            and not _is_ephemeral_session_path(primary_path_for_cleanup)
        ):
            cleanup_req = {**cleanup_req, "required": True}
        primary_method = _text(primary_op.get("method")).upper()
        primary_path = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        cleanup_op = _text(
            cleanup_req.get("operation_ref")
            or cleanup_req.get("compensation_operation_ref")
        )
        if not cleanup_op:
            # Bind unique source compensates relation when the obligation left
            # cleanup unresolved but Behavior IR already declared it.
            relation_compensators = {
                _text(relation.get("operation_ref") or relation.get("from_ref"))
                for relation in _list(ir.get("relations"))
                if isinstance(relation, dict)
                and _text(relation.get("relation_type")) == "compensates"
                and (
                    _text(relation.get("to_ref")) == primary_op_id
                    or any(
                        isinstance(effect, dict)
                        and _text(effect.get("cleanup_target_operation_ref"))
                        == primary_op_id
                        for effect in _list(relation.get("effects"))
                    )
                )
                and _text(relation.get("operation_ref") or relation.get("from_ref"))
                in ops
                and _text(relation.get("operation_ref") or relation.get("from_ref"))
                != primary_op_id
            }
            if len(relation_compensators) == 1:
                cleanup_op = next(iter(relation_compensators))
                cleanup_req = {
                    **cleanup_req,
                    "operation_ref": cleanup_op,
                    "required": True,
                    "mode": _text(cleanup_req.get("mode") or "reverse_order"),
                }
        delta_field, inverse_delta_body = _inverse_delta_cleanup_spec(primary_op)
        cleanup_op_method = _text(
            _dict(ops.get(cleanup_op)).get("method")
        ).upper() if cleanup_op else ""
        if not cleanup_op and primary_path.startswith("/") and delta_field:
            cleanup_plan = [{
                "action": "inverse_delta_compensation",
                "mode": "delta_inverse",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "delta_field": delta_field,
                "body": inverse_delta_body,
                "runtime_response_binding_required": "{" in primary_path,
            }]
        elif (
            primary_path.startswith("/")
            and primary_method in {"PUT", "PATCH"}
            and (
                not cleanup_op
                # create→DELETE compensators must not destroy pre-existing
                # resources that an in-place PUT/PATCH only mutated.
                or cleanup_op_method == "DELETE"
            )
        ):
            cleanup_plan = [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "runtime_response_binding_required": "{" in primary_path,
            }]
        elif (
            # State-transition POST: operates on an existing resource (has
            # path parameter) rather than creating a new collection item.
            # Use before/after snapshot restore as the cleanup strategy.
            primary_path.startswith("/")
            and primary_method == "POST"
            and "{" in primary_path
            and not cleanup_op
        ):
            cleanup_plan = [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "runtime_response_binding_required": True,
            }]
        elif (
            # Ephemeral session/token posts only: identity-bound paths (/{id}/…)
            # are persistent mutations even under /api/auth/… admin routes.
            # Match terminal path segments — never a bare "/auth/" substring.
            primary_method == "POST"
            and not cleanup_op
            and "{" not in primary_path
            and _is_ephemeral_session_path(primary_path)
        ):
            cleanup_plan = []
            cleanup_explicitly_not_required = True
        elif (
            # Collection POST create: only a source-declared identity-bound
            # DELETE on the same collection may compensate. Invented
            # ``primary/{created_resource_id}`` DELETE paths are prohibited.
            primary_method == "POST"
            and primary_path.startswith("/")
            and "{" not in primary_path
            and not cleanup_op
        ):
            delete_candidates = _identity_bound_delete_refs(
                primary_op_id=primary_op_id,
                primary_path=primary_path,
                ops=ops,
            )
            if len(delete_candidates) == 1:
                cleanup_op = delete_candidates[0]
                cleanup_req = {
                    **cleanup_req,
                    "operation_ref": cleanup_op,
                    "required": True,
                    "mode": "reverse_order",
                }
            # else: leave unresolved → BLOCKED_NON_REVERSIBLE_WRITE below
        elif (
            # DELETE recreation requires an explicit compensates relation; do
            # not invent snapshot_restore / POST recreate from route shape.
            primary_method == "DELETE"
            and primary_path.startswith("/")
            and not cleanup_op
        ):
            pass
        elif (
            # In-place mutation fallback only. Collection creates without a
            # source DELETE must not compile a fake restore plan.
            primary_path.startswith("/")
            and primary_method in {"PUT", "PATCH"}
            and not cleanup_op
        ):
            cleanup_plan = [{
                "action": "restore_before_snapshot",
                "mode": "snapshot_restore",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "runtime_response_binding_required": "{" in primary_path,
                "_universal_fallback": True,
            }]
        if not cleanup_plan and not cleanup_op:
            action_compensators = declared_action_compensators(
                primary_op,
                behavior_ir=ir,
            )
            if len(action_compensators) == 1:
                compensator = action_compensators[0]
                cleanup_plan = [{
                    "action": "source_declared_compensation",
                    "mode": "reverse_order",
                    "operation_ref": _text(compensator.get("operation_ref")),
                    "compensates_operation_ref": primary_op_id,
                    "path": _text(compensator.get("path")),
                    "method": _text(compensator.get("method")).upper(),
                    "body_from_original_request": True,
                    "runtime_response_binding_required": (
                        "{" in _text(compensator.get("path"))
                    ),
                }]
        if not cleanup_plan:
            cleanup_path = normalize_path_placeholders(
                _text(
                    _dict(ops.get(cleanup_op)).get("path")
                    or _dict(ops.get(cleanup_op)).get("raw_path")
                )
            )
            cleanup_method = _text(
                _dict(ops.get(cleanup_op)).get("method")
            ).upper()
            if (
                not cleanup_op
                or cleanup_op == primary_op_id
                or cleanup_op not in ops
                or cleanup_method not in {"POST", "PUT", "PATCH", "DELETE"}
                or not cleanup_path.startswith("/")
            ):
                if cleanup_req.get("required") is False:
                    # Cleanup explicitly not required; proceed without it.
                    cleanup_plan = []
                    cleanup_explicitly_not_required = True
                else:
                    # Authorization/isolation probes that expect rejection still
                    # need a source-declared compensator when cleanup is
                    # required: an unexpected accepted write must be reversible.
                    return blocked_experiment(
                        oid,
                        "BLOCKED_NON_REVERSIBLE_WRITE",
                        f"cleanup_unresolved:{cleanup_op}",
                    )
            # Only build cleanup plan if cleanup_op is valid
            if cleanup_op and cleanup_op in ops and cleanup_op != primary_op_id:
                cleanup_mode = _text(cleanup_req.get("mode") or "reverse_order")
                primary_body = _source_request_example(primary_op)
                cleanup_body = _source_request_example(_dict(ops.get(cleanup_op)))
                if (
                    cleanup_mode == "recreate_compensated_resource"
                    and cleanup_method in {"POST", "PUT", "PATCH"}
                    and primary_body
                ):
                    # Compensator primary (release/cancel) → recreate via the unique
                    # compensated write. Reuse each accepted primary request body
                    # (already runtime-bound) rather than a static example that still
                    # contains `<order_id>`-style tokens.
                    cleanup_plan = [{
                        "action": "source_declared_compensation",
                        "mode": cleanup_mode,
                        "operation_ref": cleanup_op,
                        "compensates_operation_ref": primary_op_id,
                        "path": cleanup_path,
                        "method": cleanup_method,
                        "body_from_original_request": True,
                        "runtime_response_binding_required": "{" in cleanup_path,
                    }]
                elif cleanup_mode == "recreate_compensated_resource":
                    # DELETE/empty-body primary: recreate from the create operation's
                    # source example; executor must materialize runtime tokens.
                    cleanup_plan = [{
                        "action": "reverse_order_compensation",
                        "mode": cleanup_mode,
                        "operation_ref": cleanup_op,
                        "path": cleanup_path,
                        "method": cleanup_method,
                        "body": cleanup_body or None,
                        "runtime_response_binding_required": "{" in cleanup_path,
                    }]
                elif cleanup_method == "DELETE":
                    # Identity-bound create→DELETE: no cleanup body. Emit reverse-order
                    # DELETE so the executor binds each accepted create id and does not
                    # route through the body-oriented source_declared_compensation arm.
                    cleanup_plan = [{
                        "action": "reverse_order_compensation",
                        "mode": cleanup_mode,
                        "operation_ref": cleanup_op,
                        "compensates_operation_ref": primary_op_id,
                        "path": cleanup_path,
                        "method": cleanup_method,
                        "runtime_response_binding_required": "{" in cleanup_path,
                    }]
                else:
                    # Relation-bound POST/PUT/PATCH compensators (reserve→release)
                    # must reuse the original write body. Empty cleanup bodies
                    # previously produced target-side NaN/500.
                    cleanup_plan = [{
                        "action": "source_declared_compensation",
                        "mode": cleanup_mode,
                        "operation_ref": cleanup_op,
                        "compensates_operation_ref": primary_op_id,
                        "path": cleanup_path,
                        "method": cleanup_method,
                        "body_from_original_request": True,
                        "runtime_response_binding_required": "{" in cleanup_path,
                    }]

    control_actor = _text(
        prop.get("control_actor_ref")
        or prop.get("owner_actor_ref")
        or prop.get("actor_ref")
        or (required_actors[0] if required_actors else "")
    )
    treatment_actor = _text(
        prop.get("treatment_actor_ref")
        or prop.get("viewer_actor_ref")
        or prop.get("actor_ref")
        or (
            required_actors[1]
            if len(required_actors) > 1
            else control_actor
        )
    )
    # ── Enhanced: For authorization/isolation/visibility tests, ensure proper actor roles ──
    # Control = authorized actor (higher privilege), Treatment = unauthorized (lower privilege)
    _high_privilege_roles = {"admin", "administrator", "superadmin", "root", "system"}
    _lower_privilege_roles = {"buyer", "customer", "user", "viewer", "guest", "anonymous", "public"}
    if family in {"authorization", "isolation", "visibility"}:
        _control_role = _text(actors.get(control_actor, {}).get("role")).lower()
        _treatment_role = _text(actors.get(treatment_actor, {}).get("role")).lower()
        # If treatment has higher privilege than control, swap them
        if _treatment_role in _high_privilege_roles and _control_role not in _high_privilege_roles:
            control_actor, treatment_actor = treatment_actor, control_actor
            _control_role, _treatment_role = _treatment_role, _control_role
        # If treatment equals control, or treatment has high privilege, find a lower-privilege actor
        if treatment_actor == control_actor or _treatment_role in _high_privilege_roles:
            _alt_actor = next(
                (
                    aid for aid, a in actors.items()
                    if isinstance(a, dict)
                    and _actor_is_executable(a)
                    and aid != control_actor
                    and _text(a.get("role")).lower() in _lower_privilege_roles
                ),
                None,
            ) or next(
                (
                    aid for aid, a in actors.items()
                    if isinstance(a, dict)
                    and _actor_is_executable(a)
                    and aid != control_actor
                    and _text(a.get("role")).lower() not in _high_privilege_roles
                ),
                None,
            ) or next(
                (
                    aid for aid, a in actors.items()
                    if isinstance(a, dict)
                    and _actor_is_executable(a)
                    and aid != control_actor
                ),
                None,
            )
            if _alt_actor:
                treatment_actor = _alt_actor
    observer_requirements = list(required_observers)
    permit_only = _text(prop.get("template")) == "permitted_operation_invocation"
    if (
        is_write
        and family in {"authorization", "isolation", "visibility"}
        and not permit_only
    ):
        observer_requirements.append("business_effect")
    observers, observer_reason, observer_detail = compile_observer_requirements(
        observer_requirements,
        risk_family=family,
        available_adapters=adapters,
        # Permit-only protocols have empty control_plan; auth comparison cannot run.
        require_authorization_comparison=not permit_only,
    )
    if observer_reason:
        return blocked_experiment(oid, observer_reason, observer_detail)

    if any(
        _text(observer.get("observer_id")) == "resource_ownership"
        for observer in observers
    ):
        ownership_proofs = [
            row
            for row in binding_plan
            if isinstance(row, dict)
            and _text(row.get("status")) == "fixture_proof"
            and _text(row.get("owner_actor_ref"))
            and _text(row.get("binding_target"))
        ]
        if not ownership_proofs:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_FIXTURE",
                "ownership_proof",
            )
        for observer in observers:
            if _text(observer.get("observer_id")) == "resource_ownership":
                observer["fixture_proof_refs"] = [
                    _text(row.get("fixture_id"))
                    for row in ownership_proofs
                    if _text(row.get("fixture_id"))
                ]

    _EFFECT_OBSERVER_IDS = {
        "after_state",
        "before_state",
        "business_effect",
        "entity_state",
        "final_state",
    }

    protocol = compile_family_protocol(
        risk_family=family,
        operation=primary_op,
        operation_ref=primary_op_id,
        control_actor_ref=control_actor,
        treatment_actor_ref=treatment_actor,
        property_spec=prop,
        behavior_ir=ir,
    )
    # Merge protocol-level observers (e.g. before_state, after_state) before
    # attaching resolvers — otherwise they enter the experiment with no way to
    # read before/after state and finish INDETERMINATE.
    for pobs in _list(protocol.get("observers")):
        if isinstance(pobs, dict) and _text(pobs.get("observer_id")) not in {
            _text(o.get("observer_id")) for o in observers if isinstance(o, dict)
        }:
            observers.append(dict(pobs))
    if _text(protocol.get("status")) != "COMPILED":
        return blocked_experiment(
            oid,
            _text(protocol.get("reason_code"))
            or "BLOCKED_UNSUPPORTED_ADAPTER",
            _text(protocol.get("detail")),
        )

    effect_observer_ids = {
        _text(observer.get("observer_id"))
        for observer in observers
        if _text(observer.get("observer_id")) in _EFFECT_OBSERVER_IDS
    }
    if effect_observer_ids:
        if not write_observers:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_OBSERVER",
                ",".join(sorted(effect_observer_ids)),
            )
        for observer in observers:
            if _text(observer.get("observer_id")) in effect_observer_ids:
                observer["resolver_operations"] = write_observers

    if family == "state":
        treatment_rows = [
            row
            for row in _list(protocol.get("treatment_plan"))
            if isinstance(row, dict)
        ]
        if len(treatment_rows) != 1:
            return blocked_experiment(
                oid,
                "BLOCKED_UNSUPPORTED_ADAPTER",
                "state_transition_protocol_shape_invalid",
            )
        treatment_rows[0] = {
            **treatment_rows[0],
            "intent": "state_transition",
            "protocol_step": "state_transition_write",
            "from_state_ref": _text(prop.get("from_state_ref")),
            "to_state_ref": _text(prop.get("to_state_ref")),
        }
        # Postcondition causal-chain assertions use a dedicated evaluation path
        # that checks entity_state observer evidence (state_change_count, effect_count)
        # rather than requiring explicit from_state/to_state values.
        expr_kind = _text(_dict(prop.get("expression")).get("kind"))
        if expr_kind == "postcondition":
            # ── P0-5: detect field_delta operands for causal verification ──
            _pc_expr = _dict(prop.get("expression"))
            _pc_ops = _list(_pc_expr.get("operands"))
            _has_delta = any(
                isinstance(op, dict)
                and (op.get("expected_delta") is not None or _text(op.get("expected_delta_direction")))
                for op in _pc_ops
            )
            _a_kind = "field_delta" if _has_delta else "postcondition"
            protocol = {
                **protocol,
                "control_plan": [],
                "treatment_plan": treatment_rows,
                "assertion": {
                    "kind": _a_kind,
                    "operator": _text(_pc_expr.get("operator")),
                    "operands": _pc_ops,
                    "fields": _pc_ops if _has_delta else [],
                },
            }
        else:
            protocol = {
                **protocol,
                "control_plan": [],
                "treatment_plan": treatment_rows,
                "assertion": {
                    "kind": "state_transition",
                    "from_state": _text(prop.get("from_state")),
                    "to_state": _text(prop.get("to_state")),
                    "from_state_ref": _text(prop.get("from_state_ref")),
                    "to_state_ref": _text(prop.get("to_state_ref")),
                },
            }

    control_plan = [
        row
        for row in _list(protocol.get("control_plan"))
        if isinstance(row, dict)
    ]
    treatment_plan = [
        row
        for row in _list(protocol.get("treatment_plan"))
        if isinstance(row, dict)
    ]
    # ── Enhanced: propagate resolved path to steps for runtime preflight ──
    _resolved_path = _text(primary_op.get("path"))
    if _resolved_path and "{" not in _resolved_path:
        for _step in control_plan + treatment_plan:
            if isinstance(_step, dict) and _text(_step.get("operation_ref")) == primary_op_id:
                _step["path"] = _resolved_path
    needs_control = bool(control_plan)

    protocol_assertion = _dict(protocol.get("assertion"))
    assertion_kind = (
        _text(protocol_assertion.get("kind"))
        or _FAMILY_ASSERTION_KIND.get(family)
        or "http_status"
    )
    assertions = [{
        "assertion_id": f"assert_{family or 'generic'}",
        "kind": assertion_kind,
        "template": _text(prop.get("template")),
        "expected_from": "source_property",
        "property": prop,
        "require_control": needs_control,
        **{
            key: value
            for key, value in protocol_assertion.items()
            if key != "kind"
        },
    }]
    # ── Enhanced: add secondary assertions for deeper bug detection ──
    # For authorization/isolation/visibility: add response body leakage check
    if family in ("authorization", "isolation", "visibility") and not permit_only:
        assertions.append({
            "assertion_id": f"assert_{family}_body_leakage",
            "kind": "http_status_class",
            "expected_class": 4,
            "compare_field": "status_code",
            "template": _text(prop.get("template")),
            "expected_from": "source_property",
            "property": prop,
            "require_control": needs_control,
            "require_nonzero_effect": True,
            "_secondary_assertion": True,
        })
    # For validation: add strict rejection assertion
    if family == "validation":
        assertions.append({
            "assertion_id": "assert_validation_strict_reject",
            "kind": "http_status_class",
            "expected_class": 4,
            "compare_field": "status_code",
            "template": _text(prop.get("template")),
            "expected_from": "source_property",
            "property": prop,
            "require_control": needs_control,
            "_secondary_assertion": True,
        })
    # For idempotency: add effect-count assertion
    if family == "idempotency":
        assertions.append({
            "assertion_id": "assert_idempotency_effect",
            "kind": "idempotency_effect",
            "expected_effect_count": 1,
            "template": _text(prop.get("template")),
            "expected_from": "source_property",
            "property": prop,
            "require_control": needs_control,
            "_secondary_assertion": True,
        })
    return make_experiment(
        obligation_id=oid,
        policy_version=policy_version,
        risk_family=family,
        control_plan=control_plan,
        treatment_plan=treatment_plan,
        binding_plan=binding_plan,
        setup_plan=[{
            "action": "resolve_bindings",
            "bindings": [b.get("target") for b in binding_plan],
        }],
        assertions=assertions,
        observers=observers,
        cleanup_plan=cleanup_plan,
        safety_contract={
            "environment_type": env,
            "non_production_required": True,
            "governed_write": is_write,
            "cleanup_not_required": cleanup_explicitly_not_required,
        },
        source_refs=list(obl.get("source_refs") or [])[:5] or [
            {"id": oid, "type": "obligation", "locator": primary_op_id or ""}
        ],
        source_identity_fields=source_identity_fields_for_operation(primary_op, ir),
        compile_receipt={
            "status": "COMPILED",
            "reason_code": "",
            "unresolved_placeholders": 0,
            "control_present": bool(control_plan),
            "treatment_present": bool(treatment_plan),
            "cleanup_present": bool(cleanup_plan) or not is_write,
        },
    )

# ── Experiment Contract (merged from experiment_contract.py) ──────────

SCHEMA_VERSION = "qualibug.experiment.v1"

BLOCK_REASONS = (
    "BLOCKED_MISSING_OPERATION",
    "BLOCKED_MISSING_ACTOR",
    "BLOCKED_MISSING_FIXTURE",
    "BLOCKED_MISSING_BINDING",
    "BLOCKED_MISSING_OBSERVER",
    "BLOCKED_NON_REVERSIBLE_WRITE",
    "BLOCKED_CONFLICTING_SOURCE",
    "BLOCKED_UNSUPPORTED_ADAPTER",
)


def stable_experiment_id(obligation_id: str, *parts: Any) -> str:
    raw = "|".join([_text(obligation_id), *(_text(p) for p in parts if _text(p))])
    return f"exp_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def make_experiment(
    *,
    obligation_id: str,
    policy_version: str = "",
    risk_family: str = "",
    control_plan: list[dict[str, Any]] | None = None,
    treatment_plan: list[dict[str, Any]] | None = None,
    binding_plan: list[dict[str, Any]] | None = None,
    setup_plan: list[dict[str, Any]] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    observers: list[dict[str, Any]] | None = None,
    async_observation_policy: dict[str, Any] | None = None,
    cleanup_plan: list[dict[str, Any]] | None = None,
    safety_contract: dict[str, Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    compile_receipt: dict[str, Any] | None = None,
    experiment_id: str | None = None,
    source_identity_fields: list[str] | None = None,
) -> dict[str, Any]:
    eid = _text(experiment_id) or stable_experiment_id(obligation_id, "v1")
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": eid,
        "obligation_id": _text(obligation_id),
        "policy_version": _text(policy_version),
        "risk_family": _text(risk_family),
        "source_identity_fields": list(source_identity_fields or []),
        "control_plan": list(control_plan or []),
        "treatment_plan": list(treatment_plan or []),
        "binding_plan": list(binding_plan or []),
        "setup_plan": list(setup_plan or []),
        "assertions": list(assertions or []),
        "observers": list(observers or []),
        "async_observation_policy": dict(async_observation_policy or {"mode": "bounded_backoff"}),
        "cleanup_plan": list(cleanup_plan or []),
        "safety_contract": dict(safety_contract or {"environment": "non_production_required"}),
        "source_refs": list(source_refs or []),
        "compile_receipt": dict(compile_receipt or {"status": "COMPILED"}),
    }


def blocked_experiment(obligation_id: str, reason_code: str, detail: str = "") -> dict[str, Any]:
    code = reason_code if reason_code in BLOCK_REASONS else "BLOCKED_UNSUPPORTED_ADAPTER"
    return make_experiment(
        obligation_id=obligation_id,
        compile_receipt={
            "status": "BLOCKED",
            "reason_code": code,
            "detail": _text(detail),
        },
        experiment_id=stable_experiment_id(obligation_id, code),
    )
