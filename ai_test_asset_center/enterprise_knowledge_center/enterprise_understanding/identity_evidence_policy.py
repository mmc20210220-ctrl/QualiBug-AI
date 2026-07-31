"""Classify source identity statements before the identity graph consumes them.

This policy does not create aliases. It only narrows already extracted TERM_ALIAS
facts into hard identity evidence or candidate-only definitions.
"""
from __future__ import annotations

import re
from typing import Any

from .schema import as_dict, as_list, text

_FORMULA = re.compile(
    r"[=\uff1d+\uff0b\u00d7*\u00f7/<>]|"
    r"(?:\u52a0|\u51cf|\u4e58|\u9664).*(?:\u91d1\u989d|\u6570\u91cf|\u5355\u4ef7)"
)
_SHORT_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,23}$")


def classify_identity_fact(fact: dict[str, Any]) -> str:
    explicit = text(fact.get("identity_evidence_class"))
    if explicit:
        return explicit
    statement = text(fact.get("raw_statement"))
    alias = text(fact.get("alias"))
    if _FORMULA.search(alias) or _FORMULA.search(statement):
        return "DEFINITION"
    if re.search(
        r"\u66f4\u540d\u4e3a|\u6539\u79f0|\u539f\u540d|\u65e7\u79f0|\u65b0\u540d\u79f0",
        statement,
    ):
        return "RENAMING"
    if re.search(
        r"\u4ee5\u4e0b\u7b80\u79f0|\u7b80\u79f0\u4e3a|\u7b80\u79f0|\u7f29\u5199|aka|a\.k\.a",
        statement,
        re.I,
    ):
        return "EXPLICIT_ABBREVIATION"
    if (
        re.search(r"[\uff08(][^()\uff08\uff09]{1,32}[\uff09)]", statement)
        and _SHORT_CODE.fullmatch(alias)
    ):
        return "EXPLICIT_ABBREVIATION"
    if re.search(
        r"\u53c8\u79f0|\u4e5f\u79f0|\u53c8\u540d|\u4e5f\u53eb|\u53c8\u53eb|\u4ea6\u79f0|"
        r"\u7b49\u540c\u4e8e|\u76f8\u5f53\u4e8e|\u540c\u4e49\u4e8e|"
        r"\u5373(?!\u53ef|\u4f7f|\u4fbf|\u5c06|\u523b|\u65e5|\u4ee4)|"
        r"also known as|also called",
        statement,
        re.I,
    ):
        return "EXPLICIT_ALIAS"
    if re.search(
        r"\u662f\u6307|\u6307\u7684\u662f|\u5b9a\u4e49\u4e3a|\u5b9a\u4e49\u662f|\u5373\u4e3a|\u5373\u662f",
        statement,
    ):
        return "DEFINITION"
    return "POSSIBLE_EQUIVALENCE"


def apply_identity_evidence_policy(asset: dict[str, Any]) -> dict[str, Any]:
    facts = as_list(as_dict(asset.get("business_fact_ledger")).get("items"))
    distribution: dict[str, int] = {}
    for fact in facts:
        if not isinstance(fact, dict) or text(fact.get("kind")) != "TERM_ALIAS":
            continue
        evidence_class = classify_identity_fact(fact)
        fact["identity_evidence_class"] = evidence_class
        fact["formal_identity_union_allowed"] = evidence_class in {
            "EXPLICIT_ALIAS",
            "EXPLICIT_ABBREVIATION",
            "RENAMING",
        }
        distribution[evidence_class] = distribution.get(evidence_class, 0) + 1
    asset["identity_evidence_policy_receipt"] = {
        "schema": "qualibug.enterprise-identity-evidence-policy-receipt.v1",
        "classified_fact_count": sum(distribution.values()),
        "class_distribution": dict(sorted(distribution.items())),
        "definition_is_identity": False,
        "formula_is_identity": False,
        "parenthetical_text_is_abbreviation_only_for_short_code": True,
        "automatic_similarity_merge_allowed": False,
    }
    return asset
