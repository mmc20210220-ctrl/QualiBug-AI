"""Chinese Semantic Frame context resolution — omitted actor, coreference
candidates, section context (SPEC: QUALIBUG-CHINESE-SEMANTIC-ROOT-FIX-V1,
P0-C: 中文上下文解析).

Contract:
- This stage complements the fact-level context resolution
  (``_chinese_document_context`` / ``_document_ir_context``, which already
  resolve omitted subjects/actors for FACTS with a unique-candidate rule).
  The frame level uses the SAME decision shape — a candidate is injected only
  when it is unique, ambiguity stays unresolved with an explicit reason code,
  and nothing is ever force-bound — but the candidates come from frame slots
  and the clause tree: the 只有…才 subject (ONLY_IF_SUBJECT, same-sentence
  explicit noun — highest evidence priority), unique prior frames in the same
  section (上一原子句), and unique section-heading mentions (章节主题).
- List-parent and table-header context were already injected by P0-B; this
  stage does not repeat them.
- Coreference is resolved at MENTION level only: the raw condition text is
  never rewritten. A condition carrying a coreference token (该/本/此/其/
  上述/前述/对应/相关/当前) gets a resolution candidate from the frame's own
  explicit mentions, then typed prior-frame object/actor mentions, then section
  titles (exact name match only); no unique typed referent →
  COREFERENCE_UNRESOLVED. A unique prior object mention may fill only the
  previously empty object slot, with source-frame lineage; a context-injected
  actor never counts as fresh evidence for that decision.
- Actor mentions are NOT part of the semantic signature. A materialized object
  mention is a typed semantic change, so its signature is recomputed immediately;
  it still needs the existing grounding gate before Behavior IR can consume it.
- The stage is idempotent and fail-closed: every frame stays
  qualibug.chinese-semantic-frame.v1 valid, and every unresolved slot keeps
  its explicit status + reason code.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .chinese_clause_parser import clause_tree_for_block
from .chinese_context_envelope import envelope_from_asset
from .chinese_semantic_frame_compiler import _resolve_frame_block
from .chinese_semantic_ledger_adapter import frames_from_asset
from .chinese_semantic_schema import semantic_signature, validate_semantic_frame

CHINESE_SEMANTIC_CONTEXT_RESOLUTION_SCHEMA = (
    "qualibug.chinese-semantic-context-resolution.v1"
)
CHINESE_SEMANTIC_CONTEXT_RESOLUTION_RECEIPT_SCHEMA = (
    "qualibug.chinese-semantic-context-resolution-receipt.v1"
)

# Coreference signals (language function words — aligned with the fact-level
# reference markers; SPEC §10.1). 该X / 本X / 此X capture the noun up to the
# next modal/negation word or punctuation — never across the verb.
_COREFERENCE_EXPLICIT = re.compile(
    r"(?:该|本|此)(?P<noun>[^，。；,;不得不能不可允许可以必须应当禁止需]{1,8})"
)
_BARE_COREFERENCE_TOKENS = ("其", "上述", "前述", "对应", "相关", "当前")

_PRIOR_FRAME_WINDOW = 3


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


def _envelope_index(asset: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """(source_id, block_id) → envelope entry for every block."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for source in _list(envelope_from_asset(asset).get("sources")):
        source_id = _text(source.get("source_id"))
        for block in _dict(source.get("blocks")).values():
            if isinstance(block, dict):
                index[(source_id, _text(block.get("block_id")))] = block
    return index


