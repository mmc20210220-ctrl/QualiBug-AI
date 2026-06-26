"""
Phase79: Observer Candidate Builder + Fixture Readiness Analyzer +
         Verification Coverage Analyzer + Onboarding Gap Reporter

Supporting modules for ProjectContextCompiler. All output feeds into
Phase78 Semantic Verifier main chain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .project_context_compiler import (
    ProjectContext, EntityCandidate, APICapability,
    ObserverCandidate, BindingCandidate, FixtureReadiness, GapReport,
)

# ═══════════════════════════════════════════════════════════════════
# Observer Candidate Builder
# ═══════════════════════════════════════════════════════════════════

class ObserverCandidateBuilder:
    """Builds observer candidates from API capability map and entity catalog."""

    def build(
        self,
        apis: list[APICapability],
        entities: list[EntityCandidate],
        base_url: str = "",
    ) -> list[ObserverCandidate]:
        observers: list[ObserverCandidate] = []

        for api in apis:
            if not api.is_observer_candidate:
                continue

            entity = self._match_entity(api, entities)
            if not entity:
                continue

            projection = self._build_projection(api, entity)

            obs = ObserverCandidate(
                observer_id=f"candidate_{api.entity_alias}_{api.capability}",
                entity_alias=entity.entity_alias,
                method=api.method,
                path=api.path,
                read_only_confidence=0.95 if api.method == "GET" else 0.5,
                projection=projection,
                confidence=api.confidence,
                evidence=api.evidence + [{"source": "entity_catalog_match"}],
                requires_human_confirmation=True,
            )
            observers.append(obs)

        return observers

    def _match_entity(self, api: APICapability, entities: list[EntityCandidate]) -> EntityCandidate | None:
        api_entity = api.entity_alias.lower().replace("-", "_").replace(" ", "_")
        for e in entities:
            e_alias = e.entity_alias.lower().replace("-", "_").replace(" ", "_")
            if api_entity == e_alias or e_alias in api_entity or api_entity in e_alias:
                return e
        return None

    def _build_projection(self, api: APICapability, entity: EntityCandidate) -> dict:
        proj: dict = {}

        # Identity fields
        for f in entity.identity_fields[:1]:
            proj["entity_id"] = f"$.data.{f}" if f else ""

        # State fields
        for f in entity.state_fields[:1]:
            proj["lifecycle_state"] = f"$.data.{f}"

        # Amount fields
        amounts = {}
        for f in entity.amount_fields[:3]:
            amounts[f] = f"$.data.{f}"
        if amounts:
            proj["amounts"] = amounts

        # Quantity fields
        quantities = {}
        for f in entity.quantity_fields[:3]:
            quantities[f] = f"$.data.{f}"
        if quantities:
            proj["quantities"] = quantities

        # Version
        for f in entity.version_fields[:1]:
            proj["version"] = f"$.data.{f}"

        return proj


# ═══════════════════════════════════════════════════════════════════
# Binding Candidate Builder
# ═══════════════════════════════════════════════════════════════════

class BindingCandidateBuilder:
    """Infers entity binding from API schema and response patterns."""

    def build(
        self,
        apis: list[APICapability],
        entities: list[EntityCandidate],
    ) -> list[BindingCandidate]:
        bindings: list[BindingCandidate] = []

        for api in apis:
            if not api.is_action_candidate:
                continue

            entity = self._match_entity(api, entities)
            binding = BindingCandidate(
                action_step=f"{api.method} {api.path}",
                entity_alias=entity.entity_alias if entity else api.entity_alias,
                entity_id_source="response_body" if api.has_entity_id else "unknown",
                entity_id_path="$.data.id" if api.has_entity_id else "",
                entity_id_confidence=0.9 if api.has_entity_id else 0.3,
                correlation_id_source="response_header" if api.has_correlation_id else "unknown",
                correlation_id_path="X-Correlation-Id" if api.has_correlation_id else "",
                confidence=api.confidence,
                evidence=api.evidence + [{"source": "api_schema"}],
            )
            bindings.append(binding)

        return bindings

    def _match_entity(self, api: APICapability, entities: list[EntityCandidate]) -> EntityCandidate | None:
        api_entity = api.entity_alias.lower().replace("-", "_").replace(" ", "_")
        for e in entities:
            if api_entity in e.entity_alias.lower().replace("-", "_").replace(" ", "_"):
                return e
        return None


# ═══════════════════════════════════════════════════════════════════
# Fixture Readiness Analyzer
# ═══════════════════════════════════════════════════════════════════

class FixtureReadinessAnalyzer:
    """Analyzes whether candidate flows are ready to execute."""

    def analyze(
        self,
        bindings: list[BindingCandidate],
        observers: list[ObserverCandidate],
        apis: list[APICapability],
        fixtures: dict | None = None,
        test_accounts: dict | None = None,
    ) -> list[FixtureReadiness]:
        results: list[FixtureReadiness] = []
        fixtures = fixtures or {}
        test_accounts = test_accounts or {}

        for binding in bindings:
            missing = []
            actions = []

            # Check fixtures
            fixture_needed = any(
                api.is_action_candidate and api.method in ("POST", "PUT", "PATCH")
                for api in apis
                if api.entity_alias == binding.entity_alias
            )
            if fixture_needed and not fixtures:
                missing.append("No fixtures configured for entity")
                actions.append("Provide fixture data for this entity type")

            # Check observer
            has_observer = any(o.entity_alias == binding.entity_alias for o in observers)
            if not has_observer:
                missing.append(f"No observer for entity {binding.entity_alias}")
                actions.append("Configure a GET observer for this entity")

            # Check entity binding
            if binding.entity_id_confidence < 0.5:
                missing.append("Entity ID binding confidence too low")
                actions.append("Specify entity_id extraction path")

            # Check test accounts
            if not test_accounts:
                missing.append("No test accounts configured")
                actions.append("Add test account credentials")

            if not missing:
                readiness = "READY"
            elif "fixture" in str(missing).lower():
                readiness = "BLOCKED_BY_FIXTURE"
            elif "observer" in str(missing).lower():
                readiness = "BLOCKED_BY_OBSERVER"
            elif "binding" in str(missing).lower():
                readiness = "BLOCKED_BY_ENTITY_BINDING"
            elif "account" in str(missing).lower():
                readiness = "BLOCKED_BY_PERMISSION"
            else:
                readiness = "PARTIALLY_READY"

            results.append(FixtureReadiness(
                flow_id=f"flow_{binding.entity_alias}",
                readiness=readiness,
                missing_requirements=missing,
                recommended_next_actions=actions,
                risk_level="high" if readiness.startswith("BLOCKED") else "medium",
                auto_retryable=not readiness.startswith("BLOCKED"),
            ))

        return results


# ═══════════════════════════════════════════════════════════════════
# Verification Coverage Analyzer
# ═══════════════════════════════════════════════════════════════════

class VerificationCoverageAnalyzer:
    """Computes coverage metrics across entities, observers, flows, invariants."""

    def analyze(self, ctx: ProjectContext) -> dict:
        entities = ctx.entities or []
        observers = ctx.observers or []
        apis = ctx.apis or []
        bindings = ctx.bindings or []
        readiness_list = ctx.fixtures or []

        # Entity coverage
        entities_observable = sum(1 for e in entities if any(o.entity_alias == e.entity_alias for o in observers))
        entities_actionable = sum(1 for e in entities if any(b.entity_alias == e.entity_alias for b in bindings))

        # Observer coverage
        entities_with_before_after = entities_observable
        entities_with_version = sum(1 for e in entities if e.version_fields)

        # Flow coverage
        total_flows = len(bindings)
        ready_flows = sum(1 for r in readiness_list if hasattr(r, 'readiness') and r.readiness == "READY")
        blocked_fixture = sum(1 for r in readiness_list if hasattr(r, 'readiness') and "FIXTURE" in str(r.readiness))
        blocked_observer = sum(1 for r in readiness_list if hasattr(r, 'readiness') and "OBSERVER" in str(r.readiness))

        # Invariant coverage: which of the 8 invariant types are verifiable
        invariant_types = [
            "state_unchanged_after_rejection",
            "lifecycle_transition",
            "numeric_delta",
            "conservation",
            "cross_view_equal",
            "eventually",
            "idempotency_replay",
            "authorization_non_mutation",
        ]
        invariants_verifiable = 0
        if entities_with_before_after >= 1:
            invariants_verifiable += 3  # state_unchanged, lifecycle, authorization
        if any(e.amount_fields or e.quantity_fields for e in entities):
            invariants_verifiable += 2  # numeric_delta, conservation
        if entities_observable >= 2:
            invariants_verifiable += 1  # cross_view_equal

        # Blind spots
        blind_spots = []
        for e in entities:
            if not any(o.entity_alias == e.entity_alias for o in observers):
                blind_spots.append({
                    "type": "unobservable_entity",
                    "entity": e.entity_alias,
                    "impact": "Cannot verify state changes for this entity",
                    "fix": "Configure a GET observer for this entity",
                })
        for b in bindings:
            if b.entity_id_confidence < 0.5:
                blind_spots.append({
                    "type": "weak_binding",
                    "action": b.action_step,
                    "impact": f"Entity ID confidence={b.entity_id_confidence}",
                    "fix": "Specify entity_id extraction path in response",
                })

        return {
            "entity_coverage": {
                "total": len(entities),
                "observable": entities_observable,
                "actionable": entities_actionable,
                "rate": entities_observable / max(len(entities), 1),
            },
            "observer_coverage": {
                "total_entities": len(entities),
                "with_observer": entities_with_before_after,
                "with_version": entities_with_version,
                "cross_view_capable": 1 if entities_observable >= 2 else 0,
            },
            "flow_coverage": {
                "total_candidate_flows": total_flows,
                "ready": ready_flows,
                "blocked_by_fixture": blocked_fixture,
                "blocked_by_observer": blocked_observer,
            },
            "invariant_coverage": {
                "total_types": len(invariant_types),
                "verifiable": invariants_verifiable,
                "rate": invariants_verifiable / len(invariant_types),
            },
            "blind_spots": blind_spots,
        }


# ═══════════════════════════════════════════════════════════════════
# Onboarding Gap Reporter
# ═══════════════════════════════════════════════════════════════════

class OnboardingGapReporter:
    """Generates a structured gap report from ProjectContext."""

    def report(self, ctx: ProjectContext) -> list[GapReport]:
        gaps: list[GapReport] = []
        coverage = VerificationCoverageAnalyzer().analyze(ctx)

        # Gap: no source documents
        if not ctx.source_documents:
            gaps.append(GapReport(
                gap_id="GAP_NO_DOCUMENTS",
                category="onboarding",
                severity="critical",
                description="No source documents provided. Cannot extract entities, APIs, or observers.",
                affected_entities=[],
                recommended_action="Provide PRD, OpenAPI spec, or API documentation.",
            ))

        # Gap: no entities identified
        if not ctx.entities:
            gaps.append(GapReport(
                gap_id="GAP_NO_ENTITIES",
                category="entity_extraction",
                severity="critical",
                description="No business entities identified from source documents.",
                affected_entities=[],
                recommended_action="Check PRD and OpenAPI for entity definitions or provide explicit entity mapping.",
            ))

        # Gap: entities without observers
        unobserved = [e.entity_alias for e in (ctx.entities or [])
                      if not any(o.entity_alias == e.entity_alias for o in (ctx.observers or []))]
        if unobserved:
            gaps.append(GapReport(
                gap_id="GAP_UNOBSERVED_ENTITIES",
                category="observer",
                severity="high",
                description=f"{len(unobserved)} entities have no observer: {', '.join(unobserved[:5])}",
                affected_entities=unobserved,
                recommended_action="Configure GET observers for unobserved entities. Provide read-only API endpoints.",
            ))

        # Gap: no bindings
        if not ctx.bindings:
            gaps.append(GapReport(
                gap_id="GAP_NO_BINDINGS",
                category="entity_binding",
                severity="high",
                description="No entity bindings identified. Cannot track entities through flows.",
                affected_entities=[],
                recommended_action="Provide entity ID extraction paths and correlation ID rules.",
            ))

        # Gap: no fixtures
        if not ctx.fixtures:
            gaps.append(GapReport(
                gap_id="GAP_NO_FIXTURES",
                category="fixture",
                severity="medium",
                description="No fixtures configured. Write operations cannot be executed.",
                affected_entities=[],
                recommended_action="Provide fixture data or test data for at least one entity type.",
            ))

        # Blind spots from coverage
        for bs in coverage.get("blind_spots", []):
            gaps.append(GapReport(
                gap_id=f"GAP_BLIND_{bs['type'].upper()}",
                category="blind_spot",
                severity="medium",
                description=bs.get("impact", ""),
                affected_entities=[bs.get("entity", bs.get("action", ""))],
                recommended_action=bs.get("fix", ""),
            ))

        return gaps
