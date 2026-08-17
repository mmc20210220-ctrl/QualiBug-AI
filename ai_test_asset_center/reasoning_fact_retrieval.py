"""
Grounded business-fact retrieval for reasoner engines (reasoner toolization).

Deterministic, read-only extraction of source-anchored business facts from
structured knowledge payloads (Behavior IR-shaped model dicts or the reader's
"business world" JSON) so every reasoner engine can reason over exact declared
facts instead of truncated raw documents.

Contract:
- Facts are quoted verbatim with their source refs; nothing is inferred,
  summarized, or generated here.  Missing values stay missing.
- Output is a bounded text block plus an explicit receipt; any failure yields
  an empty block + FAILED receipt and never blocks reasoning (fail-soft).
- Advisory role only: the block is attention guidance for hypothesis
  generation.  It never merges formal facts, never touches assertions, and
  never feeds the delivery gate.
"""

from __future__ import annotations

import os
import re
from typing import Any, NamedTuple

FACT_BLOCK_HEADER = "\n\n[GROUNDED BUSINESS FACTS (source-anchored)]\n"
SEMANTIC_HYPOTHESIS_BLOCK_HEADER = (
    "\n\n[UNVERIFIED SEMANTIC HYPOTHESES (source-anchored, not facts)]\n"
    "- authority=advisory_only; must_not_satisfy_formal_rule_authority; "
    "must_not_satisfy_customer_delivery_evidence; use_only_to_design_governed_runtime_experiments; "
    "delivery_requires_reproducible_runtime_observation_receipts; "
    "copy_used_candidate_ids_to_semantic_hypothesis_refs; "
    "cross_source_reasoning_must_preserve_every_used_candidate_id_and_source; "
    "surface_conflicts_without_harmonizing_them\n"
)

# The source-anchored fact block is the ONLY channel that carries the
# world-model projection's documented_rules into the Reasoner with correct
# semantics (the reader_json JSON-prefix slices only surface the first few
# rules and drop state_machines/entities/relationships entirely because they
# sit later in the serialized dict).  The rule cap was a hardcoded silent
# truncation; it is now an operator-visible, env-overridable budget with a
# floor (breadth is a floor, never a ceiling).  The emitted-vs-total split is
# receipted so the truncation is countable instead of invisible.
MAX_FACTS = 96
MAX_BLOCK_CHARS = 24000
MAX_FACT_CHARS = 220
_MAX_RULES_DEFAULT = 64
_MAX_RULES_FLOOR = 24


class _FactRow(NamedTuple):
    text: str
    source_identity: str = ""


def _max_rules() -> int:
    """Operator-visible source-rule budget for the grounded fact block.

    ``QUALIBUG_GROUNDED_FACT_MAX_RULES`` overrides the default; the floor
    guarantees an operator override can never narrow comprehension below the
    historical 24-rule baseline.
    """
    raw = (os.environ.get("QUALIBUG_GROUNDED_FACT_MAX_RULES") or "").strip()
    if not raw:
        return _MAX_RULES_DEFAULT
    try:
        return max(int(raw), _MAX_RULES_FLOOR)
    except ValueError:
        return _MAX_RULES_DEFAULT

_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+\-/=]+|(?:api[_\s-]?key|token|secret|password|credential)\s*[:=]\s*[^\s,;]+|sk-[a-z0-9_-]{8,})"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bounded(value: Any, limit: int = MAX_FACT_CHARS) -> str:
    text = _SECRET_RE.sub("[REDACTED]", _text(value))
    return text[:limit] + ("…" if len(text) > limit else "")


def _source_ref(fact: dict[str, Any]) -> str:
    """Short source citation from a fact's provenance fields (verbatim only)."""
    raw = fact.get("source_refs") or fact.get("source_ref") or fact.get("provenance") or fact.get("source")
    if isinstance(raw, list):
        parts: list[str] = []
        for ref in raw[:2]:
            ref_dict = _dict(ref)
            if ref_dict:
                kind = _text(ref_dict.get("kind"))
                locator = _text(
                    ref_dict.get("locator")
                    or ref_dict.get("location")
                    or ref_dict.get("source_id")
                    or ref_dict.get("source")
                    or ref_dict.get("document")
                )
                parts.append(f"{kind}:{locator}" if kind and locator else (kind or locator))
            else:
                parts.append(_text(ref))
        return " | ".join(part for part in parts if part)[:160]
    return _text(raw)[:160]