def _frames_by_block(frames: list[dict[str, Any]], asset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """block_id → frames attached to it (one frame usually, but never assume)."""
    index: dict[str, list[dict[str, Any]]] = {}
    for frame in frames:
        block = _resolve_frame_block(asset, frame)
        block_id = _text(block.get("block_id"))
        if block_id:
            index.setdefault(block_id, []).append(frame)
    return index


def _known_actor_names(
    asset: dict[str, Any],
    source_id: str,
    frames: list[dict[str, Any]],
) -> dict[str, str]:
    """name → canonical label, from existing registries and frame mentions.

    Exact registry data only — no similarity, no invented names.
    """
    names: dict[str, str] = {}

    def _register(value: Any, canonical: str) -> None:
        item = _norm(value)
        if item:
            names.setdefault(item, _norm(canonical) or item)

    model = _dict(asset.get("enterprise_understanding_model"))
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        canonical = _text(actor.get("actor_id") or actor.get("name"))
        for field in ("name", "role_key", "role"):
            _register(actor.get(field), canonical)
    registry = _dict(asset.get("enterprise_identity_registry"))
    for entity in _list(registry.get("entities")):
        if not isinstance(entity, dict):
            continue
        canonical = _text(entity.get("canonical_label") or entity.get("entity_id"))
        _register(entity.get("canonical_label"), canonical)
        for alias in _list(entity.get("aliases")):
            _register(alias, canonical)
        for label in _list(entity.get("labels")):
            _register(label, canonical)
    for frame in frames:
        if _text(_dict(frame.get("source_span")).get("source_id")) != _text(source_id):
            continue
        for mention in _list(_dict(frame.get("actor")).get("mentions")):
            _register(mention, mention)
    return names


def _same_section(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return _list(first.get("section_block_ids")) == _list(second.get("section_block_ids"))


def _fill_document_context(
    frame: dict[str, Any],
    block: dict[str, Any],
    frame_block_index: dict[str, list[dict[str, Any]]],
    entry_index: dict[tuple[str, str], dict[str, Any]],
    source: dict[str, Any],
) -> dict[str, Any]:
    """Populate section/list/neighbor context on the frame (schema fields)."""
    source_id = _text(source.get("source_id"))
    context = _dict(frame.get("document_context"))
    context["section_path"] = list(_list(block.get("section_path")))
    titles = _list(block.get("section_path"))
    context["heading"] = _norm(titles[-1]) if titles else ""
    context["list_parent"] = _norm(
        _dict(block.get("list_context")).get("list_parent")
    )
    previous_refs: list[str] = []
    next_refs: list[str] = []
    for direction, target in (("previous", previous_refs), ("next", next_refs)):
        for neighbor_id in _list(_dict(block.get("neighbors")).get(direction)):
            neighbor = entry_index.get((source_id, _text(neighbor_id)))
            if not neighbor:
                continue
            if not _same_section(block, neighbor):
                continue
            for neighbor_frame in frame_block_index.get(_text(neighbor_id), []):
                frame_id = _text(neighbor_frame.get("frame_id"))
                if frame_id and frame_id != _text(frame.get("frame_id")):
                    target.append(frame_id)
    context["previous_frame_refs"] = list(dict.fromkeys(previous_refs))
    context["next_frame_refs"] = list(dict.fromkeys(next_refs))
    frame["document_context"] = context
    return context


def _only_if_subject_candidate(asset: dict[str, Any], block: dict[str, Any]) -> str:
    tree = clause_tree_for_block(asset, _text(block.get("block_id")))
    if not tree:
        return ""
    return _norm(_dict(tree.get("actor_mention")).get("raw"))


def _prior_frame_actor_candidates(
    frame: dict[str, Any],
    block: dict[str, Any],
    frames: list[dict[str, Any]],
    entry_index: dict[tuple[str, str], dict[str, Any]],
    source_id: str,
) -> list[str]:
    """Actor mentions of the nearest prior frames in the same section.

    "Prior" means a smaller block order in the same section (上一原子句);
    cross-section frames never contribute (SPEC §10.2 evidence priority).
    """
    current_order = block.get("order")
    candidates: list[tuple[int, str]] = []
    for other in frames:
        if _text(other.get("frame_id")) == _text(frame.get("frame_id")):
            continue
        if _text(_dict(other.get("source_span")).get("source_id")) != _text(source_id):
            continue
        other_entry = entry_index.get(
            (
                source_id,
                _text(_dict(other.get("source_span")).get("document_block_id")),
            )
        )
        if not other_entry:
            continue
        if not _same_section(block, other_entry):
            continue
        try:
            other_order = int(other_entry.get("order"))
            current = int(current_order)
        except (TypeError, ValueError):
            continue
        if other_order >= current:
            continue
        for mention in _list(_dict(other.get("actor")).get("mentions")):
            item = _norm(mention)
            if item:
                candidates.append((other_order, item))
    candidates.sort(key=lambda row: row[0], reverse=True)
    return list(dict.fromkeys(item for _order, item in candidates[:_PRIOR_FRAME_WINDOW]))


def _section_heading_actor_candidates(
    block: dict[str, Any],
    known_names: dict[str, str],
) -> list[str]:
    """Canonical actor names mentioned in the section heading titles.

    Multiple surface names that collapse to one canonical identity count as a
    single candidate (alias-aware); the longest surface name is the display
    mention — mentions stay source-layer words, canonical ids are grounding.
    """
    titles = [_norm(title) for title in _list(block.get("section_path"))]
    if not titles:
        return []
    by_canonical: dict[str, str] = {}
    for name, canonical in sorted(known_names.items(), key=lambda row: -len(row[0])):
        if any(name in title for title in titles):
            current = by_canonical.get(canonical, "")
            if len(name) > len(current):
                by_canonical[canonical] = name
    return sorted(by_canonical.values())


def _prior_frame_coreference_candidates(
    frame: dict[str, Any],
    block: dict[str, Any],
    frames: list[dict[str, Any]],
    entry_index: dict[tuple[str, str], dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    """Typed mentions from the nearest prior frames in the same section.

    Object and actor mentions remain distinct candidates even when their text
    is identical. This prevents a bare pronoun from being forced into a slot
    merely because two different semantic roles share one surface label.
    """
    current_order = block.get("order")
    rows: list[tuple[int, str, str, str]] = []
    for other in frames:
        frame_id = _text(other.get("frame_id"))
        if frame_id == _text(frame.get("frame_id")):
            continue
        if _text(_dict(other.get("source_span")).get("source_id")) != _text(source_id):
            continue
        other_entry = entry_index.get(
            (
                source_id,
                _text(_dict(other.get("source_span")).get("document_block_id")),
            )
        )
        if not other_entry or not _same_section(block, other_entry):
            continue
        try:
            other_order = int(other_entry.get("order"))
            current = int(current_order)
        except (TypeError, ValueError):
            continue
        if other_order >= current:
            continue
        for slot in ("object", "actor"):
            for mention in _list(_dict(other.get(slot)).get("mentions")):
                item = _norm(mention)
                if item:
                    rows.append((other_order, frame_id, slot, item))

    rows.sort(key=lambda row: row[0], reverse=True)
    selected_frame_ids: list[str] = []
    selected: list[tuple[int, str, str, str]] = []
    for row in rows:
        frame_id = row[1]
        if frame_id not in selected_frame_ids:
            if len(selected_frame_ids) >= _PRIOR_FRAME_WINDOW:
                continue
            selected_frame_ids.append(frame_id)
        selected.append(row)

    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for _order, frame_id, slot, mention in selected:
        key = (slot, mention)
        candidate = by_identity.setdefault(
            key,
            {
                "mention": mention,
                "resolved_slot": slot,
                "candidate_source": "prior_frame_same_section",
                "source_frame_refs": [],
            },
        )
        if frame_id and frame_id not in candidate["source_frame_refs"]:
            candidate["source_frame_refs"].append(frame_id)
    return list(by_identity.values())


def _resolve_omitted_actor(
    frame: dict[str, Any],
    asset: dict[str, Any],
    block: dict[str, Any],
    frames: list[dict[str, Any]],
    entry_index: dict[tuple[str, str], dict[str, Any]],
    source_id: str,
    known_names: dict[str, str],
    reason_codes: list[str],
) -> dict[str, Any]:
    actor = _dict(frame.get("actor"))
    if _text(actor.get("resolution_status")) != "OMITTED":
        # Already resolved (this run or a previous one): replay the stored
        # resolution so re-running the stage is deterministic and idempotent.
        previous = _dict(_dict(frame.get("context_resolution")).get("actor_resolution"))
        if previous:
            return dict(previous)
        return {}
    if not _text(_dict(frame.get("source_span")).get("source_id")):
        return {}

    # 1. Same-sentence explicit noun: the 只有…才 subject (highest priority).
    only_if = _only_if_subject_candidate(asset, block)
    if only_if:
        return {
            "method": "only_if_subject",
            "mention": only_if,
            "resolution_status": "RESOLVED",
        }

    # 2. Unique prior frames in the same section (上一原子句).
    prior = _prior_frame_actor_candidates(frame, block, frames, entry_index, source_id)
    # 3. Unique section-heading mentions (章节主题, alias-aware).
    heading = _section_heading_actor_candidates(block, known_names)

    unique_prior = prior[0] if len(prior) == 1 else ""
    unique_heading = heading[0] if len(heading) == 1 else ""

    if unique_prior and unique_heading and unique_prior == unique_heading:
        return {
            "method": "prior_frame_and_section_heading",
            "mention": unique_prior,
            "resolution_status": "RESOLVED",
        }
    if unique_prior and unique_heading:
        reason_codes.append("MULTIPLE_ACTOR_CANDIDATES")
        return {}
    if unique_prior:
        return {
            "method": "unique_prior_frame_in_same_section",
            "mention": unique_prior,
            "resolution_status": "RESOLVED",
        }
    if unique_heading:
        return {
            "method": "unique_section_heading",
            "mention": unique_heading,
            "resolution_status": "RESOLVED",
        }
    if len(prior) > 1 or len(heading) > 1:
        reason_codes.append("MULTIPLE_ACTOR_CANDIDATES")
    return {}


def _coreference_candidates(
    frame: dict[str, Any],
    frames: list[dict[str, Any]],
    block: dict[str, Any],
    entry_index: dict[tuple[str, str], dict[str, Any]],
    source_id: str,
    known_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Mention-level coreference resolution — raw text is never rewritten.

    该X / 本X / 此X name their referent explicitly in the same sentence (X is
    taken from the raw text itself — no inference). Bare pronouns (其/上述/…)
    are resolved only when current explicit mentions or typed prior-frame
    mentions yield exactly one candidate; otherwise the resolution is UNKNOWN.
    """
    previous = [
        dict(row)
        for row in _list(
            _dict(frame.get("context_resolution")).get("coreference_resolutions")
        )
        if isinstance(row, dict)
    ]
    if previous:
        return previous

    resolutions: list[dict[str, Any]] = []
    condition_texts = [
        _norm(_dict(row).get("raw"))
        for row in _list(frame.get("conditions"))
        if _norm(_dict(row).get("raw"))
    ]
    scan_texts = list(condition_texts)
    if not any(
        _COREFERENCE_EXPLICIT.search(text) or any(t in text for t in _BARE_COREFERENCE_TOKENS)
        for text in scan_texts
    ):
        quote = _norm(_dict(frame.get("source_span")).get("quote"))
        if quote:
            scan_texts.append(quote)

    for raw in scan_texts:
        explicit = list(
            dict.fromkeys(
                _norm(match.group("noun"))
                for match in _COREFERENCE_EXPLICIT.finditer(raw)
                if _norm(match.group("noun"))
            )
        )
        bare_signals = [token for token in _BARE_COREFERENCE_TOKENS if token in raw]
        if not explicit and not bare_signals:
            continue
        for noun in explicit:
            resolutions.append(
                {
                    "raw_condition": raw,
                    "signals": ["该/本/此"],
                    "resolved_mention_candidate": noun,
                    "method": "same_sentence_explicit_noun",
                    "resolution_status": "RESOLVED",
                }
            )
        if bare_signals:
            typed_candidates: list[dict[str, Any]] = []
            for slot in ("object", "actor"):
                slot_row = _dict(frame.get(slot))
                context_injected_mentions = {
                    _norm(row.get("mention"))
                    for row in _list(slot_row.get("evidence"))
                    if (
                        isinstance(row, dict)
                        and _text(row.get("origin")) == "context_resolution"
                        and _norm(row.get("mention"))
                    )
                }
                for mention in _list(slot_row.get("mentions")):
                    item = _norm(mention)
                    if item in context_injected_mentions:
                        continue
                    candidate = {
                        "mention": item,
                        "resolved_slot": slot,
                        "candidate_source": "current_frame",
                        "source_frame_refs": [_text(frame.get("frame_id"))],
                    }
                    if item and candidate not in typed_candidates:
                        typed_candidates.append(candidate)
            if not typed_candidates:
                typed_candidates = _prior_frame_coreference_candidates(
                    frame, block, frames, entry_index, source_id
                )
            if not typed_candidates:
                for name in _section_heading_actor_candidates(block, known_names):
                    typed_candidates.append({
                        "mention": name,
                        "resolved_slot": "actor",
                        "candidate_source": "section_heading",
                        "source_frame_refs": [],
                    })
            if len(typed_candidates) == 1:
                candidate = typed_candidates[0]
                candidate_source = _text(candidate.get("candidate_source"))
                resolved_slot = _text(candidate.get("resolved_slot"))
                if candidate_source == "prior_frame_same_section":
                    method = f"unique_prior_frame_{resolved_slot}_same_section"
                elif candidate_source == "section_heading":
                    method = "unique_section_heading_actor"
                else:
                    method = "unique_frame_mention"
                resolutions.append(
                    {
                        "raw_condition": raw,
                        "signals": bare_signals,
                        "resolved_mention_candidate": candidate["mention"],
                        "resolved_slot": resolved_slot,
                        "source_frame_refs": list(candidate.get("source_frame_refs") or []),
                        "method": method,
                        "resolution_status": "RESOLVED",
                    }
                )
            else:
                resolutions.append(
                    {
                        "raw_condition": raw,
                        "signals": bare_signals,
                        "candidate_count": len(typed_candidates),
                        "candidates": [
                            {
                                "mention": row.get("mention"),
                                "resolved_slot": row.get("resolved_slot"),
                                "source_frame_refs": list(row.get("source_frame_refs") or []),
                            }
                            for row in typed_candidates
                        ],
                        "resolution_status": "UNKNOWN",
                    }
                )
    return resolutions


def _materialize_resolved_object_coreference(
    frame: dict[str, Any],
    resolutions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Carry a uniquely typed prior object mention into the frame object slot."""
    previous = [
        dict(row)
        for row in _list(_dict(frame.get("context_resolution")).get("materialized_slots"))
        if isinstance(row, dict)
    ]
    if previous:
        return previous

    object_slot = _dict(frame.get("object"))
    if _list(object_slot.get("mentions")):
        return []
    eligible = [
        row
        for row in resolutions
        if (
            _text(row.get("resolution_status")) == "RESOLVED"
            and _text(row.get("resolved_slot")) == "object"
            and _text(row.get("method")) == "unique_prior_frame_object_same_section"
            and _norm(row.get("resolved_mention_candidate"))
        )
    ]
    mentions = list(
        dict.fromkeys(_norm(row.get("resolved_mention_candidate")) for row in eligible)
    )
    if len(mentions) != 1:
        return []

    mention = mentions[0]
    source_frame_refs = list(
        dict.fromkeys(
            _text(ref)
            for row in eligible
            for ref in _list(row.get("source_frame_refs"))
            if _text(ref)
        )
    )
    evidence_entry = {
        "origin": "context_resolution",
        "method": "unique_prior_frame_object_same_section",
        "mention": mention,
        "source_frame_refs": source_frame_refs,
    }
    evidence = [dict(row) for row in _list(object_slot.get("evidence")) if isinstance(row, dict)]
    if evidence_entry not in evidence:
        evidence.append(evidence_entry)
    object_slot["mentions"] = [mention]
    object_slot["resolution_status"] = "RESOLVED"
    object_slot["evidence"] = evidence
    frame["object"] = object_slot
    frame["resolution"]["semantic_signature"] = semantic_signature(frame)
    return [{
        "slot": "object",
        "mention": mention,
        "method": "unique_prior_frame_object_same_section",
        "source_frame_refs": source_frame_refs,
    }]


def resolve_chinese_semantic_context(asset: dict[str, Any]) -> dict[str, Any]:
    """Resolve omitted actors and coreference candidates for every frame."""
    ledger = _dict(asset.get("chinese_semantic_frame_ledger"))
    frames = frames_from_asset(asset)
    entry_index = _envelope_index(asset)
    frames_by_block = _frames_by_block(frames, asset)

    actor_resolved = 0
    actor_ambiguous = 0
    coreference_resolved = 0
    coreference_unresolved = 0
    coreference_materialized = 0
    unlocated = 0
    reason_counts: Counter = Counter()
    items: list[dict[str, Any]] = []

    for frame in frames:
        block = _resolve_frame_block(asset, frame)
        source_id = _text(_dict(frame.get("source_span")).get("source_id"))
        reason_codes: list[str] = []
        actor_resolution: dict[str, Any] = {}
        coreference_resolutions: list[dict[str, Any]] = []
        materialized_slots: list[dict[str, Any]] = []

        if not block:
            unlocated += 1
        else:
            source = next(
                (
                    row
                    for row in _list(envelope_from_asset(asset).get("sources"))
                    if _text(row.get("source_id")) == source_id
                ),
                {},
            )
            _fill_document_context(frame, block, frames_by_block, entry_index, source)
            known_names = _known_actor_names(asset, source_id, frames)
            actor_resolution = _resolve_omitted_actor(
                frame, asset, block, frames, entry_index, source_id,
                known_names, reason_codes,
            )
            if actor_resolution:
                actor = frame["actor"]
                mention = _norm(actor_resolution.get("mention"))
                mentions = list(_list(actor.get("mentions")))
                if mention and mention not in mentions:
                    mentions.append(mention)
                actor["mentions"] = mentions
                actor["resolution_status"] = "RESOLVED"
                evidence_entry = {
                    "origin": "context_resolution",
                    "method": _text(actor_resolution.get("method")),
                    "mention": mention,
                }
                evidence = list(_list(actor.get("evidence")))
                if evidence_entry not in evidence:
                    evidence.append(evidence_entry)
                actor["evidence"] = evidence
                frame["actor"] = actor
                reason_codes = [
                    code
                    for code in reason_codes
                    if _text(code) != "OMITTED_ACTOR_UNRESOLVED"
                ]
                frame["resolution"]["reason_codes"] = [
                    code
                    for code in _list(frame["resolution"].get("reason_codes"))
                    if _text(code) != "OMITTED_ACTOR_UNRESOLVED"
                ]
                actor_resolved += 1
            else:
                # OMITTED stays OMITTED — the reason code is already carried by
                # the frame; ambiguity gets its own code.
                if "MULTIPLE_ACTOR_CANDIDATES" in reason_codes:
                    actor_ambiguous += 1

            coreference_resolutions = _coreference_candidates(
                frame, frames, block, entry_index, source_id, known_names
            )
            materialized_slots = _materialize_resolved_object_coreference(
                frame,
                coreference_resolutions,
            )
            coreference_materialized += len(materialized_slots)
            for row in coreference_resolutions:
                if _text(row.get("resolution_status")) == "RESOLVED":
                    coreference_resolved += 1
                else:
                    coreference_unresolved += 1
                    if "COREFERENCE_UNRESOLVED" not in reason_codes:
                        reason_codes.append("COREFERENCE_UNRESOLVED")

        for code in reason_codes:
            reason_counts[_text(code)] += 1
        frame["context_resolution"] = {
            "schema": CHINESE_SEMANTIC_CONTEXT_RESOLUTION_SCHEMA,
            "status": "UNLOCATED" if not block else "RESOLVED",
            "actor_resolution": actor_resolution,
            "coreference_resolutions": coreference_resolutions,
            "materialized_slots": materialized_slots,
            "reason_codes": sorted(set(reason_codes)),
        }
        items.append(
            {
                "frame_id": _text(frame.get("frame_id")),
                "actor_resolution": actor_resolution,
                "coreference_resolutions": coreference_resolutions,
                "materialized_slots": materialized_slots,
                "reason_codes": sorted(set(reason_codes)),
            }
        )
        errors = validate_semantic_frame(frame)
        if errors:
            raise ValueError(
                "chinese_semantic_frame_invalid_after_context_resolution:"
                + ",".join(sorted(errors))
            )

    if ledger:
        ledger["context_resolution_receipt"] = {
            "schema": CHINESE_SEMANTIC_CONTEXT_RESOLUTION_RECEIPT_SCHEMA,
            "status": "PARTIAL" if (actor_ambiguous or coreference_unresolved) else "PASS",
            "actor_resolved_count": actor_resolved,
            "actor_ambiguous_count": actor_ambiguous,
            "coreference_resolution_count": coreference_resolved,
            "coreference_unresolved_count": coreference_unresolved,
            "coreference_materialized_slot_count": coreference_materialized,
            "unlocated_frame_count": unlocated,
            "reason_code_counts": dict(sorted(reason_counts.items())),
            "raw_text_never_rewritten": True,
        }
        asset["chinese_semantic_frame_ledger"] = ledger

    asset["chinese_semantic_context_resolution_ledger"] = {
        "schema": CHINESE_SEMANTIC_CONTEXT_RESOLUTION_SCHEMA,
        "items": items,
        "receipt": dict(_dict(ledger.get("context_resolution_receipt"))),
    }
    return asset
