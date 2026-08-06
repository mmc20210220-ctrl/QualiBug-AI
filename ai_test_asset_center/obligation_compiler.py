"""Add source-grounded privacy field obligations beside actor-pair tests."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import obligation_compiler_privacy_pair_base as _pair
from .obligation_compiler_privacy_pair_base import *  # noqa: F401,F403
from .obligation_compiler_sod import compile_sod_obligations


_original_compile = _pair._original_compile


_ABSENT_POLICIES = {
    "absent", "exclude", "forbid", "forbidden", "must_hide",
    "must_not_expose", "must_not_return", "not_expose", "omit",
    "prohibit", "redact_remove",
}
_MASK_POLICIES = {
    "mask", "masked", "must_mask", "must_redact", "redact", "redacted",
}
_ABSENT_MARKERS = (
    "must not expose", "must not return", "must not include",
    "shall not expose", "shall not return", "不得暴露", "不得返回",
    "不应返回", "不可返回", "禁止返回", "不能返回",
)
_MASK_MARKERS = (
    "must mask", "must be masked", "must redact", "redacted", "masked",
    "脱敏", "掩码", "打码",
)


def __getattr__(name: str) -> Any:
    return getattr(_pair, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _policy_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _text(value).lower()).strip("_")


def _path_tokens(value: Any) -> tuple[str | int, ...]:
    raw = _text(value)
    if raw.startswith("$"):
        raw = raw[1:]
    raw = raw.lstrip(".")
    if not raw or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_-]*(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[\d+\]))*",
        raw,
    ):
        return ()
    tokens: list[str | int] = []
    for name, index in re.findall(
        r"(?:^|\.)([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]",
        raw,
    ):
        tokens.append(name if name else int(index))
    return tuple(tokens)


def _json_path(tokens: tuple[str | int, ...]) -> str:
    path = "$"
    for token in tokens:
        path += f"[{token}]" if isinstance(token, int) else f".{token}"
    return path


def _privacy_policy(explicit: Any, fallback: Any, raw_text: Any) -> str:
    for value in (explicit, fallback):
        token = _policy_token(value)
        if token in _ABSENT_POLICIES:
            return "absent"
        if token in _MASK_POLICIES:
            return "masked"
    raw = _text(raw_text).lower()
    if any(marker in raw for marker in _ABSENT_MARKERS):
        return "absent"
    if any(marker in raw for marker in _MASK_MARKERS):
        return "masked"
    return ""


def _privacy_targets(property_spec: dict[str, Any]) -> list[dict[str, Any]]:
    prop = _dict(property_spec)
    expression = _dict(prop.get("expression"))
    default_policy = _privacy_policy(
        prop.get("privacy_policy"),
        expression.get("operator"),
        expression.get("raw"),
    )
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operand in _list(expression.get("operands")):
        row = _dict(operand)
        field = operand if isinstance(operand, str) else (
            row.get("field") or row.get("path") or row.get("json_path")
            or row.get("field_ref") or row.get("name")
        )
        tokens = _path_tokens(field)
        if not tokens:
            continue
        policy = _privacy_policy(
            row.get("policy") or row.get("operator") or row.get("mode"),
            default_policy,
            expression.get("raw"),
        ) or default_policy
        pattern = _text(
            row.get("mask_pattern") or row.get("expected_pattern")
            or row.get("regex") or row.get("pattern")
        )
        if policy == "masked":
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error:
                continue
        if policy not in {"absent", "masked"}:
            continue
        target = {
            "privacy_policy": policy,
            "field_tokens": list(tokens),
            "json_path": _json_path(tokens),
            "mask_pattern": pattern,
            "allow_absent": row.get("allow_absent") is True,
        }
        key = json.dumps(target, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            targets.append(target)
    return targets


def _actor_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    return bool(secret_ref and not secret_ref.lower().startswith("secret_ref:actor:"))


def _field_actor_refs(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    operation_ref: str,
) -> list[str]:
    actors = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    explicit = [
        _text(value) for value in _list(obligation.get("required_actors"))
        if _text(value) in actors and _actor_executable(actors[_text(value)])
    ]
    if explicit:
        return list(dict.fromkeys(explicit))
    return sorted({
        _text(row.get("actor_ref") or row.get("from_ref"))
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict)
        and _text(row.get("relation_type")) == "permits"
        and _text(row.get("operation_ref")) == operation_ref
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
        and _text(row.get("actor_ref") or row.get("from_ref")) in actors
        and _actor_executable(actors[_text(row.get("actor_ref") or row.get("from_ref"))])
    })


def _privacy_gap(obligation: dict[str, Any], operation_ref: str, reason: str) -> dict[str, Any]:
    invariant_ref = _text(_dict(obligation.get("property")).get("invariant_ref"))
    material = "|".join(["privacy_field", invariant_ref, operation_ref, reason])
    return {
        "id": "compile_gap_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16],
        "code": "BLOCKED_PRIVACY_FIELD_POLICY",
        "gap_type": "privacy_field_policy_not_executable",
        "subject_ref": invariant_ref or operation_ref,
        "operation_ref": operation_ref,
        "risk_family": "privacy",
        "reason": reason,
        "description": "Privacy rule lacks one explicit executable field policy",
        "status": "unsupported",
        "source_refs": [
            dict(item) for item in _list(obligation.get("source_refs"))
            if isinstance(item, dict)
        ][:5],
    }


def _field_variants(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prop = _dict(obligation.get("property"))
    operation_ref = next((
        _text(value) for value in _list(obligation.get("required_operations"))
        if _text(value)
    ), "") or _text(prop.get("operation_ref"))
    operations = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    if _text(_dict(operations.get(operation_ref)).get("method")).upper() != "GET":
        return [], []
    targets = _privacy_targets(prop)
    if not targets:
        return [], []
    actor_refs = _field_actor_refs(
        obligation,
        behavior_ir=behavior_ir,
        operation_ref=operation_ref,
    )
    if not actor_refs:
        return [], [_privacy_gap(obligation, operation_ref, "privacy_runtime_actor_missing")]

    variants: list[dict[str, Any]] = []
    for actor_ref in actor_refs:
        for target in targets:
            variant_property = dict(prop)
            variant_property.update(target)
            variant_property.update({
                "actor_ref": actor_ref,
                "privacy_test_mode": "field_policy",
                "expanded_from_obligation_id": _text(obligation.get("obligation_id")),
                "require_meaningful_response": True,
            })
            variants.append(_pair._base.make_obligation(
                risk_family="privacy",
                subject_refs=[operation_ref, actor_ref, target["json_path"]],
                property_spec=variant_property,
                required_actors=[actor_ref],
                required_operations=[operation_ref],
                required_observers=["http_response", "source_invariant"],
                cleanup_requirement={"required": False},
                source_refs=[
                    dict(item) for item in _list(obligation.get("source_refs"))
                    if isinstance(item, dict)
                ],
                relation_refs=[
                    _text(value) for value in _list(obligation.get("relation_refs"))
                    if _text(value)
                ],
                confidence=float(obligation.get("confidence") or 0.5),
            ))
    return variants, []


def _with_source_declared_ownership_relations(behavior_ir: dict[str, Any]) -> dict[str, Any]:
    """Materialize explicit operation-local own-scope contracts as IR relations.

    The operation must be a collection read, explicitly state caller ownership,
    and declare an ownership identity parameter. This is source normalization,
    not an inferred permission fallback: the existing isolation compiler and
    Oracle remain the only obligation and verdict authorities.
    """

    ir = dict(_dict(behavior_ir))
    operations = _pair._base._accepted(_list(ir.get("operations")))
    relations = [
        dict(row)
        for row in _list(ir.get("relations"))
        if isinstance(row, dict)
    ]
    existing_owned_operations = {
        _text(row.get("operation_ref"))
        for row in relations
        if _text(row.get("relation_type")) == "owns"
        and _text(row.get("status")) not in {"conflicting", "unsupported"}
    }
    actors = _pair._base._active_actors(
        _pair._base._accepted(_list(ir.get("actors")))
    )
    changed = False
    for operation in operations:
        operation_ref = _text(operation.get("id"))
        path = _text(operation.get("path") or operation.get("raw_path"))
        is_read = _text(
            operation.get("read_write") or operation.get("side_effect_class")
        ) != "write"
        source_declares_own_scope = bool(
            is_read
            and operation_ref
            and operation_ref not in existing_owned_operations
            and not _pair._base._path_has_resource_placeholder(path)
            and _pair._base._operation_declares_ownership_language(operation)
            and _pair._base._ownership_params_declared_on_operation(operation)
        )
        if not source_declares_own_scope:
            continue
        source_refs = _pair._base._combined_source_refs(operation)
        for actor in actors:
            actor_ref = _text(actor.get("id"))
            if not actor_ref or not _text(actor.get("account_ref")):
                continue
            relation_material = "|".join([
                "source_declared_owns", actor_ref, operation_ref,
            ])
            relations.append({
                "id": "rel_" + hashlib.sha256(
                    relation_material.encode("utf-8")
                ).hexdigest()[:20],
                "relation_type": "owns",
                "from_ref": actor_ref,
                "to_ref": operation_ref,
                "operation_ref": operation_ref,
                "actor_ref": actor_ref,
                "preconditions": [{"scope": "own"}],
                "effects": [],
                "permission_decision": "",
                "source_relationship_ref": "",
                "source_refs": source_refs,
                "confidence": min(
                    float(operation.get("confidence") or 0.7),
                    float(actor.get("confidence") or 0.7),
                ),
                "derivation": "explicit",
                "status": "accepted",
                "scope": "own",
            })
            changed = True
    if changed:
        ir["relations"] = relations
    return ir


def compile_obligations_from_behavior_ir(
    behavior_ir: dict[str, Any],
    *,
    root: str = "",
    project: str = "",
) -> dict[str, Any]:
    normalized_ir = _with_source_declared_ownership_relations(behavior_ir)
    baseline_compile = _original_compile
    paired_result = _pair.compile_obligations_from_behavior_ir(
        normalized_ir,
        base_compile=baseline_compile,
        root=root,
        project=project,
    )
    original_result = baseline_compile(normalized_ir, root=root, project=project)
    additions: list[dict[str, Any]] = []
    field_gaps: list[dict[str, Any]] = []
    expanded_keys: set[tuple[str, str]] = set()
    for obligation in _list(original_result.get("obligations")):
        if not isinstance(obligation, dict) or _text(obligation.get("risk_family")) != "privacy":
            continue
        variants, gaps = _field_variants(obligation, behavior_ir=behavior_ir)
        additions.extend(variants)
        field_gaps.extend(gaps)
        if variants:
            prop = _dict(obligation.get("property"))
            operation_ref = next((
                _text(value) for value in _list(obligation.get("required_operations"))
                if _text(value)
            ), "") or _text(prop.get("operation_ref"))
            expanded_keys.add((_text(prop.get("invariant_ref")), operation_ref))

    output = dict(paired_result)
    sod_obligations, sod_gaps = compile_sod_obligations(normalized_ir)
    obligations = _pair._base.dedupe_obligations([
        *[dict(item) for item in _list(output.get("obligations")) if isinstance(item, dict)],
        *additions,
        *sod_obligations,
    ])
    gaps = []
    for gap in _list(output.get("coverage_gaps")):
        if not isinstance(gap, dict):
            continue
        key = (_text(gap.get("subject_ref")), _text(gap.get("operation_ref")))
        if _text(gap.get("code")) == "BLOCKED_MISSING_ACTOR_PAIR" and key in expanded_keys:
            continue
        gaps.append(dict(gap))
    existing_gap_ids = {_text(item.get("id")) for item in gaps}
    for gap in [*field_gaps, *sod_gaps]:
        if _text(gap.get("id")) not in existing_gap_ids:
            gaps.append(gap)
            existing_gap_ids.add(_text(gap.get("id")))
    output["obligations"] = obligations
    output["obligation_count"] = len(obligations)
    output["coverage_gaps"] = gaps
    output["by_family"] = {
        family: sum(1 for item in obligations if _text(item.get("risk_family")) == family)
        for family in _pair._base.RISK_FAMILIES
    }
    return output
