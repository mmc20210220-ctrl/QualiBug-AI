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
        # Rules that a structured parser classified explicitly (e.g. a UI/UX
        # requirements document declaring rule_type=ui_state_consistency /
        # risk_type=ui) are not legacy compatibility rules: their family is
        # the source document's own declaration and must survive the legacy
        # reclassification, or the whole UI surface chain (rule -> invariant
        # kind=ui -> obligation) silently disappears. Generic rule: never
        # overwrite an explicit structured classification with the legacy
        # fallback.
        _structured_classification = (
            _text(rule.get("rule_type")) not in {"", "business_rule", "business_logic"}
            or _text(rule.get("risk_type")) not in {"", "business_logic"}
        )
        if (
            not _structured_classification
            and not _list(_dict(rule.get("semantic_contract")).get("state_effects"))
        ):
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


V1_EXTRACTOR_DEMOTION_RECEIPT_SCHEMA = "qualibug.v1-extractor-demotion-receipt.v1"

_FRAME_CONFIRMATION_REASONS = frozenset(
    {
        "FRAME_GROUNDED",
        "FRAME_UNGROUNDED",
        "NO_FRAME_FOR_RULE",
    }
)


def _v1_frame_grounded(frame: dict[str, Any]) -> bool:
    """A frame is grounded when the P0-D grounding engine resolved at least
    one technical ref the frame channel can emit relations from
    (GROUNDED/PARTIAL status; PENDING frames are not grounded). Mirrors
    behavior_ir_core's P0-E gate exactly."""
    if not isinstance(frame, dict):
        return False
    grounding = _dict(frame.get("technical_grounding"))
    return bool(
        _list(grounding.get("operation_refs"))
        or _list(grounding.get("actor_refs"))
        or _list(grounding.get("entity_refs"))
    )


def _v1_frame_for_rule(
    rule_id: str,
    statement: str,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    """Locate the frame for a rule (identity mirrors
    chinese_semantic_grounding._find_rule: origin fact id ↔
    zh_business:<fact tail>, then statement text)."""
    by_fact_id: dict[str, dict[str, Any]] = {}
    by_fact_tail: dict[str, dict[str, Any]] = {}
    by_statement: dict[str, dict[str, Any]] = {}
    for frame in frames:
        fact_id = _text(_dict(frame.get("origin")).get("origin_fact_id"))
        if fact_id:
            by_fact_id.setdefault(fact_id, frame)
            tail = fact_id.split(":", 1)[-1]
            if tail:
                by_fact_tail.setdefault(tail, frame)
        quote = _text(_dict(frame.get("source_span")).get("quote")).strip()
        if quote:
            by_statement.setdefault(quote, frame)
    if not rule_id and not statement:
        return {}
    frame = by_fact_id.get(rule_id)
    if frame:
        return frame
    if rule_id.startswith("zh_business:"):
        tail = rule_id.split(":", 1)[-1]
        frame = by_fact_tail.get(tail) or by_fact_tail.get(tail[-20:])
        if frame:
            return frame
    return by_statement.get(_text(statement).strip(), {})


def apply_v1_extractor_frame_confirmation(asset: dict[str, Any]) -> dict[str, Any]:
    """P0-E phase 2 confirmation gate over v1 extractor candidate rules.

    Every rule the legacy fixed-vocabulary regex extractor produced
    (semantic_candidate=True) is decided against the Chinese Semantic Frame
    SSOT after P0-D grounding:
    - frame grounded → CONFIRMED (SSOT wins; FRAME_GROUNDED)
    - frame exists but ungrounded → FALLBACK_UNGROUNDED (legacy fallback,
      receipted)
    - no frame for the rule → UNCONFIRMED_NO_FRAME (legacy fallback,
      receipted)
    - no frame ledger → compat path: rules are untouched (no
      frame_confirmation field) and the receipt only records
      V1_EXTRACTOR_NO_FRAME_LEDGER.

    The confirmation status rides on the rule (asset layer) and is carried
    onto the Behavior IR invariant (behavior_ir_core transparent pass), so
    the demotion is observable end-to-end and the ledger-less assets stay
    byte-identical. Returns the receipt.
    """
    ledger = _dict(asset.get("chinese_semantic_frame_ledger"))
    frames = [
        row for row in _list(ledger.get("items")) if isinstance(row, dict)
    ]
    frame_ledger_present = bool(frames)
    kind_counts: dict[str, int] = {}
    candidate_rule_count = 0
    confirmed_count = 0

    for rule in _list(asset.get("rule_library")):
        if not isinstance(rule, dict) or rule.get("semantic_candidate") is not True:
            continue
        candidate_rule_count += 1
        if not frame_ledger_present:
            # Compat path: no SSOT exists, so nothing is a fallback.
            continue
        rule_id = _text(rule.get("rule_id"))
        statement = _text(rule.get("statement"))
        frame = _v1_frame_for_rule(rule_id, statement, frames)
        if frame and _v1_frame_grounded(frame):
            rule["frame_confirmation"] = "CONFIRMED"
            rule["frame_confirmation_reason"] = "FRAME_GROUNDED"
            kind_counts["V1_CANDIDATE_CONFIRMED_BY_FRAME"] = (
                kind_counts.get("V1_CANDIDATE_CONFIRMED_BY_FRAME", 0) + 1
            )
            confirmed_count += 1
        elif frame:
            rule["frame_confirmation"] = "FALLBACK_UNGROUNDED"
            rule["frame_confirmation_reason"] = "FRAME_UNGROUNDED"
            kind_counts["V1_CANDIDATE_FRAME_UNGROUNDED_FALLBACK"] = (
                kind_counts.get("V1_CANDIDATE_FRAME_UNGROUNDED_FALLBACK", 0) + 1
            )
        else:
            rule["frame_confirmation"] = "UNCONFIRMED_NO_FRAME"
            rule["frame_confirmation_reason"] = "NO_FRAME_FOR_RULE"
            kind_counts["V1_CANDIDATE_NO_FRAME_FOR_RULE"] = (
                kind_counts.get("V1_CANDIDATE_NO_FRAME_FOR_RULE", 0) + 1
            )

    if not frame_ledger_present:
        kind_counts["V1_EXTRACTOR_NO_FRAME_LEDGER"] = candidate_rule_count

    receipt = {
        "schema": V1_EXTRACTOR_DEMOTION_RECEIPT_SCHEMA,
        "frame_ledger_present": frame_ledger_present,
        "candidate_rule_count": candidate_rule_count,
        "confirmed_count": confirmed_count,
        "kind_counts": dict(sorted(kind_counts.items())),
        "reason_codes": (
            ["V1_EXTRACTOR_CANDIDATE_DEMOTION"]
            if frame_ledger_present and kind_counts
            else []
        ),
        "contract": {
            "gate": "v1_extractor_frame_confirmation",
            "frame_grounded_wins": True,
            "legacy_fallback_observable": True,
            "no_ledger_behavior_unchanged": True,
        },
    }
    asset["v1_extractor_demotion_receipt"] = receipt
    return receipt
