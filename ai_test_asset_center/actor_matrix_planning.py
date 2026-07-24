"""Actor Matrix Planning — generates discriminating actor combinations for
authorization and tenant-isolation experiments.

Single responsibility (SPEC §5):
    Actor Assets + Resource Relation + Operation Requirement
    → Actor Matrix Candidates

This module does NOT:
    - Send HTTP requests
    - Create Fixtures
    - Modify accounts
    - Execute Oracle
    - Generate Findings
    - Read Benchmark data

Fully generic: no project-specific or benchmark-specific logic.
Consumes Behavior IR actors, invariants, relations, and operations.
"""
from __future__ import annotations

import hashlib
from typing import Any

# ─── Helpers ───────────────────────────────────────────────────────────────────


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    return str(v or "").strip()


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "amx_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Actor Relation Types (SPEC §10) ──────────────────────────────────────────

RELATION_SAME_TENANT_OWNER = "SAME_TENANT_OWNER"
RELATION_SAME_TENANT_CREATOR = "SAME_TENANT_CREATOR"
RELATION_SAME_TENANT_ASSIGNEE = "SAME_TENANT_ASSIGNEE"
RELATION_SAME_TENANT_ALLOWED_ROLE = "SAME_TENANT_ALLOWED_ROLE"
RELATION_SAME_TENANT_DENIED_ROLE = "SAME_TENANT_DENIED_ROLE"
RELATION_SAME_TENANT_UNRELATED = "SAME_TENANT_UNRELATED"
RELATION_CROSS_TENANT_SAME_ROLE = "CROSS_TENANT_SAME_ROLE"
RELATION_CROSS_TENANT_DIFFERENT_ROLE = "CROSS_TENANT_DIFFERENT_ROLE"
RELATION_CROSS_TENANT_ADMIN = "CROSS_TENANT_ADMIN"
RELATION_GLOBAL_ADMIN = "GLOBAL_ADMIN"
RELATION_ANONYMOUS = "ANONYMOUS"
RELATION_UNKNOWN = "UNKNOWN_RELATION"

ALL_RELATION_TYPES = frozenset({
    RELATION_SAME_TENANT_OWNER,
    RELATION_SAME_TENANT_CREATOR,
    RELATION_SAME_TENANT_ASSIGNEE,
    RELATION_SAME_TENANT_ALLOWED_ROLE,
    RELATION_SAME_TENANT_DENIED_ROLE,
    RELATION_SAME_TENANT_UNRELATED,
    RELATION_CROSS_TENANT_SAME_ROLE,
    RELATION_CROSS_TENANT_DIFFERENT_ROLE,
    RELATION_CROSS_TENANT_ADMIN,
    RELATION_GLOBAL_ADMIN,
    RELATION_ANONYMOUS,
    RELATION_UNKNOWN,
})

# Admin-like role keywords (generic, no project hardcoding)
_ADMIN_ROLE_KEYWORDS = frozenset({
    "admin", "administrator", "superuser", "root", "sysadmin",
    "super_admin", "platform_admin", "global_admin",
})


# ─── Actor Inventory (SPEC §7) ────────────────────────────────────────────────


