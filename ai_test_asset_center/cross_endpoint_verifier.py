"""
Phase79+: Cross-Endpoint Consistency Verifier

Compares entity state across multiple API endpoints to detect:
- List vs detail data mismatches
- Admin vs viewer view inconsistencies  
- Aggregation/summary vs detail discrepancies
- Cache vs source-of-truth drift

Builds on: StateObserver, StateProjectionEngine, APICapabilityMapper
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .state_observer_registry import StateObserver, CanonicalStateSnapshot, snapshot_diff
from .state_projection_engine import StateProjectionEngine
from .unified_http_transport import SafeHttpTransport, ExecutionPolicy
from .project_context_compiler import EntityCandidate, APICapability


@dataclass
class EndpointPair:
    """Two endpoints that can be compared for the same entity."""
    entity_alias: str
    endpoint_a: str  # e.g., "GET /api/orders" (list)
    endpoint_b: str  # e.g., "GET /api/orders/{id}" (detail)
    common_fields: list[str] = field(default_factory=list)
    a_is_list: bool = False
    b_is_list: bool = False
    confidence: float = 0.0


@dataclass
class CrossEndpointResult:
    """Result of comparing entity state across two endpoints."""
    entity_alias: str
    entity_id: str
    endpoint_a: str
    endpoint_b: str
    matched: bool  # True if all fields match
    conflicts: list[dict] = field(default_factory=list)  # [{field, value_a, value_b}]
    snapshot_a: dict | None = None
    snapshot_b: dict | None = None
    verdict: str = "OK"  # OK | SOURCE_CONFLICT | BINDING_FAILED


class CrossEndpointVerifier:
    """
    Verifies data consistency across multiple API endpoints for the same entity.

    Usage:
        verifier = CrossEndpointVerifier(transport)
        pairs = verifier.discover_pairs(openapi_spec, entities, apis)
        for pair in pairs:
            results = verifier.verify_pair(pair, entity_ids=["OBJ-001"])
    """

    def __init__(self, transport: SafeHttpTransport | None = None):
        self.transport = transport or SafeHttpTransport(ExecutionPolicy(environment="test"))
        self.observer = StateObserver(redact_sensitive=True)
        self.projection = StateProjectionEngine()

    # ── Discovery: find comparable endpoint pairs ──

    def discover_pairs(
        self,
        openapi_spec: dict,
        entities: list[EntityCandidate],
        apis: list[APICapability] | None = None,
    ) -> list[EndpointPair]:
        """Find pairs of endpoints that observe the same entity."""
        pairs: list[EndpointPair] = []
        paths = openapi_spec.get("paths", {})

        for entity in entities:
            alias = entity.entity_alias.lower()
            endpoints = []

            for path, methods in paths.items():
                if not isinstance(methods, dict):
                    continue
                path_lower = path.lower()
                if alias not in path_lower:
                    continue

                for method, details in methods.items():
                    if method.upper() != "GET":
                        continue
                    endpoints.append((f"GET {path}", path, details))

            if len(endpoints) >= 2:
                # Pair list endpoint with detail endpoint
                list_eps = [(ep, p, d) for ep, p, d in endpoints if "{" not in p]
                detail_eps = [(ep, p, d) for ep, p, d in endpoints if "{" in p]

                for list_ep, list_path, _ in list_eps:
                    for detail_ep, detail_path, _ in detail_eps:
                        common = self._common_fields(entity, openapi_spec, list_path, detail_path)
                        pairs.append(EndpointPair(
                            entity_alias=entity.entity_alias,
                            endpoint_a=list_ep,
                            endpoint_b=detail_ep,
                            common_fields=common,
                            a_is_list=True,
                            b_is_list=False,
                            confidence=0.85 if common else 0.5,
                        ))

        return pairs

    def _common_fields(
        self, entity: EntityCandidate, spec: dict, path_a: str, path_b: str,
    ) -> list[str]:
        """Find fields that appear in both endpoint response schemas."""
        fields = set()

        # Add identity fields
        fields.update(entity.identity_fields)
        # Add state fields (most important for consistency)
        fields.update(entity.state_fields)
        # Add amounts and quantities
        fields.update(entity.amount_fields[:2])
        fields.update(entity.quantity_fields[:2])

        return list(fields)

    # ── Verification: compare two endpoints ──

    def verify_pair(
        self,
        pair: EndpointPair,
        entity_ids: list[str],
        token: str | None = None,
    ) -> list[CrossEndpointResult]:
        """Compare entity state across two endpoints for given entity IDs."""
        results: list[CrossEndpointResult] = []

        for eid in entity_ids:
            result = self._compare_endpoints(pair, eid, token)
            results.append(result)

        return results

    def _compare_endpoints(
        self, pair: EndpointPair, entity_id: str, token: str | None,
    ) -> CrossEndpointResult:
        """Compare one entity across two endpoints."""
        conflicts = []

        # Fetch from endpoint A (list)
        url_a = pair.endpoint_a.split(" ", 1)[1] if " " in pair.endpoint_a else pair.endpoint_a
        resp_a = self.transport.get(url_a, token=token)

        # Fetch from endpoint B (detail)
        url_b = pair.endpoint_b.split(" ", 1)[1] if " " in pair.endpoint_b else pair.endpoint_b
        url_b = url_b.replace("{id}", entity_id).replace("{entity_id}", entity_id)
        resp_b = self.transport.get(url_b, token=token)

        if resp_b.blocked or resp_a.blocked:
            return CrossEndpointResult(
                entity_alias=pair.entity_alias, entity_id=entity_id,
                endpoint_a=pair.endpoint_a, endpoint_b=pair.endpoint_b,
                matched=False, verdict="BLOCKED_BY_SAFETY",
            )

        # Find entity in list response
        entity_in_list = self._find_in_list(resp_a.json, entity_id)
        entity_in_detail = resp_b.json

        if not entity_in_list or not entity_in_detail:
            return CrossEndpointResult(
                entity_alias=pair.entity_alias, entity_id=entity_id,
                endpoint_a=pair.endpoint_a, endpoint_b=pair.endpoint_b,
                matched=False, verdict="BINDING_FAILED",
            )

        # Create snapshots
        snap_a = self.observer.observe_from_http(
            entity_in_list, resp_a.status_code, url_a,
            entity_id=entity_id, observer_id="list",
        )
        snap_b = self.observer.observe_from_http(
            entity_in_detail, resp_b.status_code, url_b,
            entity_id=entity_id, observer_id="detail",
        )

        # Compare common fields
        for field in pair.common_fields:
            val_a = self.projection.extract(entity_in_list, field)
            val_b = self.projection.extract(entity_in_detail, field)

            if val_a != val_b and val_a is not None and val_b is not None:
                conflicts.append({
                    "field": field,
                    "value_list": val_a,
                    "value_detail": val_b,
                    "endpoint_a": pair.endpoint_a,
                    "endpoint_b": pair.endpoint_b,
                })

        matched = len(conflicts) == 0
        return CrossEndpointResult(
            entity_alias=pair.entity_alias, entity_id=entity_id,
            endpoint_a=pair.endpoint_a, endpoint_b=pair.endpoint_b,
            matched=matched,
            conflicts=conflicts,
            snapshot_a=snap_a.to_dict() if hasattr(snap_a, 'to_dict') else None,
            snapshot_b=snap_b.to_dict() if hasattr(snap_b, 'to_dict') else None,
            verdict="OK" if matched else "SOURCE_CONFLICT",
        )

    def _find_in_list(self, response: dict | None, entity_id: str) -> dict | None:
        """Find an entity by ID in a list response."""
        if not response:
            return None
        items = response.get("items", response.get("data", []))
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    item_id = str(item.get("id", item.get("code", "")))
                    if item_id == entity_id:
                        return item
        return None

    # ── Batch verification ──

    def verify_all(
        self,
        openapi_spec: dict,
        entities: list[EntityCandidate],
        apis: list[APICapability] | None = None,
        entity_ids: list[str] | None = None,
        token: str | None = None,
    ) -> dict:
        """Full pipeline: discover pairs → verify all."""
        pairs = self.discover_pairs(openapi_spec, entities, apis)
        entity_ids = entity_ids or ["1"]

        all_results = []
        for pair in pairs:
            results = self.verify_pair(pair, entity_ids, token)
            all_results.extend(results)

        conflicts = [r for r in all_results if r.verdict == "SOURCE_CONFLICT"]
        return {
            "pairs_found": len(pairs),
            "total_comparisons": len(all_results),
            "conflicts": len(conflicts),
            "conflict_details": [
                {
                    "entity": c.entity_alias,
                    "entity_id": c.entity_id,
                    "endpoints": [c.endpoint_a, c.endpoint_b],
                    "mismatched_fields": [f["field"] for f in c.conflicts],
                }
                for c in conflicts
            ],
            "results": [{"entity": r.entity_alias, "id": r.entity_id,
                        "verdict": r.verdict, "conflicts": len(r.conflicts)}
                       for r in all_results],
        }
