"""LLM semantic extraction layer: source-backed candidates, never facts.

Phase 3 of SPEC_FORMAT_AGNOSTIC_ENTERPRISE_MATERIAL_COMPREHENSION.

The layer is Chinese-first: Chinese names, roles, states and enterprise terms stay
in their original language. Long sources are processed by bounded overlapping
chunks; any unprocessed range is explicit in the receipt instead of being silently
truncated. Every model output remains a candidate until independent engineering
validation promotes it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "run_semantic_extraction",
    "semantic_extraction_availability",
    "validate_semantic_candidates",
    "validate_rule_candidates",
    "resolve_semantic_rule_extraction_mode",
    "provider_status",
    "SemanticExtractionReceipt",
    "RULE_VALIDATION_STATUSES",
    "SEMANTIC_RULE_EXTRACTION_MODES",
]

_MAX_CHUNK_CHARS = 6000
# Backward-compatible name: one model request still sees at most 6000 chars.
_MAX_SOURCE_CHARS = _MAX_CHUNK_CHARS
_CHUNK_OVERLAP_CHARS = 400
_MAX_CHUNKS_PER_SOURCE = 8
# Backward-compatible per-call candidate ceiling.
_MAX_CANDIDATES_PER_SOURCE = 50
_MAX_CANDIDATES_PER_CHUNK = _MAX_CANDIDATES_PER_SOURCE
_MAX_TOTAL_CANDIDATES_PER_SOURCE = 240
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
#            NOT IMPLEMENTED in this phase; resolves to shadow with a visible
#            fallback_reason until the promotion gates are met.
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
    (SPEC §12.5). ``augment`` is not yet enabled — it resolves to ``shadow``
    with fallback_reason ``augment_not_yet_enabled`` so no phase-one run can
    accidentally change formal Canonical Rule output.
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
            effective = "shadow"
            fallback_reason = "augment_not_yet_enabled"
        else:
            effective = "off"
            fallback_reason = "missing_credentials"
    elif requested == "required":
        if provider_ok:
            effective = "shadow"
            fallback_reason = "augment_not_yet_enabled"
        else:
            effective = "required"
            fallback_reason = "missing_credentials"
    else:  # pragma: no cover - guarded above
        effective = "off"
        fallback_reason = "invalid_requested_mode"

    return {
        "schema_version": _RULE_MODE_RECEIPT_SCHEMA,
        "requested_mode": requested,
        "effective_mode": effective,
        "provider_status": _text(provider_status_value).lower(),
        "fallback_mode": fallback,
        "fallback_reason": fallback_reason,
        "governance_policy_applied": bool(policy),
        "canonical_rule_output_affected": False,
    }

_SYSTEM_PROMPT = """\
你是企业资料结构化抽取器。输入可能是中文 PRD、制度、实施说明、接口说明、
数据字典、权限矩阵或中英文混合资料。

只抽取原文明确出现的候选实体、字段、关系、状态和业务角色。

严格规则：
1. 中文是事实语言。保留原始中文名称，不得先翻译成英文，不得用英文改写中文术语。
2. 每个候选必须包含 verbatim_quote，它必须是输入原文中的连续精确子串。
3. name 必须逐字出现在原文中；不得补全、省略、同义替换或发明。
4. 区分 entity、field、relation、state、actor，不得把角色、状态、字段都标成实体。
5. source_locator 使用输入提供的章节或字符范围；不确定时原样返回输入 locator。
6. 只输出合法 JSON：{"candidates": [...]}。
7. 每个候选格式：
   {"kind":"entity|field|relation|state|actor",
    "name":"原文名称",
    "source_locator":"章节或字符范围",
    "verbatim_quote":"原文精确证据",
    "confidence":0.0-1.0}
8. 没有可抽取内容时返回 {"candidates": []}。
9. 不得推断、翻译、归纳或创造业务事实。

业务规则抽取（kind=rule）额外规则：
10. 找出明确表达约束、许可、禁止、条件、状态变化、不变量、阈值、时间限制的原文，
    输出 kind=rule 候选。每个 rule 候选格式：
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
12. rule_origin=explicit 只用于原文明确陈述的规则；推断、隐含、示例、问题、
    讨论、历史描述一律 rule_origin=inferred，不得标为 explicit。
13. 示例句（例如…/比如…）不得作为规则候选输出。
14. 冲突规则分别输出，不自行裁决。
15. normalized_suggestion 是标准化建议，不是事实；每个标准化字段必须在
    derivations 中给出 derived_from_text（原文证据）与 normalization_method。
16. 保留中文，不翻译后再抽取。
"""

_USER_PROMPT_TEMPLATE = """\
从下面的企业资料片段中抽取结构化候选。
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