def _fact_source_identity(fact: dict[str, Any]) -> str:
    """Declared source identity for scheduling; never rendered as new evidence."""
    direct = _text(fact.get("source_id"))
    if direct:
        return direct
    raw = fact.get("source_refs") or fact.get("source_ref") or fact.get("provenance")
    refs = raw if isinstance(raw, list) else [raw] if raw else []
    for ref in refs:
        ref_dict = _dict(ref)
        identity = _text(
            ref_dict.get("source_id")
            or ref_dict.get("document")
            or ref_dict.get("source")
        )
        if identity:
            return identity
    source = fact.get("source")
    if isinstance(source, str):
        return _text(source).split("@", 1)[0]
    return ""


def _source_fair_fact_rows(rows: list[_FactRow]) -> list[_FactRow]:
    """Round-robin declared source queues while preserving within-source order."""
    queues: dict[str, list[_FactRow]] = {}
    for row in rows:
        key = row.source_identity or "__unknown_source__"
        queues.setdefault(key, []).append(row)
    ordered: list[_FactRow] = []
    index = 0
    while True:
        emitted = False
        for queue in queues.values():
            if index < len(queue):
                ordered.append(queue[index])
                emitted = True
        if not emitted:
            return ordered
        index += 1


def _extract_rules(payload: dict[str, Any]) -> tuple[list[_FactRow], int]:
    """Business rules / invariants with source refs. Only verbatim text.

    Accepts both the Behavior-IR-shaped model dict (``business_rules`` /
    ``rules`` / ``invariants`` / ``rule_library``) and the world-model
    projection (``documented_rules``, whose verbatim field is ``rule``).
    Without the ``documented_rules`` key the reasoner's world-model bridge
    silently starved the source-anchored fact block — the two layers used
    different key names for the same declared rules.

    Returns ``(lines, rules_total)`` so the caller can receipt emitted-vs-total
    instead of silently dropping the overflow.
    """
    rows: list[_FactRow] = []
    rules_total = 0
    for key in ("business_rules", "rules", "invariants", "rule_library", "documented_rules"):
        for item in _list(payload.get(key)):
            item_dict = _dict(item)
            if not item_dict:
                continue
            text = _text(item_dict.get("normalized_text") or item_dict.get("text") or item_dict.get("statement") or item_dict.get("invariant") or item_dict.get("rule"))
            if not text:
                continue
            if str(item_dict.get("rule_origin") or "").strip().lower() == "inferred":
                # Advisory/inferred rules stay out of the grounded block; only
                # explicit or unmarked source-declared rules qualify.
                continue
            rules_total += 1
            ref = _source_ref(item_dict)
            rows.append(_FactRow(
                f"- [rule] {_bounded(text)}" + (f" (source: {ref})" if ref else ""),
                _fact_source_identity(item_dict),
            ))
    return _source_fair_fact_rows(rows), rules_total


def _extract_semantic_hypotheses(payload: dict[str, Any]) -> list[_FactRow]:
    """Source-anchored inferred meanings for experiment ideation only.

    These rows are never rendered as ``[rule]`` and never enter the grounded
    fact count as formal authority. The first emitted row carries the explicit
    safety contract so no reasoner can confuse an inferred meaning with a
    declared business rule or customer-delivery evidence.
    """
    rows: list[dict[str, Any]] = []
    rows.extend(
        item for item in _list(payload.get("semantic_hypotheses"))
        if isinstance(item, dict)
    )
    for key in ("business_rules", "rules", "invariants", "rule_library", "documented_rules"):
        rows.extend(
            item
            for item in _list(payload.get(key))
            if isinstance(item, dict)
            and _text(item.get("rule_origin")).lower() == "inferred"
        )

    lines: list[_FactRow] = []
    seen: set[str] = set()
    for item in rows:
        if item.get("formal_rule_authority") is True:
            continue
        statement = _text(
            item.get("statement")
            or item.get("normalized_text")
            or item.get("text")
            or item.get("rule")
            or item.get("verbatim_quote")
        )
        if not statement:
            continue
        candidate_id = _text(item.get("candidate_id") or item.get("id"))
        identity = candidate_id or f"{_source_ref(item)}\n{statement}"
        if identity in seen:
            continue
        seen.add(identity)
        parts = [
            f"candidate_id={_bounded(candidate_id, 100)}" if candidate_id else "",
            f"statement={_bounded(statement)}",
            (
                f"family={_bounded(item.get('suggested_rule_family'), 80)}"
                if _text(item.get("suggested_rule_family"))
                else ""
            ),
        ]
        ref = _source_ref(item)
        if ref:
            parts.append(f"source={_bounded(ref, 160)}")
        lines.append(_FactRow(
            "- [semantic_hypothesis] " + "; ".join(part for part in parts if part),
            _fact_source_identity(item),
        ))
    lines = _source_fair_fact_rows(lines)
    if lines:
        lines[0] = _FactRow(
            SEMANTIC_HYPOTHESIS_BLOCK_HEADER + lines[0].text,
            lines[0].source_identity,
        )
    return lines


