"""Source-derived business behavior graph.

This remains the V12 state-graph module.  It now derives states, transitions,
invariants and dependencies exclusively from the current project's requirement
text, API contract and database schema.  It deliberately has no industry
lifecycle template, default endpoint or default business entity.
"""
from __future__ import annotations

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


@dataclass
class StateEdge:
    source_entity: str
    source_state: str
    target_entity: str
    target_state: str
    relation: str = "depends_on"
    condition: str = ""
    risk_score: float = 0.0


@dataclass
class BusinessStateGraph:
    entity: str
    states: dict[str, StateNode] = field(default_factory=dict)
    transitions: list[StateTransition] = field(default_factory=list)
    edges: list[StateEdge] = field(default_factory=list)
    source_refs: list[dict[str, str]] = field(default_factory=list)

    def add_state(self, name: str, invariants: list[str] | None = None,
                  conditions: list[str] | None = None, risk_score: float = 0.0,
                  source_refs: list[dict[str, str]] | None = None,
                  observed_from_api: bool = False, observed_from_doc: bool = False) -> None:
        name = _state(name)
        if not name:
            return
        node = self.states.get(name)
        if node is None:
            self.states[name] = StateNode(
                entity=self.entity, state=name, invariants=list(invariants or []),
                conditions=list(conditions or []), risk_score=float(risk_score or 0.0),
                observed_from_api=observed_from_api, observed_from_doc=observed_from_doc,
                source_refs=_dedupe_refs(source_refs or []),
            )
            return
        node.invariants = _dedupe(node.invariants + list(invariants or []))
        node.conditions = _dedupe(node.conditions + list(conditions or []))
        node.source_refs = _dedupe_refs(node.source_refs + list(source_refs or []))
        node.risk_score = max(node.risk_score, float(risk_score or 0.0))
        node.observed_from_api = node.observed_from_api or observed_from_api
        node.observed_from_doc = node.observed_from_doc or observed_from_doc

    def add_transition(self, transition: StateTransition) -> None:
        key = (transition.from_state, transition.to_state, transition.action,
               transition.api_endpoint, transition.is_forbidden)
        if any((item.from_state, item.to_state, item.action, item.api_endpoint,
                item.is_forbidden) == key for item in self.transitions):
            return
        self.transitions.append(transition)
        self.add_state(transition.from_state, source_refs=transition.source_refs)
        self.add_state(transition.to_state, source_refs=transition.source_refs)
        if transition.is_forbidden:
            self.states[transition.to_state].risk_score = max(self.states[transition.to_state].risk_score, 0.9)
        elif transition.is_boundary:
            self.states[transition.to_state].risk_score = max(self.states[transition.to_state].risk_score, 0.5)

    def add_edge(self, source_entity: str, source_state: str, target_entity: str,
                 target_state: str, relation: str = "depends_on", condition: str = "") -> None:
        edge = StateEdge(source_entity, source_state, target_entity, target_state,
                         relation, condition, 0.7 if relation == "conflicts" else 0.4)
        if edge not in self.edges:
            self.edges.append(edge)

    def conflict_states(self) -> list[StateTransition]:
        grouped: dict[tuple[str, str], list[StateTransition]] = defaultdict(list)
        for transition in self.transitions:
            grouped[(transition.from_state, transition.action)].append(transition)
        return [item for items in grouped.values() if len(items) > 1 for item in items]

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
                    "state": node.state, "invariants": node.invariants,
                    "conditions": node.conditions, "constraints": node.constraints,
                    "risk_score": node.risk_score, "depends_on": node.depends_on,
                    "conflicts_with": node.conflicts_with,
                    "observed_from_api": node.observed_from_api,
                    "observed_from_doc": node.observed_from_doc,
                    "source_refs": node.source_refs,
                }
                for name, node in self.states.items()
            },
            "transitions": [
                {
                    "from": item.from_state, "to": item.to_state, "action": item.action,
                    "endpoint": item.api_endpoint, "normal": item.is_normal,
                    "forbidden": item.is_forbidden, "boundary": item.is_boundary,
                    "risk_score": item.risk_score, "triggers": item.trigger_conditions,
                    "depends_on": f"{item.depends_on_entity}/{item.depends_on_state}" if item.depends_on_entity else "",
                    "source_refs": item.source_refs,
                }
                for item in self.transitions
            ],
            "edges": [
                {"source": f"{item.source_entity}/{item.source_state}",
                 "target": f"{item.target_entity}/{item.target_state}",
                 "relation": item.relation, "condition": item.condition}
                for item in self.edges
            ],
            "stats": {
                "total_states": len(self.states), "total_transitions": len(self.transitions),
                "cross_entity_edges": len(self.edges), "forbidden_paths": len(self.forbidden_paths()),
                "conflict_paths": len(self.conflict_states()),
                "top_risk": [(item.state, item.risk_score) for item in self.top_risk_states(5)],
            },
        }


