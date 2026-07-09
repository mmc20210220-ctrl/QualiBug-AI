"""Learning Generator — Generate NEW probes, oracles, and fixtures from confirmed bugs.

This module proves that learning is NOT just re-sorting existing probes.
It takes confirmed bug findings and generates genuinely NEW test artifacts:
  1. Variant probes: role/entity/endpoint/parameter variants of the confirmed bug
  2. Sibling oracles: apply the same oracle pattern to sibling endpoints
  3. Reproduction fixtures: minimal test data to reproduce the confirmed bug
  4. Regression fixtures: test data for regression guard

Every generated artifact carries a traceable source: which confirmed bug it came
from, what mutation strategy was applied, and why.

Design principles (per AGENTS.md):
  - From real confirmed bugs only: never fabricates probes from thin air
  - Traceable: LearningManifest records source_bug_id → generated_artifact
  - Dedup: checks for existing probes before generating duplicates
  - Industry-agnostic: variant logic based on generic patterns (role, entity, param)
  - Safe: generated probes inherit execution_policy from source
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ═════════════════════════════════════════════════════════════════════════════
# Enums
# ═════════════════════════════════════════════════════════════════════════════

class GenerationStrategy(str, Enum):
    """How a new artifact was generated from a confirmed bug."""
    ROLE_VARIANT = "role_variant"             # Same endpoint, different actor
    ENTITY_VARIANT = "entity_variant"          # Same risk_type, different entity
    ENDPOINT_VARIANT = "endpoint_variant"      # Same oracle, sibling endpoint
    PARAMETER_VARIANT = "parameter_variant"    # Boundary/mutation on params
    CROSS_ENTITY_ORACLE = "cross_entity_oracle"  # Apply oracle to sibling entities
    DERIVED_ORACLE = "derived_oracle"           # Derive new oracle from pattern
    REPRODUCTION_FIXTURE = "reproduction_fixture"
    REGRESSION_FIXTURE = "regression_fixture"


class ArtifactKind(str, Enum):
    PROBE = "probe"
    ORACLE = "oracle"
    FIXTURE = "fixture"


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class GeneratedProbe:
    """A new probe generated from a confirmed bug."""
    probe_id: str
    title: str
    risk_type: str
    severity: str
    actor: str
    method: str
    path: str
    expected_status: int
    source_bug_id: str               # Which confirmed bug this came from
    strategy: GenerationStrategy     # How it was generated
    rationale: str                   # Why this probe was generated
    execution_policy: str = "candidate_only"
    evidence_required: list[str] = field(default_factory=lambda: [
        "actor_role", "request", "response_status", "response_body"
    ])
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedOracle:
    """A new oracle rule generated from a confirmed bug pattern."""
    oracle_id: str
    oracle_name: str
    layer: str                      # L1-L6
    rule_description: str
    applies_to_method: str
    applies_to_path_pattern: str
    expected_behavior: str
    violation_signal: str
    source_bug_id: str
    strategy: GenerationStrategy
    rationale: str
    severity: str = "P1"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedFixture:
    """A new fixture plan generated from a confirmed bug."""
    fixture_id: str
    entity_type: str
    purpose: str                    # "reproduction" | "regression"
    setup_requests: list[dict[str, Any]]
    required_fields: dict[str, Any]
    cleanup_requests: list[dict[str, Any]]
    source_bug_id: str
    strategy: GenerationStrategy
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LearningManifest:
    """Tracks all generated artifacts from a learning run."""
    manifest_id: str
    generated_at: str
    source_bug_count: int
    generated_probes: list[GeneratedProbe] = field(default_factory=list)
    generated_oracles: list[GeneratedOracle] = field(default_factory=list)
    generated_fixtures: list[GeneratedFixture] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════════
# Mutation Strategies (industry-agnostic)
# ═════════════════════════════════════════════════════════════════════════════

# HTTP methods that indicate a write operation
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Role escalation ladder: lower → higher privilege.
# NOTE: This is a generic default. Override per-industry via project_context["role_hierarchy"].
ROLE_ESCALATION: list[list[str]] = [
    ["anonymous", "guest"],
    ["normal_user", "customer", "member", "user"],
    ["seller", "agent", "operator", "staff"],
    ["manager", "supervisor", "team_lead"],
    ["admin", "owner", "super_admin", "platform_admin"],
    ["auditor", "compliance_officer"],
]

# Oracle families that apply across entities
CROSS_ENTITY_ORACLE_FAMILIES = {
    "state_flow": "State transitions must be valid for all lifecycle entities",
    "permission_bypass": "Authorization must be enforced on all protected endpoints",
    "idor": "Cross-user access must be blocked on all resource endpoints",
    "tenant_isolation": "Tenant boundaries must be enforced on all multi-tenant endpoints",
    "idempotency": "Duplicate requests must not create duplicate side effects on all POST endpoints",
    "data_consistency": "API responses must match persisted state for all entities",
    "input_validation": "Invalid input must be rejected on all write endpoints",
}

# Parameter mutation templates by risk_type
PARAMETER_MUTATIONS: dict[str, list[dict[str, Any]]] = {
    "input_validation": [
        {"field_strategy": "negative", "value": -1, "expected": 400},
        {"field_strategy": "zero", "value": 0, "expected": 400},
        {"field_strategy": "overflow", "value": 999999999, "expected": 400},
    ],
    "idor": [
        {"field_strategy": "other_user_id", "value": "<OTHER_USER_ID>", "expected": 403},
    ],
    "tenant_isolation": [
        {"field_strategy": "other_tenant_id", "value": "<OTHER_TENANT_ID>", "expected": 403},
    ],
    "state_flow": [
        {"field_strategy": "terminal_state_action", "action": "cancel", "expected": 409},
        {"field_strategy": "terminal_state_action", "action": "delete", "expected": 409},
    ],
    "idempotency": [
        {"field_strategy": "duplicate_key", "value": "<SAME_KEY>", "expected": 200},
    ],
}


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _make_id(prefix: str, *parts: str) -> str:
    """Generate a stable, human-readable ID."""
    raw = f"{prefix}:{':'.join(str(p) for p in parts)}"
    short_hash = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"{prefix}_{short_hash}"


def _extract_entity_from_path(path: str) -> str:
    """Extract the entity name from an API path."""
    if not path or path == "/":
        return "resource"
    # e.g., /api/v1/orders/123 → orders
    # e.g., /admin/products → products
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{") and not p.isdigit()]
    # Skip common API prefixes — version-agnostic using regex
    import re as _re
    skip_pattern = _re.compile(r'^(api|v\d+|[a-z]{2}(-[A-Z]{2})?|admin|internal|public|private|web|gateway|proxy|services|platform)$')
    for p in parts:
        if not skip_pattern.match(p.lower()):
            return p
    return parts[-1] if parts else "resource"


def _extract_actor_from_finding(finding: dict[str, Any]) -> str:
    """Extract the actor/role from a confirmed finding."""
    # Try multiple possible locations
    actor = finding.get("actor") or ""
    if not actor:
        variant = finding.get("variant_dimensions") or {}
        actor = variant.get("actor", "")
    if not actor:
        repro = finding.get("reproduction") or {}
        actor = repro.get("actor", "")
    if not actor:
        # Try to infer from title
        title = str(finding.get("title", "")).lower()
        for role_group in ROLE_ESCALATION:
            for role in role_group:
                if role in title:
                    return role
    return actor or "normal_user"  # Sensible default for variant generation


def _normalize_method_path(method: str, path: str) -> str:
    """Normalize method+path for dedup."""
    norm_path = re.sub(r"/\d+", "/{id}", path.strip().lower().rstrip("/"))
    norm_path = re.sub(r"/\{[^}]+\}", "/{id}", norm_path)
    return f"{method.lower()}:{norm_path}"


def _role_level(actor: str) -> int:
    """Return the privilege level of a role (higher = more privileged)."""
    actor_lower = actor.lower()
    for i, group in enumerate(ROLE_ESCALATION):
        if any(r in actor_lower for r in group):
            return i
    return 2  # default middle


def _lower_roles(actor: str) -> list[str]:
    """Return roles with lower privilege than the given actor."""
    level = _role_level(actor)
    if level <= 0:
        return []  # No lower roles for lowest-privilege actors
    result = []
    for group in ROLE_ESCALATION[:level]:
        result.extend(group)
    return result


def _higher_roles(actor: str) -> list[str]:
    """Return roles with higher privilege than the given actor."""
    level = _role_level(actor)
    result = []
    for group in ROLE_ESCALATION[level + 1:]:
        result.extend(group)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Probe Generator
# ═════════════════════════════════════════════════════════════════════════════

class ProbeGenerator:
    """Generate new probe variants from confirmed bugs."""

    def __init__(self, existing_probes: list[dict[str, Any]] | None = None):
        self._existing_keys: set[str] = set()
        if existing_probes:
            for p in existing_probes:
                key = _normalize_method_path(
                    p.get("method", "GET"),
                    p.get("path", ""),
                )
                actor = p.get("actor", "")
                risk = p.get("risk_type", "")
                self._existing_keys.add(f"{key}:{actor}:{risk}")

    def _is_duplicate(self, method: str, path: str, actor: str, risk_type: str) -> bool:
        key = f"{_normalize_method_path(method, path)}:{actor}:{risk_type}"
        return key in self._existing_keys

    def _register(self, method: str, path: str, actor: str, risk_type: str) -> None:
        key = f"{_normalize_method_path(method, path)}:{actor}:{risk_type}"
        self._existing_keys.add(key)

    # ── Role Variants ──────────────────────────────────────────────────

    def generate_role_variants(
        self,
        confirmed_bug: dict[str, Any],
        available_roles: list[str] | None = None,
    ) -> list[GeneratedProbe]:
        """Generate probes with different actor roles.

        If the bug is a permission_bypass where a low-privilege user accessed
        admin data, generate variants with other low-privilege roles.
        If it's a privilege_escalation, try even higher roles.
        """
        results: list[GeneratedProbe] = []
        risk_type = str(confirmed_bug.get("risk_type", ""))
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        severity = str(confirmed_bug.get("severity", "P1"))
        source_actor = _extract_actor_from_finding(confirmed_bug)
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))

        # Determine which roles to try
        if risk_type in ("permission_bypass", "auth_bypass", "privilege_escalation"):
            # Bug is about unauthorized access — try lower-privilege roles
            variant_roles = _lower_roles(source_actor)
        elif risk_type == "idor":
            # IDOR — try different roles at same level
            variant_roles = [r for g in ROLE_ESCALATION for r in g if r != source_actor][:5]
        else:
            # General case — try roles from different levels
            variant_roles = (
                _lower_roles(source_actor)[:2] + _higher_roles(source_actor)[:2]
            )

        # Filter to available roles if specified
        if available_roles:
            variant_roles = [r for r in variant_roles if r in available_roles]

        for role in variant_roles:
            if self._is_duplicate(method, path, role, risk_type):
                continue

            probe = GeneratedProbe(
                probe_id=_make_id("LRN-RV", bug_id, role, risk_type),
                title=f"[Learning] {role} → {method} {path} (from {risk_type} bug)",
                risk_type=risk_type,
                severity=severity,
                actor=role,
                method=method,
                path=path,
                expected_status=confirmed_bug.get("expected_status", 403),
                source_bug_id=bug_id,
                strategy=GenerationStrategy.ROLE_VARIANT,
                rationale=f"Role variant of confirmed {risk_type} bug {bug_id}: testing if {role} also exhibits the same defect",
                execution_policy=confirmed_bug.get("execution_policy", "candidate_only"),
            )
            self._register(method, path, role, risk_type)
            results.append(probe)

        return results

    # ── Entity Variants ────────────────────────────────────────────────

    def generate_entity_variants(
        self,
        confirmed_bug: dict[str, Any],
        all_entities: list[str],
    ) -> list[GeneratedProbe]:
        """Generate probes for the same risk_type on different entities.

        If a state_flow bug was found on /orders/{id}/cancel, generate
        similar probes for /products/{id}/disable, /users/{id}/deactivate, etc.
        """
        results: list[GeneratedProbe] = []
        risk_type = str(confirmed_bug.get("risk_type", ""))
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        severity = str(confirmed_bug.get("severity", "P1"))
        actor = _extract_actor_from_finding(confirmed_bug)
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))
        source_entity = _extract_entity_from_path(path)

        # Only generate entity variants for risk types that apply across entities
        if risk_type not in CROSS_ENTITY_ORACLE_FAMILIES:
            return results

        for entity in all_entities:
            if entity == source_entity:
                continue

            # Replace the source entity with the target entity in the path
            # Use path-segment replacement to avoid substring issues (e.g., "order" in "orders")
            path_segments = path.strip("/").split("/")
            new_segments = []
            replaced = False
            for seg in path_segments:
                # Only replace if the segment matches the source entity exactly or with 's' suffix
                if seg == source_entity:
                    new_segments.append(entity)
                    replaced = True
                elif seg == source_entity + "s":
                    new_segments.append(entity + "s")
                    replaced = True
                elif seg.rstrip("s") == source_entity:
                    new_segments.append(entity + ("s" if seg.endswith("s") else ""))
                    replaced = True
                else:
                    new_segments.append(seg)
            new_path = "/" + "/".join(new_segments)
            if not replaced or new_path == path:
                continue

            if self._is_duplicate(method, new_path, actor, risk_type):
                continue

            probe = GeneratedProbe(
                probe_id=_make_id("LRN-EV", bug_id, entity, risk_type),
                title=f"[Learning] {risk_type} on {entity} (from {source_entity} bug)",
                risk_type=risk_type,
                severity=severity,
                actor=actor,
                method=method,
                path=new_path,
                expected_status=confirmed_bug.get("expected_status", 403),
                source_bug_id=bug_id,
                strategy=GenerationStrategy.ENTITY_VARIANT,
                rationale=f"Entity variant: confirmed {risk_type} on {source_entity}, testing {entity}",
                execution_policy=confirmed_bug.get("execution_policy", "candidate_only"),
            )
            self._register(method, new_path, actor, risk_type)
            results.append(probe)

        return results

    # ── Endpoint Variants ──────────────────────────────────────────────

    def generate_endpoint_variants(
        self,
        confirmed_bug: dict[str, Any],
        all_endpoints: list[dict[str, str]],
    ) -> list[GeneratedProbe]:
        """Generate probes for sibling endpoints with the same oracle pattern.

        If an idempotency bug was found on POST /orders, generate similar
        probes for POST /payments, POST /refunds, etc.
        """
        results: list[GeneratedProbe] = []
        risk_type = str(confirmed_bug.get("risk_type", ""))
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        severity = str(confirmed_bug.get("severity", "P1"))
        actor = _extract_actor_from_finding(confirmed_bug)
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))

        if risk_type not in CROSS_ENTITY_ORACLE_FAMILIES:
            return results

        for ep in all_endpoints:
            if not isinstance(ep, dict):
                continue
            ep_method = str(ep.get("method", "")).upper()
            ep_path = str(ep.get("path", ""))

            # Skip the source endpoint itself
            if ep_method == method and ep_path == path:
                continue

            # Only match endpoints with the same HTTP method
            if ep_method != method:
                continue

            if self._is_duplicate(ep_method, ep_path, actor, risk_type):
                continue

            probe = GeneratedProbe(
                probe_id=_make_id("LRN-EPV", bug_id, ep_method, ep_path[:30]),
                title=f"[Learning] {risk_type} on {ep_method} {ep_path}",
                risk_type=risk_type,
                severity=severity,
                actor=actor,
                method=ep_method,
                path=ep_path,
                expected_status=confirmed_bug.get("expected_status", 403),
                source_bug_id=bug_id,
                strategy=GenerationStrategy.ENDPOINT_VARIANT,
                rationale=f"Endpoint variant: confirmed {risk_type} on {method} {path}, testing sibling {ep_method} {ep_path}",
                execution_policy=confirmed_bug.get("execution_policy", "candidate_only"),
            )
            self._register(ep_method, ep_path, actor, risk_type)
            results.append(probe)

        return results

    # ── Parameter Variants ─────────────────────────────────────────────

    def generate_parameter_variants(
        self,
        confirmed_bug: dict[str, Any],
    ) -> list[GeneratedProbe]:
        """Generate probes with mutated parameters based on risk_type."""
        results: list[GeneratedProbe] = []
        risk_type = str(confirmed_bug.get("risk_type", ""))
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        severity = str(confirmed_bug.get("severity", "P1"))
        actor = _extract_actor_from_finding(confirmed_bug)
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))

        mutations = PARAMETER_MUTATIONS.get(risk_type, [])
        for mutation in mutations:
            strategy_label = str(mutation.get("field_strategy", "unknown"))

            probe = GeneratedProbe(
                probe_id=_make_id("LRN-PV", bug_id, strategy_label),
                title=f"[Learning] {risk_type} {strategy_label} on {method} {path}",
                risk_type=risk_type,
                severity=severity,
                actor=actor,
                method=method,
                path=path,
                expected_status=mutation.get("expected", 400),
                source_bug_id=bug_id,
                strategy=GenerationStrategy.PARAMETER_VARIANT,
                rationale=f"Parameter variant: {strategy_label} mutation for confirmed {risk_type} bug",
                execution_policy=confirmed_bug.get("execution_policy", "candidate_only"),
                metadata={"mutation": mutation},
            )
            self._register(method, path, actor, risk_type)
            results.append(probe)

        return results

    # ── All Variants ───────────────────────────────────────────────────

    def generate_all_variants(
        self,
        confirmed_bug: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedProbe]:
        """Generate all variant probes for a confirmed bug."""
        all_probes: list[GeneratedProbe] = []

        all_probes.extend(self.generate_role_variants(
            confirmed_bug,
            available_roles=context.get("roles"),
        ))
        all_probes.extend(self.generate_entity_variants(
            confirmed_bug,
            all_entities=context.get("entities", []),
        ))
        all_probes.extend(self.generate_endpoint_variants(
            confirmed_bug,
            all_endpoints=context.get("endpoints", []),
        ))
        all_probes.extend(self.generate_parameter_variants(confirmed_bug))

        return all_probes


# ═════════════════════════════════════════════════════════════════════════════
# Oracle Generator
# ═════════════════════════════════════════════════════════════════════════════

class OracleGenerator:
    """Generate new oracle rules from confirmed bug patterns."""

    def __init__(self):
        self._generated_ids: set[str] = set()

    # ── Sibling Oracles ────────────────────────────────────────────────

    def generate_sibling_oracles(
        self,
        confirmed_bug: dict[str, Any],
        all_endpoints: list[dict[str, str]],
    ) -> list[GeneratedOracle]:
        """Apply the confirmed bug's oracle pattern to sibling endpoints.

        If a state_flow oracle caught a bug on GET /orders/{id}, generate
        similar oracle rules for GET /products/{id}, GET /users/{id}, etc.
        """
        results: list[GeneratedOracle] = []
        risk_type = str(confirmed_bug.get("risk_type", ""))
        oracle_data = confirmed_bug.get("oracle") or {}
        oracle_type = str(oracle_data.get("type", risk_type))
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))
        expected_status = confirmed_bug.get("expected_status", 200)
        bug_signal = str(oracle_data.get("bug_signal", ""))

        if risk_type not in CROSS_ENTITY_ORACLE_FAMILIES:
            return results

        layer = self._layer_for_risk_type(risk_type)

        for ep in all_endpoints:
            if not isinstance(ep, dict):
                continue
            ep_method = str(ep.get("method", "")).upper()
            ep_path = str(ep.get("path", ""))

            if ep_method == method and ep_path == path:
                continue

            oracle_id = _make_id("LRN-OR", bug_id, oracle_type, ep_method, ep_path[:20])
            if oracle_id in self._generated_ids:
                continue
            self._generated_ids.add(oracle_id)

            oracle = GeneratedOracle(
                oracle_id=oracle_id,
                oracle_name=f"Learned{oracle_type.replace('_', ' ').title()}Oracle",
                layer=layer,
                rule_description=f"Apply {oracle_type} check on {ep_method} {ep_path}",
                applies_to_method=ep_method,
                applies_to_path_pattern=ep_path,
                expected_behavior=f"Should return {expected_status} or reject",
                violation_signal=bug_signal or f"status_code != {expected_status}",
                source_bug_id=bug_id,
                strategy=GenerationStrategy.CROSS_ENTITY_ORACLE,
                rationale=f"Sibling oracle: confirmed {oracle_type} on {method} {path}, extends to {ep_method} {ep_path}",
                severity=confirmed_bug.get("severity", "P1"),
            )
            results.append(oracle)

        return results

    # ── Derived Oracles ────────────────────────────────────────────────

    def generate_derived_oracles(
        self,
        confirmed_bug: dict[str, Any],
    ) -> list[GeneratedOracle]:
        """Derive new oracle types from the confirmed bug pattern.

        If a permission_bypass bug was found, derive oracles for:
        - auth_bypass (check if unauthenticated access is possible)
        - privilege_escalation (check if role escalation is possible)
        """
        results: list[GeneratedOracle] = []
        risk_type = str(confirmed_bug.get("risk_type", ""))
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))

        # Derivation map: risk_type → related oracle types to generate
        derivations: dict[str, list[tuple[str, str, str]]] = {
            "permission_bypass": [
                ("auth_bypass", "L4", "Check if unauthenticated access is also possible"),
                ("privilege_escalation", "L4", "Check if role escalation is also possible"),
            ],
            "idor": [
                ("permission_bypass", "L4", "Check if unauthorized role access exists"),
                ("visibility_disclosure", "L2", "Check if IDOR leads to data leakage in lists"),
            ],
            "state_flow": [
                ("state_consistency", "L2", "Check if state inconsistency exists after transition"),
                ("workflow_bypass", "L3", "Check if workflow steps can be skipped"),
            ],
            "idempotency": [
                ("concurrency", "L5", "Check if race condition exists on same endpoint"),
            ],
            "money_consistency": [
                ("input_validation", "L1", "Check if negative amounts are accepted"),
                ("audit_traceability", "L6", "Check if money changes are audited"),
            ],
            "tenant_isolation": [
                ("visibility_disclosure", "L2", "Check if cross-tenant data leaks in exports"),
            ],
        }

        derived = derivations.get(risk_type, [])
        for derived_type, layer, rationale_template in derived:
            oracle_id = _make_id("LRN-DR", bug_id, risk_type, derived_type)
            if oracle_id in self._generated_ids:
                continue
            self._generated_ids.add(oracle_id)

            oracle = GeneratedOracle(
                oracle_id=oracle_id,
                oracle_name=f"Derived{derived_type.replace('_', ' ').title()}Oracle",
                layer=layer,
                rule_description=f"Derived {derived_type} check from {risk_type} bug on {method} {path}",
                applies_to_method=method,
                applies_to_path_pattern=path,
                expected_behavior=f"Should enforce {derived_type} invariant",
                violation_signal=f"status_code indicates {derived_type} violation",
                source_bug_id=bug_id,
                strategy=GenerationStrategy.DERIVED_ORACLE,
                rationale=f"{rationale_template} (derived from {risk_type} bug {bug_id})",
                severity=confirmed_bug.get("severity", "P1"),
            )
            results.append(oracle)

        return results

    # ── All Oracles ────────────────────────────────────────────────────

    def generate_all_oracles(
        self,
        confirmed_bug: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedOracle]:
        """Generate all oracle variants for a confirmed bug."""
        all_oracles: list[GeneratedOracle] = []

        all_oracles.extend(self.generate_sibling_oracles(
            confirmed_bug,
            all_endpoints=context.get("endpoints", []),
        ))
        all_oracles.extend(self.generate_derived_oracles(confirmed_bug))

        return all_oracles

    @staticmethod
    def _layer_for_risk_type(risk_type: str) -> str:
        mapping = {
            "permission_bypass": "L4", "auth_bypass": "L4", "privilege_escalation": "L4",
            "idor": "L4", "tenant_isolation": "L4",
            "state_flow": "L3", "state_consistency": "L2", "workflow_bypass": "L3",
            "money_consistency": "L3", "idempotency": "L5", "concurrency": "L5",
            "data_consistency": "L2", "input_validation": "L1",
            "audit_traceability": "L6", "visibility_disclosure": "L2",
        }
        return mapping.get(risk_type, "L3")


# ═════════════════════════════════════════════════════════════════════════════
# Fixture Generator
# ═════════════════════════════════════════════════════════════════════════════

class FixtureGenerator:
    """Generate test data fixtures from confirmed bugs."""

    def generate_reproduction_fixture(
        self,
        confirmed_bug: dict[str, Any],
    ) -> GeneratedFixture | None:
        """Generate minimal test data to reproduce the confirmed bug.

        Uses the bug's trigger conditions and method/path to construct
        setup requests that create the preconditions for the bug.
        """
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))
        entity = _extract_entity_from_path(path)
        variant = confirmed_bug.get("variant_dimensions") or {}

        # Build setup requests to create preconditions
        setup_requests: list[dict[str, Any]] = []

        # If write method, we need a resource to operate on
        # For DELETE, the resource must already exist — create it first
        if method in WRITE_METHODS:
            # Determine the creation endpoint (strip the ID for DELETE paths)
            if method == "DELETE":
                # DELETE /api/orders/123 → create via POST /api/orders
                create_path = "/".join(path.strip("/").split("/")[:-1]) if "/" in path else path
                setup_requests.append({
                    "purpose": "create_resource_for_delete_test",
                    "method": "POST",
                    "path": create_path,
                    "body": {
                        # NOTE: body fields (name, source, disposable) are generic defaults.
# Override via confirmed_bug["fixture_fields"] for industry-specific entities.
                        "name": f"qb_learning_test_{bug_id[:8]}",
                        "source": "learning_generator",
                        "disposable": True,
                    },
                })
            else:
                setup_requests.append({
                    "purpose": "create_test_resource",
                    "method": "POST",
                    "path": path,
                    "body": {
                        "name": f"qb_learning_test_{bug_id[:8]}",
                        "source": "learning_generator",
                        "disposable": True,
                    },
                })

        # Build required fields from variant dimensions
        required_fields: dict[str, Any] = {
            "entity": entity,
            "method": method,
            "path": path,
        }
        if variant.get("actor"):
            required_fields["actor"] = variant["actor"]
        if variant.get("auth_state"):
            required_fields["auth_state"] = variant["auth_state"]
        if variant.get("tenant_scope"):
            required_fields["tenant_scope"] = variant["tenant_scope"]

        # Cleanup: delete created resources
        cleanup_requests: list[dict[str, Any]] = []
        if setup_requests:
            cleanup_requests.append({
                "purpose": "cleanup_test_resource",
                "method": "DELETE",
                "path": f"{path}/{{created_id}}",
            })

        return GeneratedFixture(
            fixture_id=_make_id("LRN-FIX", bug_id, "repro"),
            entity_type=entity,
            purpose="reproduction",
            setup_requests=setup_requests,
            required_fields=required_fields,
            cleanup_requests=cleanup_requests,
            source_bug_id=bug_id,
            strategy=GenerationStrategy.REPRODUCTION_FIXTURE,
            rationale=f"Minimal reproduction fixture for confirmed bug {bug_id}: {method} {path}",
        )

    def generate_regression_fixture(
        self,
        confirmed_bug: dict[str, Any],
    ) -> GeneratedFixture | None:
        """Generate test data for regression testing of a fixed bug.

        Includes both the preconditions and the expected correct behavior
        after the fix.
        """
        method = str(confirmed_bug.get("method", "GET")).upper()
        path = str(confirmed_bug.get("path") or confirmed_bug.get("api", ""))
        bug_id = str(confirmed_bug.get("bug_id") or confirmed_bug.get("evidence_id", ""))
        entity = _extract_entity_from_path(path)
        expected_status = confirmed_bug.get("expected_status", 403)

        # Regression fixture: create preconditions, verify fix
        setup_requests: list[dict[str, Any]] = [
            {
                "purpose": "create_regression_test_resource",
                "method": "POST",
                "path": path,
                "body": {
                    "name": f"qb_regression_test_{bug_id[:8]}",
                    "source": "learning_generator_regression",
                    "disposable": True,
                },
            },
            {
                "purpose": "verify_bug_is_fixed",
                "method": method,
                "path": path,
                "expected_status_after_fix": expected_status,
                "note": f"This should return {expected_status} if the bug is fixed",
            },
        ]

        required_fields: dict[str, Any] = {
            "entity": entity,
            "method": method,
            "path": path,
            "expected_fixed_behavior": f"Should return {expected_status}",
            "original_bug_behavior": confirmed_bug.get("actual_bug_behavior", ""),
        }

        cleanup_requests: list[dict[str, Any]] = [
            {
                "purpose": "cleanup_regression_resource",
                "method": "DELETE",
                "path": f"{path}/{{created_id}}",
            },
        ]

        return GeneratedFixture(
            fixture_id=_make_id("LRN-FIX", bug_id, "regr"),
            entity_type=entity,
            purpose="regression",
            setup_requests=setup_requests,
            required_fields=required_fields,
            cleanup_requests=cleanup_requests,
            source_bug_id=bug_id,
            strategy=GenerationStrategy.REGRESSION_FIXTURE,
            rationale=f"Regression fixture for confirmed bug {bug_id}: verifies {method} {path} returns {expected_status} after fix",
        )

    def generate_all_fixtures(
        self,
        confirmed_bug: dict[str, Any],
    ) -> list[GeneratedFixture]:
        """Generate all fixture types for a confirmed bug."""
        fixtures: list[GeneratedFixture] = []
        repro = self.generate_reproduction_fixture(confirmed_bug)
        if repro:
            fixtures.append(repro)
        regr = self.generate_regression_fixture(confirmed_bug)
        if regr:
            fixtures.append(regr)
        return fixtures


# ═════════════════════════════════════════════════════════════════════════════
# Learning Generator (orchestrator)
# ═════════════════════════════════════════════════════════════════════════════

class LearningGenerator:
    """Orchestrate learning-driven artifact generation from confirmed bugs.

    Usage::

        generator = LearningGenerator(existing_probes, project_context)
        manifest = generator.generate_from_confirmed_bugs(confirmed_findings)
        # manifest contains all generated probes, oracles, fixtures
    """

    def __init__(
        self,
        existing_probes: list[dict[str, Any]] | None = None,
        project_context: dict[str, Any] | None = None,
    ):
        self.probe_generator = ProbeGenerator(existing_probes)
        self.oracle_generator = OracleGenerator()
        self.fixture_generator = FixtureGenerator()
        self.context = project_context or {}
        self._log: list[str] = []

    def generate_from_confirmed_bugs(
        self,
        confirmed_findings: list[dict[str, Any]],
        *,
        max_probes_per_bug: int = 10,
        max_oracles_per_bug: int = 5,
    ) -> LearningManifest:
        """Generate new artifacts from a list of confirmed bug findings.

        Args:
            confirmed_findings: List of confirmed bug dicts.
            max_probes_per_bug: Max new probes to generate per bug.
            max_oracles_per_bug: Max new oracles to generate per bug.

        Returns:
            LearningManifest with all generated artifacts.
        """
        manifest_id = f"LM-{int(time.time())}"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        all_probes: list[GeneratedProbe] = []
        all_oracles: list[GeneratedOracle] = []
        all_fixtures: list[GeneratedFixture] = []
        source_bugs_used = 0

        for finding in confirmed_findings:
            if not isinstance(finding, dict):
                continue

            # Only process confirmed bugs
            verdict = str(finding.get("verdict") or finding.get("confirmation_status", ""))
            if verdict not in ("confirmed", "validated", "validated_candidate", "reproduced"):
                continue

            source_bugs_used += 1

            # Generate probes
            probes = self.probe_generator.generate_all_variants(finding, self.context)
            all_probes.extend(probes[:max_probes_per_bug])

            # Generate oracles
            oracles = self.oracle_generator.generate_all_oracles(finding, self.context)
            all_oracles.extend(oracles[:max_oracles_per_bug])

            # Generate fixtures
            fixtures = self.fixture_generator.generate_all_fixtures(finding)
            all_fixtures.extend(fixtures)

        self._log.append(
            f"generate_from_confirmed_bugs: {source_bugs_used} confirmed bugs → "
            f"{len(all_probes)} probes, {len(all_oracles)} oracles, {len(all_fixtures)} fixtures"
        )

        # Build summary
        strategies_used = set()
        for p in all_probes:
            strategies_used.add(p.strategy.value)
        for o in all_oracles:
            strategies_used.add(o.strategy.value)
        for f in all_fixtures:
            strategies_used.add(f.strategy.value)

        return LearningManifest(
            manifest_id=manifest_id,
            generated_at=timestamp,
            source_bug_count=source_bugs_used,
            generated_probes=all_probes,
            generated_oracles=all_oracles,
            generated_fixtures=all_fixtures,
            summary={
                "total_probes_generated": len(all_probes),
                "total_oracles_generated": len(all_oracles),
                "total_fixtures_generated": len(all_fixtures),
                "strategies_used": sorted(strategies_used),
                "probes_by_strategy": {
                    s.value: len([p for p in all_probes if p.strategy == s])
                    for s in GenerationStrategy
                    if any(p.strategy == s for p in all_probes)
                },
                "oracles_by_strategy": {
                    s.value: len([o for o in all_oracles if o.strategy == s])
                    for s in GenerationStrategy
                    if any(o.strategy == s for o in all_oracles)
                },
                "fixtures_by_strategy": {
                    s.value: len([f for f in all_fixtures if f.strategy == s])
                    for s in GenerationStrategy
                    if any(f.strategy == s for f in all_fixtures)
                },
            },
        )

    def manifest_to_dict(self, manifest: LearningManifest) -> dict[str, Any]:
        """Convert a LearningManifest to a JSON-safe dict."""
        return {
            "manifest_id": manifest.manifest_id,
            "generated_at": manifest.generated_at,
            "source_bug_count": manifest.source_bug_count,
            "summary": manifest.summary,
            "generated_probes": [
                {
                    "probe_id": p.probe_id,
                    "title": p.title,
                    "risk_type": p.risk_type,
                    "severity": p.severity,
                    "actor": p.actor,
                    "method": p.method,
                    "path": p.path,
                    "expected_status": p.expected_status,
                    "source_bug_id": p.source_bug_id,
                    "strategy": p.strategy.value,
                    "rationale": p.rationale,
                    "execution_policy": p.execution_policy,
                }
                for p in manifest.generated_probes
            ],
            "generated_oracles": [
                {
                    "oracle_id": o.oracle_id,
                    "oracle_name": o.oracle_name,
                    "layer": o.layer,
                    "rule_description": o.rule_description,
                    "applies_to": f"{o.applies_to_method} {o.applies_to_path_pattern}",
                    "source_bug_id": o.source_bug_id,
                    "strategy": o.strategy.value,
                    "rationale": o.rationale,
                }
                for o in manifest.generated_oracles
            ],
            "generated_fixtures": [
                {
                    "fixture_id": f.fixture_id,
                    "entity_type": f.entity_type,
                    "purpose": f.purpose,
                    "source_bug_id": f.source_bug_id,
                    "strategy": f.strategy.value,
                    "rationale": f.rationale,
                    "setup_request_count": len(f.setup_requests),
                    "cleanup_request_count": len(f.cleanup_requests),
                }
                for f in manifest.generated_fixtures
            ],
        }

    def persist_manifest(
        self,
        manifest: LearningManifest,
        output_dir: str | Path,
    ) -> Path:
        """Persist a learning manifest to disk so generated artifacts survive across runs.

        Args:
            manifest: The LearningManifest to persist.
            output_dir: Directory to write the manifest JSON.

        Returns:
            Path to the written file.
        """
        import os
        output_path = Path(output_dir or os.environ.get(
            "QUALIBUG_WORKSPACE_ROOT",
            str(Path(__file__).resolve().parents[1])
        ))
        out_dir = output_path / "platform_outputs" / "_learning"
        out_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = out_dir / f"learning_manifest_{manifest.manifest_id}.json"
        manifest_dict = self.manifest_to_dict(manifest)
        manifest_path.write_text(
            json.dumps(manifest_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._log.append(f"persist_manifest: {manifest_path}")
        return manifest_path

    def get_log(self) -> list[str]:
        return list(self._log)