def _source_chunks(text: str) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
    """Return bounded overlapping chunks plus explicit budget-skipped ranges."""
    source = str(text or "")
    if not source:
        return [], []
    step = max(1, _MAX_CHUNK_CHARS - _CHUNK_OVERLAP_CHARS)
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(source) and len(chunks) < _MAX_CHUNKS_PER_SOURCE:
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
        # Rule-candidate funnel (SPEC §14). Every count is traceable to
        # candidate ids in candidates / rejected_candidates.
        self.rule_candidates_raw: list[dict[str, Any]] = []
        self.rule_candidates_validated: list[dict[str, Any]] = []
        self.rule_candidates_rejected: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
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
            "candidate_budget": {
                "per_chunk": _MAX_CANDIDATES_PER_CHUNK,
                "per_source": _MAX_TOTAL_CANDIDATES_PER_SOURCE,
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
) -> SemanticExtractionReceipt:
    """Run bounded semantic extraction without silently truncating long sources.

    By default the legacy zero-structured-output trigger is preserved. ``force``
    is available to coverage-ledger callers that need semantic extraction for an
    uncovered span even when another part of the source produced a table or field.
    """
    receipt = SemanticExtractionReceipt(source_id)
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
    chunks, skipped = _source_chunks(text)
    receipt.chunks_total = len(chunks)
    receipt.unprocessed_ranges = skipped

    try:
        from ..llm_reasoning import _get_client

        client = _get_client()
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

    for chunk in chunks:
        receipt.chunks_attempted += 1
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
            result = client.chat_json(
                user_prompt,
                system_prompt=_SYSTEM_PROMPT,
            )
        except Exception as exc:
            chunk_failures += 1
            chunk_receipt["status"] = "FAILED_LLM_ERROR"
            chunk_receipt["error"] = f"{type(exc).__name__}: {str(exc)[:240]}"
            receipt.chunk_receipts.append(chunk_receipt)
            continue

        if not isinstance(result, dict):
            chunk_failures += 1
            chunk_receipt["status"] = "FAILED_MALFORMED_RESPONSE"
            chunk_receipt["error"] = (
                f"LLM returned non-dict: {type(result).__name__}"
            )
            receipt.chunk_receipts.append(chunk_receipt)
            continue
        raw_candidates = result.get("candidates")
        if not isinstance(raw_candidates, list):
            chunk_failures += 1
            chunk_receipt["status"] = "FAILED_MALFORMED_RESPONSE"
            chunk_receipt["error"] = "LLM response missing 'candidates' list"
            receipt.chunk_receipts.append(chunk_receipt)
            continue

        bounded_raw = raw_candidates[:_MAX_CANDIDATES_PER_CHUNK]
        enriched_raw: list[dict[str, Any]] = []
        for candidate in bounded_raw:
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
        raw_all.extend(
            candidate for candidate in enriched_raw if isinstance(candidate, dict)
        )
        validated_all.extend(validated)
        rejected_all.extend(rejected)
        receipt.chunks_completed += 1
        chunk_receipt.update(
            {
                "status": "COMPLETED",
                "raw_count": len(enriched_raw),
                "validated_count": len(validated),
                "rejected_count": len(rejected),
                "candidate_budget_truncated": (
                    len(raw_candidates) > _MAX_CANDIDATES_PER_CHUNK
                ),
            }
        )
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

    receipt.candidates_raw = deduped_raw[:_MAX_TOTAL_CANDIDATES_PER_SOURCE]
    receipt.candidates_validated = deduped_validated[
        :_MAX_TOTAL_CANDIDATES_PER_SOURCE
    ]
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

    source_budget_truncated = (
        len(deduped_raw) > _MAX_TOTAL_CANDIDATES_PER_SOURCE
        or len(deduped_validated) > _MAX_TOTAL_CANDIDATES_PER_SOURCE
    )
    if receipt.chunks_completed == 0:
        first_failure = receipt.chunk_receipts[0] if receipt.chunk_receipts else {}
        receipt.status = (
            _text(first_failure.get("status"))
            if len(receipt.chunk_receipts) == 1
            else "FAILED_ALL_CHUNKS"
        ) or "FAILED_ALL_CHUNKS"
        receipt.error = _text(first_failure.get("error")) or "all semantic extraction chunks failed"
    elif chunk_failures or skipped or source_budget_truncated:
        receipt.status = "COMPLETED_WITH_GAPS"
        gap_reasons: list[str] = []
        if chunk_failures:
            gap_reasons.append(f"{chunk_failures}_chunk_failures")
        if skipped:
            gap_reasons.append("source_chunk_budget_exhausted")
        if source_budget_truncated:
            gap_reasons.append("candidate_budget_exhausted")
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
    return receipt


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
) -> bool:
    """Every non-empty leaf of normalized_suggestion needs a derivation entry."""
    covered: set[str] = set()
    for row in derivations:
        if isinstance(row, dict) and _text(row.get("normalized_path")):
            covered.add(_text(row.get("normalized_path")))
            if not _text(row.get("normalization_method")):
                return False

    def leaves(prefix: str, node: Any) -> list[str]:
        if isinstance(node, dict):
            out: list[str] = []
            for key, child in node.items():
                out.extend(leaves(f"{prefix}.{key}" if prefix else key, child))
            return out
        if node is None or node == "" or node == [] or node == {}:
            return []
        return [prefix]

    for path in leaves("", suggestion):
        if path not in covered:
            return False
    return True


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
        if suggestion and not _derivations_cover_suggestion(suggestion, derivations):
            refuse("REJECTED_AMBIGUOUS_STRUCTURE", "derivation_requirement_missing")
            continue

        joined_evidence = " ".join(evidence_texts)
        if _EXAMPLE_LEAD_RE.search(joined_evidence.strip()):
            refuse("REJECTED_AMBIGUOUS_STRUCTURE", "example_sentence_not_a_rule")
            continue
        if not _RULE_SIGNAL_RE.search(joined_evidence):
            # An "explicit" claim with no constraint signal at all cannot be
            # distinguished from an inferred one — reject rather than trust.
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
