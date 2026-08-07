"""Chinese Semantic Frame technical grounding — evidence-driven bindings.

SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1 (P0-D: 技术 Grounding, §12).

Contract:
- Every binding is evidence-driven with the SPEC §12.2 priority chains and
  produces a typed receipt (GROUNDED / AMBIGUOUS / UNKNOWN + reason code).
  Word-list guessing (CJK field tokens, semantic classification tables,
  containment scoring) never grounds anything — near matches are
  candidate-only and stay UNKNOWN.
- The only language-function mappings allowed are: ownership phrases
  (本人/自己/名下/… SPEC §9.2) → structured ``OWN`` relation, and CRUD verbs
  (创建/查询/修改/删除 → POST/GET/PUT|PATCH/DELETE) — industry-neutral
  semantics, never a business vocabulary.
- Multiple same-level candidates → AMBIGUOUS with MULTIPLE_*_CANDIDATES
  (SPEC §12.3: never take the first item). No evidence → UNKNOWN with
  GROUNDING_EVIDENCE_INSUFFICIENT.
- Grounded refs are emitted in forms the Behavior IR channel resolves:
  actor → role names (registered by role/name), operation → interface ids
  (``api:<METHOD>:<path>``, registered as ``METHOD:path``), entity →
  understanding-model object ids or declared labels (registered by name).
- Scope ownership structuring changes typed slots, so the semantic signature
  is recomputed afterwards; frames stay fail-closed valid.
- This stage ACTIVATES the P0-A Behavior IR channel: grounded frames now
  contribute owns/permits/denies relations with full grounding receipts.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .business_concept_registry import build_business_concept_registry, concept_lookup
from .chinese_semantic_ledger_adapter import frames_from_asset
from .chinese_semantic_receipts import build_receipt
from .chinese_semantic_schema import (
    semantic_signature,
    validate_semantic_frame,
)

CHINESE_SEMANTIC_GROUNDING_SCHEMA = "qualibug.chinese-semantic-grounding.v1"

# Ownership phrases → structured OWN relation (SPEC §9.2 language functions).
_OWNERSHIP_SELF_MARKERS = (
    "本人", "自己", "名下", "当前登录", "当前账号", "当前用户",
    "我的", "制单人", "申请人本人", "经办人", "归属于当前",
)
_OWNERSHIP_ORG_MARKERS = (
    "所属", "所在部门", "所在门店", "负责区域", "关联项目", "负责的",
)

# CRUD verbs → HTTP methods (industry-neutral language semantics).
_CRUD_METHODS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("创建", "新增", "生成", "提交", "申请", "发起", "注册", "上传", "导入"), ("POST",)),
    (("查询", "查看", "获取", "检索", "浏览", "下载", "导出", "读取"), ("GET",)),
    (("修改", "更新", "编辑", "调整", "变更"), ("PUT", "PATCH")),
    (("删除", "取消", "作废", "移除", "撤销", "注销"), ("DELETE",)),
)

# State prefixes inside conditions (language functions, SPEC §9.3).
_STATE_PREFIX = re.compile(r"(?:已|未|待|处于)(?P<state>[^，,；;。的时后前]{1,24})")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return " ".join(_text(value).split()).strip()


def _interfaces(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _list(asset.get("interfaces")) if isinstance(row, dict)]


def _resolvable_operation_ref(ref: str, interfaces: list[dict[str, Any]]) -> str:
    """Convert an interface id (api:GET:/path) into the IR-resolvable
    METHOD:path form; METHOD:path and node-id forms pass through unchanged."""
    item = _norm(ref)
    if not item:
        return ""
    if item.startswith(("api:", "postman:", "har:")):
        for interface in interfaces:
            if _norm(interface.get("interface_id")) == item:
                method = _text(interface.get("method")).upper()
                path = _norm(interface.get("path"))
                if method and path:
                    return f"{method}:{path}"
        return ""
    return item


def _resolvable_entity_refs(
    mention: str,
    concept: dict[str, Any],
    asset_entity_names: set[str],
) -> list[str]:
    """Entity ref forms the IR channel can resolve.

    The IR builder only keeps entities whose canonical name is ASCII
    (`_canonical_entity_name` extracts [a-z0-9]+), so Chinese mentions alone
    never resolve. Declared concept aliases that match an asset entity name
    are the resolvable forms; the mention itself is kept as knowledge-level
    grounding (the IR channel skips it safely when unresolvable).
    """
    candidates: list[str] = [mention]
    for alias in _list(concept.get("aliases")):
        item = _norm(alias)
        if item and item not in candidates and item.lower() in asset_entity_names:
            candidates.append(item)
    return candidates


def _permission_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(asset.get("permission_matrix") or asset.get("permissions"))
        if isinstance(row, dict)
    ]


def _formal_ui_contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("ui_formal_contracts", "formal_ui_contracts"):
        for row in _list(asset.get(key)):
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _relationship_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _list(asset.get("relationships")) if isinstance(row, dict)]


def _rule_library(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(asset.get("rule_library") or asset.get("rules"))
        if isinstance(row, dict)
    ]


def _find_rule(asset: dict[str, Any], origin_fact_id: str) -> dict[str, Any]:
    """Locate the rule promoted from the frame's origin fact (exact identity)."""
    fact_tail = _text(origin_fact_id)
    if fact_tail.startswith("fact:"):
        fact_tail = fact_tail[len("fact:") :]
    for rule in _rule_library(asset):
        rule_id = _text(rule.get("rule_id") or rule.get("id"))
        if rule_id == f"zh_business:{fact_tail[-20:]}":
            return rule
        if _text(rule.get("fact_id")) == _text(origin_fact_id):
            return rule
        if _text(rule.get("source_rule_id")) == _text(origin_fact_id):
            return rule
    return {}


