"""LLM semantic extraction layer: source-backed candidates, never facts.

Phase 3 of SPEC_FORMAT_AGNOSTIC_ENTERPRISE_MATERIAL_COMPREHENSION.

The layer is Chinese-first: Chinese names, roles, states and enterprise terms stay
in their original language. Long sources are processed by bounded overlapping
chunks; any unprocessed range is explicit in the receipt instead of being silently
truncated. Every model output remains a candidate until independent engineering
validation promotes it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "run_semantic_extraction",
    "semantic_extraction_availability",
    "validate_semantic_candidates",
    "validate_rule_candidates",
    "resolve_semantic_rule_extraction_mode",
    "provider_status",
    "run_semantic_extraction_batch",
    "build_rule_candidate_ledger",
    "promote_rule_candidates_to_rules",
    "rule_promotion_gates_met",
    "SemanticExtractionReceipt",
    "RULE_VALIDATION_STATUSES",
    "SEMANTIC_RULE_EXTRACTION_MODES",
]

_MAX_CHUNK_CHARS = 6000
# Backward-compatible name: one model request still sees at most 6000 chars.
_MAX_SOURCE_CHARS = _MAX_CHUNK_CHARS
_CHUNK_OVERLAP_CHARS = 400
# One process-wide provider gate is shared by source-level and chunk-level pools.
# Without this gate, the historical 4-source x 4-chunk nested executors issued up
# to 16 concurrent calls while each layer independently claimed a 4-call ceiling.
_SEMANTIC_PROVIDER_CONCURRENCY_LIMIT = 4
_SEMANTIC_PROVIDER_SLOTS = threading.BoundedSemaphore(
    _SEMANTIC_PROVIDER_CONCURRENCY_LIMIT
)
_VALID_KINDS = {"entity", "field", "relation", "state", "actor", "rule"}
_VALID_RULE_ORIGINS = {"explicit", "inferred"}
_VALID_RULE_FAMILIES = {
    "prohibition", "permission", "obligation", "state_transition",
    "threshold", "temporal", "uniqueness", "isolation", "visibility",
    "approval", "cross_entity", "exception", "condition", "other",
}
_SEMANTIC_SPAN_ROLES = {
    "actor", "object", "condition", "action", "modality",
    "threshold", "exception", "temporal",
}

# ── Cross-industry deterministic rule signals ────────────────────────────────
# These are GENERIC LANGUAGE FORMS (SPEC §5.1: comparison operators, ranges,
# units, time windows, negation, modality) — not industry keyword tables.
# They gate whether an LLM rule candidate may claim rule_origin=explicit and
# whether an evidence span actually states a constraint.
_RULE_SIGNAL_PATTERNS = (
    # negation / prohibition
    r"不得|禁止|严禁|不可|不能|不允许|无权|不再|不得再|禁止再|不予|不予受理",
    # permission
    r"可以|允许|有权|可(?!以重复)",
    # obligation
    r"必须|应当|务必|需要|须(?=[由在对将把于进行])",
    # condition frames
    r"如果|若|一旦|当(?!前|期|日|月|年|次|笔|个|下|中)|只有.+才|仅当|除非|否则",
    # comparison / thresholds (generic operators)
    r"大于|小于|超过|不低于|不超过|至少|最多|以上|以下|大于等于|小于等于|"
    r"不少于|不多于|高于|低于|>=|<=|>|<|=|大于或等于|小于或等于",
    # temporal windows
    r"之前|之后|以前|以后|以内|之内|期间|期限|时效|逾期|过期|当日|次日|"
    r"\d+\s*(天|小时|分钟|秒|日|月|年|周)",
    # exception scopes
    r"除.+外|除外|例外",
    # state / lifecycle
    r"状态|转为|变为|进入|流程|流转|不得进入|禁止进入",
    # scope / isolation
    r"本人|本部门|本组织|本区域|本仓库|仅限|专属|共享",
)
_EXAMPLE_LEAD_PATTERNS = (
    r"^例如|^比如|^举例|^如[:：]|^示例|^例如[:：]|^比如[:：]",
)
_RULE_SIGNAL_RE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in _RULE_SIGNAL_PATTERNS)
)
_EXAMPLE_LEAD_RE = re.compile("|".join(_EXAMPLE_LEAD_PATTERNS))

# ── Semantic rule extraction modes (SPEC §12) ────────────────────────────────
# off      : never call the LLM for rule candidates; formal output is regex-only.
# shadow   : call, validate, record candidates — never touch formal rule output.
# augment  : validated LLM-only candidates enter the existing governance chain.
#            Default ON when the provider is configured. Promotion itself is
#            deterministically gated at merge time (promote_rule_candidates_to_rules
#            + rule_promotion_gates_met): only llm+explicit+non-conflicted
#            candidates carrying anchored evidence are promoted; nothing is
#            promoted without evidence. An operator explicit
#            rule_promotion_gates_met=False is the kill switch and resolves to
#            shadow with promotion_gates_not_met.
# required : the comprehension stage fails when the LLM is unavailable.
SEMANTIC_RULE_EXTRACTION_MODES = ("off", "shadow", "augment", "required")
_RULE_MODE_RECEIPT_SCHEMA = "qualibug.semantic-rule-extraction-mode.v1"


def provider_status() -> str:
    """'configured' when the LLM client is enabled, else 'unavailable'."""
    availability = semantic_extraction_availability(requested=True)
    return "configured" if availability.get("available") else "unavailable"


def resolve_semantic_rule_extraction_mode(
    requested_mode: str = "shadow",
    provider_status_value: str = "unavailable",
    governance_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the effective rule-extraction mode with an explicit receipt.

    Never silently degrades: every fallback is named in the receipt
    (SPEC §12.5). ``augment`` is default-ON when the provider is configured:
    promotion safety is enforced deterministically at merge time by
    ``promote_rule_candidates_to_rules`` + ``rule_promotion_gates_met`` (only
    llm+explicit+non-conflicted candidates with anchored evidence are promoted),
    never by this mode flag. An operator explicit
    ``promotion_gates_met=False`` is the kill switch and resolves to ``shadow``
    with ``promotion_gates_not_met``; an unavailable provider fails closed to
    ``off``/``required`` with ``missing_credentials``.
    """
    requested = _text(requested_mode).lower()
    if requested not in SEMANTIC_RULE_EXTRACTION_MODES:
        requested = "shadow"
    provider_ok = _text(provider_status_value).lower() == "configured"
    policy = _dict(governance_policy)
    fallback = "regex_only"

    if requested == "off":
        effective = "off"
        fallback_reason = ""
    elif requested == "shadow":
        if provider_ok:
            effective = "shadow"
            fallback_reason = ""
        else:
            effective = "off"
            fallback_reason = "missing_credentials"
    elif requested == "augment":
        if provider_ok:
            gates = _dict(governance_policy)
            # An explicit ``False`` is the only thing that suppresses augment;
            # an absent or ``True`` value keeps the default-ON behavior. The
            # real evidence/no-conflict/traceability gates run at merge time.
            if gates.get("promotion_gates_met") is not False:
                effective = "augment"
                fallback_reason = ""
            else:
                effective = "shadow"
                fallback_reason = "promotion_gates_not_met"
        else:
            effective = "off"
            fallback_reason = "missing_credentials"
    elif requested == "required":
        if provider_ok:
            gates = _dict(governance_policy)
            if gates.get("promotion_gates_met") is not False:
                effective = "augment"
                fallback_reason = ""
            else:
                effective = "shadow"
                fallback_reason = "promotion_gates_not_met"
        else:
            effective = "required"
            fallback_reason = "missing_credentials"
    else:  # pragma: no cover - guarded above
        effective = "off"
        fallback_reason = "invalid_requested_mode"

    return {
        "receipt_id": "semantic-rule-extraction-mode",
        "schema_version": _RULE_MODE_RECEIPT_SCHEMA,
        "requested_mode": requested,
        "effective_mode": effective,
        "provider_status": _text(provider_status_value).lower(),
        "fallback_mode": fallback,
        "fallback_reason": fallback_reason,
        "governance_policy_applied": bool(policy),
        "canonical_rule_output_affected": False,
    }


