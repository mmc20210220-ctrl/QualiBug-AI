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

from .state_guard_coordinates import (
    close_state_guard_coordinates,
    synchronize_rule_library_from_facts,
)

_PACKAGE = __package__.rsplit("._chinese_business_comprehension", 1)[0]
_LEGACY_NAME = f"{_PACKAGE}._chinese_business_comprehension_extractor_v1"
_LEGACY_PATH = Path(__file__).resolve().parents[1] / "_chinese_business_comprehension_extractor_v1.py"
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
    close_state_guard_coordinates(facts)
    return coverage, facts, glossary


def build_chinese_first_comprehension(
    asset: dict[str, Any], parsed_sources: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    sources = list(parsed_sources)
    enriched = _legacy.build_chinese_first_comprehension(asset, sources)
    facts = _list(_dict(enriched.get("business_fact_ledger")).get("items"))
    _annotate_fact_mentions(facts)
    state_guard_receipt = close_state_guard_coordinates(facts)
    synchronize_rule_library_from_facts(enriched, facts)
    enriched["state_guard_coordinate_closure_receipt"] = state_guard_receipt
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

    # Reclassify compatibility rules from the same fact authorization authority.
    # The historical extractor used "actor present" as permission evidence, which
    # incorrectly upgraded ordinary responsibilities to authorization scenarios.
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.authorization_semantics import (
        resolve_fact_authorization,
    )
    for raw_rule in _list(enriched.get("rule_library")):
        if not isinstance(raw_rule, dict):
            continue
        rule = raw_rule
        authorization = resolve_fact_authorization(_dict(rule.get("semantic_contract")))
        kind = _text(authorization.get("semantic_kind"))
        declared = bool(authorization.get("authority_declared"))
        resolved_auth = kind in {"AUTHORIZATION", "AUTHORIZATION_DELEGATION"} and declared
        if not _list(_dict(rule.get("semantic_contract")).get("state_effects")):
            rule["risk_type"] = "authorization" if resolved_auth else "business_logic"
            rule["rule_type"] = "permission" if resolved_auth else "business_rule"
        rule["authorization_decision"] = _text(authorization.get("decision"))
        rule["authorization_semantic_kind"] = kind
        rule["authorization_declared"] = declared
        rule["authorization_status"] = (
            _text(authorization.get("resolution_status")) or "NOT_DECLARED"
        )
        rule["authorization_derivation"] = _text(authorization.get("derivation"))

    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.role_inheritance_authority import (
        materialize_role_inheritance_contracts,
    )
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.segregation_of_duties_authority import (
        materialize_sod_contracts,
    )
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.fact_permission_matrix import (
        materialize_fact_permission_matrix,
    )
    materialize_role_inheritance_contracts(enriched, sources)
    materialize_sod_contracts(enriched, sources)
    return materialize_fact_permission_matrix(enriched)