def _extract_state_machines(payload: dict[str, Any]) -> list[str]:
    """State machines: declared states and transitions, verbatim."""
    lines: list[str] = []
    machines = _list(payload.get("state_machines"))
    if not machines:
        return lines
    for machine in machines:
        machine_dict = _dict(machine)
        if not machine_dict:
            continue
        name = _text(machine_dict.get("object") or machine_dict.get("name") or machine_dict.get("entity"))
        states = _list(machine_dict.get("states"))
        transitions = _list(machine_dict.get("transitions"))
        if not name and not states and not transitions:
            continue
        parts: list[str] = []
        if name:
            parts.append(f"object={name}")
        if states:
            parts.append("states=" + ",".join(_bounded(s, 40) for s in states[:12]))
        if transitions:
            rendered = []
            for t in transitions[:12]:
                t_dict = _dict(t)
                if t_dict:
                    from_state = _text(t_dict.get("from") or t_dict.get("from_state"))
                    to_state = _text(t_dict.get("to") or t_dict.get("to_state"))
                    if from_state and to_state:
                        rendered.append(f"{from_state}->{to_state}")
                else:
                    text = _bounded(t, 60)
                    if text:
                        rendered.append(text)
            rendered = [r for r in rendered if r]
            if rendered:
                parts.append("transitions=" + ",".join(rendered))
        if parts:
            lines.append(f"- [state_machine] {'; '.join(parts)}")
    return lines


def _extract_relations(payload: dict[str, Any]) -> list[str]:
    """Entity relations: from/to/type only, verbatim.

    Accepts both the model-dict shape (``relations`` / ``dependencies`` with
    ``from_object``/``to_object`` or ``from``/``to``) and the world-model
    projection (``relationships`` with ``from_entity``/``to_entity``).
    """
    lines: list[str] = []
    for key in ("relations", "dependencies", "relationships"):
        for item in _list(payload.get(key)):
            item_dict = _dict(item)
            if not item_dict:
                continue
            source = _text(
                item_dict.get("from_object")
                or item_dict.get("from")
                or item_dict.get("from_entity")
                or item_dict.get("source")
            )
            target = _text(
                item_dict.get("to_object")
                or item_dict.get("target")
                or item_dict.get("to")
                or item_dict.get("to_entity")
            )
            rel_type = _text(item_dict.get("relationship_type") or item_dict.get("type") or item_dict.get("relation"))
            if not source and not target:
                continue
            lines.append(f"- [relation] {source or '?'} -{rel_type or '?'}-> {target or '?'}")
    return lines


def _extract_entities(payload: dict[str, Any]) -> list[str]:
    """Core entity names and declared fields (field lists verbatim)."""
    lines: list[str] = []
    for item in _list(payload.get("entities")):
        item_dict = _dict(item)
        if not item_dict:
            continue
        name = _text(item_dict.get("name") or item_dict.get("entity_alias"))
        if not name:
            continue
        fields = _list(
            item_dict.get("key_business_fields")
            or item_dict.get("fields")
            or item_dict.get("attributes")
        )
        field_names = [f.get("name") if isinstance(f, dict) else f for f in fields]
        field_names = [_text(f) for f in field_names if _text(f)]
        parts = [f"entity={_bounded(name, 80)}"]
        aliases = _list(item_dict.get("aliases"))
        identifiers = _list(item_dict.get("key_identifiers"))
        if aliases:
            parts.append("aliases=" + ",".join(_bounded(value, 60) for value in aliases))
        if identifiers:
            parts.append(
                "identifiers=" + ",".join(_bounded(value, 60) for value in identifiers)
            )
        if field_names:
            parts.append("fields=" + ",".join(field_names[:16]))
        lines.append(f"- [entity] {'; '.join(parts)}")
    return lines