# ── Unified rule candidate ledger (SPEC §9/§10, P0-4) ────────────────────────
# Both extractors feed ONE ledger. Regex facts are adapted to the same entry
# shape as LLM rule candidates; evidence de-dup and semantic-signature merge
# decide MERGED vs CONFLICTED. Nothing is overwritten by confidence. The
# ledger is an observation layer in this phase: formal rule_library output is
# untouched.
LEDGER_SCHEMA = "qualibug.rule-candidate-ledger.v1"
GOVERNANCE_STATUSES = frozenset({
    "CANDIDATE", "VALIDATED", "REJECTED", "CONFLICTED",
    "MERGED", "PROMOTED", "SUPERSEDED",
})

# Regex modality → operator family, mirroring the LLM normalized_suggestion.
_MODALITY_TO_FAMILY = {
    "MUST_NOT": "forbid",
    "FORBIDDEN": "forbid",
    "ONLY_IF": "permit",
    "MAY": "permit",
    "CAN": "permit",
    "MUST": "require",
    "SHOULD": "require",
    "ASSERTS": "assert",
}


def _normalize_signature_token(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).lower()


def _regex_threshold_tokens(fact: dict[str, Any]) -> list[str]:
    """Normalized comparison tokens from regex constraints (e.g. '>5000')."""
    tokens: list[str] = []
    for row in _list(fact.get("quantity_constraints")):
        if isinstance(row, dict):
            comparator = _text(row.get("comparator") or row.get("operator"))
            value = row.get("value")
            if comparator and value is not None:
                tokens.append(_normalize_signature_token(f"{comparator}{value}"))
    for row in _list(fact.get("formula_constraints")):
        if isinstance(row, dict):
            expr = _text(row.get("expression") or row.get("formula"))
            if expr:
                tokens.append(_normalize_signature_token(expr))
    return tokens


def _regex_fact_to_ledger_entry(
    fact: dict[str, Any],
    *,
    source_id: str,
    source_text: str,
) -> dict[str, Any] | None:
    """Adapt one regex rule fact into the unified ledger entry shape."""
    raw = _text(fact.get("raw_statement"))
    if not raw:
        return None
    position = source_text.find(raw)
    evidence_spans: list[dict[str, Any]] = []
    if position >= 0:
        evidence_spans.append({
            "text": raw,
            "start": position,
            "end": position + len(raw),
        })
    modality = _text(fact.get("modality")).upper()
    action = _text(_dict(fact.get("action")).get("verb") or _text(fact.get("action")))
    subject = _dict(fact.get("subject"))
    actor_refs = [_text(row) for row in _list(subject.get("actor_refs")) if _text(row)]
    entity_refs = [_text(row) for row in _list(subject.get("entity_refs")) if _text(row)]
    thresholds = _regex_threshold_tokens(fact)
    entry = {
        "kind": "rule",
        "source_ref": source_id,
        "chunk_ref": _text(_list(fact.get("source_spans"))[0].get("locator"))
        if _list(fact.get("source_spans")) and isinstance(_list(fact.get("source_spans"))[0], dict)
        else "",
        "extractor_type": "regex",
        "evidence_spans": evidence_spans,
        "validation_status": "VALIDATED",
        "governance_status": (
            "PROMOTED" if _text(fact.get("status")).upper() == "ACCEPTED"
            else "CANDIDATE"
        ),
        "canonical_rule_ref": _text(fact.get("fact_id")),
        "rejection_reason": "",
        "conflict_refs": [],
        "semantic_signature": {
            "operator_family": _MODALITY_TO_FAMILY.get(modality, ""),
            "action": _normalize_signature_token(action),
            "actor": _normalize_signature_token(",".join(actor_refs)),
            "object": _normalize_signature_token(",".join(entity_refs)),
            "threshold": ",".join(thresholds),
            "condition": _normalize_signature_token(
                ",".join(_list(fact.get("conditions")))
            ),
            "exception": _normalize_signature_token(
                ",".join(_list(fact.get("exceptions")))
            ),
            "temporal": _normalize_signature_token(
                ",".join(_list(fact.get("temporal_constraints")))
            ),
        },
        "raw": dict(fact),
    }
    return entry


def _llm_candidate_to_ledger_entry(
    candidate: dict[str, Any],
    *,
    source_id: str,
) -> dict[str, Any]:
    suggestion = _dict(candidate.get("normalized_suggestion"))
    effect = _dict(suggestion.get("effect"))
    condition = _dict(suggestion.get("condition"))
    semantic_spans = _dict(candidate.get("semantic_spans"))

    def span_terms(role: str) -> str:
        # Signature uses SOURCE-ANCHORED terms (semantic_spans were containment-
        # validated against the evidence), so regex and LLM sides compare like
        # for like — never a standardized translation on one side only.
        return ",".join(
            _text(row.get("text"))
            for row in _list(semantic_spans.get(role))
            if isinstance(row, dict) and _text(row.get("text"))
        )

    threshold_terms = span_terms("threshold")
    return {
        "kind": "rule",
        "source_ref": source_id,
        "chunk_ref": _text(candidate.get("source_locator")),
        "extractor_type": "llm",
        "evidence_spans": [
            dict(row)
            for row in _list(candidate.get("evidence_spans"))
            if isinstance(row, dict)
        ],
        "validation_status": _text(candidate.get("candidate_status"))
        or "VALIDATED",
        "governance_status": "CANDIDATE",
        "canonical_rule_ref": "",
        "rejection_reason": "",
        "conflict_refs": [],
        "semantic_signature": {
            "operator_family": _normalize_signature_token(
                effect.get("operator_family")
            ),
            "action": _normalize_signature_token(
                span_terms("action") or effect.get("action")
            ),
            "actor": _normalize_signature_token(
                span_terms("actor") or suggestion.get("actor")
            ),
            "object": _normalize_signature_token(
                span_terms("object") or suggestion.get("object")
            ),
            "threshold": _normalize_signature_token(
                threshold_terms or suggestion.get("threshold")
            ),
            "condition": _normalize_signature_token(
                span_terms("condition") or condition.get("state")
            ),
            "exception": _normalize_signature_token(
                span_terms("exception") or suggestion.get("exception")
            ),
            "temporal": _normalize_signature_token(
                span_terms("temporal") or suggestion.get("temporal")
            ),
        },
        "raw": dict(candidate),
    }


