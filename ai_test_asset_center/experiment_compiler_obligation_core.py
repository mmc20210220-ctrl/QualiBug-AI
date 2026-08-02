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
from .behavior_ir_core import _infer_operation_effect, _is_ephemeral_session_path
from .experiment_protocols import compile_family_protocol
from .observer_contracts_base import compile_observer_requirements
from .real_id_resolver import (
    collection_path,
    infer_path_params,
    normalize_path_placeholders,
)

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

_INVARIANT_ASSERTION_KINDS = frozenset({
    "conservation",
    "cross_entity_consistency",
    "field_delta",
    "forbidden_state_transition",
    "idempotency",
    "postcondition",
    "state_transition",
})
from .runtime_binding_graph import (
    blocked_binding_reasons,
    build_binding_plan,
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


def _find_collection_get_resolvers(
    primary_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> list[dict[str, str]]:
    """Find GET operations in Behavior IR that can resolve path placeholders.

    For response-only families (authorization/isolation/visibility), the
    experiment tests that a write is REJECTED. The path placeholder only needs
    a concrete resource ID to aim the request at. Any source-declared GET on
    the same entity collection can serve as a runtime resolver.
    """
    primary_path = normalize_path_placeholders(
        _text(primary_op.get("path") or primary_op.get("raw_path"))
    )
    if not primary_path.startswith("/"):
        return []
    # Derive collection path: /api/orders/{orderId} -> /api/orders
    coll = normalize_path_placeholders(collection_path(primary_path))
    if not coll.startswith("/") or "{" in coll:
        logger.debug(
            "[V1.8-rescue] collection_path invalid: primary=%s coll=%s",
            primary_path[:80], coll[:80],
        )
        return []
    resolvers: list[dict[str, str]] = []
    _ir_ops = _list(_dict(behavior_ir).get("operations"))
    for candidate in _ir_ops:
        if not isinstance(candidate, dict):
            continue
        method = _text(candidate.get("method")).upper()
        cand_path = normalize_path_placeholders(
            _text(candidate.get("path") or candidate.get("raw_path"))
        )
        cand_id = _text(candidate.get("id"))
        if not cand_id or method not in {"GET", "HEAD"}:
            continue
        # Match: collection list GET (no placeholders) on same collection
        if cand_path.rstrip("/") == coll.rstrip("/") and "{" not in cand_path:
            resolvers.append({
                "operation_ref": cand_id,
                "method": method,
                "path": cand_path,
            })
        # Also match: entity-level GET on same path pattern (e.g. GET /orders/{id})
        elif (
            "{" in cand_path
            and normalize_path_placeholders(collection_path(cand_path)).rstrip("/")
            == coll.rstrip("/")
        ):
            resolvers.append({
                "operation_ref": cand_id,
                "method": method,
                "path": cand_path,
            })
        if len(resolvers) >= 3:
            break
    return resolvers


def _find_collection_create_fixture(
    primary_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build a source-declared disposable fixture for a response-only probe.

    A fixture create is itself a governed write. Reuse the binding graph's
    existing setup authority so the request example, dependency reads, actor
    permission, and identity-bound cleanup all remain source-backed.
    """
    primary_path = normalize_path_placeholders(
        _text(primary_op.get("path") or primary_op.get("raw_path"))
    )
    if not primary_path.startswith("/"):
        return {}
    targets = infer_path_params(primary_path)
    if not targets:
        return {}
    from .runtime_binding_graph import _declared_fixture_setup

    return _declared_fixture_setup(
        primary_op,
        target=targets[0],
        behavior_ir=behavior_ir,
    )


def _rescue_binding_for_response_only_family(
    binding_plan: list[dict[str, Any]],
    primary_op: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> bool:
    """Rescue blocked bindings for response-only families.

    Replaces 'blocked' status entries with 'runtime_resolvable' when a
    source-declared GET resolver or POST create fixture exists on the same
    entity collection. Returns True if at least one binding was rescued.
    """
    resolvers = [
        row
        for row in _find_collection_get_resolvers(primary_op, behavior_ir)
        if isinstance(row, dict)
        and "{" not in _text(row.get("path"))
        and ":" not in _text(row.get("path"))
    ]
    fixture = {} if resolvers else _find_collection_create_fixture(primary_op, behavior_ir)
    if not resolvers and not fixture:
        return False
    _primary_path = normalize_path_placeholders(
        _text(primary_op.get("path") or primary_op.get("raw_path"))
    )
    _primary_params = set(infer_path_params(_primary_path))
    rescued = False
    for entry in binding_plan:
        if not isinstance(entry, dict):
            continue
        if _text(entry.get("status")) != "blocked":
            continue
        _entry_target = _text(entry.get("target"))
        # Body field placeholders (e.g. an order body's addressId) belong to
        # the field's own entity, never to the write operation's collection.
        # Binding them from GET /api/orders would cross-bind a cart/address id
        # into an order context. Leave them blocked; the observation-driven
        # expansion round recompiles them against the expanded IR.
        if (
            _list(entry.get("body_template_paths"))
            or _text(entry.get("source_priority")) == "body_placeholder_unresolvable"
            or _entry_target not in _primary_params
        ):
            continue
        entry["status"] = "runtime_resolvable"
        entry.pop("blocked_reason", None)
        if resolvers:
            entry["source_priority"] = "response_only_family_collection_read"
            entry["resolver_operations"] = resolvers[:2]
        else:
            entry["source_priority"] = "response_only_family_create_fixture"
            entry["resolver_operations"] = []
            entry["fixture_setup"] = fixture
        rescued = True
    return rescued


def _rescue_unresolved_for_response_only_family(
    binding_plan: list[dict[str, Any]],
    primary_op: dict[str, Any],
    behavior_ir: dict[str, Any],
    unresolved: list[str],
) -> bool:
    """Rescue unresolved placeholders for response-only families.

    Appends new runtime_resolvable binding entries for placeholders that
    have no binding at all, using collection GET resolvers or POST create
    fixture. Returns True if at least one placeholder was rescued.
    """
    resolvers = _find_collection_get_resolvers(primary_op, behavior_ir)
    fixture = {} if resolvers else _find_collection_create_fixture(primary_op, behavior_ir)
    if not resolvers and not fixture:
        return False
    rescued = False
    for name in unresolved:
        entry: dict[str, Any] = {
            "target": name,
            "target_path": f"/{{{name}}}",
            "status": "runtime_resolvable",
            "value_fingerprint": "",
        }
        if resolvers:
            entry["source_priority"] = "response_only_family_collection_read"
            entry["resolver_operations"] = resolvers[:2]
        else:
            entry["source_priority"] = "response_only_family_create_fixture"
            entry["resolver_operations"] = []
            entry["fixture_setup"] = fixture
        binding_plan.append(entry)
        rescued = True
    return rescued


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


# Action-verb terminal segments: POST endpoints ending in these are semantically
# read-only operations (validation, computation, query) that do not create a
# durable entity requiring effect-read observation.
_ACTION_VERB_SEGMENTS = frozenset({
    "validate",
    "verify",
    "check",
    "preview",
    "simulate",
    "estimate",
    "calculate",
    "compute",
    "search",
    "query",
    "export",
    "evaluate",
    "assess",
    "confirm",
    "test",
    "dry-run",
    "dryrun",
})


def _is_action_verb_path(path: str) -> bool:
    """True when the terminal path segment is a read-only action verb."""
    normalized = normalize_path_placeholders(_text(path)).lower().rstrip("/")
    segments = [seg for seg in normalized.split("/") if seg]
    if not segments:
        return False
    return segments[-1] in _ACTION_VERB_SEGMENTS


def _resolve_fallback_cleanup_tier(
    *,
    primary_op: dict[str, Any],
    behavior_ir: dict[str, Any],
    available_adapters: Any,
    environment_type: str = "",
) -> dict[str, Any]:
    """A cleanup plan from a non-HTTP adapter, when the API declares no compensator.

    Returns {} unless every leg holds: the adapter is declared, the environment permits
    writes, the entity is a source-declared table, and that table declares an identity
    column. The row's ownership cannot be checked here -- the identity is a runtime value
    -- so the plan carries requires_ownership_proof and the executor must refuse any row
    this run did not create.
    """
    from .cleanup_adapter_ladder import (
        LADDER_SCHEMA,
        TIER_DB,
        resolve_cleanup_adapter,
    )
    from .target_policy import is_nonproduction_environment

    if not is_nonproduction_environment(environment_type):
        return {}

    entity = _entity_for_operation(primary_op, behavior_ir)
    if not entity:
        return {}

    resolved = resolve_cleanup_adapter(
        available_adapters=available_adapters,
        api_compensator=None,
        ui_cleanup_declared=False,
        entity=entity,
        identity_value="",
        creation_receipts=[],
        target_write_approved=True,
        # Compile has no row identity; ownership is proven at runtime against the real
        # value. A placeholder identity would simply always fail the ownership gate,
        # which is why the tier never resolved here before.
        availability_only=True,
    )
    tier = _text(resolved.get("tier"))
    if tier != TIER_DB:
        # Only the data-layer tier can be pre-authorised from static facts. A UI cleanup
        # needs a declared flow, which is not derivable here.
        return {}
    plan = dict(_dict(resolved.get("plan")))
    plan.pop("identity_value", None)
    plan.pop("ownership_basis", None)
    # Align plan mode with WRP cleanup surface: mutates/transitions → field_restore,
    # produces-only → row_delete. Runtime still overrides via executed receipt mode.
    from .write_reversibility_contract import _adapter_cleanup_is_field_restore

    field_restore = _adapter_cleanup_is_field_restore(
        {"mode": "adapter_row_delete"},
        primary_method=_text(primary_op.get("method")),
        primary_path=_text(
            primary_op.get("path") or primary_op.get("raw_path")
        ),
        primary_op=primary_op,
        primary_operation_ref=_text(primary_op.get("id")),
        relations=_list(_dict(behavior_ir).get("relations")),
    )
    return {
        "schema_version": LADDER_SCHEMA,
        "tier": tier,
        "mode": "field_restore" if field_restore else "adapter_row_delete",
        "requires_ownership_proof": True,
        "plan": plan,
    }


def _entity_for_operation(
    operation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """The source-declared entity an operation's path names, if the IR declares one."""
    path = _text(_dict(operation).get("path") or _dict(operation).get("raw_path")).lower()
    if not path:
        return {}
    # A table name joins words with "_" while a path joins them with "/", so
    # /api/cart/items and the cart_items table never matched literally. Compare both
    # with separators removed; the longest match still wins, so "cart_items" beats
    # "items" rather than the other way round.
    flat_path = path.replace("_", "").replace("-", "")
    squashed_path = flat_path.replace("/", "")
    best: dict[str, Any] = {}
    for node in _list(_dict(behavior_ir).get("entities")):
        row = _dict(node)
        name = _text(row.get("name")).lower()
        if not name or not _list(row.get("identity_fields")):
            continue
        flat_name = name.replace("_", "").replace("-", "")
        singular = flat_name[:-1] if flat_name.endswith("s") and not flat_name.endswith("ss") else flat_name
        matched = (
            f"/{flat_name}" in flat_path
            or f"/{singular}" in flat_path
            or (len(flat_name) >= 6 and flat_name in squashed_path)
        )
        if matched and len(name) > len(_text(best.get("name"))):
            best = row
    return best


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
    permit_only = _text(prop.get("template")) == "permitted_operation_invocation"
    response_only_family = (
        family in {"authorization", "validation", "isolation", "visibility"}
        and not permit_only
    )
    required_ops = [
        _text(x) for x in _list(obl.get("required_operations")) if _text(x)
    ]
    required_actors = [
        _text(x) for x in _list(obl.get("required_actors")) if _text(x)
    ]
    _explicit_actor_fields = (
        "actor_ref",
        "owner_actor_ref",
        "viewer_actor_ref",
        "control_actor_ref",
        "treatment_actor_ref",
    )
    _actor_selection_explicit = bool(required_actors) or any(
        _text(prop.get(field)) for field in _explicit_actor_fields
    )
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
            return blocked_experiment(oid, "BLOCKED_MISSING_OPERATION", op_id)
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
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_ACTOR",
                f"source_permitted_actor_missing:{primary_op_id}",
            )
        required_actors = [ranked_actors[0][2]]
        prop = {
            **prop,
            "actor_ref": required_actors[0],
        }
    for actor_id in required_actors:
        if actor_id not in actors:
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
        # ── Fallback: resolve primary operation from source_refs locators ──
        # Many obligations carry source_refs with kind=api_operation and a
        # "METHOD /path" locator.  Match these against the IR operations by
        # method + normalized path so the obligation can still compile.
        _fallback_op_id = ""
        for _sref in _list(obl.get("source_refs")):
            if not isinstance(_sref, dict):
                continue
            if _text(_sref.get("kind")) != "api_operation":
                continue
            _loc = _text(_sref.get("locator"))
            if not _loc:
                continue
            _parts = _loc.split(None, 1)
            if len(_parts) != 2:
                continue
            _loc_method, _loc_path = _parts[0].upper(), _parts[1].strip()
            for _ir_id, _ir_op in ops.items():
                if not isinstance(_ir_op, dict):
                    continue
                if (
                    _text(_ir_op.get("method")).upper() == _loc_method
                    and normalize_path_placeholders(
                        _text(_ir_op.get("path") or _ir_op.get("raw_path"))
                    ) == normalize_path_placeholders(_loc_path)
                ):
                    _fallback_op_id = _ir_id
                    break
            if _fallback_op_id:
                break
        # Also try property.operation_path_prefix as a coarse match
        if not _fallback_op_id:
            _path_prefix = _text(prop.get("operation_path_prefix"))
            _op_method = _text(prop.get("method")).upper()
            if _path_prefix:
                for _ir_id, _ir_op in ops.items():
                    if not isinstance(_ir_op, dict):
                        continue
                    _ir_path = normalize_path_placeholders(
                        _text(_ir_op.get("path") or _ir_op.get("raw_path"))
                    )
                    if _ir_path.startswith(_path_prefix) and (
                        not _op_method
                        or _text(_ir_op.get("method")).upper() == _op_method
                    ):
                        _fallback_op_id = _ir_id
                        break
        if _fallback_op_id:
            primary_op_id = _fallback_op_id
            if primary_op_id not in required_ops:
                required_ops.append(primary_op_id)
    if not primary_op_id or primary_op_id not in ops:
        # Skip obligations whose primary operation is not available
        return make_experiment(
            obligation_id=oid,
            risk_family=family,
            compile_receipt={"status": "DEFERRED", "reason_code": "MISSING_PRIMARY_OPERATION", "detail": primary_op_id or "none"},
        )
    primary_op = ops[primary_op_id]
    primary_path_early = normalize_path_placeholders(
        _text(primary_op.get("path") or primary_op.get("raw_path"))
    )
    is_ephemeral_session = _is_ephemeral_session_path(primary_path_early)
    # Determine governed-write status from the source-declared semantic effect
    # first. Session/token exchanges may use POST and even be declared as
    # write-like at the HTTP layer, but they do not create a durable resource
    # that can carry a reversible cleanup proof.
    # A POST is not inherently a mutation: source contracts can explicitly
    # declare read-only action endpoints such as validation/preview routes.
    # The shared helper falls back to the HTTP method only when the source did
    # not declare an effect, keeping ordinary undeclared POSTs governed as
    # writes while avoiding a false cleanup requirement for declared reads.
    _op_method_upper = _text(primary_op.get("method")).upper()
    is_write = (
        _infer_operation_effect(primary_op, _op_method_upper) == "write"
        and not is_ephemeral_session
    )
    if (
        is_write
        and _op_method_upper in {"PUT", "PATCH"}
        and not _source_request_example(primary_op)
    ):
        return blocked_experiment(
            oid,
            "BLOCKED_MISSING_BINDING",
            f"source_declared_request_body_missing:{primary_op_id}",
        )
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
        except Exception as _rb_exc:
            logging.getLogger(__name__).warning(
                "readback resolver raised for obligation %s (%s: %s)",
                oid,
                type(_rb_exc).__name__,
                str(_rb_exc)[:200],
                exc_info=_rb_exc,
            )
            return blocked_experiment(
                oid,
                "BLOCKED_OBSERVER_RESOLUTION_FAILED",
                f"{type(_rb_exc).__name__}:{str(_rb_exc)[:160]}",
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
        # V1.8: Authorization/isolation/visibility families test access denial.
        # The write is expected to be REJECTED (403). The path placeholder only
        # needs a concrete resource ID to aim at — rescue blocked bindings by
        # finding any source-declared GET on the same entity collection.
        if response_only_family:
            _rescued = _rescue_binding_for_response_only_family(
                binding_plan, primary_op, ir,
            )
            if _rescued:
                _blocked_reasons = blocked_binding_reasons(binding_plan)
            logger.warning(
                "[V1.8-rescue] oid=%s family=%s path=%s rescued=%s still_blocked=%s",
                oid[:30], family,
                _text(primary_op.get("path") or primary_op.get("raw_path"))[:50],
                _rescued, _blocked_reasons[:2],
            )
        if _blocked_reasons:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_BINDING",
                f"unresolvable_path_placeholders:{';'.join(_blocked_reasons[:4])}",
            )
    if (
        not is_write
        and response_only_family
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
                return blocked_experiment(
                    oid,
                    "BLOCKED_MISSING_BINDING",
                    f"owner_identity_resolver_missing:{identity_target}",
                )
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
    if unresolved:
        # V1.8: Same rescue for response-only families at the unresolved check.
        if response_only_family:
            _rescue_unresolved = _rescue_unresolved_for_response_only_family(
                binding_plan, primary_op, ir, unresolved,
            )
            if _rescue_unresolved:
                unresolved = unresolved_placeholders(primary_op, binding_plan)
        if unresolved:
            return blocked_experiment(
                oid,
                "BLOCKED_MISSING_BINDING",
                f"unresolved_placeholders_no_fixture:{';'.join(unresolved[:6])}",
            )
    if is_write and not write_observers:
        primary_path_for_observers = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        # Session/token posts are verified by transport status alone; they do
        # not produce a durable entity that an effect-read observer can bind.
        if not _is_ephemeral_session_path(primary_path_for_observers):
            # ── Auto-observer resolution: find a source-declared GET that can
            # observe this write's effect (same entity path prefix). ──
            from .auto_observer_injector import find_read_endpoint_for_write as _find_read
            _auto_read_op = _find_read(primary_op, ir)
            if _auto_read_op and _text(_auto_read_op.get("id")):
                write_observers = [{
                    "operation_ref": _text(_auto_read_op.get("id")),
                    "method": "GET",
                    "path": normalize_path_placeholders(
                        _text(_auto_read_op.get("path") or _auto_read_op.get("raw_path"))
                    ),
                    "observation_basis": "discovered_source_declared_read",
                }]
            else:
                # V1.7: Authorization/isolation/visibility/validation experiments
                # prove violations via response status (403/400/422) or comparison
                # observer. A missing effect-read endpoint does not invalidate the
                # test — the response/comparison is the primary evidence authority.
                if response_only_family:
                    pass  # compile without write_observers
                elif _is_action_verb_path(primary_path_for_observers):
                    # POST endpoints whose terminal segment is an action verb
                    # (validate, verify, check, ...) are semantically read-only;
                    # they do not produce a durable entity requiring observation.
                    pass
                else:
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
        # Instead of blocking, inject the most basic observer. http_response
        # can detect status-code and body-level violations without any extra
        # fixture or readback setup.
        required_observers = ["http_response"]

    raw_cleanup_req = obl.get("cleanup_requirement")
    if isinstance(raw_cleanup_req, str):
        # Handle string format: "required" or "not_required"
        cleanup_req = {"required": raw_cleanup_req.strip().lower() != "not_required"}
    else:
        cleanup_req = _dict(raw_cleanup_req)
    cleanup_plan: list[dict[str, Any]] = []
    cleanup_explicitly_not_required = is_ephemeral_session
    if is_write:
        primary_path_for_cleanup = normalize_path_placeholders(
            _text(primary_op.get("path") or primary_op.get("raw_path"))
        )
        # Fail closed: a write may waive cleanup only for ephemeral session
        # posts. Matrix/coverage strings like "not_required" on entity writes
        # previously compiled accepted creates with no compensator.
        #
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
                else:
                    # No API compensator. "The API has no undo" is not "this cannot be
                    # cleaned up": a run that creates its own row can delete that row
                    # through an adapter the operator declared. Measured on a live
                    # target, only 2 of 17 writes had an API compensator and 680
                    # obligations blocked here, so refusing to look further turned a
                    # capability gap into a coverage gap.
                    #
                    # Compile decides only that the tier is AVAILABLE -- the declared
                    # adapter, a source-declared table, a declared identity column. The
                    # concrete row identity is not known until runtime, so the ownership
                    # proof is deferred and carried as requires_ownership_proof: a
                    # data-layer delete may only ever touch a row this run created.
                    _fallback = _resolve_fallback_cleanup_tier(
                        primary_op=primary_op,
                        behavior_ir=ir,
                        available_adapters=available_adapters,
                        environment_type=environment_type,
                    )
                    if _fallback:
                        cleanup_req = {
                            **cleanup_req,
                            "required": True,
                            "mode": _text(_fallback.get("mode")) or "adapter_cleanup",
                            "adapter_cleanup": _fallback,
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
                "mode": "restore_snapshot",
                "operation_ref": primary_op_id,
                "path": primary_path,
                "method": primary_method,
                "runtime_response_binding_required": "{" in primary_path,
            }]
        if not cleanup_plan and not cleanup_op:
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
                kind = _text(rel.get("kind") or rel.get("relation_type"))
                if kind not in {"compensates", "inverse", "compensation"}:
                    continue
                if not _list(rel.get("source_refs")):
                    continue
                standard_cleanup = _text(
                    rel.get("operation_ref") or rel.get("from_ref")
                )
                standard_primary = _text(rel.get("to_ref"))
                legacy_primary = _text(
                    rel.get("source") or rel.get("source_operation_ref")
                )
                legacy_cleanup = _text(
                    rel.get("target") or rel.get("target_operation_ref")
                )
                if (
                    standard_primary == primary_op_id
                    and standard_cleanup in ops
                ):
                    explicit_compensator = _dict(ops.get(standard_cleanup))
                    break
                if legacy_primary == primary_op_id and legacy_cleanup in ops:
                    explicit_compensator = _dict(ops.get(legacy_cleanup))
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
                    "mode": "restore_snapshot",
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
                # Resolve the fallback tier HERE, at the point of blocking, rather than
                # relying on one upstream branch. That branch only runs for paths with no
                # placeholder, which excludes most writes -- the ladder was wired where it
                # could not fire.
                _adapter_cleanup = _dict(cleanup_req.get("adapter_cleanup"))
                if not _adapter_cleanup.get("plan") and cleanup_req.get("required") is not False:
                    _adapter_cleanup = _resolve_fallback_cleanup_tier(
                        primary_op=primary_op,
                        behavior_ir=ir,
                        available_adapters=available_adapters,
                        environment_type=environment_type,
                    )
                if cleanup_req.get("required") is False:
                    # Cleanup explicitly not required; proceed without it.
                    cleanup_plan = []
                    cleanup_explicitly_not_required = True
                elif _adapter_cleanup.get("plan"):
                    # No API compensator, but the operator declared an adapter that can
                    # undo this write. The requirement is unchanged -- a real, executable
                    # compensator that leaves a receipt -- only the surface differs.
                    #
                    # requires_ownership_proof travels with the plan and the executor
                    # must refuse any row this run did not create. Compile cannot check
                    # it: the row identity is a runtime value.
                    #
                    # Mode must win over ladder plan defaults (often row_delete): unpack
                    # plan first, then set the WRP-aligned surface mode last.
                    _adapter_plan = _dict(_adapter_cleanup.get("plan"))
                    cleanup_plan = [{
                        **_adapter_plan,
                        "action": "declared_adapter_cleanup",
                        "mode": _text(_adapter_cleanup.get("mode")) or "adapter_row_delete",
                        "adapter": _text(_adapter_plan.get("adapter")),
                        "requires_ownership_proof": True,
                        "compensates_operation_ref": primary_op_id,
                    }]
                else:
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
                        "mode": "delete_created_resource",
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
                        "mode": "compensating_transition",
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

    # ── V1.5.0 §10: Compile-Time Fixture Binding ──
    # Discover fixture candidates and build Disposable Fixture Contract BEFORE
    # final experiment assembly. Write experiments that create persistent entities
    # carry a resolved contract; multi-step protocols consume it.
    _disposable_fixture_contract: dict | None = None
    _fixture_dag: dict | None = None
    if is_write and not _is_ephemeral_session_path(primary_path_early):
        try:
            from .disposable_fixture_contract import (
                discover_fixture_candidates as _discover_fixture_candidates,
                build_disposable_fixture_contract as _build_fixture_contract,
                build_fixture_dag as _build_fixture_dag,
                STATUS_RESOLVED as _FIXTURE_RESOLVED,
            )
            _fixture_candidates = _discover_fixture_candidates(
                ir,
                entity_ids=[
                    _text(ref)
                    for ref in _list(primary_op.get("entity_refs"))
                    if _text(ref)
                ] or None,
            )
            if _fixture_candidates:
                # Use the first resolved candidate (primary entity)
                _best_candidate = next(
                    (c for c in _fixture_candidates if c.get("status") == _FIXTURE_RESOLVED),
                    _fixture_candidates[0],
                )
                _disposable_fixture_contract = _build_fixture_contract(
                    obligation_id=oid,
                    experiment_id=f"exp_{oid}",
                    campaign_id="",  # bound at runtime
                    candidate=_best_candidate,
                    behavior_ir=ir,
                    actor_ref=control_actor or (required_actors[0] if required_actors else ""),
                )
                # Build DAG if multiple candidates (multi-entity fixture)
                if len(_fixture_candidates) > 1:
                    _multi_contracts = [
                        _build_fixture_contract(
                            obligation_id=oid,
                            experiment_id=f"exp_{oid}",
                            campaign_id="",
                            candidate=cand,
                            behavior_ir=ir,
                            actor_ref=control_actor or (required_actors[0] if required_actors else ""),
                        )
                        for cand in _fixture_candidates
                    ]
                    _fixture_dag = _build_fixture_dag(
                        _multi_contracts,
                        behavior_ir=ir,
                    )
        except Exception as _fc_exc:
            logger.warning(
                "fixture contract discovery failed for %s: %s: %s",
                oid,
                type(_fc_exc).__name__,
                str(_fc_exc)[:200],
                exc_info=_fc_exc,
            )
            return blocked_experiment(
                oid,
                "BLOCKED_FIXTURE_CONTRACT_FAILED",
                f"{type(_fc_exc).__name__}:{str(_fc_exc)[:160]}",
            )
    observer_requirements = list(required_observers)
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
    # Write experiments with resolved write_observers but no effect observer
    # need a default entity_state observer so the runtime gate can verify the
    # write took effect. Without this, the experiment compiles but is blocked
    # at execution as BLOCKED_MISSING_OBSERVER:write_observer.
    #
    # EXCEPTION: authorization/validation/isolation/visibility families assert
    # on HTTP response status (403/400/401). Their write is expected to be
    # rejected; no state change occurs, so effect observation is unnecessary.
    if (
        is_write
        and write_observers
        and not effect_observer_ids
        and not response_only_family
    ):
        observers.append({
            "observer_id": "entity_state",
            "adapter": "http_api",
            "observation_mode": "before_after_comparison",
        })
        effect_observer_ids = {"entity_state"}
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

    # Registered-protocol extensions. Guarded on the marker, which experiment_protocols sets
    # in exactly one place after a registry hit -- so this block never runs for a built-in
    # protocol and the state rewrite below evaluates identically for every existing state
    # obligation. tests/test_experiment_protocol_registry.py asserts, for all six built-in
    # families, that no built-in result carries the marker.
    _registry_protocol_id = _text(protocol.get("_registry_protocol_id"))
    _precondition_plan: list[dict[str, Any]] = []
    if _registry_protocol_id:
        # Establish the declared source state before the measured window.
        #
        # Without this, experiment_compiler_support strips the entity_in_state:* fixtures a
        # state obligation requests and substitutes the literal "unknown_state", which the
        # state_transition evaluator degrades from "did the declared transition happen" into
        # "did anything change at all".
        if protocol.get("requires_state_precondition") is True:
            from .state_precondition_planner import plan_state_precondition

            _precondition = plan_state_precondition(
                behavior_ir=ir,
                from_state=_text(prop.get("from_state")),
                actors=[_text(actor) for actor in required_actors if _text(actor)],
            )
            if _text(_precondition.get("status")) != "PLANNED":
                return blocked_experiment(
                    oid,
                    "BLOCKED_PRECONDITION_UNREACHABLE",
                    f"{_registry_protocol_id}:{_text(_precondition.get('reason_code'))}",
                )
            _precondition_plan = [
                row for row in _list(_precondition.get("steps")) if isinstance(row, dict)
            ]

        # Cleanup coverage across EVERY write, not just the first.
        #
        # _identify_primary_write proves reversibility for the first treatment write only, so
        # an N-step process whose later steps create entities under different operations
        # would carry a compile-time proof covering step 1 and leave the rest as residue.
        # Blocking is the fail-closed answer; AGENTS.md forbids waiving cleanup.
        _declared_compensated_ops = {
            _text(_dict(row).get("operation_ref"))
            for row in _list(protocol.get("cleanup_plan"))
            if _text(_dict(row).get("operation_ref"))
        }
        _write_ops_needing_cleanup = {
            _text(_dict(row).get("operation_ref"))
            for row in [*_precondition_plan, *_list(protocol.get("treatment_plan"))]
            if _text(_dict(row).get("operation_ref"))
            and _text(_dict(ops.get(_text(_dict(row).get("operation_ref")))).get("read_write"))
            == "write"
        }
        _uncovered = sorted(_write_ops_needing_cleanup - _declared_compensated_ops)
        if _uncovered and _list(protocol.get("cleanup_plan")):
            return blocked_experiment(
                oid,
                "BLOCKED_STEP_CLEANUP_UNCOVERED",
                f"{_registry_protocol_id}:{','.join(_uncovered)}",
            )

    if family == "state" and not _registry_protocol_id:
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
            # V1.6.1: lift from/to from expression operands when top-level absent.
            _st_expr = _dict(prop.get("expression"))
            _st_from = _text(prop.get("from_state") or prop.get("from_state_ref"))
            _st_to = _text(prop.get("to_state") or prop.get("to_state_ref"))
            if not _st_from or not _st_to:
                for _op in _list(_st_expr.get("operands")):
                    if not isinstance(_op, dict):
                        continue
                    if not _st_from:
                        _st_from = _text(_op.get("from_state") or _op.get("from_state_ref"))
                    if not _st_to:
                        _st_to = _text(_op.get("to_state") or _op.get("to_state_ref"))
            _st_kind = (
                "forbidden_state_transition"
                if _text(_st_expr.get("kind")) == "forbidden_state_transition"
                or _text(_st_expr.get("operator")).lower() == "must_not_transition"
                else "state_transition"
            )
            protocol = {
                **protocol,
                "control_plan": [],
                "treatment_plan": treatment_rows,
                "assertion": {
                    "kind": _st_kind,
                    "from_state": _st_from,
                    "to_state": _st_to,
                    "operator": _text(_st_expr.get("operator")) or "must_transition",
                    "operands": _list(_st_expr.get("operands")),
                    "invariant_ref": _text(prop.get("invariant_ref")),
                    "rule_id": _text(prop.get("invariant_ref")),
                },
            }
        # ── V1.5.0 §16: State Precondition Planning for built-in state family ──
        # Plan the path to establish from_state before the measured window.
        _from_state = _text(
            _dict(protocol.get("assertion")).get("from_state")
            or prop.get("from_state")
        )
        if _from_state and _from_state.lower() not in ("", "unknown_state", "unknown"):
            from .state_precondition_planner import (
                plan_state_precondition as _plan_precondition,
                STATUS_PLANNED as _PRECOND_PLANNED,
            )
            _precond_result = _plan_precondition(
                behavior_ir=ir,
                from_state=_from_state,
                actors=[_text(a) for a in required_actors if _text(a)],
            )
            if _text(_precond_result.get("status")) == _PRECOND_PLANNED:
                _precondition_plan = [
                    row for row in _list(_precond_result.get("steps"))
                    if isinstance(row, dict)
                ]
            # BLOCKED precondition is not fatal for built-in state family;
            # the experiment can still execute with runtime binding.
            # But we record it for observability.
            elif _text(_precond_result.get("status")) == "BLOCKED":
                logger.debug(
                    "V1.5.0 state precondition blocked for %s: %s",
                    oid, _precond_result.get("reason_code"),
                )

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
    # Plan step-identity gate. Runs for EVERY protocol, built-in or registered.
    #
    # A step's id is its contract subject: contract_oracles._plan_subjects derives each
    # activation subject as `step_id or id` and then collapses the list with
    # dict.fromkeys. So an empty or repeated step_id silently shrinks required[phase],
    # shifts every positional lookup after it, and finally trips the delivery gate's
    # duplicate-subject check -- after the experiment has already issued real requests.
    # Blocking at compile time makes it one visible reason code instead of a confusing
    # late failure.
    #
    # Safe for existing plans: every built-in protocol branch emits exactly one literal
    # 'control_1' and/or one 'treatment_1', so no current plan has an empty or duplicated
    # step_id. Verified against the built-in branches before adding this.
    for _phase_name, _phase_plan in (("control", control_plan), ("treatment", treatment_plan)):
        _seen_step_ids: set[str] = set()
        for _plan_step in _phase_plan:
            _step_identity = _text(_plan_step.get("step_id"))
            if not _step_identity:
                return blocked_experiment(
                    oid,
                    "BLOCKED_PLAN_STEP_IDENTITY_INVALID",
                    f"{_phase_name}:missing_step_id",
                )
            if _step_identity in _seen_step_ids:
                return blocked_experiment(
                    oid,
                    "BLOCKED_PLAN_STEP_IDENTITY_INVALID",
                    f"{_phase_name}:duplicate:{_step_identity}",
                )
            _seen_step_ids.add(_step_identity)

    # A multi-step plan whose steps cannot each be observed must not execute. Per-phase
    # observation keeps only the first and last governed write, so steps 2..N-1 would
    # vanish and the experiment would still report a verdict from partial evidence.
    if len(treatment_plan) > 1 and not bool(protocol.get("per_step_evidence")):
        return blocked_experiment(
            oid,
            "BLOCKED_STEP_EVIDENCE_UNOBSERVABLE",
            f"treatment_steps={len(treatment_plan)}:per_step_evidence_not_declared",
        )

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
    protocol_kind = _text(protocol_assertion.get("kind"))
    declared_invariant_kind = ""
    if family == "invariant":
        expression_kind = _text(_dict(prop.get("expression")).get("kind")).lower()
        for candidate in (
            _text(prop.get("invariant_kind")).lower(),
            expression_kind,
        ):
            if candidate in _INVARIANT_ASSERTION_KINDS:
                declared_invariant_kind = candidate
                break
        if not protocol_kind and not declared_invariant_kind:
            return blocked_experiment(
                oid,
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                "invariant_assertion_kind_missing",
            )
    assertion_kind = (
        protocol_kind
        or declared_invariant_kind
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
    # V1.6.1: propagate field_rule_binding into assertion + experiment contract.
    _frb = _dict(prop.get("field_rule_binding"))
    if not _frb and _text(prop.get("invariant_ref")):
        _frb = {
            "rule_id": _text(prop.get("invariant_ref")),
            "rule_fingerprint": _text(prop.get("invariant_ref")),
            "rule_type": family,
            "required_field_ids": [],
            "typed_expression": _dict(prop.get("expression")),
            "operation_id": primary_op_id,
        }
    assertions = [{
        "assertion_id": f"assert_{family or 'generic'}",
        "kind": assertion_kind,
        "template": _text(prop.get("template")),
        "expected_from": "source_property",
        "property": prop,
        "require_control": needs_control,
        "rule_id": _text(_frb.get("rule_id") or prop.get("invariant_ref")),
        "invariant_ref": _text(prop.get("invariant_ref")),
        **{
            key: value
            for key, value in protocol_assertion.items()
            if key != "kind"
        },
    }]
    if _frb:
        assertions[0]["field_rule_binding"] = dict(_frb)
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

    # ── V1.6.0 P0-6: Field-Level Rule Completeness Gate ──
    # Fail closed before COMPILED when deep families lack bound fields/formula/
    # observers. Authorization/HTTP families are intentionally excluded.
    _field_gate = _field_level_rule_completeness_gate(
        family=family,
        assertion_kind=assertion_kind,
        protocol_assertion=protocol_assertion,
        prop=prop,
        observers=observers,
        cleanup_plan=cleanup_plan,
        is_write=is_write,
    )
    if _field_gate:
        return blocked_experiment(oid, _field_gate[0], _field_gate[1])

    # V1.6.0 P0-11: attach field observation contract onto field observers.
    _req_field_ids: list[str] = []
    for _asrt in assertions:
        if not isinstance(_asrt, dict):
            continue
        for _op in _list(_asrt.get("operands") or _asrt.get("fields")):
            if isinstance(_op, dict):
                _fid = _text(_op.get("field_id") or _op.get("field"))
                if _fid:
                    _req_field_ids.append(_fid)
        _eq = _dict(_asrt.get("equation"))
        for _term in _list(_eq.get("terms") or _eq.get("fields")):
            if isinstance(_term, dict):
                _fid = _text(_term.get("field_id") or _term.get("field"))
                if _fid:
                    _req_field_ids.append(_fid)
            elif _text(_term):
                _req_field_ids.append(_text(_term))
    _req_field_ids = list(dict.fromkeys(_req_field_ids))
    if _req_field_ids:
        for _obs in observers:
            if not isinstance(_obs, dict):
                continue
            if _text(_obs.get("observer_id")) in {
                "entity_state",
                "before_state",
                "after_state",
                "business_effect",
                "final_state",
            }:
                _obs["required_field_ids"] = list(_req_field_ids)
                _obs["field_observation_contract"] = {
                    "schema_version": "qualibug.field-observation-contract.v1",
                    "required_field_ids": list(_req_field_ids),
                    "scan_unscoped_numerics": False,
                }

    experiment = make_experiment(
        obligation_id=oid,
        policy_version=policy_version,
        risk_family=family,
        # Record the adapter set this compile was gated against, so runtime validation
        # agrees with it instead of re-asserting a hardcoded http_api-only world.
        compiled_adapters=adapters,
        # Empty for every built-in compile, so the key is not emitted at all there.
        precondition_plan=_precondition_plan,
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
            # The path classifier is source-derived and is the only authority for
            # treating session/token exchanges as non-durable. Preserve that
            # boundary for the runtime assertion gate instead of asking it to
            # invent a business-effect receipt for a session response.
            "business_effect_requirement": (
                "NOT_APPLICABLE" if is_ephemeral_session else "REQUIRED"
            ),
            "business_effect_requirement_basis": (
                "source_path_semantics"
                if is_ephemeral_session
                else "source_operation_effect"
            ),
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

    # Persist compiled write_observers so the runtime step executor can fall back
    # to compiler-resolved observation paths when its own re-derivation fails
    # (e.g. placeholder materialization gaps between compile and runtime).
    if write_observers:
        experiment["write_observers"] = list(write_observers)

    # ── Multi-write cleanup step scoping ──
    # The cleanup validator requires per-step identity (source_step_id) when an
    # experiment has multiple mutating steps (e.g. control_1 + treatment_1).
    # Expand each template cleanup entry into one entry per matching write step,
    # emitted in reverse execution order so the validator's reverse-dependency
    # check passes.
    if is_write and cleanup_plan:
        _write_step_rows: list[tuple[str, str]] = []  # (step_id, operation_ref)
        for _phase in ("control_plan", "treatment_plan"):
            for _step in _list(experiment.get(_phase)):
                _s = _dict(_step)
                _s_op = _text(_s.get("operation_ref"))
                _s_method = _text(
                    _s.get("method") or _dict(ops.get(_s_op)).get("method")
                ).upper()
                if _s_method in {"POST", "PUT", "PATCH", "DELETE"}:
                    _write_step_rows.append((_text(_s.get("step_id")), _s_op))
        if len(_write_step_rows) > 1:
            # A snapshot restore is an experiment-level authority: the
            # cleanup executor restores every accepted write for the same
            # operation from the captured before-state. Expanding it once per
            # step would execute the same restore repeatedly.
            _snapshot_cleanup = [
                _tmpl
                for _tmpl in cleanup_plan
                if _text(_tmpl.get("action")) == "restore_before_snapshot"
                and _text(_tmpl.get("mode")) in {"snapshot_restore", "restore_snapshot"}
            ]
            if len(_snapshot_cleanup) == 1 and len(_snapshot_cleanup) == len(cleanup_plan):
                _expanded_cleanup = list(cleanup_plan)
            else:
                _expanded_cleanup = []
            if not _expanded_cleanup:
                for _step_id, _step_op_ref in reversed(_write_step_rows):
                    for _tmpl in cleanup_plan:
                        _comp_ref = _text(
                            _tmpl.get("compensates_operation_ref")
                            or _tmpl.get("operation_ref")
                        )
                        if _comp_ref == _step_op_ref:
                            _expanded_cleanup.append({**_tmpl, "source_step_id": _step_id})
            if _expanded_cleanup:
                experiment["cleanup_plan"] = _expanded_cleanup

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

    # ── V1.5.0 §10: Attach Disposable Fixture Contract ──
    if _disposable_fixture_contract:
        experiment["disposable_fixture_contract"] = _disposable_fixture_contract
        experiment["compile_receipt"]["fixture_contract_id"] = _text(
            _disposable_fixture_contract.get("fixture_id")
        )
        experiment["compile_receipt"]["fixture_contract_status"] = _text(
            _disposable_fixture_contract.get("status")
        )
    if _fixture_dag:
        experiment["fixture_dag"] = _fixture_dag
        experiment["compile_receipt"]["fixture_dag_id"] = _text(
            _fixture_dag.get("fixture_dag_id")
        )
        experiment["compile_receipt"]["fixture_dag_status"] = _text(
            _fixture_dag.get("status")
        )

    # ── SPEC v1.1 §9: Pass cleanup exemption contract from obligation ──
    cleanup_exemption = _dict(obl.get("cleanup_exemption_contract"))
    if cleanup_exemption:
        experiment["cleanup_exemption_contract"] = cleanup_exemption
    elif cleanup_explicitly_not_required:
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
        "selection_mode": (
            "explicit" if _actor_selection_explicit else "source_permitted"
        ),
        "control_actor_ref": control_actor,
        "treatment_actor_ref": treatment_actor,
        "required_roles": [],
        "constraints": [],
        "source_refs": list(obl.get("source_refs") or [])[:3],
        "substitution_allowed": False,
    }

    # ── SPEC v1.1 §8: Compile-time Cleanup Validation ──
    # Every governed write, including response-only probes, must pass semantic
    # cleanup validation before COMPILED. A rejected response is an observed
    # outcome, not proof that fixture/setup writes were harmless.
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

    # ── V1.3.0-A: Database Cleanup Contract (compile-time) ──
    # Every governed write gets a structured DB cleanup contract that binds
    # entity identity, dependency graph, authority, and pre-image plan.
    if is_write:
        from .cleanup_adapter_ladder import build_database_cleanup_contract as _build_db_contract
        _db_contract = _build_db_contract(
            experiment_id=_text(experiment.get("experiment_id")),
            campaign_id="",  # bound at runtime by campaign context
            write_operation=primary_op,
            behavior_ir=ir,
            entities=_list(ir.get("entities")),
            cleanup_plan=cleanup_plan,
            environment_type=env,
            available_adapters=adapters,
        )
        experiment["database_cleanup_contract"] = _db_contract
        experiment["database_dependency_graph"] = _list(_db_contract.get("dependency_order"))
        experiment["compile_receipt"]["db_cleanup_contract_id"] = _text(
            _db_contract.get("contract_id")
        )
        experiment["compile_receipt"]["db_cleanup_contract_status"] = _text(
            _db_contract.get("status")
        )
        # V1.3.0-A: Contract is informational at compile-time; runtime executor
        # enforces cleanup via the existing validate_cleanup_plan gate above.
        # UNSAFE/NOT_DECLARED is recorded for observability, not blocking here.

    # V1.6.1: Field Oracle Runtime Contract on compiled experiments.
    if _frb or assertion_kind in {
        "conservation",
        "field_delta",
        "postcondition",
        "state_transition",
        "forbidden_state_transition",
        "cross_entity_consistency",
    }:
        _a0 = assertions[0] if assertions and isinstance(assertions[0], dict) else {}
        experiment["field_oracle_runtime_contract"] = {
            "schema_version": "qualibug.field-oracle-runtime-contract.v1",
            "rule_id": _text(_frb.get("rule_id") or _a0.get("rule_id") or prop.get("invariant_ref")),
            "rule_fingerprint": _text(_frb.get("rule_fingerprint") or prop.get("invariant_ref")),
            "rule_type": family,
            "operation_id": primary_op_id,
            "required_field_ids": list(_req_field_ids or _frb.get("required_field_ids") or []),
            "scope_field_ids": [],
            "before_observation_contract": "before_state",
            "after_observation_contract": "after_state",
            "assertion_kind": assertion_kind,
            "typed_expression": _dict(prop.get("expression") or _frb.get("typed_expression")),
            "fixture_contract_id": "",
            "cleanup_contract_ids": [
                _text(row.get("action") or row.get("step_id"))
                for row in cleanup_plan
                if isinstance(row, dict)
            ][:5],
            "status": "RESOLVED",
        }

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
    # A plan step has no id or repeats one, so its contract subject would collide.
    "BLOCKED_PLAN_STEP_IDENTITY_INVALID",
    # A multi-step plan with no per-step observation would lose its middle steps.
    "BLOCKED_STEP_EVIDENCE_UNOBSERVABLE",
    # The declared source state cannot be established from the declared transition graph.
    "BLOCKED_PRECONDITION_UNREACHABLE",
    # A write in the plan has no declared compensator, so it would leave residue.
    "BLOCKED_STEP_CLEANUP_UNCOVERED",
    "BLOCKED_FIXTURE_DAG_DRIFT",
    # V1.3.0-A: Database cleanup contract breakpoints
    "DB_CLEANUP_AUTHORITY_NOT_DECLARED",
    "DB_ROW_IDENTITY_NOT_BOUND",
    "DB_DEPENDENCY_GRAPH_INCOMPLETE",
    "DB_PREIMAGE_NOT_CAPTURED",
    # V1.6.0 field-level oracle completeness
    "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
    "BLOCKED_EMPTY_CONSERVATION_TERMS",
    "STATE_RULE_PRECONDITION_NOT_ESTABLISHED",
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
    precondition_plan: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    eid = _text(experiment_id) or stable_experiment_id(obligation_id, "v1")
    experiment = {
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
    # Emitted CONDITIONALLY. An unconditional "precondition_plan": [] would add a key to
    # every experiment dict the product has ever produced, changing the shape of ~3100
    # stored artifacts for no benefit. Only a plan that actually establishes something
    # carries the key.
    if precondition_plan:
        experiment["precondition_plan"] = [
            dict(row) for row in precondition_plan if isinstance(row, dict)
        ]
    return experiment


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


def _field_level_rule_completeness_gate(
    *,
    family: str,
    assertion_kind: str,
    protocol_assertion: dict[str, Any],
    prop: dict[str, Any],
    observers: list[Any],
    cleanup_plan: list[Any],
    is_write: bool,
) -> tuple[str, str] | None:
    """Return (reason_code, detail) when a field-level rule cannot execute.

    Parent reason is FIELD_LEVEL_RULE_NOT_EXECUTABLE except empty conservation
    terms which keep BLOCKED_EMPTY_CONSERVATION_TERMS for funnel continuity.
    """
    deep_kinds = {
        "conservation",
        "field_delta",
        "postcondition",
        "state_transition",
        "cross_entity_consistency",
    }
    deep_families = {"conservation", "state", "state_integrity", "lifecycle", "invariant"}
    if assertion_kind not in deep_kinds and family not in deep_families:
        return None

    expression = _dict(prop.get("expression") or protocol_assertion.get("expression"))
    equation = _dict(
        protocol_assertion.get("equation")
        or prop.get("equation")
        or expression.get("equation")
    )
    operands = _list(
        protocol_assertion.get("operands")
        or protocol_assertion.get("fields")
        or expression.get("operands")
    )
    field_ids = []
    for item in operands:
        if isinstance(item, dict):
            fid = _text(item.get("field_id") or item.get("field"))
            if fid:
                field_ids.append(fid)
    for term in _list(equation.get("terms") or equation.get("fields")):
        if isinstance(term, dict):
            fid = _text(term.get("field_id") or term.get("field"))
            if fid:
                field_ids.append(fid)
        elif _text(term):
            field_ids.append(_text(term))

    observer_ids = {
        _text(row.get("observer_id"))
        for row in observers
        if isinstance(row, dict) and _text(row.get("observer_id"))
    }
    field_observers = observer_ids & {
        "entity_state",
        "before_state",
        "after_state",
        "business_effect",
        "final_state",
    }

    if assertion_kind == "conservation" or family == "conservation":
        if not field_ids:
            return (
                "BLOCKED_EMPTY_CONSERVATION_TERMS",
                "missing_equation_terms_or_field_operands",
            )
        if not field_observers:
            return (
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                "conservation_missing_field_observer",
            )
        if is_write and not cleanup_plan:
            return (
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                "conservation_missing_cleanup",
            )
        return None

    if assertion_kind in {"state_transition", "forbidden_state_transition"} or (
        family in {"state", "state_integrity", "lifecycle"}
        and assertion_kind not in {"conservation", "field_delta", "postcondition"}
    ):
        from_state = _text(
            protocol_assertion.get("from_state") or prop.get("from_state")
        )
        to_state = _text(
            protocol_assertion.get("to_state") or prop.get("to_state")
        )
        if not from_state or not to_state:
            # V1.6.1: lift endpoints from expression operands before fail-closed.
            for _op in operands:
                if not isinstance(_op, dict):
                    continue
                if not from_state:
                    from_state = _text(_op.get("from_state"))
                if not to_state:
                    to_state = _text(_op.get("to_state"))
        from_state_l = from_state.lower()
        to_state_l = to_state.lower()
        if from_state_l in {"", "unknown_state", "unknown"} or to_state_l in {
            "",
            "unknown_state",
            "unknown",
        }:
            return (
                "STATE_RULE_PRECONDITION_NOT_ESTABLISHED",
                "state_transition_requires_concrete_from_to",
            )
        if not field_observers:
            return (
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                "state_missing_field_observer",
            )
        return None

    if assertion_kind in {"postcondition", "field_delta"}:
        if assertion_kind == "field_delta" and not field_ids:
            return (
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                "field_delta_missing_field_operands",
            )
        if assertion_kind == "postcondition":
            has_create = any(
                isinstance(op, dict) and bool(op.get("must_create")) for op in operands
            )
            has_expected = any(
                isinstance(op, dict)
                and (
                    op.get("expected_value") is not None
                    or _text(op.get("field_id") or op.get("field"))
                )
                for op in operands
            )
            if not has_create and not has_expected:
                return (
                    "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                    "postcondition_missing_bound_field_or_expected_value",
                )
        if not field_observers:
            return (
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                f"{assertion_kind}_missing_field_observer",
            )
        if is_write and not cleanup_plan:
            return (
                "FIELD_LEVEL_RULE_NOT_EXECUTABLE",
                f"{assertion_kind}_missing_cleanup",
            )
        return None

    return None