def _unique_candidate(candidates: list[str], ambiguous_code: str) -> tuple[str, str]:
    """(ref, status) — unique → GROUNDED, several → AMBIGUOUS, none → UNKNOWN."""
    unique = list(dict.fromkeys(_norm(item) for item in candidates if _norm(item)))
    if len(unique) == 1:
        return unique[0], "GROUNDED"
    if len(unique) > 1:
        return "", "AMBIGUOUS"
    return "", "UNKNOWN"


# ── Actor grounding ──


def _ground_actor(frame: dict[str, Any], asset: dict[str, Any], registry: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """SPEC §12.2 actor chain: permission matrix > roles > UI contract > concept registry."""
    mentions = [
        _norm(item)
        for item in _list(_dict(frame.get("actor")).get("mentions"))
        if _norm(item)
    ]
    if not mentions:
        return [], {"status": "NOT_MENTIONED", "reason_code": "NOT_APPLICABLE"}
    for mention in mentions:
        chains: list[tuple[str, list[str]]] = []
        permission_roles = [
            _norm(row.get("role") or row.get("actor") or row.get("principal"))
            for row in _permission_rows(asset)
            if _norm(row.get("role") or row.get("actor") or row.get("principal")).casefold()
            == mention.casefold()
        ]
        chains.append(("permission_matrix_role", permission_roles))
        declared_roles = [
            _norm(row.get("role") or row.get("name") or row.get("id"))
            for row in _list(asset.get("roles"))
            if isinstance(row, dict)
            and _norm(row.get("role") or row.get("name") or row.get("id")).casefold()
            == mention.casefold()
        ]
        chains.append(("source_role_declaration", declared_roles))
        ui_roles = [
            _norm(row.get("actor_role") or row.get("actor"))
            for row in _formal_ui_contracts(asset)
            if _norm(row.get("actor_role") or row.get("actor")).casefold()
            == mention.casefold()
        ]
        chains.append(("formal_ui_contract_actor", ui_roles))
        lookup = concept_lookup(registry, "actor", mention)
        if lookup.get("status") == "AMBIGUOUS":
            return [], {
                "status": "AMBIGUOUS",
                "mention": mention,
                "candidate_refs": list(_dict(lookup).get("candidates")),
                "reason_code": "MULTIPLE_ACTOR_CANDIDATES",
                "evidence": ["concept_registry_ambiguous"],
            }
        registry_refs = (
            [mention] if lookup.get("status") == "RESOLVED" else []
        )
        chains.append(("concept_registry_resolved", registry_refs))

        for chain_name, candidates in chains:
            if not candidates:
                continue
            ref, status = _unique_candidate(candidates, "MULTIPLE_ACTOR_CANDIDATES")
            if status == "GROUNDED":
                return [ref], {
                    "status": "GROUNDED",
                    "mention": mention,
                    "candidate_ref": ref,
                    "reason_code": chain_name.upper() + "_EXACT",
                    "evidence": [chain_name],
                }
            if status == "AMBIGUOUS":
                return [], {
                    "status": "AMBIGUOUS",
                    "mention": mention,
                    "candidate_refs": sorted(set(candidates)),
                    "reason_code": "MULTIPLE_ACTOR_CANDIDATES",
                    "evidence": [chain_name],
                }
        return [], {
            "status": "UNKNOWN",
            "mention": mention,
            "candidate_ref": "",
            "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
            "evidence": [],
        }
    return [], {"status": "NOT_MENTIONED", "reason_code": "NOT_APPLICABLE"}


# ── Object grounding ──


def _asset_entity_names(asset: dict[str, Any]) -> set[str]:
    """Entity names the IR builder keeps (same keys it reads)."""
    names: set[str] = set()
    for key in ("objects", "entities", "tables", "business_objects", "data_tables"):
        for row in _list(asset.get(key)):
            name = ""
            if isinstance(row, str):
                name = row
            elif isinstance(row, dict):
                name = _norm(
                    row.get("name") or row.get("object") or row.get("table") or row.get("entity")
                )
            if name:
                names.add(name.lower())
    return names


def _ground_object(frame: dict[str, Any], asset: dict[str, Any], registry: dict[str, Any]) -> tuple[list[str], dict[str, Any], list[str]]:
    """Entity refs from declared object concepts (alias-aware, IR-resolvable).

    Returns (refs, receipt, entity_labels) — entity_labels are the concept's
    declared labels used by the structural operation chain to match interface
    entity_refs.
    """
    mentions = [
        _norm(item)
        for item in _list(_dict(frame.get("object")).get("mentions"))
        if _norm(item)
    ]
    if not mentions:
        return [], {"status": "NOT_MENTIONED", "reason_code": "NOT_APPLICABLE"}, []
    results: list[str] = []
    labels: list[str] = []
    receipts: list[dict[str, Any]] = []
    asset_entity_names = _asset_entity_names(asset)
    for mention in mentions:
        lookup = concept_lookup(registry, "object", mention)
        if lookup.get("status") == "RESOLVED":
            # Declared-label (name) forms: mention + declared aliases that
            # match an asset entity name (the IR keeps ASCII names only).
            refs = _resolvable_entity_refs(mention, lookup, asset_entity_names)
            ref = refs[0]
            results.append(ref)
            labels.append(mention)
            labels.extend(
                alias
                for alias in _list(lookup.get("aliases"))
                if _norm(alias) and _norm(alias) not in labels
            )
            receipts.append(
                {
                    "status": "GROUNDED",
                    "mention": mention,
                    "candidate_ref": ref,
                    "canonical": _text(lookup.get("canonical")),
                    "reason_code": "CONCEPT_REGISTRY_RESOLVED",
                    "evidence": list(_dict(lookup).get("evidence")),
                }
            )
        elif lookup.get("status") == "AMBIGUOUS":
            receipts.append(
                {
                    "status": "AMBIGUOUS",
                    "mention": mention,
                    "candidate_refs": list(_dict(lookup).get("candidates")),
                    "reason_code": "MULTIPLE_ENTITY_CANDIDATES",
                    "evidence": [],
                }
            )
        else:
            receipts.append(
                {
                    "status": "UNKNOWN",
                    "mention": mention,
                    "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
                    "evidence": [],
                }
            )
    return list(dict.fromkeys(results)), {"status": "GROUNDED" if results else "UNKNOWN",
                                          "receipts": receipts}, list(dict.fromkeys(labels))


# ── Operation grounding ──


def _crud_methods_for(mention: str) -> tuple[str, ...]:
    for verbs, methods in _CRUD_METHODS:
        if any(verb in mention for verb in verbs):
            return methods
    return ()


def _ground_operation(
    frame: dict[str, Any],
    asset: dict[str, Any],
    *,
    entity_refs: list[str],
    entity_labels: list[str],
) -> tuple[list[str], dict[str, Any]]:
    """SPEC §12.2 operation chain: rule ref > summary > description >
    rule_to_interface > UI contract > structural entity+method candidate."""
    mentions = [
        _norm(item)
        for item in _list(_dict(frame.get("action")).get("mentions"))
        if _norm(item)
    ]
    if not mentions:
        return [], {"status": "NOT_MENTIONED", "reason_code": "NOT_APPLICABLE"}
    interfaces = _interfaces(asset)

    def _refs(*values: Any) -> list[str]:
        """Interface ids → IR-resolvable METHOD:path forms."""
        return list(
            dict.fromkeys(
                ref
                for value in values
                for ref in [_resolvable_operation_ref(_norm(value), interfaces)]
                if ref
            )
        )

    for mention in mentions:
        # 1. Rule-explicit operation refs.
        rule = _find_rule(asset, _text(_dict(frame.get("origin")).get("origin_fact_id")))
        rule_refs = _refs(
            *_list(rule.get("operation_refs")),
            *_list(rule.get("authoritative_operation_refs")),
        )
        if rule_refs:
            ref, status = _unique_candidate(rule_refs, "MULTIPLE_OPERATION_CANDIDATES")
            if status == "GROUNDED":
                return [ref], {
                    "status": "GROUNDED",
                    "mention": mention,
                    "candidate_ref": ref,
                    "reason_code": "RULE_EXPLICIT_OPERATION_REF",
                    "evidence": ["rule_library_operation_refs"],
                }
            if status == "AMBIGUOUS":
                return [], {
                    "status": "AMBIGUOUS",
                    "mention": mention,
                    "candidate_refs": sorted(set(rule_refs)),
                    "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
                    "evidence": ["rule_library_operation_refs"],
                }

        # 2+3. Verbatim summary / description containment.
        for field, reason_code in (
            ("summary", "SOURCE_SUMMARY_EXACT_MATCH"),
            ("description", "SOURCE_DESCRIPTION_EXACT_MATCH"),
        ):
            matches = _refs(
                *[
                    _norm(row.get("interface_id"))
                    for row in interfaces
                    if _norm(row.get(field)) and mention in _norm(row.get(field))
                ]
            )
            if matches:
                ref, status = _unique_candidate(matches, "MULTIPLE_OPERATION_CANDIDATES")
                if status == "GROUNDED":
                    return [ref], {
                        "status": "GROUNDED",
                        "mention": mention,
                        "candidate_ref": ref,
                        "reason_code": reason_code,
                        "evidence": [f"interface_{field}"],
                    }
                if status == "AMBIGUOUS":
                    return [], {
                        "status": "AMBIGUOUS",
                        "mention": mention,
                        "candidate_refs": sorted(set(matches)),
                        "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
                        "evidence": [f"interface_{field}"],
                    }

        # 4. Authoritative rule_to_interface edges.
        if rule:
            rule_id = _text(rule.get("rule_id") or rule.get("id"))
            edges = _refs(
                *[
                    row.get("to")
                    for row in _relationship_rows(asset)
                    if isinstance(row, dict)
                    and _text(row.get("relation")) == "rule_to_interface"
                    and _text(row.get("from")) == rule_id
                    and _text(row.get("status"))
                    in ("accepted", "active", "confirmed", "verified", "resolved")
                ]
            )
            if edges:
                ref, status = _unique_candidate(edges, "MULTIPLE_OPERATION_CANDIDATES")
                if status == "GROUNDED":
                    return [ref], {
                        "status": "GROUNDED",
                        "mention": mention,
                        "candidate_ref": ref,
                        "reason_code": "RULE_TO_INTERFACE_AUTHORITATIVE",
                        "evidence": ["relationships_rule_to_interface"],
                    }
                if status == "AMBIGUOUS":
                    return [], {
                        "status": "AMBIGUOUS",
                        "mention": mention,
                        "candidate_refs": sorted(set(edges)),
                        "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
                        "evidence": ["relationships_rule_to_interface"],
                    }

        # 5. Formal UI contract identity.
        ui_refs = _refs(
            *[
                row.get("operation_ref")
                for row in _formal_ui_contracts(asset)
                if _norm(row.get("title")) and mention in _norm(row.get("title"))
                and _norm(row.get("operation_ref"))
            ]
        )
        if ui_refs:
            ref, status = _unique_candidate(ui_refs, "MULTIPLE_OPERATION_CANDIDATES")
            if status == "GROUNDED":
                return [ref], {
                    "status": "GROUNDED",
                    "mention": mention,
                    "candidate_ref": ref,
                    "reason_code": "FORMAL_UI_CONTRACT_IDENTITY",
                    "evidence": ["formal_ui_contract_operation_ref"],
                }
            if status == "AMBIGUOUS":
                return [], {
                    "status": "AMBIGUOUS",
                    "mention": mention,
                    "candidate_refs": sorted(set(ui_refs)),
                    "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
                    "evidence": ["formal_ui_contract_operation_ref"],
                }

        # 6. Structural candidate: grounded entity + CRUD method mapping.
        methods = _crud_methods_for(mention)
        if entity_refs and methods:
            structural = _refs(
                *[
                    row.get("interface_id")
                    for row in interfaces
                    if _norm(row.get("interface_id"))
                    and _text(row.get("method")).upper() in methods
                    and any(
                        _text(label).casefold()
                        in _text(row.get("entity_refs") or []).casefold()
                        for label in entity_labels or entity_refs
                    )
                ]
            )
            if structural:
                ref, status = _unique_candidate(structural, "MULTIPLE_OPERATION_CANDIDATES")
                if status == "GROUNDED":
                    return [ref], {
                        "status": "GROUNDED",
                        "mention": mention,
                        "candidate_ref": ref,
                        "reason_code": "STRUCTURAL_ENTITY_METHOD_MATCH",
                        "evidence": ["interface_entity_refs", "crud_method_mapping"],
                    }
                if status == "AMBIGUOUS":
                    return [], {
                        "status": "AMBIGUOUS",
                        "mention": mention,
                        "candidate_refs": sorted(set(structural)),
                        "reason_code": "MULTIPLE_OPERATION_CANDIDATES",
                        "evidence": ["interface_entity_refs", "crud_method_mapping"],
                    }

        return [], {
            "status": "UNKNOWN",
            "mention": mention,
            "candidate_ref": "",
            "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
            "evidence": [],
        }
    return [], {"status": "NOT_MENTIONED", "reason_code": "NOT_APPLICABLE"}


def _permission_scope_for(
    asset: dict[str, Any],
    actor_mention: str,
    operation_ref: str,
) -> str:
    """The permission row scope that legacy relations carry for this
    (role, operation) — matched rows must agree, else "" (no scope)."""
    path = operation_ref.split(":", 1)[-1] if ":" in operation_ref else operation_ref
    scopes: list[str] = []
    for row in _permission_rows(asset):
        role = _norm(row.get("role") or row.get("actor") or row.get("principal"))
        resource = _norm(row.get("resource"))
        if role.casefold() != actor_mention.casefold() or not resource:
            continue
        if not (resource == path or resource in path or path in resource):
            continue
        scope = _norm(row.get("scope"))
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes[0] if len(scopes) == 1 else ""


# ── State grounding ──


def _ground_state(frame: dict[str, Any], asset: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """State concepts → enum values via field descriptions / state machines.

    The FIELD binding is grounded when a technical declaration description
    carries the state concept verbatim; the VALUE is GROUNDED only when the
    field's enum has exactly one member, else AMBIGUOUS
    (MULTIPLE_STATE_VALUE_CANDIDATES). State refs stay metadata — the
    transitions/invariant channel stays deferred (P0-E).
    """
    refs: list[str] = []
    receipts: list[dict[str, Any]] = []
    state_machines = [
        row
        for row in _list(asset.get("state_machines") or asset.get("states"))
        if isinstance(row, dict)
    ]
    for condition in _list(frame.get("conditions")):
        raw = _norm(_dict(condition).get("raw"))
        if not raw:
            continue
        states = [
            _norm(match.group("state"))
            for match in _STATE_PREFIX.finditer(raw)
            if _norm(match.group("state"))
        ]
        for state in states:
            enum_matches: list[str] = []
            for interface in _interfaces(asset):
                for declaration in _list(interface.get("technical_declarations")):
                    if not isinstance(declaration, dict):
                        continue
                    description = _norm(declaration.get("description"))
                    enum = [
                        _text(value)
                        for value in _list(_dict(declaration.get("constraints")).get("enum"))
                        if _text(value)
                    ]
                    if description and state in description and enum:
                        enum_matches.extend(enum)
            if enum_matches:
                unique = list(dict.fromkeys(enum_matches))
                if len(unique) == 1:
                    refs.append(unique[0])
                    receipts.append(
                        {
                            "status": "GROUNDED",
                            "concept": state,
                            "candidate_ref": unique[0],
                            "reason_code": "FIELD_DESCRIPTION_STATE_MATCH",
                            "evidence": ["technical_declaration_description"],
                        }
                    )
                else:
                    receipts.append(
                        {
                            "status": "AMBIGUOUS",
                            "concept": state,
                            "candidate_refs": unique,
                            "reason_code": "MULTIPLE_STATE_VALUE_CANDIDATES",
                            "evidence": ["technical_declaration_description"],
                        }
                    )
                continue
            machine_hits = [
                _norm(state_row)
                for machine in state_machines
                for state_row in _list(machine.get("states"))
                if isinstance(state_row, str) and _norm(state_row) == state
            ]
            if machine_hits:
                refs.append(state)
                receipts.append(
                    {
                        "status": "GROUNDED",
                        "concept": state,
                        "candidate_ref": state,
                        "reason_code": "STATE_MACHINE_STATE_EXACT",
                        "evidence": ["state_machine_states"],
                    }
                )
                continue
            receipts.append(
                {
                    "status": "UNKNOWN",
                    "concept": state,
                    "candidate_ref": "",
                    "reason_code": "GROUNDING_EVIDENCE_INSUFFICIENT",
                    "evidence": [],
                }
            )
    return list(dict.fromkeys(refs)), receipts


# ── Scope grounding ──


def _ground_scope(frame: dict[str, Any], asset: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ownership phrases → structured OWN relation (SPEC §9.2); scope
    coordinates only with direct permission-matrix evidence."""
    scope = dict(_dict(frame.get("scope")))
    ownership = _dict(scope.get("ownership_relation"))
    raw_ownership = _norm(ownership.get("raw"))
    if raw_ownership:
        if any(marker in raw_ownership for marker in _OWNERSHIP_SELF_MARKERS):
            structured = {"kind": "OWN", "target": "current_actor", "raw": raw_ownership}
            return structured, {
                "status": "GROUNDED",
                "candidate_ref": "OWN:current_actor",
                "reason_code": "OWNERSHIP_PHRASE_STRUCTURED",
                "evidence": ["ownership_phrase_self_marker"],
            }
        if any(marker in raw_ownership for marker in _OWNERSHIP_ORG_MARKERS):
            structured = {
                "kind": "OWN",
                "target": "current_actor_organization",
                "raw": raw_ownership,
            }
            return structured, {
                "status": "GROUNDED",
                "candidate_ref": "OWN:current_actor_organization",
                "reason_code": "OWNERSHIP_PHRASE_STRUCTURED",
                "evidence": ["ownership_phrase_organization_marker"],
            }
        return dict(ownership), {
            "status": "UNKNOWN",
            "reason_code": "OWNERSHIP_RELATION_UNRESOLVED",
            "evidence": [],
        }
    return dict(ownership), {"status": "NOT_MENTIONED", "reason_code": "NOT_APPLICABLE"}


# ── Main entry ──


def ground_semantic_frames(asset: dict[str, Any]) -> dict[str, Any]:
    """Ground every frame's slots with evidence receipts (in place)."""
    registry = build_business_concept_registry(asset)
    frames = frames_from_asset(asset)
    grounded = 0
    partial = 0
    pending = 0
    reason_counts: Counter = Counter()
    items: list[dict[str, Any]] = []

    for frame in frames:
        receipts: list[dict[str, Any]] = []

        actor_refs, actor_receipt = _ground_actor(frame, asset, registry)
        entity_refs, object_receipt, entity_labels = _ground_object(frame, asset, registry)
        operation_refs, operation_receipt = _ground_operation(
            frame, asset, entity_refs=entity_refs, entity_labels=entity_labels
        )
        state_refs, state_receipts = _ground_state(frame, asset)
        ownership_relation, scope_receipt = _ground_scope(frame, asset)

        receipts.append(
            {
                "grounding_type": "ACTOR",
                **actor_receipt,
            }
        )
        receipts.extend(
            {
                "grounding_type": "OBJECT",
                **receipt,
            }
            for receipt in _list(object_receipt.get("receipts"))
        )
        receipts.append(
            {
                "grounding_type": "OPERATION",
                **operation_receipt,
            }
        )
        receipts.append(
            {
                "grounding_type": "SCOPE",
                **scope_receipt,
            }
        )
        receipts.extend(
            {
                "grounding_type": "STATE",
                **receipt,
            }
            for receipt in state_receipts
        )

        # Write slot refs (grounding is the authority for the IR-resolvable
        # form). AMBIGUOUS/UNKNOWN clears stale knowledge-model refs — a
        # silent pick among candidates is forbidden (SPEC §12.3).
        actor = frame["actor"]
        if actor_refs:
            actor["grounded_actor_refs"] = actor_refs
            actor["resolution_status"] = "GROUNDED"
        else:
            actor["grounded_actor_refs"] = []
            if _text(actor_receipt.get("status")) in ("AMBIGUOUS", "UNKNOWN"):
                actor["resolution_status"] = "RESOLVED"
        frame["actor"] = actor
        action = frame["action"]
        if operation_refs:
            action["grounded_operation_refs"] = operation_refs
            action["resolution_status"] = "GROUNDED"
        frame["action"] = action
        obj = frame["object"]
        if entity_refs:
            obj["grounded_entity_refs"] = entity_refs
            obj["resolution_status"] = "GROUNDED"
        frame["object"] = obj
        scope = frame["scope"]
        if ownership_relation:
            scope["ownership_relation"] = ownership_relation
            scope["resolution_status"] = "RESOLVED"
        frame["scope"] = scope

        # Permission-row scope alignment: legacy permission/ownership relations
        # carry the permission row's scope in their node id, so the frame
        # channel must emit the same scope to dedup against them.
        permission_scope = _permission_scope_for(
            asset,
            _text(actor_receipt.get("mention")),
            _text(operation_receipt.get("candidate_ref")),
        )
        if permission_scope:
            receipts.append(
                {
                    "grounding_type": "SCOPE_ALIGNMENT",
                    "status": "GROUNDED",
                    "candidate_ref": permission_scope,
                    "reason_code": "PERMISSION_ROW_SCOPE_EXACT",
                    "evidence": ["permission_matrix_scope"],
                }
            )

        actor_ok = bool(actor_refs) or actor.get("resolution_status") in ("OMITTED", "NOT_MENTIONED")
        action_ok = (
            bool(operation_refs)
            or not _list(action.get("mentions"))
            or _text(_dict(frame.get("modality")).get("type")) in ("ASSERTS", "")
        )
        entity_ok = bool(entity_refs) or not _list(obj.get("mentions"))
        if actor_ok and action_ok and entity_ok:
            grounding_status = "GROUNDED"
            grounded += 1
        elif actor_refs or entity_refs or operation_refs:
            grounding_status = "PARTIAL"
            partial += 1
        else:
            grounding_status = "PENDING"
            pending += 1

        frame["technical_grounding"] = {
            "operation_refs": list(operation_refs),
            "entity_refs": list(entity_refs),
            "field_refs": [],
            "actor_refs": list(actor_refs),
            "state_value_refs": list(state_refs),
            "permission_scope": permission_scope,
            "status": grounding_status,
        }
        if grounding_status == "GROUNDED":
            frame["resolution"]["reason_codes"] = [
                code
                for code in _list(frame["resolution"].get("reason_codes"))
                if _text(code) != "TECHNICAL_GROUNDING_PENDING"
            ]
        frame["grounding_receipts"] = receipts
        for receipt in receipts:
            reason_counts[_text(receipt.get("reason_code"))] += 1

        # Scope structuring changes typed slots → recompute the signature.
        frame["resolution"]["semantic_signature"] = semantic_signature(frame)
        errors = validate_semantic_frame(frame)
        if errors:
            raise ValueError(
                "chinese_semantic_frame_invalid_after_grounding:"
                + ",".join(sorted(errors))
            )
        items.append(
            {
                "frame_id": _text(frame.get("frame_id")),
                "grounding_status": grounding_status,
                "receipts": receipts,
            }
        )

    asset["chinese_semantic_grounding_ledger"] = {
        "schema": CHINESE_SEMANTIC_GROUNDING_SCHEMA,
        "items": items,
        "closure": {
            "status": "PASS",
            "grounded_frame_count": grounded,
            "partial_frame_count": partial,
            "pending_frame_count": pending,
            "receipt_count": sum(len(_list(row.get("receipts"))) for row in items),
            "reason_code_counts": dict(sorted(reason_counts.items())),
            "similarity_merge_allowed": False,
        },
    }
    return asset
