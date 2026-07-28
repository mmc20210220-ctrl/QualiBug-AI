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
    "SemanticExtractionReceipt",
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
_VALID_KINDS = {"entity", "field", "relation", "state", "actor"}

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
