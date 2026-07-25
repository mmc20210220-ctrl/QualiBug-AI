"""LLM semantic extraction layer: candidates, not facts.

Phase 3 of SPEC_FORMAT_AGNOSTIC_ENTERPRISE_MATERIAL_COMPREHENSION.

Trigger: a source that after Phase 1-2 still produces zero entities/fields/
permissions AND has non-empty text. Per-source trigger, not bulk.

Output contract: LLM only produces CANDIDATES. Each candidate carries a
verbatim_quote that must be locatable in the original text. Engineering-side
validation is mandatory — LLM is NOT a fact authority.

Status: always CANDIDATE. Stored in `semantic_candidates`, never mixed into
`data_tables` / `data_fields` directly.
"""
from __future__ import annotations

import json
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

# Maximum text chars sent to LLM per source (budget governance)
_MAX_SOURCE_CHARS = 6000
# Maximum candidates accepted per source
_MAX_CANDIDATES_PER_SOURCE = 50

_SYSTEM_PROMPT = """\
You are a structured information extractor. Given a document excerpt, extract
candidate entities, fields, relations, states, and actors.

STRICT RULES:
1. Every candidate MUST include a verbatim_quote: an exact substring from the
   source text that proves the candidate exists. No paraphrasing.
2. The name MUST appear literally in the source text. No synonyms or inventions.
3. Output ONLY valid JSON: {"candidates": [...]}
4. Each candidate: {"kind": "entity|field|relation|state|actor",
   "name": "<exact name from text>", "source_locator": "<section/heading>",
   "verbatim_quote": "<exact substring from source>", "confidence": 0.0-1.0}
5. If nothing can be extracted, return {"candidates": []}
6. Do NOT invent, infer, or hallucinate. Only extract what is literally present.
"""

_USER_PROMPT_TEMPLATE = """\
Extract structured candidates from this document excerpt.
Source ID: {source_id}
Filename: {filename}

--- DOCUMENT START ---
{text}
--- DOCUMENT END ---

Return JSON: {{"candidates": [...]}}
"""


