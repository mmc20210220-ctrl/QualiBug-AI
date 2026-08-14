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
from typing import Any

FACT_BLOCK_HEADER = "\n\n[GROUNDED BUSINESS FACTS (source-anchored)]\n"

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
_MAX_ITEMS_PER_SECTION = 64

_MAX_RULES_DEFAULT = 64
_MAX_RULES_FLOOR = 24


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
                locator = _text(ref_dict.get("locator") or ref_dict.get("location") or ref_dict.get("source") or ref_dict.get("document"))
                parts.append(f"{kind}:{locator}" if kind and locator else (kind or locator))
            else:
                parts.append(_text(ref))
        return " | ".join(part for part in parts if part)[:160]
    return _text(raw)[:160]


def _extract_rules(payload: dict[str, Any]) -> tuple[list[str], int]:
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
    lines: list[str] = []
    rules_total = 0
    max_rules = _max_rules()
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
            if len(lines) >= max_rules:
                continue
            ref = _source_ref(item_dict)
            lines.append(f"- [rule] {_bounded(text)}" + (f" (source: {ref})" if ref else ""))
            if len(lines) >= max_rules:
                continue
    return lines, rules_total


def _extract_state_machines(payload: dict[str, Any]) -> list[str]:
    """State machines: declared states and transitions, verbatim."""
    lines: list[str] = []
    machines = _list(payload.get("state_machines"))
    if not machines:
        return lines
    for machine in machines[:4]:
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
        if len(lines) >= _MAX_ITEMS_PER_SECTION:
            return lines
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
                or item_dict.get("source")
                or item_dict.get("from")
                or item_dict.get("from_entity")
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
            if len(lines) >= _MAX_ITEMS_PER_SECTION:
                return lines
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
        fields = _list(item_dict.get("fields") or item_dict.get("attributes"))
        field_names = [f.get("name") if isinstance(f, dict) else f for f in fields]
        field_names = [_text(f) for f in field_names if _text(f)]
        parts = [f"entity={_bounded(name, 80)}"]
        if field_names:
            parts.append("fields=" + ",".join(field_names[:16]))
        lines.append(f"- [entity] {'; '.join(parts)}")
        if len(lines) >= _MAX_ITEMS_PER_SECTION:
            return lines
    return lines


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
        lines: list[str] = []
        rule_lines, rules_total = _extract_rules(payload)
        lines.extend(rule_lines)
        lines.extend(_extract_state_machines(payload))
        lines.extend(_extract_relations(payload))
        lines.extend(_extract_entities(payload))
        bounded_lines: list[str] = []
        chars = 0
        for line in lines:
            line = line[:max_chars - chars]
            if len(line) <= 0:
                break
            bounded_lines.append(line)
            chars += len(line) + 1
            if len(bounded_lines) >= max(1, min(int(max_facts or MAX_FACTS), 128)):
                break
            if chars >= max_chars:
                break
        block = FACT_BLOCK_HEADER + "\n".join(bounded_lines) if bounded_lines else ""
        receipt = {
            "status": "CONSUMED" if block else "EMPTY",
            "reason": "source_anchored_fact_retrieval",
            "facts": len(bounded_lines),
            "chars": len(block),
            # The source-rule budget is operator-visible: emitted-vs-total makes
            # any truncation countable instead of silently dropping the overflow.
            "rules_total": rules_total,
            "rules_emitted": len(rule_lines),
            "rules_truncated": max(0, rules_total - len(rule_lines)),
            "max_rules": _max_rules(),
        }
        return block, receipt
    except Exception as exc:
        return "", {
            "status": "FAILED",
            "reason": f"{type(exc).__name__}:{str(exc)[:120]}",
            "facts": 0,
            "chars": 0,
        }
