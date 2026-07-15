"""Versioned Behavior IR — structured executable fact model for discovery.

Schema: qualibug.behavior-ir.v2

Natural language is for explanation only. Downstream obligation/experiment
compilation must reference IR node IDs. No industry or benchmark hardcoding.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any


SCHEMA_VERSION = "qualibug.behavior-ir.v2"
V1_SCHEMA_VERSION = "qualibug.behavior-ir.v1"
ALLOWED_RELATION_TYPES = frozenset({
    "produces",
    "consumes",
    "transitions",
    "permits",
    "denies",
    "owns",
    "scopes",
    "conserves",
    "observes",
    "compensates",
    "permission_unknown",
})
_DERIVATIONS = {"explicit", "schema-derived", "runtime-observed", "model-inferred"}
_STATUSES = {"accepted", "conflicting", "unsupported", "unknown"}


class BehaviorIRError(ValueError):
    """Behavior IR is not valid for authoritative runtime compilation."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_relation(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize one typed relation without inventing missing join targets."""

    if not isinstance(value, dict):
        raise BehaviorIRError("relation_not_object")
    relation_type = _text(value.get("relation_type"))
    if relation_type not in ALLOWED_RELATION_TYPES:
        raise BehaviorIRError(f"relation_type_invalid:{relation_type}")
    for field in ("id", "from_ref", "to_ref"):
        if not _text(value.get(field)):
            raise BehaviorIRError(f"relation_field_missing:{field}")
    return {
        **value,
        "relation_type": relation_type,
        "operation_ref": _text(value.get("operation_ref")),
        "actor_ref": _text(value.get("actor_ref")),
        "preconditions": list(value.get("preconditions") or []),
        "effects": list(value.get("effects") or []),
        "source_refs": list(value.get("source_refs") or []),
    }


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"bir_{digest}"


def _source_ref(source_id: str = "", *, version: str = "", locator: str = "", quote: str = "", kind: str = "") -> dict[str, Any]:
    quote_text = _text(quote)
    return {
        "source_id": _text(source_id) or "unknown",
        "version": _text(version),
        "locator": _text(locator),
        "kind": _text(kind),
        "quote_hash": hashlib.sha256(quote_text.encode("utf-8")).hexdigest()[:16] if quote_text else "",
    }


_METHOD_ACTIONS = {
    "GET": {"get", "read", "view", "list", "query"},
    "HEAD": {"head", "read", "view"},
    "OPTIONS": {"options", "read"},
    "POST": {"post", "create", "submit", "request", "write"},
    "PUT": {"put", "update", "modify", "write"},
    "PATCH": {"patch", "update", "modify", "write"},
    "DELETE": {"delete", "remove", "write"},
}
_UNIVERSAL_ACTIONS = {
    "*",
    "all",
    "any",
    "full_access",
    "manage",
    "administer",
}
_UNIVERSAL_RESOURCES = {
    "*",
    "all",
    "any",
    "all_resources",
    "everything",
    "global",
}
_EXCLUSIVE_ROLE_MARKERS = (
    r"\bonly\b",
    r"\bsolely\b",
    r"\bexclusively\b",
    r"\brestricted\s+to\b",
    r"\blimited\s+to\b",
    r"\bexclusive\s+to\b",
    "\u4ec5\u9650",
    "\u53ea\u9650",
    "\u552f\u6709",
    "\u5fc5\u987b\u7531",
)
_PERMIT_DECISIONS = {"allow", "allowed", "grant", "granted", "permit", "permitted"}
_DENY_DECISIONS = {
    "deny",
    "denied",
    "forbid",
    "forbidden",
    "not_allow",
    "not_allowed",
    "prohibit",
    "prohibited",
}
_CLEANUP_ACTION_RE = re.compile(
    r"(?:cancel|close|void|disable|archive|reject|release|rollback|revoke|"
    r"remove|delete|deactivate|suspend|expire|invalidate|terminate|withdraw|"
    r"abandon|discard|retire|freeze|reset|clear|purge)$",
    re.I,
)


def _path_shape(value: Any) -> str:
    path = _text(value).split("?", 1)[0].strip().lower()
    if not path:
        return ""
    segments = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        if (
            (segment.startswith("{") and segment.endswith("}"))
            or segment.startswith(":")
            or segment == "*"
        ):
            segments.append("{}")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def _canonical_json_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _merge_unique_sorted(*collections: list[Any]) -> list[Any]:
    by_key: dict[str, Any] = {}
    for collection in collections:
        for value in _list(collection):
            by_key.setdefault(_canonical_json_key(value), value)
    return [by_key[key] for key in sorted(by_key)]


