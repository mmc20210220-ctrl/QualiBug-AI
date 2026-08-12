"""Compile Behavior IR into industry-agnostic Test Obligations."""
from __future__ import annotations

import hashlib
import re
from itertools import permutations
from typing import Any

from .behavior_ir import BehaviorIRError, SCHEMA_VERSION as BEHAVIOR_IR_SCHEMA, validate_behavior_ir
from .real_id_resolver import collection_path, normalize_path_placeholders
from .real_id_resolver_base import path_has_placeholders
from .test_obligation import RISK_FAMILIES, dedupe_obligations, make_obligation
from .validation_read_side_protocol import is_ownership_key


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _surface_contracts_for_screen(
    ui_specs: list[dict[str, Any]],
    screen_ref: str,
) -> list[dict[str, Any]]:
    """Surface-declared browser plans belonging to one screen identity.

    The UI surface declaration chain attaches compiled read-only Playwright
    contracts (``surface_contracts``) to each UI design spec. This helper
    resolves them by the same exact screen identity the URL uses, so the
    protocol compiles the document's own DOM assertions into the experiment
    instead of token-guessing the page. Contracts are carried verbatim —
    nothing is inferred here.
    """
    if not _text(screen_ref):
        return []
    for spec in ui_specs:
        spec_screen = _text(
            spec.get("screen")
            or spec.get("screen_id")
            or _text(spec.get("ui_spec_id") or "").rsplit(":", 1)[-1]
        )
        if screen_ref not in {spec_screen, _text(spec.get("name"))}:
            continue
        contracts = _list(spec.get("surface_contracts"))
        return [dict(row) for row in contracts if isinstance(row, dict)]
    return []


# P0-E phase-3: structured frame_type → risk family mapping (Chinese
# Semantic Frame SSOT schema types — industry-neutral structured evidence).
# When a rule's frame is grounded, its frame_type decides the obligation
# family; legacy CJK kind tokens (库存/金额/隐私/过期/可见/状态/因果/后置) are
# demoted to an observable fallback.
_FRAME_TYPE_FAMILY = {
    "TIME_WINDOW_CONSTRAINT": "temporal",
    "QUANTITY_CONSTRAINT": "conservation",
    "FORMULA_CONSTRAINT": "conservation",
    "VALIDATION_RULE": "validation",
    "UNIQUENESS_CONSTRAINT": "validation",
    "CARDINALITY_CONSTRAINT": "validation",
    "PERMISSION_RULE": "visibility",
    "OWNERSHIP_RULE": "visibility",
    "SCOPE_RULE": "visibility",
    "DATA_VISIBILITY_RULE": "visibility",
    "STATE_TRANSITION": "state",
    "COMPENSATION_RULE": "state",
    "PROCESS_ORDERING": "state",
}

_CJK_FAMILY_TOKENS = (
    "库存", "金额", "隐私", "过期", "可见", "状态", "因果", "后置",
)

_CJK_OWNERSHIP_LANGUAGE_MARKERS = (
    "自己的", "本人", "归属", "只能查询",
)


def _legacy_fallback_kind_counts(ir: dict[str, Any]) -> dict[str, int] | None:
    """P0-E: the legacy fallback receipt's kind_counts, but only when a frame
    ledger exists — without an SSOT, legacy CJK parsing is the plain legacy
    semantics, not a demotion worth counting. None disables counting."""
    receipt = _dict(ir.get("legacy_semantic_fallback_receipt"))
    if receipt.get("frame_ledger_present") is not True:
        return None
    counts = receipt.get("kind_counts")
    if not isinstance(counts, dict):
        counts = {}
        receipt["kind_counts"] = counts
    return counts


def _count_legacy_cjk_kind(
    counts: dict[str, int] | None,
    kind: str,
    amount: int = 1,
) -> None:
    if counts is None or amount <= 0:
        return
    counts[kind] = counts.get(kind, 0) + amount


def _postcondition_has_bound_effect(expression: dict[str, Any]) -> bool:
    """Return whether a postcondition names an observable field or create effect."""

    for operand in _list(_dict(expression).get("operands")):
        if not isinstance(operand, dict):
            continue
        if _text(operand.get("field_id") or operand.get("field")):
            return True
        expected = operand.get("expected_value")
        if expected is not None and (
            not isinstance(expected, str) or bool(expected.strip())
        ):
            return True
        if bool(operand.get("must_create")):
            return True
    return False