def semantic_extraction_availability(requested: bool = True) -> dict[str, Any]:
    """Report once whether the semantic layer can run at all.

    Checked before the per-source loop so that a layer-wide outage surfaces as a
    single explicit signal instead of one identical failure per source. Makes no
    provider call — configuration only.

    Each triggered source costs a provider round-trip, so callers opt in rather
    than having a paid network dependency attached to every asset build.
    """
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
    """Receipt for one source's semantic extraction attempt."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.triggered = False
        self.candidates_raw: list[dict[str, Any]] = []
        self.candidates_validated: list[dict[str, Any]] = []
        self.rejected_candidates: list[dict[str, Any]] = []
        self.error: str = ""
        self.status: str = "NOT_TRIGGERED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.semantic-extraction-receipt.v1",
            "source_id": self.source_id,
            "triggered": self.triggered,
            "status": self.status,
            "candidates_raw_count": len(self.candidates_raw),
            "candidates_validated_count": len(self.candidates_validated),
            "rejected_count": len(self.rejected_candidates),
            "candidates": self.candidates_validated,
            "rejected_candidates": self.rejected_candidates[:20],
            "error": self.error,
        }


def run_semantic_extraction(
    text: str,
    *,
    source_id: str,
    filename: str = "",
    existing_tables: int = 0,
    existing_fields: int = 0,
    existing_permissions: int = 0,
) -> SemanticExtractionReceipt:
    """Run LLM semantic extraction for a source that produced zero structured output.

    Trigger condition: existing_tables + existing_fields + existing_permissions == 0
    AND text is non-empty.

    Args:
        text: Decoded source text.
        source_id: Source identifier.
        filename: Original filename.
        existing_tables: Count of tables already extracted by Phase 1-2.
        existing_fields: Count of fields already extracted.
        existing_permissions: Count of permissions already extracted.

    Returns:
        SemanticExtractionReceipt with validated candidates or explicit error.
    """
    receipt = SemanticExtractionReceipt(source_id)

    # ── Trigger condition ──
    if existing_tables + existing_fields + existing_permissions > 0:
        receipt.status = "NOT_TRIGGERED_HAS_OUTPUT"
        return receipt

    if not text or not text.strip():
        receipt.status = "NOT_TRIGGERED_EMPTY_TEXT"
        return receipt

    receipt.triggered = True

    # ── Get LLM client ──
    try:
        from ..llm_reasoning import _get_client
        client = _get_client()
    except Exception as exc:
        receipt.status = "FAILED_CLIENT_UNAVAILABLE"
        receipt.error = f"LLM client unavailable: {type(exc).__name__}: {str(exc)[:200]}"
        logger.warning(
            "Semantic extraction failed for %s: LLM client unavailable: %s",
            source_id, exc,
        )
        return receipt

    if not client.config.enabled:
        receipt.status = "FAILED_LLM_NOT_CONFIGURED"
        receipt.error = "LLM is not configured (enabled=False)"
        logger.warning("Semantic extraction skipped for %s: LLM not configured", source_id)
        return receipt

    # ── Build prompt ──
    truncated_text = text[:_MAX_SOURCE_CHARS]
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        source_id=source_id,
        filename=filename,
        text=truncated_text,
    )

    # ── Call LLM ──
    try:
        result = client.chat_json(user_prompt, system_prompt=_SYSTEM_PROMPT)
    except Exception as exc:
        receipt.status = "FAILED_LLM_ERROR"
        receipt.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.warning(
            "Semantic extraction LLM call failed for %s: %s",
            source_id, exc,
        )
        return receipt

    if not isinstance(result, dict):
        receipt.status = "FAILED_MALFORMED_RESPONSE"
        receipt.error = f"LLM returned non-dict: {type(result).__name__}"
        return receipt

    raw_candidates = result.get("candidates")
    if not isinstance(raw_candidates, list):
        receipt.status = "FAILED_MALFORMED_RESPONSE"
        receipt.error = "LLM response missing 'candidates' list"
        return receipt

    receipt.candidates_raw = raw_candidates[:_MAX_CANDIDATES_PER_SOURCE]

    # ── Engineering-side validation ──
    validated, rejected = validate_semantic_candidates(
        receipt.candidates_raw, text, source_id
    )
    receipt.candidates_validated = validated
    receipt.rejected_candidates = rejected
    receipt.status = "COMPLETED"

    logger.info(
        "Semantic extraction for %s: %d raw → %d validated, %d rejected",
        source_id, len(receipt.candidates_raw), len(validated), len(rejected),
    )
    return receipt


def validate_semantic_candidates(
    candidates: list[dict[str, Any]],
    source_text: str,
    source_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate LLM-produced candidates against source text.

    Validation rules:
    1. verbatim_quote must be locatable in source_text (exact substring match)
    2. name must appear literally in source_text
    3. kind must be one of: entity, field, relation, state, actor

    Returns:
        (validated_candidates, rejected_candidates)
    """
    validated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    valid_kinds = {"entity", "field", "relation", "state", "actor"}

    for candidate in candidates:
        if not isinstance(candidate, dict):
            rejected.append({"raw": str(candidate)[:200], "reason": "not_a_dict"})
            continue

        kind = str(candidate.get("kind") or "").strip().lower()
        name = str(candidate.get("name") or "").strip()
        verbatim_quote = str(candidate.get("verbatim_quote") or "").strip()
        confidence = candidate.get("confidence", 0.5)

        # Validate kind
        if kind not in valid_kinds:
            rejected.append({
                "name": name, "kind": kind,
                "reason": f"invalid_kind:{kind}",
            })
            continue

        # Validate name is non-empty
        if not name:
            rejected.append({"kind": kind, "reason": "empty_name"})
            continue

        # Validate verbatim_quote is locatable in source
        if not verbatim_quote:
            rejected.append({
                "name": name, "kind": kind,
                "reason": "missing_verbatim_quote",
            })
            continue

        if verbatim_quote not in source_text:
            # Try normalized match (whitespace collapse)
            normalized_source = re.sub(r"\s+", " ", source_text)
            normalized_quote = re.sub(r"\s+", " ", verbatim_quote)
            if normalized_quote not in normalized_source:
                rejected.append({
                    "name": name, "kind": kind,
                    "verbatim_quote": verbatim_quote[:100],
                    "reason": "quote_not_locatable",
                })
                continue

        # Validate name appears in source
        if name not in source_text:
            # Try case-insensitive
            if name.lower() not in source_text.lower():
                rejected.append({
                    "name": name, "kind": kind,
                    "reason": "name_not_in_source",
                })
                continue

        # Passed all validation
        validated.append({
            "kind": kind,
            "name": name,
            "source_id": source_id,
            "source_locator": str(candidate.get("source_locator") or ""),
            "verbatim_quote": verbatim_quote[:500],
            "confidence": min(1.0, max(0.0, float(confidence or 0.5))),
            "status": "CANDIDATE",
        })

    return validated, rejected