def build_actor_inventory(
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build standardized actor inventory from Behavior IR actors.

    Each actor is normalized to include: actor_id, role, tenant, credential_ref,
    active status, and capability evidence.

    Returns list of standardized actor dicts.
    """
    ir = _dict(behavior_ir)
    raw_actors = _list(ir.get("actors"))
    inventory: list[dict[str, Any]] = []

    for raw in raw_actors:
        if not isinstance(raw, dict):
            continue
        actor_id = _text(raw.get("id"))
        role = _text(raw.get("role") or raw.get("role_key"))
        if not actor_id or not role:
            continue

        # Skip template/placeholder actors
        secret = _text(raw.get("credential_secret_ref") or raw.get("secret_ref"))
        if not role or role.startswith("{") or role.startswith(":"):
            continue
        if not secret or secret.startswith("{") or ":{" in secret:
            continue

        tenant = _text(
            raw.get("tenant") or raw.get("tenant_id")
            or raw.get("tenant_scope") or raw.get("org")
            or raw.get("scope")
        )
        # Normalize "unspecified" tenant
        if tenant.lower() in {"unspecified", "unknown", ""}:
            tenant = ""

        inventory.append({
            "actor_id": actor_id,
            "role": role,
            "role_key": role.lower(),
            "tenant": tenant,
            "credential_ref": secret,
            "account_ref": _text(raw.get("account_ref") or raw.get("account_id")),
            "active": _text(raw.get("status") or raw.get("account_status")).lower() in {"active", ""},
            "is_admin": role.lower() in _ADMIN_ROLE_KEYWORDS,
            "allowed_actions": _list(raw.get("allowed_actions")),
            "denied_actions": _list(raw.get("denied_actions")),
            "source_evidence": _list(raw.get("source_refs")),
        })

    return inventory


# ─── Resource Context (SPEC §8) ───────────────────────────────────────────────


def resolve_resource_context(
    expression: Any,
    invariant: dict[str, Any],
    behavior_ir: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any]:
    """Resolve resource context: tenant, owner, creator, assignee.

    Priority: Fixture Receipt > Create Operation Response > Root Observer >
    Related Observer > Entity Relation Graph > Document inference.

    Returns resource_context dict with available evidence.
    """
    expr = _dict(expression) if isinstance(expression, dict) else {}
    inv = _dict(invariant)
    ir = _dict(behavior_ir)

    # Extract entity reference
    entity_ref = _text(
        inv.get("entity_ref") or expr.get("entity_ref")
        or expr.get("entity") or expr.get("resource_type")
    )

    # Extract ownership field from expression or relations
    owner_field = _text(
        expr.get("owner_field") or expr.get("ownership_field")
        or expr.get("owner")
    )
    tenant_field = _text(
        expr.get("tenant_field") or expr.get("scope_field")
        or expr.get("tenant")
    )
    creator_field = _text(
        expr.get("creator_field") or expr.get("created_by")
        or expr.get("creator")
    )
    assignee_field = _text(
        expr.get("assignee_field") or expr.get("assigned_to")
        or expr.get("assignee")
    )

    # Infer ownership field from rule semantics
    rule_text = _text(
        inv.get("description") or expr.get("description")
        or expr.get("rule") or expr.get("expression")
    ).lower()

    if not owner_field:
        # Generic inference from rule text
        for candidate in ("customer_id", "owner_id", "user_id", "created_by",
                          "uploaded_by", "author_id", "creator_id"):
            if candidate in rule_text:
                owner_field = candidate
                break
        # Check entity relations in IR
        if not owner_field:
            for rel in _list(ir.get("relations")):
                if not isinstance(rel, dict):
                    continue
                rel_type = _text(rel.get("relation_type")).lower()
                if rel_type in {"belongs_to", "owned_by", "ownership"}:
                    from_ref = _text(rel.get("from_ref")).lower()
                    if entity_ref and entity_ref.lower() in from_ref:
                        owner_field = _text(rel.get("field") or rel.get("via_field"))
                        break

    if not tenant_field:
        for candidate in ("tenant", "tenant_id", "org_id", "scope", "organization_id"):
            if candidate in rule_text:
                tenant_field = candidate
                break

    # Determine required relations from rule semantics
    owner_required = bool(
        owner_field
        or "owner" in rule_text
        or "creator" in rule_text
        or "uploaded_by" in rule_text
        or "belongs_to" in rule_text
        or expr.get("owner_required")
    )
    same_tenant_required = bool(
        tenant_field
        or "tenant" in rule_text
        or "isolation" in rule_text
        or "cross_tenant" in rule_text
        or "scope" in rule_text
        or expr.get("same_tenant_required")
    )

    return {
        "entity_ref": entity_ref,
        "owner_field": owner_field,
        "tenant_field": tenant_field,
        "creator_field": creator_field,
        "assignee_field": assignee_field,
        "owner_required": owner_required,
        "same_tenant_required": same_tenant_required,
        "rule_text": rule_text,
        "source_evidence": _list(inv.get("source_refs")) + _list(expr.get("source_refs")),
    }


# ─── Operation Authorization Requirement (SPEC §9) ────────────────────────────


def extract_operation_authorization_requirement(
    expression: Any,
    invariant: dict[str, Any],
    resource_context: dict[str, Any],
) -> dict[str, Any]:
    """Extract operation authorization requirement from rule and expression.

    Returns structured requirement: allowed_roles, denied_roles,
    owner_required, same_tenant_required, etc.
    """
    expr = _dict(expression) if isinstance(expression, dict) else {}
    inv = _dict(invariant)

    # Extract allowed/denied roles from expression
    allowed_roles: list[str] = []
    denied_roles: list[str] = []

    # From explicit expression fields
    for key in ("authorized_role", "required_role", "allowed_role", "role"):
        role = _text(expr.get(key))
        if role and role not in allowed_roles:
            allowed_roles.append(role)

    for key in ("denied_role", "forbidden_role", "unauthorized_role"):
        role = _text(expr.get(key))
        if role and role not in denied_roles:
            denied_roles.append(role)

    # From allowed_roles list
    for role in _list(expr.get("allowed_roles")):
        r = _text(role)
        if r and r not in allowed_roles:
            allowed_roles.append(r)

    # Determine requirement types
    rc = _dict(resource_context)
    owner_required = bool(rc.get("owner_required"))
    creator_required = bool(rc.get("creator_field"))
    assignee_required = bool(rc.get("assignee_field"))
    same_tenant_required = bool(rc.get("same_tenant_required"))
    cross_tenant_forbidden = same_tenant_required  # If same-tenant required, cross is forbidden

    # Confidence assessment
    confidence = 0.5
    if allowed_roles:
        confidence += 0.2
    if owner_required or same_tenant_required:
        confidence += 0.2
    if rc.get("owner_field") or rc.get("tenant_field"):
        confidence += 0.1
    confidence = min(confidence, 1.0)

    return {
        "allowed_roles": allowed_roles,
        "denied_roles": denied_roles,
        "owner_required": owner_required,
        "creator_required": creator_required,
        "assignee_required": assignee_required,
        "same_tenant_required": same_tenant_required,
        "cross_tenant_forbidden": cross_tenant_forbidden,
        "source_evidence": _list(inv.get("source_refs")),
        "confidence": confidence,
        "complete": bool(allowed_roles or owner_required or same_tenant_required),
    }


# ─── Actor Relation Classification (SPEC §10) ─────────────────────────────────


def classify_actor_relation(
    actor: dict[str, Any],
    resource_context: dict[str, Any],
    auth_requirement: dict[str, Any],
    resource_tenant: str = "",
    resource_owner_actor_id: str = "",
    resource_creator_actor_id: str = "",
    control_actor_role_key: str = "",
) -> str:
    """Classify an actor's relation to the target resource.

    Priority: CROSS_TENANT > OWNER/CREATOR/ASSIGNEE > ALLOWED/DENIED ROLE > UNRELATED
    """
    actor_tenant = _text(actor.get("tenant"))
    actor_role_key = _text(actor.get("role_key"))
    actor_id = _text(actor.get("actor_id"))
    is_admin = bool(actor.get("is_admin"))

    # Determine resource tenant
    res_tenant = resource_tenant or _text(_dict(resource_context).get("tenant"))

    # Cross-tenant checks (highest priority)
    if res_tenant and actor_tenant and actor_tenant != res_tenant:
        if is_admin:
            return RELATION_CROSS_TENANT_ADMIN
        # Same role as control actor (for discriminating tenant isolation)
        if control_actor_role_key and actor_role_key == control_actor_role_key:
            return RELATION_CROSS_TENANT_SAME_ROLE
        # Check if role is in allowed list
        allowed = _list(_dict(auth_requirement).get("allowed_roles"))
        if allowed and actor_role_key in [r.lower() for r in allowed]:
            return RELATION_CROSS_TENANT_SAME_ROLE
        return RELATION_CROSS_TENANT_DIFFERENT_ROLE

    # Same-tenant checks
    # Owner check
    if resource_owner_actor_id and actor_id == resource_owner_actor_id:
        return RELATION_SAME_TENANT_OWNER

    # Creator check
    if resource_creator_actor_id and actor_id == resource_creator_actor_id:
        return RELATION_SAME_TENANT_CREATOR

    # Role-based checks
    allowed = _list(_dict(auth_requirement).get("allowed_roles"))
    denied = _list(_dict(auth_requirement).get("denied_roles"))
    allowed_lower = [r.lower() for r in allowed]
    denied_lower = [r.lower() for r in denied]

    if allowed_lower and actor_role_key in allowed_lower:
        return RELATION_SAME_TENANT_ALLOWED_ROLE
    if denied_lower and actor_role_key in denied_lower:
        return RELATION_SAME_TENANT_DENIED_ROLE

    # If owner is required but this actor is not the owner
    if _dict(auth_requirement).get("owner_required") and resource_owner_actor_id:
        if actor_id != resource_owner_actor_id:
            # Same tenant, allowed role, but not owner
            if allowed_lower and actor_role_key in allowed_lower:
                return RELATION_SAME_TENANT_ALLOWED_ROLE
            return RELATION_SAME_TENANT_UNRELATED

    # If no allowed_roles defined but actor has same role as control, treat as allowed
    if control_actor_role_key and actor_role_key == control_actor_role_key:
        return RELATION_SAME_TENANT_ALLOWED_ROLE

    if is_admin:
        return RELATION_GLOBAL_ADMIN

    return RELATION_SAME_TENANT_UNRELATED


# ─── Actor Matrix Candidate Generation (SPEC §11) ─────────────────────────────


def generate_actor_matrix_candidates(
    actor_inventory: list[dict[str, Any]],
    resource_context: dict[str, Any],
    auth_requirement: dict[str, Any],
    *,
    resource_tenant: str = "",
    resource_owner_actor_id: str = "",
    resource_creator_actor_id: str = "",
    max_candidates_per_rule: int = 8,
) -> list[dict[str, Any]]:
    """Generate actor matrix candidates based on relation dimensions.

    Does NOT do cartesian product of all accounts. Instead, selects
    discriminating candidates per relation dimension.

    Returns list of candidate dicts with relation classification and score.
    """
    rc = _dict(resource_context)
    ar = _dict(auth_requirement)
    res_tenant = resource_tenant or _text(rc.get("tenant"))

    # Determine control actor role for same-role comparison
    # Pick the first same-tenant non-admin actor as reference control role
    control_role_key = ""
    for actor in actor_inventory:
        if not _dict(actor).get("active", True):
            continue
        a_tenant = _text(actor.get("tenant"))
        if res_tenant and a_tenant == res_tenant and not actor.get("is_admin"):
            control_role_key = _text(actor.get("role_key"))
            break
    if not control_role_key and actor_inventory:
        control_role_key = _text(actor_inventory[0].get("role_key"))

    candidates: list[dict[str, Any]] = []
    seen_relations: set[str] = set()

    for actor in actor_inventory:
        if not _dict(actor).get("active", True):
            continue

        relation = classify_actor_relation(
            actor, rc, ar,
            resource_tenant=res_tenant,
            resource_owner_actor_id=resource_owner_actor_id,
            resource_creator_actor_id=resource_creator_actor_id,
            control_actor_role_key=control_role_key,
        )

        if relation == RELATION_UNKNOWN:
            continue

        # Determine expected authorization
        expected_authorized = _compute_expected_authorized(relation, ar)

        # Score the candidate (SPEC §14)
        score = _score_candidate(actor, relation, ar, rc)

        candidate = {
            "actor_id": _text(actor.get("actor_id")),
            "role": _text(actor.get("role")),
            "role_key": _text(actor.get("role_key")),
            "tenant": _text(actor.get("tenant")),
            "credential_ref": _text(actor.get("credential_ref")),
            "relation_type": relation,
            "expected_authorized": expected_authorized,
            "score": score,
            "is_admin": bool(actor.get("is_admin")),
        }
        candidates.append(candidate)

    # Sort by score descending
    candidates.sort(key=lambda c: c.get("score", 0), reverse=True)

    # Apply budget: prefer diversity of relation types
    selected: list[dict[str, Any]] = []
    for cand in candidates:
        if len(selected) >= max_candidates_per_rule:
            break
        rel = _text(cand.get("relation_type"))
        # Always include first of each relation type; limit duplicates
        rel_count = sum(1 for s in selected if s.get("relation_type") == rel)
        if rel_count >= 2:
            continue
        selected.append(cand)
        seen_relations.add(rel)

    return selected


def _compute_expected_authorized(
    relation: str,
    auth_requirement: dict[str, Any],
) -> bool:
    """Determine if this relation type should be authorized."""
    ar = _dict(auth_requirement)

    # Always authorized
    if relation in {RELATION_SAME_TENANT_OWNER, RELATION_SAME_TENANT_CREATOR,
                    RELATION_SAME_TENANT_ASSIGNEE, RELATION_SAME_TENANT_ALLOWED_ROLE}:
        return True

    # Always denied
    if relation in {RELATION_CROSS_TENANT_SAME_ROLE, RELATION_CROSS_TENANT_DIFFERENT_ROLE,
                    RELATION_CROSS_TENANT_ADMIN, RELATION_SAME_TENANT_DENIED_ROLE}:
        return False

    # Context-dependent
    if relation == RELATION_GLOBAL_ADMIN:
        # Admin may or may not be authorized depending on rule
        return not ar.get("owner_required", False)

    if relation == RELATION_SAME_TENANT_UNRELATED:
        return not ar.get("owner_required", False)

    return False


def _score_candidate(
    actor: dict[str, Any],
    relation: str,
    auth_requirement: dict[str, Any],
    resource_context: dict[str, Any],
) -> float:
    """Score actor candidate (SPEC §14 weights)."""
    score = 0.0

    # relation_match (30%): how well this relation matches what we need
    ar = _dict(auth_requirement)
    if ar.get("owner_required") and relation in {RELATION_SAME_TENANT_OWNER, RELATION_SAME_TENANT_UNRELATED}:
        score += 30.0
    elif ar.get("same_tenant_required") and "CROSS_TENANT" in relation:
        score += 30.0
    elif relation in {RELATION_SAME_TENANT_ALLOWED_ROLE, RELATION_SAME_TENANT_DENIED_ROLE}:
        score += 25.0
    else:
        score += 15.0

    # dimension_isolation (25%): single-variable discrimination quality
    if relation in {RELATION_CROSS_TENANT_SAME_ROLE, RELATION_SAME_TENANT_OWNER}:
        score += 25.0  # Best isolation
    elif relation in {RELATION_SAME_TENANT_DENIED_ROLE, RELATION_SAME_TENANT_UNRELATED}:
        score += 20.0
    else:
        score += 10.0

    # credential_validity (15%): has valid credential
    if _text(actor.get("credential_ref")):
        score += 15.0

    # operation_relevance (10%): role mentioned in requirement
    allowed = [r.lower() for r in _list(ar.get("allowed_roles"))]
    if _text(actor.get("role_key")) in allowed:
        score += 10.0
    elif allowed:
        score += 5.0

    # resource_scope_confidence (10%)
    rc = _dict(resource_context)
    if rc.get("owner_field") or rc.get("tenant_field"):
        score += 10.0
    else:
        score += 5.0

    # historical_execution_success (5%) — assume new, neutral
    score += 2.5

    # risk_cost_inverse (5%): prefer non-admin for lower risk
    if not actor.get("is_admin"):
        score += 5.0
    else:
        score += 2.0

    return round(score, 1)


# ─── Discriminating Pair Selection (SPEC §13) ─────────────────────────────────


def select_discriminating_pairs(
    candidates: list[dict[str, Any]],
    auth_requirement: dict[str, Any],
    resource_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select discriminating actor pairs for experiments.

    Each pair changes ONE primary dimension while holding others constant.
    Returns list of pair dicts.
    """
    ar = _dict(auth_requirement)
    rc = _dict(resource_context)
    pairs: list[dict[str, Any]] = []

    # Find control actor (authorized, highest score)
    control_candidates = [c for c in candidates if c.get("expected_authorized")]
    violation_candidates = [c for c in candidates if not c.get("expected_authorized")]

    if not control_candidates:
        return []

    control = control_candidates[0]  # Highest scored authorized

    # Strategy 1: Same role, different tenant (tenant isolation)
    if ar.get("same_tenant_required") or ar.get("cross_tenant_forbidden"):
        cross_tenant_same_role = [
            v for v in violation_candidates
            if v.get("relation_type") == RELATION_CROSS_TENANT_SAME_ROLE
        ]
        if cross_tenant_same_role:
            pairs.append(_build_pair(
                control, cross_tenant_same_role[0],
                dimension_under_test="tenant",
                held_constant=["role", "ownership"],
                changed=["tenant"],
            ))
        else:
            # Fallback: any cross-tenant
            cross_tenant = [
                v for v in violation_candidates
                if "CROSS_TENANT" in _text(v.get("relation_type"))
            ]
            if cross_tenant:
                pairs.append(_build_pair(
                    control, cross_tenant[0],
                    dimension_under_test="tenant",
                    held_constant=["role"] if cross_tenant[0].get("role_key") == control.get("role_key") else [],
                    changed=["tenant", "role"] if cross_tenant[0].get("role_key") != control.get("role_key") else ["tenant"],
                ))

    # Strategy 2: Same tenant, denied role (role authorization)
    denied_role = [
        v for v in violation_candidates
        if v.get("relation_type") == RELATION_SAME_TENANT_DENIED_ROLE
    ]
    if denied_role:
        pairs.append(_build_pair(
            control, denied_role[0],
            dimension_under_test="role",
            held_constant=["tenant", "ownership"],
            changed=["role"],
        ))

    # Strategy 3: Same tenant, same role, non-owner (ownership)
    if ar.get("owner_required"):
        non_owner = [
            v for v in violation_candidates
            if v.get("relation_type") in {RELATION_SAME_TENANT_UNRELATED, RELATION_SAME_TENANT_ALLOWED_ROLE}
            and v.get("tenant") == control.get("tenant")
        ]
        if non_owner:
            pairs.append(_build_pair(
                control, non_owner[0],
                dimension_under_test="ownership",
                held_constant=["tenant", "role"],
                changed=["ownership"],
            ))

    # Strategy 4: Cross-tenant admin (privilege escalation)
    cross_admin = [
        v for v in violation_candidates
        if v.get("relation_type") == RELATION_CROSS_TENANT_ADMIN
    ]
    if cross_admin:
        pairs.append(_build_pair(
            control, cross_admin[0],
            dimension_under_test="tenant_admin",
            held_constant=[],
            changed=["tenant", "role"],
        ))

    # Ensure at least one pair exists
    if not pairs and violation_candidates:
        pairs.append(_build_pair(
            control, violation_candidates[0],
            dimension_under_test="general",
            held_constant=[],
            changed=["actor_relation"],
        ))

    return pairs


def _build_pair(
    control: dict[str, Any],
    violation: dict[str, Any],
    *,
    dimension_under_test: str,
    held_constant: list[str],
    changed: list[str],
) -> dict[str, Any]:
    """Build a discriminating pair dict."""
    discrimination_quality = "HIGH" if len(changed) == 1 else "MEDIUM"
    return {
        "pair_id": _stable_id("pair", _text(control.get("actor_id")),
                              _text(violation.get("actor_id")), dimension_under_test),
        "dimension_under_test": dimension_under_test,
        "control_actor": control,
        "violation_actor": violation,
        "held_constant_dimensions": held_constant,
        "changed_dimensions": changed,
        "discrimination_quality": discrimination_quality,
    }


# ─── Actor Relation Proof (SPEC §17) ──────────────────────────────────────────


def build_actor_relation_proof(
    actor: dict[str, Any],
    resource_context: dict[str, Any],
    relation_type: str,
    expected_authorized: bool,
    *,
    experiment_id: str = "",
    resource_tenant: str = "",
    resource_owner: str = "",
    resource_creator: str = "",
) -> dict[str, Any]:
    """Build actor relation proof for experiment execution gate."""
    rc = _dict(resource_context)
    proof_content = (
        f"{_text(actor.get('actor_id'))}|{_text(actor.get('role'))}|"
        f"{_text(actor.get('tenant'))}|{resource_tenant}|"
        f"{resource_owner}|{resource_creator}|{relation_type}|{expected_authorized}"
    )
    proof_hash = hashlib.sha256(proof_content.encode()).hexdigest()[:24]

    # Relation completeness check
    relation_complete = bool(
        _text(actor.get("actor_id"))
        and _text(actor.get("role"))
        and relation_type != RELATION_UNKNOWN
    )

    return {
        "proof_id": _stable_id("proof", _text(actor.get("actor_id")), experiment_id),
        "experiment_id": experiment_id,
        "actor_id": _text(actor.get("actor_id")),
        "actor_roles": [_text(actor.get("role"))],
        "actor_tenant": _text(actor.get("tenant")),
        "resource_tenant": resource_tenant,
        "resource_owner": resource_owner,
        "resource_creator": resource_creator,
        "role_relation": relation_type,
        "tenant_relation": "same" if _text(actor.get("tenant")) == resource_tenant else "cross",
        "ownership_relation": "owner" if relation_type == RELATION_SAME_TENANT_OWNER else "non_owner",
        "expected_authorized": expected_authorized,
        "source_evidence_ids": _list(actor.get("source_evidence")),
        "relation_complete": relation_complete,
        "proof_hash": proof_hash,
    }


# ─── Main Entry: Generate Actor Matrix for a Rule ─────────────────────────────


def plan_actor_matrix(
    expression: Any,
    invariant: dict[str, Any],
    behavior_ir: dict[str, Any],
    operation: dict[str, Any],
    *,
    resource_tenant: str = "",
    resource_owner_actor_id: str = "",
    resource_creator_actor_id: str = "",
    max_candidates: int = 8,
) -> dict[str, Any]:
    """Main entry point: generate complete actor matrix for one rule/operation.

    Returns:
        {
            "actor_inventory": [...],
            "resource_context": {...},
            "auth_requirement": {...},
            "candidates": [...],
            "discriminating_pairs": [...],
            "proofs": [...],
            "status": "COMPLETE" | "BLOCKED",
            "block_reason": "",
        }
    """
    # Step 1: Build actor inventory
    inventory = build_actor_inventory(behavior_ir)
    if not inventory:
        return {
            "actor_inventory": [],
            "resource_context": {},
            "auth_requirement": {},
            "candidates": [],
            "discriminating_pairs": [],
            "proofs": [],
            "status": "BLOCKED",
            "block_reason": "ACTOR_INVENTORY_EMPTY",
        }

    # Step 2: Resolve resource context
    rc = resolve_resource_context(expression, invariant, behavior_ir, operation)

    # Infer resource_tenant from inventory if not provided
    if not resource_tenant:
        # Use the most common non-empty tenant among actors
        tenant_counts: dict[str, int] = {}
        for a in inventory:
            t = _text(a.get("tenant"))
            if t:
                tenant_counts[t] = tenant_counts.get(t, 0) + 1
        if tenant_counts:
            resource_tenant = max(tenant_counts, key=tenant_counts.get)  # type: ignore[arg-type]

    # Infer resource_owner from inventory if owner_required but no owner specified
    if not resource_owner_actor_id and rc.get("owner_required"):
        # Pick first same-tenant non-admin actor as potential owner
        for a in inventory:
            if (a.get("active", True)
                    and _text(a.get("tenant")) == resource_tenant
                    and not a.get("is_admin")):
                resource_owner_actor_id = _text(a.get("actor_id"))
                break

    # Step 3: Extract authorization requirement
    ar = extract_operation_authorization_requirement(expression, invariant, rc)
    if not ar.get("complete"):
        return {
            "actor_inventory": inventory,
            "resource_context": rc,
            "auth_requirement": ar,
            "candidates": [],
            "discriminating_pairs": [],
            "proofs": [],
            "status": "BLOCKED",
            "block_reason": "ACTOR_AUTHORIZATION_REQUIREMENT_INCOMPLETE",
        }

    # Step 4: Generate matrix candidates
    candidates = generate_actor_matrix_candidates(
        inventory, rc, ar,
        resource_tenant=resource_tenant,
        resource_owner_actor_id=resource_owner_actor_id,
        resource_creator_actor_id=resource_creator_actor_id,
        max_candidates_per_rule=max_candidates,
    )
    if not candidates:
        return {
            "actor_inventory": inventory,
            "resource_context": rc,
            "auth_requirement": ar,
            "candidates": [],
            "discriminating_pairs": [],
            "proofs": [],
            "status": "BLOCKED",
            "block_reason": "ACTOR_MATRIX_EMPTY",
        }

    # Step 5: Select discriminating pairs
    pairs = select_discriminating_pairs(candidates, ar, rc)
    if not pairs:
        return {
            "actor_inventory": inventory,
            "resource_context": rc,
            "auth_requirement": ar,
            "candidates": candidates,
            "discriminating_pairs": [],
            "proofs": [],
            "status": "BLOCKED",
            "block_reason": "ACTOR_MATRIX_NOT_DISCRIMINATING",
        }

    # Step 6: Build relation proofs for each pair
    proofs: list[dict[str, Any]] = []
    for pair in pairs:
        ctrl = _dict(pair.get("control_actor"))
        viol = _dict(pair.get("violation_actor"))
        proofs.append(build_actor_relation_proof(
            ctrl, rc, _text(ctrl.get("relation_type")),
            True,
            resource_tenant=resource_tenant,
            resource_owner=resource_owner_actor_id,
            resource_creator=resource_creator_actor_id,
        ))
        proofs.append(build_actor_relation_proof(
            viol, rc, _text(viol.get("relation_type")),
            False,
            resource_tenant=resource_tenant,
            resource_owner=resource_owner_actor_id,
            resource_creator=resource_creator_actor_id,
        ))

    return {
        "actor_inventory": inventory,
        "resource_context": rc,
        "auth_requirement": ar,
        "candidates": candidates,
        "discriminating_pairs": pairs,
        "proofs": proofs,
        "status": "COMPLETE",
        "block_reason": "",
    }
