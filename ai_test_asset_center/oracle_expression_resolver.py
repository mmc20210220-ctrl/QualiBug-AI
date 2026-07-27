"""Generic Oracle expression resolver.

Parses invariant raw text against Behavior IR entity/field vocabulary to
produce structured multi-entity expressions for Oracle compilation.

This module is industry-neutral: it uses ONLY the entity names, field names,
and relations present in the Behavior IR as its parsing dictionary.  No
project-specific business terms are encoded.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Vocabulary Construction ────────────────────────────────────────────────


def _build_entity_vocabulary(
    behavior_ir: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build entity_name -> entity_info mapping from Behavior IR."""
    entities: dict[str, dict[str, Any]] = {}
    for entity in _list(behavior_ir.get("entities")):
        if not isinstance(entity, dict):
            continue
        eid = _text(entity.get("id") or entity.get("entity_id"))
        name = _text(entity.get("name") or entity.get("entity_name"))
        if not eid:
            continue
        fields: list[dict[str, Any]] = []
        for f in _list(entity.get("fields")):
            if isinstance(f, dict):
                fields.append(f)
            elif isinstance(f, str):
                fields.append({"name": f})
        field_names = {
            _text(f.get("name") or f.get("field_id")): f
            for f in fields
            if _text(f.get("name") or f.get("field_id"))
        }
        entities[name] = {
            "entity_id": eid,
            "name": name,
            "fields": field_names,
            "field_list": list(field_names.keys()),
        }
    return entities