def _extract_permissions(payload: dict[str, Any]) -> list[str]:
    """Role permissions projected from explicit source rows only."""
    lines: list[str] = []
    for role in _list(payload.get("roles")):
        role_dict = _dict(role)
        role_name = _text(role_dict.get("name") or role_dict.get("role"))
        for permission in _list(role_dict.get("permissions")):
            item = _dict(permission)
            if not item:
                continue
            parts = []
            for label, key in (
                ("role", None),
                ("operation", "operation_ref"),
                ("action", "action"),
                ("resource", "resource"),
                ("decision", "decision"),
                ("scope", "scope"),
            ):
                value = role_name if key is None else _text(item.get(key))
                if value:
                    parts.append(f"{label}={_bounded(value, 100)}")
            if not parts:
                continue
            ref = _source_ref(item)
            lines.append(
                f"- [permission] {'; '.join(parts)}"
                + (f" (source: {ref})" if ref else "")
            )
    return lines


def _extract_conflicts(payload: dict[str, Any]) -> list[str]:
    """Cross-source contradictions remain contradictions, never merged rules."""
    lines: list[str] = []
    for raw in _list(payload.get("contradictions") or payload.get("cross_document_conflicts")):
        item = _dict(raw)
        if not item:
            continue
        kind = _text(item.get("kind") or item.get("conflict_type"))
        summary = _text(item.get("summary") or item.get("detail") or item.get("statement"))
        conflict_id = _text(item.get("conflict_id") or item.get("id"))
        parts = [part for part in (
            f"id={_bounded(conflict_id, 100)}" if conflict_id else "",
            f"kind={_bounded(kind, 100)}" if kind else "",
            f"summary={_bounded(summary)}" if summary else "",
        ) if part]
        if not parts:
            continue
        ref = _source_ref(item)
        lines.append(
            f"- [conflict] {'; '.join(parts)}"
            + (f" (source: {ref})" if ref else "")
        )
    return lines


def _extract_gaps(payload: dict[str, Any]) -> list[str]:
    """Explicit parse/coverage gaps prevent missing evidence looking complete."""
    lines: list[str] = []
    raw_gaps = payload.get("gaps")
    if isinstance(raw_gaps, dict):
        gap_rows = [raw_gaps] if raw_gaps else []
    else:
        gap_rows = _list(raw_gaps)
    for raw in gap_rows:
        item = _dict(raw)
        if not item:
            continue
        kind = _text(item.get("kind") or item.get("gap_type") or item.get("code"))
        gap_type = _text(item.get("gap_type") or item.get("reason") or item.get("detail"))
        source_id = _text(item.get("source_id"))
        parts = [part for part in (
            f"kind={_bounded(kind, 100)}" if kind else "",
            f"gap_type={_bounded(gap_type, 140)}" if gap_type and gap_type != kind else "",
            f"source_id={_bounded(source_id, 100)}" if source_id else "",
        ) if part]
        if parts:
            lines.append(f"- [gap] {'; '.join(parts)}")
    return lines


def _fair_fact_rows(
    sections: list[tuple[str, list[_FactRow]]],
) -> list[tuple[str, _FactRow]]:
    """Deterministic round-robin so a large rule list cannot starve a surface."""
    rows: list[tuple[str, _FactRow]] = []
    index = 0
    while True:
        emitted = False
        for name, section_rows in sections:
            if index < len(section_rows):
                rows.append((name, section_rows[index]))
                emitted = True
        if not emitted:
            return rows
        index += 1


