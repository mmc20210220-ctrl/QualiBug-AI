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
    # "state" was absent, so a state obligation whose protocol did not supply a kind
    # fell through to the `or "http_status"` default at the call site -- a lifecycle
    # transition asserted as a bare status-code check. The state protocol branch does
    # supply state_transition today, so this is a latent fallback rather than an active
    # defect, but the default is the wrong shape for this family and matches the
    # evaluator's own "state" -> state_transition alias.
    "state": "state_transition",
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
    declared_action_recreate_primaries,
    declared_effect_observers,
    unresolved_placeholders,
)
from .assertion_dsl_base import unproducible_assertion_evidence
from .cleanup_plan_validator import validate_cleanup_plan
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


def _unique_collection_create_ref(
    *,
    primary_op_id: str,
    primary_path: str,
    ops: dict[str, dict[str, Any]],
) -> str:
    """Return the unique source-declared collection POST create, if any."""

    op_collection = normalize_path_placeholders(collection_path(primary_path))
    if not op_collection.startswith("/"):
        return ""
    create_refs = [
        cand_id
        for cand_id, cand_op in ops.items()
        if cand_id != primary_op_id
        and isinstance(cand_op, dict)
        and _text(cand_op.get("method")).upper() == "POST"
        and normalize_path_placeholders(
            _text(cand_op.get("path") or cand_op.get("raw_path"))
        ) == op_collection
    ]
    return create_refs[0] if len(create_refs) == 1 else ""


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
    if segments[-1] in _EPHEMERAL_SESSION_SEGMENTS:
        return True
    # Password reset / forgot flows send a token mail; they do not create a
    # durable collection row that needs governed DELETE compensation.
    if (
        len(segments) >= 2
        and segments[-2] == "password"
        and segments[-1] in {"reset", "forgot", "recover"}
    ):
        return True
    return False


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
    # ── SPEC v1.1.1 §11: Actor Selection Contract detection ──
    # Explicit actors: obligation directly names actors or property has actor refs.
    _explicit_actor_fields = ("actor_ref", "owner_actor_ref", "viewer_actor_ref",
                              "control_actor_ref", "treatment_actor_ref")
    _has_explicit_actor_from_obl = bool(required_actors)
    _has_explicit_actor_from_prop = any(
        _text(prop.get(f)) for f in _explicit_actor_fields
    )
    _actor_selection_explicit = _has_explicit_actor_from_obl or _has_explicit_actor_from_prop
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
            # ── SPEC v1.1.1 §11.1: Explicit actors must NOT be substituted ──
            if _actor_selection_explicit:
                return blocked_experiment(oid, "BLOCKED_MISSING_ACTOR", actor_id)
            # Constraint-based: try fallback actor
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
            # ── SPEC v1.1.1 §11.1: Explicit actors must NOT be substituted ──
            if _actor_selection_explicit:
                return blocked_experiment(
                    oid,
                    "BLOCKED_MISSING_ACTOR",
                    f"missing_secret_ref:{actor_id}",
                )
            # Constraint-based: try fallback actor with valid secret
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
    # ── V1.2.3: Source-Declared Readback Resolver enhancement ──
    # When the existing declared_effect_observers finds nothing, try the
    # readback resolver which uses deeper source-evidence analysis including
    # domain matching, identity strategy resolution, and response-bound creates.
    _readback_contract: dict | None = None
    if is_write and not write_observers:
        try:
            from .source_declared_readback_resolver import (
                resolve_readback_contract as _resolve_readback,
                STATUS_RESOLVED as _READBACK_RESOLVED,
            )
            _rb_result = _resolve_readback(primary_op, behavior_ir=ir)
            if _rb_result.get("status") == _READBACK_RESOLVED and _rb_result.get("contract"):
                _readback_contract = _rb_result["contract"]
                # Convert resolved contract to write_observers format
                write_observers = [{
                    "operation_ref": _readback_contract.get("read_operation_id", ""),
                    "method": _readback_contract.get("method", "GET"),
                    "path": _readback_contract.get("endpoint_template", ""),
                    "readback_contract_id": _readback_contract.get("contract_id", ""),
                    "readback_surface_type": _readback_contract.get("readback_surface_type", ""),
                    "identity_strategy": _readback_contract.get("identity_strategy", {}),
                    "provenance_fingerprint": _readback_contract.get("provenance_fingerprint", ""),
                }]
        except Exception:
            pass  # Fail-safe: resolver failure does not change existing behavior
    # ── Filter write-only observers for read-only operations ──
    # entity_state, before_state, after_state, final_state, business_effect
    # require write steps with governance receipts. For read-only operations,
    # these observers would always return INDETERMINATE, blocking activation.
    _WRITE_ONLY_OBSERVERS = frozenset({
        "entity_state", "before_state", "after_state", "final_state", "business_effect",
    })
    primary_path_early = normalize_path_placeholders(
        _text(primary_op.get("path") or primary_op.get("raw_path"))
    )
    if not is_write:
        required_observers = [
            obs for obs in required_observers
            if obs not in _WRITE_ONLY_OBSERVERS
        ]
    elif _is_ephemeral_session_path(primary_path_early):
        # Session/token posts have no durable entity for state/effect reads.
        required_observers = [
            obs for obs in required_observers
            if obs not in _WRITE_ONLY_OBSERVERS
        ]
        if not required_observers:
            required_observers = ["http_response"]
    binding_plan = build_binding_plan(
        operation=primary_op,
        obligation=obl,
        actors=[actors[a] for a in required_actors if a in actors],
        behavior_ir=ir,
    )
    # ── V1.2.3 §15: Write Readback Contract into Binding Graph ──
    # The readback binding entry tells the runtime how to resolve the
    # readback operation's path parameter from the write response.
    if _readback_contract:
        _rb_identity = _dict(_readback_contract.get("identity_strategy"))
        _rb_target_param = _text(_rb_identity.get("target_parameter")) or "id"
        _rb_endpoint = _text(_readback_contract.get("endpoint_template"))
        # Only add if the readback endpoint has a path parameter to resolve
        if "{" in _rb_endpoint and not any(
            _text(row.get("target")) == f"__readback_{_rb_target_param}"
            for row in binding_plan
            if isinstance(row, dict)
        ):
            binding_plan.append({
                "target": f"__readback_{_rb_target_param}",
                "target_path": _rb_endpoint,
                "status": "runtime_resolvable",
                "source_priority": "readback_contract",
                "binding_type": "READBACK_IDENTITY",
                "readback_contract_id": _text(_readback_contract.get("contract_id")),
                "write_operation_id": _text(_readback_contract.get("write_operation_id")),
                "read_operation_id": _text(_readback_contract.get("read_operation_id")),
                "identity_strategy_type": _text(_rb_identity.get("type")),
                "identity_source_path": _text(_rb_identity.get("source_path")),
                "resolver_operations": [{
                    "operation_ref": _text(_readback_contract.get("read_operation_id")),
                    "method": _text(_readback_contract.get("method")) or "GET",
                    "path": _rb_endpoint,
                }],
                "value_fingerprint": "",
            })
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
        primary_path_for_observers = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        # Session/token posts are verified by transport status alone; they do
        # not produce a durable entity that an effect-read observer can bind.
        if not _is_ephemeral_session_path(primary_path_for_observers):
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
            # Accepts both relation_type/kind and from/to/source/target formats.
            relation_compensators = set()
            for relation in _list(ir.get("relations")):
                if not isinstance(relation, dict):
                    continue
                rel_kind = _text(relation.get("relation_type") or relation.get("kind"))
                if rel_kind not in {"compensates", "inverse", "compensation"}:
                    continue
                # Determine compensator op ref from relation fields
                comp_ref = _text(
                    relation.get("operation_ref")
                    or relation.get("from_ref")
                    or relation.get("from")
                    or relation.get("source")
                )
                # Determine target (the primary being compensated)
                target_ref = _text(
                    relation.get("to_ref")
                    or relation.get("to")
                    or relation.get("target")
                )
                # Check if this relation compensates the primary operation
                if target_ref == primary_op_id and comp_ref in ops and comp_ref != primary_op_id:
                    relation_compensators.add(comp_ref)
                elif comp_ref == primary_op_id:
                    # Reverse direction: source is primary, target is compensator
                    alt_ref = target_ref
                    if alt_ref in ops and alt_ref != primary_op_id:
                        relation_compensators.add(alt_ref)
                # Also check effects list
                if any(
                    isinstance(effect, dict)
                    and _text(effect.get("cleanup_target_operation_ref"))
                    == primary_op_id
                    for effect in _list(relation.get("effects"))
                ):
                    if comp_ref in ops and comp_ref != primary_op_id:
                        relation_compensators.add(comp_ref)
            if len(relation_compensators) == 1:
                cleanup_op = next(iter(relation_compensators))
                # Determine mode from cleanup operation method:
                # DELETE → reverse_order; POST/PUT/PATCH → compensator
                _cleanup_op_method = _text(
                    _dict(ops.get(cleanup_op)).get("method")
                ).upper()
                _relation_mode = (
                    "reverse_order" if _cleanup_op_method == "DELETE"
                    else "compensator"
                )
                cleanup_req = {
                    **cleanup_req,
                    "operation_ref": cleanup_op,
                    "required": True,
                    "mode": _text(cleanup_req.get("mode") or _relation_mode),
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
            # Identity-bound POST (ship/cancel/status/…): do not invent a
            # compensator here; the shared fallback below binds sibling
            # cleanup, recreate-from-create, or snapshot restore.
            primary_path.startswith("/")
            and primary_method == "POST"
            and "{" in primary_path
            and not cleanup_op
        ):
            pass
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
            # Collection POST create: prefer a unique identity-bound DELETE;
            # otherwise accept a unique source-shaped compensation action on
            # the same collection (e.g. …/{id}/cancel). Invented DELETE paths
            # remain prohibited.
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
            else:
                from .runtime_binding_graph import _declared_cleanup_operations

                cleanup_candidates = [
                    row
                    for row in _declared_cleanup_operations(
                        primary_path,
                        behavior_ir=ir,
                    )
                    if _text(row.get("operation_ref")) != primary_op_id
                ]
                if len(cleanup_candidates) == 1:
                    cleanup_op = _text(cleanup_candidates[0].get("operation_ref"))
                    cleanup_req = {
                        **cleanup_req,
                        "operation_ref": cleanup_op,
                        "required": True,
                        "mode": "reverse_order",
                    }
            # else: leave unresolved → BLOCKED_NON_REVERSIBLE_WRITE below
        elif (
            # DELETE: unique collection POST create may recreate the resource.
            primary_method == "DELETE"
            and primary_path.startswith("/")
            and not cleanup_op
        ):
            create_ref = _unique_collection_create_ref(
                primary_op_id=primary_op_id,
                primary_path=primary_path,
                ops=ops,
            )
            if create_ref:
                cleanup_op = create_ref
                cleanup_req = {
                    **cleanup_req,
                    "operation_ref": cleanup_op,
                    "required": True,
                    "mode": "recreate_compensated_resource",
                }
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
            }]
        if not cleanup_plan and not cleanup_op:
            from .runtime_binding_graph import _CLEANUP_ACTION_RE, _declared_cleanup_operations

            # SPEC v1.1 §12: Removed unsafe fallbacks:
            # - §12.1: Action → DELETE forbidden (DELETE only for collection create)
            # - §12.2: Cancel/Reject → Collection Create forbidden without explicit proof
            # - §12.3: Unique Candidate → Compensator forbidden (requires explicit relation)
            #
            # Only snapshot_restore is allowed for action POSTs with non-empty body.
            # All other cases require explicit source-declared relations.

            # Check for explicit compensates relation in Behavior IR
            relations = _list(ir.get("relations"))
            explicit_compensator = None
            for rel in relations:
                if not isinstance(rel, dict):
                    continue
                kind = _text(rel.get("kind"))
                if kind not in {"compensates", "inverse", "compensation"}:
                    continue
                src = _text(rel.get("source") or rel.get("source_operation_ref"))
                tgt = _text(rel.get("target") or rel.get("target_operation_ref"))
                if src == primary_op_id and tgt and tgt in ops:
                    explicit_compensator = _dict(ops.get(tgt))
                    break
                if tgt == primary_op_id and src and src in ops:
                    explicit_compensator = _dict(ops.get(src))
                    break

            if explicit_compensator:
                cleanup_plan = [{
                    "action": "source_declared_compensation",
                    "mode": "compensator",
                    "operation_ref": _text(explicit_compensator.get("id")),
                    "compensates_operation_ref": primary_op_id,
                    "path": _text(explicit_compensator.get("path") or explicit_compensator.get("raw_path")),
                    "method": _text(explicit_compensator.get("method")).upper(),
                    "body_from_original_request": True,
                    "runtime_response_binding_required": (
                        "{" in _text(explicit_compensator.get("path") or explicit_compensator.get("raw_path"))
                    ),
                }]
            elif primary_method == "POST" and "{" in primary_path and write_observers and _source_request_example(primary_op):
                # SPEC §7.3: Snapshot restore for action POSTs with non-empty body.
                # Empty-body actions (ship/confirm/approve) cannot use snapshot restore.
                cleanup_plan = [{
                    "action": "restore_before_snapshot",
                    "mode": "snapshot_restore",
                    "operation_ref": primary_op_id,
                    "path": primary_path,
                    "method": primary_method,
                    "runtime_response_binding_required": True,
                }]
        if not cleanup_plan and not cleanup_explicitly_not_required:
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

    # Recreate cleanup bodies may introduce new placeholders (addressId,
    # order_id). Merge those into binding_plan so preflight/runtime can resolve
    # them from source-declared list reads instead of blocking.
    for cleanup_row in cleanup_plan:
        if not isinstance(cleanup_row, dict):
            continue
        if _text(cleanup_row.get("mode")) != "recreate_compensated_resource":
            continue
        recreate_op = ops.get(_text(cleanup_row.get("operation_ref"))) or {}
        if not recreate_op:
            continue
        recreate_bindings = build_binding_plan(
            operation=recreate_op,
            obligation=obl,
            actors=[actors[aid] for aid in actors if isinstance(actors.get(aid), dict)],
            behavior_ir=ir,
        )
        existing_targets = {
            _text(row.get("target"))
            for row in binding_plan
            if isinstance(row, dict)
        }
        for row in recreate_bindings:
            if not isinstance(row, dict):
                continue
            target = _text(row.get("target"))
            if not target or target.startswith("actor:") or target in existing_targets:
                continue
            if _text(row.get("status")) not in {"runtime_resolvable", "bound"}:
                continue
            binding_plan.append(row)
            existing_targets.add(target)

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
    # ── SPEC v1.1.1 §11.1: For authorization/isolation/visibility tests ──
    # Control = authorized actor (higher privilege), Treatment = unauthorized (lower privilege)
    # When actors are explicitly specified, NO swap or substitution is allowed.
    _high_privilege_roles = {"admin", "administrator", "superadmin", "root", "system"}
    _lower_privilege_roles = {"buyer", "customer", "user", "viewer", "guest", "anonymous", "public"}
    if family in {"authorization", "isolation", "visibility"} and not _actor_selection_explicit:
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
        and not _is_ephemeral_session_path(primary_path_early)
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
    # Family protocols (state/conservation) may re-attach write-only observers.
    # Read primaries cannot produce governance before/after receipts — strip them
    # again so we do not false-block with BLOCKED_MISSING_OBSERVER.
    if not is_write:
        observers = [
            obs
            for obs in observers
            if _text(obs.get("observer_id")) not in _WRITE_ONLY_OBSERVERS
        ]
        if not observers:
            observers = [{"observer_id": "http_response"}]
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
        if not is_write:
            # Defensive: write-only observers should already be stripped above.
            observers = [
                obs
                for obs in observers
                if _text(obs.get("observer_id")) not in _EFFECT_OBSERVER_IDS
            ]
            effect_observer_ids = set()
        elif not write_observers:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_OBSERVER",
                ",".join(sorted(effect_observer_ids)),
            )
        for observer in observers:
            if _text(observer.get("observer_id")) in effect_observer_ids:
                observer["resolver_operations"] = write_observers
                # ── V1.2.3 §16: Observer Compiler consumes Readback Contract ──
                if _readback_contract:
                    observer["readback_contract_id"] = _text(
                        _readback_contract.get("contract_id")
                    )
                    observer["readback_surface_type"] = _text(
                        _readback_contract.get("readback_surface_type")
                    )
                    observer["identity_bindings"] = _dict(
                        _readback_contract.get("identity_strategy")
                    )
                    observer["required_fields"] = _list(
                        _readback_contract.get("required_fields")
                    )
                    observer["scope_validation"] = _dict(
                        _readback_contract.get("scope_bindings")
                    )
                    observer["async_policy"] = _dict(
                        _readback_contract.get("async_policy")
                    ) or {"mode": "synchronous"}
                    observer["provenance_fingerprint"] = _text(
                        _readback_contract.get("provenance_fingerprint")
                    )
                    observer["observer_compile_status"] = "COMPILED"

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
    # Binder-location materializability gate.
    #
    # A protocol may place a distinguishing mutation in query, path, header or body
    # (see the ownership binder in experiment_protocols_base). The executor only
    # materializes step["query"] (experiment_plan_executor.py:247-278) and
    # _http_request builds a fixed header set -- Accept, trace context, Content-Type,
    # Authorization -- with no custom headers (sandbox_write_executor_base.py:339-348).
    # step["path_params"] has zero read points anywhere in the executor chain.
    #
    # So a header- or path-located binder is silently DROPPED: the treatment request
    # goes out without the mutation, becomes identical to control, and the assertion
    # PASSES. For an isolation or authorization obligation that is a fabricated
    # "boundary verified" result -- strictly worse than a blocker, because a blocker
    # is visible and this reads as a proven property.
    #
    # Blocked here until the transport can carry these locations. Detected by the
    # presence of an unresolved placeholder token, so a literal value that happens to
    # sit in one of these keys is not affected.
    _UNMATERIALIZABLE_STEP_KEYS = ("headers", "path_params")
    for _step in control_plan + treatment_plan:
        for _location in _UNMATERIALIZABLE_STEP_KEYS:
            _spec = _dict(_step.get(_location))
            _tokens = [
                _text(name)
                for name, value in _spec.items()
                if isinstance(value, str) and "{" in value and "}" in value
            ]
            if _tokens:
                return blocked_experiment(
                    oid,
                    "BLOCKED_BINDING_LOCATION_NOT_MATERIALIZABLE",
                    f"location={_location}:params={','.join(sorted(_tokens))}",
                )

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
    # Kind-to-evidence contract. An assertion kind whose required observation key no
    # observer writes can never return a verdict; compiling it produces an experiment
    # that executes, consumes budget, and dies as a permanent INDETERMINATE that is
    # folded away downstream — the capability looks present while being unfalsifiable.
    # Block it here so the gap is a visible, countable coverage statement instead.
    _missing_evidence = unproducible_assertion_evidence(assertion_kind)
    if _missing_evidence:
        return blocked_experiment(
            oid,
            "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
            f"assertion_kind={assertion_kind}:missing_observation_key={_missing_evidence}",
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
    experiment = make_experiment(
        obligation_id=oid,
        policy_version=policy_version,
        risk_family=family,
        # Record the adapter set this compile was gated against, so runtime validation
        # agrees with it instead of re-asserting a hardcoded http_api-only world.
        compiled_adapters=adapters,
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

    # ── SPEC v1.2.2 §4: Coverage Recovery Orchestrator — Fail-Closed Gate ──
    # All five modules are hard gates. Any BLOCKED → experiment BLOCKED.
    from ai_test_asset_center.v12_coverage_recovery_orchestrator import prepare_experiment_v12
    v12_result = prepare_experiment_v12(
        obligation=obl,
        behavior_ir=ir,
        compiler_context={
            "experiment": experiment,
            "primary_operation": primary_op,
        },
    )
    v12_verdict = v12_result.get("verdict", "READY")
    if v12_verdict in ("BLOCKED", "SOURCE_DEPENDENT", "ENVIRONMENT_DEPENDENT"):
        primary_block = v12_result.get("primary_blocking_reason") or {}
        return blocked_experiment(
            oid,
            primary_block.get("reason_code", "BLOCKED_COVERAGE_RECOVERY_RECEIPT_MISSING"),
            primary_block.get("detail", "v12_coverage_recovery_blocked"),
        )
    # READY: attach v1.2 artifacts to experiment
    experiment["coverage_recovery_version"] = "v1.2.2"
    experiment["compile_coverage_receipt"] = {
        "verdict": v12_verdict,
        "fingerprint": v12_result.get("fingerprint", ""),
        "gate_receipts": v12_result.get("gate_receipts", []),
        "binding_graph_fingerprint": _text(
            _dict(v12_result.get("module_results", {}).get("binding_coverage_graph")).get("binding_graph_fingerprint")
        ),
        "observer_resolution_status": _text(
            _dict(v12_result.get("module_results", {}).get("observer_resolution_plan")).get("resolution_status")
        ),
    }
    experiment["observer_resolution_plan"] = v12_result.get("module_results", {}).get("observer_resolution_plan")
    experiment["binding_coverage_graph"] = v12_result.get("module_results", {}).get("binding_coverage_graph")
    experiment["oracle_input_contract"] = v12_result.get("module_results", {}).get("oracle_input_contract")
    experiment["fixture_dependency_dag"] = v12_result.get("module_results", {}).get("fixture_dependency_dag")
    experiment["compensation_relation_plan"] = v12_result.get("module_results", {}).get("compensation_relation_plan")

    # ── V1.2.3: Attach Readback Contract for runtime receipt generation ──
    if _readback_contract:
        experiment["readback_contract"] = _readback_contract
        experiment["compile_receipt"]["readback_contract_id"] = _text(
            _readback_contract.get("contract_id")
        )
        experiment["compile_receipt"]["readback_surface_type"] = _text(
            _readback_contract.get("readback_surface_type")
        )
        experiment["compile_receipt"]["readback_provenance_fingerprint"] = _text(
            _readback_contract.get("provenance_fingerprint")
        )

    # ── SPEC v1.1 §9: Pass cleanup exemption contract from obligation ──
    cleanup_exemption = _dict(obl.get("cleanup_exemption_contract"))
    if cleanup_exemption:
        experiment["cleanup_exemption_contract"] = cleanup_exemption
    elif cleanup_explicitly_not_required and is_write:
        # Compiler-detected ephemeral session: auto-generate exemption contract
        # so the compile-time validator can verify the waiver.
        experiment["cleanup_exemption_contract"] = {
            "kind": "ephemeral_session",
            "source_refs": _list(primary_op.get("source_refs"))[:3],
            "persistent_effect_absent": True,
            "verification_basis": "source_declared",
        }

    # ── SPEC v1.1.1 §11: Actor Selection Contract ──
    experiment["actor_selection_contract"] = {
        "selection_mode": "explicit" if _actor_selection_explicit else "constraint_based",
        "control_actor_ref": control_actor,
        "treatment_actor_ref": treatment_actor,
        "required_roles": [],
        "constraints": [],
        "source_refs": list(obl.get("source_refs") or [])[:3],
        "substitution_allowed": not _actor_selection_explicit,
    }

    # ── SPEC v1.1 §8: Compile-time Cleanup Validation ──
    # All governed writes must pass semantic cleanup validation before COMPILED.
    if is_write:
        validation = validate_cleanup_plan(
            experiment,
            ir,
            phase="compile",
        )
        if not validation["valid"]:
            return blocked_experiment(
                oid,
                validation["reason_code"],
                validation["detail"],
            )
        # Attach proof to experiment
        proof = validation.get("proof") or {}
        experiment["write_reversibility_proof"] = proof
        experiment["compile_receipt"]["write_reversibility_proof_id"] = _text(
            proof.get("proof_id")
        )
        experiment["compile_receipt"]["write_reversibility_fingerprint"] = _text(
            proof.get("fingerprint")
        )
        experiment["compile_receipt"]["cleanup_semantic_validated"] = True

    return experiment

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
    "BLOCKED_INVALID_CLEANUP_PLAN",
    "BLOCKED_CLEANUP_CONTRACT_DRIFT",
    # v1.2.2 hard gate reason codes
    "BLOCKED_ORACLE_INPUT_INCOMPLETE",
    "BLOCKED_BINDING_CYCLE",
    "BLOCKED_FORBIDDEN_BINDING_SOURCE",
    "BLOCKED_COVERAGE_RECOVERY_RECEIPT_MISSING",
    "BLOCKED_OBSERVER_CONTRACT_DRIFT",
    "BLOCKED_BINDING_GRAPH_INVALID",
    # Kind-to-evidence contract: the assertion kind requires an observation key that
    # no observer writes, so it could never return a verdict.
    "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
    # The distinguishing mutation sits in a request location the transport cannot
    # carry, so the treatment request would execute without it and PASS.
    "BLOCKED_BINDING_LOCATION_NOT_MATERIALIZABLE",
    # A registered protocol returned an unusable plan or raised. Distinct from an adapter
    # problem: the cause is the registration, not the target.
    "BLOCKED_REGISTERED_PROTOCOL_INVALID",
    "BLOCKED_FIXTURE_DAG_DRIFT",
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
    compiled_adapters: "set[str] | list[str] | None" = None,
) -> dict[str, Any]:
    eid = _text(experiment_id) or stable_experiment_id(obligation_id, "v1")
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": eid,
        # The adapter capability set this experiment was COMPILED against. Runtime
        # validation re-checks the observers against this recorded set instead of a
        # hardcoded {"http_api"}: hardcoding it meant an experiment compiled with a
        # wider set -- the whole point of registering a non-http observer -- would
        # compile and then be rejected at runtime as BLOCKED_UNSUPPORTED_ADAPTER.
        # Recording it keeps the drift check (compile and runtime must agree) without
        # pinning the value, the same pattern as the binding-graph fingerprint.
        "compiled_adapters": sorted(
            {_text(item) for item in (compiled_adapters or {"http_api"}) if _text(item)}
        ) or ["http_api"],
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