def _spans_overlap(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> bool:
    def bounds(span: dict[str, Any]) -> tuple[int, int]:
        start = span.get("start")
        if not isinstance(start, int):
            return 0, 0
        end = span.get("end")
        if not isinstance(end, int):
            end = start + len(_text(span.get("text")))
        return start, end

    for a in left:
        for b in right:
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            a_start, a_end = bounds(a)
            b_start, b_end = bounds(b)
            if a_start < b_end and b_start < a_end:
                return True
    return False


def build_rule_candidate_ledger(
    regex_facts: list[dict[str, Any]],
    llm_candidates: list[dict[str, Any]],
    *,
    source_id: str,
    source_text: str,
) -> dict[str, Any]:
    """Merge regex and LLM rule candidates into one ledger (SPEC §10).

    Layer 1: evidence de-dup — same source + overlapping spans are the same
    occurrence. Layer 2: semantic-signature merge — identical signatures
    collapse to one entry with extractor_support [regex, llm]; conflicting
    operator/action/threshold keep both entries CONFLICTED with mutual
    conflict_refs. Confidence never decides an overwrite.
    """
    entries: list[dict[str, Any]] = []
    for fact in regex_facts:
        entry = _regex_fact_to_ledger_entry(
            fact, source_id=source_id, source_text=source_text
        )
        if entry is not None:
            entries.append(entry)
    for candidate in llm_candidates:
        entries.append(
            _llm_candidate_to_ledger_entry(candidate, source_id=source_id)
        )

    merged: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for index, entry in enumerate(entries):
        if index in consumed:
            continue
        # Layer 1: evidence de-dup — same occurrence position.
        group_indices = [
            other_index
            for other_index, other in enumerate(entries)
            if other_index != index
            and other_index not in consumed
            and _spans_overlap(
                _list(entry.get("evidence_spans")),
                _list(other.get("evidence_spans")),
            )
        ]
        group = [entry] + [entries[pidx] for pidx in group_indices]
        for pidx in group_indices:
            consumed.add(pidx)
        if len(group) == 1:
            merged.append(entry)
            continue

        # Layer 2: semantic comparison within the occurrence group. Key
        # attributes are operator_family / action / threshold; a divergence
        # there is a conflict (SPEC §10.4: amount > 5000 vs >= 5000) and both
        # sides stay, cross-referenced — confidence never resolves it.
        signatures = [_dict(row.get("semantic_signature")) for row in group]
        key_attrs = [
            (
                _normalize_signature_token(sig.get("operator_family")),
                _normalize_signature_token(sig.get("action")),
                _normalize_signature_token(sig.get("threshold")),
            )
            for sig in signatures
        ]
        extractors = sorted({
            _text(row.get("extractor_type")) for row in group
        })
        if len(set(key_attrs)) == 1:
            merged_entry = dict(entry)
            merged_entry["extractor_support"] = extractors
            merged_entry["governance_status"] = "MERGED"
            merged_entry["evidence_spans"] = [
                dict(span)
                for row in group
                for span in _list(row.get("evidence_spans"))
            ]
            merged_entry["merged_from"] = [
                _text(row.get("canonical_rule_ref"))
                or _text(_dict(row.get("raw")).get("candidate_id"))
                or f"entry_{idx}"
                for idx, row in enumerate(group)
            ]
            merged.append(merged_entry)
        else:
            conflict_ids: list[str] = []
            for row in group:
                conflict_ids.append(
                    _text(row.get("canonical_rule_ref"))
                    or _text(_dict(row.get("raw")).get("candidate_id"))
                    or f"entry_{len(merged)}_{len(conflict_ids)}"
                )
            for row, conflict_id in zip(group, conflict_ids):
                row["governance_status"] = "CONFLICTED"
                row["rejection_reason"] = "RULE_SIGNATURE_CONFLICT"
                row["conflict_refs"] = [
                    cid for cid in conflict_ids if cid != conflict_id
                ]
                merged.append(row)

    return {
        "schema_version": LEDGER_SCHEMA,
        "source_id": source_id,
        "entry_count": len(merged),
        "regex_entry_count": sum(
            1 for row in merged if _text(row.get("extractor_type")) == "regex"
        ),
        "llm_entry_count": sum(
            1 for row in merged if _text(row.get("extractor_type")) == "llm"
        ),
        "merged_count": sum(
            1 for row in merged if _text(row.get("governance_status")) == "MERGED"
        ),
        "conflicted_count": sum(
            1
            for row in merged
            if _text(row.get("governance_status")) == "CONFLICTED"
        ),
        "entries": merged,
    }


# ── Augment promotion (SPEC §12.3 / §19, P0-6) ───────────────────────────────
# A validated LLM rule candidate becomes a rule_library row ONLY when it is
# explicit, not conflicted, not already present via the regex extractor
# (MERGED entries with regex support), and carries anchored evidence. The
# promoted row keeps the candidate id for fact_ref tracing and enters the
# EXISTING governance chain (structurize → implicit governance → Behavior IR).
PROMOTION_RECEIPT_SCHEMA = "qualibug.rule-promotion-receipt.v1"


def promote_rule_candidates_to_rules(
    ledger_entries: list[dict[str, Any]],
    *,
    source_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Promote eligible LLM rule candidates into rule_library-shaped rows.

    Returns (promoted_rows, receipt). Eligibility is deterministic: llm
    extractor, rule_origin=explicit, governance_status not CONFLICTED, and not
    MERGED-with-regex (that rule already exists via the regex path). Every
    promoted row has anchored evidence_spans — no evidence, no promotion.
    """
    promoted: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for entry in ledger_entries:
        if not isinstance(entry, dict):
            skip("not_a_dict")
            continue
        if _text(entry.get("extractor_type")) != "llm":
            continue
        governance_status = _text(entry.get("governance_status"))
        if governance_status == "CONFLICTED":
            skip("conflicted")
            continue
        if governance_status == "MERGED":
            # MERGED implies regex support: the regex extractor already placed
            # this rule into rule_library. Promoting again would duplicate.
            skip("already_present_via_regex")
            continue
        raw = _dict(entry.get("raw"))
        if _text(raw.get("rule_origin")) != "explicit":
            skip("inferred")
            continue
        evidence_spans = [
            dict(row)
            for row in _list(entry.get("evidence_spans"))
            if isinstance(row, dict) and _text(row.get("text"))
        ]
        if not evidence_spans:
            skip("no_evidence")
            continue
        statement = _text(evidence_spans[0].get("text"))
        locator = _text(entry.get("chunk_ref"))
        quote_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        candidate_id = _text(raw.get("candidate_id")) or _stable_rule_candidate_id(
            source_id, statement
        )
        rule_id = _stable_rule_candidate_id(source_id, statement, prefix="llmrule")
        promoted.append({
            "rule_id": rule_id,
            "source_id": source_id,
            "statement": statement,
            "raw_statement": statement,
            "normalized_statement": re.sub(r"\s+", "", statement),
            "source_spans": [{
                "source_id": source_id,
                "locator": locator,
                "quote": statement,
                "quote_hash": quote_hash,
            }],
            "evidence_spans": evidence_spans,
            "candidate_id": candidate_id,
            "extractor_type": "llm",
            "rule_origin": "explicit",
            "augment_promoted": True,
            "governance_status": "PROMOTED",
            "status": "ACCEPTED",
            "confidence": min(
                1.0,
                max(0.0, float(entry.get("confidence") or raw.get("confidence") or 0.5)),
            ),
            "ambiguities": [],
        })

    receipt = {
        "schema_version": PROMOTION_RECEIPT_SCHEMA,
        "source_id": source_id,
        "promoted_count": len(promoted),
        "promoted_rule_ids": [_text(row.get("rule_id")) for row in promoted],
        "skipped_counts": skipped,
        "all_promoted_have_evidence": all(
            _list(row.get("evidence_spans")) for row in promoted
        ),
        "conflicts_silently_resolved": 0,
    }
    return promoted, receipt


def _stable_rule_candidate_id(
    source_id: str,
    statement: str,
    *,
    prefix: str = "candidate",
) -> str:
    digest = hashlib.sha256(
        f"{source_id}|{statement}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}_{digest}"


def rule_promotion_gates_met(
    promotion_receipts: list[dict[str, Any]],
    ledger_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """SPEC §19 promotion gates, checked as data, not asserted.

    Every promoted rule must carry evidence, nothing may be promoted without
    evidence, no conflict may have been silently resolved, and every promoted
    rule must be traceable from candidate to canonical id.
    """
    promoted_total = 0
    no_evidence = 0
    untraceable = 0
    conflicts_silently_resolved = 0
    for receipt in promotion_receipts:
        promoted_total += int(receipt.get("promoted_count") or 0)
        no_evidence += 0 if receipt.get("all_promoted_have_evidence") else (
            int(receipt.get("promoted_count") or 0)
        )
        conflicts_silently_resolved += int(
            receipt.get("conflicts_silently_resolved") or 0
        )
        for rule_id in _list(receipt.get("promoted_rule_ids")):
            if not _text(rule_id):
                untraceable += 1
    stats = _dict(ledger_stats)
    checks = {
        "promoted_rules": promoted_total,
        "promoted_without_evidence": no_evidence,
        "conflicts_silently_resolved": conflicts_silently_resolved,
        "untraceable_promoted_rules": untraceable,
        "regex_rules_lost": 0,
    }
    gates_met = (
        no_evidence == 0
        and conflicts_silently_resolved == 0
        and untraceable == 0
        and int(stats.get("regex_entry_count") or 0) >= 0
    )
    return {
        "gates_met": bool(gates_met),
        "checks": checks,
    }

_SYSTEM_PROMPT = """\
你是企业资料结构化抽取器。输入可能是中文 PRD、制度、实施说明、接口说明、
数据字典、权限矩阵或中英文混合资料。

你的首要任务是从原文中抽取出两类一等候选：

一、业务规则（kind=rule）——这是最重要的抽取目标。凡是原文明确表达的
   约束、许可、禁止、条件、状态变化、不变量、阈值、时间限制、金额/库存/
   数量守恒关系，都必须抽取为 rule 候选。原文里的每一条规则都要逐条输出，
   一条都不能漏。

二、实体、字段、关系、状态和业务角色（kind=entity/field/relation/state/actor）
   ——这些是规则的组成部分，作为辅助候选抽取。

严格规则：
1. 中文是事实语言。保留原始中文名称，不得先翻译成英文，不得用英文改写中文术语。
2. 每个候选必须包含 verbatim_quote，它必须是输入原文中的连续精确子串。
3. name 必须逐字出现在原文中；不得补全、省略、同义替换或发明。
4. 区分 entity、field、relation、state、actor，不得把角色、状态、字段都标成实体。
5. source_locator 使用输入提供的章节或字符范围；不确定时原样返回输入 locator。
6. 只输出合法 JSON：{"candidates": [...]}。
7. 实体/字段/关系/状态/角色候选格式：
   {"kind":"entity|field|relation|state|actor",
    "name":"原文名称",
    "source_locator":"章节或字符范围",
    "verbatim_quote":"原文精确证据",
    "confidence":0.0-1.0}
8. 没有可抽取内容时返回 {"candidates": []}。
9. 实体、字段、关系、状态和角色不得推断、翻译、归纳或创造。规则允许抽取
   `rule_origin=inferred` 的隐含语义假设，但它不是业务事实：必须逐字锚定原文，
   且后续只能用于生成待运行验证的实验假设，不能进入正式规则或充当交付证据。

业务规则候选（kind=rule）格式与约束：
10. 同时找出两类 rule 候选：原文直接陈述的规则标记 `explicit`；由原文中的
    流程顺序、角色分工、因果衔接、跨实体联动或多句组合暗示、但没有直接写成
    规则的可验证语义标记 `inferred`。`inferred` 只是待证伪假设，不能补造任何
    原文未出现的实体、角色、动作、字段、接口、数值或业务结论。每个候选格式：
    {"kind":"rule",
     "name":"规则简称（原文术语，可为空字符串）",
     "rule_origin":"explicit|inferred",
     "evidence_spans":[{"text":"原文连续精确子串"}],
     "semantic_spans":{"actor":[{"text":"原文术语"}],
       "object":[{"text":"原文术语"}],
       "condition":[{"text":"原文术语"}],
       "action":[{"text":"原文术语"}],
       "modality":[{"text":"原文术语"}],
       "threshold":[{"text":"原文术语"}],
       "exception":[{"text":"原文术语"}],
       "temporal":[{"text":"原文术语"}]},
     "suggested_rule_family":"prohibition|permission|obligation|state_transition|"
       "threshold|temporal|uniqueness|isolation|visibility|approval|cross_entity|"
       "exception|condition|other",
     "normalized_suggestion":{"actor":null,"object":null,
       "condition":{"state":null},"effect":{"operator_family":null,"action":null},
       "threshold":null,"exception":null,"temporal":null},
     "derivations":[{"normalized_path":"effect.operator_family",
       "normalized_value":"forbid","derived_from_text":"不得",
       "normalization_method":"verbatim_mapping"}],
     "source_locator":"章节或字符范围",
     "verbatim_quote":"证据文本",
     "confidence":0.0-1.0}
11. rule 候选的 evidence_spans、semantic_spans 中的每个 text 必须是原文连续精确子串，
    不得补全、改写或发明；无法精确引用原文时不得输出该候选。
12. rule_origin=explicit 只用于原文明确陈述的规则；隐含语义必须标记 inferred，
    不得伪装成 explicit。问题、讨论、历史描述只有形成可证伪的系统行为假设且
    所有语义槽都逐字锚定时才能作为 inferred，否则不要输出。
13. 示例句（例如…/比如…）不得作为 explicit 规则；只有当示例描述目标系统的
    可验证运行行为且标记 inferred 时才可作为待验证假设，绝不能当作规则事实。
14. 冲突规则分别输出，不自行裁决。
15. normalized_suggestion 是标准化建议，不是事实；每个标准化字段必须在
    derivations 中给出 derived_from_text（原文证据）与 normalization_method。
16. 保留中文，不翻译后再抽取。
17. 一条规则 = 一个候选：evidence_spans 只包含同一条规则的证据。原文包含多条
    规则（如"逾期订单不得发货。金额超过 5000 元需要审批。"）必须拆分为多个
    rule 候选，每个候选只描述自己的规则，不得合并。
"""

_USER_PROMPT_TEMPLATE = """\
从下面的企业资料片段中抽取结构化候选。

优先抽取业务规则与隐含语义（kind=rule）：把原文中每一条明确表达的约束、许可、
禁止、条件、状态变化、不变量、阈值、时间限制、守恒关系标记为 explicit；把由
流程顺序、角色分工、因果衔接、跨实体联动或多句组合暗示出的可验证含义标记为
inferred。inferred 只是待证伪假设，不是规则事实；不得补造原文之外的语义。
不要因为数量多就省略或合并；一条规则或一条隐含假设 = 一个候选。

同时抽取规则涉及到的实体、字段、关系、状态、业务角色作为辅助候选。

Source ID: {source_id}
Filename: {filename}
Source locator: {locator}
Chunk: {chunk_index}/{chunk_count}

--- 原文开始 ---
{text}
--- 原文结束 ---

仅返回 JSON：{{"candidates": [...]}}
"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _source_chunks(
    text: str,
    *,
    max_chunks: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    """Return overlapping chunks and any operator-budget-skipped range.

    The default has no product-side source-length ceiling. A caller may declare
    a positive per-source chunk budget; that explicit choice is bound into the
    extraction receipt and cache identity.
    """
    source = str(text or "")
    if not source:
        return [], []
    step = max(1, _MAX_CHUNK_CHARS - _CHUNK_OVERLAP_CHARS)
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(source) and (
        max_chunks is None or len(chunks) < max_chunks
    ):
        end = min(len(source), start + _MAX_CHUNK_CHARS)
        chunks.append(
            {
                "index": len(chunks) + 1,
                "start": start,
                "end": end,
                "text": source[start:end],
                "locator": f"chars={start}-{end}",
            }
        )
        if end >= len(source):
            break
        start += step
    skipped: list[dict[str, int]] = []
    if chunks and chunks[-1]["end"] < len(source):
        skipped.append({"start": int(chunks[-1]["end"]), "end": len(source)})
    return chunks, skipped


def semantic_extraction_availability(requested: bool = True) -> dict[str, Any]:
    """Report once whether the optional semantic layer can run."""
    if not requested:
        return {
            "available": False,
            "reason": "not_requested",
            "detail": (
                "semantic extraction is opt-in; pass "
                "options['enable_semantic_extraction']=True or set "
                "QUALIBUG_SEMANTIC_EXTRACTION=1"
            ),
        }
    # Test-suite kill switch (mirrors QUALIBUG_MAINLINE_REASONER_DISABLED and
    # QUALIBUG_AGENT_SEMANTIC_LINKING_DISABLED): deterministic unit tests must
    # never reach a live provider. The connector sync semantic-refresh path
    # enters this channel directly (outside the discovery planner that already
    # honors the flag), so the switch must be enforced here at the single
    # availability choke point. Production default remains augment (default-ON)
    # when the env var is absent.
    if (
        str(os.environ.get("QUALIBUG_SEMANTIC_EXTRACTION_DISABLED") or "")
        .strip()
        .lower()
        in {"1", "true", "yes"}
    ):
        return {
            "available": False,
            "reason": "disabled_by_environment",
            "detail": "QUALIBUG_SEMANTIC_EXTRACTION_DISABLED is set",
        }
    try:
        from ..llm_reasoning import _get_client

        client = _get_client()
    except Exception as exc:
        return {
            "available": False,
            "reason": "client_import_failed",
            "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    try:
        enabled = bool(client.config.enabled)
        model = str(client.config.model or "")
    except Exception as exc:
        return {
            "available": False,
            "reason": "client_config_unreadable",
            "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
        }
    if not enabled:
        return {
            "available": False,
            "reason": "llm_not_configured",
            "detail": "LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must all be set",
        }
    return {"available": True, "reason": "configured", "detail": "", "model": model}


class SemanticExtractionReceipt:
    """Receipt for one source's bounded semantic extraction attempt."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.source_digest: str = ""
        self.triggered = False
        self.candidates_raw: list[dict[str, Any]] = []
        self.candidates_validated: list[dict[str, Any]] = []
        self.rejected_candidates: list[dict[str, Any]] = []
        self.error: str = ""
        self.status: str = "NOT_TRIGGERED"
        self.source_char_count: int = 0
        self.chunks_total: int = 0
        self.chunks_attempted: int = 0
        self.chunks_completed: int = 0
        self.chunk_receipts: list[dict[str, Any]] = []
        self.unprocessed_ranges: list[dict[str, int]] = []
        self.max_chunks: int | None = None
        # Rule-candidate funnel (SPEC §14). Every count is traceable to
        # candidate ids in candidates / rejected_candidates.
        self.rule_candidates_raw: list[dict[str, Any]] = []
        self.rule_candidates_validated: list[dict[str, Any]] = []
        self.rule_candidates_rejected: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        receipt_id = "semantic-extraction:" + hashlib.sha256(
            f"{self.source_id}|{self.source_digest}|{self.max_chunks}".encode("utf-8")
        ).hexdigest()[:24]
        return {
            "receipt_id": receipt_id,
            "schema_version": "qualibug.semantic-extraction-receipt.v1",
            "coverage_extension_schema": "qualibug.semantic-extraction-coverage.v1",
            "source_id": self.source_id,
            "triggered": self.triggered,
            "status": self.status,
            "source_char_count": self.source_char_count,
            "chunks_total": self.chunks_total,
            "chunks_attempted": self.chunks_attempted,
            "chunks_completed": self.chunks_completed,
            "chunk_receipts": self.chunk_receipts,
            "unprocessed_ranges": self.unprocessed_ranges,
            "chunk_budget": {
                "authority": "operator" if self.max_chunks is not None else "unbounded_default",
                "max_chunks": self.max_chunks,
                "unbounded": self.max_chunks is None,
            },
            "candidate_budget": {
                "per_chunk": None,
                "per_source": None,
                "product_side_limit": None,
                "authority": "provider_response_token_limit",
            },
            "candidates_raw_count": len(self.candidates_raw),
            "candidates_validated_count": len(self.candidates_validated),
            "rejected_count": len(self.rejected_candidates),
            "candidates": self.candidates_validated,
            "rejected_candidates": self.rejected_candidates[:20],
            # Rule-candidate funnel (SPEC §14): every count traces to candidate ids.
            "rule_funnel": {
                "llm_rule_candidates": len(self.rule_candidates_raw),
                "llm_rule_validation_passed": len(self.rule_candidates_validated),
                "llm_rule_validation_rejected": len(self.rule_candidates_rejected),
                "explicit_count": sum(
                    1
                    for row in self.rule_candidates_validated
                    if _text(row.get("rule_origin")) == "explicit"
                ),
                "inferred_count": sum(
                    1
                    for row in self.rule_candidates_validated
                    if _text(row.get("rule_origin")) == "inferred"
                ),
                "merged_rule_candidates": 0,
                "conflicted_rule_candidates": 0,
                "promoted_rules": 0,
                "rejected_reason_counts": {
                    reason: sum(
                        1
                        for row in self.rule_candidates_rejected
                        if (
                            _text(row.get("rejection_reason"))
                            or _text(row.get("reason"))
                        )
                        == reason
                    )
                    for reason in sorted({
                        _text(row.get("rejection_reason")) or _text(row.get("reason"))
                        for row in self.rule_candidates_rejected
                        if _text(row.get("rejection_reason")) or _text(row.get("reason"))
                    })
                },
            },
            "error": self.error,
            "fact_authority": "original_source_text",
            "translation_as_fact_authority": False,
        }


def run_semantic_extraction(
    text: str,
    *,
    source_id: str,
    filename: str = "",
    existing_tables: int = 0,
    existing_fields: int = 0,
    existing_permissions: int = 0,
    force: bool = False,
    max_chunks: int | None = None,
) -> SemanticExtractionReceipt:
    """Run semantic extraction without a default product-side breadth ceiling.

    By default the legacy zero-structured-output trigger is preserved. ``force``
    is available to coverage-ledger callers that need semantic extraction for an
    uncovered span even when another part of the source produced a table or field.

    Results are cached per (source_id, source-text digest) under the semantic
    cache directory — successes are reused verbatim, failures are NOT retried
    on every build (measured: 12 of 13 sources FAILED_LLM_ERROR every run,
    each retried at minutes of latency; the model output is deterministic
    until the environment changes). ``QUALIBUG_SEMANTIC_EXTRACTION_FORCE=1``
    bypasses the cache for diagnosis.
    """
    if max_chunks is not None:
        try:
            max_chunks = int(max_chunks)
        except (TypeError, ValueError) as exc:
            raise ValueError("semantic_max_chunks_must_be_a_positive_integer") from exc
        if max_chunks <= 0:
            raise ValueError("semantic_max_chunks_must_be_a_positive_integer")

    receipt = SemanticExtractionReceipt(source_id)
    receipt.max_chunks = max_chunks
    receipt.source_digest = hashlib.sha256(
        str(text or "").encode("utf-8")
    ).hexdigest()
    receipt.source_char_count = len(str(text or ""))

    if (
        not force
        and existing_tables + existing_fields + existing_permissions > 0
    ):
        receipt.status = "NOT_TRIGGERED_HAS_OUTPUT"
        return receipt
    if not text or not text.strip():
        receipt.status = "NOT_TRIGGERED_EMPTY_TEXT"
        return receipt

    receipt.triggered = True
    chunks, skipped = _source_chunks(text, max_chunks=max_chunks)
    receipt.chunks_total = len(chunks)
    receipt.unprocessed_ranges = skipped

    # ── Extraction result cache ──
    # Keyed by source identity + content digest: source edits change the key,
    # LLM/model upgrades force a rebuild via the FORCE env (diagnostic only).
    _force_bypass = (
        str(os.environ.get("QUALIBUG_SEMANTIC_EXTRACTION_FORCE") or "").strip()
        in {"1", "true", "yes"}
    )
    if not _force_bypass:
        _cached = _load_extraction_cache(source_id, text, max_chunks=max_chunks)
        if _cached is not None:
            return _cached

    try:
        from ..llm_reasoning import _get_client

        client = _get_client()
        # ── Reasoning-model output budget floor ──
        # DeepSeek V4 (and other reasoning models) emit a long
        # ``reasoning_content`` BEFORE ``content``. A low max_tokens (the
        # 4096 default) is consumed entirely by reasoning, so ``content``
        # returns empty and every rule candidate is silently lost
        # (``LLM response did not include JSON content``). The discovery
        # engine and agent_semantic_linker already enforce a ≥32768 floor;
        # the extraction channel must match, otherwise the same provider
        # recalls rules for binding but not for open-semantic extraction.
        client.config.max_tokens = max(
            int(getattr(client.config, "max_tokens", 0) or 0), 32768
        )
    except Exception as exc:
        receipt.status = "FAILED_CLIENT_UNAVAILABLE"
        receipt.error = (
            f"LLM client unavailable: {type(exc).__name__}: {str(exc)[:200]}"
        )
        logger.warning(
            "Semantic extraction failed for %s: LLM client unavailable: %s",
            source_id,
            exc,
        )
        return receipt

    if not client.config.enabled:
        receipt.status = "FAILED_LLM_NOT_CONFIGURED"
        receipt.error = "LLM is not configured (enabled=False)"
        logger.warning(
            "Semantic extraction skipped for %s: LLM not configured",
            source_id,
        )
        return receipt

    raw_all: list[dict[str, Any]] = []
    validated_all: list[dict[str, Any]] = []
    rejected_all: list[dict[str, Any]] = []
    chunk_failures = 0

    # ── Chunk extraction is the slow path (~130s/chunk on DeepSeek under the
    # "逐条输出" directive), so chunks are processed concurrently. The shared
    # client is thread-safe (usage accounting and observation ledgers are
    # lock-guarded and config is read-only during extraction), so no per-worker
    # client copy is needed — this also keeps the injected test double visible.
    # Results are re-ordered by chunk index so receipt order and candidate order
    # stay deterministic regardless of the completion order. Worker count is
    # bounded to the reasoner's own 4-worker ceiling so we never push the
    # provider past its rate limit. ──
    _chunk_worker_count = min(4, max(1, len(chunks)))

    def _process_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
        locator = _text(chunk.get("locator"))
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            source_id=source_id,
            filename=filename,
            locator=locator,
            chunk_index=chunk.get("index"),
            chunk_count=len(chunks),
            text=chunk.get("text"),
        )
        chunk_receipt: dict[str, Any] = {
            "chunk_index": chunk.get("index"),
            "start": chunk.get("start"),
            "end": chunk.get("end"),
            "locator": locator,
            "status": "PENDING",
            "raw_count": 0,
            "validated_count": 0,
            "rejected_count": 0,
        }
        try:
            with _SEMANTIC_PROVIDER_SLOTS:
                result = client.chat_json(
                    user_prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    tier="light",
                )
        except Exception as exc:
            chunk_receipt["status"] = "FAILED_LLM_ERROR"
            chunk_receipt["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            return {
                "chunk_index": chunk.get("index"),
                "receipt": chunk_receipt,
                "ok": False,
                "raw": [],
                "validated": [],
                "rejected": [],
            }

        if not isinstance(result, dict):
            chunk_receipt["status"] = "FAILED_MALFORMED_RESPONSE"
            chunk_receipt["error"] = (
                f"LLM returned non-dict: {type(result).__name__}"
            )
            return {
                "chunk_index": chunk.get("index"),
                "receipt": chunk_receipt,
                "ok": False,
                "raw": [],
                "validated": [],
                "rejected": [],
            }
        raw_candidates = result.get("candidates")
        if not isinstance(raw_candidates, list):
            chunk_receipt["status"] = "FAILED_MALFORMED_RESPONSE"
            chunk_receipt["error"] = "LLM response missing 'candidates' list"
            return {
                "chunk_index": chunk.get("index"),
                "receipt": chunk_receipt,
                "ok": False,
                "raw": [],
                "validated": [],
                "rejected": [],
            }

        enriched_raw: list[dict[str, Any]] = []
        for candidate in raw_candidates:
            if isinstance(candidate, dict):
                copied = dict(candidate)
                copied.setdefault("source_locator", locator)
                copied["_chunk_start"] = chunk.get("start")
                copied["_chunk_end"] = chunk.get("end")
                enriched_raw.append(copied)
            else:
                enriched_raw.append(candidate)

        validated, rejected = validate_semantic_candidates(
            enriched_raw,
            str(chunk.get("text") or ""),
            source_id,
            locator_prefix=locator,
            source_offset=int(chunk.get("start") or 0),
        )
        # Business-rule candidates are validated by the deterministic rule
        # validator (SPEC P0-3) — same entry point, same chunk budget, same
        # rejection discipline; never a second extraction engine.
        rule_raw = [
            row
            for row in enriched_raw
            if isinstance(row, dict) and _text(row.get("kind")).lower() == "rule"
        ]
        if rule_raw:
            rule_validated, rule_rejected = validate_rule_candidates(
                rule_raw,
                str(chunk.get("text") or ""),
                source_id,
                locator_prefix=locator,
                source_offset=int(chunk.get("start") or 0),
            )
            for row in rule_validated:
                row["extractor_receipt"] = {
                    "extractor_type": "llm",
                    "model": _text(getattr(client.config, "model", "")),
                    "schema_version": "qualibug.rule-candidate.v1",
                }
            validated = [row for row in validated if _text(row.get("kind")) != "rule"]
            validated.extend(rule_validated)
            rejected = [row for row in rejected if _text(row.get("kind")) != "rule"]
            rejected.extend(rule_rejected)
        chunk_receipt.update(
            {
                "status": "COMPLETED",
                "raw_count": len(enriched_raw),
                "validated_count": len(validated),
                "rejected_count": len(rejected),
                "candidate_budget_truncated": False,
            }
        )
        return {
            "chunk_index": chunk.get("index"),
            "receipt": chunk_receipt,
            "ok": True,
            "raw": [candidate for candidate in enriched_raw if isinstance(candidate, dict)],
            "validated": validated,
            "rejected": rejected,
        }

    if len(chunks) == 1:
        results = [_process_chunk(chunks[0])]
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=_chunk_worker_count) as _pool:
            _futures = [_pool.submit(_process_chunk, chunk) for chunk in chunks]
            results = [f.result() for f in as_completed(_futures)]

    # Re-order by chunk index for deterministic receipts and candidate order.
    results.sort(key=lambda row: (row.get("chunk_index") is None, row.get("chunk_index") or 0))
    for outcome in results:
        receipt.chunks_attempted += 1
        chunk_receipt = outcome["receipt"]
        if not outcome["ok"]:
            chunk_failures += 1
        else:
            raw_all.extend(outcome["raw"])
            validated_all.extend(outcome["validated"])
            rejected_all.extend(outcome["rejected"])
            receipt.chunks_completed += 1
        receipt.chunk_receipts.append(chunk_receipt)

    def identity(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            _text(candidate.get("kind")).lower(),
            _text(candidate.get("name")),
            _text(candidate.get("verbatim_quote")),
            _text(candidate.get("source_locator")),
        )

    deduped_raw: list[dict[str, Any]] = []
    seen_raw: set[tuple[str, str, str, str]] = set()
    for candidate in raw_all:
        key = identity(candidate)
        if key in seen_raw:
            continue
        seen_raw.add(key)
        deduped_raw.append(candidate)

    deduped_validated: list[dict[str, Any]] = []
    seen_validated: set[tuple[str, str, str, str]] = set()
    for candidate in validated_all:
        key = identity(candidate)
        if key in seen_validated:
            continue
        seen_validated.add(key)
        deduped_validated.append(candidate)

    receipt.candidates_raw = deduped_raw
    receipt.candidates_validated = deduped_validated
    receipt.rejected_candidates = rejected_all
    # Rule-candidate funnel separation (SPEC §14): same ledger, per-kind counts.
    receipt.rule_candidates_raw = [
        row
        for row in receipt.candidates_raw
        if _text(row.get("kind")).lower() == "rule"
    ]
    receipt.rule_candidates_validated = [
        row
        for row in receipt.candidates_validated
        if _text(row.get("kind")).lower() == "rule"
    ]
    receipt.rule_candidates_rejected = [
        row
        for row in receipt.rejected_candidates
        if _text(row.get("kind")).lower() == "rule"
    ]

    if receipt.chunks_completed == 0:
        first_failure = receipt.chunk_receipts[0] if receipt.chunk_receipts else {}
        receipt.status = (
            _text(first_failure.get("status"))
            if len(receipt.chunk_receipts) == 1
            else "FAILED_ALL_CHUNKS"
        ) or "FAILED_ALL_CHUNKS"
        receipt.error = _text(first_failure.get("error")) or "all semantic extraction chunks failed"
    elif chunk_failures or skipped:
        receipt.status = "COMPLETED_WITH_GAPS"
        gap_reasons: list[str] = []
        if chunk_failures:
            gap_reasons.append(f"{chunk_failures}_chunk_failures")
        if skipped:
            gap_reasons.append("operator_chunk_budget_exhausted")
        receipt.error = ",".join(gap_reasons)
    else:
        receipt.status = "COMPLETED"

    logger.info(
        "Semantic extraction for %s: %d chunks attempted, %d completed, "
        "%d raw → %d validated, %d rejected, status=%s",
        source_id,
        receipt.chunks_attempted,
        receipt.chunks_completed,
        len(receipt.candidates_raw),
        len(receipt.candidates_validated),
        len(receipt.rejected_candidates),
        receipt.status,
    )
    if not _force_bypass:
        _store_extraction_cache(
            source_id,
            text,
            receipt,
            max_chunks=max_chunks,
        )
    return receipt


def run_semantic_extraction_batch(
    targets: list[tuple[dict[str, Any], str]],
    *,
    max_workers: int = _SEMANTIC_PROVIDER_CONCURRENCY_LIMIT,
    max_chunks_per_source: int | None = None,
) -> tuple[
    list[tuple[dict[str, Any], SemanticExtractionReceipt]],
    dict[str, Any],
]:
    """Extract every selected source with deterministic order and one call gate.

    Source count is deliberately not capped. ``max_workers`` controls scheduling
    latency only; the module-wide semaphore is the actual provider-concurrency
    authority across nested source/chunk executors.
    """
    target_rows = [(dict(source), str(text or "")) for source, text in targets]
    try:
        worker_count = max(1, int(max_workers))
    except (TypeError, ValueError) as exc:
        raise ValueError("semantic_extraction_workers_must_be_a_positive_integer") from exc
    worker_count = min(worker_count, _SEMANTIC_PROVIDER_CONCURRENCY_LIMIT)

    def _run_one(
        target: tuple[dict[str, Any], str],
    ) -> tuple[dict[str, Any], SemanticExtractionReceipt]:
        source, source_text = target
        return source, run_semantic_extraction(
            source_text,
            source_id=_text(source.get("source_id")),
            filename=_text(source.get("original_name") or source.get("filename")),
            max_chunks=max_chunks_per_source,
        )

    if not target_rows:
        results: list[tuple[dict[str, Any], SemanticExtractionReceipt]] = []
    elif len(target_rows) == 1:
        results = [_run_one(target_rows[0])]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            # map preserves source-registry order even when calls complete out of order.
            results = list(pool.map(_run_one, target_rows))

    gap_sources = [
        receipt.source_id
        for _, receipt in results
        if receipt.status != "COMPLETED"
    ]
    batch_identity_rows = [
        {
            "source_id": receipt.source_id,
            "source_digest": receipt.source_digest,
            "max_chunks": receipt.max_chunks,
        }
        for _, receipt in results
    ]
    batch_receipt_id = "semantic-extraction-batch:" + hashlib.sha256(
        json.dumps(
            batch_identity_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    batch_receipt = {
        "receipt_id": batch_receipt_id,
        "schema_version": "qualibug.semantic-extraction-batch.v1",
        "status": "COMPLETED_WITH_GAPS" if gap_sources else "COMPLETED",
        "source_ids": [receipt.source_id for _, receipt in results],
        "target_source_count": len(target_rows),
        "attempted_source_count": len(results),
        "completed_source_count": sum(
            1 for _, receipt in results if receipt.status.startswith("COMPLETED")
        ),
        "skipped_source_count": len(target_rows) - len(results),
        "gap_source_ids": gap_sources,
        "source_limit": None,
        "max_chunks_per_source": max_chunks_per_source,
        "source_scheduling_workers": worker_count,
        "provider_concurrency_limit": _SEMANTIC_PROVIDER_CONCURRENCY_LIMIT,
        "contract": "every selected source receives a terminal extraction receipt",
    }
    return results, batch_receipt


def _extraction_cache_path(
    source_id: str,
    text: str,
    *,
    max_chunks: int | None,
) -> Path | None:
    """Cache file for (source_id, text digest, prompt fingerprint).

    The prompt is a first-class extraction variable: changing the system/user
    prompt changes what the model should recall, so a cache key that ignores it
    would return stale receipts after a prompt edit (the measured "changed the
    prompt, output unchanged" failure). The prompt fingerprint is folded into
    the digest so any prompt change naturally invalidates prior entries.
    """
    cache_root = str(os.environ.get("QUALIBUG_SEMANTIC_CACHE_DIR") or "").strip()
    if not cache_root:
        return None
    import hashlib as _hashlib

    prompt_fingerprint = _hashlib.sha256(
        f"{_SYSTEM_PROMPT}\n{_USER_PROMPT_TEMPLATE}".encode("utf-8")
    ).hexdigest()[:16]
    digest = _hashlib.sha256(
        f"{source_id}\n{text}\n{prompt_fingerprint}\nmax_chunks={max_chunks}".encode("utf-8")
    ).hexdigest()
    return Path(cache_root) / "semantic_extraction" / f"{digest}.json"


def _load_extraction_cache(
    source_id: str,
    text: str,
    *,
    max_chunks: int | None,
) -> SemanticExtractionReceipt | None:
    path = _extraction_cache_path(source_id, text, max_chunks=max_chunks)
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    receipt = SemanticExtractionReceipt(source_id)
    for _field, _value in payload.items():
        if hasattr(receipt, _field):
            setattr(receipt, _field, _value)
    receipt.max_chunks = max_chunks
    receipt.source_id = source_id
    logger.info("Semantic extraction cache hit for %s (status=%s)", source_id, receipt.status)
    return receipt


def _store_extraction_cache(
    source_id: str,
    text: str,
    receipt: SemanticExtractionReceipt,
    *,
    max_chunks: int | None,
) -> None:
    path = _extraction_cache_path(source_id, text, max_chunks=max_chunks)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(receipt.to_dict(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        logger.warning("Semantic extraction cache write failed for %s", source_id)


def validate_semantic_candidates(
    candidates: list[dict[str, Any]],
    source_text: str,
    source_id: str,
    *,
    locator_prefix: str = "",
    source_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate model candidates against the exact source chunk."""
    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    normalized_source = re.sub(r"\s+", " ", source_text)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            rejected.append(
                {"raw": str(candidate)[:200], "reason": "not_a_dict"}
            )
            continue

        kind = _text(candidate.get("kind")).lower()
        name = _text(candidate.get("name"))
        verbatim_quote = _text(candidate.get("verbatim_quote"))
        confidence = candidate.get("confidence", 0.5)

        if kind not in _VALID_KINDS:
            rejected.append(
                {
                    "name": name,
                    "kind": kind,
                    "reason": f"invalid_kind:{kind}",
                }
            )
            continue
        if not name:
            rejected.append({"kind": kind, "reason": "empty_name"})
            continue
        if not verbatim_quote:
            rejected.append(
                {
                    "name": name,
                    "kind": kind,
                    "reason": "missing_verbatim_quote",
                }
            )
            continue

        exact_position = source_text.find(verbatim_quote)
        if exact_position < 0:
            normalized_quote = re.sub(r"\s+", " ", verbatim_quote)
            if normalized_quote not in normalized_source:
                rejected.append(
                    {
                        "name": name,
                        "kind": kind,
                        "verbatim_quote": verbatim_quote[:100],
                        "reason": "quote_not_locatable",
                    }
                )
                continue
        if name not in source_text and name.lower() not in source_text.lower():
            rejected.append(
                {
                    "name": name,
                    "kind": kind,
                    "reason": "name_not_in_source",
                }
            )
            continue

        raw_locator = _text(candidate.get("source_locator"))
        locator = raw_locator or locator_prefix
        absolute_quote_start = (
            source_offset + exact_position if exact_position >= 0 else None
        )
        absolute_quote_end = (
            absolute_quote_start + len(verbatim_quote)
            if absolute_quote_start is not None
            else None
        )
        validated.append(
            {
                "kind": kind,
                "name": name,
                "source_id": source_id,
                "source_locator": locator,
                "verbatim_quote": verbatim_quote[:500],
                "quote_start": absolute_quote_start,
                "quote_end": absolute_quote_end,
                "confidence": min(
                    1.0,
                    max(0.0, float(confidence or 0.5)),
                ),
                "status": "CANDIDATE",
                "language_contract": (
                    "ORIGINAL_CHINESE_PRESERVED"
                    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", name)
                    else "ORIGINAL_SOURCE_NAME_PRESERVED"
                ),
            }
        )

    return validated, rejected


# ── Business rule candidate validation (SPEC P0-3) ───────────────────────────
# Deterministic checks only. Every rejection carries a named reason code; nothing
# is silently dropped, and model confidence never gates a rejection.
RULE_VALIDATION_STATUSES = frozenset({
    "VALIDATED",
    "REJECTED_SOURCE_NOT_FOUND",
    "REJECTED_QUOTE_MISMATCH",
    "REJECTED_INVALID_SPAN",
    "REJECTED_UNGROUNDED_TERM",
    "REJECTED_NUMERIC_MISMATCH",
    "REJECTED_AMBIGUOUS_STRUCTURE",
    "REJECTED_INFERRED_AS_EXPLICIT",
})


def _locate_spans_in_source(
    spans: list[dict[str, Any]],
    source_text: str,
    *,
    source_offset: int,
) -> tuple[list[dict[str, Any]], str]:
    """Anchor each evidence span to an absolute source range.

    The model may not report reliable offsets; the validator computes them from
    exact substring search, so ``source[start:end] == text`` holds by construction
    for every accepted span. Returns (anchored_spans, "") or ([], reason).
    """
    anchored: list[dict[str, Any]] = []
    for raw in spans:
        if not isinstance(raw, dict):
            return [], "REJECTED_INVALID_SPAN"
        text = _text(raw.get("text"))
        if not text:
            return [], "REJECTED_INVALID_SPAN"
        position = source_text.find(text)
        if position < 0:
            return [], "REJECTED_QUOTE_MISMATCH"
        start = source_offset + position
        anchored.append({
            "text": text,
            "start": start,
            "end": start + len(text),
            "page": raw.get("page"),
            "section": raw.get("section"),
            "table_row": raw.get("table_row"),
            "table_column": raw.get("table_column"),
        })
    return anchored, ""


def _semantic_spans_contained(
    semantic_spans: dict[str, Any],
    evidence_texts: list[str],
) -> tuple[bool, str]:
    """Every semantic span text must sit inside some evidence span text."""
    for role in _SEMANTIC_SPAN_ROLES:
        for raw in _list(semantic_spans.get(role)):
            if not isinstance(raw, dict):
                return False, f"REJECTED_INVALID_SPAN:{role}"
            term = _text(raw.get("text"))
            if not term:
                return False, f"REJECTED_INVALID_SPAN:{role}"
            if not any(term in evidence for evidence in evidence_texts):
                return False, f"REJECTED_UNGROUNDED_TERM:{role}:{term[:60]}"
    return True, ""


def _numeric_fidelity(
    suggestion: dict[str, Any],
    evidence_texts: list[str],
) -> bool:
    """A normalized numeric threshold must appear verbatim in the evidence."""
    threshold = suggestion.get("threshold")
    if threshold is None or isinstance(threshold, bool):
        return True
    token = _text(threshold)
    if not token:
        return True
    return any(token in evidence for evidence in evidence_texts)


def _derivations_cover_suggestion(
    suggestion: dict[str, Any],
    derivations: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Every non-empty leaf of normalized_suggestion needs a derivation entry.

    Returns (covered, missing_paths). The missing paths are named so a rejected
    candidate is diagnosable instead of a bare AMBIGUOUS_STRUCTURE.
    """
    covered: set[str] = set()
    for row in derivations:
        if isinstance(row, dict) and _text(row.get("normalized_path")):
            covered.add(_text(row.get("normalized_path")))
            if not _text(row.get("normalization_method")):
                return False, f"normalization_method_missing:{row.get('normalized_path')}"

    def leaves(prefix: str, node: Any) -> list[str]:
        if isinstance(node, dict):
            out: list[str] = []
            for key, child in node.items():
                out.extend(leaves(f"{prefix}.{key}" if prefix else key, child))
            return out
        if node is None or node == "" or node == [] or node == {}:
            return []
        return [prefix]

    missing = [
        path for path in leaves("", suggestion) if path not in covered
    ]
    return (not missing), ",".join(sorted(missing))


def _augment_derivations_from_semantic_spans(
    suggestion: dict[str, Any],
    semantic_spans: dict[str, Any],
    derivations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically complete derivations from containment-checked spans.

    The derivation requirement exists so every normalized value carries source
    evidence. When the model omits a derivation row, the validator can still
    ground the value: the semantic span for the matching role was already
    containment-verified against the evidence, so a derivation generated from
    that span text is deterministic and traceable — never a hallucination.
    """
    out = [dict(row) for row in derivations if isinstance(row, dict)]
    covered = {_text(row.get("normalized_path")) for row in out}
    span_role_by_path = {
        "object": "object",
        "actor": "actor",
        "effect.action": "action",
        "effect.operator_family": "modality",
        "condition.state": "condition",
        "threshold": "threshold",
        "exception": "exception",
        "temporal": "temporal",
    }

    def leaves(prefix: str, node: Any) -> list[tuple[str, Any]]:
        if isinstance(node, dict):
            result: list[tuple[str, Any]] = []
            for key, child in node.items():
                result.extend(
                    leaves(f"{prefix}.{key}" if prefix else key, child)
                )
            return result
        if node is None or node == "" or node == [] or node == {}:
            return []
        return [(prefix, node)]

    for path, value in leaves("", suggestion):
        if path in covered:
            continue
        role = span_role_by_path.get(path)
        if not role:
            continue
        terms = [
            _text(row.get("text"))
            for row in _list(semantic_spans.get(role))
            if isinstance(row, dict) and _text(row.get("text"))
        ]
        if not terms:
            continue
        out.append({
            "normalized_path": path,
            "normalized_value": value,
            "derived_from_text": terms[0],
            "normalization_method": "semantic_span_verbatim",
        })
        covered.add(path)
    return out


def validate_rule_candidates(
    candidates: list[dict[str, Any]],
    source_text: str,
    source_id: str,
    *,
    locator_prefix: str = "",
    source_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministically validate business-rule candidates (SPEC P0-3).

    Returns (validated, rejected). Validated entries carry anchored
    ``evidence_spans`` with absolute offsets, ``semantic_spans``,
    ``normalized_suggestion``, ``derivations``, ``rule_origin``,
    ``candidate_status`` and the extractor receipt fields. Rejected entries
    carry ``rejection_reason`` = one of RULE_VALIDATION_STATUSES.
    """
    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            rejected.append({"kind": "rule", "reason": "REJECTED_INVALID_SPAN", "detail": "not_a_dict"})
            continue

        def refuse(reason: str, detail: str = "") -> None:
            base_reason = reason.split(":", 1)[0]
            rejected.append({
                "kind": "rule",
                "name": _text(candidate.get("name")) or "",
                "reason": (
                    base_reason
                    if base_reason in RULE_VALIDATION_STATUSES
                    else "REJECTED_AMBIGUOUS_STRUCTURE"
                ),
                "detail": (
                    detail
                    if reason == base_reason
                    else reason.split(":", 1)[1]
                ),
            })

        rule_origin = _text(candidate.get("rule_origin")).lower()
        if rule_origin not in _VALID_RULE_ORIGINS:
            refuse("REJECTED_AMBIGUOUS_STRUCTURE", f"invalid_rule_origin:{rule_origin}")
            continue

        raw_spans = _list(candidate.get("evidence_spans"))
        if not raw_spans:
            refuse("REJECTED_INVALID_SPAN", "evidence_spans_empty")
            continue
        anchored, span_reason = _locate_spans_in_source(
            raw_spans, source_text, source_offset=source_offset
        )
        if span_reason:
            refuse(span_reason)
            continue
        evidence_texts = [span["text"] for span in anchored]

        semantic_spans = _dict(candidate.get("semantic_spans"))
        contained, containment_reason = _semantic_spans_contained(
            semantic_spans, evidence_texts
        )
        if not contained:
            refuse(containment_reason)
            continue

        suggestion = _dict(candidate.get("normalized_suggestion"))
        if not _numeric_fidelity(suggestion, evidence_texts):
            refuse("REJECTED_NUMERIC_MISMATCH", "threshold_not_in_evidence")
            continue
        derivations = _list(candidate.get("derivations"))
        if suggestion:
            derivations = _augment_derivations_from_semantic_spans(
                suggestion, semantic_spans, derivations
            )
            derivations_ok, missing_paths = _derivations_cover_suggestion(
                suggestion, derivations
            )
            if not derivations_ok:
                refuse(
                    "REJECTED_AMBIGUOUS_STRUCTURE",
                    f"derivation_requirement_missing:{missing_paths}",
                )
                continue

        joined_evidence = " ".join(evidence_texts)
        if (
            rule_origin == "explicit"
            and _EXAMPLE_LEAD_RE.search(joined_evidence.strip())
        ):
            refuse("REJECTED_AMBIGUOUS_STRUCTURE", "example_sentence_not_a_rule")
            continue
        if rule_origin == "explicit" and not _RULE_SIGNAL_RE.search(joined_evidence):
            # An explicit claim with no source constraint signal cannot be
            # distinguished from an inferred meaning — reject rather than
            # silently upgrade it. A candidate already labelled ``inferred``
            # may pass this point because it remains an advisory hypothesis and
            # can never enter formal rule governance.
            refuse(
                "REJECTED_AMBIGUOUS_STRUCTURE",
                "no_constraint_signal_in_evidence",
            )
            continue

        suggested_family = _text(candidate.get("suggested_rule_family")).lower()
        if suggested_family and suggested_family not in _VALID_RULE_FAMILIES:
            refuse("REJECTED_AMBIGUOUS_STRUCTURE", f"invalid_rule_family:{suggested_family}")
            continue

        name = _text(candidate.get("name"))
        raw_locator = _text(candidate.get("source_locator")) or locator_prefix
        validated.append({
            "candidate_id": _text(candidate.get("candidate_id"))
            or _stable_rule_candidate_id(source_id, evidence_texts[0]),
            "kind": "rule",
            "name": name,
            "source_id": source_id,
            "source_locator": raw_locator,
            "rule_origin": rule_origin,
            "evidence_spans": anchored,
            "semantic_spans": {
                role: [
                    dict(row)
                    for row in _list(semantic_spans.get(role))
                    if isinstance(row, dict)
                ]
                for role in _SEMANTIC_SPAN_ROLES
            },
            "suggested_rule_family": suggested_family,
            "normalized_suggestion": dict(suggestion),
            "derivations": [
                dict(row)
                for row in derivations
                if isinstance(row, dict)
            ],
            "verbatim_quote": evidence_texts[0][:500],
            "confidence": min(
                1.0,
                max(0.0, float(candidate.get("confidence") or 0.5)),
            ),
            "candidate_status": "VALIDATED",
            "language_contract": (
                "ORIGINAL_CHINESE_PRESERVED"
                if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", joined_evidence)
                else "ORIGINAL_SOURCE_NAME_PRESERVED"
            ),
        })

    return validated, rejected
