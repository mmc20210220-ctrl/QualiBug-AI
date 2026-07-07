"""Source-derived behavior graph and incremental slice contract for V12.

The builder does not infer business domains. It turns only source-bound state
transitions, invariants and schema dependencies into behavior slices. Rules
without a defensible entity binding are emitted as coverage gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StateNode:
    entity: str
    state: str
    invariants: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    depends_on: list[tuple[str, str]] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    observed_from_api: bool = False
    observed_from_doc: bool = False
    source_refs: list[dict[str, str]] = field(default_factory=list)


@dataclass
class StateTransition:
    from_state: str
    to_state: str
    action: str
    api_endpoint: str = ""
    guard_conditions: list[str] = field(default_factory=list)
    trigger_conditions: list[str] = field(default_factory=list)
    is_normal: bool = True
    is_forbidden: bool = False
    is_boundary: bool = False
    is_concurrent: bool = False
    risk_score: float = 0.0
    depends_on_entity: str = ""
    depends_on_state: str = ""
    source_refs: list[dict[str, str]] = field(default_factory=list)
    behavior_slice_id: str = ""


@dataclass
class StateEdge:
    source_entity: str
    source_state: str
    target_entity: str
    target_state: str
    relation: str = "depends_on"
    condition: str = ""
    risk_score: float = 0.0
    source_refs: list[dict[str, str]] = field(default_factory=list)


@dataclass
class BehaviorSlice:
    """One source-bound, independently schedulable behavior obligation."""

    slice_id: str
    entity: str
    kind: str
    states: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    priority: float = 0.0
    source_refs: list[dict[str, str]] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slice_id": self.slice_id,
            "entity": self.entity,
            "kind": self.kind,
            "states": list(self.states),
            "endpoints": list(self.endpoints),
            "priority": self.priority,
            "source_refs": _refs(self.source_refs),
            "evidence_gaps": _unique(self.evidence_gaps),
        }


def behavior_slice_id(kind: str, entity: str, *parts: Any) -> str:
    """Deterministic identity that excludes project data and raw evidence."""
    canonical = "|".join([str(kind or ""), _entity(entity), *(str(item or "") for item in parts)])
    return "BHV_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass
class BusinessStateGraph:
    entity: str
    states: dict[str, StateNode] = field(default_factory=dict)
    transitions: list[StateTransition] = field(default_factory=list)
    edges: list[StateEdge] = field(default_factory=list)
    source_refs: list[dict[str, str]] = field(default_factory=list)

    def add_state(
        self,
        name: str,
        invariants: list[str] | None = None,
        conditions: list[str] | None = None,
        risk_score: float = 0.0,
        source_refs: list[dict[str, str]] | None = None,
        observed_from_api: bool = False,
        observed_from_doc: bool = False,
    ) -> None:
        name = _state(name)
        if not name:
            return
        if name not in self.states:
            self.states[name] = StateNode(
                self.entity,
                name,
                list(invariants or []),
                list(conditions or []),
                [],
                float(risk_score or 0),
                [],
                [],
                observed_from_api,
                observed_from_doc,
                _refs(source_refs or []),
            )
            return
        node = self.states[name]
        node.invariants = _unique(node.invariants + list(invariants or []))
        node.conditions = _unique(node.conditions + list(conditions or []))
        node.source_refs = _refs(node.source_refs + list(source_refs or []))
        node.risk_score = max(node.risk_score, float(risk_score or 0))
        node.observed_from_api |= observed_from_api
        node.observed_from_doc |= observed_from_doc

    def add_transition(self, item: StateTransition) -> None:
        key = (item.from_state, item.to_state, item.action, item.api_endpoint, item.is_forbidden)
        if any((row.from_state, row.to_state, row.action, row.api_endpoint, row.is_forbidden) == key for row in self.transitions):
            return
        self.transitions.append(item)
        self.add_state(item.from_state, source_refs=item.source_refs)
        self.add_state(item.to_state, source_refs=item.source_refs)
        if item.is_forbidden:
            self.states[item.to_state].risk_score = max(self.states[item.to_state].risk_score, 0.9)
        elif item.is_boundary:
            self.states[item.to_state].risk_score = max(self.states[item.to_state].risk_score, 0.5)

    def add_edge(
        self,
        source_entity: str,
        source_state: str,
        target_entity: str,
        target_state: str,
        relation: str = "depends_on",
        condition: str = "",
        source_refs: list[dict[str, str]] | None = None,
    ) -> None:
        edge = StateEdge(
            source_entity,
            source_state,
            target_entity,
            target_state,
            relation,
            condition,
            0.7 if relation == "conflicts" else 0.4,
            _refs(source_refs or []),
        )
        if edge not in self.edges:
            self.edges.append(edge)

    def conflict_states(self) -> list[StateTransition]:
        groups: dict[tuple[str, str], list[StateTransition]] = defaultdict(list)
        for item in self.transitions:
            groups[(item.from_state, item.action)].append(item)
        return [item for values in groups.values() if len(values) > 1 for item in values]

    def top_risk_states(self, n: int = 5) -> list[StateNode]:
        return sorted(self.states.values(), key=lambda item: item.risk_score, reverse=True)[:n]

    def forbidden_paths(self) -> list[StateTransition]:
        return [item for item in self.transitions if item.is_forbidden]

    def normal_paths(self) -> list[StateTransition]:
        return [item for item in self.transitions if item.is_normal and not item.is_forbidden]

    def boundary_paths(self) -> list[StateTransition]:
        return [item for item in self.transitions if item.is_boundary]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "source_refs": self.source_refs,
            "states": {
                name: {
                    "state": node.state,
                    "invariants": node.invariants,
                    "conditions": node.conditions,
                    "constraints": node.constraints,
                    "risk_score": node.risk_score,
                    "depends_on": node.depends_on,
                    "conflicts_with": node.conflicts_with,
                    "observed_from_api": node.observed_from_api,
                    "observed_from_doc": node.observed_from_doc,
                    "source_refs": node.source_refs,
                }
                for name, node in self.states.items()
            },
            "transitions": [
                {
                    "from": item.from_state,
                    "to": item.to_state,
                    "action": item.action,
                    "endpoint": item.api_endpoint,
                    "normal": item.is_normal,
                    "forbidden": item.is_forbidden,
                    "boundary": item.is_boundary,
                    "risk_score": item.risk_score,
                    "triggers": item.trigger_conditions,
                    "depends_on": f"{item.depends_on_entity}/{item.depends_on_state}" if item.depends_on_entity else "",
                    "source_refs": item.source_refs,
                    "behavior_slice_id": item.behavior_slice_id,
                }
                for item in self.transitions
            ],
            "edges": [
                {
                    "source": f"{item.source_entity}/{item.source_state}",
                    "target": f"{item.target_entity}/{item.target_state}",
                    "relation": item.relation,
                    "condition": item.condition,
                    "source_refs": item.source_refs,
                }
                for item in self.edges
            ],
            "stats": {
                "total_states": len(self.states),
                "total_transitions": len(self.transitions),
                "cross_entity_edges": len(self.edges),
                "forbidden_paths": len(self.forbidden_paths()),
                "conflict_paths": len(self.conflict_states()),
                "top_risk": [(item.state, item.risk_score) for item in self.top_risk_states(5)],
            },
        }


class BusinessStateGraphBuilder:
    _transition = re.compile(r"(?P<before>[A-Z][A-Z0-9_]{1,64}|[\u4e00-\u9fff]{2,24})\s*(?:->|→|=>)\s*(?P<after>[A-Z][A-Z0-9_]{1,64}|[\u4e00-\u9fff]{2,24})")
    _modal = re.compile(r"\b(?:must|shall|cannot|must\s+not|only|become|becomes)\b|必须|不得|不允许|不可|只能|禁止|变为|变成", re.I)
    _forbidden = re.compile(r"forbidden|invalid|禁止|不得|不允许|不可", re.I)
    _state_field = re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I)

    def __init__(self) -> None:
        self.graphs: dict[str, BusinessStateGraph] = {}
        self.behavior_slices: list[BehaviorSlice] = []
        self.coverage_gaps: list[dict[str, Any]] = []
        self.bound_invariants: list[dict[str, Any]] = []
        self.endpoint_catalog: list[dict[str, str]] = []

    def build(self, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, BusinessStateGraph]:
        api_entities, api_states, endpoints = _api_facts(api_spec_text, self._state_field)
        self.endpoint_catalog = list(endpoints)
        db_entities, db_states, dependencies = _schema_facts(db_schema_text, self._state_field)
        source_map: dict[str, list[dict[str, str]]] = defaultdict(list)
        for source in (api_entities, db_entities):
            for entity, refs in source.items():
                source_map[entity].extend(refs)
        self.graphs = {entity: BusinessStateGraph(entity, source_refs=_refs(refs)) for entity, refs in source_map.items()}
        self.behavior_slices = []
        self.coverage_gaps = []
        self.bound_invariants = []
        for entity, graph in self.graphs.items():
            for name, refs in api_states.get(entity, {}).items():
                graph.add_state(name, source_refs=refs, observed_from_api=True)
            for name, refs in db_states.get(entity, {}).items():
                graph.add_state(name, source_refs=refs, observed_from_doc=True)

        known = {entity: set(api_states.get(entity, {})) | set(db_states.get(entity, {})) for entity in self.graphs}
        source_fields = _source_field_index(api_spec_text, db_schema_text)
        for section in self._sections(prd_text):
            entity, binding_mode = _best_entity_for_section(section, known, source_fields)
            if not entity:
                self._record_unbound_section(section)
                continue
            graph = self.graphs[entity]
            for row in section["transitions"]:
                action, endpoint = _source_action(row["line"], entity, endpoints)
                transition = StateTransition(
                    row["before"],
                    row["after"],
                    action,
                    endpoint,
                    [section["title"]] if section["forbidden"] else [],
                    [],
                    not section["forbidden"],
                    section["forbidden"],
                    False,
                    False,
                    0.9 if section["forbidden"] else 0.2,
                    "",
                    "",
                    [row["ref"]],
                )
                transition.behavior_slice_id = behavior_slice_id(
                    "transition",
                    entity,
                    transition.from_state,
                    transition.to_state,
                    transition.action,
                    transition.api_endpoint,
                    "forbidden" if transition.is_forbidden else "normal",
                )
                graph.add_transition(transition)
            for invariant, ref in section["invariants"]:
                if graph.states:
                    for name in list(graph.states):
                        graph.add_state(name, invariants=[invariant], source_refs=[ref], observed_from_doc=True)
                else:
                    self.bound_invariants.append({
                        "entity": entity,
                        "invariant": invariant,
                        "source_refs": _refs([ref] + graph.source_refs),
                        "binding_mode": binding_mode,
                    })

        for child, parent, ref in dependencies:
            if child in self.graphs and parent in self.graphs:
                self.graphs[child].add_edge(child, "*", parent, "*", "depends_on", ref["quote"], [ref])

        self.behavior_slices = self.build_slices()
        return self.graphs

    def _record_unbound_section(self, section: dict[str, Any]) -> None:
        refs = [row["ref"] for row in section.get("transitions", [])]
        refs.extend(ref for _, ref in section.get("invariants", []))
        if not refs:
            return
        self.coverage_gaps.append({
            "kind": "UNBOUND_REQUIREMENT",
            "title": str(section.get("title") or "untitled_requirement"),
            "reason": "no_source_derived_entity_binding",
            "source_refs": _refs(refs),
            "required_asset": "source_entity_binding_or_runtime_observation",
        })

    def build_slices(self) -> list[BehaviorSlice]:
        """Create deterministic source-bound slices without routes or actors."""
        slices: list[BehaviorSlice] = []
        for entity, graph in sorted(self.graphs.items()):
            for transition in graph.transitions:
                slice_id = transition.behavior_slice_id or behavior_slice_id(
                    "transition", entity, transition.from_state, transition.to_state,
                    transition.action, transition.api_endpoint,
                    "forbidden" if transition.is_forbidden else "normal",
                )
                transition.behavior_slice_id = slice_id
                gaps: list[str] = []
                if not transition.action or not transition.api_endpoint:
                    gaps.append("ACTION_ROUTE_NOT_SOURCE_BOUND")
                slices.append(BehaviorSlice(
                    slice_id=slice_id,
                    entity=entity,
                    kind="transition",
                    states=_unique([transition.from_state, transition.to_state]),
                    endpoints=[transition.api_endpoint] if transition.api_endpoint else [],
                    priority=max(float(transition.risk_score or 0.0), 0.9 if transition.is_forbidden else 0.35),
                    source_refs=_refs(transition.source_refs or graph.source_refs),
                    evidence_gaps=gaps,
                ))
            for state_name, node in graph.states.items():
                for invariant in node.invariants:
                    observation_endpoints = _observation_endpoints(entity, self.endpoint_catalog)
                    gaps = [] if observation_endpoints else ["OBSERVATION_ROUTE_NOT_SOURCE_BOUND"]
                    slices.append(BehaviorSlice(
                        slice_id=behavior_slice_id("invariant", entity, state_name, invariant),
                        entity=entity,
                        kind="invariant",
                        states=[state_name],
                        endpoints=observation_endpoints,
                        priority=max(float(node.risk_score or 0.0), 0.55),
                        source_refs=_refs(node.source_refs or graph.source_refs),
                        evidence_gaps=gaps,
                    ))
            for edge in graph.edges:
                slices.append(BehaviorSlice(
                    slice_id=behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation),
                    entity=entity,
                    kind="dependency",
                    states=_unique([edge.source_state, edge.target_state]),
                    endpoints=[],
                    priority=max(float(edge.risk_score or 0.0), 0.4),
                    source_refs=_refs(edge.source_refs or graph.source_refs),
                    evidence_gaps=["CROSS_ENTITY_OBSERVATION_CONTRACT_MISSING"],
                ))
            observation_endpoints = _observation_endpoints(entity, self.endpoint_catalog)
            has_entity_slice = any(item.entity == entity and item.endpoints for item in slices)
            if observation_endpoints and not has_entity_slice:
                slices.append(BehaviorSlice(
                    slice_id=behavior_slice_id("source_observation", entity, ",".join(observation_endpoints)),
                    entity=entity,
                    kind="source_observation",
                    states=sorted(graph.states),
                    endpoints=observation_endpoints,
                    priority=0.45 if graph.states else 0.3,
                    source_refs=_refs(graph.source_refs),
                    evidence_gaps=[],
                ))
        for item in self.bound_invariants:
            observation_endpoints = _observation_endpoints(str(item["entity"]), self.endpoint_catalog)
            gaps = ["STATE_ANCHOR_NOT_SOURCE_BOUND"]
            if not observation_endpoints:
                gaps.insert(0, "OBSERVATION_ROUTE_NOT_SOURCE_BOUND")
            slices.append(BehaviorSlice(
                slice_id=behavior_slice_id("invariant", item["entity"], item["invariant"]),
                entity=item["entity"],
                kind="invariant",
                states=[],
                endpoints=observation_endpoints,
                priority=0.55,
                source_refs=_refs(item["source_refs"]),
                evidence_gaps=gaps,
            ))
        deduped: dict[str, BehaviorSlice] = {}
        for item in slices:
            existing = deduped.get(item.slice_id)
            if existing is None:
                deduped[item.slice_id] = item
                continue
            existing.source_refs = _refs(existing.source_refs + item.source_refs)
            existing.evidence_gaps = _unique(existing.evidence_gaps + item.evidence_gaps)
            existing.priority = max(existing.priority, item.priority)
        return sorted(deduped.values(), key=lambda item: (-item.priority, item.entity, item.slice_id))

    def behavior_contract(self) -> dict[str, Any]:
        by_kind: dict[str, int] = defaultdict(int)
        for item in self.behavior_slices:
            by_kind[item.kind] += 1
        return {
            "slices": [item.to_dict() for item in self.behavior_slices],
            "coverage_gaps": list(self.coverage_gaps),
            "summary": {
                "total_slices": len(self.behavior_slices),
                "by_kind": dict(sorted(by_kind.items())),
                "coverage_gap_count": len(self.coverage_gaps),
                "source_field_bound_invariant_count": len(self.bound_invariants),
            },
        }

    def _sections(self, text: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for number, raw in enumerate(str(text or "").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            header = line.startswith("#") or (
                bool(self._forbidden.search(line))
                and line.endswith((":", "："))
                and not self._transition.search(line)
            )
            if header:
                title = line.lstrip("#").strip().rstrip("：:")
                current = {"title": title, "forbidden": bool(self._forbidden.search(title)), "states": set(), "transitions": [], "invariants": []}
                result.append(current)
                continue
            if current is None:
                continue
            ref = _ref("requirement", f"line:{number}", line)
            current["states"].update(_line_states(line))
            for match in self._transition.finditer(line):
                before, after = _state(match.group("before")), _state(match.group("after"))
                if before and after:
                    current["states"].update((before, after))
                    current["transitions"].append({"before": before, "after": after, "line": line, "ref": ref})
            if self._modal.search(line):
                current["invariants"].append((line, ref))
        return [item for item in result if item["states"] or item["invariants"]]

    def _extract_entities(self, prd: str) -> list[str]:
        return sorted({_entity(line.lstrip("#").strip()) for line in str(prd or "").splitlines() if line.startswith("#")} - {""})

    def _extract_api_actions(self, api_spec: str) -> dict[str, set[str]]:
        _, _, endpoints = _api_facts(api_spec, self._state_field)
        result: dict[str, set[str]] = defaultdict(set)
        for item in endpoints:
            if item["entity"] and item["action"]:
                result[item["entity"]].add(item["action"])
        return result

    def _extract_api_states(self, api_spec: str) -> dict[str, list[str]]:
        _, states, _ = _api_facts(api_spec, self._state_field)
        return {key: sorted(value) for key, value in states.items()}

    def _extract_invariants(self, prd: str, entity: str, state: str) -> list[str]:
        return [line.strip() for line in str(prd or "").splitlines() if self._modal.search(line)][:20]

    def _find_endpoint(self, api_spec: str, entity: str, action: str) -> str:
        _, _, endpoints = _api_facts(api_spec, self._state_field)
        return next((item["path"] for item in endpoints if item["entity"] == entity and item["action"] == action), "")

    def _generic_pattern(self, entity: str) -> dict[str, list[Any]]:
        return {"states": [], "normal": [], "forbidden": [], "boundary": []}


def _state(value: Any) -> str:
    text = str(value or "").strip().strip("`'\"[](){}<>.,;:：；。")
    valid = re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}|[\u4e00-\u9fff]{2,24}", text)
    return text if text and len(text) <= 64 and not any(char.isspace() for char in text) and valid else ""


def _line_states(line: str) -> set[str]:
    states: set[str] = set()
    for value in re.findall(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"", str(line or "")):
        for candidate in value:
            token = _state(candidate)
            if token:
                states.add(token)
    for candidate in re.findall(r"\b[A-Z][A-Z0-9_]{1,64}\b", str(line or "")):
        token = _state(candidate)
        if token:
            states.add(token)
    return states


def _entity(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")
    if text.endswith("ies") and len(text) > 4:
        text = text[:-3] + "y"
    elif text.endswith("s") and len(text) > 3 and not text.endswith("ss"):
        text = text[:-1]
    return text[:80]


def _ref(kind: str, locator: str, quote: str) -> dict[str, str]:
    return {"source_type": kind, "locator": locator, "quote": str(quote)[:500]}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _refs(values: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if isinstance(value, dict):
            item = _ref(str(value.get("source_type") or ""), str(value.get("locator") or ""), str(value.get("quote") or ""))
            key = (item["source_type"], item["locator"], item["quote"])
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _path(path: str, operation: str) -> tuple[str, str]:
    parts = [part for part in str(path or "").strip("/").split("/") if part and not part.startswith(":") and not part.startswith("{")]
    while parts and (parts[0].lower() == "api" or re.fullmatch(r"v\d+", parts[0].lower())):
        parts.pop(0)
    entity = _entity(parts[0]) if parts else ""
    action = _entity(parts[-1]) if len(parts) > 1 else _entity(operation)
    return entity, "" if action == entity else action


def _observation_endpoints(entity: str, endpoints: list[dict[str, str]]) -> list[str]:
    return list(dict.fromkeys(
        str(item.get("path") or "")
        for item in endpoints
        if str(item.get("entity") or "") == entity and str(item.get("method") or "").upper() in {"GET", "HEAD", "OPTIONS"} and str(item.get("path") or "").startswith("/")
    ))


def _api_facts(text: str, state_re: re.Pattern[str]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, list[dict[str, str]]]], list[dict[str, str]]]:
    entities: dict[str, list[dict[str, str]]] = defaultdict(list)
    states: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    endpoints: list[dict[str, str]] = []
    spec = _parse_structured_api_spec(text)
    if isinstance(spec.get("paths"), dict):
        for path, operations in spec["paths"].items():
            if isinstance(operations, dict):
                for method, operation in operations.items():
                    if str(method).lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                        operation = _dict(operation)
                        entity, action = _path(str(path), str(operation.get("operationId") or ""))
                        if entity:
                            ref = _ref("openapi", f"paths.{path}.{method}", str(operation.get("summary") or operation.get("operationId") or path))
                            entities[entity].append(ref)
                            endpoints.append({"entity": entity, "action": action, "path": str(path), "method": str(method).upper()})
        for name, schema in _dict(_dict(spec.get("components")).get("schemas")).items():
            if isinstance(schema, dict):
                for field, definition in _dict(schema.get("properties")).items():
                    if state_re.search(str(field)) and isinstance(definition, dict):
                        for value in definition.get("enum") or []:
                            token = _state(value)
                            if token:
                                states[_entity(name)][token].append(_ref("openapi", f"components.schemas.{name}.properties.{field}", token))
        return entities, states, endpoints
    for match in re.finditer(r"(?im)^###\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9_:\-{}./]+)", str(text or "")):
        entity, action = _path(match.group(2), "")
        if entity:
            ref = _ref("api_document", f"line:{str(text).count(chr(10), 0, match.start()) + 1}", match.group(0))
            entities[entity].append(ref)
            endpoints.append({"entity": entity, "action": action, "path": match.group(2), "method": match.group(1).upper()})
    return entities, states, endpoints


def _parse_structured_api_spec(text: str) -> dict[str, Any]:
    raw = str(text or "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw) if raw.lstrip().startswith("{") else {}
    except Exception:
        parsed = {}
    if isinstance(parsed, dict) and isinstance(parsed.get("paths"), dict):
        return parsed
    try:
        import yaml

        parsed = yaml.safe_load(raw)
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _schema_facts(text: str, state_re: re.Pattern[str]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, list[dict[str, str]]]], list[tuple[str, str, dict[str, str]]]]:
    entities: dict[str, list[dict[str, str]]] = defaultdict(list)
    states: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    deps: list[tuple[str, str, dict[str, str]]] = []
    for match in re.finditer(r"(?is)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s*\((.*?)\);", str(text or "")):
        entity, body = _entity(match.group(1)), match.group(2)
        entities[entity].append(_ref("database_schema", entity, f"CREATE TABLE {match.group(1)}"))
        for line in body.splitlines():
            if state_re.search(line):
                for value in re.findall(r"'([^']+)'", line):
                    token = _state(value)
                    if token:
                        states[entity][token].append(_ref("database_schema", entity, line.strip()))
            for parent in re.findall(r"(?i)REFERENCES\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)", line):
                deps.append((entity, _entity(parent), _ref("database_schema", entity, line.strip())))
    return entities, states, deps


def _source_field_index(api_spec_text: str, db_schema_text: str) -> dict[str, set[str]]:
    """Index source-declared field identifiers without attaching business semantics."""
    result: dict[str, set[str]] = defaultdict(set)
    spec = _parse_structured_api_spec(api_spec_text)
    for name, schema in _dict(_dict(spec.get("components")).get("schemas")).items():
        if not isinstance(schema, dict):
            continue
        entity = _entity(name)
        for field_name in _dict(schema.get("properties")).keys():
            result[entity].update(_identifier_tokens(str(field_name)))
    for match in re.finditer(r"(?is)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s*\((.*?)\);", str(db_schema_text or "")):
        entity, body = _entity(match.group(1)), match.group(2)
        for line in body.splitlines():
            field_match = re.match(r"\s*[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)", line)
            if field_match:
                result[entity].update(_identifier_tokens(field_match.group(1)))
    return result


def _identifier_tokens(value: str) -> set[str]:
    raw = {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,63}", str(value or ""))}
    parts = {part for token in raw for part in token.split("_") if len(part) >= 4}
    return raw | parts


def _invariant_tokens(section: dict[str, Any]) -> set[str]:
    ignored = {"must", "shall", "cannot", "equal", "only", "true", "false", "with", "from", "that", "this", "where", "when", "then", "status", "state", "type", "name", "code", "date", "time", "value", "count", "total", "amount", "identifier"}
    tokens: set[str] = set()
    for invariant, _ in section.get("invariants", []):
        for token in _identifier_tokens(str(invariant)):
            if token not in ignored and ("_" in token or len(token) >= 8):
                tokens.add(token)
    return tokens


def _best_entity_for_section(section: dict[str, Any], known: dict[str, set[str]], source_fields: dict[str, set[str]]) -> tuple[str, str]:
    state_entity = _best_entity(section.get("states", set()), known)
    if state_entity:
        return state_entity, "state_overlap"
    title_entity = _entity(section.get("title") or "")
    for entity in sorted(known):
        if entity and (title_entity == entity or title_entity.startswith(f"{entity}_") or title_entity.endswith(f"_{entity}")):
            return entity, "section_title"
    tokens = _invariant_tokens(section)
    if not tokens:
        return "", ""
    candidates: list[tuple[int, int, str]] = []
    for entity, fields in source_fields.items():
        overlap = tokens & fields
        weighted = sum(2 if "_" in token else 1 for token in overlap)
        if weighted:
            candidates.append((weighted, len(overlap), entity))
    candidates.sort(key=lambda row: (-row[0], -row[1], row[2]))
    if not candidates:
        return "", ""
    best_weight, best_count, entity = candidates[0]
    second_weight = candidates[1][0] if len(candidates) > 1 else 0
    if best_weight >= 2 and best_weight > second_weight and best_count >= 1:
        return entity, "source_field_overlap"
    return "", ""


def _best_entity(values: set[str], known: dict[str, set[str]]) -> str:
    candidates = [(len(values & states) / len(values | states), entity) for entity, states in known.items() if values and states and values & states]
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates[0][1] if candidates and candidates[0][0] >= 0.15 else ""


def _source_action(line: str, entity: str, endpoints: list[dict[str, str]]) -> tuple[str, str]:
    for item in endpoints:
        if item["entity"] == entity and item["action"] and re.search(rf"\b{re.escape(item['action'])}\b", line, re.I):
            return item["action"], item["path"]
    return "", ""
