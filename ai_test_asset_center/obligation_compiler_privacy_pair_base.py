"""Source-grounded actor pairing for privacy and visibility obligations.

The baseline obligation compiler remains unchanged. This facade replaces
single-actor privacy/visibility obligations with explicit permitted/denied actor
pairs from Behavior IR. Without two distinct runtime actors, a control/treatment
experiment cannot exercise disclosure boundaries and is recorded as a coverage
gap instead of a misleading executable obligation.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
from typing import Any

from . import obligation_compiler_base as _base
from .obligation_compiler_base import *  # noqa: F401,F403


_original_compile = _base.compile_obligations_from_behavior_ir
_PAIR_FAMILIES = frozenset({"privacy", "visibility"})

# Response-side constraint signals — the same generic business syntax the
# protocol layer uses for response_field_absent (导出结果禁止包含 password):
# a rule carrying one of these constrains RESPONSE content, so its obligation
# is a single-arm field check that must survive actor pairing. Kept in sync
# with experiment_protocols_base._RESPONSE_SIDE_SIGNALS (single source of
# vocabulary, mirrored here to avoid an import cycle).
_RESPONSE_SIDE_SIGNALS = ("导出", "结果", "响应", "返回", "输出")

# P0-E phase-3: CJK privacy policy markers (从 obligation_compiler 的
# _ABSENT_MARKERS/_MASK_MARKERS 中文项派生——SSOT 单一词表，不重复定义)
# 是 legacy 候选提示；frame 通道尚无隐私策略粒度（登记扩展点），计数使
# 降级可观测。
_CJK_PRIVACY_POLICY_MARKERS = ()


def _count_cjk_privacy_policy_markers(behavior_ir: dict[str, Any]) -> None:
    counts = _base._legacy_fallback_kind_counts(behavior_ir)
    if counts is None:
        return
    global _CJK_PRIVACY_POLICY_MARKERS
    if not _CJK_PRIVACY_POLICY_MARKERS:
        from .obligation_compiler import (
            _ABSENT_MARKERS as _ABSENT_MARKERS_SRC,
            _MASK_MARKERS as _MASK_MARKERS_SRC,
        )

        def _cjk_only(markers: tuple) -> tuple:
            return tuple(
                m for m in markers
                if any("一" <= ch <= "鿿" for ch in m)
            )

        _CJK_PRIVACY_POLICY_MARKERS = (
            _cjk_only(_ABSENT_MARKERS_SRC) + _cjk_only(_MASK_MARKERS_SRC)
        )
    hits = 0
    for inv in _list(behavior_ir.get("invariants")):
        if not isinstance(inv, dict):
            continue
        raw = _text(_dict(inv.get("expression")).get("raw"))
        if any(marker in raw for marker in _CJK_PRIVACY_POLICY_MARKERS):
            hits += 1
    _base._count_legacy_cjk_kind(counts, "PRIVACY_POLICY_CJK_CANDIDATE", hits)


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_pair_gap(
    *,
    family: str,
    invariant_ref: str,
    operation_ref: str,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    material = "|".join([
        "BLOCKED_MISSING_ACTOR_PAIR",
        family,
        invariant_ref,
        operation_ref,
    ])
    return {
        "id": "compile_gap_" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:16],
        "code": "BLOCKED_MISSING_ACTOR_PAIR",
        "subject_ref": invariant_ref or operation_ref,
        "operation_ref": operation_ref,
        "risk_family": family,
        "required_relation_types": ["permits", "denies"],
        "required_binding": "distinct_runtime_control_and_treatment_actors",
        "description": (
            "Privacy/visibility testing requires one source-permitted actor "
            "and one source-denied actor for the same operation"
        ),
        "status": "unsupported",
        "source_refs": [
            dict(item) for item in source_refs if isinstance(item, dict)
        ][:5],
    }


def _pair_obligations(
    result: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    output = dict(_dict(result))
    ir = _dict(behavior_ir)
    actors = _base._active_actors(
        _base._accepted(_list(ir.get("actors")))
    )
    actors_by_id = {
        _text(actor.get("id")): actor
        for actor in actors
        if _text(actor.get("id"))
    }
    relations = _base._accepted(_list(ir.get("relations")))
    operations = {
        _text(operation.get("id")): operation
        for operation in _base._accepted(_list(ir.get("operations")))
        if _text(operation.get("id"))
    }
    invariants = {
        _text(invariant.get("id")): invariant
        for invariant in _base._accepted(_list(ir.get("invariants")))
        if _text(invariant.get("id"))
    }

    paired: list[dict[str, Any]] = []
    gaps = [
        dict(item)
        for item in _list(output.get("coverage_gaps"))
        if isinstance(item, dict)
    ]
    gap_ids = {_text(item.get("id")) for item in gaps}

    for obligation in _list(output.get("obligations")):
        if not isinstance(obligation, dict):
            continue
        family = _text(obligation.get("risk_family"))
        if family not in _PAIR_FAMILIES:
            paired.append(dict(obligation))
            continue
        prop = _dict(obligation.get("property"))
        existing_actors = [
            _text(value)
            for value in _list(obligation.get("required_actors"))
            if _text(value)
        ]
        control_ref = _text(prop.get("control_actor_ref"))
        treatment_ref = _text(prop.get("treatment_actor_ref"))
        if (
            len(set(existing_actors)) >= 2
            and control_ref
            and treatment_ref
            and control_ref != treatment_ref
        ):
            paired.append(dict(obligation))
            continue

        # ── Single-arm privacy field constraints ──
        # A privacy rule constraining RESPONSE content (导出结果禁止包含
        # password / 响应不得返回密钥) is a single-arm field check: any
        # permitted actor's read IS the observation — no treatment actor is
        # needed. Requiring a permits/denies pair here discards the obligation
        # (BLOCKED_MISSING_ACTOR_PAIR) whenever the permission matrix declares
        # only the allowed role, which is exactly the documented contract for
        # admin-only exports. The response-side signal vocabulary (导出/结果/
        # 响应/返回/输出) is the same generic business syntax the protocol
        # layer uses for response_field_absent; keeping it in sync there is
        # the only contract (see experiment_protocols_base._RESPONSE_SIDE_SIGNALS).
        _privacy_expr = _dict(prop.get("expression"))
        _privacy_raw = " ".join(
            _text(value)
            for value in (
                _privacy_expr.get("raw"),
                prop.get("source_intent"),
                prop.get("description"),
            )
            if _text(value)
        )
        if family == "privacy" and any(
            signal in _privacy_raw for signal in _RESPONSE_SIDE_SIGNALS
        ):
            # Field-policy shape: the rule names forbidden response fields
            # (导出结果禁止包含 password) without structured operands, so the
            # field-policy protocol's token requirement must be supplied from
            # the rule's own text. Reuses the protocol layer's response-side
            # extractor (same regex + secret/account concept vocabulary) so the
            # single-arm privacy_field_policy assertion checks the observed
            # body for exactly the forbidden material the rule declared.
            from .experiment_protocols_base import (
                _extract_forbidden_response_fields,
            )

            _forbidden, _family_match = _extract_forbidden_response_fields(
                {"expression": _privacy_expr}
            )
            if _forbidden:
                prop = dict(prop)
                prop.update({
                    "privacy_test_mode": "field_policy",
                    "privacy_policy": "absent",
                    "field_tokens": list(dict.fromkeys(_forbidden)),
                    "match_field_names": True,
                    "privacy_field_source": "response_side_rule_text",
                })
                obligation = {**dict(obligation), "property": prop}
            paired.append(dict(obligation))
            continue

        operation_ref = (
            next(
                (
                    _text(value)
                    for value in _list(obligation.get("required_operations"))
                    if _text(value)
                ),
                "",
            )
            or _text(prop.get("operation_ref"))
        )
        invariant_ref = _text(prop.get("invariant_ref"))
        permit_relations = [
            relation
            for relation in relations
            if _text(relation.get("operation_ref")) == operation_ref
            and _text(relation.get("relation_type")) == "permits"
            and _base._relation_actor_ref(relation) in actors_by_id
        ]
        deny_relations = [
            relation
            for relation in relations
            if _text(relation.get("operation_ref")) == operation_ref
            and _text(relation.get("relation_type")) == "denies"
            and _base._relation_actor_ref(relation) in actors_by_id
        ]
        pairs = [
            (permit, deny)
            for permit in permit_relations
            for deny in deny_relations
            if _base._relation_actor_ref(permit)
            != _base._relation_actor_ref(deny)
        ]
        if not pairs:
            gap = _actor_pair_gap(
                family=family,
                invariant_ref=invariant_ref,
                operation_ref=operation_ref,
                source_refs=[
                    dict(item)
                    for item in _list(obligation.get("source_refs"))
                    if isinstance(item, dict)
                ],
            )
            if gap["id"] not in gap_ids:
                gaps.append(gap)
                gap_ids.add(gap["id"])
            continue

        for permit, deny in pairs:
            allowed_ref = _base._relation_actor_ref(permit)
            denied_ref = _base._relation_actor_ref(deny)
            allowed = actors_by_id[allowed_ref]
            denied = actors_by_id[denied_ref]
            paired_property = dict(prop)
            paired_property.pop("actor_ref", None)
            paired_property.update({
                "template": f"{family}_control_treatment",
                "control_actor_ref": allowed_ref,
                "treatment_actor_ref": denied_ref,
                "operation_ref": operation_ref,
                "require_same_resource": True,
                "actor_pair_source": "permits_denies",
            })
            observer_ids = [
                _text(value)
                for value in _list(obligation.get("required_observers"))
                if _text(value)
            ]
            for observer_id in (
                "http_response",
                "actor_identity",
                "authorization_comparison",
            ):
                if observer_id not in observer_ids:
                    observer_ids.append(observer_id)
            relation_refs = sorted({
                *(
                    _text(value)
                    for value in _list(obligation.get("relation_refs"))
                    if _text(value)
                ),
                _text(permit.get("id")),
                _text(deny.get("id")),
            })
            source_node = {
                "source_refs": [
                    dict(item)
                    for item in _list(obligation.get("source_refs"))
                    if isinstance(item, dict)
                ]
            }
            source_refs = _base._combined_source_refs(
                source_node,
                invariants.get(invariant_ref) or {},
                operations.get(operation_ref) or {},
                allowed,
                denied,
                permit,
                deny,
            )
            confidence = min(
                float(obligation.get("confidence") or 0.5),
                float(allowed.get("confidence") or 0.7),
                float(denied.get("confidence") or 0.7),
                float(permit.get("confidence") or 0.8),
                float(deny.get("confidence") or 0.8),
            )
            paired.append(_base.make_obligation(
                risk_family=family,
                subject_refs=[
                    invariant_ref,
                    operation_ref,
                    allowed_ref,
                    denied_ref,
                ],
                property_spec=paired_property,
                required_actors=[allowed_ref, denied_ref],
                required_operations=[operation_ref],
                required_fixtures=[
                    _text(value)
                    for value in _list(obligation.get("required_fixtures"))
                    if _text(value)
                ],
                required_observers=observer_ids,
                cleanup_requirement=dict(
                    _dict(obligation.get("cleanup_requirement"))
                ),
                source_refs=source_refs,
                relation_refs=relation_refs,
                confidence=confidence,
            ))

    deduped = _base.dedupe_obligations(paired)
    output["obligations"] = deduped
    output["obligation_count"] = len(deduped)
    output["coverage_gaps"] = gaps
    output["by_family"] = {
        family: sum(
            1
            for obligation in deduped
            if _text(obligation.get("risk_family")) == family
        )
        for family in _base.RISK_FAMILIES
    }
    return output


def compile_obligations_from_behavior_ir(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    root: str = "",
    project: str = "",
) -> dict[str, Any]:
    compiler = base_compile or _original_compile
    if root or project:
        # Forwarded only when the caller actually has a workspace identity; a
        # base_compile that predates the parameters still works on plain IR.
        result = _pair_obligations(
            compiler(behavior_ir, root=root, project=project),
            behavior_ir,
        )
    else:
        result = _pair_obligations(
            compiler(behavior_ir),
            behavior_ir,
        )
    _count_cjk_privacy_policy_markers(behavior_ir)
    return result