class BusinessStateGraphBuilder:
    """Build a graph from source facts only; missing source facts stay missing."""

    _transition_re = re.compile(
        r"(?P<before>[A-Z][A-Z0-9_]{1,64}|[\u4e00-\u9fff]{2,24})\s*(?:->|→|=>)\s*"
        r"(?P<after>[A-Z][A-Z0-9_]{1,64}|[\u4e00-\u9fff]{2,24})"
    )
    _modal_re = re.compile(r"\b(?:must|shall|cannot|must\s+not|only)\b|必须|不得|不允许|不可|只能|禁止", re.I)
    _forbidden_re = re.compile(r"forbidden|invalid|禁止|不得|不允许|不可", re.I)
    _state_field_re = re.compile(r"(?:^|[_\-.])(status|state|phase|stage|lifecycle)(?:$|[_\-.])", re.I)

    def __init__(self) -> None:
        self.graphs: dict[str, BusinessStateGraph] = {}

    def build(self, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, BusinessStateGraph]:
        api_entities, api_states, endpoints = _parse_api(api_spec_text, self._state_field_re)
        schema_entities, schema_states, dependencies = _parse_schema(db_schema_text, self._state_field_re)
        sections = self._parse_document(prd_text)
        source_entities: dict[str, list[dict[str, str]]] = defaultdict(list)
        for entity, refs in api_entities.items():
            source_entities[entity].extend(refs)
        for entity, refs in schema_entities.items():
            source_entities[entity].extend(refs)
        for entity in sorted(source_entities):
            graph = BusinessStateGraph(entity=entity, source_refs=_dedupe_refs(source_entities[entity]))
            for state, refs in api_states.get(entity, {}).items():
                graph.add_state(state, source_refs=refs, observed_from_api=True)
            for state, refs in schema_states.get(entity, {}).items():
                graph.add_state(state, source_refs=refs, observed_from_api=True)
            self.graphs[entity] = graph

        known_states: dict[str, set[str]] = {
            entity: set(api_states.get(entity, {})) | set(schema_states.get(entity, {}))
            for entity in self.graphs
        }
        for section in sections:
            entity = _best_entity(section["states"], known_states)
            if not entity:
                continue
            graph = self.graphs[entity]
            for transition in section["transitions"]:
                action, endpoint = _source_named_action(transition["line"], entity, endpoints)
                graph.add_transition(StateTransition(
                    from_state=transition["before"], to_state=transition["after"],
                    action=action, api_endpoint=endpoint,
                    guard_conditions=[section["title"]] if section["forbidden"] else [],
                    is_normal=not section["forbidden"], is_forbidden=section["forbidden"],
                    risk_score=0.9 if section["forbidden"] else 0.2,
                    source_refs=[transition["source_ref"]],
                ))
            for invariant, ref in section["invariants"]:
                for state in list(graph.states) or ["*"]:
                    graph.add_state(state, invariants=[invariant], source_refs=[ref], observed_from_doc=True)
        for child, parent, ref in dependencies:
            if child in self.graphs and parent in self.graphs:
                self.graphs[child].add_edge(child, "*", parent, "*", "depends_on", ref["quote"])
        return self.graphs

    def _parse_document(self, text: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for number, raw in enumerate(str(text or "").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                current = {"title": title, "forbidden": bool(self._forbidden_re.search(title)),
                           "states": set(), "transitions": [], "invariants": []}
                output.append(current)
                continue
            if current is None:
                continue
            ref = _ref("requirement", f"line:{number}", line)
            for match in self._transition_re.finditer(line):
                before, after = _state(match.group("before")), _state(match.group("after"))
                if before and after:
                    current["states"].update((before, after))
                    current["transitions"].append({"before": before, "after": after, "line": line, "source_ref": ref})
            if self._modal_re.search(line):
                current["invariants"].append((line, ref))
        return [item for item in output if item["states"] or item["invariants"]]

    # Compatibility helpers now return only extracted source facts.
    def _extract_entities(self, prd: str) -> list[str]:
        return sorted({_entity_from_heading(line) for line in str(prd or "").splitlines() if line.startswith("#")} - {""})

    def _extract_api_actions(self, api_spec: str) -> dict[str, set[str]]:
        _, _, endpoints = _parse_api(api_spec, self._state_field_re)
        out: dict[str, set[str]] = defaultdict(set)
        for endpoint in endpoints:
            if endpoint["entity"] and endpoint["action"]:
                out[endpoint["entity"]].add(endpoint["action"])
        return out

    def _extract_api_states(self, api_spec: str) -> dict[str, list[str]]:
        _, states, _ = _parse_api(api_spec, self._state_field_re)
        return {entity: sorted(values) for entity, values in states.items()}

    def _extract_invariants(self, prd: str, entity: str, state: str) -> list[str]:
        return [line.strip() for line in str(prd or "").splitlines() if self._modal_re.search(line)][:20]

    def _find_endpoint(self, api_spec: str, entity: str, action: str) -> str:
        _, _, endpoints = _parse_api(api_spec, self._state_field_re)
        for endpoint in endpoints:
            if endpoint["entity"] == entity and endpoint["action"] == action:
                return endpoint["path"]
        return ""

    def _generic_pattern(self, entity: str) -> dict[str, list[Any]]:
        return {"states": [], "normal": [], "forbidden": [], "boundary": []}


def _state(value: Any) -> str:
    text = str(value or "").strip().strip("`'\"[](){}<>.,;:：；。")
    if not text or len(text) > 64 or any(char.isspace() for char in text):
        return ""
    return text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}|[\u4e00-\u9fff]{2,24}", text) else ""


def _entity(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")
    if text.endswith("ies") and len(text) > 4:
        text = text[:-3] + "y"
    elif text.endswith("s") and len(text) > 3 and not text.endswith("ss"):
        text = text[:-1]
    return text[:80]


def _entity_from_heading(line: str) -> str:
    return _entity(str(line or "").lstrip("#").strip().split(":", 1)[0].split("：", 1)[0])


def _ref(source_type: str, locator: str, quote: str) -> dict[str, str]:
    return {"source_type": source_type, "locator": locator, "quote": str(quote)[:500]}


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _dedupe_refs(values: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        item = _ref(str(value.get("source_type") or ""), str(value.get("locator") or ""), str(value.get("quote") or ""))
        key = (item["source_type"], item["locator"], item["quote"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _parse_api(text: str, state_field_re: re.Pattern[str]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, list[dict[str, str]]]], list[dict[str, str]]]:
    entities: dict[str, list[dict[str, str]]] = defaultdict(list)
    states: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    endpoints: list[dict[str, str]] = []
    try:
        spec = json.loads(text) if str(text or "").lstrip().startswith("{") else {}
    except Exception:
        spec = {}
    paths = spec.get("paths") if isinstance(spec, dict) else None
    if isinstance(paths, dict):
        for path, operations in paths.items():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                if str(method).lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                    continue
                operation = operation if isinstance(operation, dict) else {}
                entity, action = _path_entity_action(str(path), str(operation.get("operationId") or ""))
                if entity:
                    ref = _ref("openapi", f"paths.{path}.{method}", str(operation.get("summary") or operation.get("operationId") or path))
                    entities[entity].append(ref)
                    endpoints.append({"entity": entity, "action": action, "path": str(path), "method": str(method).upper(), "summary": ref["quote"]})
        schemas = _as_dict(_as_dict(spec.get("components")).get("schemas"))
        for name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            entity = _entity(name)
            for field, definition in _as_dict(schema.get("properties")).items():
                if not state_field_re.search(str(field)) or not isinstance(definition, dict):
                    continue
                for value in definition.get("enum") or []:
                    token = _state(value)
                    if token:
                        states[entity][token].append(_ref("openapi", f"components.schemas.{name}.properties.{field}", token))
        return entities, states, endpoints
    pattern = re.compile(r"(?im)^###\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9_:\-{}./]+)")
    for match in pattern.finditer(str(text or "")):
        method, path = match.group(1).upper(), match.group(2)
        entity, action = _path_entity_action(path, "")
        if entity:
            ref = _ref("api_document", f"line:{str(text).count(chr(10), 0, match.start()) + 1}", match.group(0))
            entities[entity].append(ref)
            endpoints.append({"entity": entity, "action": action, "path": path, "method": method, "summary": match.group(0)})
    return entities, states, endpoints


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _path_entity_action(path: str, operation_id: str) -> tuple[str, str]:
    parts = [part for part in str(path or "").strip("/").split("/") if part and not part.startswith(":") and not part.startswith("{")]
    while parts and (parts[0].lower() == "api" or re.fullmatch(r"v\d+", parts[0].lower())):
        parts.pop(0)
    entity = _entity(parts[0]) if parts else ""
    action = _entity(parts[-1]) if len(parts) > 1 else _entity(operation_id)
    return entity, "" if action == entity else action


def _parse_schema(text: str, state_field_re: re.Pattern[str]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, list[dict[str, str]]]], list[tuple[str, str, dict[str, str]]]]:
    entities: dict[str, list[dict[str, str]]] = defaultdict(list)
    states: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    dependencies: list[tuple[str, str, dict[str, str]]] = []
    for match in re.finditer(r"(?is)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_][A-Za-z0-9_]*)[\"`]?\s*\((.*?)\);", str(text or "")):
        entity, body = _entity(match.group(1)), match.group(2)
        entities[entity].append(_ref("database_schema", entity, f"CREATE TABLE {match.group(1)}"))
        for line in body.splitlines():
            if state_field_re.search(line):
                for value in re.findall(r"'([^']+)'", line):
                    token = _state(value)
                    if token:
                        states[entity][token].append(_ref("database_schema", entity, line.strip()))
            for parent in re.findall(r"(?i)REFERENCES\s+[\"`]?([A-Za-z_][A-Za-z0-9_]*)", line):
                dependencies.append((entity, _entity(parent), _ref("database_schema", entity, line.strip())))
    return entities, states, dependencies


def _best_entity(states: set[str], known: dict[str, set[str]]) -> str:
    ranked: list[tuple[float, str]] = []
    for entity, candidates in known.items():
        if not states or not candidates:
            continue
        score = len(states & candidates) / len(states | candidates)
        if score:
            ranked.append((score, entity))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked and ranked[0][0] >= 0.15 else ""


def _source_named_action(line: str, entity: str, endpoints: list[dict[str, str]]) -> tuple[str, str]:
    for endpoint in endpoints:
        if endpoint["entity"] != entity:
            continue
        action = endpoint["action"]
        if action and re.search(rf"\b{re.escape(action)}\b", line, re.I):
            return action, endpoint["path"]
    return "", ""