def _json_examples_from_text(value: Any) -> list[dict[str, Any]]:
    text = _text(value)
    if not text:
        return []
    examples: list[dict[str, Any]] = []
    blocks = re.findall(
        r"```(?:json|JSON)?\s*(\{.*?\})\s*```",
        text,
        flags=re.DOTALL,
    )
    if not blocks:
        match = re.search(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, flags=re.DOTALL)
        blocks = [match.group(1)] if match else []
    for block in blocks:
        try:
            parsed = json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed:
            examples.append(parsed)
    curl_blocks = re.findall(
        r"(?:-d|--data|--data-raw)\s+['\"](\{.*?\})['\"]",
        text,
        flags=re.DOTALL,
    )
    for block in curl_blocks:
        try:
            parsed = json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and parsed:
            examples.append(parsed)
    yaml_blocks = re.findall(
        r"```(?:yaml|yml)\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    for block in yaml_blocks:
        parsed_yaml: dict[str, Any] = {}
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, raw_value = (part.strip() for part in line.split(":", 1))
            if not key:
                continue
            try:
                parsed_value = json.loads(raw_value)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed_value = raw_value.strip("'\"")
            parsed_yaml[key] = parsed_value
        if parsed_yaml:
            examples.append(parsed_yaml)
    return examples


def _request_example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(schema.get("example"))
    if direct:
        return deepcopy(direct)
    for media in _dict(schema.get("content")).values():
        if not isinstance(media, dict):
            continue
        example = _dict(media.get("example"))
        if example:
            return deepcopy(example)
        for row in _dict(media.get("examples")).values():
            value = _dict(row).get("value")
            if isinstance(value, dict) and value:
                return deepcopy(value)
    return {}


def _operation_request_example(operation: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(operation.get("request_example"))
    if direct:
        return deepcopy(direct)
    schema_example = _request_example_from_schema(
        _dict(operation.get("request_schema") or operation.get("requestBody"))
    )
    if schema_example:
        return schema_example
    examples = _list(operation.get("examples"))
    for example in examples:
        if isinstance(example, dict) and example:
            return deepcopy(example)
    source_examples = _json_examples_from_text(operation.get("source_excerpt"))
    return deepcopy(source_examples[0]) if source_examples else {}


def _schema_type_for_value(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


def _schema_from_example(value: Any) -> dict[str, Any]:
    schema_type = _schema_type_for_value(value)
    if isinstance(value, dict):
        return {
            "type": "object",
            "required": sorted(str(key) for key in value),
            "properties": {
                str(key): _schema_from_example(child)
                for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            },
        }
    if isinstance(value, list):
        first = next((item for item in value if item is not None), None)
        return {
            "type": "array",
            "items": _schema_from_example(first) if first is not None else {},
        }
    return {"type": schema_type}


def _schema_from_field_dictionary(fields: list[Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in sorted({_text(value) for value in fields if _text(value)}):
        head = field.split(".", 1)[0].split("[", 1)[0]
        if not head:
            continue
        properties.setdefault(head, {"type": "string"})
    if not properties:
        return {}
    return {
        "type": "object",
        "properties": properties,
    }


def _merge_schema_dicts(prior: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if not prior:
        return deepcopy(incoming)
    if not incoming:
        return deepcopy(prior)
    merged: dict[str, Any] = {}
    for key in sorted(set(prior) | set(incoming)):
        left = prior.get(key)
        right = incoming.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            merged[key] = _merge_schema_dicts(left, right)
        elif isinstance(left, list) and isinstance(right, list):
            merged[key] = _merge_unique_sorted(left, right)
        elif key not in prior:
            merged[key] = deepcopy(right)
        elif key not in incoming or left == right:
            merged[key] = deepcopy(left)
        else:
            merged[key] = min(left, right, key=_canonical_json_key)
    return merged


def _schema_conflict_paths(prior: Any, incoming: Any, path: str = "") -> list[str]:
    if not prior or not incoming:
        return []
    if isinstance(prior, dict) and isinstance(incoming, dict):
        conflicts: list[str] = []
        for key in sorted(set(prior) & set(incoming)):
            if key in {"example", "examples", "required", "description", "summary"}:
                continue
            conflicts.extend(
                _schema_conflict_paths(
                    prior.get(key),
                    incoming.get(key),
                    f"{path}.{key}" if path else str(key),
                )
            )
        return conflicts
    if isinstance(prior, list) or isinstance(incoming, list):
        return []
    return [path or "$"] if prior != incoming else []


def _request_schema_for_operation(operation: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(_dict(operation.get("request_schema") or operation.get("requestBody")))
    example = _operation_request_example(operation)
    field_schema = _schema_from_field_dictionary(_list(operation.get("field_dictionary")))
    inferred = _schema_from_example(example) if example else field_schema
    if not schema and (example or inferred):
        schema = {"content": {"application/json": {}}}
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        if example and not _dict(media.get("example")):
            media["example"] = deepcopy(example)
        if inferred:
            media["schema"] = _merge_schema_dicts(_dict(media.get("schema")), inferred)
        content["application/json"] = media
        schema["content"] = content
    elif inferred:
        schema = _merge_schema_dicts(schema, inferred)
    return schema


def _best_text(left: Any, right: Any) -> str:
    candidates = [_text(left), _text(right)]
    candidates = [value for value in candidates if value]
    if not candidates:
        return ""
    return sorted(candidates, key=lambda value: (-len(value), value))[0]


def _canonical_operation_id(source_refs: list[Any], fallback: str) -> str:
    aliases = [
        _text(value)
        for value in source_refs
        if _text(value) and ":" not in _text(value)
    ]
    return sorted(aliases)[0] if aliases else fallback


def _canonical_transport_operation_id(service: Any, method: Any, path: Any) -> str:
    service_part = re.sub(r"[^a-z0-9]+", "_", _text(service).lower()).strip("_")
    path_part = re.sub(r"[^a-z0-9]+", "_", _path_shape(path).lower()).strip("_")
    return "_".join(part for part in (service_part, _text(method).lower(), path_part or "root") if part)


def _canonicalize_duplicate_operation_ids(model: dict[str, Any]) -> None:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    by_operation_id: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        operation_id = _text(operation.get("operation_id"))
        if operation_id:
            by_operation_id.setdefault(operation_id, []).append(operation)
        operation["canonical_operation_id"] = _canonical_transport_operation_id(
            operation.get("service"), operation.get("method"), operation.get("path")
        )
    used_ids = {_text(row.get("operation_id")) for row in operations if _text(row.get("operation_id"))}
    for source_operation_id, duplicates in sorted(by_operation_id.items()):
        if len(duplicates) < 2:
            continue
        duplicate_refs: list[str] = []
        source_refs: list[dict[str, Any]] = []
        for operation in sorted(duplicates, key=lambda row: _text(row.get("id"))):
            canonical_id = _text(operation.get("canonical_operation_id"))
            if canonical_id in used_ids and canonical_id != source_operation_id:
                canonical_id = f"{canonical_id}_{_text(operation.get('id'))[-8:]}"
            operation["operation_id"] = canonical_id
            used_ids.add(canonical_id)
            duplicate_refs.append(_text(operation.get("id")))
            source_refs.extend(_list(operation.get("source_refs")))
        model["conflicts"].append(_fact_node(
            node_id=_stable_id("conflict", "duplicate_source_operation_id", source_operation_id),
            typed_fields={
                "conflict_type": "duplicate_source_operation_id",
                "source_operation_id": source_operation_id,
                "operation_refs": duplicate_refs,
            },
            source_refs=_merge_unique_sorted(source_refs),
            confidence=1.0,
            derivation="explicit",
            status="conflicting",
        ))


def _singular_token(value: Any) -> str:
    token = re.sub(r"[^a-z0-9]+", "", _text(value).lower())
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and len(token) > 1 and not token.endswith("ss"):
        return token[:-1]
    return token


def _canonical_entity_name(value: Any) -> str:
    parts = [
        _singular_token(part)
        for part in re.findall(r"[a-z0-9]+", _text(value).lower())
        if _singular_token(part)
    ]
    return "_".join(parts)


def _operation_structural_entity(
    operation: dict[str, Any],
    entities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    path_parts = [
        _singular_token(part)
        for part in re.findall(r"[a-z0-9]+", _path_shape(operation.get("path")))
        if _singular_token(part) not in {"api", "v1", "v2", "v3", "admin"}
    ]
    if not path_parts:
        return None
    ranked: list[tuple[int, int, int, dict[str, Any]]] = []
    for entity in entities:
        canonical = _canonical_entity_name(entity.get("name"))
        entity_parts = [part for part in canonical.split("_") if part]
        if not entity_parts or len(entity_parts) > len(path_parts):
            continue
        positions = [
            index
            for index in range(len(path_parts) - len(entity_parts) + 1)
            if path_parts[index:index + len(entity_parts)] == entity_parts
        ]
        if not positions:
            continue
        ranked.append((
            len(entity_parts),
            len(canonical),
            -min(positions),
            entity,
        ))
    if not ranked:
        return None
    best_score = max(row[:3] for row in ranked)
    matches = [row[3] for row in ranked if row[:3] == best_score]
    return matches[0] if len(matches) == 1 else None


def _resource_matches_operation(resource: Any, operation: dict[str, Any]) -> bool:
    resource_text = _text(resource).lower()
    operation_path = _text(operation.get("path"))
    if not resource_text or not operation_path:
        return False
    if _normalize_action(resource_text) in _UNIVERSAL_RESOURCES:
        return True
    if resource_text.startswith("/"):
        return _path_shape(resource_text) == _path_shape(operation_path)
    resource_tokens = set(re.findall(r"[a-z0-9_]+", resource_text))
    operation_tokens = set(re.findall(r"[a-z0-9_]+", operation_path.lower()))
    for value in _list(operation.get("tags")) + _list(operation.get("entity_refs")):
        operation_tokens.update(re.findall(r"[a-z0-9_]+", _text(value).lower()))
    return bool(resource_tokens and resource_tokens.intersection(operation_tokens))


def _normalize_action(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", _text(value).lower())


def _actions_match_operation(actions: list[Any], operation: dict[str, Any]) -> bool:
    normalized = {_text(action).lower() for action in actions if _text(action)}
    normalized = {_normalize_action(action) for action in normalized}
    if normalized.intersection(_UNIVERSAL_ACTIONS):
        return True
    method = _text(operation.get("method")).upper()
    return bool(normalized.intersection(_METHOD_ACTIONS.get(method, {method.lower()})))


def _declared_permission_polarity(row: dict[str, Any]) -> str:
    raw_decision = _normalize_action(
        row.get("decision")
        or row.get("effect")
        or row.get("outcome")
        or row.get("access")
    )
    if row.get("allowed") is False:
        return "DENY"
    if row.get("allowed") is True:
        return "PERMIT"
    if raw_decision in _DENY_DECISIONS:
        return "DENY"
    if raw_decision in _PERMIT_DECISIONS:
        return "PERMIT"
    return ""


def _permission_row_decision(
    row: dict[str, Any],
    operation: dict[str, Any],
) -> str:
    """Return PERMIT, DENY, or UNKNOWN from explicit permission evidence.

    An omitted action is not evidence of a denial. A caller may opt into
    closed-world semantics only through an explicit policy declaration.
    """

    denied_actions = _list(
        row.get("denied_actions")
        or row.get("forbidden_actions")
        or row.get("prohibited_actions")
    )
    if denied_actions and _actions_match_operation(denied_actions, operation):
        return "DENY"

    declared_polarity = _declared_permission_polarity(row)

    actions = _list(row.get("actions"))
    if not actions and _text(row.get("action")):
        actions = [row.get("action")]

    if declared_polarity == "DENY":
        return "DENY" if not actions or _actions_match_operation(actions, operation) else "UNKNOWN"
    raw_decision = _normalize_action(
        row.get("decision")
        or row.get("effect")
        or row.get("outcome")
        or row.get("access")
    )
    if raw_decision and not declared_polarity:
        return "UNKNOWN"
    return "PERMIT" if _actions_match_operation(actions, operation) else "UNKNOWN"


def _relation_node(
    *,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    operation_ref: str = "",
    actor_ref: str = "",
    preconditions: list[Any] | None = None,
    effects: list[Any] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    confidence: float = 0.8,
    derivation: str = "schema-derived",
    status: str = "accepted",
    permission_decision: str = "",
    source_relationship_ref: str = "",
) -> dict[str, Any]:
    return _fact_node(
        node_id=_stable_id("rel", relation_type, from_ref, to_ref, operation_ref, actor_ref),
        typed_fields={
            "relation_type": relation_type,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "preconditions": list(preconditions or []),
            "effects": list(effects or []),
            "permission_decision": _text(permission_decision),
            "source_relationship_ref": _text(source_relationship_ref),
        },
        source_refs=source_refs,
        confidence=confidence,
        derivation=derivation,
        status=status,
    )


def _fact_node(
    *,
    node_id: str,
    typed_fields: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    confidence: float = 0.5,
    derivation: str = "explicit",
    status: str = "accepted",
) -> dict[str, Any]:
    der = derivation if derivation in _DERIVATIONS else "model-inferred"
    st = status if status in _STATUSES else "unknown"
    conf = max(0.0, min(1.0, float(confidence)))
    return {
        "id": node_id,
        **typed_fields,
        "source_refs": list(source_refs or []),
        "confidence": conf,
        "derivation": der,
        "status": st,
    }


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        node_id = _text(node.get("id"))
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        result.append(node)
    return result


def _derive_permission_relations(
    model: dict[str, Any],
    permission_rows: list[Any],
    *,
    closed_world: bool = False,
) -> list[dict[str, Any]]:
    actors_by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        role_key = _text(actor.get("role_key") or actor.get("role")).lower()
        if role_key:
            actors_by_role.setdefault(role_key, []).append(actor)

    rows_by_role: dict[str, list[dict[str, Any]]] = {}
    for row in permission_rows:
        if not isinstance(row, dict):
            continue
        role_key = _text(row.get("role") or row.get("actor") or row.get("principal")).lower()
        if role_key:
            rows_by_role.setdefault(role_key, []).append(row)

    relations: list[dict[str, Any]] = []
    for role_key, actors in actors_by_role.items():
        role_rows = rows_by_role.get(role_key, [])
        if not role_rows:
            continue
        for operation in _list(model.get("operations")):
            if not isinstance(operation, dict):
                continue
            matching_rows = [
                row
                for row in role_rows
                if _resource_matches_operation(row.get("resource"), operation)
            ]
            if not matching_rows:
                continue
            row_decisions = {
                _permission_row_decision(row, operation)
                for row in matching_rows
            }
            explicit_decisions = row_decisions.intersection({"PERMIT", "DENY"})
            if len(explicit_decisions) > 1:
                permission_decision = "UNKNOWN"
                relation_type = "permission_unknown"
                relation_status = "conflicting"
            elif explicit_decisions:
                permission_decision = next(iter(explicit_decisions))
                relation_type = "permits" if permission_decision == "PERMIT" else "denies"
                relation_status = "accepted"
            elif closed_world:
                permission_decision = "DENY"
                relation_type = "denies"
                relation_status = "accepted"
            else:
                permission_decision = "UNKNOWN"
                relation_type = "permission_unknown"
                relation_status = "unknown"
            source_refs = [
                _source_ref(
                    _text(row.get("source_id")) or "permission_matrix",
                    locator=f"{role_key}->{_text(row.get('resource'))}",
                    kind="permission_matrix",
                )
                for row in matching_rows
            ]
            actions = sorted({
                _text(action)
                for row in matching_rows
                for action in _list(row.get("actions"))
                if _text(action)
            })
            for actor in actors:
                actor_ref = _text(actor.get("id"))
                operation_ref = _text(operation.get("id"))
                relations.append(_relation_node(
                    relation_type=relation_type,
                    from_ref=actor_ref,
                    to_ref=operation_ref,
                    operation_ref=operation_ref,
                    actor_ref=actor_ref,
                    effects=[{"allowed_actions": actions}],
                    source_refs=source_refs,
                    confidence=(
                        0.82
                        if permission_decision == "PERMIT"
                        else 0.8 if permission_decision == "DENY" else 0.55
                    ),
                    derivation=(
                        "schema-derived"
                        if closed_world and not explicit_decisions
                        else "explicit"
                    ),
                    status=relation_status,
                    permission_decision=permission_decision,
                ))
                owns_scope = permission_decision == "PERMIT" and any(
                    "own" in _text(row.get("scope")).lower()
                    for row in matching_rows
                )
                if owns_scope:
                    relations.append(_relation_node(
                        relation_type="owns",
                        from_ref=actor_ref,
                        to_ref=operation_ref,
                        operation_ref=operation_ref,
                        actor_ref=actor_ref,
                        preconditions=[{"scope": "own"}],
                        source_refs=source_refs,
                        confidence=0.78,
                        derivation="explicit",
                    ))
            if permission_decision == "UNKNOWN":
                gap_id = _stable_id("gap", "permission_unknown", role_key, operation.get("id"))
                model["coverage_gaps"].append(_fact_node(
                    node_id=gap_id,
                    typed_fields={
                        "gap_type": "permission_decision_unknown",
                        "description": "No explicit source fact permits or denies this role-operation pair",
                        "role_key": role_key,
                        "operation_ref": _text(operation.get("id")),
                    },
                    source_refs=source_refs,
                    confidence=1.0,
                    derivation="explicit",
                    status="unsupported" if relation_status != "conflicting" else "conflicting",
                ))
            if relation_status == "conflicting":
                model["conflicts"].append(_fact_node(
                    node_id=_stable_id("conflict", "permission", role_key, operation.get("id")),
                    typed_fields={
                        "conflict_type": "permission_decision_conflict",
                        "role_key": role_key,
                        "operation_ref": _text(operation.get("id")),
                        "decisions": sorted(explicit_decisions),
                    },
                    source_refs=source_refs,
                    confidence=1.0,
                    derivation="explicit",
                    status="conflicting",
                ))
    return relations


def _role_terms(actor: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for raw in (actor.get("role_key"), actor.get("role")):
        value = _text(raw).lower()
        if not value:
            continue
        variants = {
            value,
            value.replace("_", " "),
            value.replace("-", " "),
        }
        normalized = _normalize_action(value)
        if normalized.endswith("_role"):
            variants.add(normalized[:-5].replace("_", " "))
            variants.add(normalized[:-5])
        for variant in variants:
            cleaned = variant.strip(" _-")
            if len(cleaned) > 1 and cleaned not in terms:
                terms.append(cleaned)
    return terms


def _contains_role_term(source_text: str, term: str) -> bool:
    term = _text(term).lower()
    if not term:
        return False
    if not term.isascii():
        return term in source_text
    pieces = [piece for piece in re.split(r"[\s_\-]+", term) if piece]
    if not pieces:
        return False
    body = r"[\s/_:\-]+".join(re.escape(piece) for piece in pieces)
    return re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", source_text) is not None


def _operation_contract_text(operation: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("method", "path", "operation_id", "summary", "description"):
        value = _text(operation.get(field))
        if value:
            parts.append(value)
    parts.extend(_text(value) for value in _list(operation.get("tags")) if _text(value))
    return " ".join(parts).lower()


def _has_exclusive_role_marker(source_text: str) -> bool:
    return any(re.search(marker, source_text) for marker in _EXCLUSIVE_ROLE_MARKERS)


def _source_declared_allowed_roles(
    operation: dict[str, Any],
    actors_by_role: dict[str, list[dict[str, Any]]],
) -> set[str]:
    source_text = _operation_contract_text(operation)
    if not source_text or not _has_exclusive_role_marker(source_text):
        return set()
    allowed: set[str] = set()
    for role_key, actors in actors_by_role.items():
        if any(
            _contains_role_term(source_text, term)
            for actor in actors
            for term in _role_terms(actor)
        ):
            allowed.add(role_key)
    return allowed


def _permission_conflict_node(
    *,
    actor_ref: str,
    operation_ref: str,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    return _fact_node(
        node_id=_stable_id("conflict", "source_role_restriction", actor_ref, operation_ref),
        typed_fields={
            "conflict_type": "permission_decision_conflict",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "decisions": ["DENY", "PERMIT"],
        },
        source_refs=source_refs,
        confidence=1.0,
        derivation="explicit",
        status="conflicting",
    )


def _derive_source_role_restriction_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    actors_by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        role_key = _text(actor.get("role_key") or actor.get("role")).lower()
        if role_key:
            actors_by_role.setdefault(role_key, []).append(actor)
    if not actors_by_role:
        return []

    derived: list[dict[str, Any]] = []
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        operation_ref = _text(operation.get("id"))
        if not operation_ref:
            continue
        allowed_roles = _source_declared_allowed_roles(operation, actors_by_role)
        if not allowed_roles:
            continue
        source_text = _operation_contract_text(operation)
        source_refs = list(operation.get("source_refs") or [])
        source_refs.append(_source_ref(
            "operation_contract",
            locator=f"{_text(operation.get('method'))} {_text(operation.get('path'))}",
            quote=source_text[:200],
            kind="role_restriction",
        ))
        for role_key, actors in actors_by_role.items():
            permission_decision = "PERMIT" if role_key in allowed_roles else "DENY"
            relation_type = "permits" if permission_decision == "PERMIT" else "denies"
            opposite_type = "denies" if relation_type == "permits" else "permits"
            for actor in actors:
                actor_ref = _text(actor.get("id"))
                if not actor_ref:
                    continue
                existing = [
                    row
                    for row in [*model["relations"], *derived]
                    if _text(row.get("operation_ref")) == operation_ref
                    and _text(row.get("actor_ref") or row.get("from_ref")) == actor_ref
                ]
                opposite = [
                    row for row in existing
                    if _text(row.get("relation_type")) == opposite_type
                    and _text(row.get("status")) not in {"unsupported", "unknown"}
                ]
                same = [
                    row for row in existing
                    if _text(row.get("relation_type")) == relation_type
                    and _text(row.get("status")) not in {"unsupported", "unknown"}
                ]
                if opposite:
                    for row in opposite:
                        row["status"] = "conflicting"
                    relation_status = "conflicting"
                    model["conflicts"].append(_permission_conflict_node(
                        actor_ref=actor_ref,
                        operation_ref=operation_ref,
                        source_refs=source_refs,
                    ))
                elif same:
                    continue
                else:
                    relation_status = "accepted"
                derived.append(_relation_node(
                    relation_type=relation_type,
                    from_ref=actor_ref,
                    to_ref=operation_ref,
                    operation_ref=operation_ref,
                    actor_ref=actor_ref,
                    source_refs=source_refs,
                    confidence=0.84,
                    derivation="explicit",
                    status=relation_status,
                    permission_decision=permission_decision,
                ))
    return derived


def _derive_compensation_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    relations: list[dict[str, Any]] = []
    for create_operation in operations:
        if _text(create_operation.get("method")).upper() != "POST":
            continue
        create_shape = _path_shape(create_operation.get("path")).rstrip("/")
        if not create_shape or "{}" in create_shape:
            continue
        candidates: list[dict[str, Any]] = []
        for candidate in operations:
            candidate_method = _text(candidate.get("method")).upper()
            if candidate_method not in {"DELETE", "POST", "PATCH", "PUT"}:
                continue
            compensation_shape = _path_shape(candidate.get("path")).rstrip("/")
            segments = compensation_shape.split("/")
            if not segments or segments[-1] != "{}":
                action_collection = "/".join(segments[:-2]).rstrip("/") if len(segments) >= 3 else ""
                action_name = segments[-1] if segments else ""
                if (
                    candidate_method in {"POST", "PATCH", "PUT"}
                    and action_collection == create_shape
                    and _CLEANUP_ACTION_RE.search(action_name)
                ):
                    candidates.append(candidate)
                continue
            collection_shape = "/".join(segments[:-1]).rstrip("/")
            if candidate_method == "DELETE" and collection_shape == create_shape:
                candidates.append(candidate)
        if len(candidates) != 1:
            continue
        compensation = candidates[0]
        relations.append(_relation_node(
            relation_type="compensates",
            from_ref=_text(compensation.get("id")),
            to_ref=_text(create_operation.get("id")),
            operation_ref=_text(compensation.get("id")),
            effects=[{"cleanup_target_operation_ref": _text(create_operation.get("id"))}],
            source_refs=(
                list(compensation.get("source_refs") or [])
                + list(create_operation.get("source_refs") or [])
            )[:5],
            confidence=min(
                float(compensation.get("confidence") or 0.7),
                float(create_operation.get("confidence") or 0.7),
            ),
        ))
    return relations


def _derive_operation_entity_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    entities = [row for row in _list(model.get("entities")) if isinstance(row, dict)]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        aliases = [
            _text(entity.get("id")),
            _text(entity.get("name")),
            *_list(entity.get("source_entity_names")),
        ]
        for key in {
            normalized
            for value in aliases
            for normalized in (_text(value).lower(), _canonical_entity_name(value))
            if normalized
        }:
            if key:
                by_key.setdefault(key, []).append(entity)
    relation_type_by_method = {
        "GET": "observes",
        "HEAD": "observes",
        "OPTIONS": "observes",
        "POST": "produces",
        "DELETE": "consumes",
        "PUT": "transitions",
        "PATCH": "transitions",
    }
    relations: list[dict[str, Any]] = []
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        resolved_entities: list[tuple[dict[str, Any], str]] = []
        for entity_hint in _list(operation.get("entity_refs")):
            matches = list(dict.fromkeys(
                id(row)
                for key in (
                    _text(entity_hint).lower(),
                    _canonical_entity_name(entity_hint),
                )
                for row in by_key.get(key, [])
            ))
            if len(matches) == 1:
                entity = next(row for row in entities if id(row) == matches[0])
                resolved_entities.append((entity, "explicit"))
        if not resolved_entities:
            structural = _operation_structural_entity(operation, entities)
            if structural is not None:
                resolved_entities.append((structural, "schema-derived"))
        for entity, derivation in resolved_entities:
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type=relation_type_by_method.get(
                    _text(operation.get("method")).upper(),
                    "observes",
                ),
                from_ref=operation_ref,
                to_ref=_text(entity.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    list(operation.get("source_refs") or [])
                    + list(entity.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(operation.get("confidence") or 0.7),
                    float(entity.get("confidence") or 0.7),
                ),
                derivation=derivation,
            ))
    return relations


def _resolve_operation(
    operations: list[dict[str, Any]],
    relation_source: dict[str, Any],
) -> dict[str, Any] | None:
    operation_hint = _text(
        relation_source.get("operation_ref")
        or relation_source.get("operation_id")
        or relation_source.get("operation")
    )
    method_hint = _text(relation_source.get("method")).upper()
    path_hint = _text(relation_source.get("path"))
    candidates = operations
    if operation_hint:
        candidates = [
            row
            for row in candidates
            if operation_hint in {_text(row.get("id")), _text(row.get("operation_id"))}
        ]
    if method_hint:
        candidates = [row for row in candidates if _text(row.get("method")).upper() == method_hint]
    if path_hint:
        candidates = [row for row in candidates if _path_shape(row.get("path")) == _path_shape(path_hint)]
    return candidates[0] if len(candidates) == 1 else None


def _derive_state_transition_relations(
    model: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    states_by_key = {
        (_text(row.get("entity_ref")).lower(), _text(row.get("name")).lower()): row
        for row in _list(model.get("states"))
        if isinstance(row, dict)
    }
    relations: list[dict[str, Any]] = []
    for machine in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(machine, dict):
            continue
        entity = _text(machine.get("entity") or machine.get("object") or "entity").lower()
        for transition in _list(machine.get("transitions")):
            if not isinstance(transition, dict):
                continue
            from_state = states_by_key.get((entity, _text(transition.get("from") or transition.get("from_state")).lower()))
            to_state = states_by_key.get((entity, _text(transition.get("to") or transition.get("to_state")).lower()))
            operation_binding = any(
                _text(transition.get(key))
                for key in ("operation_ref", "operation_id", "operation", "method", "path")
            )
            operation = _resolve_operation(operations, transition) if operation_binding else None
            from_name = _text(transition.get("from") or transition.get("from_state"))
            to_name = _text(transition.get("to") or transition.get("to_state"))
            if not from_state or not to_state or not operation:
                if from_state and to_state and not operation:
                    model["coverage_gaps"].append(_fact_node(
                        node_id=_stable_id(
                            "gap",
                            "state_transition_operation_unresolved",
                            entity,
                            from_name,
                            to_name,
                        ),
                        typed_fields={
                            "gap_type": "state_transition_operation_unresolved",
                            "entity_ref": entity,
                            "from_state": from_name,
                            "to_state": to_name,
                            "operation_hint": _text(
                                transition.get("operation_ref")
                                or transition.get("operation_id")
                                or transition.get("operation")
                            ),
                            "description": "State transition has no unique source-bound operation",
                        },
                        source_refs=[_source_ref(
                            _text(transition.get("source_id")) or "state_machine",
                            locator=f"{entity}:{from_name}->{to_name}",
                            kind="state_transition",
                        )],
                        confidence=1.0,
                        derivation="explicit",
                        status="unsupported",
                    ))
                continue
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type="transitions",
                from_ref=_text(from_state.get("id")),
                to_ref=_text(to_state.get("id")),
                operation_ref=operation_ref,
                preconditions=_list(transition.get("preconditions")),
                effects=_list(transition.get("effects")),
                source_refs=[_source_ref(
                    _text(transition.get("source_id")) or "state_machine",
                    locator=f"{entity}:{_text(from_state.get('name'))}->{_text(to_state.get('name'))}",
                    kind="state_transition",
                )],
                confidence=float(transition.get("confidence") or 0.8),
                derivation="explicit",
            ))
    return relations


def _invariant_relation_type(invariant: dict[str, Any]) -> str:
    kind = _text(_dict(invariant.get("expression")).get("kind")).lower()
    return "conserves" if any(
        token in kind for token in ("conserv", "balance", "amount", "quantity")
    ) else "observes"


_TOKEN_OVERLAP_RELATION_GATE = "token_overlap_only_requires_explicit_source_relation"
_NON_AUTHORITATIVE_SOURCE_RELATION_STATUSES = {
    "candidate",
    "proposed",
    "unknown",
    "unsupported",
    "rejected",
}


def _source_relationship_candidate_reason(edge: dict[str, Any]) -> str:
    status = _text(edge.get("status")).lower()
    evidence_gate = _text(edge.get("evidence_gate"))
    derivation = _text(edge.get("derivation")).lower().replace("-", "_")
    evidence = _dict(edge.get("evidence"))

    if evidence_gate == _TOKEN_OVERLAP_RELATION_GATE:
        return evidence_gate
    if derivation == "token_overlap":
        return _TOKEN_OVERLAP_RELATION_GATE
    if evidence and set(evidence) <= {"token_overlap"}:
        return _TOKEN_OVERLAP_RELATION_GATE
    if status in _NON_AUTHORITATIVE_SOURCE_RELATION_STATUSES:
        return evidence_gate or status
    return ""


def _derive_source_relationship_relations(
    model: dict[str, Any],
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve typed knowledge-asset edges by exact source identifiers only."""

    invariants_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for invariant in _list(model.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        aliases = [_text(invariant.get("id")), *_list(invariant.get("source_rule_refs"))]
        for alias in {_text(value) for value in aliases if _text(value)}:
            invariants_by_source_ref.setdefault(alias, []).append(invariant)

    operations_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for operation in _list(model.get("operations")):
        if not isinstance(operation, dict):
            continue
        aliases = [_text(operation.get("id")), *_list(operation.get("source_operation_refs"))]
        for alias in {_text(value) for value in aliases if _text(value)}:
            operations_by_source_ref.setdefault(alias, []).append(operation)

    relations: list[dict[str, Any]] = []
    for edge in _list(data.get("relationships")):
        if not isinstance(edge, dict):
            continue
        relationship_type = _text(edge.get("relation") or edge.get("relation_type")).lower()
        if relationship_type != "rule_to_interface":
            continue
        relationship_id = _text(edge.get("edge_id") or edge.get("id")) or _stable_id(
            "source_relationship",
            relationship_type,
            edge.get("from") or edge.get("from_ref"),
            edge.get("to") or edge.get("to_ref"),
        )
        source_rule_ref = _text(edge.get("from") or edge.get("from_ref"))
        source_operation_ref = _text(edge.get("to") or edge.get("to_ref"))
        invariant_matches = invariants_by_source_ref.get(source_rule_ref, [])
        operation_matches = operations_by_source_ref.get(source_operation_ref, [])
        edge_source_ref = _source_ref(
            _text(edge.get("source_id")) or "knowledge_relationships",
            locator=relationship_id,
            kind=relationship_type,
        )
        candidate_reason = _source_relationship_candidate_reason(edge)
        if candidate_reason:
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "source_relationship_candidate_only", relationship_id),
                typed_fields={
                    "gap_type": "source_relationship_candidate_only",
                    "description": "A source relationship is candidate-only and lacks explicit source evidence for an executable semantic join",
                    "relationship_id": relationship_id,
                    "relationship_type": relationship_type,
                    "source_rule_ref": source_rule_ref,
                    "source_operation_ref": source_operation_ref,
                    "candidate_reason": candidate_reason,
                    "invariant_match_count": len(invariant_matches),
                    "operation_match_count": len(operation_matches),
                },
                source_refs=[edge_source_ref],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue
        if len(invariant_matches) != 1 or len(operation_matches) != 1:
            model["coverage_gaps"].append(_fact_node(
                node_id=_stable_id("gap", "source_relationship_unresolved", relationship_id),
                typed_fields={
                    "gap_type": "source_relationship_unresolved",
                    "description": "A typed source relationship could not be joined to exactly one IR rule and operation",
                    "relationship_id": relationship_id,
                    "relationship_type": relationship_type,
                    "source_rule_ref": source_rule_ref,
                    "source_operation_ref": source_operation_ref,
                    "invariant_match_count": len(invariant_matches),
                    "operation_match_count": len(operation_matches),
                },
                source_refs=[edge_source_ref],
                confidence=1.0,
                derivation="explicit",
                status="unsupported",
            ))
            continue

        invariant = invariant_matches[0]
        operation = operation_matches[0]
        operation_ref = _text(operation.get("id"))
        relations.append(_relation_node(
            relation_type=_invariant_relation_type(invariant),
            from_ref=operation_ref,
            to_ref=_text(invariant.get("id")),
            operation_ref=operation_ref,
            source_refs=(
                [edge_source_ref]
                + list(operation.get("source_refs") or [])
                + list(invariant.get("source_refs") or [])
            )[:5],
            confidence=min(
                float(edge.get("confidence") or 0.7),
                float(operation.get("confidence") or 0.7),
                float(invariant.get("confidence") or 0.7),
            ),
            derivation="schema-derived",
            source_relationship_ref=relationship_id,
        ))
    return relations


def _derive_invariant_relations(model: dict[str, Any]) -> list[dict[str, Any]]:
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    relations: list[dict[str, Any]] = []
    for invariant in _list(model.get("invariants")):
        if not isinstance(invariant, dict):
            continue
        relation_type = _invariant_relation_type(invariant)
        op_refs = _list(invariant.get("operation_refs"))

        # Fallback: when no explicit operation_refs, match by token overlap
        # with operation paths and field dictionaries.
        if not op_refs:
            import re as _re
            # Tokenize both CJK characters and ASCII words
            def _tokens(text: str) -> set[str]:
                cjk = set(_re.findall(r'[\u4e00-\u9fff]{1,2}', text.lower()))
                ascii_words = set(_re.findall(r'[a-z0-9_]+', text.lower()))
                return cjk | ascii_words
            inv_tokens = _tokens(_text(invariant.get("description") or ""))
            if inv_tokens:
                # Prefer write operations for better protocol compatibility
                _write_matches = []
                _read_matches = []
                for op in operations:
                    op_path = _text(op.get("path") or op.get("raw_path") or "").lower()
                    op_summary = _text(op.get("summary") or op.get("description") or "").lower()
                    op_fields = " ".join(
                        _text(f.get("field") or f.get("name") or "") if isinstance(f, dict) else _text(f)
                        for f in _list(op.get("field_dictionary"))
                    ).lower()
                    op_text = f"{op_path} {op_summary} {op_fields}"
                    op_tokens = _tokens(op_text)
                    overlap = len(inv_tokens & op_tokens)
                    if overlap >= 1:
                        is_write = _text(op.get("read_write") or op.get("side_effect_class")) == "write"
                        (_write_matches if is_write else _read_matches).append(_text(op.get("id")))
                # Prefer write ops, fall back to read ops
                op_refs.extend((_write_matches or _read_matches)[:5])
            # Write back to the invariant so downstream consumers see the match
            if op_refs:
                invariant["operation_refs"] = list(op_refs)

        for hint in op_refs:
            operation = _resolve_operation(operations, {"operation_ref": hint})
            if not operation:
                continue
            operation_ref = _text(operation.get("id"))
            relations.append(_relation_node(
                relation_type=relation_type,
                from_ref=operation_ref,
                to_ref=_text(invariant.get("id")),
                operation_ref=operation_ref,
                source_refs=(
                    list(operation.get("source_refs") or [])
                    + list(invariant.get("source_refs") or [])
                )[:5],
                confidence=min(
                    float(operation.get("confidence") or 0.7),
                    float(invariant.get("confidence") or 0.7),
                ),
                derivation="explicit",
            ))
    return relations


def empty_behavior_ir(*, project_id: str = "", source_snapshot_hash: str = "") -> dict[str, Any]:
    model = {
        "schema_version": SCHEMA_VERSION,
        "project_id": _text(project_id) or "opaque-project-id",
        "source_snapshot_hash": _text(source_snapshot_hash),
        "sources": [],
        "entities": [],
        "operations": [],
        "actors": [],
        "states": [],
        "relations": [],
        "invariants": [],
        "observation_surfaces": [],
        "capabilities": [],
        "conflicts": [],
        "coverage_gaps": [],
    }
    model["model_id"] = _content_addressed_id(model)
    return model


def _content_addressed_id(model: dict[str, Any]) -> str:
    payload = {k: v for k, v in model.items() if k != "model_id"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"bir_model_{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:24]}"


def validate_behavior_ir(
    model: dict[str, Any],
    *,
    require_explicit_relations: bool = True,
) -> list[str]:
    errors: list[str] = []
    if _text(model.get("schema_version")) != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    for collection in (
        "sources", "entities", "operations", "actors", "states", "relations",
        "invariants", "observation_surfaces", "capabilities", "conflicts", "coverage_gaps",
    ):
        if not isinstance(model.get(collection), list):
            errors.append(f"missing_collection:{collection}")
            continue
        seen_ids: set[str] = set()
        for item in model[collection]:
            if not isinstance(item, dict) or not _text(item.get("id")):
                errors.append(f"invalid_node:{collection}")
                continue
            item_id = _text(item.get("id"))
            if item_id in seen_ids:
                errors.append(f"duplicate_node_id:{collection}:{item_id}")
            seen_ids.add(item_id)
            if _text(item.get("derivation")) and _text(item.get("derivation")) not in _DERIVATIONS:
                errors.append(f"bad_derivation:{item.get('id')}")
            if _text(item.get("status")) and _text(item.get("status")) not in _STATUSES:
                errors.append(f"bad_status:{item.get('id')}")
            if "ground_truth" in json.dumps(item, ensure_ascii=False, default=str).lower():
                errors.append(f"forbidden_ground_truth_ref:{item.get('id')}")
            if collection == "relations":
                try:
                    normalize_relation(item)
                except BehaviorIRError as exc:
                    errors.append(str(exc))
                    continue
                if require_explicit_relations:
                    for field in (
                        "operation_ref",
                        "actor_ref",
                        "preconditions",
                        "effects",
                        "source_refs",
                    ):
                        if field not in item:
                            errors.append(f"relation_field_missing:{field}:{item.get('id')}")
    return errors


def migrate_behavior_ir_v1_to_v2(value: dict[str, Any]) -> dict[str, Any]:
    """Explicitly migrate a persisted V1 diagnostic artifact to V2 shape."""

    if not isinstance(value, dict) or _text(value.get("schema_version")) != V1_SCHEMA_VERSION:
        raise BehaviorIRError("behavior_ir_v1_required")
    migrated = deepcopy(value)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["relations"] = [normalize_relation(row) for row in _list(value.get("relations"))]
    errors = validate_behavior_ir(migrated, require_explicit_relations=True)
    if errors:
        raise BehaviorIRError("behavior_ir_v2_invalid:" + ",".join(errors))
    migrated["model_id"] = _content_addressed_id(migrated)
    return migrated


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build Behavior IR from enterprise knowledge asset + optional OpenAPI ops.

    Fully generic: binds only to structured fields present in the asset.
    """
    model = empty_behavior_ir(project_id=project_id, source_snapshot_hash=source_snapshot_hash)
    data = _dict(asset)
    if not data and not api_operations and not runtime_actors:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_sources"),
            typed_fields={"gap_type": "missing_sources", "description": "No knowledge asset or operations available"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))
        model["model_id"] = _content_addressed_id(model)
        return model

    # Sources
    for src in _list(data.get("sources") or data.get("source_inventory")):
        if not isinstance(src, dict):
            continue
        sid = _text(src.get("source_id") or src.get("id")) or _stable_id("src", src.get("filename"))
        model["sources"].append(_fact_node(
            node_id=sid if sid.startswith("bir_") else _stable_id("src", sid),
            typed_fields={
                "name": _text(src.get("filename") or src.get("original_name") or src.get("name") or sid),
                "source_type": _text(src.get("source_type") or src.get("type")),
                "hash": _text(src.get("text_hash") or src.get("content_hash") or src.get("hash")),
            },
            source_refs=[_source_ref(sid)],
            confidence=0.9,
            derivation="explicit",
        ))

    # Operations from OpenAPI / asset interfaces
    operations_by_transport_identity: dict[tuple[str, str, str], dict[str, Any]] = {}

    def merge_unique(existing: list[Any], incoming: list[Any]) -> list[Any]:
        return _merge_unique_sorted(existing, incoming)

    for op in list(api_operations or []) + _list(data.get("operations") or data.get("interfaces")):
        if not isinstance(op, dict):
            continue
        method = _text(op.get("method") or op.get("http_method") or "GET").upper()
        path = _text(op.get("path") or op.get("endpoint") or op.get("url"))
        if not path:
            continue
        service = _text(op.get("service") or op.get("service_name") or op.get("server"))
        op_id = _text(op.get("operation_id") or op.get("operationId") or op.get("id")) or _stable_id("op", method, path)
        side_effect = "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
        field_dictionary = _merge_unique_sorted(
            _list(op.get("field_dictionary")),
        )
        request_schema = _request_schema_for_operation(op)
        request_example = _operation_request_example(op)
        source_operation_refs = _merge_unique_sorted([
            _text(value)
            for value in (
                op.get("interface_id"),
                op.get("operation_id"),
                op.get("operationId"),
                op.get("id"),
            )
            if _text(value)
        ])
        operation = _fact_node(
            node_id=op_id if op_id.startswith("bir_") else _stable_id("op", service, method, path),
            typed_fields={
                "operation_id": op_id,
                "service": service,
                "method": method,
                "path": path,
                "request_schema": request_schema,
                "request_example": request_example,
                "response_schema": _dict(op.get("response_schema") or op.get("responses")),
                "parameters": _merge_unique_sorted(
                    _list(op.get("parameters")),
                    field_dictionary,
                ),
                "field_dictionary": field_dictionary,
                "security": _list(op.get("security")),
                "summary": _text(op.get("summary") or op.get("title")),
                "description": _text(op.get("description")),
                "tags": _list(op.get("tags")),
                "side_effect_class": _text(op.get("side_effect_class") or side_effect),
                "read_write": side_effect,
                "entity_refs": [_text(x) for x in _list(op.get("entity_refs")) if _text(x)],
                "examples": _list(op.get("examples")),
                "source_operation_refs": source_operation_refs,
            },
            source_refs=[_source_ref(_text(op.get("source_id")) or "api_spec", locator=f"{method} {path}", kind="api_operation")],
            confidence=0.85 if op.get("operation_id") else 0.7,
            derivation="schema-derived" if not op.get("operation_id") else "explicit",
        )
        transport_identity = (service, method, _path_shape(path))
        existing = operations_by_transport_identity.get(transport_identity)
        if existing is None:
            operations_by_transport_identity[transport_identity] = operation
            model["operations"].append(operation)
            continue

        existing["source_refs"] = merge_unique(
            _list(existing.get("source_refs")),
            _list(operation.get("source_refs")),
        )
        existing["source_operation_refs"] = merge_unique(
            _list(existing.get("source_operation_refs")),
            _list(operation.get("source_operation_refs")),
        )
        for field in (
            "parameters",
            "field_dictionary",
            "security",
            "tags",
            "entity_refs",
            "examples",
        ):
            existing[field] = merge_unique(
                _list(existing.get(field)),
                _list(operation.get(field)),
            )
        for field in ("request_schema", "response_schema"):
            prior = _dict(existing.get(field))
            incoming = _dict(operation.get(field))
            if not prior and incoming:
                existing[field] = incoming
            elif prior and incoming and prior != incoming:
                existing[field] = _merge_schema_dicts(prior, incoming)
                conflict_paths = _schema_conflict_paths(prior, incoming)
                if not conflict_paths:
                    continue
                conflict_id = _stable_id(
                    "conflict",
                    "operation_schema",
                    method,
                    transport_identity[1],
                    field,
                )
                model["conflicts"].append(_fact_node(
                    node_id=conflict_id,
                    typed_fields={
                        "conflict_type": "operation_schema_conflict",
                        "operation_ref": _text(existing.get("id")),
                        "field": field,
                        "method": method,
                        "path_shape": transport_identity[2],
                        "conflict_paths": conflict_paths,
                    },
                    source_refs=merge_unique(
                        _list(existing.get("source_refs")),
                        _list(operation.get("source_refs")),
                    ),
                    confidence=1.0,
                    derivation="explicit",
                    status="conflicting",
                ))
        if _dict(operation.get("request_example")):
            existing["request_example"] = (
                existing.get("request_example")
                if _dict(existing.get("request_example"))
                else operation.get("request_example")
            )
        for field in ("summary", "description"):
            existing[field] = _best_text(existing.get(field), operation.get(field))
        existing["operation_id"] = _canonical_operation_id(
            _list(existing.get("source_operation_refs")),
            _text(existing.get("operation_id")),
        )
        existing["confidence"] = max(
            float(existing.get("confidence") or 0.0),
            float(operation.get("confidence") or 0.0),
        )
        if operation.get("derivation") == "explicit":
            existing["derivation"] = "explicit"

    _canonicalize_duplicate_operation_ids(model)

    # ── Inherit request examples from sibling POST operations ───────────
    # Many API docs omit request bodies for some POST endpoints (especially
    # admin/internal ones). When an operation has no request_example, copy
    # from the nearest sibling POST operation sharing the same service
    # prefix (first 3 path segments: /api/{service}/...).
    # ───────────────────────────────────────────────────────────────────
    import re as _re
    _PARAM_RE = _re.compile(r"\{[^}]+\}")
    def _norm_path(p: str) -> str:
        return _PARAM_RE.sub(":p", str(p or ""))
    def _svc_prefix(p: str) -> str:
        segs = [s for s in _norm_path(p).strip("/").split("/") if s]
        return "/".join(segs[:2]) if len(segs) >= 2 else _norm_path(p)
    _ops = model.get("operations") or []
    if isinstance(_ops, list):
        for _op in _ops:
            if not isinstance(_op, dict):
                continue
            if _dict(_op.get("request_example")):
                continue
            _op_svc = _svc_prefix(_text(_op.get("path") or _op.get("raw_path")))
            for _sib in _ops:
                if not isinstance(_sib, dict) or _sib is _op:
                    continue
                if _text(_sib.get("method")).upper() != "POST":
                    continue
                _sib_example = _dict(_sib.get("request_example"))
                if not _sib_example:
                    continue
                _sib_svc = _svc_prefix(_text(_sib.get("path") or _sib.get("raw_path")))
                if _sib_svc == _op_svc:
                    _op["request_example"] = dict(_sib_example)
                    break

    # ── Synthetic request bodies for undocumented POST endpoints ──────
    # When a POST endpoint has no documented request_example and no sibling
    # to inherit from, the Behavior IR leaves request_example unset rather
    # than fabricating industry-specific test data. The experiment compiler
    # handles missing bodies through path-parameter inference and generic
    # field placeholders derived from declared schemas.
    # ───────────────────────────────────────────────────────────────────
    # shorter objects/entities/tables aliases; merge them instead of choosing
    # only the first non-empty collection.
    entity_rows: list[Any] = []
    for key in ("objects", "entities", "tables", "business_objects", "data_tables"):
        entity_rows.extend(_list(data.get(key)))
    entities_by_canonical_name: dict[str, dict[str, Any]] = {}
    for ent in entity_rows:
        if isinstance(ent, str):
            name = _text(ent)
            canonical_name = _canonical_entity_name(name)
            if not name or not canonical_name:
                continue
            existing = entities_by_canonical_name.get(canonical_name)
            if existing is not None:
                existing["source_entity_names"] = merge_unique(
                    _list(existing.get("source_entity_names")),
                    [name],
                )
                continue
            entity = _fact_node(
                node_id=_stable_id("ent", name),
                typed_fields={
                    "name": name,
                    "kind": "resource",
                    "entity_kinds": ["resource"],
                    "source_entity_names": [name],
                },
                confidence=0.6,
                derivation="schema-derived",
            )
            entities_by_canonical_name[canonical_name] = entity
            model["entities"].append(entity)
            continue
        if not isinstance(ent, dict):
            continue
        name = _text(ent.get("name") or ent.get("object") or ent.get("table") or ent.get("entity"))
        canonical_name = _canonical_entity_name(name)
        if not name or not canonical_name:
            continue
        kind = _text(ent.get("kind") or "resource")
        source_refs = [_source_ref(_text(ent.get("source_id")), locator=name)]
        existing = entities_by_canonical_name.get(canonical_name)
        if existing is not None:
            existing["source_entity_names"] = merge_unique(
                _list(existing.get("source_entity_names")),
                [name],
            )
            existing["entity_kinds"] = merge_unique(
                _list(existing.get("entity_kinds")),
                [kind],
            )
            existing["fields"] = merge_unique(
                _list(existing.get("fields")),
                _list(ent.get("fields") or ent.get("columns")),
            )
            existing["source_refs"] = merge_unique(
                _list(existing.get("source_refs")),
                source_refs,
            )
            existing["confidence"] = max(
                float(existing.get("confidence") or 0.0),
                float(ent.get("confidence") or 0.7),
            )
            continue
        entity = _fact_node(
            node_id=_text(ent.get("entity_id") or ent.get("id")) or _stable_id("ent", name),
            typed_fields={
                "name": name,
                "kind": kind,
                "entity_kinds": [kind],
                "source_entity_names": [name],
                "fields": _list(ent.get("fields") or ent.get("columns")),
            },
            source_refs=source_refs,
            confidence=float(ent.get("confidence") or 0.7),
            derivation="explicit",
        )
        entities_by_canonical_name[canonical_name] = entity
        model["entities"].append(entity)

    # ── Infer entity_refs for operations without explicit entity mapping ──
    # Operations that lack entity_refs (e.g., report endpoints) can't
    # participate in authorization/isolation obligations. Infer entity
    # from path segments so the obligation compiler can generate tests.
    if isinstance(_ops, list):
        for _op in _ops:
            if not isinstance(_op, dict):
                continue
            if _list(_op.get("entity_refs")):
                continue
            _op_path = _norm_path(_text(_op.get("path") or _op.get("raw_path")))
            _segments = [s for s in _op_path.strip("/").split("/") if s and not s.startswith(":")]
            _skip = {"admin", "manual-success", "approve", "reject", "cancel", "confirm", "ship", "pay", "validate", "adjust", "consume", "release", "reserve", "reset", "use"}
            _entity = None
            for _seg in reversed(_segments):
                if _seg not in _skip:
                    _entity = _seg
                    break
            if _entity and _entity not in ("api", "v1", "v2", "v3"):
                _entity_id = f"bir_{hashlib.sha256(f'entity:{_entity}'.encode()).hexdigest()[:16]}"
                _op["entity_refs"] = [_entity_id]
                # Ensure the entity exists in the model
                _existing = {_text(e.get("id")): e for e in _list(model.get("entities")) if isinstance(e, dict)}
                if _entity_id not in _existing:
                    model.setdefault("entities", []).append({
                        "id": _entity_id,
                        "name": _entity,
                        "kind": "resource",
                        "source_refs": [{"source_id": "api_spec", "locator": _entity}],
                        "confidence": 0.6,
                        "derivation": "model-inferred",
                    })

    # Actors from permission matrix / roles / runtime actors (secret_ref only)
    actor_names: set[str] = set()
    actor_ids: set[str] = set()
    permission_rows = _list(data.get("permission_matrix") or data.get("permissions"))
    permission_by_role: dict[str, dict[str, Any]] = {}
    for perm in permission_rows:
        if not isinstance(perm, dict):
            continue
        role = _text(perm.get("role") or perm.get("actor") or perm.get("principal"))
        if not role:
            continue
        role_key = role.lower()
        aggregate = permission_by_role.setdefault(role_key, {
            "role": role,
            "resources": [],
            "actions": [],
            "denied_actions": [],
            "scopes": [],
            "source_ids": [],
        })
        declared_polarity = _declared_permission_polarity(perm)
        resource = _text(perm.get("resource"))
        if declared_polarity != "DENY" and resource and resource not in aggregate["resources"]:
            aggregate["resources"].append(resource)
        declared_actions = _list(perm.get("actions"))
        for action in declared_actions if declared_polarity != "DENY" else []:
            value = _text(action)
            if value and value not in aggregate["actions"]:
                aggregate["actions"].append(value)
        denied_actions = (
            declared_actions if declared_polarity == "DENY" else []
        ) + _list(
            perm.get("denied_actions")
            or perm.get("forbidden_actions")
            or perm.get("prohibited_actions")
        )
        for action in denied_actions:
            value = _text(action)
            if value and value not in aggregate["denied_actions"]:
                aggregate["denied_actions"].append(value)
        scope = _text(perm.get("scope"))
        if scope and scope not in aggregate["scopes"]:
            aggregate["scopes"].append(scope)
        source_id = _text(perm.get("source_id"))
        if source_id and source_id not in aggregate["source_ids"]:
            aggregate["source_ids"].append(source_id)
    for role_key, aggregate in permission_by_role.items():
        role = _text(aggregate.get("role") or role_key)
        actor_names.add(role_key)
        actor_id = _stable_id("actor", role)
        actor_ids.add(actor_id)
        model["actors"].append(_fact_node(
            node_id=actor_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "tenant_scope": ",".join(aggregate["scopes"]) or "unspecified",
                "credential_secret_ref": f"secret_ref:actor:{role}",
                "account_status": "active",
                "allowed_resources": aggregate["resources"],
                "allowed_actions": aggregate["actions"],
                "denied_actions": aggregate["denied_actions"],
            },
            source_refs=[
                _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
                for source_id in (aggregate["source_ids"] or [""])
            ],
            confidence=0.8,
            derivation="explicit",
        ))
    for declared_role in _list(data.get("roles")):
        if not isinstance(declared_role, dict):
            continue
        role = _text(declared_role.get("role") or declared_role.get("name") or declared_role.get("id"))
        role_key = role.lower()
        if not role or role_key in actor_names:
            continue
        actor_names.add(role_key)
        actor_id = _stable_id("actor", role)
        actor_ids.add(actor_id)
        model["actors"].append(_fact_node(
            node_id=actor_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "tenant_scope": _text(declared_role.get("scope") or "unspecified"),
                "credential_secret_ref": f"secret_ref:actor:{role}",
                "account_status": "active",
                "allowed_resources": [],
                "allowed_actions": [],
                "denied_actions": [],
            },
            source_refs=[_source_ref(_text(declared_role.get("source_id")) or "roles", locator=role, kind="role_catalog")],
            confidence=float(declared_role.get("confidence") or 0.75),
            derivation="explicit",
        ))
    for actor in _list(runtime_actors):
        if not isinstance(actor, dict):
            continue
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        if not role:
            continue
        role_key = role.lower()
        aggregate = permission_by_role.get(role_key, {})
        if role_key not in actor_names:
            actor_names.add(role_key)
            actor_id = _stable_id("actor", role)
            actor_ids.add(actor_id)
            source_refs = [
                _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
                for source_id in (aggregate.get("source_ids") or [])
            ]
            source_refs.append(_source_ref("runtime_actors", locator=role, kind="runtime_actor"))
            model["actors"].append(_fact_node(
                node_id=actor_id,
                typed_fields={
                    "role": role,
                    "role_key": role_key,
                    "tenant_scope": _text(actor.get("tenant") or actor.get("scope") or "unspecified"),
                    "credential_secret_ref": _text(actor.get("secret_ref") or f"secret_ref:actor:{role}"),
                    "account_status": _text(actor.get("status") or "active"),
                    "allowed_resources": list(aggregate.get("resources") or []),
                    "allowed_actions": list(aggregate.get("actions") or []),
                    "denied_actions": list(aggregate.get("denied_actions") or []),
                    "runtime_bound": True,
                },
                source_refs=source_refs,
                confidence=0.9,
                derivation="runtime-observed",
            ))
        account_ref = _text(actor.get("account_ref") or actor.get("email") or actor.get("username") or actor.get("id"))
        if not account_ref:
            continue
        account_id = _stable_id("actor_account", account_ref)
        if account_id in actor_ids:
            continue
        actor_ids.add(account_id)
        source_refs = [
            _source_ref(source_id or "permission_matrix", locator=role, kind="permission_matrix")
            for source_id in (aggregate.get("source_ids") or [])
        ]
        source_refs.append(_source_ref("runtime_actors", locator=f"{role}:{account_ref}", kind="runtime_actor"))
        model["actors"].append(_fact_node(
            node_id=account_id,
            typed_fields={
                "role": role,
                "role_key": role_key,
                "account_ref": account_ref,
                "tenant_scope": _text(actor.get("tenant") or actor.get("scope") or "unspecified"),
                "credential_secret_ref": _text(actor.get("secret_ref") or f"secret_ref:test_accounts:{account_ref}"),
                "account_status": _text(actor.get("status") or "active"),
                "allowed_resources": list(aggregate.get("resources") or []),
                "allowed_actions": list(aggregate.get("actions") or []),
                "denied_actions": list(aggregate.get("denied_actions") or []),
                "runtime_bound": True,
            },
            source_refs=source_refs,
            confidence=0.9,
            derivation="runtime-observed",
        ))

    # States from state machines
    for sm in _list(data.get("state_machines") or data.get("states")):
        if not isinstance(sm, dict):
            continue
        entity = _text(sm.get("entity") or sm.get("object") or "entity")
        for state_name in _list(sm.get("states") or ([sm.get("name")] if sm.get("name") else [])):
            name = _text(state_name)
            if not name:
                continue
            model["states"].append(_fact_node(
                node_id=_stable_id("state", entity, name),
                typed_fields={"entity_ref": entity, "name": name},
                source_refs=[_source_ref(_text(sm.get("source_id")) or "state_machine", locator=f"{entity}:{name}")],
                confidence=0.75,
                derivation="explicit",
            ))

    # Invariants from rule library (typed expression + description)
    for rule in _list(data.get("rule_library") or data.get("rules")):
        if not isinstance(rule, dict):
            continue
        statement = _text(rule.get("statement") or rule.get("expression") or rule.get("title"))
        if not statement:
            continue
        rid = _text(rule.get("rule_id") or rule.get("id")) or _stable_id("inv", statement)
        model["invariants"].append(_fact_node(
            node_id=rid if rid.startswith("bir_") else _stable_id("inv", rid),
            typed_fields={
                "description": statement,
                "expression": {
                    "kind": _text(rule.get("kind") or rule.get("risk_type") or "business_rule"),
                    "operator": _text(rule.get("operator") or "must_hold"),
                    "operands": _list(rule.get("operands")),
                    "raw": statement,
                },
                "operation_refs": [
                    _text(value)
                    for value in _list(
                        rule.get("operation_refs")
                        or ([rule.get("operation_ref") or rule.get("operation_id")]
                            if rule.get("operation_ref") or rule.get("operation_id") else [])
                    )
                    if _text(value)
                ],
                "source_rule_refs": list(dict.fromkeys(
                    _text(value)
                    for value in (rule.get("rule_id"), rule.get("id"))
                    if _text(value)
                )),
            },
            source_refs=[_source_ref(_text(rule.get("source_id")) or "rule_library", quote=statement[:200])],
            confidence=float(rule.get("confidence") or 0.7),
            derivation="explicit",
        ))

    # Runtime V2 relations are the only semantic joins used by the compiler.
    permission_policy_mode = _text(
        data.get("permission_policy_mode")
        or data.get("permission_matrix_mode")
    ).lower()
    permission_matrix_complete = bool(data.get("permission_matrix_complete")) or permission_policy_mode in {
        "closed_world",
        "complete",
    }
    model["relations"].extend(_derive_permission_relations(
        model,
        permission_rows,
        closed_world=permission_matrix_complete,
    ))
    model["relations"].extend(_derive_source_role_restriction_relations(model))
    model["relations"].extend(_derive_operation_entity_relations(model))
    model["relations"].extend(_derive_state_transition_relations(model, data))
    model["relations"].extend(_derive_source_relationship_relations(model, data))
    model["relations"].extend(_derive_invariant_relations(model))
    model["relations"].extend(_derive_compensation_relations(model))

    # Default observation surfaces based on available capabilities
    surfaces = [("http_api", "HTTP/API"), ("ui_browser", "Browser/UI"), ("db_snapshot", "DB read snapshot")]
    for surface_id, label in surfaces:
        model["observation_surfaces"].append(_fact_node(
            node_id=_stable_id("surface", surface_id),
            typed_fields={"surface": surface_id, "label": label, "available": surface_id == "http_api"},
            confidence=1.0 if surface_id == "http_api" else 0.3,
            derivation="schema-derived",
            status="accepted" if surface_id == "http_api" else "unknown",
        ))
    model["capabilities"].append(_fact_node(
        node_id=_stable_id("cap", "http_execute"),
        typed_fields={"capability": "http_execute", "adapter": "http_api"},
        confidence=1.0,
        derivation="schema-derived",
    ))

    if not model["operations"]:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_operations"),
            typed_fields={"gap_type": "missing_operations", "description": "No operations derived from sources"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))
    if not model["actors"]:
        model["coverage_gaps"].append(_fact_node(
            node_id=_stable_id("gap", "no_actors"),
            typed_fields={"gap_type": "missing_actors", "description": "No actors/roles derived from sources"},
            confidence=1.0,
            derivation="explicit",
            status="unsupported",
        ))

    model["relations"] = [normalize_relation(row) for row in _dedupe_nodes(model["relations"])]
    model["coverage_gaps"] = _dedupe_nodes(model["coverage_gaps"])
    model["conflicts"] = _dedupe_nodes(model["conflicts"])
    model["model_id"] = _content_addressed_id(model)
    return model


def behavior_ir_summary(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": _text(model.get("schema_version")),
        "model_id": _text(model.get("model_id")),
        "project_id": _text(model.get("project_id")),
        "counts": {
            key: len(_list(model.get(key)))
            for key in (
                "sources", "entities", "operations", "actors", "states",
                "relations", "invariants", "observation_surfaces", "capabilities",
                "conflicts", "coverage_gaps",
            )
        },
        "validation_errors": validate_behavior_ir(model),
    }
