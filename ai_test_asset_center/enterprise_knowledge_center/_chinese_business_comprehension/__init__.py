"""Chinese comprehension compatibility surface with non-destructive mentions.

The historical extractor remains the source-text parser. This package preserves
its API while making its cross-document alias rewrite an explicitly non-authority
compatibility projection. Formal identity is decided later by
enterprise_understanding.identity_resolution.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Iterable

_PACKAGE = __package__.rsplit("._chinese_business_comprehension", 1)[0]
_LEGACY_NAME = f"{_PACKAGE}._chinese_business_comprehension_extractor_v1"
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "_chinese_business_comprehension.py"
_spec = importlib.util.spec_from_file_location(_LEGACY_NAME, _LEGACY_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError(f"cannot load Chinese business extractor: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules.setdefault(_LEGACY_NAME, _legacy)
_spec.loader.exec_module(_legacy)

for _name, _value in vars(_legacy).items():
    if _name.startswith("__") or _name in {"analyze_chinese_business_source", "build_chinese_first_comprehension"}:
        continue
    globals().setdefault(_name, _value)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _annotate_fact_mentions(facts: Iterable[dict[str, Any]]) -> None:
    for fact in facts:
        if not isinstance(fact, dict) or _text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
            continue
        carried_mentions: list[str] = []
        for side in ("subject", "object"):
            slot = dict(_dict(fact.get(side)))
            mentions = [*_list(slot.get("entity_mentions")), *_list(slot.get("entity_refs"))]
            for resolution in _list(slot.get("resolution_evidence")):
                if isinstance(resolution, dict):
                    mentions.extend([resolution.get("mention"), resolution.get("resolved_ref")])
            mentions.extend(carried_mentions)
            normalized = sorted({_text(value) for value in mentions if _text(value)})
            slot["entity_mentions"] = normalized
            slot.setdefault("resolved_entity_refs", [])
            fact[side] = slot
            carried_mentions.extend(
                _text(row.get("mention"))
                for row in _list(slot.get("resolution_evidence"))
                if isinstance(row, dict) and _text(row.get("mention"))
            )


def analyze_chinese_business_source(
    source: dict[str, Any], *, asset: dict[str, Any] | None = None
):
    coverage, facts, glossary = _legacy.analyze_chinese_business_source(source, asset=asset)
    _annotate_fact_mentions(facts)
    return coverage, facts, glossary


def build_chinese_first_comprehension(
    asset: dict[str, Any], parsed_sources: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    enriched = _legacy.build_chinese_first_comprehension(asset, parsed_sources)
    facts = _list(_dict(enriched.get("business_fact_ledger")).get("items"))
    _annotate_fact_mentions(facts)
    projection = _dict(enriched.get("term_alias_identity_merge"))
    projection.update(
        {
            "formal_identity_authority": False,
            "authority": "LEGACY_COMPATIBILITY_PROJECTION",
            "formal_identity_authority_module": "enterprise_understanding.identity_resolution",
        }
    )
    enriched["term_alias_identity_merge"] = projection
    governance = _dict(enriched.get("governance"))
    governance["cross_document_identity_authority"] = "enterprise_understanding.identity_resolution"
    governance["legacy_alias_rewrite_is_fact_authority"] = False
    enriched["governance"] = governance
    return enriched
