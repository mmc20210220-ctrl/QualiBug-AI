"""Behavior IR → Hypothesis coverage gap analysis.

Produces source-backed hypotheses for Behavior IR nodes that have zero
coverage from the LLM reasoner engines. Every generated hypothesis carries
explicit ``source_refs`` traced back to the originating Behavior IR node.

Schema: qualibug.behavior-ir-hypothesis-coverage.v1

This module is industry-neutral. It must never reference benchmark bug IDs,
match keywords, hidden ground truth, customer names, or fixed API paths.
"""

from __future__ import annotations

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


COVERAGE_SCHEMA = "qualibug.behavior-ir-hypothesis-coverage.v1"

# Maximum additional hypotheses generated per coverage run.
# Kept small to avoid overwhelming the existing engine output.
MAX_COVERAGE_HYPOTHESES = 30

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

    # ── Operation coverage nodes ──
    for op in operations:
        op_id = _text(op.get("id"))
        if not op_id:
            continue
        method = _text(op.get("method")).upper()
        path = _text(op.get("path"))
        side_effect = _text(op.get("side_effect_class")).lower()
        entity_refs = [ref for ref in _list(op.get("entity_refs")) if _text(ref)]
        source_refs = _list(op.get("source_refs"))

        families: list[str] = []

        # Write operations: authorization, isolation, consistency, state, lifecycle
        if side_effect == "write":
            families.extend(["authorization", "isolation", "consistency", "state_integrity", "lifecycle"])
            # Also check for invariants that reference this operation
            families.append("invariant")

        # Read operations: visibility (can others see this?)
        if method in ("GET", "HEAD"):
            families.append("visibility")

        families = sorted(set(families))
        if not families:
            continue

        for family in families:
            coverage_id = f"cov_op_{op_id}_{family}"
            if coverage_id in seen_ids:
                continue
            seen_ids.add(coverage_id)
            nodes.append({
                "coverage_id": coverage_id,
                "node_type": "operation",
                "ir_node_id": op_id,
                "risk_family": family,
                "operation_method": method,
                "operation_path": path,
                "entity_refs": entity_refs,
                "source_refs": source_refs,
                "coverage_signature": _coverage_signature(op_id, family, method, path),
            })

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
            side_effect = _text(op.get("side_effect_class")).lower()
            if side_effect == "write":
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
        operation_refs = [ref for ref in _list(inv.get("operation_refs")) if _text(ref)]
        source_refs = _list(inv.get("source_refs"))

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
    for rel in relations:
        rel_id = _text(rel.get("id"))
        rel_type = _text(rel.get("relation_type"))
        if not rel_id or not rel_type:
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
        if len(hypotheses) >= max(1, min(int(max_hypotheses), MAX_COVERAGE_HYPOTHESES)):
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
        if len(obligations) >= max(1, min(int(max_obligations), MAX_COVERAGE_HYPOTHESES)):
            break

        family = _text(node.get("risk_family"))
        node_type = _text(node.get("node_type"))
        # For actor_operation nodes, the real operation id is in "operation_id"
        if node_type == "actor_operation":
            op_id = _text(node.get("operation_id"))
        else:
            op_id = _text(node.get("ir_node_id"))

        # Validate: only generate obligations for coverage nodes that reference
        # real operations in the Behavior IR, or are relation/invariant/state
        # nodes that don't require a specific operation.
        op_refs: list[str] = []
        if node_type == "invariant":
            op_refs = [ref for ref in _list(node.get("operation_refs")) if _text(ref) in valid_op_ids]
        elif node_type == "relation":
            # Relation nodes may reference an operation; validate it
            rel_op_ref = _text(node.get("operation_ref"))
            if rel_op_ref and rel_op_ref in valid_op_ids:
                op_refs = [rel_op_ref]
        elif node_type == "state":
            # State nodes reference entities, not operations directly
            op_refs = []
        elif op_id and op_id in valid_op_ids:
            op_refs = [op_id]
        elif node_type in ("operation", "actor_operation"):
            # Operation node whose operation ID doesn't exist → skip
            continue
        # For other node types without operation references, it's ok to have empty op_refs

        op_path = _text(node.get("operation_path"))
        op_method = _text(node.get("operation_method"))
        actor_id = _text(node.get("actor_id"))
        source_refs = _list(node.get("source_refs"))
        coverage_id = _text(node.get("coverage_id"))

        # Determine property template
        template_map: dict[str, str] = {
            "authorization": "permitted_operation_invocation",
            "isolation": "owner_viewer_isolation",
            "visibility": "permitted_operation_invocation",
            "consistency": "single_dimension_mutation",
            "state_integrity": "state_transition_boundary",
        }
        template = template_map.get(family, "permitted_operation_invocation")

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

        # Observer determination
        observer_map: dict[str, list[str]] = {
            "authorization": ["http_response", "actor_identity"],
            "isolation": ["http_response", "resource_ownership"],
            "visibility": ["http_response", "actor_identity"],
            "consistency": ["http_response", "entity_state"],
            "state_integrity": ["http_response", "entity_state"],
            "lifecycle": ["http_response", "entity_state"],
            "invariant": ["http_response", "entity_state"],
        }
        required_observers = observer_map.get(family, ["http_response"])

        # Cleanup requirement for writes
        side_effect = _text(node.get("side_effect_class") or "").lower()
        is_write = bool(op_method and op_method.upper() in {"POST", "PUT", "PATCH", "DELETE"})
        cleanup_requirement = "required" if is_write else "not_required"

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

        try:
            obl = make_obligation(
                risk_family=family,
                subject_refs=op_refs + ([actor_ref] if actor_ref else []),
                property_spec=property_spec,
                required_actors=required_actors,
                required_operations=op_refs,
                required_observers=required_observers,
                cleanup_requirement=cleanup_requirement,
                source_refs=source_refs,
                confidence=0.5,
            )
        except Exception:
            # Build minimal obligation if make_obligation rejects
            raw_id = f"obl_cov_{coverage_id}"
            obl = {
                "obligation_id": f"obl_{hashlib.sha256(raw_id.encode()).hexdigest()[:16]}",
                "risk_family": family,
                "subject_refs": op_refs,
                "property_spec": property_spec,
                "required_actors": required_actors,
                "required_operations": op_refs,
                "required_observers": required_observers,
                "cleanup_requirement": cleanup_requirement,
                "source_refs": source_refs,
                "confidence": 0.5,
                "_coverage_obligation": True,
            }

        obl["_coverage_obligation"] = True
        obligations.append(obl)

    return obligations

