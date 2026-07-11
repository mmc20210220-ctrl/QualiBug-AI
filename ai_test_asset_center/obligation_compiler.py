"""Compile Behavior IR into industry-agnostic Test Obligations."""
from __future__ import annotations

import re
from typing import Any

from .enterprise_knowledge_center import _lexicon_dict
from .test_obligation import RISK_FAMILIES, dedupe_obligations, make_obligation


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _accepted(nodes: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _text(node.get("status")) in {"conflicting", "unsupported"}:
            continue
        out.append(node)
    return out


def _singular(value: Any) -> str:
    token = _text(value).lower().replace("-", "_")
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 2:
        return token[:-1]
    return token


_RESOURCE_STOP_TOKENS = {"api", "v1", "v2", "v3", "admin", "id", "ids", "by", "me", "self"}
_READ_ACTIONS = {"get", "read", "view", "list", "query", "head", "options"}
_WRITE_ACTIONS = {"post", "create", "submit", "request", "put", "patch", "update", "modify", "adjust", "delete", "remove", "write"}


def _resource_tokens(value: Any) -> set[str]:
    raw = _text(value).lower().replace("-", "_")
    if not raw:
        return set()
    if raw == "*":
        return {"*"}
    tokens: set[str] = set()
    for part in re.split(r"[/\\\s{}:?.#&=,;()\[\]_]+", raw):
        token = _singular(part)
        if token and token not in _RESOURCE_STOP_TOKENS and not token.isdigit():
            tokens.add(token)
    return tokens


def _operation_resource_candidates(op: dict[str, Any]) -> set[str]:
    candidates = _resource_tokens(op.get("path"))
    for value in _list(op.get("entity_refs")):
        candidates.update(_resource_tokens(value))
    for value in _list(op.get("tags")):
        candidates.update(_resource_tokens(value))
    return candidates


def _operation_resource(op: dict[str, Any]) -> str:
    for part in _text(op.get("path")).strip("/").split("/"):
        normalized = _singular(part)
        if normalized and normalized not in _RESOURCE_STOP_TOKENS and not part.startswith(("{", ":")):
            return normalized
    return ""


def _operation_actions(op: dict[str, Any]) -> set[str]:
    method = _text(op.get("method")).upper()
    method_actions = {
        "GET": {"get", "read", "view", "list", "query"},
        "HEAD": {"head", "read"},
        "OPTIONS": {"options", "read"},
        "POST": {"post", "create", "submit", "request", "write"},
        "PUT": {"put", "update", "modify", "write"},
        "PATCH": {"patch", "update", "modify", "adjust", "write"},
        "DELETE": {"delete", "remove", "write"},
    }
    actions = set(method_actions.get(method, {method.lower()} if method else set()))
    parts = [
        _singular(part)
        for part in _text(op.get("path")).strip("/").split("/")
        if part and not part.startswith(("{", ":")) and _singular(part) not in _RESOURCE_STOP_TOKENS
    ]
    if len(parts) > 1:
        actions.add(parts[-1])
    evidence = " ".join(
        [
            _text(op.get("summary")),
            _text(op.get("description")),
            _text(op.get("operation_id")),
            " ".join(_text(tag) for tag in _list(op.get("tags"))),
            " ".join(parts),
        ]
    ).lower()
    for source_token, aliases in _lexicon_dict("verb_action_lexicon").items():
        alias_values = [_text(source_token), *[_text(token) for token in _list(aliases)]]
        if any(token.lower() in evidence for token in alias_values if token):
            actions.update(token.lower() for token in alias_values if token)
    return actions


def _summary_declared_roles(op: dict[str, Any], actors: list[dict[str, Any]]) -> set[str]:
    summary = " ".join(
        [
            _text(op.get("summary")),
            _text(op.get("description")),
            _text(op.get("operation_id")),
            " ".join(_text(tag) for tag in _list(op.get("tags"))),
        ]
    ).lower()
    if not summary.strip():
        return set()
    role_words = _lexicon_dict("role_words")
    declared: set[str] = set()
    for actor in actors:
        role = _text(actor.get("role")).lower()
        if not role:
            continue
        aliases = [role, *role_words.get(role, [])]
        if any(
            re.search(rf"(?<![a-z0-9_]){re.escape(alias.lower())}(?![a-z0-9_])", summary)
            if alias.isascii() else alias.lower() in summary
            for alias in aliases
            if alias
        ):
            declared.add(role)
    return declared


def _actor_is_allowed(actor: dict[str, Any], op: dict[str, Any], direct_roles: set[str]) -> bool:
    role = _text(actor.get("role")).lower()
    if direct_roles:
        return role in direct_roles
    resources: set[str] = set()
    for value in _list(actor.get("allowed_resources")):
        resources.update(_resource_tokens(value))
    actions = {_text(value).lower() for value in _list(actor.get("allowed_actions")) if _text(value)}
    if "*" in resources and ("*" in actions or "manage" in actions):
        return True
    if not resources or not (_operation_resource_candidates(op) & resources):
        return False
    return _action_matches(actions, op)


def _action_matches(actions: set[str], op: dict[str, Any]) -> bool:
    if "*" in actions or "manage" in actions:
        return True
    op_actions = _operation_actions(op)
    if actions & op_actions:
        return True
    method = _text(op.get("method")).upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return bool(actions & _READ_ACTIONS)
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return bool(actions & _WRITE_ACTIONS)
    return False


def _actor_has_specific_permission(actor: dict[str, Any], op: dict[str, Any]) -> bool:
    resources: set[str] = set()
    for value in _list(actor.get("allowed_resources")):
        resources.update(_resource_tokens(value))
    actions = {_text(value).lower() for value in _list(actor.get("allowed_actions")) if _text(value)}
    if not resources or "*" in resources or not actions or "*" in actions:
        return False
    return bool(resources & _operation_resource_candidates(op)) and _action_matches(actions, op)


def _active_actors(actors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked = {"disabled", "locked", "inactive", "suspended"}
    return [
        actor
        for actor in actors
        if _text(actor.get("account_status")).lower() not in blocked
    ]


def _actor_sort_key(actor: dict[str, Any]) -> tuple[int, int, str]:
    role = _text(actor.get("role")).lower()
    secret = _text(actor.get("credential_secret_ref"))
    return (
        0 if secret.startswith("secret_ref:test_accounts:") else 1,
        1 if role in {"admin", "administrator", "superadmin", "root"} else 0,
        _text(actor.get("id")),
    )


def _combined_source_refs(*nodes: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        for ref in _list(_dict(node).get("source_refs")):
            if not isinstance(ref, dict):
                continue
            key = "|".join(_text(ref.get(k)) for k in ("source_id", "locator", "kind", "quote_hash"))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(ref))
            if len(out) >= limit:
                return out
    return out


def compile_obligations_from_behavior_ir(behavior_ir: dict[str, Any]) -> dict[str, Any]:
    """Produce obligations from IR facts using generic property templates.

    Templates bind to IR role/operation/entity IDs only — never name strings
    that encode a specific industry or benchmark answer.
    """
    ir = _dict(behavior_ir)
    operations = _accepted(_list(ir.get("operations")))
    actors = _accepted(_list(ir.get("actors")))
    invariants = _accepted(_list(ir.get("invariants")))
    relations = _accepted(_list(ir.get("relations")))
    states = _accepted(_list(ir.get("states")))
    entities = _accepted(_list(ir.get("entities")))
    obligations: list[dict[str, Any]] = []

    write_ops = [op for op in operations if _text(op.get("read_write") or op.get("side_effect_class")) == "write"]
    read_ops = [op for op in operations if _text(op.get("read_write") or op.get("side_effect_class")) != "write"]

    active_actors = _active_actors(actors)

    # Authorization: source-permitted actor control vs source-denied treatment
    # on the same operation. Never infer allow/deny from actor ordering.
    if len(active_actors) >= 2 and operations:
        for op in operations[:120]:
            direct_roles = _summary_declared_roles(op, active_actors)
            allowed_actors = [
                actor for actor in active_actors
                if _actor_is_allowed(actor, op, direct_roles)
            ]
            specific_allowed = [
                actor for actor in allowed_actors
                if _text(actor.get("role")).lower() in direct_roles or _actor_has_specific_permission(actor, op)
            ]
            if not specific_allowed:
                continue
            allowed_roles = {_text(actor.get("role")).lower() for actor in allowed_actors}
            denied_actors = [
                actor for actor in active_actors
                if _text(actor.get("role")).lower() not in allowed_roles
                and not _actor_is_allowed(actor, op, direct_roles)
            ]
            if not denied_actors:
                continue
            allowed = sorted(specific_allowed, key=_actor_sort_key)[0]
            denied_candidates = [
                actor for actor in denied_actors
                if _text(actor.get("role")).lower() != _text(allowed.get("role")).lower()
            ]
            if not denied_candidates:
                continue
            denied = sorted(denied_candidates, key=_actor_sort_key)[0]
            obligations.append(make_obligation(
                risk_family="authorization",
                subject_refs=[_text(op.get("id")), _text(allowed.get("id")), _text(denied.get("id"))],
                property_spec={
                    "template": "authorization_control_treatment",
                    "control_actor_ref": _text(allowed.get("id")),
                    "treatment_actor_ref": _text(denied.get("id")),
                    "operation_ref": _text(op.get("id")),
                    "require_same_resource": True,
                },
                required_actors=[_text(allowed.get("id")), _text(denied.get("id"))],
                required_operations=[_text(op.get("id"))],
                required_observers=["http_response", "actor_identity"],
                cleanup_requirement={"required": _text(op.get("read_write")) == "write", "mode": "reverse_order"},
                source_refs=_combined_source_refs(op, allowed, denied),
                confidence=min(
                    0.9 if direct_roles else 0.82,
                    float(op.get("confidence") or 0.7),
                    float(allowed.get("confidence") or 0.7),
                    float(denied.get("confidence") or 0.7),
                ),
            ))

    # Isolation: two concrete active accounts with the same role on an owned
    # resource. Without two account-bound actors, leave this to the legacy
    # source-grounded isolation slices instead of compiling an arbitrary pair.
    by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in active_actors:
        if _text(actor.get("account_ref")):
            by_role.setdefault(_text(actor.get("role")).lower(), []).append(actor)
    owned_read_ops = [
        op for op in read_ops
        if ("{" in _text(op.get("path")) or "/:" in _text(op.get("path")))
    ]
    for group in by_role.values():
        if len(group) < 2:
            continue
        owner, viewer = sorted(group, key=_actor_sort_key)[:2]
        op = next((candidate for candidate in owned_read_ops if _actor_is_allowed(owner, candidate, set())), None)
        if not op:
            continue
        obligations.append(make_obligation(
            risk_family="isolation",
            subject_refs=[_text(op.get("id")), _text(owner.get("id")), _text(viewer.get("id"))],
            property_spec={
                "template": "owner_viewer_isolation",
                "owner_actor_ref": _text(owner.get("id")),
                "viewer_actor_ref": _text(viewer.get("id")),
                "operation_ref": _text(op.get("id")),
                "require_ownership_evidence": True,
            },
            required_actors=[_text(owner.get("id")), _text(viewer.get("id"))],
            required_operations=[_text(op.get("id"))],
            required_fixtures=["owned_resource"],
            required_observers=["http_response", "resource_ownership"],
            source_refs=_combined_source_refs(op, owner, viewer),
            confidence=0.7,
        ))

    # State transitions from IR states
    if states and write_ops:
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for st in states:
            by_entity.setdefault(_text(st.get("entity_ref") or "entity"), []).append(st)
        for entity_ref, entity_states in list(by_entity.items())[:10]:
            if len(entity_states) < 2:
                continue
            op = write_ops[0]
            obligations.append(make_obligation(
                risk_family="state",
                subject_refs=[_text(op.get("id")), entity_ref, _text(entity_states[0].get("id")), _text(entity_states[1].get("id"))],
                property_spec={
                    "template": "state_transition",
                    "entity_ref": entity_ref,
                    "from_state_ref": _text(entity_states[0].get("id")),
                    "to_state_ref": _text(entity_states[1].get("id")),
                    "operation_ref": _text(op.get("id")),
                },
                required_operations=[_text(op.get("id"))],
                required_fixtures=[f"entity_in_state:{_text(entity_states[0].get('id'))}"],
                required_observers=["before_state", "after_state"],
                cleanup_requirement={"required": True, "mode": "reverse_order"},
                source_refs=list(entity_states[0].get("source_refs") or [])[:2],
                confidence=0.6,
            ))

    # Idempotency / concurrency for write ops
    for op in write_ops[:20]:
        obligations.append(make_obligation(
            risk_family="idempotency",
            subject_refs=[_text(op.get("id"))],
            property_spec={
                "template": "idempotent_effect_cardinality",
                "operation_ref": _text(op.get("id")),
                "compare": "business_effect_not_http_status",
            },
            required_operations=[_text(op.get("id"))],
            required_observers=["business_effect", "http_response"],
            cleanup_requirement={"required": True, "mode": "reverse_order"},
            source_refs=list(op.get("source_refs") or [])[:2],
            confidence=0.55,
        ))
        obligations.append(make_obligation(
            risk_family="concurrency",
            subject_refs=[_text(op.get("id"))],
            property_spec={
                "template": "concurrent_final_invariant",
                "operation_ref": _text(op.get("id")),
                "insufficient_signal": "dual_2xx_alone",
            },
            required_operations=[_text(op.get("id"))],
            required_observers=["final_state", "barrier_timeline"],
            cleanup_requirement={"required": True, "mode": "reverse_order"},
            source_refs=list(op.get("source_refs") or [])[:2],
            confidence=0.55,
        ))

    # Conservation / privacy / validation from invariants
    for inv in invariants[:30]:
        expr = _dict(inv.get("expression"))
        kind = _text(expr.get("kind") or "business_rule").lower()
        family = "validation"
        if any(token in kind for token in ("conserv", "balance", "amount", "quantity", "库存", "金额")):
            family = "conservation"
        elif any(token in kind for token in ("privacy", "pii", "mask", "隐私")):
            family = "privacy"
        elif any(token in kind for token in ("time", "expir", "temporal", "过期")):
            family = "temporal"
        elif any(token in kind for token in ("visib", "scope", "可见")):
            family = "visibility"
        op_ref = _text(operations[0].get("id")) if operations else ""
        obligations.append(make_obligation(
            risk_family=family if family in RISK_FAMILIES else "validation",
            subject_refs=[_text(inv.get("id")), op_ref] if op_ref else [_text(inv.get("id"))],
            property_spec={
                "template": f"invariant_{family}",
                "invariant_ref": _text(inv.get("id")),
                "expression": expr,
                "operation_ref": op_ref,
            },
            required_operations=[op_ref] if op_ref else [],
            required_observers=["typed_assertion", "source_invariant"],
            source_refs=list(inv.get("source_refs") or [])[:3],
            confidence=float(inv.get("confidence") or 0.6),
        ))

    # Entity+operation validation mutation template (generic)
    if entities and write_ops:
        ent = entities[0]
        op = write_ops[0]
        obligations.append(make_obligation(
            risk_family="validation",
            subject_refs=[_text(ent.get("id")), _text(op.get("id"))],
            property_spec={
                "template": "single_dimension_mutation",
                "entity_ref": _text(ent.get("id")),
                "operation_ref": _text(op.get("id")),
                "require_control_success": True,
            },
            required_operations=[_text(op.get("id"))],
            required_observers=["http_response", "entity_state"],
            cleanup_requirement={"required": True, "mode": "reverse_order"},
            source_refs=list(ent.get("source_refs") or [])[:2],
            confidence=0.5,
        ))

    deduped = dedupe_obligations(obligations)
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "behavior_ir_model_id": _text(ir.get("model_id")),
        "obligation_count": len(deduped),
        "by_family": {
            family: sum(1 for item in deduped if item.get("risk_family") == family)
            for family in RISK_FAMILIES
        },
        "obligations": deduped,
        "coverage_gaps": list(ir.get("coverage_gaps") or []),
    }