def _accepted(nodes: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if _text(node.get("status")) in {"conflicting", "unsupported"}:
            continue
        out.append(node)
    return out


def related_operations(
    behavior_ir: dict[str, Any],
    *,
    node_ref: str,
    relation_types: set[str],
) -> list[dict[str, Any]]:
    """Join a node to operations only through explicit Behavior IR relations."""

    relation_rows = _accepted(_list(_dict(behavior_ir).get("relations")))
    operation_ids = {
        _text(row.get("operation_ref"))
        for row in relation_rows
        if _text(row.get("relation_type")) in relation_types
        and _text(node_ref) in {_text(row.get("from_ref")), _text(row.get("to_ref"))}
        and _text(row.get("operation_ref"))
    }
    return [
        row
        for row in _accepted(_list(_dict(behavior_ir).get("operations")))
        if _text(row.get("id")) in operation_ids
    ]


def _relations_for_operation(
    relations: list[dict[str, Any]],
    operation_ref: str,
    relation_types: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in relations
        if _text(row.get("operation_ref")) == _text(operation_ref)
        and _text(row.get("relation_type")) in relation_types
    ]


def _relation_actor_ref(relation: dict[str, Any]) -> str:
    return _text(relation.get("actor_ref") or relation.get("from_ref"))


_OWNERSHIP_LANGUAGE_MARKERS = (
    "自己的",
    "自己",
    "本人",
    "归属",
    "own",
    "owner",
    "cross-user",
    "只能查询",
)

# Restrictive own-scope modals (只能/仅限/仅允许/only/must) combined with an
# ownership modal (自己的/本人/own/self) on a FIELD's own description mark it
# as caller-scoped identity input even when its name does not end with a
# generic owner key (e.g. a role-scoped ``sellerId`` documented as 只能以
# 自己作为 sellerId). Vocabulary is modality, never an industry term.
_OWNERSHIP_RESTRICTIVE_MODALS = ("只能", "仅限", "仅允许", "only", "must")


def _ownership_param_description_declares_scope(description: str) -> bool:
    raw = _text(description)
    if not raw:
        return False
    has_restrictive = any(m in raw for m in _OWNERSHIP_RESTRICTIVE_MODALS)
    has_modal = any(m in raw for m in _OWNERSHIP_LANGUAGE_MARKERS)
    return has_restrictive and has_modal


def _param_key(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", _text(name).lower())


def _is_ownership_param_name(name: str) -> bool:
    # Ownership-key vocabulary is a single SSOT: the read-side protocol's
    # suffix matcher (fromUserId/toUserId/ownerId/… all end with an owner
    # key). An exact-set membership test here would silently miss every
    # scoped body field and fall back to a corpus-wide phantom binder.
    return is_ownership_key(name)


def _path_has_resource_placeholder(path: str) -> bool:
    normalized = normalize_path_placeholders(_text(path))
    return "{" in normalized or "/:" in _text(path)


def _entity_state_field(entities: list[dict[str, Any]], entity_ref: str) -> str:
    """Resolve the entity's state field name from its declared fields.

    The IR marks the field whose values the machine governs with
    ``semantic_type == "STATE"`` or the generic lifecycle field names
    (status/state/stage/phase/lifecycle_state / *_status / *_state). The
    resolved name rides on the obligation so the state-precondition freeze
    can bind one authoritative state field to every establishment step.
    Entity identity is tolerant of the plural/singular forms the different
    IR builders emit (``orders`` vs ``order``); unrelated prefixed entities
    (``order_items``) never match.
    """
    target = _text(entity_ref).lower()
    if not target:
        return ""
    for entity in entities:
        name = _text(entity.get("name") or entity.get("id")).lower()
        if not (name == target or name == target + "s" or target == name + "s"):
            continue
        for field in _list(entity.get("fields")):
            if not isinstance(field, dict):
                continue
            fname = _text(field.get("name") or field.get("field") or field.get("field_name")).lower()
            is_state_field = (
                _text(field.get("semantic_type")) == "STATE"
                or fname in {"status", "state", "stage", "phase", "lifecycle_state"}
                or fname.endswith("_status")
                or fname.endswith("_state")
            )
            if is_state_field:
                return fname
    return ""


def _ownership_params_declared_on_operation(operation: dict[str, Any]) -> list[str]:
    """Return ownership identity params declared on one operation."""

    found: list[str] = []
    seen: set[str] = set()
    # Operation-level own-scope declaration (只能以自己作为 sellerId / 普通
    # 用户只能使用自己的 ID): when the operation itself declares a caller
    # scope, an identity-shaped body field (name ending in Id/ID, description
    # carrying an ID marker) is ownership input even when its name does not
    # end with a generic owner key (sellerId, memberId, …). Without the
    # operation-level declaration the field stays an ordinary id.
    _op_scope_declared = _operation_declares_ownership_language(operation)
    _id_field_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[Ii][Dd]|_id|_ID)$")

    def _add(name: str, description: str = "") -> None:
        text = _text(name)
        key = _param_key(text)
        if not text or key in seen:
            return
        if not (
            _is_ownership_param_name(text)
            or _ownership_param_description_declares_scope(description)
            or (
                _op_scope_declared
                and bool(_id_field_pattern.match(text))
                and re.search(r"(?:ID|Id|id)", _text(description))
            )
        ):
            return
        seen.add(key)
        found.append(text)

    def _walk_properties(properties: dict[str, Any], *, depth: int = 0) -> None:
        if depth > 8 or not isinstance(properties, dict):
            return
        for field_name, field_schema in properties.items():
            _add(str(field_name), _text(_dict(field_schema).get("description")))
            nested = _dict(field_schema)
            _walk_properties(_dict(nested.get("properties")), depth=depth + 1)
            items = _dict(nested.get("items"))
            if items:
                _walk_properties(_dict(items.get("properties")), depth=depth + 1)

    def _walk_example(value: Any, *, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for field_name, nested in value.items():
                _add(str(field_name))
                _walk_example(nested, depth=depth + 1)
        elif isinstance(value, list) and value:
            _walk_example(value[0], depth=depth + 1)

    for tag in _list(operation.get("tags")):
        _add(_text(tag))
    for parameter in _list(operation.get("parameters")):
        if isinstance(parameter, dict):
            _add(_text(parameter.get("name")))
        else:
            _add(parameter)
    corpus = " ".join((
        _text(operation.get("summary")),
        _text(operation.get("description")),
    ))
    for match in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", corpus):
        _add(match)
    for match in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", corpus):
        if _is_ownership_param_name(match):
            _add(match)
    schema = _dict(operation.get("request_schema"))
    content = _dict(schema.get("content"))
    if _dict(schema.get("properties")):
        _walk_properties(_dict(schema.get("properties")))
    for media in content.values():
        properties = _dict(_dict(_dict(media).get("schema")).get("properties"))
        _walk_properties(properties)
    _walk_example(_dict(operation.get("request_example")))
    return found


def _ownership_binder_location(
    operation: dict[str, Any],
    *,
    name: str,
) -> str:
    """Prefer the documented parameter location; fall back by HTTP method."""

    for parameter in _list(operation.get("parameters")):
        if not isinstance(parameter, dict):
            continue
        if _param_key(_text(parameter.get("name"))) != _param_key(name):
            continue
        location = _text(parameter.get("in") or parameter.get("location")).lower()
        if location in {"query", "path", "header", "body"}:
            return location
    method = _text(operation.get("method")).upper()
    return "body" if method in {"POST", "PUT", "PATCH"} else "query"


def _operation_declares_ownership_language(operation: dict[str, Any]) -> bool:
    corpus = " ".join((
        _text(operation.get("summary")),
        _text(operation.get("description")),
    ))
    lowered = corpus.lower()
    return any(
        marker in corpus or marker in lowered
        for marker in _OWNERSHIP_LANGUAGE_MARKERS
    )


def _corpus_ownership_params(operations: list[dict[str, Any]]) -> list[str]:
    """Reuse ownership binders already declared elsewhere in the same IR corpus."""

    found: list[str] = []
    seen: set[str] = set()
    for operation in operations:
        if not (
            _ownership_params_declared_on_operation(operation)
            or _operation_declares_ownership_language(operation)
        ):
            continue
        for name in _ownership_params_declared_on_operation(operation):
            key = _param_key(name)
            if key in seen:
                continue
            seen.add(key)
            found.append(name)
    return found


def _ownership_field_path(
    operation: dict[str, Any],
    *,
    name: str,
) -> str:
    """Return dotted body path for a nested ownership field when present."""

    target = _param_key(name)

    def _search(properties: dict[str, Any], prefix: tuple[str, ...] = ()) -> str:
        for field_name, field_schema in properties.items():
            field = str(field_name)
            path = (*prefix, field)
            if _param_key(field) == target:
                return ".".join(path)
            nested = _dict(field_schema)
            found = _search(_dict(nested.get("properties")), path)
            if found:
                return found
            items = _dict(nested.get("items"))
            if items:
                found = _search(_dict(items.get("properties")), path)
                if found:
                    return found
        return ""

    schema = _dict(operation.get("request_schema"))
    if _dict(schema.get("properties")):
        found = _search(_dict(schema.get("properties")))
        if found:
            return found
    for media in _dict(schema.get("content")).values():
        properties = _dict(_dict(_dict(media).get("schema")).get("properties"))
        found = _search(properties)
        if found:
            return found
    example = operation.get("request_example")

    def _search_example(value: Any, prefix: tuple[str, ...] = ()) -> str:
        if not isinstance(value, dict):
            return ""
        for field_name, nested in value.items():
            field = str(field_name)
            path = (*prefix, field)
            if _param_key(field) == target:
                return ".".join(path)
            found = _search_example(nested, path)
            if found:
                return found
        return ""

    return _search_example(example) if isinstance(example, dict) else ""


def _resolve_ownership_binder(
    operation: dict[str, Any],
    *,
    operations: list[dict[str, Any]],
) -> dict[str, str]:
    """Resolve a source-grounded ownership identity binder for collection ops.

    Path-param resources do not need a binder. Collection reads/writes only
    compile isolation when an ownership param is declared on the operation or
    transferable from another ownership-declared operation in the same IR.
    """

    path = _text(operation.get("path") or operation.get("raw_path"))
    if _path_has_resource_placeholder(path):
        return {}
    declared = _ownership_params_declared_on_operation(operation)
    candidates = list(declared)
    for name in _corpus_ownership_params(operations):
        if _param_key(name) not in {_param_key(item) for item in candidates}:
            candidates.append(name)
    if not candidates:
        return {}
    name = candidates[0]
    location = _ownership_binder_location(operation, name=name)
    field_path = _ownership_field_path(operation, name=name) if location == "body" else ""
    return {
        "name": field_path or name,
        "location": location,
        "identity_binding_target": "user_id",
    }


def _compile_gap(*, subject_ref: str, relation_types: set[str]) -> dict[str, Any]:
    relation_label = ",".join(sorted(relation_types))
    material = f"BLOCKED_MISSING_IR_RELATION|{subject_ref}|{relation_label}"
    return {
        "id": f"compile_gap_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
        "code": "BLOCKED_MISSING_IR_RELATION",
        "subject_ref": _text(subject_ref),
        "required_relation_types": sorted(relation_types),
        "description": "No explicit Behavior IR relation resolves the required operation join",
        "status": "unsupported",
        "source_refs": [],
    }


def _operation_path_prefix(operation: dict[str, Any]) -> str:
    """Stable path-prefix key for planning diversity (first two segments)."""

    path = normalize_path_placeholders(
        _text(operation.get("path") or operation.get("raw_path"))
    ).split("?", 1)[0].rstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return "/" + "/".join(parts[:2])
    if parts:
        return "/" + parts[0]
    return ""


# Decision input surfaces: operations that decide an entity's eligibility
# (validate/check/use/claim/simulate/estimate — 校验/验证/使用/领取/可用/模拟/
# 试算/计算/预估/报价) and echo the decision in their response. For such
# operations the response body IS the effect — an entity-eligibility rule is
# decidable on them even when the operation is read-like, and the decision
# oracle's response_decision channel already treats an acceptance decision for
# a violating entity as the violation. GET/HEAD list reads are never decision
# input surfaces (they return collections, not a decision). Generic technical
# verbs, never industry terms.
_DECISION_INPUT_SURFACE_TOKENS = (
    "validate", "check", "verify", "eligible", "usable", "consume",
    "apply", "simulate", "quote", "estimate", "calculate", "use",
    "claim", "校验", "验证", "使用", "领取", "可用", "模拟", "计算",
    "预估", "报价", "试算",
)


def _decision_input_surface(operation: dict[str, Any]) -> bool:
    """True when the operation is a decision input surface (POST/PUT/PATCH
    whose path or summary carries the generic decision vocabulary)."""
    if not isinstance(operation, dict):
        return False
    method = _text(operation.get("method")).upper()
    if method not in {"POST", "PUT", "PATCH"}:
        return False
    combined = " ".join([
        _text(operation.get("path") or operation.get("raw_path")),
        _text(operation.get("summary") or operation.get("title") or ""),
    ]).casefold()
    return any(token in combined for token in _DECISION_INPUT_SURFACE_TOKENS)


def _authorization_pair_incomplete_gap(
    *,
    operation: dict[str, Any],
    permit_relations: list[dict[str, Any]],
    deny_relations: list[dict[str, Any]],
) -> dict[str, Any]:
    operation_ref = _text(operation.get("id"))
    missing = []
    if not permit_relations:
        missing.append("permits")
    if not deny_relations:
        missing.append("denies")
    if not missing:
        missing = ["permits", "denies"]
    material = "|".join(
        ["BLOCKED_MISSING_ACTOR_PAIR", operation_ref, ",".join(missing)]
    )
    return {
        "id": f"compile_gap_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
        "code": "BLOCKED_MISSING_ACTOR_PAIR",
        "risk_family": (
            "visibility"
            if _text(operation.get("method")).upper() in {"GET", "HEAD"}
            else "authorization"
        ),
        "subject_ref": operation_ref,
        "operation_ref": operation_ref,
        "required_relation_types": ["denies", "permits"],
        "missing_relation_types": missing,
        "description": (
            "Operation has source-backed actor relations but no executable "
            "permit×deny authorization pair"
        ),
        "status": "unsupported",
        "source_refs": _combined_source_refs(
            operation,
            *permit_relations[:2],
            *deny_relations[:2],
        ),
    }


def _boolish_true(value: Any) -> bool:
    return value is True or _text(value).lower() == "true"


def _is_unresolvable_actor_secret_ref(secret_ref: str) -> bool:
    return _text(secret_ref).lower().startswith("secret_ref:actor:")


def _actor_role_key(actor: dict[str, Any]) -> str:
    return _text(actor.get("role_key") or actor.get("role")).lower()


def _actor_has_runtime_binding(actor: dict[str, Any]) -> bool:
    role = _actor_role_key(actor)
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    if _is_unresolvable_actor_secret_ref(secret_ref):
        return False
    if _text(actor.get("account_ref")):
        return True
    if _boolish_true(actor.get("runtime_bound")):
        return bool(secret_ref)
    return bool(secret_ref)


def _cleanup_is_schedulable(requirement: dict[str, Any]) -> bool:
    """True when a write may leave the compile pool (cleanup not required or bound)."""

    req = _dict(requirement)
    if req.get("required") is False:
        return True
    if _text(req.get("mode")) == "snapshot_restore":
        # PUT/PATCH restore via before-snapshot; experiment compile validates
        # the primary method before building the cleanup plan.
        return True
    return bool(
        _text(req.get("operation_ref") or req.get("compensation_operation_ref"))
    )


def _find_read_operation(
    collection_path: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a GET operation on the same collection for authorization testing."""
    from .real_id_resolver_base import normalize_path_placeholders
    normalized = normalize_path_placeholders(collection_path).rstrip("/")
    if not normalized.startswith("/"):
        return None
    best: dict[str, Any] | None = None
    best_score = -1
    for op in operations:
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in ("GET", "HEAD"):
            continue
        op_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        ).rstrip("/")
        if op_path == normalized:
            return op
        if op_path.startswith(normalized) or normalized.startswith(op_path):
            score = len(set(op_path.split("/")) & set(normalized.split("/")))
            if score > best_score:
                best_score = score
                best = op
    return best


def _cleanup_requirement(
    operation: dict[str, Any],
    operations: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    *,
    required: bool | None = None,
) -> dict[str, Any]:
    """Bind cleanup only through one explicit ``compensates`` relation.

    Two source-grounded directions are accepted:
    - create/write primary → unique compensator (DELETE/release/…)
    - compensator primary (DELETE/release/cancel/…) → unique compensated
      create/restore operation (recreate), preferring a unique POST when multiple
      targets exist

    Compensator identity may appear on ``operation_ref``, ``from_ref``, or
    ``effects[].cleanup_target_operation_ref`` (Behavior IR derivation stamps).

    Without an explicit relation, only PUT/PATCH snapshot restoration and an
    identity-bound DELETE for a create POST are schedulable. Route shape never
    proves that a deleted entity can be recreated, and unrelated source
    relations never prove that a write is non-mutating.
    """
    op = _dict(operation)
    is_write = _text(op.get("read_write")) == "write"
    must_cleanup = is_write if required is None else bool(required)
    requirement: dict[str, Any] = {"required": must_cleanup, "mode": "reverse_order"}
    if not must_cleanup:
        return requirement
    operations_by_id = {
        _text(row.get("id")): row
        for row in operations
        if isinstance(row, dict) and _text(row.get("id"))
    }
    operation_ids = set(operations_by_id)
    op_id = _text(op.get("id"))
    compensation_refs: set[str] = set()
    for relation in relations:
        if _text(relation.get("relation_type")) != "compensates":
            continue
        compensator = _text(
            relation.get("operation_ref") or relation.get("from_ref")
        )
        targets_primary = _text(relation.get("to_ref")) == op_id
        if not targets_primary:
            for effect in _list(relation.get("effects")):
                if not isinstance(effect, dict):
                    continue
                if _text(effect.get("cleanup_target_operation_ref")) == op_id:
                    targets_primary = True
                    compensator = compensator or _text(
                        relation.get("operation_ref") or relation.get("from_ref")
                    )
                    break
        if (
            targets_primary
            and compensator in operation_ids
            and compensator != op_id
        ):
            compensation_refs.add(compensator)
    if len(compensation_refs) == 1:
        requirement["operation_ref"] = next(iter(compensation_refs))
        return requirement

    method = _text(op.get("method")).upper()
    if method == "DELETE":
        # A DELETE may be a soft delete: the row stays in the target, so a
        # collection recreate collides with the row's unique key (the server
        # answers 5xx on the unique constraint). When the source declares a
        # same-path restore write (PATCH/PUT on the deleted resource with a
        # request body), prefer it: the executor restores state when the row
        # still exists and falls back to the collection recreate when it is
        # gone. Without a source body the restore is not writable and the
        # regular compensator resolution below applies.
        raw_path = normalize_path_placeholders(
            _text(op.get("path") or op.get("raw_path"))
        )
        restore_writes = [
            cand_id
            for cand_id, cand_op in operations_by_id.items()
            if cand_id != op_id
            and isinstance(cand_op, dict)
            and _text(cand_op.get("method")).upper() in {"PATCH", "PUT"}
            and normalize_path_placeholders(
                _text(cand_op.get("path") or cand_op.get("raw_path"))
            ) == raw_path
            and isinstance(_dict(cand_op.get("request_example")), dict)
            and _dict(cand_op.get("request_example"))
        ]
        if len(restore_writes) == 1:
            requirement["operation_ref"] = restore_writes[0]
            requirement["mode"] = "restore_deleted_resource"
            return requirement

        # Recreate-from-compensated-create is a DELETE-only compensation: a
        # DELETE removes the resource, so recreating the create it compensates
        # restores existence. A state-changing POST (cancel/close/approve/…)
        # leaves the resource in place; recreating its create would produce a
        # SECOND resource, not a rollback. The relation "X compensates create
        # C" therefore proves recreate authority only when X is a DELETE.
        restore_refs = {
            _text(relation.get("to_ref"))
            for relation in relations
            if _text(relation.get("relation_type")) == "compensates"
            and _text(relation.get("operation_ref") or relation.get("from_ref")) == op_id
            and _text(relation.get("to_ref")) in operation_ids
            and _text(relation.get("to_ref")) != op_id
        }
        if len(restore_refs) > 1:
            post_restores = {
                ref
                for ref in restore_refs
                if _text(_dict(operations_by_id.get(ref)).get("method")).upper() == "POST"
            }
            if len(post_restores) == 1:
                restore_refs = post_restores
        if len(restore_refs) == 1:
            requirement["operation_ref"] = next(iter(restore_refs))
            requirement["mode"] = "recreate_compensated_resource"
            return requirement

    if method in {"PUT", "PATCH"}:
        requirement["mode"] = "snapshot_restore"
        return requirement

    # A create route may use only an exact identity-bound DELETE on the same
    # collection. DELETE-to-POST recreation requires an explicit relation.
    # Identity-bound POSTs (/api/products/admin/{sku}/status, …/{id}/default)
    # are state-changing actions on an existing resource, never collection
    # creates: a DELETE derived for them would remove the resource the action
    # merely mutated, so the derivation is restricted to placeholder-free
    # collection POSTs.
    raw_path = normalize_path_placeholders(
        _text(op.get("path") or op.get("raw_path"))
    )
    op_collection = normalize_path_placeholders(collection_path(raw_path))
    if (
        method == "POST"
        and "{" not in raw_path
        and op_collection.startswith("/")
    ):
        delete_candidates: list[str] = []
        for cand_id, cand_op in operations_by_id.items():
            if cand_id == op_id:
                continue
            cand_method = _text(cand_op.get("method")).upper()
            if cand_method != "DELETE":
                continue
            cand_path = normalize_path_placeholders(
                _text(cand_op.get("path") or cand_op.get("raw_path"))
            )
            cand_collection = normalize_path_placeholders(collection_path(cand_path))
            identity_suffix = cand_path[len(op_collection):]
            if (
                cand_collection == op_collection
                and re.fullmatch(r"/\{[A-Za-z_]\w*\}", identity_suffix)
            ):
                delete_candidates.append(cand_id)
        if len(delete_candidates) == 1:
            requirement["operation_ref"] = delete_candidates[0]
            requirement["mode"] = "reverse_order"
            return requirement

    # A write with no compensator keeps required=True and no operation_ref, so
    # the obligation stays blocked. A per-run database reset is not a governed
    # cleanup receipt for the individual write and cannot stand in for one.
    return requirement


def _active_actors(actors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked = {"disabled", "locked", "inactive", "suspended"}
    active = [
        actor
        for actor in actors
        if _text(actor.get("account_status")).lower() not in blocked
        and _actor_has_runtime_binding(actor)
    ]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in active:
        role_key = _actor_role_key(actor)
        by_role.setdefault(role_key, []).append(actor)

    selected_ids: set[int] = set()
    for role_actors in by_role.values():
        account_bound = [actor for actor in role_actors if _text(actor.get("account_ref"))]
        runtime_bound = [
            actor
            for actor in role_actors
            if _boolish_true(actor.get("runtime_bound"))
        ]
        chosen = account_bound or runtime_bound or role_actors
        selected_ids.update(id(actor) for actor in chosen)

    return [actor for actor in active if id(actor) in selected_ids]


def _combined_source_refs(*nodes: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in nodes:
        for ref in _list(_dict(node).get("source_refs")):
            if not isinstance(ref, dict):
                continue
            key = "|".join(_text(ref.get(k)) for k in ("source_id", "locator", "kind", "quote_hash"))
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(ref))
            if len(out) >= limit:
                return out
    return out


def _permission_rule_binding(
    *relations: dict[str, Any],
    operation_ref: str = "",
) -> dict[str, Any]:
    """Compile the permission-rule binding carried by permit/deny/owns relations.

    The binding carries the verbatim permission-matrix / operation role
    declaration statements the relations were derived from (carried by the IR
    builder as ``source_rule_statements`` on each relation).  This is the
    permission rule-binding channel: authorization obligations bind their role
    declaration to the actual permission rule statement text, so delivered
    findings carry a 源契约 even when the knowledge asset is not present at
    delivery time.  Only verbatim source texts are bound — relations without a
    statement contribute nothing, and no binding is emitted when no statement
    exists.
    """
    statements: list[str] = []
    rule_refs: list[str] = []
    for relation in relations:
        for row in _list(_dict(relation).get("source_rule_statements")):
            if not isinstance(row, dict):
                continue
            statement = _text(row.get("statement"))
            rule_id = _text(row.get("rule_id"))
            if statement:
                statements.append(statement)
            if rule_id and rule_id not in rule_refs:
                rule_refs.append(rule_id)
    statements = list(dict.fromkeys(statement for statement in statements if statement))
    if not statements:
        return {}
    return {
        "rule_id": rule_refs[0] if rule_refs else "",
        "rule_fingerprint": rule_refs[0] if rule_refs else "",
        "rule_type": "permission",
        "statement": "；".join(statements),
        "source_rule_refs": list(rule_refs),
        "required_field_ids": [],
        "typed_expression": {},
        "operation_id": _text(operation_ref),
    }


def _with_permission_rule_binding(
    property_spec: dict[str, Any],
    *relations: dict[str, Any],
    operation_ref: str = "",
) -> dict[str, Any]:
    """Attach the permission rule binding onto an authorization property spec."""
    binding = _permission_rule_binding(*relations, operation_ref=operation_ref)
    if binding:
        property_spec["field_rule_binding"] = binding
    return property_spec


def _actor_binding_gap(
    *,
    actor: dict[str, Any],
    operation_ref: str = "",
    relation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor_ref = _text(actor.get("id"))
    relation_id = _text(_dict(relation).get("id"))
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    reason = (
        "unresolved_role_secret_ref"
        if _is_unresolvable_actor_secret_ref(secret_ref)
        else "missing_runtime_actor_binding"
    )
    material = "|".join([
        "BLOCKED_MISSING_ACTOR_BINDING",
        actor_ref,
        _text(operation_ref),
        relation_id,
        reason,
    ])
    return {
        "id": f"compile_gap_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}",
        "code": "BLOCKED_MISSING_ACTOR_BINDING",
        "subject_ref": _text(operation_ref) or actor_ref,
        "actor_ref": actor_ref,
        "operation_ref": _text(operation_ref),
        "relation_refs": [relation_id] if relation_id else [],
        "reason": reason,
        "required_binding": "runtime_actor_or_resolvable_secret_ref",
        "description": "Actor relation is source-known but cannot be executed without a runtime account or resolvable credential reference",
        "status": "unsupported",
        "source_refs": _combined_source_refs(actor, _dict(relation)),
    }


def _append_gap_once(coverage_gaps: list[dict[str, Any]], gap: dict[str, Any]) -> None:
    gap_id = _text(gap.get("id"))
    if gap_id and any(_text(existing.get("id")) == gap_id for existing in coverage_gaps):
        return
    coverage_gaps.append(gap)


def _seed_obligation_fact_refs(
    obligations: list[dict[str, Any]],
    invariants: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Carry exact accepted-fact identity from IR nodes onto obligations.

    Invariants produced from fact-promoted rules carry ``fact_refs`` at IR
    construction (behavior_ir_core attaches rule.semantic_contract.fact_id).
    Obligations compiled from those nodes inherit the identity here — inside
    the obligation production function, so compile/selection/execution see the
    fact authority by construction, never through a post-hoc planning join.
    Relations resolve through their single incident invariant. ``fact_refs``
    are never inferred: an obligation with no fact-carrying node simply keeps
    an empty list.
    """
    invariant_fact_refs: dict[str, tuple[str, ...]] = {}
    for row in invariants:
        if not isinstance(row, dict) or not _text(row.get("id")):
            continue
        refs = tuple(sorted({
            _text(value) for value in _list(row.get("fact_refs")) if _text(value)
        }))
        if refs:
            invariant_fact_refs[_text(row.get("id"))] = refs
    relation_fact_refs: dict[str, tuple[str, ...]] = {}
    for row in relations:
        if not isinstance(row, dict) or not _text(row.get("id")):
            continue
        incident = {
            ref
            for ref in (_text(row.get("from_ref")), _text(row.get("to_ref")))
            if ref in invariant_fact_refs
        }
        if len(incident) != 1:
            continue
        relation_fact_refs[_text(row.get("id"))] = invariant_fact_refs[
            next(iter(incident))
        ]
    for obligation in obligations:
        if not isinstance(obligation, dict):
            continue
        prop = _dict(obligation.get("property"))
        candidates: set[str] = set()
        invariant_ref = _text(prop.get("invariant_ref"))
        if invariant_ref in invariant_fact_refs:
            candidates.update(invariant_fact_refs[invariant_ref])
        for relation_ref in _list(obligation.get("relation_refs")):
            if relation_ref in relation_fact_refs:
                candidates.update(relation_fact_refs[relation_ref])
        if candidates:
            obligation["fact_refs"] = sorted(candidates)
    return obligations


def compile_obligations_from_behavior_ir(
    behavior_ir: dict[str, Any],
    *,
    root: str = "",
    project: str = "",
) -> dict[str, Any]:
    """Produce obligations from IR facts using generic property templates.

    Templates bind to IR role/operation/entity IDs only — never name strings
    that encode a specific industry or benchmark answer.

    ``root`` / ``project`` name the customer workspace so the persistence
    observer can resolve the customer-declared database DSN. They are only
    forwarded when the persistence surface is installed (adapter ``db_sql``
    declared); without them the persistence branch emits a visible coverage gap
    instead of an obligation that could never observe.
    """
    ir = _dict(behavior_ir)
    if _text(ir.get("schema_version")) != BEHAVIOR_IR_SCHEMA:
        raise BehaviorIRError("behavior_ir_v2_required")
    validation_errors = validate_behavior_ir(ir, require_explicit_relations=True)
    if validation_errors:
        raise BehaviorIRError("behavior_ir_v2_invalid:" + ",".join(validation_errors))
    operations = _accepted(_list(ir.get("operations")))
    actors = _accepted(_list(ir.get("actors")))
    invariants = _accepted(_list(ir.get("invariants")))
    relations = _accepted(_list(ir.get("relations")))
    states = _accepted(_list(ir.get("states")))
    entities = _accepted(_list(ir.get("entities")))
    obligations: list[dict[str, Any]] = []
    coverage_gaps = [dict(item) for item in _list(ir.get("coverage_gaps")) if isinstance(item, dict)]

    write_ops = [op for op in operations if _text(op.get("read_write") or op.get("side_effect_class")) == "write"]
    read_ops = [op for op in operations if _text(op.get("read_write") or op.get("side_effect_class")) != "write"]

    # P0-E phase-3: operation-level CJK ownership language markers
    # (自己的/本人/归属/只能查询) are legacy candidate hints — the frame
    # channel's structured ownership (IR owns relations) is the ownership
    # SSOT. Counted only when a frame ledger exists.
    _ownership_kind_counts = _legacy_fallback_kind_counts(ir)
    if _ownership_kind_counts is not None:
        _cjk_ownership_hits = 0
        for _op_row in operations:
            _op_corpus = " ".join((
                _text(_op_row.get("summary")),
                _text(_op_row.get("description")),
            ))
            if any(_m in _op_corpus for _m in _CJK_OWNERSHIP_LANGUAGE_MARKERS):
                _cjk_ownership_hits += 1
        _count_legacy_cjk_kind(
            _ownership_kind_counts, "OWNERSHIP_LANGUAGE_CJK_CANDIDATE", _cjk_ownership_hits
        )

    active_actors = _active_actors(actors)
    active_actors_by_id = {
        _text(actor.get("id")): actor
        for actor in active_actors
        if _text(actor.get("id"))
    }
    actors_by_id = {
        _text(actor.get("id")): actor
        for actor in actors
        if _text(actor.get("id"))
    }
    executable_roles = {
        _actor_role_key(actor)
        for actor in active_actors
        if _actor_role_key(actor)
    }

    def note_unbound_actor_relation(relation: dict[str, Any], operation_ref: str) -> None:
        actor_ref = _relation_actor_ref(relation)
        if not actor_ref or actor_ref in active_actors_by_id:
            return
        actor = actors_by_id.get(actor_ref)
        if not actor or _actor_has_runtime_binding(actor):
            return
        if _actor_role_key(actor) in executable_roles:
            return
        _append_gap_once(
            coverage_gaps,
            _actor_binding_gap(
                actor=actor,
                operation_ref=operation_ref,
                relation=relation,
            ),
        )

    # Authorization joins explicit permit and deny relations for one operation.
    # Permit-only (or unpaired) operations must not vanish: emit a visible gap
    # and, for non-write ops, a source-grounded permitted invocation so the
    # module path can enter the schedulable Behavior Field.
    for op in operations:
        operation_ref = _text(op.get("id"))
        permit_relations = _relations_for_operation(relations, operation_ref, {"permits"})
        deny_relations = _relations_for_operation(relations, operation_ref, {"denies"})
        for relation in [*permit_relations, *deny_relations]:
            note_unbound_actor_relation(relation, operation_ref)
        paired_auth = 0
        # Limit authorization pairs for inferred operations (max 2 pairs)
        _is_inferred = any(
            str(s.get("kind", "")).startswith("permission_")
            for s in _list(op.get("source_refs"))
        )
        _MAX_AUTH_PAIRS = 2 if _is_inferred else 999
        for permit_relation in permit_relations:
            if paired_auth >= _MAX_AUTH_PAIRS:
                break
            allowed = active_actors_by_id.get(_relation_actor_ref(permit_relation))
            if not allowed:
                continue
            for deny_relation in deny_relations:
                if paired_auth >= _MAX_AUTH_PAIRS:
                    break
                denied = active_actors_by_id.get(_relation_actor_ref(deny_relation))
                if not denied or _text(denied.get("id")) == _text(allowed.get("id")):
                    continue
                paired_auth += 1
                obligations.append(make_obligation(
                    risk_family="authorization",
                    subject_refs=[
                        operation_ref,
                        _text(allowed.get("id")),
                        _text(denied.get("id")),
                    ],
                    property_spec=_with_permission_rule_binding(
                        {
                            "template": "authorization_control_treatment",
                            "control_actor_ref": _text(allowed.get("id")),
                            "treatment_actor_ref": _text(denied.get("id")),
                            "operation_ref": operation_ref,
                            "operation_path_prefix": _operation_path_prefix(op),
                            "require_same_resource": True,
                        },
                        permit_relation,
                        deny_relation,
                        operation_ref=operation_ref,
                    ),
                    required_actors=[_text(allowed.get("id")), _text(denied.get("id"))],
                    required_operations=[operation_ref],
                    required_observers=["http_response", "actor_identity"],
                    cleanup_requirement=_cleanup_requirement(op, operations, relations),
                    source_refs=_combined_source_refs(
                        op,
                        allowed,
                        denied,
                        permit_relation,
                        deny_relation,
                    ),
                    relation_refs=[
                        _text(permit_relation.get("id")),
                        _text(deny_relation.get("id")),
                    ],
                    confidence=min(
                        float(op.get("confidence") or 0.7),
                        float(allowed.get("confidence") or 0.7),
                        float(denied.get("confidence") or 0.7),
                        float(permit_relation.get("confidence") or 0.8),
                        float(deny_relation.get("confidence") or 0.8),
                    ),
                ))
        active_permit_refs = sorted({
            _relation_actor_ref(relation)
            for relation in permit_relations
            if _relation_actor_ref(relation) in active_actors_by_id
        })
        if permit_relations and paired_auth == 0:
            _append_gap_once(
                coverage_gaps,
                _authorization_pair_incomplete_gap(
                    operation=op,
                    permit_relations=permit_relations,
                    deny_relations=deny_relations,
                ),
            )
        is_write = _text(op.get("read_write") or op.get("side_effect_class")) == "write"
        if active_permit_refs and paired_auth == 0:
            cleanup_req = _cleanup_requirement(op, operations, relations)
            actor_ref = active_permit_refs[0]
            actor = active_actors_by_id[actor_ref]
            actor_relations = [
                relation
                for relation in permit_relations
                if _relation_actor_ref(relation) == actor_ref
            ]
            # Skip operations with unresolvable path placeholders:
            # if the path has {param} tokens and no GET endpoint exists on
            # the same resource path (with placeholder) to resolve them,
            # the obligation will always fail at binding.
            _op_path = normalize_path_placeholders(_text(op.get("path") or op.get("raw_path")))
            if path_has_placeholders(_op_path):
                _has_resolver = any(
                    _text(o.get("method")).upper() in ("GET", "HEAD")
                    and (
                        normalize_path_placeholders(_text(o.get("path") or o.get("raw_path"))).rstrip("/")
                        == _op_path.rstrip("/")
                        or normalize_path_placeholders(collection_path(_text(o.get("path") or o.get("raw_path")))).rstrip("/")
                        == normalize_path_placeholders(collection_path(_op_path)).rstrip("/")
                    )
                    for o in operations if isinstance(o, dict)
                )
                if not _has_resolver:
                    continue  # skip — can never resolve path params
            # Writes enter the schedulable pool only when source cleanup is bound
            # (or explicitly not required). Uncompensated writes keep the gap only.
            if is_write and not _cleanup_is_schedulable(cleanup_req):
                # ── Fallback: use GET for authorization testing ──
                op_collection_clean = normalize_path_placeholders(
                    collection_path(
                        normalize_path_placeholders(_text(op.get("path") or op.get("raw_path")))
                    )
                )
                read_op = _find_read_operation(op_collection_clean, operations)
                if read_op and _text(read_op.get("id")) != operation_ref:
                    read_cleanup = {"required": False, "mode": "not_required_read"}
                    obligations.append(make_obligation(
                        risk_family="authorization",
                        subject_refs=[_text(read_op.get("id")), actor_ref],
                        property_spec=_with_permission_rule_binding(
                            {
                                "template": "permitted_operation_invocation",
                                "actor_ref": actor_ref,
                                "control_actor_ref": actor_ref,
                                "treatment_actor_ref": actor_ref,
                                "operation_ref": _text(read_op.get("id")),
                                "operation_path_prefix": _operation_path_prefix(read_op),
                            },
                            *actor_relations,
                            operation_ref=_text(read_op.get("id")),
                        ),
                        required_actors=[actor_ref],
                        required_operations=[_text(read_op.get("id"))],
                        required_observers=["http_response", "actor_identity"],
                        cleanup_requirement=read_cleanup,
                        source_refs=_combined_source_refs(read_op, actor, *actor_relations),
                        relation_refs=sorted({
                            _text(relation.get("id"))
                            for relation in actor_relations
                            if _text(relation.get("id"))
                        }),
                        confidence=min(
                            float(op.get("confidence") or 0.7),
                            float(actor.get("confidence") or 0.7),
                        ),
                    ))
                continue
            obligations.append(make_obligation(
                risk_family="authorization",
                subject_refs=[operation_ref, actor_ref],
                property_spec=_with_permission_rule_binding(
                    {
                        "template": "permitted_operation_invocation",
                        "actor_ref": actor_ref,
                        "control_actor_ref": actor_ref,
                        "treatment_actor_ref": actor_ref,
                        "operation_ref": operation_ref,
                        "operation_path_prefix": _operation_path_prefix(op),
                    },
                    *actor_relations,
                    operation_ref=operation_ref,
                ),
                required_actors=[actor_ref],
                required_operations=[operation_ref],
                required_observers=["http_response", "actor_identity"],
                cleanup_requirement=cleanup_req,
                source_refs=_combined_source_refs(op, actor, *actor_relations),
                relation_refs=sorted({
                    _text(relation.get("id"))
                    for relation in actor_relations
                    if _text(relation.get("id"))
                }),
                confidence=min(
                    float(op.get("confidence") or 0.7),
                    float(actor.get("confidence") or 0.7),
                    float(actor_relations[0].get("confidence") or 0.8)
                    if actor_relations
                    else 0.7,
                ),
            ))

    # Isolation uses only account-bound actors explicitly linked by ownership.
    # Path-param owned reads keep the owned_resource proof path. Owned
    # collections (and owned writes) compile only when a source-grounded
    # ownership identity binder is available from the same Behavior IR corpus.
    owned_isolation_ops = [
        op for op in [*read_ops, *write_ops]
        if _relations_for_operation(relations, _text(op.get("id")), {"owns"})
    ]
    for op in owned_isolation_ops:
        path = _text(op.get("path") or op.get("raw_path"))
        has_path_target = _path_has_resource_placeholder(path)
        ownership_binder = {} if has_path_target else _resolve_ownership_binder(
            op,
            operations=operations,
        )
        if not has_path_target and not ownership_binder:
            continue
        ownership_relations = _relations_for_operation(relations, _text(op.get("id")), {"owns"})
        relation_by_actor: dict[str, list[dict[str, Any]]] = {}
        for relation in ownership_relations:
            actor_ref = _relation_actor_ref(relation)
            actor = active_actors_by_id.get(actor_ref)
            if actor and _text(actor.get("account_ref")):
                relation_by_actor.setdefault(actor_ref, []).append(relation)
        by_role: dict[str, list[str]] = {}
        for actor_ref in relation_by_actor:
            actor = active_actors_by_id[actor_ref]
            by_role.setdefault(_text(actor.get("role")).lower(), []).append(actor_ref)
        for actor_refs in by_role.values():
            for owner_ref, viewer_ref in permutations(sorted(set(actor_refs)), 2):
                owner = active_actors_by_id[owner_ref]
                viewer = active_actors_by_id[viewer_ref]
                pair_relations = relation_by_actor[owner_ref] + relation_by_actor[viewer_ref]
                property_spec: dict[str, Any] = _with_permission_rule_binding(
                    {
                        "template": "owner_viewer_isolation",
                        "owner_actor_ref": owner_ref,
                        "viewer_actor_ref": viewer_ref,
                        "operation_ref": _text(op.get("id")),
                        "operation_path_prefix": _operation_path_prefix(op),
                        "require_same_resource": True,
                    },
                    *pair_relations,
                    operation_ref=_text(op.get("id")),
                )
                required_fixtures: list[str] = []
                required_observers = ["http_response", "actor_identity"]
                if has_path_target:
                    property_spec["require_ownership_evidence"] = True
                    required_fixtures = ["owned_resource"]
                    required_observers = ["http_response", "resource_ownership"]
                else:
                    property_spec.update({
                        "ownership_param": _text(ownership_binder.get("name")),
                        "ownership_param_location": _text(
                            ownership_binder.get("location")
                        ),
                        "identity_binding_target": _text(
                            ownership_binder.get("identity_binding_target")
                        ) or "user_id",
                    })
                obligations.append(make_obligation(
                    risk_family="isolation",
                    subject_refs=[_text(op.get("id")), owner_ref, viewer_ref],
                    property_spec=property_spec,
                    required_actors=[owner_ref, viewer_ref],
                    required_operations=[_text(op.get("id"))],
                    required_fixtures=required_fixtures,
                    required_observers=required_observers,
                    cleanup_requirement=_cleanup_requirement(op, operations, relations),
                    source_refs=_combined_source_refs(op, owner, viewer, *pair_relations),
                    relation_refs=sorted({
                        _text(relation.get("id"))
                        for relation in pair_relations
                        if _text(relation.get("id"))
                    }),
                    confidence=min(
                        float(op.get("confidence") or 0.7),
                        float(owner.get("confidence") or 0.7),
                        float(viewer.get("confidence") or 0.7),
                    ),
                ))

    # State obligations require an explicit state -> operation -> state join.
    states_by_id = {_text(state.get("id")): state for state in states if _text(state.get("id"))}
    operations_by_id = {_text(op.get("id")): op for op in operations if _text(op.get("id"))}
    state_entities_with_transition: set[str] = set()
    state_relations: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for relation in relations:
        if _text(relation.get("relation_type")) != "transitions":
            continue
        from_state = states_by_id.get(_text(relation.get("from_ref")))
        to_state = states_by_id.get(_text(relation.get("to_ref")))
        op = operations_by_id.get(_text(relation.get("operation_ref")))
        if not from_state or not to_state or not op:
            continue
        entity_ref = _text(from_state.get("entity_ref") or to_state.get("entity_ref"))
        state_entities_with_transition.add(entity_ref)
        state_relations.append((relation, from_state, to_state, op))
        obligations.append(make_obligation(
            risk_family="state",
            subject_refs=[
                _text(op.get("id")),
                entity_ref,
                _text(from_state.get("id")),
                _text(to_state.get("id")),
            ],
            property_spec={
                "template": "state_transition",
                "entity_ref": entity_ref,
                "from_state_ref": _text(from_state.get("id")),
                "to_state_ref": _text(to_state.get("id")),
                "operation_ref": _text(op.get("id")),
                "operation_path_prefix": _operation_path_prefix(op),
                "state_field": _entity_state_field(entities, entity_ref),
            },
            required_operations=[_text(op.get("id"))],
            required_fixtures=[f"entity_in_state:{_text(from_state.get('id'))}"],
            required_observers=["before_state", "after_state"],
            cleanup_requirement=_cleanup_requirement(op, operations, relations, required=True),
            source_refs=_combined_source_refs(relation, from_state, to_state, op),
            relation_refs=[_text(relation.get("id"))],
            confidence=min(
                float(relation.get("confidence") or 0.6),
                float(op.get("confidence") or 0.7),
            ),
        ))

    # ── Wrong-source-state probes (runtime-observed machine conformance) ──
    # A declared transition ``A --op--> B`` names the ONLY legal source state
    # for ``op``. The machine says nothing about running ``op`` from an
    # adjacent state S (its direct predecessor or successor): the same
    # operation is not a declared transition from S, so reaching B from S is
    # a machine violation. For every allowed edge we therefore also probe
    # ``op`` from each adjacent alternative source state S (S != A, S != B)
    # and assert the forbidden transition ``S --op--> B``: PASS when the
    # target rejects the operation (state unchanged), VIOLATION when the
    # operation still moves the entity to B. The alternative source comes
    # exclusively from the machine's own declared adjacency — never a
    # guessed state — and the establishment chain for S is planned by the
    # same precondition planner as the allowed edge.
    _state_adjacency: dict[str, set[str]] = {}
    for _rel, _from_state, _to_state, _op in state_relations:
        _state_adjacency.setdefault(_text(_from_state.get("id")), set()).add(_text(_to_state.get("id")))
        _state_adjacency.setdefault(_text(_to_state.get("id")), set()).add(_text(_from_state.get("id")))
    for _rel, _from_state, _to_state, _op in state_relations:
        _from_id = _text(_from_state.get("id"))
        _to_id = _text(_to_state.get("id"))
        _alt_ids = sorted(
            (neighbor for neighbor in _state_adjacency.get(_from_id, set())
             if neighbor != _from_id and neighbor != _to_id)
        )
        for _alt_id in _alt_ids:
            _alt_state = states_by_id.get(_alt_id)
            if not _alt_state:
                continue
            _entity_ref = _text(_from_state.get("entity_ref") or _to_state.get("entity_ref"))
            obligations.append(make_obligation(
                risk_family="state",
                subject_refs=[
                    _text(_op.get("id")),
                    _entity_ref,
                    _alt_id,
                    _to_id,
                ],
                property_spec={
                    "template": "state_transition",
                    "entity_ref": _entity_ref,
                    "from_state_ref": _alt_id,
                    "to_state_ref": _to_id,
                    "operation_ref": _text(_op.get("id")),
                    "operation_path_prefix": _operation_path_prefix(_op),
                    "state_field": _entity_state_field(entities, _entity_ref),
                    "expression": {
                        "kind": "forbidden_state_transition",
                        "operator": "must_not_transition",
                        "operands": [{
                            "entity_ref": _entity_ref,
                            "from_state": _text(_alt_state.get("name") or _alt_state.get("value")),
                            "to_state": _text(_to_state.get("name") or _to_state.get("value")),
                        }],
                        "raw": (
                            f"{_text(_alt_state.get('name') or _alt_state.get('value'))} "
                            f"--{_text(_op.get('id'))}--> "
                            f"{_text(_to_state.get('name') or _to_state.get('value'))} "
                            f"(forbidden: wrong source state)"
                        ),
                    },
                    "wrong_source_of": _from_id,
                },
                required_operations=[_text(_op.get("id"))],
                required_fixtures=[f"entity_in_state:{_alt_id}"],
                required_observers=["before_state", "after_state"],
                cleanup_requirement=_cleanup_requirement(_op, operations, relations, required=True),
                source_refs=_combined_source_refs(_rel, _from_state, _to_state, _op),
                relation_refs=[_text(_rel.get("id"))],
                confidence=min(
                    float(_rel.get("confidence") or 0.6),
                    float(_op.get("confidence") or 0.7),
                ),
            ))
    states_by_entity: dict[str, list[dict[str, Any]]] = {}
    for state in states:
        states_by_entity.setdefault(_text(state.get("entity_ref")), []).append(state)
    for entity_ref, entity_states in states_by_entity.items():
        if len(entity_states) >= 2 and entity_ref not in state_entities_with_transition:
            coverage_gaps.append(_compile_gap(
                subject_ref=entity_ref,
                relation_types={"transitions"},
            ))

    # Conservation / privacy / validation from invariants
    for inv in invariants:
        expr = _dict(inv.get("expression"))
        kind = _text(expr.get("kind") or "business_rule").lower()
        if kind == "postcondition" and not _postcondition_has_bound_effect(expr):
            _append_gap_once(
                coverage_gaps,
                {
                    **_compile_gap(
                        subject_ref=_text(inv.get("id")),
                        relation_types={"postcondition_effect"},
                    ),
                    "code": "SOURCE_POSTCONDITION_EFFECT_UNBOUND",
                    "description": (
                        "Source postcondition has no concrete field or create effect "
                        "that an observer can verify"
                    ),
                    "source_refs": [
                        dict(row)
                        for row in _list(inv.get("source_refs"))
                        if isinstance(row, dict)
                    ],
                },
            )
            continue
        family = "validation"
        # P0-E phase-3 frame-confirmation: a grounded frame's structured
        # frame_type is the SSOT family signal. Only when no grounded frame
        # family evidence exists does the legacy kind-token detection run —
        # CJK token hits there are counted as observable fallback.
        _frame_family = _dict(inv.get("frame_family_evidence"))
        _frame_family_type = (
            _text(_frame_family.get("frame_type"))
            if _frame_family.get("grounded") is True
            else ""
        )
        _mapped_family = _FRAME_TYPE_FAMILY.get(_frame_family_type) if _frame_family_type else ""
        if _mapped_family:
            family = _mapped_family
        else:
            _cjk_family_hit = any(token in kind for token in _CJK_FAMILY_TOKENS)
            if any(token in kind for token in ("idempot", "exactly_once", "deduplic")):
                family = "idempotency"
            elif any(token in kind for token in ("concurr", "race", "atomic")):
                family = "concurrency"
            elif any(
                token in kind
                for token in (
                    "conserv",
                    "data_conservation",
                    "balance",
                    "amount",
                    "quantity",
                    "库存",
                    "金额",
                )
            ):
                family = "conservation"
            elif any(token in kind for token in ("privacy", "pii", "mask", "隐私")):
                family = "privacy"
            elif any(token in kind for token in ("time", "expir", "temporal", "过期")):
                family = "temporal"
            elif any(
                token in kind
                for token in (
                    "permission",
                    "access_control",
                    "authorization",
                    "authorisation",
                    "authz",
                    "acl",
                    "rbac",
                    "visib",
                    "scope",
                    "可见",
                )
            ):
                family = "visibility"
            elif any(token in kind for token in ("state_machine", "state", "状态", "status_")):
                family = "state"
            elif any(token in kind for token in ("postcondition", "must_become", "must_create", "因果", "后置")):
                family = "state"
            if _cjk_family_hit:
                _count_legacy_cjk_kind(
                    _legacy_fallback_kind_counts(ir), "CJK_FAMILY_TOKEN_FALLBACK"
                )
        if kind == "ui":
            # ── UI/UX surface obligation ──
            # A UI rule (CUST-PROD-01 / ORACLE-UI-002 …) constrains a browser
            # page, not an API operation: the page URL comes from the UI
            # design specs riding on the IR, and the experiment is a browser
            # plan. No API operation binding — binding UI rules to HTTP
            # operations would compile hundreds of misdirected validation
            # obligations against the wrong surface.
            _ui_url = ""
            _ui_stmt = _text(inv.get("description")) or _text(expr.get("raw"))
            invariant_ref = _text(inv.get("id"))
            # The screen identity (CUST-01 / ADMIN-01 …) is source-declared
            # by the UI requirements document and carried on the invariant;
            # resolve the page URL by EXACT identity against the declared UI
            # specs (ui_spec_id suffix / screen field / name), never by
            # hardcoded prefix vocabulary.
            _ui_specs = [
                dict(row)
                for row in _list(ir.get("ui_specs"))
                if isinstance(row, dict)
            ]
            _screen_ref = _text(inv.get("screen"))
            _ui_url = ""
            if _screen_ref:
                for _ui_spec in _ui_specs:
                    _spec_id = _text(_ui_spec.get("ui_spec_id"))
                    _spec_name = _text(_ui_spec.get("name"))
                    _spec_screen = _text(
                        _ui_spec.get("screen")
                        or _ui_spec.get("screen_id")
                        or _spec_id.rsplit(":", 1)[-1]
                    )
                    if (
                        _screen_ref == _spec_screen
                        or _screen_ref == _spec_name
                        or _screen_ref in _spec_id
                    ):
                        _ui_url = _text(_ui_spec.get("url"))
                        break
            if not _ui_url:
                # Oracle rules (ORACLE-UI-*) may carry no screen identity; the
                # scene words in their own given/when text (customer/user/buyer
                # vs admin/finance/manager) resolve the same declared UI specs
                # directly by generic scene vocabulary. No scene signal — the
                # URL stays unresolved and the obligation blocks visibly.
                _ui_oracle_row = _dict(inv.get("ui_oracle") or {})
                _scene_text = " ".join([
                    _text(_ui_oracle_row.get("given") or ""),
                    _text(_ui_oracle_row.get("when") or ""),
                ])
                _scene_is_customer = bool(re.search(
                    r"(?:customer|user|buyer|shopper|顾客|用户|买家)",
                    _scene_text,
                ))
                _scene_is_admin = bool(re.search(
                    r"(?:admin|finance|manager|seller|管理员|管理|财务|运营)",
                    _scene_text,
                ))
                if _scene_is_customer or _scene_is_admin:
                    for _ui_spec in _ui_specs:
                        _spec_id = _text(_ui_spec.get("ui_spec_id"))
                        _spec_name = _text(_ui_spec.get("name"))
                        if _scene_is_customer and re.search(
                            r"(?:顾客|用户|买家|customer|user|buyer|shopper)",
                            f"{_spec_id} {_spec_name}",
                        ):
                            _ui_url = _text(_ui_spec.get("url"))
                            break
                        if _scene_is_admin and re.search(
                            r"(?:管理|管理员|admin|finance|manager|seller)",
                            f"{_spec_id} {_spec_name}",
                        ):
                            _ui_url = _text(_ui_spec.get("url"))
                            break
            obligations.append({
                "obligation_id": (
                    "obl_"
                    + hashlib.sha256(
                        f"ui|{invariant_ref}|{_ui_url}".encode("utf-8")
                    ).hexdigest()[:24]
                ),
                "risk_family": "ui_state_consistency",
                "required_surface": "ui_browser",
                "required_operations": [],
                "ui_url": _ui_url,
                "property": {
                    "template": "ui_state_consistency",
                    "invariant_ref": invariant_ref,
                    "expression": dict(expr),
                    "ui_url": _ui_url,
                    "screen": _screen_ref,
                    # Source-declared page-state vocabulary rides with the
                    # obligation so the browser-plan protocol can judge the
                    # rendered DOM against the document's own words.
                    "negative_examples": [
                        _text(item)
                        for item in _list(inv.get("negative_examples"))
                        if _text(item)
                    ],
                    "ui_oracle": dict(inv.get("ui_oracle") or {}),
                    # Surface-declared browser plans (compiled from visible UI
                    # material into governed read-only Playwright plans) ride
                    # with the obligation so the protocol compiles the DOM
                    # assertions into the experiment instead of relying on
                    # token guessing. Matched by the same exact screen
                    # identity used for the URL.
                    "surface_contracts": _surface_contracts_for_screen(
                        _ui_specs, _screen_ref
                    ),
                },
                "source_refs": [
                    dict(row)
                    for row in _list(inv.get("source_refs"))
                    if isinstance(row, dict)
                ],
                "schema_version": _text(ir.get("schema_version")),
            })
            continue
        relation_types = {
            "idempotency": {"observes", "produces", "consumes", "transitions"},
            "concurrency": {"observes", "produces", "consumes", "transitions"},
            # Source joins may emit observes before typed conserves normalization.
            "conservation": {"conserves", "observes"},
            "privacy": {"observes", "scopes"},
            "temporal": {"transitions", "observes"},
            "visibility": {"scopes", "observes"},
            "validation": {"produces", "consumes", "transitions", "observes"},
            "state": {"transitions", "observes"},
        }[family]
        invariant_ref = _text(inv.get("id"))
        joined_relations = [
            relation
            for relation in relations
            if _text(relation.get("relation_type")) in relation_types
            and invariant_ref in {
                _text(relation.get("from_ref")),
                _text(relation.get("to_ref")),
            }
            and _text(relation.get("operation_ref")) in operations_by_id
        ]
        if not joined_relations:
            _op_ids_from_inv = {
                _text(ref) for ref in _list(inv.get("operation_refs"))
                if _text(ref) and _text(ref) in operations_by_id
            }
            if _op_ids_from_inv:
                for oid in _op_ids_from_inv:
                    joined_relations.append({"operation_ref": oid, "relation_type": "observes"})
            if not joined_relations and family != "state":
                # ── Phase 1: entity-co-reference fallback ──
                # When no explicit relation or operation_refs exist, try to
                # bind the invariant to operations that reference the same
                # entity declared in the invariant's expression operands.
                #
                # DELIBERATELY EXCLUDED for the state family: a state-machine
                # invariant (CANCELLED -> PAID forbidden) must be tested through
                # the operation that performs that transition, never through the
                # entity's create endpoint. Binding it to POST /orders compiled
                # 36 state obligations against the create operation, executed
                # them, and then failed every one at cleanup with a misleading
                # CLEANUP_RECEIPT_FAILED. Without a declared transition
                # operation the obligation stays BLOCKED_MISSING_OPERATION —
                # visible and countable, not executed against the wrong write.
                _inv_entity_refs: set[str] = set()
                for _operand in _list(expr.get("operands")):
                    _ent = _text(_dict(_operand).get("entity_ref"))
                    if _ent:
                        _inv_entity_refs.add(_ent)
                # Also check top-level entity_ref on the invariant itself
                _top_ent = _text(inv.get("entity_ref"))
                if _top_ent:
                    _inv_entity_refs.add(_top_ent)
                # Entity-id → name resolution: invariant operands reference the
                # entity NODE id (bir_c62542c74b63e71f) while operations carry
                # the entity NAME (inventory) in entity_refs — two namespaces
                # that never intersect. Resolve node ids to their declared
                # names so co-reference can match.
                _entity_name_by_id = {
                    _text(entity.get("id")): _text(
                        entity.get("name") or entity.get("entity_ref") or ""
                    )
                    for entity in _list(ir.get("entities"))
                    if isinstance(entity, dict) and _text(entity.get("id"))
                }
                _inv_entity_names = {
                    _entity_name_by_id.get(_ent) or _ent
                    for _ent in _inv_entity_refs
                }
                _inv_entity_refs |= {name for name in _inv_entity_names if name}
                # V1.4.0: field-based dual-signal binding ──
                # When invariant carries field_ids, prefer operations whose
                # schema contains those fields (Entity + Field = dual signal).
                _inv_field_ids: set[str] = {
                    _text(f).lower() for f in _list(inv.get("field_ids"))
                    if _text(f)
                }

                def _op_schema_fields(op: dict) -> set[str]:
                    """Collect field names from operation request/response schema."""
                    fields: set[str] = set()
                    for schema_key in ("request_schema", "response_schema"):
                        schema = _dict(op.get(schema_key))
                        props = _dict(schema.get("properties"))
                        fields.update(k.lower() for k in props)
                        # Also nested content.application/json.schema.properties
                        content = _dict(schema.get("content"))
                        json_media = _dict(content.get("application/json"))
                        nested_props = _dict(_dict(json_media.get("schema")).get("properties"))
                        fields.update(k.lower() for k in nested_props)
                    return fields

                if _inv_entity_refs or _inv_field_ids:
                    for _cand_op_id, _cand_op in operations_by_id.items():
                        _op_ents = {
                            _text(e) for e in _list(_cand_op.get("entity_refs"))
                            if _text(e)
                        }
                        _entity_match = bool(_op_ents & _inv_entity_refs) if _inv_entity_refs else False
                        _field_match = False
                        if _inv_field_ids:
                            _op_fields = _op_schema_fields(_cand_op)
                            _field_match = bool(_op_fields & _inv_field_ids)
                        # Dual-signal: entity + field → high confidence
                        if _entity_match and _field_match:
                            joined_relations.append({
                                "operation_ref": _cand_op_id,
                                "relation_type": "observes",
                                "derivation": "field-co-reference",
                                "confidence": 0.7,
                            })
                        elif _entity_match:
                            joined_relations.append({
                                "operation_ref": _cand_op_id,
                                "relation_type": "observes",
                                "derivation": "entity-co-reference",
                                "confidence": 0.4,
                            })
                        elif _field_match and _inv_field_ids:
                            # Field-only signal (no entity declared) — moderate
                            joined_relations.append({
                                "operation_ref": _cand_op_id,
                                "relation_type": "observes",
                                "derivation": "field-schema-match",
                                "confidence": 0.5,
                            })
                if not joined_relations:
                    coverage_gaps.append(_compile_gap(
                        subject_ref=invariant_ref,
                        relation_types=relation_types,
                    ))
                    continue
            elif not joined_relations:
                # State family without any declared transition operation: stay
                # BLOCKED_MISSING_OPERATION (visible gap) instead of falling
                # through with an empty relation set.
                coverage_gaps.append(_compile_gap(
                    subject_ref=invariant_ref,
                    relation_types={"transitions"},
                ))
                continue
        relations_by_operation: dict[str, list[dict[str, Any]]] = {}
        for relation in joined_relations:
            relations_by_operation.setdefault(_text(relation.get("operation_ref")), []).append(relation)
        for operation_ref, operation_relations in relations_by_operation.items():
            op = operations_by_id[operation_ref]
            explicit_body_validation = family == "validation" and any(
                token in kind
                for token in (
                    "valid",
                    "schema",
                    "type",
                    "required",
                    "format",
                    "constraint",
                    "校验",
                    "验证",
                )
            )
            # Entity-eligibility rules (优惠券状态必须为 ACTIVE → status,
            # 必须在有效期内 → expires_at, 必须满足最低订单金额 →
            # min_order_amount) carry ENTITY-SCOPED operands; a decision INPUT
            # surface (validate/check/use/claim/simulate — 校验/验证/使用/领取/
            # 模拟/试算 on POST/PUT/PATCH) decides an entity's eligibility and
            # echoes the decision in its response. For such operations the
            # response IS the effect, so the rule is decidable there even when
            # the operation is read-like (read_write=read). The
            # explicit-body-validation drop exists for body-schema rules
            # (format/required/type) on reads that have no body to validate;
            # entity-eligibility rules on their entity's own decision surface
            # are exactly the cases the drop must not swallow — otherwise the
            # defect class (a decision endpoint accepting a violating entity)
            # stays invisible. Generic vocabulary, never industry terms.
            _entity_eligibility_rule = any(
                isinstance(_operand, dict)
                and (_text(_operand.get("entity_ref")) or _text(_operand.get("field")))
                for _operand in _list(expr.get("operands"))
            )
            if explicit_body_validation and _text(
                op.get("read_write") or op.get("side_effect_class")
            ) != "write":
                if not (_entity_eligibility_rule and _decision_input_surface(op)):
                    coverage_gaps.append(_compile_gap(
                        subject_ref=invariant_ref,
                        relation_types=relation_types,
                    ))
                    continue
            template_by_family = {
                "idempotency": "idempotent_effect_cardinality",
                "concurrency": "concurrent_final_invariant",
            }
            observers_by_family = {
                "idempotency": ["business_effect", "http_response"],
                "concurrency": ["final_state", "barrier_timeline"],
                "conservation": ["typed_assertion", "source_invariant", "entity_state"],
                "validation": ["http_response"],
                "state": ["entity_state", "typed_assertion", "source_invariant"],
            }
            property_spec = {
                "template": template_by_family.get(family, f"invariant_{family}"),
                "invariant_ref": invariant_ref,
                "expression": expr,
                "operation_ref": operation_ref,
                "operation_path_prefix": _operation_path_prefix(op),
            }
            # Subject-entity binding identity (用户端不展示下架商品 → product):
            # carried through so the validation protocol can resolve the
            # entity's status-carrying list read for state-violation arms.
            _subject_entities = [
                _text(value)
                for value in _list(inv.get("subject_entity_refs"))
                if _text(value)
            ]
            if _subject_entities:
                property_spec["subject_entity_refs"] = _subject_entities
            # V1.6.1: Rule Runtime Payload — preserve rule identity + fields through
            # obligation → experiment without a parallel rule engine.
            _field_ids = [
                _text(fid)
                for fid in _list(inv.get("field_ids"))
                if _text(fid)
            ]
            if not _field_ids:
                for _op in _list(_dict(expr).get("operands")):
                    if isinstance(_op, dict):
                        _fid = _text(_op.get("field_id") or _op.get("field"))
                        if _fid:
                            _field_ids.append(_fid)
            for _term in _list(_dict(_dict(expr).get("equation")).get("terms") or []):
                if isinstance(_term, dict):
                    _fid = _text(_term.get("field_id") or _term.get("field"))
                    if _fid:
                        _field_ids.append(_fid)
                elif _text(_term):
                    _field_ids.append(_text(_term))
            _field_ids = list(dict.fromkeys(_field_ids))
            property_spec["field_rule_binding"] = {
                "rule_id": invariant_ref,
                "rule_fingerprint": _text(inv.get("fingerprint") or inv.get("id")),
                "rule_type": family,
                "required_field_ids": _field_ids,
                "typed_expression": expr,
                "operation_id": operation_ref,
            }
            if family == "state":
                # Lift forbidden/allowed transition endpoints onto property for compile gates.
                for _op in _list(_dict(expr).get("operands")):
                    if not isinstance(_op, dict):
                        continue
                    if _text(_op.get("from_state")) and not _text(property_spec.get("from_state")):
                        property_spec["from_state"] = _text(_op.get("from_state"))
                    if _text(_op.get("to_state")) and not _text(property_spec.get("to_state")):
                        property_spec["to_state"] = _text(_op.get("to_state"))
                # State-field authority for the precondition freeze: resolve
                # the entity's declared state field (status/state/…) so every
                # establishment step binds the same field the assertion reads.
                _inv_entity_ref = ""
                for _op in _list(_dict(expr).get("operands")):
                    if isinstance(_op, dict) and _text(_op.get("entity_ref")):
                        _inv_entity_ref = _text(_op.get("entity_ref"))
                        break
                if not _inv_entity_ref:
                    _inv_entity_ref = _text(inv.get("entity_ref"))
                if _inv_entity_ref and not _text(property_spec.get("state_field")):
                    property_spec["state_field"] = _entity_state_field(
                        entities, _inv_entity_ref
                    )
            if family == "idempotency":
                property_spec.update({
                    "compare": "business_effect_not_http_status",
                    # Replay-window contract: the repeated input must add
                    # zero NEW business effect (the observer measures the
                    # treatment window). Mirrors the assertion contract so
                    # the evaluator's property fallback can never resurrect
                    # the old aggregate-window expectation.
                    "expected_effect_count": 0,
                })
            elif family == "concurrency":
                property_spec["insufficient_signal"] = "dual_2xx_alone"
            permit_relations = _relations_for_operation(relations, operation_ref, {"permits"})
            for relation in permit_relations:
                note_unbound_actor_relation(relation, operation_ref)
            permitted_actor_refs = sorted({
                _relation_actor_ref(relation)
                for relation in permit_relations
                if _relation_actor_ref(relation) in active_actors_by_id
            })
            for actor_ref in permitted_actor_refs or [""]:
                actor = active_actors_by_id.get(actor_ref) or {}
                actor_relations = [
                    relation
                    for relation in permit_relations
                    if _relation_actor_ref(relation) == actor_ref
                ]
                actor_property = dict(property_spec)
                if actor_ref:
                    actor_property["actor_ref"] = actor_ref
                obligations.append(make_obligation(
                    # Pass the declared family through. make_obligation resolves it
                    # via the registry and records the declared value plus a reason
                    # code; pre-coercing to "validation" here discarded both.
                    risk_family=family,
                    subject_refs=[
                        invariant_ref,
                        operation_ref,
                        *([actor_ref] if actor_ref else []),
                    ],
                    property_spec=actor_property,
                    required_actors=[actor_ref] if actor_ref else [],
                    required_operations=[operation_ref],
                    required_observers=observers_by_family.get(
                        family,
                        ["typed_assertion", "source_invariant"],
                    ),
                    cleanup_requirement=_cleanup_requirement(op, operations, relations),
                    source_refs=_combined_source_refs(
                        inv,
                        op,
                        actor,
                        *operation_relations,
                        *actor_relations,
                    ),
                    relation_refs=sorted({
                        _text(relation.get("id"))
                        for relation in [*operation_relations, *actor_relations]
                        if _text(relation.get("id"))
                    }),
                    confidence=min(
                        float(inv.get("confidence") or 0.6),
                        float(op.get("confidence") or 0.7),
                        float(actor.get("confidence") or 0.7) if actor_ref else 0.7,
                        # Phase 1: degrade confidence for entity-co-reference bindings
                        *[
                            float(r.get("confidence"))
                            for r in operation_relations
                            if isinstance(r.get("confidence"), (int, float))
                            and _text(r.get("derivation")) == "entity-co-reference"
                        ] or [1.0],
                    ),
                ))

    # Entity mutation templates require an explicit operation/entity relation.
    entity_relation_types = {"produces", "consumes", "transitions", "scopes"}
    write_operation_ids = {_text(op.get("id")) for op in write_ops}
    for ent in entities:
        entity_ref = _text(ent.get("id"))
        joined_relations = [
            relation
            for relation in relations
            if _text(relation.get("relation_type")) in entity_relation_types
            and entity_ref in {
                _text(relation.get("from_ref")),
                _text(relation.get("to_ref")),
            }
            and _text(relation.get("operation_ref")) in write_operation_ids
        ]
        if not joined_relations:
            coverage_gaps.append(_compile_gap(
                subject_ref=entity_ref,
                relation_types=entity_relation_types,
            ))
            continue
        relations_by_operation: dict[str, list[dict[str, Any]]] = {}
        for relation in joined_relations:
            relations_by_operation.setdefault(_text(relation.get("operation_ref")), []).append(relation)
        for operation_ref, operation_relations in relations_by_operation.items():
            op = operations_by_id[operation_ref]
            permit_relations = _relations_for_operation(relations, operation_ref, {"permits"})
            for relation in permit_relations:
                note_unbound_actor_relation(relation, operation_ref)
            permitted_actor_refs = sorted({
                _relation_actor_ref(relation)
                for relation in permit_relations
                if _relation_actor_ref(relation) in active_actors_by_id
            })
            if not permitted_actor_refs:
                if not permit_relations:
                    coverage_gaps.append(_compile_gap(
                        subject_ref=operation_ref,
                        relation_types={"permits"},
                    ))
                continue
            actor_ref = permitted_actor_refs[0]
            actor = active_actors_by_id[actor_ref]
            actor_relations = [
                relation
                for relation in permit_relations
                if _relation_actor_ref(relation) == actor_ref
            ]
            obligations.append(make_obligation(
                risk_family="validation",
                subject_refs=[entity_ref, operation_ref, actor_ref],
                property_spec={
                    "template": "single_dimension_mutation",
                    "entity_ref": entity_ref,
                    "operation_ref": operation_ref,
                    "actor_ref": actor_ref,
                    "operation_path_prefix": _operation_path_prefix(op),
                    "require_control_success": True,
                },
                required_actors=[actor_ref],
                required_operations=[operation_ref],
                required_observers=["http_response", "entity_state"],
                cleanup_requirement=_cleanup_requirement(op, operations, relations, required=True),
                source_refs=_combined_source_refs(ent, op, actor, *operation_relations, *actor_relations),
                relation_refs=sorted({
                    _text(relation.get("id"))
                    for relation in [*operation_relations, *actor_relations]
                    if _text(relation.get("id"))
                }),
                confidence=min(
                    float(ent.get("confidence") or 0.6),
                    float(op.get("confidence") or 0.7),
                    float(actor.get("confidence") or 0.7),
                ),
            ))

    # ── Persistence integrity: source-declared table + enumeration → DB observation ──
    #
    # Link 1 of the four-link chain for database-level defect classes. An entity that
    # carries a source-declared storage table, a canonical field whose enumeration the
    # source declared, and a produces/observes relation to a real operation compiles
    # into a read-only persistence observation judged against that enumeration. Nothing
    # is inferred: no table name, no field name and no allowed value enter this
    # obligation except what the enterprise material declared into the IR.
    #
    # The surface must be installed (adapter ``db_sql`` customer-declared) and root /
    # project provided, otherwise the branch records a visible coverage gap rather than
    # compiling an obligation whose observer could never fire.
    if root and project:
        from .test_obligation import canonical_risk_families

        if "persistence_integrity" in canonical_risk_families():
            from .persistence_observer import OBSERVER_ID as PERSISTENCE_OBSERVER_ID

            persistence_relation_types = {"produces", "observes"}
            for ent in entities:
                entity_ref = _text(ent.get("id"))
                entity_table = _text(ent.get("table"))
                if not entity_ref or not entity_table:
                    continue
                persistence_relations = [
                    relation
                    for relation in relations
                    if _text(relation.get("relation_type")) in persistence_relation_types
                    and entity_ref in {
                        _text(relation.get("from_ref")),
                        _text(relation.get("to_ref")),
                    }
                    and _text(relation.get("operation_ref")) in operations_by_id
                ]
                if not persistence_relations:
                    coverage_gaps.append(_compile_gap(
                        subject_ref=entity_ref,
                        relation_types=persistence_relation_types,
                    ))
                    continue
                raw_field_rows = _list(ent.get("fields"))
                canonical_field_rows = [
                    row
                    for row in raw_field_rows
                    if isinstance(row, dict)
                ]
                enum_fields = [
                    row
                    for row in canonical_field_rows
                    if _text(row.get("name")) and _list(row.get("enum_values"))
                ]
                # Source-declared numeric bounds (OpenAPI minimum/maximum or field
                # dictionary min/max). A bound with no declaration never compiles:
                # the evaluator refuses PERSISTED_BOUND_NOT_DECLARED.
                bounded_fields = [
                    row
                    for row in canonical_field_rows
                    if _text(row.get("name"))
                    and (
                        row.get("min_value") is not None
                        or row.get("max_value") is not None
                    )
                ]
                declared_field_names = [
                    _text(row.get("name"))
                    for row in canonical_field_rows
                    if _text(row.get("name"))
                    and _list(row.get("database_bindings"))
                ] or [
                    _text(row.get("name"))
                    for row in canonical_field_rows
                    if _text(row.get("name"))
                ] or [
                    _text(name)
                    for name in raw_field_rows
                    if _text(name)
                ]
                if (not enum_fields and not bounded_fields) or not declared_field_names:
                    coverage_gaps.append(_compile_gap(
                        subject_ref=entity_ref,
                        relation_types={"enum_values", "field_bounds"},
                    ))
                    continue
                for relation in persistence_relations:
                    operation_ref = _text(relation.get("operation_ref"))
                    op = operations_by_id.get(operation_ref) or {}
                    for field_row in enum_fields:
                        field_name = _text(field_row.get("name"))
                        enum_values = [
                            _text(value)
                            for value in _list(field_row.get("enum_values"))
                            if _text(value)
                        ]
                        if not field_name or not enum_values:
                            continue
                        obligations.append(make_obligation(
                            risk_family="persistence_integrity",
                            subject_refs=[entity_ref, operation_ref, field_name],
                            property_spec={
                                "template": "persistence_state_enumeration",
                                "persistence_root": _text(root),
                                "project": _text(project),
                                "persistence_table": entity_table,
                                "persistence_fields": declared_field_names,
                                "persistence_state_field": field_name,
                                "persistence_allowed_states": enum_values,
                                "operation_ref": operation_ref,
                                "operation_path_prefix": _operation_path_prefix(op),
                            },
                            required_operations=[operation_ref] if operation_ref else [],
                            required_observers=["http_response", PERSISTENCE_OBSERVER_ID],
                            cleanup_requirement={
                                "required": False,
                                "mode": "read_only_persistence_observation",
                            },
                            source_refs=_combined_source_refs(
                                ent,
                                op,
                                relation,
                                field_row,
                            ),
                            relation_refs=[_text(relation.get("id"))]
                            if _text(relation.get("id"))
                            else [],
                            confidence=min(
                                float(ent.get("confidence") or 0.6),
                                float(op.get("confidence") or 0.7),
                                float(field_row.get("confidence") or 0.6),
                            ),
                        ))
                    for field_row in bounded_fields:
                        field_name = _text(field_row.get("name"))
                        if not field_name:
                            continue
                        bound_property: dict[str, Any] = {
                            "template": "persistence_field_bound",
                            "persistence_root": _text(root),
                            "project": _text(project),
                            "persistence_table": entity_table,
                            "persistence_fields": declared_field_names,
                            "persistence_bounded_field": field_name,
                            "operation_ref": operation_ref,
                            "operation_path_prefix": _operation_path_prefix(op),
                        }
                        if field_row.get("min_value") is not None:
                            bound_property["persistence_min"] = field_row["min_value"]
                        if field_row.get("max_value") is not None:
                            bound_property["persistence_max"] = field_row["max_value"]
                        obligations.append(make_obligation(
                            risk_family="persistence_integrity",
                            subject_refs=[entity_ref, operation_ref, field_name],
                            property_spec=bound_property,
                            required_operations=[operation_ref] if operation_ref else [],
                            required_observers=["http_response", PERSISTENCE_OBSERVER_ID],
                            cleanup_requirement={
                                "required": False,
                                "mode": "read_only_persistence_observation",
                            },
                            source_refs=_combined_source_refs(
                                ent,
                                op,
                                relation,
                                field_row,
                            ),
                            relation_refs=[_text(relation.get("id"))]
                            if _text(relation.get("id"))
                            else [],
                            confidence=min(
                                float(ent.get("confidence") or 0.6),
                                float(op.get("confidence") or 0.7),
                                float(field_row.get("confidence") or 0.6),
                            ),
                        ))

    deduped = dedupe_obligations(
        _seed_obligation_fact_refs(obligations, invariants, relations)
    )
    return {
        "schema_version": "qualibug.obligation-compile.v1",
        "behavior_ir_model_id": _text(ir.get("model_id")),
        "obligation_count": len(deduped),
        "by_family": {
            family: sum(1 for item in deduped if item.get("risk_family") == family)
            for family in RISK_FAMILIES
        },
        "obligations": deduped,
        "coverage_gaps": coverage_gaps,
    }