def _build_foreign_key_map(
    entities: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Detect foreign key relationships from field naming patterns.

    Pattern: field named '<singular_entity>_id' in entity B references
    entity A whose name starts with '<singular_entity>' (pluralized).
    """
    fks: list[dict[str, str]] = []
    entity_names = list(entities.keys())
    for ent_name, ent_info in entities.items():
        for field_name in ent_info["field_list"]:
            if not field_name.endswith("_id"):
                continue
            prefix = field_name[:-3]  # strip '_id'
            if not prefix:
                continue
            # Find target entity: name starts with prefix (handles plural)
            for target_name in entity_names:
                if target_name == ent_name:
                    continue
                # Match: prefix is singular stem of target entity name
                if (
                    target_name.startswith(prefix)
                    or target_name.rstrip("s").startswith(prefix)
                    or prefix.startswith(target_name.rstrip("s"))
                ):
                    fks.append({
                        "from_entity": ent_name,
                        "from_field": field_name,
                        "to_entity": target_name,
                        "to_field": "id",
                    })
                    break
    return fks


# ─── Operator Pattern Detection ─────────────────────────────────────────────

# Generic comparison patterns (language-neutral via common tokens)
_COMPARISON_PATTERNS: list[tuple[str, str]] = [
    ("不得超过", "LTE"),
    ("不能超过", "LTE"),
    ("不得大于", "LTE"),
    ("小于等于", "LTE"),
    ("必须小于", "LT"),
    ("不得少于", "GTE"),
    ("不能低于", "GTE"),
    ("大于等于", "GTE"),
    ("必须大于", "GT"),
    ("必须等于", "EQ"),
    ("必须等于", "EQ"),
    ("等于", "EQ"),
    ("不得为负", "GTE_ZERO"),
    ("不为负", "GTE_ZERO"),
    ("必须为正", "GT_ZERO"),
    ("lte", "LTE"),
    ("gte", "GTE"),
    ("<=", "LTE"),
    (">=", "GTE"),
    ("==", "EQ"),
]

# Aggregate patterns
_AGGREGATE_PATTERNS: list[tuple[str, str]] = [
    ("合计", "SUM"),
    ("总和", "SUM"),
    ("累计", "SUM"),
    ("总额", "SUM"),
    ("sum", "SUM"),
    ("count", "COUNT"),
    ("数量", "COUNT"),
    ("max", "MAX"),
    ("最大", "MAX"),
    ("min", "MIN"),
    ("最小", "MIN"),
]

# Conservation patterns
_CONSERVATION_PATTERNS: list[str] = [
    "守恒",
    "不变",
    "相等",
    "平衡",
    "conservation",
    "balance",
    "不变式",
]

# State/terminal patterns
_STATE_PATTERNS: list[tuple[str, str]] = [
    ("取消", "cancelled"),
    ("终止", "terminated"),
    ("完成", "completed"),
    ("拒绝", "rejected"),
    ("驳回", "rejected"),
    ("生效", "active"),
    ("激活", "active"),
    ("草稿", "draft"),
    ("cancelled", "cancelled"),
    ("terminated", "terminated"),
    ("completed", "completed"),
    ("rejected", "rejected"),
    ("active", "active"),
    ("draft", "draft"),
]

# Compensation patterns
_COMPENSATION_PATTERNS: list[str] = [
    "释放",
    "回退",
    "恢复",
    "返还",
    "release",
    "revert",
    "restore",
    "compensate",
]


# ─── Field Matching ─────────────────────────────────────────────────────────


def _find_field_references(
    text: str,
    entities: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Find field references in text by matching against entity field names.

    Returns list of {entity_name, field_name, entity_id, field_id}.
    """
    refs: list[dict[str, str]] = []
    text_lower = text.lower()
    for ent_name, ent_info in entities.items():
        for field_name in ent_info["field_list"]:
            # Match field name or its semantic stem in text
            # Use word-boundary-like matching for English field names
            field_lower = field_name.lower()
            # Check if field name (or significant part) appears in text
            if field_lower in text_lower:
                refs.append({
                    "entity_name": ent_name,
                    "field_name": field_name,
                    "entity_id": ent_info["entity_id"],
                })
            else:
                # Try semantic stem matching for compound fields
                # e.g., "available_amount" -> "available" matches "可用"
                parts = field_lower.split("_")
                if len(parts) >= 2:
                    # Check if the descriptive part appears
                    stem = parts[0] if parts[-1] in ("amount", "id", "no", "at", "by") else "_".join(parts[:-1])
                    if len(stem) >= 3 and stem in text_lower:
                        refs.append({
                            "entity_name": ent_name,
                            "field_name": field_name,
                            "entity_id": ent_info["entity_id"],
                        })
    # Deduplicate
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for r in refs:
        key = f"{r['entity_name']}.{r['field_name']}"
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _find_entity_references(
    text: str,
    entities: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Find entity references in text."""
    refs: list[dict[str, str]] = []
    text_lower = text.lower()
    for ent_name, ent_info in entities.items():
        if ent_name.lower() in text_lower:
            refs.append({
                "entity_name": ent_name,
                "entity_id": ent_info["entity_id"],
            })
        else:
            # Try singular form
            singular = ent_name.rstrip("s")
            if len(singular) >= 3 and singular.lower() in text_lower:
                refs.append({
                    "entity_name": ent_name,
                    "entity_id": ent_info["entity_id"],
                })
    return refs


# ─── Expression Type Detection ───────────────────────────────────────────────


def _detect_expression_type(raw: str) -> str:
    """Detect the primary expression type from raw text."""
    raw_lower = raw.lower()

    # Check conservation
    for pattern in _CONSERVATION_PATTERNS:
        if pattern in raw_lower:
            return "conservation"

    # Check compensation
    for pattern in _COMPENSATION_PATTERNS:
        if pattern in raw_lower:
            return "compensation"

    # Check state consistency (has state words + cross-entity implication)
    state_count = sum(1 for s, _ in _STATE_PATTERNS if s in raw_lower)
    if state_count >= 2:
        return "cross_entity_consistency"

    # Check aggregate/limit
    has_aggregate = any(p in raw_lower for p, _ in _AGGREGATE_PATTERNS)
    has_comparison = any(p in raw_lower for p, _ in _COMPARISON_PATTERNS)
    if has_aggregate and has_comparison:
        return "limit_constraint"
    if has_comparison:
        return "limit_constraint"

    # Default
    return "conservation"


def _detect_operator(raw: str) -> str:
    """Detect comparison operator from raw text."""
    for pattern, op in _COMPARISON_PATTERNS:
        if pattern in raw:
            return op
    return "EQ"


def _detect_aggregate(raw: str) -> str:
    """Detect aggregate function from raw text."""
    for pattern, func in _AGGREGATE_PATTERNS:
        if pattern in raw.lower():
            return func
    return "SUM"


def _detect_states(raw: str) -> list[str]:
    """Detect state values mentioned in raw text."""
    states: list[str] = []
    raw_lower = raw.lower()
    for pattern, state in _STATE_PATTERNS:
        if pattern in raw_lower and state not in states:
            states.append(state)
    return states


# ─── Main Resolver ───────────────────────────────────────────────────────────


def _infer_entity_from_path(
    path: str,
    entities: dict[str, dict[str, Any]],
) -> str:
    """Infer entity name from an operation path segment.

    Generic heuristic: match the last meaningful path segment (ignoring
    version prefixes and path parameters) against known entity names.
    E.g. '/api/v1/purchase-orders' -> 'purchase_orders'.
    """
    if not path:
        return ""
    # Split path, remove empty, version tokens, and parameter placeholders
    segments = [
        seg for seg in path.strip("/").split("/")
        if seg and not seg.startswith("{") and not re.match(r"^v\d+$", seg)
    ]
    # Try segments from last to first (most specific first)
    for seg in reversed(segments):
        # Normalize: replace hyphens with underscores for matching
        normalized = seg.replace("-", "_").lower()
        for ent_name in entities:
            ent_lower = ent_name.lower()
            if (
                normalized == ent_lower
                or normalized.rstrip("s") == ent_lower.rstrip("s")
                or ent_lower.startswith(normalized.rstrip("s"))
                or normalized.startswith(ent_lower.rstrip("s"))
            ):
                return ent_name
    return ""


def _infer_entities_from_operation(
    operation: dict[str, Any],
    entities: dict[str, dict[str, Any]],
    fks: list[dict[str, str]],
    raw: str,
) -> dict[str, dict[str, Any]]:
    """Infer involved entities from operation context when raw text has no explicit refs.

    Strategy:
    1. Infer primary entity from operation path
    2. Use FK graph to find related entities that share numeric fields
       mentioned semantically in the raw text (e.g. 'amount' fields)
    3. Return involved_entities dict
    """
    path = _text(operation.get("path"))
    primary_name = _infer_entity_from_path(path, entities)
    if not primary_name:
        return {}

    involved: dict[str, dict[str, Any]] = {}
    primary_info = entities.get(primary_name, {})
    involved[primary_name] = {
        "entity_id": primary_info.get("entity_id", ""),
        "entity_name": primary_name,
        "fields": ["amount"] if "amount" in primary_info.get("fields", {}) else list(primary_info.get("fields", {}).keys())[:3],
        "cardinality": "ONE",
    }

    # Find related entities via FK that also have numeric 'amount'-like fields
    # This handles: child_entity.amount <= parent_entity.available_amount
    numeric_stems = {"amount", "available_amount", "reserved_amount", "spent", "total", "balance"}
    for fk in fks:
        related_name = ""
        if fk["from_entity"] == primary_name:
            related_name = fk["to_entity"]
        elif fk["to_entity"] == primary_name:
            related_name = fk["from_entity"]
        if not related_name or related_name in involved:
            continue
        rel_info = entities.get(related_name, {})
        rel_fields = set(rel_info.get("fields", {}).keys())
        # Check if related entity has numeric fields that could be the constraint target
        matching_fields = rel_fields & numeric_stems
        if matching_fields:
            involved[related_name] = {
                "entity_id": rel_info.get("entity_id", ""),
                "entity_name": related_name,
                "fields": sorted(matching_fields),
                "cardinality": "ONE",
            }

    # Also check entities that have FK pointing TO the primary entity
    # e.g. contracts has budget_id -> budgets; payment_requests has contract_id -> contracts
    for fk in fks:
        if fk["to_entity"] == primary_name and fk["from_entity"] not in involved:
            from_name = fk["from_entity"]
            from_info = entities.get(from_name, {})
            from_fields = set(from_info.get("fields", {}).keys())
            matching = from_fields & numeric_stems
            if matching:
                involved[from_name] = {
                    "entity_id": from_info.get("entity_id", ""),
                    "entity_name": from_name,
                    "fields": sorted(matching),
                    "cardinality": "MANY",
                }

    return involved


def resolve_expression_from_invariant(
    invariant: dict[str, Any],
    behavior_ir: dict[str, Any],
    *,
    operation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a structured expression from invariant raw text + Behavior IR.

    Args:
        invariant: dict with 'expression' (containing 'raw') and/or 'description'
        behavior_ir: the full Behavior IR dict
        operation: optional operation dict (with 'path', 'entity_refs') for context

    Returns:
        {
            "status": "RESOLVED" | "UNRESOLVED",
            "error_code": str,
            "expression_type": str,
            "entity_bindings": {...},
            "join_plan": {...},
            "expression": {...},
            "observer_requirements": [...],
            "scope_fields": [...],
        }
    """
    inv = _dict(invariant)
    expr = _dict(inv.get("expression"))
    # V1.6.0: when structured operands/equation already exist, do not invent
    # fields via NL guessing. Callers must compile from structure or block.
    structured_operands = _list(expr.get("operands"))
    structured_equation = _dict(expr.get("equation") or inv.get("equation"))
    if structured_operands or structured_equation:
        return _unresolved("STRUCTURED_EXPRESSION_PRESENT_SKIP_NL_GUESS")

    raw = _text(expr.get("raw") or inv.get("description"))
    if not raw:
        return _unresolved("EMPTY_INVARIANT_RAW")

    ir = _dict(behavior_ir)
    entities = _build_entity_vocabulary(ir)
    if not entities:
        return _unresolved("NO_ENTITIES_IN_BEHAVIOR_IR")

    fks = _build_foreign_key_map(entities)

    # Step 1: Find entity and field references in raw text
    field_refs = _find_field_references(raw, entities)
    entity_refs = _find_entity_references(raw, entities)

    # Step 2: Detect expression type
    expression_type = _detect_expression_type(raw)

    # Step 3: Build entity bindings
    involved_entities: dict[str, dict[str, Any]] = {}
    for ref in field_refs:
        ent_name = ref["entity_name"]
        if ent_name not in involved_entities:
            involved_entities[ent_name] = {
                "entity_id": ref["entity_id"],
                "entity_name": ent_name,
                "fields": [],
                "cardinality": "ONE",
            }
        involved_entities[ent_name]["fields"].append(ref["field_name"])
    for ref in entity_refs:
        ent_name = ref["entity_name"]
        if ent_name not in involved_entities:
            involved_entities[ent_name] = {
                "entity_id": ref["entity_id"],
                "entity_name": ent_name,
                "fields": [],
                "cardinality": "ONE",
            }

    # Step 3b: Fallback - infer entities from operation context
    if not involved_entities and operation:
        involved_entities = _infer_entities_from_operation(
            _dict(operation), entities, fks, raw,
        )
        # Rebuild field_refs from inferred entities
        if involved_entities:
            field_refs = []
            for ent_name, ent_info in involved_entities.items():
                for fname in ent_info.get("fields", []):
                    field_refs.append({
                        "entity_name": ent_name,
                        "field_name": fname,
                        "entity_id": ent_info.get("entity_id", ""),
                    })

    if not involved_entities:
        return _unresolved("UNRESOLVED_ENTITY_ALIAS")

    # Step 4: Determine root vs related entities via FK relationships
    root_entity = _determine_root(involved_entities, fks, entities)
    related_entities = {
        name: info
        for name, info in involved_entities.items()
        if name != root_entity
    }

    # Determine cardinality for related entities
    for rel_name in related_entities:
        # If there's a FK from related to root, it's MANY (collection)
        has_fk_to_root = any(
            fk["from_entity"] == rel_name and fk["to_entity"] == root_entity
            for fk in fks
        )
        if has_fk_to_root:
            related_entities[rel_name]["cardinality"] = "MANY"
            # Find the FK field
            for fk in fks:
                if fk["from_entity"] == rel_name and fk["to_entity"] == root_entity:
                    related_entities[rel_name]["relation_key"] = fk["from_field"]
                    break

    # Step 5: Build join plan
    join_plan = _build_join_plan(root_entity, related_entities, fks, entities)

    # Step 6: Build structured expression based on type
    operator = _detect_operator(raw)
    aggregate_func = _detect_aggregate(raw)
    states = _detect_states(raw)

    expression = _build_expression(
        expression_type=expression_type,
        operator=operator,
        aggregate_func=aggregate_func,
        states=states,
        root_entity=root_entity,
        related_entities=related_entities,
        field_refs=field_refs,
        entities=entities,
        raw=raw,
    )

    if not expression:
        return _unresolved("UNSUPPORTED_EXPRESSION_NODE")

    # Step 7: Build observer requirements
    observer_requirements = _build_observer_requirements(
        root_entity=root_entity,
        related_entities=related_entities,
        involved_entities=involved_entities,
        expression_type=expression_type,
        entities=entities,
    )

    # Step 8: Detect scope fields (tenant_id, owner_id patterns)
    scope_fields = _detect_scope_fields(involved_entities, entities)

    # Build entity_bindings output
    entity_bindings: dict[str, Any] = {}
    if root_entity and root_entity in involved_entities:
        root_info = involved_entities[root_entity]
        entity_bindings["root"] = {
            "entity_id": root_info["entity_id"],
            "entity_name": root_entity,
            "cardinality": "ONE",
            "fields": root_info.get("fields", []),
        }
    for idx, (rel_name, rel_info) in enumerate(related_entities.items()):
        alias = f"related_{chr(97 + idx)}"  # related_a, related_b, ...
        entity_bindings[alias] = {
            "entity_id": rel_info["entity_id"],
            "entity_name": rel_name,
            "cardinality": rel_info.get("cardinality", "ONE"),
            "fields": rel_info.get("fields", []),
            "relation_key": rel_info.get("relation_key", ""),
        }

    return {
        "status": "RESOLVED",
        "error_code": "",
        "expression_type": expression_type,
        "entity_bindings": entity_bindings,
        "join_plan": join_plan,
        "expression": expression,
        "observer_requirements": observer_requirements,
        "scope_fields": scope_fields,
        "root_entity": root_entity,
        "related_entities": {
            name: {
                "entity_id": info["entity_id"],
                "cardinality": info.get("cardinality", "ONE"),
                "relation_key": info.get("relation_key", ""),
                "fields": info.get("fields", []),
            }
            for name, info in related_entities.items()
        },
    }


# ─── Helper Functions ────────────────────────────────────────────────────────


def _unresolved(error_code: str) -> dict[str, Any]:
    return {
        "status": "UNRESOLVED",
        "error_code": error_code,
        "expression_type": "",
        "entity_bindings": {},
        "join_plan": {},
        "expression": {},
        "observer_requirements": [],
        "scope_fields": [],
        "root_entity": "",
        "related_entities": {},
    }


def _determine_root(
    involved_entities: dict[str, dict[str, Any]],
    fks: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
) -> str:
    """Determine root entity: the one that is referenced BY others (has no FK out)."""
    names = list(involved_entities.keys())
    if not names:
        return ""
    if len(names) == 1:
        return names[0]

    # Root = entity that other entities point TO (is the parent)
    referenced_targets: dict[str, int] = {n: 0 for n in names}
    for fk in fks:
        if fk["from_entity"] in involved_entities and fk["to_entity"] in involved_entities:
            referenced_targets[fk["to_entity"]] += 1

    # The most-referenced entity is the root (parent)
    best = max(names, key=lambda n: referenced_targets.get(n, 0))
    if referenced_targets.get(best, 0) > 0:
        return best

    # Fallback: entity with most fields (likely the parent/main entity)
    return max(names, key=lambda n: len(entities.get(n, {}).get("field_list", [])))


def _build_join_plan(
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    fks: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build join plan from FK relationships."""
    joins: list[dict[str, Any]] = []
    for rel_name, rel_info in related_entities.items():
        # Find FK connecting related to root
        fk_field = rel_info.get("relation_key", "")
        if not fk_field:
            # Try to find it
            for fk in fks:
                if fk["from_entity"] == rel_name and fk["to_entity"] == root_entity:
                    fk_field = fk["from_field"]
                    break
        if fk_field:
            joins.append({
                "left_alias": "root",
                "left_field": "id",
                "operator": "EQ",
                "right_alias": rel_name,
                "right_field": fk_field,
                "cardinality": rel_info.get("cardinality", "MANY"),
            })
    return {
        "root_entity_alias": "root",
        "root_entity_name": root_entity,
        "joins": joins,
    }


def _build_expression(
    *,
    expression_type: str,
    operator: str,
    aggregate_func: str,
    states: list[str],
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    field_refs: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
    raw: str,
) -> dict[str, Any]:
    """Build structured expression tree based on detected type."""

    if expression_type == "limit_constraint":
        return _build_limit_expression(
            operator=operator,
            aggregate_func=aggregate_func,
            root_entity=root_entity,
            related_entities=related_entities,
            field_refs=field_refs,
            entities=entities,
        )

    if expression_type == "conservation":
        return _build_conservation_expression(
            root_entity=root_entity,
            related_entities=related_entities,
            field_refs=field_refs,
            entities=entities,
            raw=raw,
        )

    if expression_type == "cross_entity_consistency":
        return _build_state_consistency_expression(
            states=states,
            root_entity=root_entity,
            related_entities=related_entities,
            field_refs=field_refs,
            entities=entities,
        )

    if expression_type == "compensation":
        return _build_compensation_expression(
            root_entity=root_entity,
            related_entities=related_entities,
            field_refs=field_refs,
            entities=entities,
        )

    return {}


def _find_amount_fields(
    entity_name: str,
    entities: dict[str, dict[str, Any]],
) -> list[str]:
    """Find numeric/amount fields in an entity (generic heuristic)."""
    ent = entities.get(entity_name, {})
    amount_fields: list[str] = []
    for fname in ent.get("field_list", []):
        fl = fname.lower()
        if any(
            token in fl
            for token in ("amount", "total", "sum", "value", "price", "cost", "balance")
        ):
            amount_fields.append(fname)
    return amount_fields


def _find_status_fields(
    entity_name: str,
    entities: dict[str, dict[str, Any]],
) -> list[str]:
    """Find status/state fields in an entity."""
    ent = entities.get(entity_name, {})
    status_fields: list[str] = []
    for fname in ent.get("field_list", []):
        fl = fname.lower()
        if any(token in fl for token in ("status", "state", "phase", "stage")):
            status_fields.append(fname)
    return status_fields


def _build_limit_expression(
    *,
    operator: str,
    aggregate_func: str,
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    field_refs: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build: aggregate(related.field) <operator> root.field"""
    # Find the aggregate side (related entity amount field)
    agg_entity = ""
    agg_field = ""
    root_field = ""

    # Related entities provide the aggregate
    for rel_name, rel_info in related_entities.items():
        amount_fields = _find_amount_fields(rel_name, entities)
        if amount_fields:
            agg_entity = rel_name
            agg_field = amount_fields[0]  # Primary amount field
            break

    # Root entity provides the limit field
    root_amounts = _find_amount_fields(root_entity, entities)
    if root_amounts:
        # Prefer fields with 'total', 'amount', 'limit' semantics
        for f in root_amounts:
            if "total" in f.lower() or "amount" in f.lower():
                root_field = f
                break
        if not root_field:
            root_field = root_amounts[0]

    # If no related entity, check field_refs for multi-entity info
    if not agg_entity:
        for ref in field_refs:
            if ref["entity_name"] != root_entity:
                agg_entity = ref["entity_name"]
                amounts = _find_amount_fields(agg_entity, entities)
                if amounts:
                    agg_field = amounts[0]
                break

    if not agg_field or not root_field:
        # Try from field_refs directly
        for ref in field_refs:
            if ref["entity_name"] == root_entity and not root_field:
                root_field = ref["field_name"]
            elif ref["entity_name"] != root_entity and not agg_field:
                agg_entity = ref["entity_name"]
                agg_field = ref["field_name"]

    if not root_field:
        return {}

    # Map operator: GTE_ZERO means field >= 0
    if operator in ("GTE_ZERO", "GT_ZERO"):
        return {
            "node_type": "comparison",
            "operator": "GTE" if operator == "GTE_ZERO" else "GT",
            "left": {
                "node_type": "field_ref",
                "entity_alias": "root",
                "entity_name": root_entity,
                "field_id": root_field,
                "snapshot": "AFTER",
            },
            "right": {
                "node_type": "literal",
                "value": 0,
                "data_type": "decimal",
            },
        }

    if not agg_entity or not agg_field:
        return {}

    return {
        "node_type": "comparison",
        "operator": operator,
        "left": {
            "node_type": "aggregate",
            "function": aggregate_func,
            "source_entity_alias": "related_a",
            "source_entity_name": agg_entity,
            "value_field": agg_field,
            "filters": [],
        },
        "right": {
            "node_type": "field_ref",
            "entity_alias": "root",
            "entity_name": root_entity,
            "field_id": root_field,
            "snapshot": "CURRENT",
        },
    }


def _build_conservation_expression(
    *,
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    field_refs: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
    raw: str,
) -> dict[str, Any]:
    """Build conservation: SUM(related.field) == root.field or before==after."""
    # If there are related entities with amount fields -> cross-entity conservation
    for rel_name, rel_info in related_entities.items():
        amount_fields = _find_amount_fields(rel_name, entities)
        root_amounts = _find_amount_fields(root_entity, entities)
        if amount_fields and root_amounts:
            return {
                "node_type": "comparison",
                "operator": "EQ",
                "left": {
                    "node_type": "aggregate",
                    "function": "SUM",
                    "source_entity_alias": "related_a",
                    "source_entity_name": rel_name,
                    "value_field": amount_fields[0],
                    "filters": [],
                },
                "right": {
                    "node_type": "field_ref",
                    "entity_alias": "root",
                    "entity_name": root_entity,
                    "field_id": root_amounts[0],
                    "snapshot": "CURRENT",
                },
            }

    # Single entity conservation (before/after unchanged)
    if root_entity:
        root_amounts = _find_amount_fields(root_entity, entities)
        if root_amounts:
            return {
                "node_type": "conservation",
                "operator": "unchanged_sum",
                "terms": [
                    {
                        "entity_alias": "root",
                        "entity_name": root_entity,
                        "field_id": f,
                    }
                    for f in root_amounts[:3]
                ],
            }

    return {}


def _build_state_consistency_expression(
    *,
    states: list[str],
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    field_refs: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build: IMPLIES(root.state IN [...], ALL related.state IN [...])."""
    root_status_fields = _find_status_fields(root_entity, entities)
    if not root_status_fields:
        return {}

    # Find related entity with status field
    rel_name = ""
    rel_status_field = ""
    for rn, ri in related_entities.items():
        rel_statuses = _find_status_fields(rn, entities)
        if rel_statuses:
            rel_name = rn
            rel_status_field = rel_statuses[0]
            break

    if not rel_name:
        # Try from field_refs
        for ref in field_refs:
            if ref["entity_name"] != root_entity:
                statuses = _find_status_fields(ref["entity_name"], entities)
                if statuses:
                    rel_name = ref["entity_name"]
                    rel_status_field = statuses[0]
                    break

    # Determine condition and consequence states
    # Heuristic: terminal states (cancelled, terminated, completed, rejected)
    # are condition; active/draft are forbidden in consequence
    terminal_states = [s for s in states if s in ("cancelled", "terminated", "completed", "rejected")]
    active_states = [s for s in states if s in ("active", "draft")]

    if not terminal_states:
        terminal_states = states[:1] if states else []
    if not active_states:
        active_states = ["active"]

    result: dict[str, Any] = {
        "node_type": "implies",
        "condition": {
            "node_type": "state_in",
            "entity_alias": "root",
            "entity_name": root_entity,
            "field_id": root_status_fields[0],
            "values": terminal_states,
        },
        "consequence": {
            "node_type": "all_match",
            "collection_entity_alias": "related_a",
            "collection_entity_name": rel_name or root_entity,
            "predicate": {
                "node_type": "state_not_in",
                "field_id": rel_status_field or "status",
                "values": active_states,
            },
        },
        "empty_collection_policy": "PASS",
    }
    return result


def _build_compensation_expression(
    *,
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    field_refs: list[dict[str, str]],
    entities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build: AFTER(root.field) == BEFORE(root.field) + delta (compensation)."""
    # Find the field that should be restored
    target_entity = root_entity
    amount_fields = _find_amount_fields(target_entity, entities)

    # If related entities have amount fields, the compensation target might be there
    for rel_name in related_entities:
        rel_amounts = _find_amount_fields(rel_name, entities)
        if rel_amounts:
            target_entity = rel_name
            amount_fields = rel_amounts
            break

    if not amount_fields:
        return {}

    return {
        "node_type": "delta",
        "operator": "INCREASED_BY",
        "field": {
            "entity_alias": "root" if target_entity == root_entity else "related_a",
            "entity_name": target_entity,
            "field_id": amount_fields[0],
        },
        "expected_delta_source": "operation_body",
        "snapshot_before": "BEFORE",
        "snapshot_after": "AFTER",
    }


def _build_observer_requirements(
    *,
    root_entity: str,
    related_entities: dict[str, dict[str, Any]],
    involved_entities: dict[str, dict[str, Any]],
    expression_type: str,
    entities: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build observer requirements from entity bindings.

    For related entities with cardinality MANY, includes collection_requirements
    specifying pagination, deduplication, and empty collection policy.
    """
    requirements: list[dict[str, Any]] = []

    # Root entity observation
    if root_entity:
        ent_info = entities.get(root_entity, {})
        requirements.append({
            "entity_alias": "root",
            "entity_name": root_entity,
            "entity_id": ent_info.get("entity_id", ""),
            "required_fields": involved_entities.get(root_entity, {}).get("fields", []),
            "aggregate_fields": [],
            "scope_fields": _detect_scope_fields_for_entity(root_entity, entities),
            "identity_fields": _detect_identity_fields(root_entity, entities),
            "snapshot": "BEFORE_AND_AFTER" if expression_type in ("conservation", "compensation") else "CURRENT",
            "cardinality": "ONE",
        })

    # Related entity observations
    for idx, (rel_name, rel_info) in enumerate(related_entities.items()):
        alias = f"related_{chr(97 + idx)}"
        ent_info = entities.get(rel_name, {})
        amount_fields = _find_amount_fields(rel_name, entities)
        cardinality = rel_info.get("cardinality", "MANY")
        identity_fields = _detect_identity_fields(rel_name, entities)

        req: dict[str, Any] = {
            "entity_alias": alias,
            "entity_name": rel_name,
            "entity_id": ent_info.get("entity_id", ""),
            "required_fields": rel_info.get("fields", []),
            "aggregate_fields": amount_fields,
            "scope_fields": _detect_scope_fields_for_entity(rel_name, entities),
            "identity_fields": identity_fields,
            "snapshot": "BEFORE_AND_AFTER" if expression_type in ("conservation", "compensation") else "CURRENT",
            "cardinality": cardinality,
            "relation_key": rel_info.get("relation_key", ""),
        }

        # Collection requirements for MANY cardinality
        if cardinality == "MANY":
            req["collection_requirements"] = {
                "pagination_required": True,
                "deduplicate_by": identity_fields or ["id"],
                "empty_collection_policy": "INDETERMINATE",  # Default: don't assume empty=pass
            }

        requirements.append(req)

    return requirements


def _detect_identity_fields(
    entity_name: str,
    entities: dict[str, dict[str, Any]],
) -> list[str]:
    """Detect identity fields for deduplication (id, uuid, code, etc.)."""
    ent = entities.get(entity_name, {})
    identity: list[str] = []
    for fname in ent.get("field_list", []):
        fl = fname.lower()
        # Match common identity patterns
        if fl in ("id", "uuid", "guid", "key", "code", "slug"):
            identity.append(fname)
        elif fl.endswith("_id") or fl.endswith("_key") or fl.endswith("_code"):
            # Only include primary-looking keys, not foreign keys
            if not any(fk in fl for fk in ("parent_", "owner_", "tenant_", "user_", "created_by")):
                identity.append(fname)
    return identity or ["id"]  # Default to id


def _detect_scope_fields(
    involved_entities: dict[str, dict[str, Any]],
    entities: dict[str, dict[str, Any]],
) -> list[str]:
    """Detect scope fields (tenant, owner) across involved entities."""
    scope: set[str] = set()
    for ent_name in involved_entities:
        scope.update(_detect_scope_fields_for_entity(ent_name, entities))
    return sorted(scope)


def _detect_scope_fields_for_entity(
    entity_name: str,
    entities: dict[str, dict[str, Any]],
) -> list[str]:
    """Detect scope fields for a single entity."""
    ent = entities.get(entity_name, {})
    scope: list[str] = []
    for fname in ent.get("field_list", []):
        fl = fname.lower()
        if any(token in fl for token in ("tenant_id", "owner_id", "department_id")):
            scope.append(fname)
    return scope
