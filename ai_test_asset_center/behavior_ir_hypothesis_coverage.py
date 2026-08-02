"""Behavior IR → Hypothesis coverage gap analysis.

Produces source-backed hypotheses for Behavior IR nodes that have zero
coverage from the LLM reasoner engines. Every generated hypothesis carries
explicit ``source_refs`` traced back to the originating Behavior IR node.

Schema: qualibug.behavior-ir-hypothesis-coverage.v1

This module is industry-neutral. It must never reference benchmark bug IDs,
match keywords, hidden ground truth, customer names, or fixed API paths.
"""

from __future__ import annotations

import collections
import hashlib
from typing import Any

# ── Re-use Behavior IR constants ──
try:
    from .behavior_ir import _source_ref, _stable_id
except ImportError:
    def _stable_id(*parts: Any) -> str:
        raw = "|".join(str(p or "").strip() for p in parts if str(p or "").strip())
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"bir_{digest}"

    def _source_ref(source_id: str = "", *, version: str = "", locator: str = "", quote: str = "", kind: str = "") -> dict[str, Any]:
        quote_text = str(quote or "").strip()
        return {
            "source_id": str(source_id or "").strip() or "unknown",
            "version": str(version or "").strip(),
            "locator": str(locator or "").strip(),
            "kind": str(kind or "").strip(),
            "quote_hash": hashlib.sha256(quote_text.encode("utf-8")).hexdigest()[:16] if quote_text else "",
        }

try:
    from .behavior_ir_core import _infer_operation_effect
except ImportError:
    def _infer_operation_effect(operation: dict[str, Any], method: str) -> str:
        declared = str(
            (operation or {}).get("read_write")
            or (operation or {}).get("side_effect_class")
            or ""
        ).strip().lower()
        if declared in {"read", "write"}:
            return declared
        return "write" if str(method or "").upper() in {"POST", "PUT", "PATCH", "DELETE"} else "read"


COVERAGE_SCHEMA = "qualibug.behavior-ir-hypothesis-coverage.v1"

# Maximum additional hypotheses generated per coverage run.
# Raised to 500 to support comprehensive coverage on large systems.
MAX_COVERAGE_HYPOTHESES = 500