def retrieve_grounded_facts(
    payload: dict[str, Any] | None,
    *,
    max_facts: int = MAX_FACTS,
    max_chars: int = MAX_BLOCK_CHARS,
) -> tuple[str, dict[str, Any]]:
    """Deterministically retrieve bounded, source-anchored business facts.

    Returns ``(block, receipt)``.  ``block`` is empty with a FAILED/SKIPPED
    receipt when the payload is unusable; reasoning must never block on it.
    """
    receipt: dict[str, Any] = {"status": "SKIPPED", "reason": "no_structured_payload", "facts": 0, "chars": 0}
    if not isinstance(payload, dict) or not payload:
        return "", receipt
    try:
        all_rule_lines, rules_total = _extract_rules(payload)
        rule_lines = all_rule_lines[:_max_rules()]
        semantic_hypothesis_lines = _extract_semantic_hypotheses(payload)

        def _plain_rows(lines: list[str]) -> list[_FactRow]:
            return [_FactRow(line) for line in lines]

        sections = [
            ("rules", rule_lines),
            ("semantic_hypotheses", semantic_hypothesis_lines),
            ("state_machines", _plain_rows(_extract_state_machines(payload))),
            ("relations", _plain_rows(_extract_relations(payload))),
            ("entities", _plain_rows(_extract_entities(payload))),
            ("permissions", _plain_rows(_extract_permissions(payload))),
            ("conflicts", _plain_rows(_extract_conflicts(payload))),
            ("gaps", _plain_rows(_extract_gaps(payload))),
        ]
        section_totals = {
            name: (rules_total if name == "rules" else len(lines))
            for name, lines in sections
        }
        section_source_totals = {
            name: len({
                row.source_identity for row in (
                    all_rule_lines if name == "rules" else lines
                ) if row.source_identity
            })
            for name, lines in sections
        }
        emitted_by_section = {name: 0 for name, _ in sections}
        emitted_sources_by_section = {name: set() for name, _ in sections}
        bounded_lines: list[str] = []
        chars = 0
        fact_limit = max(1, min(int(max_facts or MAX_FACTS), 128))
        for section_name, row in _fair_fact_rows(sections):
            line = row.text[:max_chars - chars]
            if len(line) <= 0:
                break
            bounded_lines.append(line)
            emitted_by_section[section_name] += 1
            if row.source_identity:
                emitted_sources_by_section[section_name].add(row.source_identity)
            chars += len(line) + 1
            if len(bounded_lines) >= fact_limit:
                break
            if chars >= max_chars:
                break
        block = FACT_BLOCK_HEADER + "\n".join(bounded_lines) if bounded_lines else ""
        section_receipts = {
            name: {
                "total": section_totals[name],
                "emitted": emitted_by_section[name],
                "truncated": max(0, section_totals[name] - emitted_by_section[name]),
                "sources_total": section_source_totals[name],
                "sources_emitted": len(emitted_sources_by_section[name]),
                "sources_truncated": max(
                    0,
                    section_source_totals[name]
                    - len(emitted_sources_by_section[name]),
                ),
            }
            for name, lines in sections
        }
        facts_total = sum(section_totals.values())
        receipt = {
            "status": "CONSUMED" if block else "EMPTY",
            "reason": "source_anchored_fact_retrieval",
            "facts": len(bounded_lines),
            "facts_total": facts_total,
            "facts_truncated": max(0, facts_total - len(bounded_lines)),
            "chars": len(block),
            "budgets": {"max_facts": fact_limit, "max_chars": max_chars, "max_rules": _max_rules()},
            "sections": section_receipts,
            # The source-rule budget is operator-visible: emitted-vs-total makes
            # any truncation countable instead of silently dropping the overflow.
            "rules_total": rules_total,
            "rules_emitted": emitted_by_section["rules"],
            "rules_truncated": max(0, rules_total - emitted_by_section["rules"]),
            "max_rules": _max_rules(),
            "semantic_hypotheses_total": len(semantic_hypothesis_lines),
            "semantic_hypotheses_emitted": emitted_by_section["semantic_hypotheses"],
            "semantic_hypotheses_truncated": max(
                0,
                len(semantic_hypothesis_lines)
                - emitted_by_section["semantic_hypotheses"],
            ),
        }
        return block, receipt
    except Exception as exc:
        return "", {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
            "facts": 0,
            "chars": 0,
        }
