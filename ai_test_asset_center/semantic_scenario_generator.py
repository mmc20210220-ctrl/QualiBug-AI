"""Source-grounded scenarios for the existing V12 behavior graph.

No default business entity, API path, actor, request body or cleanup action is
created here. Missing executable prerequisites are represented as plan gaps.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .auto_test_data_factory import _markdown_request_example
from .business_state_graph import BusinessStateGraph, StateEdge, StateTransition, _api_facts, behavior_slice_id
from .real_id_resolver import normalize_path_placeholders, path_has_placeholders


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
    selection_origin: str = ""

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
            "selection_origin": self.selection_origin,
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
            for edge in graph.edges:
                slice_id = behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation)
                item = self._dependency(
                    entity,
                    edge,
                    round_number,
                    slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                    api_doc=api_doc if allow_source_runtime else "",
                )
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
            for state, node in graph.states.items():
                for invariant in node.invariants:
                    slice_id = behavior_slice_id("invariant", entity, state, invariant)
                    items = self._invariant(
                        entity,
                        state,
                        invariant,
                        node.source_refs,
                        round_number,
                        slice_meta=active_slice_map.get(slice_id) if allow_source_runtime else None,
                        api_doc=api_doc if allow_source_runtime else "",
                    )
                    for item in items:
                        if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                            results.append(item)
        if allow_source_runtime:
            for item in self._source_observations(api_doc, round_number):
                if active_slice_ids is None or item.behavior_slice_id in active_slice_ids:
                    results.append(item)
        emitted_slice_ids = {item.behavior_slice_id for item in results if str(item.behavior_slice_id or "").strip()}
        for slice_id, slice_meta in active_slice_map.items():
            if slice_id in emitted_slice_ids:
                continue
            item = self._fallback_active_slice(slice_meta, round_number, api_doc if allow_source_runtime else "")
            if item is None:
                continue
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

    def _fallback_active_slice(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
    ) -> ExecutableScenario | None:
        kind = str(slice_meta.get("kind") or "").strip().lower()
        if kind == "source_observation":
            return self._source_observation_from_meta(slice_meta, discovery_round)
        if kind == "invariant":
            return self._invariant_from_meta(slice_meta, discovery_round, api_doc)
        return None

    def _source_observation_from_meta(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
    ) -> ExecutableScenario | None:
        entity = str(slice_meta.get("entity") or "").strip()
        endpoints = [str(item or "").strip() for item in (slice_meta.get("endpoints") or []) if str(item or "").strip()]
        if not entity or not endpoints:
            return None
        first = self._preferred_read_endpoint(endpoints) or endpoints[0]
        if not first.startswith("/"):
            return None
        return ExecutableScenario(
            id=self._id(entity, "source_observation", first),
            title=f"[Source observation] {entity}: {first}",
            description="Read-only source-bound endpoint observation for runtime evidence capture.",
            category="source_observation",
            severity="P2",
            entity=entity,
            steps=[ScenarioStep(order=1, action="observe_source_endpoint", api_method="GET", api_path=first, expected_status=200, actor="readonly")],
            oracle_rules=["RuntimeObservation.source_endpoint_reachable"],
            confidence=float(slice_meta.get("priority") or 0.45),
            execution_policy="safe_read_only",
            evidence_gaps=[str(item) for item in (slice_meta.get("evidence_gaps") or []) if str(item).strip()],
            source_refs=[dict(item) for item in (slice_meta.get("source_refs") or []) if isinstance(item, dict)],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="source_observation",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    def _invariant_from_meta(
        self,
        slice_meta: dict[str, Any],
        discovery_round: int,
        api_doc: str,
    ) -> ExecutableScenario | None:
        entity = str(slice_meta.get("entity") or "").strip()
        refs = [dict(item) for item in (slice_meta.get("source_refs") or []) if isinstance(item, dict)]
        states = [str(item or "").strip() for item in (slice_meta.get("states") or []) if str(item or "").strip()]
        invariant = self._slice_meta_invariant_text(slice_meta)
        observation_path = self._preferred_read_endpoint(list(slice_meta.get("endpoints") or []))
        if not entity:
            return None
        runtime_upgrade = self._invariant_runtime_upgrade(
            entity,
            states[0] if states else "",
            invariant,
            refs,
            discovery_round,
            slice_id=str(slice_meta.get("slice_id") or ""),
            observation_path=observation_path,
            api_doc=api_doc,
        )
        if runtime_upgrade is not None:
            return runtime_upgrade
        if not observation_path:
            return None
        state_or_rule = states[0] if states else invariant[:120]
        return ExecutableScenario(
            id=self._id(entity, state_or_rule or "invariant"),
            title=f"[来源约束不变量] {entity}: {state_or_rule}",
            description=invariant[:300],
            category="invariant",
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本"],
            actors=["readonly"],
            steps=[ScenarioStep(order=1, action="observe_bound_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
            expected_state=states[0] if states else "",
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.55 if refs else 0.3),
            execution_policy="safe_read_only",
            evidence_gaps=[],
            source_refs=refs,
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            selection_origin="active_slice_fallback_materialized",
        )

    @staticmethod
    def _slice_meta_invariant_text(slice_meta: dict[str, Any]) -> str:
        for ref in slice_meta.get("source_refs") or []:
            if not isinstance(ref, dict):
                continue
            quote = str(ref.get("quote") or "").strip()
            if quote:
                return quote
        states = [str(item or "").strip() for item in (slice_meta.get("states") or []) if str(item or "").strip()]
        if states:
            return states[0]
        return str(slice_meta.get("entity") or "source_invariant")

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

    def _dependency(
        self,
        entity: str,
        edge: StateEdge,
        discovery_round: int,
        *,
        slice_meta: dict[str, Any] | None = None,
        api_doc: str = "",
    ) -> ExecutableScenario:
        slice_id = behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        title = f"[跨实体依赖] {entity} -> {edge.target_entity}"
        write_path = self._preferred_write_endpoint(api_doc, entity)
        write_body = self._dependency_write_body(api_doc, entity, edge.target_entity)
        if observation_path and write_path and isinstance(write_body, dict) and write_body:
            extract_fields = ["id", "status", "state", "amount", "totalAmount", "total_amount"]
            return ExecutableScenario(
                id=self._id(entity, edge.target_entity, edge.relation, write_path),
                title=title,
                description=f"先观察 {edge.target_entity} 的真实对象，再执行 {entity} 的来源写入口，验证跨实体依赖链是否可执行。",
                category="dependency",
                severity="P1",
                entity=entity,
                preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
                actors=["readonly"],
                steps=[
                    ScenarioStep(order=1, action="observe_dependency_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=["id"]),
                    ScenarioStep(order=2, action="execute_dependency_write", api_method="POST", api_path=write_path, expected_status=200, actor="readonly", body_template=write_body),
                    ScenarioStep(order=3, action="verify_dependency_effect_after_write", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=extract_fields),
                ],
                expected_state=edge.target_state,
                oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
                confidence=0.6 if edge.source_refs else 0.35,
                execution_policy="approved_sandbox_write",
                evidence_gaps=[],
                source_refs=list(edge.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="dependency",
                discovery_round=discovery_round,
            )
        if observation_path:
            return ExecutableScenario(
                id=self._id(entity, edge.target_entity, edge.relation, observation_path),
                title=title,
                description=f"观察 {edge.target_entity} 的来源绑定运行时路径，验证 {entity} 的跨实体依赖前置是否可达。",
                category="dependency",
                severity="P1",
                entity=entity,
                preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
                actors=["readonly"],
                steps=[ScenarioStep(order=1, action="observe_dependency_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly")],
                expected_state=edge.target_state,
                oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
                confidence=0.5 if edge.source_refs else 0.3,
                execution_policy="safe_read_only",
                evidence_gaps=[],
                source_refs=list(edge.source_refs),
                behavior_slice_id=slice_id,
                behavior_slice_kind="dependency",
                discovery_round=discovery_round,
            )
        return ExecutableScenario(
            id=self._id(entity, edge.target_entity, edge.relation),
            title=title,
            description=f"当前资料缺少 {entity} -> {edge.target_entity} 的可观察运行时路由。",
            category="dependency",
            severity="P1",
            entity=entity,
            preconditions=[f"{entity} 依赖 {edge.target_entity} 的来源绑定对象"],
            expected_state=edge.target_state,
            oracle_rules=["ConsistencyOracle.cross_entity_dependency", f"{entity}->{edge.target_entity}:{edge.relation}"],
            confidence=0.4 if edge.source_refs else 0.2,
            evidence_gaps=["CROSS_ENTITY_OBSERVATION_CONTRACT_MISSING", "ACTOR_BINDING_MISSING"],
            source_refs=list(edge.source_refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="dependency",
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
        api_doc: str = "",
    ) -> list[ExecutableScenario]:
        slice_id = behavior_slice_id("invariant", entity, state, invariant)
        observation_path = self._preferred_read_endpoint((slice_meta or {}).get("endpoints") or [])
        runtime_upgrade = self._invariant_runtime_upgrade(
            entity,
            state,
            invariant,
            refs,
            discovery_round,
            slice_id=slice_id,
            observation_path=observation_path,
            api_doc=api_doc,
        )
        if runtime_upgrade is not None:
            return [runtime_upgrade]
        if observation_path:
            return [ExecutableScenario(
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
            )]
        return [ExecutableScenario(
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
        )]

    def _invariant_runtime_upgrade(
        self,
        entity: str,
        state: str,
        invariant: str,
        refs: list[dict[str, str]],
        discovery_round: int,
        *,
        slice_id: str,
        observation_path: str,
        api_doc: str,
    ) -> ExecutableScenario | None:
        if not observation_path or not str(api_doc or "").strip():
            return None
        action_plan = self._match_invariant_action(api_doc, entity, invariant, refs)
        if not action_plan:
            return None
        extract_fields = ["id", "status", "state", "amount", "totalAmount", "total_amount", "payableAmount", "payable_amount"]
        write_step = ScenarioStep(
            order=2,
            action=str(action_plan.get("scenario_action") or "execute_invariant_write"),
            api_method=str(action_plan.get("method") or "POST"),
            api_path=str(action_plan.get("path") or ""),
            expected_status=int(action_plan.get("expected_status") or 200),
            actor="readonly",
            body_template=action_plan.get("body") if isinstance(action_plan.get("body"), dict) else {},
        )
        title_suffix = str(action_plan.get("title_suffix") or str(action_plan.get("path") or "")).strip()
        steps = [
            ScenarioStep(order=1, action="observe_bound_entity", api_method="GET", api_path=observation_path, expected_status=200, actor="readonly", extract_from_response=extract_fields),
            write_step,
        ]
        if str(action_plan.get("mode") or "") == "duplicate_write":
            steps.append(ScenarioStep(
                order=3,
                action=str(action_plan.get("scenario_action") or "repeat_invariant_write"),
                api_method=str(action_plan.get("method") or "POST"),
                api_path=str(action_plan.get("path") or ""),
                expected_status=int(action_plan.get("expected_status") or 200),
                actor="readonly",
                body_template=action_plan.get("body") if isinstance(action_plan.get("body"), dict) else {},
            ))
        verify_order = len(steps) + 1
        steps.append(ScenarioStep(
            order=verify_order,
            action="verify_bound_entity_after_write",
            api_method="GET",
            api_path=observation_path,
            expected_status=200,
            actor="readonly",
            extract_from_response=extract_fields,
        ))
        return ExecutableScenario(
            id=self._id(entity, state, invariant, title_suffix),
            title=f"[来源约束不变量] {entity}: {state} -> {title_suffix}",
            description=invariant[:300],
            category=str(action_plan.get("category") or "state_machine"),
            severity="P1",
            entity=entity,
            preconditions=[f"需要 {entity} 的来源可追溯运行时样本", f"约束: {invariant[:120]}"],
            actors=["readonly"],
            steps=steps,
            expected_state=state,
            oracle_rules=["ConsistencyOracle.source_grounded_invariant", invariant[:300]],
            confidence=0.7 if refs else 0.4,
            execution_policy="approved_sandbox_write",
            evidence_gaps=[],
            source_refs=list(refs),
            behavior_slice_id=slice_id,
            behavior_slice_kind="invariant",
            discovery_round=discovery_round,
            is_forbidden_path=bool(action_plan.get("forbidden")),
        )

    @staticmethod
    def _locator_line(locator: Any) -> int | None:
        match = re.search(r"line:(\d+)", str(locator or ""), re.I)
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _invariant_action_contexts(cls, invariant: str, refs: list[dict[str, str]] | None) -> list[str]:
        anchor_text = str(invariant or "").strip()
        anchor_line: int | None = None
        normalized_refs = [item for item in (refs or []) if isinstance(item, dict)]
        for ref in normalized_refs:
            if str(ref.get("quote") or "").strip() == anchor_text:
                anchor_line = cls._locator_line(ref.get("locator"))
                break

        ranked: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        if anchor_text:
            ranked.append((-1, 0, anchor_text))
            seen.add(anchor_text)
        for index, ref in enumerate(normalized_refs, start=1):
            quote = str(ref.get("quote") or "").strip()
            if not quote or quote in seen:
                continue
            line = cls._locator_line(ref.get("locator"))
            distance = abs(line - anchor_line) if line is not None and anchor_line is not None else 10_000 + index
            ranked.append((distance, index, quote))
            seen.add(quote)
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [text for _, _, text in ranked]

    def _match_invariant_action(self, api_doc: str, entity: str, invariant: str, refs: list[dict[str, str]] | None = None) -> dict[str, Any]:
        contexts = self._invariant_action_contexts(invariant, refs)
        mode = ""
        forbidden = False
        for text in contexts:
            lowered = text.lower()
            if any(token in text for token in ("不能", "禁止", "不应", "不可", "不得")) or any(token in lowered for token in ("must not", "forbidden", "cannot", "should not")):
                mode, forbidden = "forbidden_write", True
                break
            if any(token in text for token in ("只能成功一次", "重复成功", "不能重复", "只能成功支付一次")) or any(token in lowered for token in ("only once", "duplicate", "idempotent")):
                mode = "duplicate_write"
                break
        if not mode:
            return {}

        action_profiles = [
            {"tokens": ["确认收货", "confirm"], "endpoint_tokens": ["confirm"]},
            {"tokens": ["发货", "ship"], "endpoint_tokens": ["ship"]},
            {"tokens": ["取消", "cancel"], "endpoint_tokens": ["cancel"]},
            {"tokens": ["支付", "pay", "payment"], "endpoint_tokens": ["pay", "payment"]},
            {"tokens": ["退款", "refund"], "endpoint_tokens": ["refund"]},
            {"tokens": ["审批", "approve", "approval"], "endpoint_tokens": ["approve", "approval"]},
            {"tokens": ["驳回", "reject"], "endpoint_tokens": ["reject"]},
            {"tokens": ["关闭", "close"], "endpoint_tokens": ["close"]},
            {"tokens": ["撤销", "revoke"], "endpoint_tokens": ["revoke"]},
            {"tokens": ["回滚", "rollback"], "endpoint_tokens": ["rollback"]},
            {"tokens": ["释放", "release"], "endpoint_tokens": ["release"]},
            {"tokens": ["归档", "archive"], "endpoint_tokens": ["archive"]},
            {"tokens": ["禁用", "disable"], "endpoint_tokens": ["disable"]},
            {"tokens": ["恢复", "restore", "reopen"], "endpoint_tokens": ["restore", "reopen"]},
        ]
        profile = None
        for text in contexts:
            lowered = text.lower()
            profile = next((item for item in action_profiles if any(token.lower() in lowered for token in item["tokens"])), None)
            if profile:
                break
        if not profile:
            return {}

        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        for endpoint in endpoints:
            method = str(endpoint.get("method") or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                continue
            path = str(endpoint.get("path") or "")
            haystack = " ".join(
                str(part or "").lower()
                for part in (endpoint.get("path"), endpoint.get("action"), endpoint.get("summary"), endpoint.get("entity"))
                if str(part or "").strip()
            )
            if not any(token.lower() in haystack for token in profile["endpoint_tokens"]):
                continue
            normalized_path = normalize_path_placeholders(path)
            if path_has_placeholders(normalized_path) and str(endpoint.get("entity") or "") != entity:
                continue
            body = self._invariant_write_body(api_doc, method, path, entity)
            if body is None:
                continue
            return {
                "mode": mode,
                "forbidden": forbidden,
                "method": method,
                "path": normalized_path,
                "body": body,
                "expected_status": 409 if forbidden else 200,
                "scenario_action": f"invariant_{str(profile['endpoint_tokens'][0])}_write",
                "title_suffix": normalized_path,
                "category": "state_machine" if forbidden else ("concurrency" if mode == "duplicate_write" else "invariant"),
            }
        return {}

    @staticmethod
    def _invariant_write_body(api_doc: str, method: str, path: str, entity: str) -> dict[str, Any] | None:
        example = _markdown_request_example(api_doc, method, path)
        if not example:
            return {}
        if not isinstance(example, dict):
            return None
        rendered = SemanticScenarioGenerator._bind_dependency_placeholders(example, entity)
        if SemanticScenarioGenerator._has_unresolved_dependency_placeholder(rendered):
            return None
        return rendered

    @staticmethod
    def _preferred_read_endpoint(endpoints: list[Any]) -> str:
        candidates = [str(item or "").strip() for item in endpoints if str(item or "").strip().startswith("/")]
        for path in candidates:
            if not path_has_placeholders(path):
                return path
        return ""

    @staticmethod
    def _preferred_write_endpoint(api_doc: str, entity: str) -> str:
        _entities, _states, endpoints = _api_facts(api_doc, re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I))
        candidates = [
            str(item.get("path") or "")
            for item in endpoints
            if str(item.get("entity") or "") == entity
            and str(item.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
            and str(item.get("path") or "").startswith("/")
            and not path_has_placeholders(str(item.get("path") or ""))
        ]
        return candidates[0] if candidates else ""

    @staticmethod
    def _dependency_write_body(api_doc: str, entity: str, target_entity: str) -> dict[str, Any]:
        write_path = SemanticScenarioGenerator._preferred_write_endpoint(api_doc, entity)
        if not write_path:
            return {}
        example = _markdown_request_example(api_doc, "POST", write_path)
        if not isinstance(example, dict) or not example:
            return {}
        rendered = SemanticScenarioGenerator._bind_dependency_placeholders(example, target_entity)
        if SemanticScenarioGenerator._has_unresolved_dependency_placeholder(rendered):
            return {}
        return rendered

    @staticmethod
    def _bind_dependency_placeholders(value: Any, target_entity: str, field_name: str = "") -> Any:
        if isinstance(value, dict):
            return {
                str(key): SemanticScenarioGenerator._bind_dependency_placeholders(child, target_entity, str(key))
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [SemanticScenarioGenerator._bind_dependency_placeholders(child, target_entity, field_name) for child in value]
        if not isinstance(value, str):
            return value
        normalized_target = re.sub(r"[^a-z0-9]+", "", str(target_entity or "").lower())
        normalized_field = re.sub(r"[^a-z0-9]+", "", str(field_name or "").lower())

        def repl(match: re.Match[str]) -> str:
            placeholder = re.sub(r"[^a-z0-9]+", "", str(match.group(1) or "").lower())
            if normalized_target and normalized_target in placeholder and "id" in placeholder:
                return "{id}"
            return match.group(0)

        rendered = re.sub(r"<([A-Za-z_]\w*)>", repl, value)
        if normalized_target and normalized_target in normalized_field and "id" in normalized_field and rendered == value:
            return "{id}"
        return rendered

    @staticmethod
    def _has_unresolved_dependency_placeholder(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, dict):
            return any(
                SemanticScenarioGenerator._has_unresolved_dependency_placeholder(key)
                or SemanticScenarioGenerator._has_unresolved_dependency_placeholder(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(SemanticScenarioGenerator._has_unresolved_dependency_placeholder(child) for child in value)
        if not isinstance(value, str):
            return False
        normalized = normalize_path_placeholders(value)
        placeholders = re.findall(r"\{([A-Za-z_]\w*)\}", normalized)
        return any(str(name or "").strip().lower() != "id" for name in placeholders)

    @staticmethod
    def _id(*parts: Any) -> str:
        return "SCN_SRC_" + hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:16]
