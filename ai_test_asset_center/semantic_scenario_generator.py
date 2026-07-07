"""Source-grounded scenarios for the existing V12 behavior graph.

No default business entity, API path, actor, request body or cleanup action is
created here. Missing executable prerequisites are represented as plan gaps.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .business_state_graph import BusinessStateGraph, StateTransition, _api_facts, behavior_slice_id
from .real_id_resolver import path_has_placeholders


@dataclass
class ScenarioStep:
    order: int
    action: str
    api_method: str = ""
    api_path: str = ""
    body_template: dict[str, Any] = field(default_factory=dict)
    extract_from_response: list[str] = field(default_factory=list)
    expected_status: int = 0
    actor: str = ""


@dataclass
class ExecutableScenario:
    id: str
    title: str
    description: str = ""
    category: str = "state_machine"
    severity: str = "P2"
    entity: str = ""
    preconditions: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    steps: list[ScenarioStep] = field(default_factory=list)
    expected_state: str = ""
    oracle_rules: list[str] = field(default_factory=list)
    cleanup_steps: list[ScenarioStep] = field(default_factory=list)
    is_forbidden_path: bool = False
    is_boundary_path: bool = False
    is_concurrent: bool = False
    confidence: float = 0.0
    actor_token: str = ""
    execution_policy: str = "plan_only_requires_fixture"
    evidence_gaps: list[str] = field(default_factory=list)
    source_refs: list[dict[str, str]] = field(default_factory=list)
    behavior_slice_id: str = ""
    behavior_slice_kind: str = ""
    discovery_round: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "severity": self.severity,
            "entity": self.entity,
            "preconditions": self.preconditions,
            "actors": self.actors,
            "steps": [
                {
                    "order": step.order,
                    "action": step.action,
                    "method": step.api_method,
                    "path": step.api_path,
                    "body": step.body_template,
                    "extract": step.extract_from_response,
                    "expected": step.expected_status,
                    "actor": step.actor,
                }
                for step in self.steps
            ],
            "expected_state": self.expected_state,
            "oracle_rules": self.oracle_rules,
            "cleanup": [step.action for step in self.cleanup_steps],
            "flags": {
                "forbidden": self.is_forbidden_path,
                "boundary": self.is_boundary_path,
                "concurrent": self.is_concurrent,
            },
            "confidence": self.confidence,
            "execution_policy": self.execution_policy,
            "evidence_gaps": self.evidence_gaps,
            "source_refs": self.source_refs,
            "behavior_slice_id": self.behavior_slice_id,
            "behavior_slice_kind": self.behavior_slice_kind,
            "discovery_round": self.discovery_round,
        }


class SemanticScenarioGenerator:
    """Plan only source-backed obligations selected by the incremental scheduler."""

    def generate(
        self,
        graphs: dict[str, BusinessStateGraph],
        api_doc: str = "",
        active_slice_ids: set[str] | None = None,
        discovery_round: int = 1,
        active_slices: list[dict[str, Any]] | None = None,
        allow_source_runtime: bool = False,
    ) -> list[ExecutableScenario]:
        round_number = max(1, int(discovery_round or 1))
        active_slice_map = {
            str(item.get("slice_id") or ""): dict(item)
            for item in active_slices or []
            if isinstance(item, dict) and str(item.get("slice_id") or "")
        }
        results: list[ExecutableScenario] = []
        for entity, graph in sorted((graphs or {}).items()):
            if not isinstance(graph, BusinessStateGraph):
                continue
            for transition in graph.transitions:
                item = self._transition(entity, transition, round_number)
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for state, node in graph.states.items():
                for invariant in node.invariants:
                    slice_id = behavior_slice_id("invariant", entity, state, invariant)
                    item = self._invariant(
                        entity,
                        state,
                        invariant,
                        node.source_refs,
                        round_number,
                        slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                    )
                    if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                        results.append(item)
        if allow_source_runtime:
            for item in self._source_observations(api_doc, round_number):
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
        deduped: list[ExecutableScenario] = []
        seen: set[str] = set()
        for item in results:
            fingerprint = f"{item.behavior_slice_id}|{item.entity}|{item.title}|{item.expected_state}"
            if fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(item)
        return deduped

    def _source_observations(self, api_doc: str, discovery_round: int) -> list[ExecutableScenario]:
        _entities, _states, endpoints = _api_facts(api_doc, __import__("re").compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", __import__("re").I))
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in endpoints:
            if str(item.get("method") or "").upper() not in {"GET", "HEAD", "OPTIONS"}:
                continue
            entity = str(item.get("entity") or "")
            path = str(item.get("path") or "")
            if entity and path.startswith("/"):
                grouped.setdefault(entity, []).append(item)
        results: list[ExecutableScenario] = []
        for entity, items in sorted(grouped.items()):
            paths = list(dict.fromkeys(str(item.get("path") or "") for item in items if str(item.get("path") or "")))
            if not paths:
                continue
            first = paths[0]
            results.append(ExecutableScenario(
                id=self._id(entity, "source_observation", first),
                title=f"[Source observation] {entity}: {first}",
                description="Read-only source-bound endpoint observation for runtime evidence capture.",
                category="source_observation",
                severity="P2",
                entity=entity,
                steps=[ScenarioStep(order=1, action="observe_source_endpoint", api_method="GET", api_path=first, expected_status=200, actor="readonly")],
                oracle_rules=["RuntimeObservation.source_endpoint_reachable"],
                confidence=0.5,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=[{"source_type": "openapi", "locator": first, "quote": first}],
                behavior_slice_id=behavior_slice_id("source_observation", entity, ",".join(paths)),
                behavior_slice_kind="source_observation",
                discovery_round=discovery_round,
            ))
        return results

    def _transition(self, entity: str, transition: StateTransition, discovery_round: int) -> ExecutableScenario:
        forbidden = bool(transition.is_forbidden)
        kind = "禁止流转" if forbidden else ("边界流转" if transition.is_boundary else "状态流转")
        gaps = ["FIXTURE_CONTRACT_MISSING", "ACTOR_BINDING_MISSING", "CLEANUP_CONTRACT_MISSING"]
        if not transition.action or not transition.api_endpoint:
            gaps.insert(0, "ACTION_ROUTE_NOT_SOURCE_BOUND")
        slice_id = transition.behavior_slice_id or behavior_slice_id(
            "transition",
            entity,
            transition.from_state,
            transition.to_state,
            transition.action,
            transition.api_endpoint,
            "forbidden" if forbidden else "normal",
        )
        return ExecutableScenario(
            id=self._id(entity, transition.from_state, transition.to_state, transition.action),
            title=f"[来源约束{kind}] {entity}: {transition.from_state} -> {transition.to_state}",
            description="当前资料未提供完整运行时前置数据和身份绑定；仅产生计划，不自动发起请求。",
            severity="P0" if forbidden else "P2",
            entity=entity,
            preconditions=[f"已通过可追溯数据证明 {entity} 处于 {transition.from_state}"],
            expected_state=transition.from_state if forbidden else transition.to_state,
            oracle_rules=["StateOracle.source_grounded_transition", f"{transition.from_state}->{transition.to_state}"],
            is_forbidden_path=forbidden,
            is_boundary_path=bool(transition.is_boundary),
            confidence=0.55 if transition.source_refs else 0.2,
            evidence_gaps=gaps,
            source_refs=list(transition.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="transition",
            discovery_round=discovery_round,
        )

    def _invariant(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        *,
        slice_meta: dict[str, Any] | None = None,
    ) -> ExecutableScenario:
        slice_id = behavior_slice_id("invariant", entity, state, invariant)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        if observation_path:
            return ExecutableScenario(
                id=self._id(entity, state, invariant),
                title=f"[来源约束不变量] {entity}: {state}",
                description=invariant[:300],
                category="invariant",
                severity="P1",
                entity=entity,
                preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
                actors=["readonly"],
                steps=[ScenarioStep(order=1, action="observe_bound_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
                expected_state=state,
                oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
                confidence=0.55 if refs else 0.3,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=list(refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="invariant",
                discovery_round=discovery_round,
            )
        return ExecutableScenario(
            id=self._id(entity, state, invariant),
            title=f"[来源约束不变量] {entity}: {state}",
            description=invariant[:300],
            category="invariant",
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
            expected_state=state,
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=0.45 if refs else 0.2,
            evidence_gaps=["OBSERVATION_ROUTE_NOT_SOURCE_BOUND", "ACTOR_BINDING_MISSING"],
            source_refs=list(refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
        )

    @staticmethod
    def _preferred_read_endpoint(endpoints: list[Any]) -> str:
        candidates = [str(item or "").strip() for item in endpoints if str(item or "").strip().startswith("/")]
        for path in candidates:
            if not path_has_placeholders(path):
                return path
        return ""

    @staticmethod
    def _id(*parts: Any) -> str:
        return "SCN_SRC_" + hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]