# Risk families derived from Behavior IR structure — these are the families
# that can be verified from Behavior IR facts alone, without hidden GT.
_COVERAGE_RISK_FAMILIES = frozenset({
    "authorization",
    "visibility",
    "isolation",
    "consistency",
    "state_integrity",
    "lifecycle",
    "invariant",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _postcondition_has_bound_effect(expression: dict[str, Any]) -> bool:
    """Return whether a postcondition names an effect an observer can verify."""

    for operand in _list(_dict(expression).get("operands")):
        if not isinstance(operand, dict):
            continue
        if _text(operand.get("field_id") or operand.get("field")):
            return True
        expected = operand.get("expected_value")
        if expected is not None and (
            not isinstance(expected, str) or bool(expected.strip())
        ):
            return True
        if bool(operand.get("must_create")):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Coverage Map: extract verifiable surface from Behavior IR
# ═══════════════════════════════════════════════════════════════════


def build_behavior_ir_coverage_map(behavior_ir: dict[str, Any]) -> dict[str, Any]:
    """Extract the verifiable surface from a Behavior IR v2 model.

    Returns a dict with:
    - ``nodes``: list of coverage nodes, each with a stable ``coverage_id``,
      ``node_type`` (operation/entity/actor/invariant/relation),
      ``ir_node_id``, ``risk_families`` (list of applicable risk families),
      ``source_refs``, and ``coverage_signature``.
    - ``node_count``: total coverage nodes.
    - ``risk_family_counts``: per-family node count.
    """

    nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    coverage_gaps: list[dict[str, Any]] = []

    operations = _list(behavior_ir.get("operations"))
    entities = _list(behavior_ir.get("entities"))
    actors = _list(behavior_ir.get("actors"))
    invariants = _list(behavior_ir.get("invariants"))
    relations = _list(behavior_ir.get("relations"))

    entity_map: dict[str, dict[str, Any]] = {
        _text(e.get("id")): e for e in entities if _text(e.get("id"))
    }
    actor_map: dict[str, dict[str, Any]] = {
        _text(a.get("id")): a for a in actors if _text(a.get("id"))
    }

    # An operation's existence and HTTP effect do not prove an authorization,
    # lifecycle, consistency, isolation, or invariant contract.  The previous
    # method-only expansion treated every write as all of those risk families
    # and every read as visibility, which created executable obligations from a
    # route shape alone.  That is not source-backed coverage; it is a blanket
    # business-rule claim and it consumes the same planning budget as real
    # source-bound rules.  Operation-specific coverage is represented below by
    # exact actor-operation, invariant, relation, and state nodes instead.

    # ── Actor × Operation coverage nodes ──
    active_actors = [a for a in actors if a.get("runtime_bound") is True]
    for actor in active_actors:
        actor_id = _text(actor.get("id"))
        actor_role = _text(actor.get("role"))
        allowed_actions = _list(actor.get("allowed_actions"))
        denied_actions = _list(actor.get("denied_actions"))
        source_refs = _list(actor.get("source_refs"))

        for op in operations:
            op_id = _text(op.get("id"))
            op_path = _text(op.get("path"))
            op_method = _text(op.get("method")).upper()
            if not op_id:
                continue

            # Authorization coverage: actor × write operation
            if _infer_operation_effect(op, op_method) == "write":
                coverage_id = f"cov_auth_{actor_id}_{op_id}"
                if coverage_id in seen_ids:
                    continue
                seen_ids.add(coverage_id)
                nodes.append({
                    "coverage_id": coverage_id,
                    "node_type": "actor_operation",
                    "ir_node_id": f"{actor_id}|{op_id}",
                    "risk_family": "authorization",
                    "actor_id": actor_id,
                    "actor_role": actor_role,
                    "operation_id": op_id,
                    "operation_path": op_path,
                    "operation_method": op_method,
                    "allowed_actions": allowed_actions,
                    "denied_actions": denied_actions,
                    "source_refs": source_refs + _list(op.get("source_refs")),
                    "coverage_signature": _coverage_signature(
                        f"auth_{actor_id}", op_id, "authorization", op_path
                    ),
                })

    # ── Invariant coverage nodes ──
    for inv in invariants:
        inv_id = _text(inv.get("id"))
        if not inv_id:
            continue
        description = _text(inv.get("description"))
        expression = _dict(inv.get("expression"))
        invariant_kind = _text(
            inv.get("invariant_kind")
            or inv.get("kind")
            or expression.get("kind")
        ).lower()
        operation_refs = [ref for ref in _list(inv.get("operation_refs")) if _text(ref)]
        source_refs = _list(inv.get("source_refs"))

        if invariant_kind == "postcondition" and not _postcondition_has_bound_effect(expression):
            coverage_gaps.append({
                "code": "SOURCE_POSTCONDITION_EFFECT_UNBOUND",
                "subject_ref": inv_id,
                "description": (
                    "Source postcondition has no concrete field or create effect "
                    "that an observer can verify"
                ),
                "source_refs": source_refs,
            })
            continue

        coverage_id = f"cov_inv_{inv_id}"
        if coverage_id in seen_ids:
            continue
        seen_ids.add(coverage_id)
        nodes.append({
            "coverage_id": coverage_id,
            "node_type": "invariant",
            "ir_node_id": inv_id,
            "risk_family": "invariant",
            "invariant_description": description[:300],
            "operation_refs": operation_refs,
            "source_refs": source_refs,
            "coverage_signature": _coverage_signature(inv_id, "invariant", "", description[:120]),
        })

    # ── Relation coverage nodes (state transitions, conservation, ownership) ──
    state_ids = {
        _text(state.get("id"))
        for state in _list(behavior_ir.get("states"))
        if isinstance(state, dict) and _text(state.get("id"))
    }
    for rel in relations:
        rel_id = _text(rel.get("id"))
        rel_type = _text(rel.get("relation_type"))
        if not rel_id or not rel_type:
            continue

        # ``_derive_operation_entity_relations`` uses the existing IR relation
        # vocabulary for an operation updating an entity.  Its ``transitions``
        # label is not a state-machine edge.  Only a relation whose endpoints
        # are both concrete state nodes can create state-integrity coverage.
        # This keeps method/entity structure from becoming a lifecycle claim.
        if rel_type == "transitions" and not (
            _text(rel.get("from_ref")) in state_ids
            and _text(rel.get("to_ref")) in state_ids
        ):
            continue
        source_refs = _list(rel.get("source_refs"))

        # Map relation type to risk family
        family_map = {
            "transitions": "state_integrity",
            "conserves": "consistency",
            "owns": "isolation",
            "scopes": "visibility",
            "permits": "authorization",
            "denies": "authorization",
        }
        family = family_map.get(rel_type)
        if not family:
            continue

        coverage_id = f"cov_rel_{rel_id}"
        if coverage_id in seen_ids:
            continue
        seen_ids.add(coverage_id)
        nodes.append({
            "coverage_id": coverage_id,
            "node_type": "relation",
            "ir_node_id": rel_id,
            "risk_family": family,
            "relation_type": rel_type,
            "from_ref": _text(rel.get("from_ref")),
            "to_ref": _text(rel.get("to_ref")),
            "operation_ref": _text(rel.get("operation_ref")),
            "source_refs": source_refs,
            "coverage_signature": _coverage_signature(rel_id, family, rel_type, ""),
        })

    # ── Entity state coverage ──
    states = _list(behavior_ir.get("states"))
    for state in states:
        state_id = _text(state.get("id"))
        if not state_id:
            continue
        entity_ref = _text(state.get("entity_ref"))
        source_refs = _list(state.get("source_refs"))

        coverage_id = f"cov_state_{state_id}"
        if coverage_id in seen_ids:
            continue
        seen_ids.add(coverage_id)
        nodes.append({
            "coverage_id": coverage_id,
            "node_type": "state",
            "ir_node_id": state_id,
            "risk_family": "state_integrity",
            "entity_ref": entity_ref,
            "state_name": _text(state.get("name")),
            "source_refs": source_refs,
            "coverage_signature": _coverage_signature(state_id, "state_integrity", "", _text(state.get("name"))[:120]),
        })

    # ── Compute family counts ──
    family_counts: dict[str, int] = {}
    for node in nodes:
        family = str(node.get("risk_family") or "")
        family_counts[family] = family_counts.get(family, 0) + 1

    return {
        "schema_version": COVERAGE_SCHEMA,
        "node_count": len(nodes),
        "risk_family_counts": dict(sorted(family_counts.items())),
        "coverage_gaps": coverage_gaps,
        "nodes": nodes,
    }


def _coverage_signature(*parts: str) -> str:
    """Stable hash for a coverage node identity."""
    joined = "::".join(str(p or "").strip() for p in parts if str(p or "").strip())
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════
# Gap Detection: find uncovered Behavior IR nodes
# ═══════════════════════════════════════════════════════════════════


def _hypothesis_targets_entity(hypothesis: dict[str, Any], entity_name: str) -> bool:
    """Check if a hypothesis targets a specific entity name."""
    if not entity_name:
        return False
    entity_lower = entity_name.lower()
    # Check entity field
    hyp_entity = _text(hypothesis.get("entity") or hypothesis.get("source_entity") or "").lower()
    if entity_lower in hyp_entity or hyp_entity in entity_lower:
        return True
    # Check title
    title = _text(hypothesis.get("title") or "").lower()
    if entity_lower in title:
        return True
    # Check verification path
    vm = hypothesis.get("verification_method")
    if isinstance(vm, dict):
        path = _text(vm.get("path") or "").lower()
        if entity_lower in path:
            return True
    return False


def _hypothesis_targets_path(hypothesis: dict[str, Any], path: str) -> bool:
    """Check if a hypothesis targets a specific API path."""
    if not path:
        return False
    path_lower = path.lower().rstrip("/")
    # Check verification method path
    vm = hypothesis.get("verification_method")
    if isinstance(vm, dict):
        vm_path = _text(vm.get("path") or "").lower().rstrip("/")
        if vm_path and (vm_path == path_lower or vm_path.startswith(path_lower) or path_lower.startswith(vm_path)):
            return True
    # Check related_endpoints
    endpoints = _list(hypothesis.get("related_endpoints"))
    for ep in endpoints:
        ep_text = _text(ep if isinstance(ep, str) else _dict(ep).get("path", "")).lower().rstrip("/")
        if ep_text and (ep_text == path_lower or ep_text.startswith(path_lower)):
            return True
    # Check reproduction_steps
    steps = _list(hypothesis.get("reproduction_steps"))
    for step in steps:
        step_text = _text(step).lower()
        if path_lower in step_text:
            return True
    return False


def _hypothesis_targets_risk_family(hypothesis: dict[str, Any], family: str) -> bool:
    """Check if a hypothesis targets a specific risk family."""
    if not family:
        return False
    family_lower = family.lower()
    # Check risk_type / category
    risk_type = _text(hypothesis.get("risk_type") or hypothesis.get("category") or "").lower()
    if family_lower in risk_type or risk_type in family_lower:
        return True
    # Check title for family keywords
    title = _text(hypothesis.get("title") or "").lower()
    family_keywords: dict[str, list[str]] = {
        "authorization": ["authorization", "permission", "access control", "privilege", "role", "unauthorized", "forbidden"],
        "visibility": ["visibility", "visible", "hidden", "see", "view", "expose", "leak", "disclosure"],
        "isolation": ["isolation", "tenant", "cross-tenant", "multi-tenant", "separate", "partition"],
        "consistency": ["consistency", "integrity", "corrupt", "duplicate", "idempoten", "race condition"],
        "state_integrity": ["state", "transition", "invalid state", "status", "lifecycle"],
        "lifecycle": ["lifecycle", "create", "delete", "archive", "expire", "soft delete"],
        "invariant": ["invariant", "business rule", "constraint", "validation", "ensure", "must", "required"],
    }
    keywords = family_keywords.get(family_lower, [family_lower])
    for kw in keywords:
        if kw in title:
            return True
    return False


def _hypothesis_covers_actor(hypothesis: dict[str, Any], actor_role: str) -> bool:
    """Check if a hypothesis involves a specific actor role."""
    if not actor_role:
        return False
    role_lower = actor_role.lower()
    # Check title
    title = _text(hypothesis.get("title") or "").lower()
    if role_lower in title:
        return True
    # Check description
    desc = _text(hypothesis.get("description") or "").lower()
    if role_lower in desc:
        return True
    # Check expected_behavior
    expected = _text(hypothesis.get("expected_behavior") or "").lower()
    if role_lower in expected:
        return True
    return False


def compute_hypothesis_coverage_gaps(
    behavior_ir: dict[str, Any],
    hypotheses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cross-reference hypotheses against the Behavior IR coverage map.

    Returns a dict with:
    - ``covered_count`` / ``uncovered_count`` / ``total_count``
    - ``uncovered_nodes``: list of coverage nodes with zero hypothesis coverage
    - ``uncovered_by_family``: per-risk-family uncovered counts
    - ``coverage_rate``: fraction of nodes covered
    """

    coverage_map = build_behavior_ir_coverage_map(behavior_ir)
    coverage_nodes = coverage_map.get("nodes", [])
    if not coverage_nodes:
        return {
            "schema_version": COVERAGE_SCHEMA,
            "status": "empty_behavior_ir",
            "covered_count": 0,
            "uncovered_count": 0,
            "total_count": 0,
            "coverage_rate": None,
            "uncovered_nodes": [],
            "uncovered_by_family": {},
        }

    # For each hypothesis, determine which coverage nodes it targets
    covered_ids: set[str] = set()
    for hyp in hypotheses:
        if not isinstance(hyp, dict):
            continue
        for node in coverage_nodes:
            node_id = _text(node.get("coverage_id"))
            if not node_id or node_id in covered_ids:
                continue

            node_type = _text(node.get("node_type"))
            family = _text(node.get("risk_family"))
            path = _text(node.get("operation_path"))
            entity_refs = _list(node.get("entity_refs"))
            actor_role = _text(node.get("actor_role"))

            covered = False

            # Match by risk family
            if _hypothesis_targets_risk_family(hyp, family):
                covered = True
            # Match by path
            elif path and _hypothesis_targets_path(hyp, path):
                covered = True
            # Match by entity
            elif entity_refs:
                for entity_name in entity_refs:
                    if _hypothesis_targets_entity(hyp, entity_name):
                        covered = True
                        break
            # Match by actor
            elif actor_role and _hypothesis_covers_actor(hyp, actor_role):
                covered = True

            if covered:
                covered_ids.add(node_id)

    # Build uncovered list
    uncovered: list[dict[str, Any]] = []
    uncovered_by_family: dict[str, int] = {}
    for node in coverage_nodes:
        node_id = _text(node.get("coverage_id"))
        if node_id in covered_ids:
            continue
        family = _text(node.get("risk_family"))
        uncovered.append(node)
        uncovered_by_family[family] = uncovered_by_family.get(family, 0) + 1

    total = len(coverage_nodes)
    covered = total - len(uncovered)

    return {
        "schema_version": COVERAGE_SCHEMA,
        "status": "ready",
        "covered_count": covered,
        "uncovered_count": len(uncovered),
        "total_count": total,
        "coverage_rate": round(covered / total, 4) if total > 0 else None,
        "uncovered_nodes": uncovered,
        "uncovered_by_family": dict(sorted(uncovered_by_family.items(), key=lambda x: -x[1])),
    }


# ═══════════════════════════════════════════════════════════════════
# Source-backed hypothesis generation for uncovered nodes
# ═══════════════════════════════════════════════════════════════════


def _template_title(node: dict[str, Any]) -> str:
    """Generate an industry-neutral, source-grounded hypothesis title."""
    node_type = _text(node.get("node_type"))
    family = _text(node.get("risk_family"))
    path = _text(node.get("operation_path"))
    method = _text(node.get("operation_method"))
    actor_role = _text(node.get("actor_role"))
    relation_type = _text(node.get("relation_type"))
    entity_refs = _list(node.get("entity_refs"))
    entity_name = entity_refs[0] if entity_refs else ""
    state_name = _text(node.get("state_name"))
    inv_desc = _text(node.get("invariant_description"))

    if node_type == "actor_operation" and actor_role and path:
        return f"Verify {actor_role} authorization boundary for {method} {path}"
    if node_type == "invariant":
        return f"Verify business invariant: {inv_desc[:120]}"
    if node_type == "relation" and relation_type == "transitions":
        return f"Verify state transition integrity on {path or entity_name}"
    if node_type == "relation" and relation_type == "conserves":
        return f"Verify resource conservation on {path or entity_name}"
    if node_type == "relation" and relation_type == "owns":
        return f"Verify tenant isolation boundary on {path or entity_name}"
    if node_type == "state":
        return f"Verify state integrity for {entity_name}: {state_name}"
    if node_type == "operation" and path:
        if family == "authorization":
            return f"Verify authorization enforcement on {method} {path}"
        if family == "visibility":
            return f"Verify data visibility controls on {method} {path}"
        if family == "isolation":
            return f"Verify cross-tenant isolation on {method} {path}"
        if family == "consistency":
            return f"Verify data consistency on {method} {path}"
        if family == "state_integrity":
            return f"Verify state transition validation on {method} {path}"
        if family == "lifecycle":
            return f"Verify entity lifecycle enforcement on {method} {path}"
    return f"Verify {family} behavior for documented operation"


def _template_expected_behavior(node: dict[str, Any]) -> str:
    """Generate an expected behavior description from Behavior IR facts."""
    family = _text(node.get("risk_family"))
    actor_role = _text(node.get("actor_role"))
    path = _text(node.get("operation_path"))
    method = _text(node.get("operation_method"))
    entity_refs = _list(node.get("entity_refs"))
    entity_name = entity_refs[0] if entity_refs else "resource"

    templates: dict[str, str] = {
        "authorization": (
            f"Only authorized actors should be able to perform {method} on {path}. "
            f"Unauthorized requests must receive a 401/403 response without side effects."
        ),
        "visibility": (
            f"Data returned by {method} {path} must be scoped to the requesting actor's "
            f"visibility boundary. Cross-scope data must not be exposed."
        ),
        "isolation": (
            f"Operations on {path} must be isolated per tenant scope. "
            f"A request in one tenant scope must not affect or expose data in another."
        ),
        "consistency": (
            f"{method} {path} must maintain data consistency. "
            f"Concurrent or repeated requests must not produce invalid state."
        ),
        "state_integrity": (
            f"State transitions on {entity_name} via {method} {path} must follow "
            f"the documented lifecycle. Invalid transitions must be rejected."
        ),
        "lifecycle": (
            f"The {entity_name} entity lifecycle must be enforced. "
            f"Operations outside valid lifecycle states must be rejected with clear errors."
        ),
        "invariant": (
            f"The documented business rule must hold after {method} {path}. "
            f"Violations must be detected and prevented."
        ),
    }
    return templates.get(family, f"Verify expected {family} behavior on {method} {path}.")


def _template_verification_method(node: dict[str, Any]) -> dict[str, Any]:
    """Build a verification method dict from the coverage node."""
    path = _text(node.get("operation_path"))
    method = _text(node.get("operation_method"))
    actor_role = _text(node.get("actor_role"))

    vm: dict[str, str] = {}
    if path:
        vm["path"] = path
    if method:
        vm["method"] = method

    # Step 1: Send request as treatment actor
    if actor_role:
        vm["step1"] = f"Send {method or 'GET'} request to {path or 'endpoint'} as {actor_role}"
    elif path:
        vm["step1"] = f"Send {method or 'GET'} request to {path}"

    # Step 2: Observe response
    vm["step2"] = "Observe HTTP response status and body"

    # Step 3: Compare against expected behavior
    family = _text(node.get("risk_family"))
    if family == "authorization":
        vm["step3"] = "Verify authorization decision matches documented permission model"
    elif family == "visibility":
        vm["step3"] = "Verify returned data is scoped to the requesting actor"
    elif family == "isolation":
        vm["step3"] = "Verify cross-tenant data isolation is enforced"
    elif family == "consistency":
        vm["step3"] = "Verify data consistency after operation"
    elif family == "state_integrity":
        vm["step3"] = "Verify state transition follows documented lifecycle"
    elif family == "lifecycle":
        vm["step3"] = "Verify lifecycle state is valid for the operation"
    else:
        vm["step3"] = "Verify behavior matches documented expectations"

    return vm


def build_source_backed_coverage_hypotheses(
    behavior_ir: dict[str, Any],
    gaps: dict[str, Any],
    *,
    max_hypotheses: int = MAX_COVERAGE_HYPOTHESES,
) -> list[dict[str, Any]]:
    """Generate source-grounded hypotheses for uncovered Behavior IR nodes.

    Each hypothesis carries:
    - ``source_refs``: traced from the originating Behavior IR node
    - ``_hypothesis_source``: ``"behavior_ir_coverage"``
    - ``_coverage_node_id``: the coverage node that triggered this hypothesis
    - Standard hypothesis fields: ``hypothesis_id``, ``title``, ``severity``,
      ``expected_behavior``, ``verification_method``, etc.

    Args:
        behavior_ir: The Behavior IR v2 model.
        gaps: Output from ``compute_hypothesis_coverage_gaps()``.
        max_hypotheses: Maximum hypotheses to generate (default 30).
    """
    uncovered = gaps.get("uncovered_nodes", [])
    if not uncovered:
        return []

    # Sort uncovered by priority: authorization > isolation > consistency >
    # state_integrity > visibility > lifecycle > invariant
    family_priority = {
        "authorization": 0,
        "isolation": 1,
        "consistency": 2,
        "state_integrity": 3,
        "visibility": 4,
        "lifecycle": 5,
        "invariant": 6,
    }

    sorted_uncovered = sorted(
        uncovered,
        key=lambda n: (
            family_priority.get(_text(n.get("risk_family")), 99),
            _text(n.get("coverage_id")),
        ),
    )

    hypotheses: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for node in sorted_uncovered:
        if len(hypotheses) >= max(1, int(max_hypotheses)):
            break

        title = _template_title(node)
        title_key = title.lower().strip()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        family = _text(node.get("risk_family"))
        source_refs = _list(node.get("source_refs"))
        coverage_id = _text(node.get("coverage_id"))
        path = _text(node.get("operation_path"))

        # Determine severity based on risk family
        severity_map = {
            "authorization": "P0",
            "isolation": "P0",
            "consistency": "P1",
            "state_integrity": "P1",
            "lifecycle": "P2",
            "visibility": "P2",
            "invariant": "P2",
        }
        severity = severity_map.get(family, "P2")

        hypothesis_id = _stable_id("cov_hyp", coverage_id, title)

        hypothesis: dict[str, Any] = {
            "hypothesis_id": hypothesis_id,
            "title": title,
            "severity": severity,
            "category": family,
            "risk_type": family,
            "expected_behavior": _template_expected_behavior(node),
            "verification_method": _template_verification_method(node),
            "evidence": (
                f"Derived from Behavior IR node: {_text(node.get('ir_node_id'))}. "
                f"Source documents describe this operation and its expected behavior."
            ),
            "description": (
                f"Behavior IR coverage gap detected for {family} risk on "
                f"{path or _text(node.get('ir_node_id'))}. "
                f"No existing hypothesis covers this verifiable surface."
            ),
            "why_this_matters": (
                f"Undetected {family} violations on this surface could lead to "
                f"security, data integrity, or business rule failures."
            ),
            "symptoms_if_broken": (
                f"Unexpected HTTP responses, data leakage, state corruption, "
                f"or business rule violations on {path or 'the target operation'}."
            ),
            "entity": _text(
                node.get("entity_refs", [None])[0] if _list(node.get("entity_refs")) else ""
            ),
            "source_refs": source_refs,
            "_hypothesis_source": "behavior_ir_coverage",
            "_coverage_node_id": coverage_id,
            "_reasoner_engine": "coverage_gap_filler",
            "execution_block": "",
            "confirmation_status": "",
        }

        hypotheses.append(hypothesis)

    return hypotheses


# ═══════════════════════════════════════════════════════════════════
# Top-level convenience
# ═══════════════════════════════════════════════════════════════════


def enrich_hypotheses_with_coverage(
    behavior_ir: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    *,
    max_coverage_hypotheses: int = MAX_COVERAGE_HYPOTHESES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run coverage gap analysis and append source-backed hypotheses.

    Returns ``(enriched_hypotheses, coverage_report)``.
    The coverage report includes gap counts, uncovered nodes, and coverage rate.
    """
    gaps = compute_hypothesis_coverage_gaps(behavior_ir, hypotheses)
    coverage_hypotheses = build_source_backed_coverage_hypotheses(
        behavior_ir, gaps, max_hypotheses=max_coverage_hypotheses
    )

    # Tag existing hypotheses as not from coverage (for diagnostics)
    result = list(hypotheses) if isinstance(hypotheses, list) else []
    if coverage_hypotheses:
        result.extend(coverage_hypotheses)

    report = {
        "schema_version": COVERAGE_SCHEMA,
        "original_hypothesis_count": len(hypotheses) if isinstance(hypotheses, list) else 0,
        "coverage_hypothesis_count": len(coverage_hypotheses),
        "total_hypothesis_count": len(result),
        "coverage_gap": {
            "total_nodes": gaps.get("total_count", 0),
            "covered": gaps.get("covered_count", 0),
            "uncovered": gaps.get("uncovered_count", 0),
            "coverage_rate": gaps.get("coverage_rate"),
            "uncovered_by_family": gaps.get("uncovered_by_family", {}),
        },
    }

    return result, report


# ═══════════════════════════════════════════════════════════════════
# Obligation-level coverage: generate source-backed obligations
# ═══════════════════════════════════════════════════════════════════


def _obligation_covers_node(obligation: dict[str, Any], node: dict[str, Any]) -> bool:
    """Check if an existing obligation covers a coverage node."""
    family = _text(node.get("risk_family"))
    path = _text(node.get("operation_path"))
    entity_refs = _list(node.get("entity_refs"))
    actor_role = _text(node.get("actor_role"))

    # Direct risk_family match
    obl_family = _text(obligation.get("risk_family") or "")
    if obl_family == family:
        return True

    # Obligation required_operations matches
    req_ops = _list(obligation.get("required_operations") or [])
    for op_ref in req_ops:
        if _text(op_ref) == _text(node.get("ir_node_id")) or _text(op_ref) in _text(node.get("coverage_id")):
            return True

    # Obligation subject_refs contains the operation
    subject_refs = _list(obligation.get("subject_refs") or [])
    ir_node_id = _text(node.get("ir_node_id"))
    if ir_node_id and any(ir_node_id in _text(s) for s in subject_refs):
        return True

    # Path match through property_spec
    prop_spec = _dict(obligation.get("property_spec") or {})
    obl_path = _text(prop_spec.get("operation_path_prefix") or "")
    if path and obl_path:
        path_lower = path.lower().rstrip("/")
        obl_path_lower = obl_path.lower().rstrip("/")
        if path_lower == obl_path_lower or path_lower.startswith(obl_path_lower) or obl_path_lower.startswith(path_lower):
            return True

    # Actor match
    req_actors = _list(obligation.get("required_actors") or [])
    actor_id = _text(node.get("actor_id"))
    if actor_role and actor_id:
        if actor_id in req_actors:
            return True

    # Entity match through subject_refs
    for entity_name in entity_refs:
        if any(_text(entity_name).lower() in _text(s).lower() for s in subject_refs):
            return True

    return False


def compute_obligation_coverage_gaps(
    behavior_ir: dict[str, Any],
    obligations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cross-reference existing obligations against the Behavior IR coverage map.

    Returns same shape as ``compute_hypothesis_coverage_gaps`` but matches
    against obligation fields instead of hypothesis fields.
    """

    coverage_map = build_behavior_ir_coverage_map(behavior_ir)
    coverage_nodes = coverage_map.get("nodes", [])
    if not coverage_nodes:
        return {
            "schema_version": COVERAGE_SCHEMA,
            "status": "empty_behavior_ir",
            "covered_count": 0,
            "uncovered_count": 0,
            "total_count": 0,
            "coverage_rate": None,
            "uncovered_nodes": [],
            "uncovered_by_family": {},
        }

    covered_ids: set[str] = set()
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        for node in coverage_nodes:
            node_id = _text(node.get("coverage_id"))
            if not node_id or node_id in covered_ids:
                continue
            if _obligation_covers_node(obl, node):
                covered_ids.add(node_id)

    uncovered: list[dict[str, Any]] = []
    uncovered_by_family: dict[str, int] = {}
    for node in coverage_nodes:
        node_id = _text(node.get("coverage_id"))
        if node_id in covered_ids:
            continue
        family = _text(node.get("risk_family"))
        uncovered.append(node)
        uncovered_by_family[family] = uncovered_by_family.get(family, 0) + 1

    total = len(coverage_nodes)
    covered = total - len(uncovered)

    return {
        "schema_version": COVERAGE_SCHEMA,
        "status": "ready",
        "covered_count": covered,
        "uncovered_count": len(uncovered),
        "total_count": total,
        "coverage_rate": round(covered / total, 4) if total > 0 else None,
        "uncovered_nodes": uncovered,
        "uncovered_by_family": dict(sorted(uncovered_by_family.items(), key=lambda x: -x[1])),
    }


def build_source_backed_coverage_obligations(
    behavior_ir: dict[str, Any],
    gaps: dict[str, Any],
    *,
    max_obligations: int = MAX_COVERAGE_HYPOTHESES,
) -> list[dict[str, Any]]:
    """Generate source-grounded Test Obligations for uncovered Behavior IR nodes.

    Each obligation carries ``source_refs`` from its originating IR node and
    follows the standard obligation schema (``risk_family``, ``property_spec``,
    ``required_actors``, ``required_operations``, ``required_observers``,
    ``cleanup_requirement``, ``source_refs``).

    This is the obligation-level equivalent of ``build_source_backed_coverage_hypotheses``.
    """
    uncovered = gaps.get("uncovered_nodes", [])
    if not uncovered:
        return []

    family_priority = {
        "authorization": 0,
        "isolation": 1,
        "consistency": 2,
        "state_integrity": 3,
        "visibility": 4,
        "lifecycle": 5,
        "invariant": 6,
    }

    sorted_uncovered = sorted(
        uncovered,
        key=lambda n: (
            family_priority.get(_text(n.get("risk_family")), 99),
            _text(n.get("coverage_id")),
        ),
    )

    # Import obligation factory
    try:
        from .test_obligation import make_obligation
    except ImportError:
        # Fallback: build minimal obligation dict
        def make_obligation(**kwargs: Any) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in kwargs.items():
                if value is not None:
                    result[key] = value
            if "obligation_id" not in result:
                raw = "|".join(str(v) for v in [result.get("risk_family"), result.get("subject_refs")] if v)
                result["obligation_id"] = f"obl_cov_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
            return result

    obligations: list[dict[str, Any]] = []

    # Build valid operation ID set for validation
    all_ops = _list(behavior_ir.get("operations"))
    valid_op_ids: set[str] = {_text(op.get("id")) for op in all_ops if _text(op.get("id"))}

    for node in sorted_uncovered:
        if len(obligations) >= max(1, int(max_obligations)):
            break

        family = _text(node.get("risk_family"))
        node_type = _text(node.get("node_type"))
        # For actor_operation nodes, the real operation id is in "operation_id"
        if node_type == "actor_operation":
            op_id = _text(node.get("operation_id"))
        else:
            op_id = _text(node.get("ir_node_id"))

        # Validate: only generate obligations for coverage nodes that reference
        # real operations in the Behavior IR. An obligation without a resolvable
        # primary operation can never compile — it wastes budget as DEFERRED.
        op_refs: list[str] = []
        if node_type == "invariant":
            op_refs = [ref for ref in _list(node.get("operation_refs")) if _text(ref) in valid_op_ids]
        elif node_type == "relation":
            # Relation nodes may reference an operation; validate it
            rel_op_ref = _text(node.get("operation_ref"))
            if rel_op_ref and rel_op_ref in valid_op_ids:
                op_refs = [rel_op_ref]
        elif node_type == "state":
            # State nodes reference entities — resolve operations via transitions
            _state_id = _text(node.get("ir_node_id"))
            for _rel in _list(behavior_ir.get("relations")):
                if not isinstance(_rel, dict):
                    continue
                if _text(_rel.get("relation_type")) != "transitions":
                    continue
                if _state_id in {_text(_rel.get("from_ref")), _text(_rel.get("to_ref"))}:
                    _rel_op = _text(_rel.get("operation_ref"))
                    if _rel_op and _rel_op in valid_op_ids and _rel_op not in op_refs:
                        op_refs.append(_rel_op)
        elif op_id and op_id in valid_op_ids:
            op_refs = [op_id]
        elif node_type in ("operation", "actor_operation"):
            # Operation node whose operation ID doesn't exist → skip
            continue

        # ── Entity-co-reference resolution for still-unresolved nodes ──
        # Invariant and relation nodes without direct operation refs may still
        # be resolvable through their entity declarations.
        if not op_refs and node_type in ("invariant", "relation", "state"):
            _node_entity_refs: set[str] = set()
            _ir_node_id = _text(node.get("ir_node_id"))
            # Collect entity refs from the source IR node
            if node_type == "invariant":
                for _inv in _list(behavior_ir.get("invariants")):
                    if isinstance(_inv, dict) and _text(_inv.get("id")) == _ir_node_id:
                        _node_entity_refs.update(
                            _text(e) for e in _list(_inv.get("entity_refs")) if _text(e)
                        )
                        _top_ent = _text(_inv.get("entity_ref"))
                        if _top_ent:
                            _node_entity_refs.add(_top_ent)
                        break
            elif node_type == "state":
                _ent = _text(node.get("entity_ref"))
                if _ent:
                    _node_entity_refs.add(_ent)
            elif node_type == "relation":
                # Use from_ref/to_ref to find related entities via states
                for _ref_key in ("from_ref", "to_ref"):
                    _ref_val = _text(node.get(_ref_key))
                    for _st in _list(behavior_ir.get("states")):
                        if isinstance(_st, dict) and _text(_st.get("id")) == _ref_val:
                            _ent = _text(_st.get("entity_ref"))
                            if _ent:
                                _node_entity_refs.add(_ent)
            # Match operations by entity_refs overlap
            if _node_entity_refs:
                for _cand_op in all_ops:
                    if not isinstance(_cand_op, dict):
                        continue
                    _cand_id = _text(_cand_op.get("id"))
                    if not _cand_id or _cand_id not in valid_op_ids:
                        continue
                    _cand_ents = {
                        _text(e) for e in _list(_cand_op.get("entity_refs")) if _text(e)
                    }
                    if _cand_ents & _node_entity_refs:
                        op_refs.append(_cand_id)
                        if len(op_refs) >= 3:  # cap to avoid over-binding
                            break

        # An obligation with no resolvable operation can never compile.
        # Record a coverage gap instead of creating a guaranteed-DEFERRED attempt.
        if not op_refs:
            continue

        primary_operation = next(
            (
                operation
                for operation in all_ops
                if isinstance(operation, dict)
                and _text(operation.get("id")) == op_refs[0]
            ),
            {},
        )
        op_path = _text(node.get("operation_path") or primary_operation.get("path"))
        op_method = _text(
            primary_operation.get("method") or node.get("operation_method")
        )
        actor_id = _text(node.get("actor_id"))
        source_refs = _list(node.get("source_refs"))
        coverage_id = _text(node.get("coverage_id"))

        # Coverage nodes may still label taxonomy as ``invariant``. Compile
        # authority must use the same expression→family map as Strategy-7; a bare
        # ``risk_family=invariant`` without expression hits
        # ``invariant_assertion_kind_missing`` at the experiment compiler.
        invariant_ref = ""
        expression: dict[str, Any] = {}
        executable_kind = ""
        if node_type == "invariant":
            inv_row = next(
                (
                    row
                    for row in _list(behavior_ir.get("invariants"))
                    if isinstance(row, dict)
                    and _text(row.get("id")) == _text(node.get("ir_node_id"))
                ),
                {},
            )
            invariant_ref = _text(inv_row.get("id") or node.get("ir_node_id"))
            inv_kind = _invariant_kind(inv_row) if inv_row else ""
            expression = _dict(inv_row.get("expression")) if inv_row else {}
            if not expression and inv_kind:
                expression = {"kind": inv_kind}
            if inv_kind == "postcondition" and not _postcondition_has_bound_effect(
                expression
            ):
                # Same gate as Strategy-7 / coverage-map builders: prose-only
                # postconditions stay coverage gaps, not compile-blocked obligations.
                continue
            family = _risk_family_for_matrix_invariant(inv_kind)
            executable_kind = (
                inv_kind.lower()
                if inv_kind.lower() in _EXECUTABLE_INVARIANT_ASSERTION_KINDS
                else _text(expression.get("kind")).lower()
            )
            if executable_kind not in _EXECUTABLE_INVARIANT_ASSERTION_KINDS:
                executable_kind = ""

        # Determine property template
        template_map: dict[str, str] = {
            "authorization": "permitted_operation_invocation",
            "isolation": "owner_viewer_isolation",
            "visibility": "permitted_operation_invocation",
            "consistency": "single_dimension_mutation",
            "state_integrity": "state_transition_boundary",
            "state": "invariant_violation_detection",
            "conservation": "invariant_violation_detection",
            "validation": "invariant_violation_detection",
            "idempotency": "idempotent_write_verification",
            "concurrency": "conservation_under_concurrency",
            "temporal": "invariant_violation_detection",
            "privacy": "invariant_violation_detection",
        }
        template = template_map.get(family, "permitted_operation_invocation")
        if node_type == "invariant":
            template = "invariant_violation_detection"

        # Actor handling
        actors = _list(behavior_ir.get("actors"))
        active_actors = [
            a for a in actors
            if isinstance(a, dict) and a.get("runtime_bound") is True
        ]
        actor_ref = actor_id
        alt_actor_ref = ""
        if not actor_ref and active_actors:
            actor_ref = _text(active_actors[0].get("id"))
            if len(active_actors) > 1:
                alt_actor_ref = _text(active_actors[1].get("id"))

        required_actors = [actor_ref] if actor_ref else []
        if alt_actor_ref and template == "owner_viewer_isolation":
            required_actors = [actor_ref, alt_actor_ref]

        # Observers: resolve through make_obligation's canonical family so
        # promotion candidates (invariant→validation) still get a real list.
        from .test_obligation import resolve_risk_family

        resolved_family = _text(resolve_risk_family(family).get("canonical")) or family
        required_observers = (
            _MATRIX_OBSERVERS_BY_FAMILY.get(resolved_family)
            or _MATRIX_OBSERVERS_BY_FAMILY.get(family)
            or ["http_response"]
        )

        # Cleanup follows the source-declared semantic effect. A POST can be a
        # read-only validation/preview action; method-only classification would
        # create an impossible cleanup obligation for that operation.
        # make_obligation requires a dict — a bare string raises ValueError and
        # previously fell into a silent fallback that emitted bare
        # risk_family=invariant (measured as invariant_assertion_kind_missing).
        effect_input = primary_operation or {
            "side_effect_class": node.get("side_effect_class"),
            "read_write": node.get("read_write"),
        }
        is_write = _infer_operation_effect(effect_input, op_method.upper()) == "write"
        cleanup_requirement: dict[str, Any] = {"required": bool(is_write)}

        # Build property spec
        primary_op = op_refs[0] if op_refs else ""
        property_spec: dict[str, Any] = {
            "template": template,
            "actor_ref": actor_ref or "",
            "operation_ref": primary_op,
            "operation_path_prefix": op_path,
            "_coverage_node_id": coverage_id,
        }
        if alt_actor_ref and template == "owner_viewer_isolation":
            property_spec["owner_actor_ref"] = actor_ref
            property_spec["viewer_actor_ref"] = alt_actor_ref
        if node_type == "invariant":
            property_spec.update({
                "invariant_ref": invariant_ref,
                "operation_refs": list(op_refs),
                "invariant_kind": executable_kind,
                "expression": expression,
                "_strategy": "coverage_invariant",
            })

        # Fail visible: do not paper over factory errors with a bare-family
        # obligation that can only die later as invariant_assertion_kind_missing.
        obl = make_obligation(
            risk_family=family,
            subject_refs=op_refs + ([actor_ref] if actor_ref else []),
            property_spec=property_spec,
            required_actors=required_actors,
            required_operations=op_refs,
            required_observers=list(required_observers),
            cleanup_requirement=cleanup_requirement,
            source_refs=source_refs,
            confidence=0.5,
        )

        obl["_coverage_obligation"] = True
        obligations.append(obl)


    return obligations


# ═══════════════════════════════════════════════════════════════════
# Exhaustive Obligation Matrix (Phase 4.1)
# ═══════════════════════════════════════════════════════════════════


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = operation.get("request_schema")
    if not isinstance(schema, dict):
        return {}
    content = schema.get("content")
    if isinstance(content, dict):
        for media in content.values():
            if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                return media["schema"]
    return schema if isinstance(schema.get("properties"), dict) else {}


def _declared_field_constraints(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the request-field constraints the source states for this operation.

    Only constraints written in the schema are returned. Each one names a rule a
    probe can violate on purpose, so the expected rejection is the source's claim
    rather than the harness's assumption.
    """
    schema = _request_body_schema(operation)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = {
        _text(name)
        for name in (schema.get("required") if isinstance(schema.get("required"), list) else [])
        if _text(name)
    }
    constraints: list[dict[str, Any]] = []
    for name, spec in properties.items():
        field = _text(name)
        if not field or not isinstance(spec, dict):
            continue
        if field in required:
            constraints.append({"field": field, "constraint": "required", "declared_value": True})
        declared_type = _text(spec.get("type"))
        if declared_type:
            constraints.append(
                {"field": field, "constraint": "type", "declared_value": declared_type}
            )
        for keyword in ("minimum", "maximum", "minLength", "maxLength", "pattern", "enum", "format"):
            if keyword in spec:
                constraints.append(
                    {"field": field, "constraint": keyword, "declared_value": spec[keyword]}
                )
    return constraints


# Every family this generator emits must name observers the experiment compiler
# implements. A family absent here has no way to be observed and is not emitted.
_MATRIX_OBSERVERS_BY_FAMILY: dict[str, list[str]] = {
    "authorization": ["http_response", "actor_identity"],
    "isolation": ["http_response", "resource_ownership"],
    "validation": ["http_response", "typed_assertion"],
    "state": ["entity_state", "typed_assertion", "source_invariant", "before_state", "after_state"],
    "state_integrity": ["http_response", "entity_state"],
    "conservation": ["typed_assertion", "source_invariant", "entity_state"],
    "consistency": ["http_response", "entity_state"],
    # Kept for legacy matrix rows; new Strategy-7 rows resolve via make_obligation.
    "invariant": ["http_response", "entity_state", "typed_assertion", "source_invariant"],
}

# Assertion kinds the invariant compile gate accepts (keep aligned with the
# invariant-kind registry in the obligation compiler's core module).
_EXECUTABLE_INVARIANT_ASSERTION_KINDS = frozenset({
    "conservation",
    "cross_entity_consistency",
    "field_delta",
    "forbidden_state_transition",
    "idempotency",
    "postcondition",
    "state_transition",
})


def _invariant_kind(invariant: dict[str, Any]) -> str:
    expression = invariant.get("expression")
    if isinstance(expression, dict):
        return _text(expression.get("kind"))
    return _text(invariant.get("kind"))


def _risk_family_for_matrix_invariant(inv_kind: str) -> str:
    """Map a source expression kind to a compile family (obligation-compiler SSOT).

    The exhaustive matrix previously emitted ``risk_family=invariant`` without going
    through ``make_obligation`` and without attaching ``expression``. That left the
    compiler with an empty protocol assertion and
    ``FIELD_LEVEL_RULE_NOT_EXECUTABLE:invariant_assertion_kind_missing`` — measured
    ×42 on held-in-131 after cleanup was already fixed.
    """
    kind = _text(inv_kind).lower()
    if any(token in kind for token in ("idempot", "exactly_once", "deduplic")):
        return "idempotency"
    if any(token in kind for token in ("concurr", "race", "atomic")):
        return "concurrency"
    if any(
        token in kind
        for token in (
            "conserv",
            "data_conservation",
            "balance",
            "amount",
            "quantity",
            "库存",
            "金额",
        )
    ):
        return "conservation"
    if any(token in kind for token in ("privacy", "pii", "mask", "隐私")):
        return "privacy"
    if any(token in kind for token in ("time", "expir", "temporal", "过期")):
        return "temporal"
    if any(
        token in kind
        for token in (
            "permission",
            "access_control",
            "authorization",
            "authorisation",
            "authz",
            "acl",
            "rbac",
            "visib",
            "scope",
            "可见",
        )
    ):
        return "visibility"
    if any(token in kind for token in ("state_machine", "state", "状态", "status_")):
        return "state"
    if any(
        token in kind
        for token in ("postcondition", "must_become", "must_create", "因果", "后置")
    ):
        return "state"
    return "validation"


def _normalized_field(value: Any) -> str:
    return "".join(ch for ch in _text(value).lower() if ch.isalnum())


def _invariant_operation_refs(
    invariant: dict[str, Any],
    op_by_id: dict[str, dict[str, Any]],
    operations: list[dict[str, Any]],
) -> list[str]:
    """Resolve which operations an invariant governs, from source facts only.

    An explicit ``operation_refs`` wins. Otherwise the invariant's operands name
    an entity and a field, and an operation that writes that field is one the
    invariant constrains. Both sides come from the source, so the join adds no
    claim of its own.
    """
    declared = [
        _text(ref)
        for ref in _list(invariant.get("operation_refs"))
        if _text(ref) in op_by_id
    ]
    if declared:
        return declared

    expression = invariant.get("expression")
    operands = _list(expression.get("operands")) if isinstance(expression, dict) else []
    wanted_fields = {
        _normalized_field(operand.get("field"))
        for operand in operands
        if isinstance(operand, dict) and _normalized_field(operand.get("field"))
    }
    wanted_entities = {
        _text(operand.get("entity_ref"))
        for operand in operands
        if isinstance(operand, dict) and _text(operand.get("entity_ref"))
    }
    if not wanted_fields:
        return []

    matched: list[str] = []
    for op in operations:
        if _infer_operation_effect(op, _text(op.get("method")).upper()) != "write":
            continue
        op_fields = {
            _normalized_field(name)
            for name in [
                *_list(op.get("affected_fields")),
                *_list(op.get("field_dictionary")),
                *_list(op.get("parameters")),
            ]
        }
        if not op_fields & wanted_fields:
            continue
        op_entities = {_text(ref) for ref in _list(op.get("entity_refs")) if _text(ref)}
        if wanted_entities and op_entities and not (op_entities & wanted_entities):
            continue
        op_id = _text(op.get("id"))
        if op_id and op_id not in matched:
            matched.append(op_id)
    return matched


def build_exhaustive_obligation_matrix(
    behavior_ir: dict[str, Any],
    *,
    max_obligations: int = 2000,
    report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate an exhaustive obligation matrix from Behavior IR.

    Strategies (all industry-neutral, driven solely by Behavior IR declarations):
    1. Write operation × each actor → authorization test
    2. Each operation × boundary values → input validation
    3. Each state transition × illegal source state → state integrity
    4. Each entity × cross-actor access → isolation
    5. Each write operation × repeated submission → idempotency
    6. Each conservation relation × concurrent write → consistency

    Returns a list of obligation dicts compatible with the V12 pipeline.
    """
    ir = behavior_ir if isinstance(behavior_ir, dict) else {}
    operations = _list(ir.get("operations"))
    actors = _list(ir.get("actors"))
    entities = _list(ir.get("entities"))
    invariants = _list(ir.get("invariants"))
    relations = _list(ir.get("relations"))
    state_machines = _list(ir.get("state_machines") or ir.get("state_transitions"))

    # Index maps
    op_by_id: dict[str, dict[str, Any]] = {}
    for op in operations:
        oid = _text(op.get("id"))
        if oid:
            op_by_id[oid] = op

    write_ops = [
        op for op in operations
        if _infer_operation_effect(op, _text(op.get("method")).upper()) == "write"
    ]
    read_ops = [
        op for op in operations
        if _infer_operation_effect(op, _text(op.get("method")).upper()) == "read"
    ]
    # Only use actors that are runtime-bound (have tokens configured)
    # This prevents generating obligations for declared-but-unbound actors
    active_actors = [
        a for a in actors
        if _text(a.get("id")) and a.get("runtime_bound") is True
    ]
    # Fallback: if no runtime-bound actors, use all actors with IDs
    if not active_actors:
        active_actors = [a for a in actors if _text(a.get("id"))]

    obligations: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    skipped: collections.Counter[str] = collections.Counter()

    # (actor, operation) pairs the source actually decides. A pair the source
    # never mentions carries no contract, so there is nothing to assert about it.
    actor_ids = {_text(a.get("id")) for a in actors if _text(a.get("id"))}
    decided_actor_operations: set[tuple[str, str]] = set()
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if _text(relation.get("relation_type") or relation.get("type")) not in {
            "permits",
            "denies",
        }:
            continue
        refs = [
            _text(relation.get(key))
            for key in ("from_ref", "to_ref", "actor_ref", "operation_ref")
        ]
        for actor_ref in refs:
            if actor_ref not in actor_ids:
                continue
            for op_ref in refs:
                if op_ref in op_by_id:
                    decided_actor_operations.add((actor_ref, op_ref))

    def _add_obligation(
        risk_family: str,
        subject_refs: list[str],
        property_spec: dict[str, Any],
        required_actors: list[str],
        required_operations: list[str],
        source_refs: list[dict[str, Any]],
        *,
        confidence: float = 0.5,
        cleanup: str | None = None,
    ) -> None:
        if len(obligations) >= max_obligations:
            return
        # An obligation with no observer cannot compile: the experiment compiler
        # blocks it as BLOCKED_MISSING_OBSERVER before it ever reaches a target.
        # Resolve family through the same registry as make_obligation so promotion
        # candidates (consistency→validation) still get an observer list.
        from .test_obligation import make_obligation, resolve_risk_family

        resolved_family = _text(resolve_risk_family(risk_family).get("canonical")) or _text(
            risk_family
        )
        observers = _MATRIX_OBSERVERS_BY_FAMILY.get(
            resolved_family
        ) or _MATRIX_OBSERVERS_BY_FAMILY.get(_text(risk_family))
        if not observers:
            skipped["unobservable_family:" + (_text(risk_family) or resolved_family)] += 1
            return
        if not source_refs:
            skipped["no_source_ref:" + (_text(risk_family) or resolved_family)] += 1
            return
        if cleanup is None:
            cleanup = "not_required"
            for op_ref in required_operations:
                op_row = op_by_id.get(_text(op_ref)) or {}
                method = _text(op_row.get("method")).upper()
                if _infer_operation_effect(op_row, method) == "write":
                    cleanup = "required"
                    break
        sig = _coverage_signature(risk_family, *sorted(subject_refs), *sorted(required_actors))
        if sig in seen_signatures:
            return
        seen_signatures.add(sig)
        prop = dict(property_spec or {})
        cleanup_requirement: dict[str, Any] | str
        if isinstance(cleanup, dict):
            cleanup_requirement = cleanup
        else:
            cleanup_requirement = {
                "required": _text(cleanup).lower() in {"required", "true", "1", "yes"}
            }
        obl = make_obligation(
            risk_family=risk_family,
            subject_refs=subject_refs,
            property_spec=prop,
            required_actors=required_actors,
            required_operations=required_operations,
            required_observers=list(observers),
            cleanup_requirement=cleanup_requirement
            if isinstance(cleanup_requirement, dict)
            else {"required": bool(cleanup_requirement)},
            source_refs=source_refs,
            confidence=confidence,
        )
        # Keep a diagnostic mirror for matrix provenance / older readers.
        obl["property_spec"] = dict(obl.get("property") or prop)
        obl["_matrix_obligation"] = True
        obligations.append(obl)

    # ── Strategy 1: source-decided (actor, write op) pairs → authorization ──
    # A full cross product would assert an expectation for pairs the source never
    # decided, which is a guess about the target rather than a contract.
    for op in write_ops:
        op_id = _text(op.get("id"))
        op_src = _list(op.get("source_refs"))
        for actor in active_actors:
            actor_id = _text(actor.get("id"))
            if (actor_id, op_id) not in decided_actor_operations:
                skipped["authorization_pair_not_declared"] += 1
                continue
            actor_src = _list(actor.get("source_refs"))
            _add_obligation(
                "authorization",
                [op_id, actor_id],
                {
                    "template": "permitted_operation_invocation",
                    "actor_ref": actor_id,
                    "operation_ref": op_id,
                    "operation_path_prefix": _text(op.get("path")),
                    "_strategy": "auth_matrix",
                },
                [actor_id],
                [op_id],
                op_src + actor_src,
                cleanup="required",
            )

    # ── Strategy 2: declared request-field constraints → input validation ──
    # A boundary probe is only a contract when the source declares the constraint
    # it violates. A fixed list of boundary names applied to every write asserts
    # rules the source never stated.
    for op in operations:
        op_id = _text(op.get("id"))
        method = _text(op.get("method")).upper()
        if method not in {"POST", "PUT", "PATCH"}:
            continue
        op_src = _list(op.get("source_refs"))
        actor_refs = [_text(active_actors[0].get("id"))] if active_actors else []
        for constraint in _declared_field_constraints(op):
            field = constraint["field"]
            kind = constraint["constraint"]
            _add_obligation(
                "validation",
                [op_id, f"field:{field}", f"constraint:{kind}"],
                {
                    "template": "input_boundary_validation",
                    "operation_ref": op_id,
                    "operation_path_prefix": _text(op.get("path")),
                    "field": field,
                    "declared_constraint": kind,
                    "declared_value": constraint.get("declared_value"),
                    "_strategy": "boundary_matrix",
                },
                actor_refs,
                [op_id],
                op_src,
            )

    # ── Strategy 3: State transitions × illegal source → state integrity ──
    for sm in state_machines:
        sm_id = _text(sm.get("id") or sm.get("entity_ref"))
        transitions = _list(sm.get("transitions") or sm.get("allowed_transitions"))
        states = _list(sm.get("states") or sm.get("all_states"))
        sm_src = _list(sm.get("source_refs"))
        # Collect all declared from_states
        declared_from: set[str] = set()
        transition_ops: list[str] = []
        for tr in transitions:
            if isinstance(tr, dict):
                from_s = _text(tr.get("from") or tr.get("from_state"))
                if from_s:
                    declared_from.add(from_s)
                op_ref = _text(tr.get("operation_ref") or tr.get("trigger_operation"))
                if op_ref:
                    transition_ops.append(op_ref)
        # For each state not in declared_from, generate illegal transition obligation
        all_state_names = {_text(s) if isinstance(s, str) else _text(s.get("id") or s.get("name")) for s in states}
        for state in all_state_names:
            if not state or state in declared_from:
                continue
            _add_obligation(
                "state_integrity",
                [sm_id, f"illegal_from:{state}"],
                {
                    "template": "state_transition_boundary",
                    "entity_ref": sm_id,
                    "illegal_source_state": state,
                    "_strategy": "state_matrix",
                },
                [_text(active_actors[0].get("id"))] if active_actors else [],
                transition_ops[:3],
                sm_src,
            )

    # ── Strategy 4: Entity × cross-actor access → isolation ──
    for entity in entities:
        ent_id = _text(entity.get("id"))
        if not ent_id:
            continue
        ent_src = _list(entity.get("source_refs"))
        # Find operations that reference this entity
        ent_ops = [
            _text(op.get("id")) for op in operations
            if ent_id in [_text(r) for r in _list(op.get("entity_refs"))]
        ]
        if not ent_ops:
            continue
        # Cross-actor: each pair of actors
        for i, actor_a in enumerate(active_actors):
            for actor_b in active_actors[i + 1:]:
                a_id = _text(actor_a.get("id"))
                b_id = _text(actor_b.get("id"))
                _add_obligation(
                    "isolation",
                    [ent_id, a_id, b_id],
                    {
                        "template": "owner_viewer_isolation",
                        "owner_actor_ref": a_id,
                        "viewer_actor_ref": b_id,
                        "entity_ref": ent_id,
                        "operation_ref": ent_ops[0],
                        "_strategy": "isolation_matrix",
                    },
                    [a_id, b_id],
                    ent_ops[:3],
                    ent_src + _list(actor_a.get("source_refs")) + _list(actor_b.get("source_refs")),
                )

    # ── Strategy 5: declared idempotency invariant × its operation ──
    # A write method is not evidence that an idempotency contract exists. The
    # source has to state one, and it has to name the operation it governs.
    for inv in invariants:
        if _invariant_kind(inv) != "idempotency":
            continue
        inv_id = _text(inv.get("id"))
        inv_src = _list(inv.get("source_refs"))
        for op_id in _invariant_operation_refs(inv, op_by_id, operations):
            op = op_by_id.get(op_id) or {}
            if _infer_operation_effect(op, _text(op.get("method")).upper()) != "write":
                continue
            _add_obligation(
                "consistency",
                [inv_id, op_id, "idempotency"],
                {
                    "template": "idempotent_write_verification",
                    "invariant_ref": inv_id,
                    "operation_ref": op_id,
                    "operation_path_prefix": _text(op.get("path")),
                    "repeat_count": 2,
                    "_strategy": "idempotency_matrix",
                },
                [_text(active_actors[0].get("id"))] if active_actors else [],
                [op_id],
                inv_src + _list(op.get("source_refs")),
                cleanup="required",
            )

    # ── Strategy 6: Conservation relations × concurrent write → consistency ──
    # The IR emits this relation as "conserves"; the older spellings never
    # matched anything, so this strategy silently produced nothing.
    for rel in relations:
        rel_id = _text(rel.get("id"))
        rel_type = _text(rel.get("type") or rel.get("relation_type")).lower()
        if rel_type not in {
            "conserves",
            "conservation",
            "balance",
            "sum_constraint",
            "invariant_bound",
        }:
            continue
        rel_src = _list(rel.get("source_refs"))
        rel_ops = [_text(r) for r in _list(rel.get("operation_refs")) if _text(r)]
        if not rel_ops:
            # Try from_ref / to_ref
            from_ref = _text(rel.get("from_ref") or rel.get("operation_ref"))
            if from_ref:
                rel_ops = [from_ref]
        _add_obligation(
            "consistency",
            [rel_id or f"rel_{_coverage_signature(rel_type, *rel_ops)}", "concurrent"],
            {
                "template": "conservation_under_concurrency",
                "relation_ref": rel_id,
                "operation_refs": rel_ops,
                "concurrent_participants": 2,
                "_strategy": "conservation_matrix",
            },
            [_text(active_actors[0].get("id"))] if active_actors else [],
            rel_ops[:3],
            rel_src,
            cleanup="required",
        )

    # ── Strategy 7: Invariant × the operation that can violate it ──
    # An invariant with no operation cannot be exercised: there is nothing to
    # send. Those are reported as a coverage gap instead of becoming an
    # obligation that is certain to block.
    for inv in invariants:
        inv_id = _text(inv.get("id"))
        if not inv_id:
            continue
        inv_src = _list(inv.get("source_refs"))
        inv_kind = _invariant_kind(inv)
        if inv_kind == "postcondition":
            expression = _dict(inv.get("expression"))
            operands = _list(expression.get("operands"))

            def _has_expected_value(operand: dict[str, Any]) -> bool:
                value = operand.get("expected_value")
                return value is not None and (
                    not isinstance(value, str) or bool(value.strip())
                )

            has_bound_effect = any(
                isinstance(operand, dict)
                and (
                    _text(operand.get("field_id") or operand.get("field"))
                    or _has_expected_value(operand)
                    or bool(operand.get("must_create"))
                )
                for operand in operands
            )
            if not has_bound_effect:
                # A prose-only postcondition is evidence that a rule was
                # extracted, not an executable oracle.  Keep it in the source
                # and coverage-gap receipts; do not schedule a generic
                # invariant obligation that can only fail later with a
                # fabricated state-transition assertion.
                skipped["invariant_postcondition_unbound"] += 1
                continue
        inv_ops = _invariant_operation_refs(inv, op_by_id, operations)
        if not inv_ops:
            skipped["invariant_not_bound_to_operation"] += 1
            continue
        expression = _dict(inv.get("expression"))
        if not expression and inv_kind:
            expression = {"kind": inv_kind}
        family = _risk_family_for_matrix_invariant(inv_kind)
        executable_kind = (
            inv_kind.lower()
            if inv_kind.lower() in _EXECUTABLE_INVARIANT_ASSERTION_KINDS
            else _text(expression.get("kind")).lower()
        )
        if executable_kind not in _EXECUTABLE_INVARIANT_ASSERTION_KINDS:
            executable_kind = ""
        _add_obligation(
            family,
            [inv_id],
            {
                "template": "invariant_violation_detection",
                "invariant_ref": inv_id,
                "operation_refs": inv_ops,
                "operation_ref": inv_ops[0],
                "invariant_kind": executable_kind,
                "expression": expression,
                "_strategy": "invariant_matrix",
            },
            [_text(active_actors[0].get("id"))] if active_actors else [],
            inv_ops[:3],
            inv_src,
        )

    if report is not None:
        report["matrix_skipped"] = dict(skipped)
        report["matrix_skipped_total"] = sum(skipped.values())
    return obligations

