"""Source-derived behavior graph and incremental slice contract for V12.

The builder does not infer business domains. It turns only source-bound state
transitions, invariants and schema dependencies into behavior slices. Rules
without a defensible entity binding are emitted as coverage gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEMANTIC_LEXICON_PATH = Path(__file__).resolve().parent / "policies" / "semantic_lexicon.json"
_SEMANTIC_LEXICON_CACHE: dict[str, Any] | None = None

# First-class System Behavior Space hooks — no method replacement on the builder.
BsgBuildHook = Callable[[Any, str, str, str], None]
BsgContractHook = Callable[[Any, dict[str, Any]], dict[str, Any]]
_BSG_BUILD_HOOK: BsgBuildHook | None = None
_BSG_CONTRACT_HOOK: BsgContractHook | None = None


def register_bsg_build_hook(hook: BsgBuildHook | None) -> None:
    """Post-build hook: may attach ``system_behavior_space`` on the builder."""
    global _BSG_BUILD_HOOK
    _BSG_BUILD_HOOK = hook


def register_bsg_contract_hook(hook: BsgContractHook | None) -> None:
    """Post-contract hook: may attach system-behavior slices/summary fields."""
    global _BSG_CONTRACT_HOOK
    _BSG_CONTRACT_HOOK = hook


def clear_bsg_hooks() -> None:
    register_bsg_build_hook(None)
    register_bsg_contract_hook(None)


def _semantic_lexicon() -> dict[str, Any]:
    global _SEMANTIC_LEXICON_CACHE
    if _SEMANTIC_LEXICON_CACHE is None:
        try:
            payload = json.loads(SEMANTIC_LEXICON_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        _SEMANTIC_LEXICON_CACHE = payload if isinstance(payload, dict) else {}
    return _SEMANTIC_LEXICON_CACHE


def _lexicon_list(name: str) -> list[str]:
    raw = _semantic_lexicon().get(name)
    return [str(item) for item in raw if str(item)] if isinstance(raw, list) else []


def _lexicon_groups(name: str) -> list[set[str]]:
    raw = _semantic_lexicon().get(name)
    if not isinstance(raw, list):
        return []
    groups: list[set[str]] = []
    for item in raw:
        if isinstance(item, list):
            group = {_entity(value) for value in item if str(value).strip()}
            if group:
                groups.append(group)
    return groups


def _semantic_text(value: Any) -> str:
    return re.sub(r"[\s_\-/]+", "", str(value or "").strip().lower())


def _state_aliases() -> dict[str, set[str]]:
    raw = _semantic_lexicon().get("state_aliases")
    result: dict[str, set[str]] = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        canonical = _state(key).upper()
        if not canonical:
            continue
        aliases = {_semantic_text(canonical), _semantic_text(canonical.lower())}
        if isinstance(value, list):
            aliases.update(_semantic_text(item) for item in value if str(item).strip())
        result[canonical] = {alias for alias in aliases if alias}
    return result


def _mentioned_states(text: str, known_states: set[str] | None = None) -> set[str]:
    resolved = {state.upper() for state in _line_states(text)}
    normalized_text = _semantic_text(text)
    for canonical, aliases in _state_aliases().items():
        if known_states and canonical not in known_states:
            continue
        if any(alias and alias in normalized_text for alias in aliases):
            resolved.add(canonical)
    if known_states:
        return {state for state in resolved if state in known_states}
    return resolved


def _verb_action_lexicon() -> dict[str, list[str]]:
    """Chinese -> English action-verb bridge (language resource, not domain hardcoding).

    Lets narrative PRD verbs (e.g. 支付) be routed to concrete API action tokens
    (e.g. pay) so prose state transitions become executable without per-project
    keyword tables.
    """
    raw = _semantic_lexicon().get("verb_action_lexicon")
    result: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key == "comment" or not isinstance(value, list):
                continue
            cleaned = [str(item).lower() for item in value if str(item).strip()]
            if cleaned:
                result[str(key)] = cleaned
    return result


def _denial_verbs() -> set[str]:
    """Generic denial/refusal verbs (language resource, not domain hardcoding).

    An interface whose documented identity performs a denial (驳回/拒绝/拒收/
    reject/deny/refuse/decline/...) cannot be the performer of a transition
    INTO the positive outcome state of the same flow. The set is only ever
    used to break a token tie inside the state-transition binding — never to
    invent an endpoint or to veto a unique match.
    """
    raw = _semantic_lexicon().get("denial_verbs")
    result: set[str] = set()
    if isinstance(raw, dict):
        for value in raw.values():
            if not isinstance(value, list):
                continue
            for item in value:
                text = str(item or "").strip().lower()
                if text:
                    result.add(text)
    return result


def _entity_token_lexicon() -> dict[str, list[str]]:
    """Chinese -> English ENTITY noun bridge (language resource, not domain hardcoding).

    Binds Chinese-only PRD sections (e.g. '退款', '订单') to the correct
    source-derived entity when the API catalog is English-only.
    """
    raw = _semantic_lexicon().get("entity_token_lexicon")
    result: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key == "comment" or not isinstance(value, list):
                continue
            cleaned = [str(item).lower() for item in value if str(item).strip()]
            if cleaned:
                result[str(key)] = cleaned
    return result


def _section_entity_via_tokens(
    section: dict[str, Any],
    endpoints: list[dict[str, str]],
    known: dict[str, set[str]],
) -> str:
    """Bind a Chinese-only PRD section to a source-derived entity.

    Uses the entity_token_lexicon (noun bridge, weighted +3 so the lifecycle
    SUBJECT entity wins) plus a verb->endpoint fallback (+1, it's the action's
    entity, not necessarily the subject). Returns the entity name or ''.
    """
    lex = _entity_token_lexicon()
    if not lex:
        return ""
    text = " ".join([
        str(section.get("title") or ""),
        *(str(row.get("line") or "") for row in section.get("transitions", [])),
        *(str(inv) for inv, _ in section.get("invariants", [])),
    ])
    scores: dict[str, int] = {}
    # Strong signal: an entity noun that DIRECTLY precedes 状态/state is the
    # state OWNER of the transition ("订单状态变为 REFUNDED" => order is the
    # subject whose state machine moves PAID->REFUNDED; 退款 is merely the
    # triggering action). Without this, a section mentioning both the subject
    # (订单) and the action noun (退款) ties at +3 and the alphabetical
    # tiebreaker wrongly picks 退款->refund, producing a dead transition (refund
    # has no PAID/REFUNDED state machine). Matching the lexicon key literally
    # against "<noun>状态" avoids greedy Chinese capture and needs no translation.
    # Universal: "order status becomes PAID" => order.
    for zh, en_tokens in lex.items():
        if f"{zh}状态" in text or f"{zh} 状态" in text:
            for en in en_tokens:
                ent_e = _entity(en)
                for entity in known:
                    if ent_e == _entity(entity) or en in _entity_aliases(entity):
                        scores[entity] = scores.get(entity, 0) + 10
        for en in en_tokens:
            if f"{en} status" in text or f"{en} state" in text:
                ent_e = _entity(en)
                for entity in known:
                    if ent_e == _entity(entity) or en in _entity_aliases(entity):
                        scores[entity] = scores.get(entity, 0) + 10
    for zh, en_tokens in lex.items():
        if zh not in text:
            continue
        for en in en_tokens:
            en_e = _entity(en)
            for entity in known:
                if en_e == _entity(entity) or en in _entity_aliases(entity):
                    scores[entity] = scores.get(entity, 0) + 3
    vlex = _verb_action_lexicon()
    for verb in vlex:
        if verb in text:
            act, ep = _route_narrative_action(verb, endpoints)
            if ep:
                for item in endpoints:
                    if str(item.get("path") or "") == ep:
                        ent = str(item.get("entity") or "")
                        if ent in known:
                            scores[ent] = scores.get(ent, 0) + 1
    if scores:
        return max(scores, key=lambda k: (scores[k], k))
    return ""


def _ordered_line_states(line: str) -> list[str]:
    """State tokens in appearance order (deterministic, unlike the set variant)."""
    found: list[str] = []
    for value in re.finditer(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"|\b[A-Z][A-Z0-9_]{1,64}\b", str(line or "")):
        for candidate in value.groups():
            token = _state(candidate)
            if token and token not in found:
                found.append(token)
    return found


def _extract_narrative_verb(line: str) -> str:
    """Extract the Chinese action verb that drives a narrative state transition.

    Picks the lexicon verb occurring closest *before* the transition phrase. Falls
    back to any lexicon verb present in the line. Returns '' if none.
    """
    lex = _verb_action_lexicon()
    if not lex:
        return ""
    marker = re.search(
        r"(?:状态[^，,\n]{0,20}(?:变更?为|变成|进入|流转到|转为|切换[到为]|更新为)"
        r"|become|changes?\s+to|transitioned?\s+to|set\s+to|updated?\s+to|moves?\s+to|enters?)",
        line,
        re.I,
    )
    marker_pos = marker.start() if marker else len(line)
    best_verb = ""
    best_pos = -1
    for verb in lex:
        idx = line.find(verb)
        while idx != -1:
            if idx < marker_pos and idx > best_pos:
                best_pos = idx
                best_verb = verb
            idx = line.find(verb, idx + len(verb))
    if best_verb:
        return best_verb
    for verb in lex:
        if verb in line:
            return verb
    return ""


def _route_narrative_action(
    verb: str,
    endpoints: list[dict[str, str]],
    entity: str = "",
) -> tuple[str, str]:
    """Route a narrative verb to a concrete mutating endpoint via the verb lexicon.

    Searches mutating (POST/PUT/PATCH) endpoints for an operation whose action,
    path tail, or summary overlaps the verb's candidate tokens. On a confident
    verb-stem / path-tail match a *collection* endpoint (whose `action` field is
    empty) is still routable: the path tail becomes the canonical action token so
    the transition is executable end-to-end. Entity-constrained routing (when
    `entity` is supplied) only reinforces an already-confident verb match, never
    creates one from nothing. Returns (action, endpoint_path); ('', '') if no
    confident match. Domain keywords are never matched.
    """
    if not verb:
        return "", ""
    lex = _verb_action_lexicon()
    candidates: set[str] = set(lex.get(verb, []))
    candidates.add(_entity(verb))
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", verb):
        candidates.add(verb.lower())
    candidates.discard("")
    if not candidates:
        return "", ""
    entity_e = _entity(entity) if entity else ""
    entity_aliases = _entity_aliases(entity_e) if entity_e else set()

    def _path_tail_matches(tail: str) -> bool:
        # Exact, prefix, or reversed-prefix (stem) match between the normalized
        # path tail and a candidate verb token. Handles plural/suffix variants
        # (refunds->refund, cancellations->cancel) WITHOUT substring false
        # positives (e.g. 'lock' must not match 'unlock').
        for c in candidates:
            if not c:
                continue
            if tail == c or tail.startswith(c) or c.startswith(tail):
                return True
        return False

    best: tuple[int, str, str] = (0, "", "")
    for item in endpoints:
        method = str(item.get("method") or "").upper()
        if method not in {"POST", "PUT", "PATCH"}:
            continue
        action = str(item.get("action") or "").lower()
        path = str(item.get("path") or "")
        path_tail = _entity(path.rstrip("/").split("/")[-1]) if path else ""
        summary = " ".join(str(item.get(key) or "") for key in ("summary", "action", "path")).lower()
        score = 0
        verb_match = False
        if action and action in candidates:
            score += 3
            verb_match = True
        if path_tail and _path_tail_matches(path_tail):
            score += 2
            verb_match = True
        if verb in summary or any(c and c in summary for c in candidates):
            score += 1
            verb_match = True
        # Entity constraint only reinforces a match that the verb already won.
        if entity_e and verb_match:
            item_entity = _entity(item.get("entity") or "")
            if item_entity == entity_e or (entity_aliases and item_entity in entity_aliases):
                score += 2
        if score <= 0:
            continue
        # Collection endpoint without an explicit action token but a confident
        # verb-stem / path-tail match: the path tail is the canonical action.
        if not action and verb_match and path_tail:
            action = path_tail
        if score > best[0]:
            best = (score, action, path)
    return (best[1], best[2]) if best[0] > 0 else ("", "")


def _state_action_candidates(state: str) -> set[str]:
    """Derive generic operation stems from a target state token.

    Requirements often use terse arrows (``PAID -> SHIPPED``) without naming
    the triggering verb.  English morphology supplies a source-agnostic bridge
    to documented operation tails (``ship``, ``cancel``, ``approve``) without a
    per-industry state table.  The route catalog remains the authority: these
    candidates only rank documented endpoints and never invent one.
    """
    token = _entity(state).lower()
    if not token:
        return set()
    candidates = {token}
    if token.endswith("ies") and len(token) > 4:
        candidates.add(token[:-3] + "y")
    if token.endswith("ied") and len(token) > 4:
        candidates.add(token[:-3] + "y")
    # Common irregular past-tense form (paid -> pay, laid -> lay).
    if token.endswith("id") and len(token) > 3:
        candidates.add(token[:-2] + "y")
    if token.endswith("ed") and len(token) > 3:
        stem = token[:-2]
        candidates.add(stem)
        if stem.endswith("v") or stem.endswith("t") or stem.endswith("l") or stem.endswith("p"):
            candidates.add(stem + "e")
        # English past-tense forms may double the final consonant before
        # ``-ed`` (cancelled -> cancel, submitted -> submit, stopped -> stop).
        # These candidates only rank already documented routes; they never
        # invent an endpoint.
        if len(stem) >= 2 and stem[-1] == stem[-2] and stem[-1] in "bdgklmnprt":
            candidates.add(stem[:-1])
    if token.endswith("s") and len(token) > 3:
        candidates.add(token[:-1])
    return {item for item in candidates if item}


def _route_state_target_action(
    state: str,
    endpoints: list[dict[str, str]],
    *,
    entity: str = "",
) -> tuple[str, str]:
    """Route a terse transition target to a documented mutation endpoint."""
    candidates = _state_action_candidates(state)
    if not candidates:
        return "", ""
    entity_e = _entity(entity) if entity else ""
    best: tuple[int, str, str] = (0, "", "")
    for item in endpoints:
        method = str(item.get("method") or "").upper()
        if method not in {"POST", "PUT", "PATCH"}:
            continue
        action = _entity(str(item.get("action") or "").lower())
        path = str(item.get("path") or "")
        path_tail = _entity(path.rstrip("/").split("/")[-1]) if path else ""
        summary = " ".join(str(item.get(key) or "").lower() for key in ("summary", "action", "path"))
        score = 0
        selected = action or path_tail
        if action in candidates:
            score += 4
        if path_tail in candidates:
            score += 3
            selected = path_tail
        if any(candidate and candidate in summary for candidate in candidates):
            score += 1
        if score and entity_e:
            item_entity = _entity(item.get("entity") or "")
            if item_entity == entity_e or item_entity.startswith(entity_e) or entity_e.startswith(item_entity):
                score += 2
        if score > best[0]:
            best = (score, selected, path)
    return (best[1], best[2]) if best[0] > 0 else ("", "")


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
    # 主链 4: every generated test task carries an explicit lifecycle status so
    # it can be tracked (pending/running/passed/failed/blocked) and surfaced to
    # the frontend. Defaults to "pending" at planning time; the execution
    # campaign flips it as the task is attempted and confirmed.
    status: str = field(default="pending")

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
            "status": self.status,
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

    def path_to_state(self, from_state: str, to_state: str) -> list[StateTransition] | None:
        """BFS shortest-path through normal transitions from from_state to to_state.

        Returns the ordered list of transitions required to drive an entity
        from ``from_state`` to ``to_state``, or None if unreachable.  Used by
        the state-precondition driver to actively build the target state when
        no existing entity satisfies the runtime filter.
        """
        if from_state == to_state:
            return []
        outgoing: dict[str, list[StateTransition]] = defaultdict(list)
        for item in self.transitions:
            if item.is_normal and not item.is_forbidden:
                outgoing[item.from_state].append(item)
        visited: set[str] = {from_state}
        queue: list[tuple[str, list[StateTransition]]] = [(from_state, [])]
        while queue:
            current, trail = queue.pop(0)
            for t in outgoing.get(current, []):
                if t.to_state in visited:
                    continue
                new_trail = list(trail) + [t]
                if t.to_state == to_state:
                    return new_trail
                visited.add(t.to_state)
                queue.append((t.to_state, new_trail))
        return None

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
    _modal = re.compile(
        r"\b(?:must|shall|cannot|must\s+not|only|become|becomes)\b|必须|不得|不允许|不可|只能|禁止|变为|变成|不展示|仅展示|只展示|不可见|隐藏",
        re.I,
    )
    _forbidden = re.compile(r"forbidden|invalid|禁止|不得|不允许|不可", re.I)
    _state_field = re.compile(r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", re.I)
    # Narrative state transitions — PRDs that describe flows in prose
    # ("支付成功后订单状态变为 PAID") instead of arrow diagrams
    # ("CREATED -> PAID").  Universal across languages and domains.
    _narrative_transition = re.compile(
        r"状态(?:[^，,\n]{0,20})(?:变更?为|变成|进入|流转到|转为|切换[到为]|更新为)\s*`?([A-Z][A-Z0-9_]+)`?"
        r"|\b(?:become|changes?\s+to|transitioned?\s+to|set\s+to|updated?\s+to|moves?\s+to|enters?)\s+`?([A-Z][A-Z0-9_]+)`?",
        re.I,
    )
    # Precondition patterns — "必须处于 PENDING_PAYMENT 状态" / "must be in state X".
    # Requires an explicit state anchor so we don't grab arbitrary uppercase tokens.
    _narrative_precondition = re.compile(
        r"(?:处于|状态(?:必须|应)?为|必须为|当前状态)\s*`?([A-Z][A-Z0-9_]{2,})`?"
        r"|\b(?:must|should)\s+be\s+(?:in\s+)?(?:state\s+|status\s+)?`?([A-Z][A-Z0-9_]{2,})`?",
        re.I,
    )

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
        _augment_source_fields_from_endpoints(source_fields, endpoints, set(known))
        for section in self._sections(prd_text):
            entity, binding_mode = _best_entity_for_section(section, known, source_fields)
            if not entity:
                # Chinese-only sections fail state/field overlap; bridge them via
                # the entity_token_lexicon (language resource, not hardcoding).
                entity = _section_entity_via_tokens(section, endpoints, known)
                if entity:
                    binding_mode = "entity_token_bridge"
            if not entity:
                self._record_unbound_section(section)
                continue
            graph = self.graphs[entity]
            for row in section["transitions"]:
                action, endpoint = _source_action(row["line"], entity, endpoints)
                # ── Narrative verb routing ──
                # Arrow transitions carry an explicit action verb that _source_action
                # matches literally. Narrative transitions ("支付成功后状态变为 PAID")
                # carry a *Chinese* verb with no English action token in the line, so
                # the literal match fails. Bridge it through the verb_action_lexicon
                # (language resource, not domain hardcoding) to a concrete endpoint.
                if not action and not endpoint:
                    action, endpoint = _route_narrative_action(_extract_narrative_verb(row["line"]), endpoints, entity=entity)
                if not action and not endpoint:
                    # Arrow-style requirements frequently omit the action verb
                    # (for example ``PAID -> SHIPPED``).  Use the target state
                    # only to rank an already documented mutation route.
                    action, endpoint = _route_state_target_action(
                        row.get("after") or "",
                        endpoints,
                        entity=entity,
                    )
                # ── Route-aware risk scoring ──
                # A transition detected from narrative PRD text that cannot be
                # mapped to a concrete API endpoint is still structurally valid
                # for the state graph, but its *slice* must rank below every
                # executable slice so it doesn't displace real probes in the per-
                # round budget.  No per-project scoring table — just a structural
                # penalty on unresolvable transitions.
                can_route = bool(action and endpoint)
                risk_boost = 0.9 if section["forbidden"] else (0.5 if can_route else 0.02)
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
                    risk_boost,
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
                    known_states = {str(name).strip().upper() for name in graph.states}
                    targeted_states = _mentioned_states(
                        f"{section.get('title') or ''} {invariant}",
                        known_states=known_states,
                    )
                    state_names = [name for name in graph.states if str(name).strip().upper() in targeted_states] or list(graph.states)
                    for name in state_names:
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
        if _BSG_BUILD_HOOK is not None:
            _BSG_BUILD_HOOK(self, prd_text, api_spec_text, db_schema_text)
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
        adjacency: dict[str, set[str]] = defaultdict(set)
        for entity, graph in self.graphs.items():
            adjacency.setdefault(_entity(entity), set())
            for edge in graph.edges:
                source_entity = _entity(edge.source_entity)
                target_entity = _entity(edge.target_entity)
                if source_entity and target_entity:
                    adjacency[source_entity].add(target_entity)
                    adjacency[target_entity].add(source_entity)
        for entity, graph in sorted(self.graphs.items()):
            for transition in graph.transitions:
                # Unroutable transition (no action/endpoint): keep it OBSERVABLE as a
                # coverage gap but do NOT emit a competing behavior slice. A dead
                # slice with empty endpoints would otherwise be selected first
                # (transition kind_rank=0) in round 1 and displace executable
                # probes from the per-round budget — the exact regression we saw.
                if not transition.action or not transition.api_endpoint:
                    self.coverage_gaps.append({
                        "kind": "UNROUTABLE_TRANSITION",
                        "title": f"{transition.from_state}->{transition.to_state}",
                        "reason": "no_source_bound_action_route",
                        "source_refs": _refs(transition.source_refs or graph.source_refs),
                        "required_asset": "endpoint_matching_narrative_verb_or_explicit_action",
                    })
                    continue
                slice_id = transition.behavior_slice_id or behavior_slice_id(
                    "transition", entity, transition.from_state, transition.to_state,
                    transition.action, transition.api_endpoint,
                    "forbidden" if transition.is_forbidden else "normal",
                )
                transition.behavior_slice_id = slice_id
                slices.append(BehaviorSlice(
                    slice_id=slice_id,
                    entity=entity,
                    kind="transition",
                    states=_unique([transition.from_state, transition.to_state]),
                    endpoints=[transition.api_endpoint],
                    priority=max(float(transition.risk_score or 0.0), 0.9 if transition.is_forbidden else 0.35),
                    source_refs=_refs(transition.source_refs or graph.source_refs),
                    evidence_gaps=[],
                ))
            for state_name, node in graph.states.items():
                for invariant in node.invariants:
                    observation_endpoints = _observation_endpoints(entity, self.endpoint_catalog)
                    if not observation_endpoints:
                        observation_endpoints = _adjacent_observation_endpoints(entity, self.endpoint_catalog, adjacency)
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
                dependency_endpoints = _dependency_endpoints(entity, edge.target_entity, self.endpoint_catalog)
                if not dependency_endpoints:
                    dependency_endpoints = _unique(
                        _adjacent_observation_endpoints(entity, self.endpoint_catalog, adjacency)
                        + _adjacent_observation_endpoints(edge.target_entity, self.endpoint_catalog, adjacency)
                    )
                gaps = [] if dependency_endpoints else ["CROSS_ENTITY_OBSERVATION_CONTRACT_MISSING"]
                slices.append(BehaviorSlice(
                    slice_id=behavior_slice_id("dependency", entity, edge.source_state, edge.target_entity, edge.target_state, edge.relation),
                    entity=entity,
                    kind="dependency",
                    states=_unique([edge.source_state, edge.target_state]),
                    endpoints=dependency_endpoints,
                    priority=max(float(edge.risk_score or 0.0), 0.4),
                    source_refs=_refs(edge.source_refs or graph.source_refs),
                    evidence_gaps=gaps,
                ))
            observation_endpoints = _observation_endpoints(entity, self.endpoint_catalog)
            if not observation_endpoints:
                observation_endpoints = _adjacent_observation_endpoints(entity, self.endpoint_catalog, adjacency)
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
            if not observation_endpoints:
                observation_endpoints = _adjacent_observation_endpoints(str(item["entity"]), self.endpoint_catalog, adjacency)
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
        contract: dict[str, Any] = {
            "slices": [item.to_dict() for item in self.behavior_slices],
            "coverage_gaps": list(self.coverage_gaps),
            "summary": {
                "total_slices": len(self.behavior_slices),
                "by_kind": dict(sorted(by_kind.items())),
                "coverage_gap_count": len(self.coverage_gaps),
                "source_field_bound_invariant_count": len(self.bound_invariants),
            },
        }
        if _BSG_CONTRACT_HOOK is not None:
            contract = _BSG_CONTRACT_HOOK(self, contract)
        return contract

    def _sections(self, text: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        # Document-level rolling last-observed state. Used only as a final
        # fallback for narrative transitions whose source is implied by the
        # nearest preceding state discussed anywhere in the PRD (e.g.
        # "退款成功后状态变为 REFUNDED" whose source PAID was stated two sections
        # earlier). Pure structural heuristic — no per-domain hardcoding.
        self._doc_last_observed_state: str = ""
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
            # ── Narrative transition extraction ──
            # PRDs that describe state flows in prose (e.g. "支付成功后
            # 状态变为 PAID") need the same transition-graph treatment that
            # arrow-diagram PRDs get automatically.  For narrative lines we
            # infer the source state from the section's last observed
            # precondition, and the target from the transition verb.
            # Config-driven — no per-project state-name hardcoding.
            line_states = _ordered_line_states(line)
            narr_trans = self._narrative_transition.search(line)
            if narr_trans and len(line_states) >= 1:
                target = _state(narr_trans.group(1) or narr_trans.group(2) or "")
                if not target and line_states:
                    target = line_states[-1]
                # The source state is the last precondition observed in this
                # section, or — if this line carries its own precondition — the
                # state that immediately precedes the transition verb in the
                # narrative.  Heuristic: pick the last state-before-verb that
                # also appears in the section's known states.  Fall back to the
                # most recently *observed* state in the section so prose like
                # "退款成功后订单状态变为 REFUNDED" (whose source is only implied
                # by an earlier "已支付/已完成") is not silently dropped.
                source = ""
                if len(line_states) >= 2:
                    # Two states on the same line with a transition verb between
                    # them is the strongest signal — pair them in order.
                    source = line_states[0]
                else:
                    source = (
                        current.get("_last_precondition_state")
                        or current.get("_last_observed_state", "")
                        or getattr(self, "_doc_last_observed_state", "")
                    )
                if source and target and source != target:
                    tr = {"before": source, "after": target, "line": line, "ref": ref}
                    current["transitions"].append(tr)
                    current["states"].update((source, target))
            # Track the last observed state (rolling, deterministic) AFTER source
            # resolution so the target state on this line does not poison the next
            # transition's source inference. Rolled at both the section and the
            # document level so later narrative transitions can fall back to it.
            if line_states:
                current["_last_observed_state"] = line_states[-1]
                self._doc_last_observed_state = line_states[-1]
            # Track the last explicit precondition state for the section.
            precond = self._narrative_precondition.search(line)
            if precond:
                ps = _state(precond.group(1) or precond.group(2) or "")
                if ps:
                    current.setdefault("_last_precondition_state", "")
                    current["_last_precondition_state"] = ps
                    current["states"].add(ps)
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
    parts = [_singularize_entity_segment(part) for part in text.split("_") if part]
    return "_".join(parts)[:80]


def _singularize_entity_segment(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.endswith("ies") and len(text) > 4:
        return text[:-3] + "y"
    if text.endswith("ses") and len(text) > 4 and not text.endswith(("ases", "eses", "ises", "oses", "uses")):
        return text[:-1]
    if text.endswith(("sses", "shes", "ches", "xes", "zes")) and len(text) > 4:
        return text[:-2]
    if text.endswith("s") and len(text) > 3 and not text.endswith(("ss", "us", "is")):
        return text[:-1]
    return text


def _entity_aliases(value: Any) -> set[str]:
    entity = _entity(value)
    if not entity:
        return set()
    aliases = {entity}
    for group in _lexicon_groups("entity_alias_groups"):
        if entity in group:
            aliases.update(group)
    parts = [part for part in entity.split("_") if part]
    aliases.update(parts)
    if len(parts) > 1:
        aliases.add("".join(parts))
    if entity.endswith("_usage"):
        aliases.add(entity[: -len("_usage")])
    if entity.endswith("_item"):
        aliases.add(entity[: -len("_item")])
    aliases.update(_identifier_tokens(entity))
    return {_entity(alias) for alias in aliases if alias}


def _endpoint_supports_observation(item: dict[str, str]) -> bool:
    method = str(item.get("method") or "").upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if method != "POST":
        return False
    action = _entity(item.get("action") or "")
    summary_tokens = {_entity(token) for token in _text_tokens(" ".join(str(item.get(key) or "") for key in ("summary", "path", "action")))}
    observation_markers = {_entity(token) for token in _lexicon_list("observation_action_markers")}
    return bool(action in observation_markers or summary_tokens & observation_markers)


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
        if _endpoint_relates_to_entity(item, entity)
        and _endpoint_supports_observation(item)
        and str(item.get("path") or "").startswith("/")
    ))


def _dependency_endpoints(source_entity: str, target_entity: str, endpoints: list[dict[str, str]]) -> list[str]:
    return _unique(_observation_endpoints(target_entity, endpoints) + _observation_endpoints(source_entity, endpoints))


def _adjacent_observation_endpoints(entity: str, endpoints: list[dict[str, str]], adjacency: dict[str, set[str]]) -> list[str]:
    related: list[str] = []
    for neighbor in sorted(adjacency.get(_entity(entity), set())):
        related.extend(_observation_endpoints(neighbor, endpoints))
    return _unique(related)


def _api_facts(text: str, state_re: re.Pattern[str]) -> tuple[dict[str, list[dict[str, str]]], dict[str, dict[str, list[dict[str, str]]]], list[dict[str, str]]]:
    entities: dict[str, list[dict[str, str]]] = defaultdict(list)
    states: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    endpoints: list[dict[str, str]] = []
    spec = _parse_structured_api_spec(text)
    if isinstance(spec.get("paths"), dict):
        server_base_path = ""
        servers = spec.get("servers") if isinstance(spec.get("servers"), list) else []
        if servers and isinstance(servers[0], dict):
            server_url = str(servers[0].get("url") or "").strip()
            parsed_server_path = urllib.parse.urlparse(server_url).path.rstrip("/")
            if parsed_server_path.startswith("/") and "{" not in parsed_server_path:
                server_base_path = parsed_server_path
        for source_path, operations in spec["paths"].items():
            source_path = str(source_path)
            path = f"{server_base_path}/{source_path.lstrip('/')}" if server_base_path else source_path
            if isinstance(operations, dict):
                for method, operation in operations.items():
                    if str(method).lower() in {"get", "post", "put", "patch", "delete", "head", "options"}:
                        operation = _dict(operation)
                        entity, action = _path(str(path), str(operation.get("operationId") or ""))
                        if entity:
                            ref = _ref("openapi", f"paths.{source_path}.{method}", str(operation.get("summary") or operation.get("operationId") or source_path))
                            entities[entity].append(ref)
                            endpoints.append({
                                "entity": entity,
                                "action": action,
                                "path": str(path),
                                "method": str(method).upper(),
                                "operation_id": str(operation.get("operationId") or ""),
                                "summary": str(operation.get("summary") or ""),
                                "parameters": [
                                    {
                                        "name": str(item.get("name") or ""),
                                        "in": str(item.get("in") or ""),
                                        "required": bool(item.get("required")),
                                    }
                                    for item in (operation.get("parameters") or [])
                                    if isinstance(item, dict) and str(item.get("name") or "").strip()
                                ],
                            })
        for name, schema in _dict(_dict(spec.get("components")).get("schemas")).items():
            if isinstance(schema, dict):
                for field, definition in _dict(schema.get("properties")).items():
                    if state_re.search(str(field)) and isinstance(definition, dict):
                        for value in definition.get("enum") or []:
                            token = _state(value)
                            if token:
                                states[_entity(name)][token].append(_ref("openapi", f"components.schemas.{name}.properties.{field}", token))
        return entities, states, endpoints
    lines = str(text or "").splitlines()
    current: dict[str, str] | None = None
    current_description: list[str] = []
    current_line = 0

    def flush_current() -> None:
        nonlocal current, current_description, current_line
        if not current:
            return
        entity = str(current.get("entity") or "")
        action = str(current.get("action") or "")
        path = str(current.get("path") or "")
        method = str(current.get("method") or "")
        summary = " ".join(part.strip() for part in current_description if part.strip())
        if entity:
            ref_quote = summary or f"{method} {path}"
            ref = _ref("api_document", f"line:{current_line}", ref_quote)
            entities[entity].append(ref)
            endpoints.append({"entity": entity, "action": action, "path": path, "method": method, "summary": summary})
        current = None
        current_description = []
        current_line = 0

    for line_number, raw in enumerate(lines, 1):
        line = str(raw or "").strip()
        header = re.match(r"^###\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[A-Za-z0-9_:\-{}./]+)", line, re.I)
        if header:
            flush_current()
            entity, action = _path(header.group(2), "")
            current = {"entity": entity, "action": action, "path": header.group(2), "method": header.group(1).upper()}
            current_line = line_number
            continue
        if current is None:
            continue
        if line.startswith("### ") or line.startswith("## "):
            flush_current()
            continue
        if line.startswith("```"):
            continue
        if line:
            current_description.append(line)
    flush_current()
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


def _augment_source_fields_from_endpoints(source_fields: dict[str, set[str]], endpoints: list[dict[str, str]], known_entities: set[str]) -> None:
    for item in endpoints:
        text = " ".join(
            str(part or "")
            for part in (item.get("path"), item.get("action"), item.get("summary"))
            if str(part or "").strip()
        )
        tokens = _text_tokens(text)
        entity = _entity(item.get("entity") or "")
        if entity and tokens:
            source_fields.setdefault(entity, set()).update(tokens)
        for known in sorted(known_entities):
            if known and known in tokens:
                source_fields.setdefault(known, set()).update(tokens)


def _identifier_tokens(value: str) -> set[str]:
    raw = {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,63}", str(value or ""))}
    canonical = {_entity(token) for token in raw if token}
    parts = {
        _singularize_entity_segment(part)
        for token in raw
        for part in token.split("_")
        if len(part) >= 4
    }
    return {token for token in (raw | canonical | parts) if token}


def _text_tokens(value: str) -> set[str]:
    text = str(value or "")
    tokens = set(_identifier_tokens(text))
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,24}", text):
        tokens.add(phrase)
        compact = phrase.strip()
        if len(compact) <= 8:
            for size in range(2, min(4, len(compact)) + 1):
                for index in range(0, len(compact) - size + 1):
                    tokens.add(compact[index:index + size])
    return {token for token in tokens if str(token).strip()}


def _section_tokens(section: dict[str, Any]) -> set[str]:
    ignored = {_entity(token) for token in _lexicon_list("binding_stop_words")}
    tokens: set[str] = set()
    tokens.update(_text_tokens(str(section.get("title") or "")))
    for invariant, _ in section.get("invariants", []):
        tokens.update(_text_tokens(str(invariant)))
    for row in section.get("transitions", []):
        tokens.update(_text_tokens(str(row.get("line") or "")))
    return {
        token for token in tokens
        if token not in ignored and (re.search(r"[\u4e00-\u9fff]", token) or "_" in token or len(token) >= 4)
    }


def _best_entity_for_section(section: dict[str, Any], known: dict[str, set[str]], source_fields: dict[str, set[str]]) -> tuple[str, str]:
    state_entity = _best_entity(section.get("states", set()), known)
    if state_entity:
        return state_entity, "state_overlap"
    title = str(section.get("title") or "")
    title_entity = _entity(title)
    title_tokens = _text_tokens(title)
    for entity in sorted(known):
        if entity and (title_entity == entity or title_entity.startswith(f"{entity}_") or title_entity.endswith(f"_{entity}")):
            return entity, "section_title"
    tokens = _section_tokens(section)
    if not tokens:
        return "", ""
    candidates: list[tuple[int, int, int, int, str]] = []
    for entity, fields in source_fields.items():
        overlap = tokens & fields
        title_overlap = title_tokens & fields
        title_direct = entity in title_tokens or any(token == entity for token in title_tokens)
        weighted = sum(2 if "_" in token else 1 for token in overlap)
        weighted += sum(3 if "_" in token else 2 for token in title_overlap)
        if title_direct:
            weighted += 3
        if weighted:
            candidates.append((weighted, len(overlap), len(known.get(entity, set())), len(fields), entity))
    candidates.sort(key=lambda row: (-row[0], -row[1], -row[2], -row[3], row[4]))
    if not candidates:
        return "", ""
    best_weight, best_count, best_state_count, best_field_count, entity = candidates[0]
    second_weight, second_count, second_state_count, second_field_count = candidates[1][:4] if len(candidates) > 1 else (0, 0, -1, -1)
    best_overlap = tokens & source_fields.get(entity, set())
    title_overlap = title_tokens & source_fields.get(entity, set())
    has_cjk_overlap = any(re.search(r"[\u4e00-\u9fff]", token) for token in (best_overlap | title_overlap))
    title_direct = entity in title_tokens or any(token == entity for token in title_tokens)
    if (
        ((best_weight >= 2) or has_cjk_overlap or title_direct)
        and (best_weight, best_count, best_state_count, best_field_count) > (second_weight, second_count, second_state_count, second_field_count)
        and (best_count >= 1 or title_direct or bool(title_overlap))
    ):
        return entity, "source_field_overlap"
    return "", ""


def _best_entity(values: set[str], known: dict[str, set[str]]) -> str:
    candidates = [(len(values & states) / len(values | states), entity) for entity, states in known.items() if values and states and values & states]
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates[0][1] if candidates and candidates[0][0] >= 0.15 else ""


def _endpoint_relates_to_entity(item: dict[str, str], entity: str) -> bool:
    endpoint_entity = _entity(item.get("entity") or "")
    entity_aliases = _entity_aliases(entity)
    endpoint_aliases = _entity_aliases(endpoint_entity)
    if endpoint_entity == entity or entity_aliases & endpoint_aliases:
        return True
    tokens = _text_tokens(" ".join(
        str(part or "")
        for part in (item.get("path"), item.get("action"), item.get("summary"))
        if str(part or "").strip()
    ))
    normalized_tokens = {_entity(token) for token in tokens}
    return bool(entity_aliases & normalized_tokens)


def _source_action(line: str, entity: str, endpoints: list[dict[str, str]]) -> tuple[str, str]:
    for item in endpoints:
        if item["entity"] == entity and item["action"] and re.search(rf"\b{re.escape(item['action'])}\b", line, re.I):
            return item["action"], item["path"]
    return "", ""
