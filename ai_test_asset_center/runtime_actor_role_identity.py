"""Source-scoped runtime actor role identity resolution.

Permission contracts use canonical role coordinates while test-account catalogs often
carry localized display labels.  This module binds the two only when a role is declared
by the customer source and the repository's canonical semantic lexicon yields one unique
coordinate.  Ambiguous or unsupported labels stay unchanged and visible; no account or
permission is invented.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .enterprise_knowledge_center._utils import _lexicon_dict


RECEIPT_SCHEMA = "qualibug.runtime-actor-role-identity.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"[\s_\-:：()（）\[\]【】]+", "", _text(value).casefold())


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _source_declared_roles(asset: dict[str, Any]) -> set[str]:
    permission_roles: set[str] = set()
    for row in _list(asset.get("permission_matrix") or asset.get("permissions")):
        if not isinstance(row, dict):
            continue
        role = _text(row.get("role") or row.get("actor") or row.get("principal")).casefold()
        if role:
            permission_roles.add(role)
    if permission_roles:
        return permission_roles

    # A source may declare actors before it declares any permission matrix. This
    # fallback preserves exact role coordinates, but excludes industry inference.
    roles: set[str] = set()
    for row in _list(asset.get("roles")):
        if not isinstance(row, dict) or _text(row.get("source_id")) == "industry_inference":
            continue
        role = _text(row.get("role") or row.get("name") or row.get("id")).casefold()
        if role:
            roles.add(role)
    return roles


def _role_source_terms(asset: dict[str, Any], declared_roles: set[str]) -> dict[str, set[str]]:
    terms: dict[str, set[str]] = defaultdict(set)
    lexicon = _lexicon_dict("role_words")
    for role in declared_roles:
        terms[role].add(role)
        for alias in lexicon.get(role, []):
            value = _text(alias)
            if value:
                terms[role].add(value)

    for row in _list(asset.get("roles")):
        if not isinstance(row, dict):
            continue
        role = _text(row.get("role") or row.get("name") or row.get("id")).casefold()
        if role not in declared_roles:
            continue
        evidence = row.get("evidence")
        if isinstance(evidence, str) and _text(evidence):
            terms[role].add(_text(evidence))
        for item in _list(evidence):
            if not isinstance(item, dict):
                continue
            value = _text(item.get("matched_term") or item.get("quote"))
            if value:
                terms[role].add(value)

    # Reuse accepted, role-scoped identity facts when available.  Generic object
    # aliases are deliberately excluded from actor identity.
    alias_facts = [
        *[
            row for row in _list(_dict(asset.get("business_fact_ledger")).get("items"))
            if isinstance(row, dict)
        ],
        *[
            row for row in _list(_dict(asset.get("chinese_business_glossary")).get("items"))
            if isinstance(row, dict)
        ],
    ]
    for fact in alias_facts:
        if _text(fact.get("kind")) != "TERM_ALIAS":
            continue
        if _text(fact.get("status") or "ACCEPTED") != "ACCEPTED":
            continue
        if _text(fact.get("identity_scope")) != "role":
            continue
        canonical = _text(fact.get("canonical_term"))
        alias = _text(fact.get("alias"))
        canonical_key = canonical.casefold()
        alias_key = alias.casefold()
        if canonical_key in declared_roles:
            terms[canonical_key].update({canonical, alias})
        if alias_key in declared_roles:
            terms[alias_key].update({canonical, alias})
    return terms


def _match_strength(label: str, candidate: str) -> int:
    normalized_label = _norm(label)
    normalized_candidate = _norm(candidate)
    if not normalized_label or not normalized_candidate:
        return 0
    if normalized_label == normalized_candidate:
        return 3
    # Source role labels often add a qualifier (普通/禁用) or a role suffix
    # (人员/运营).  Containment is accepted only for substantive terms and only
    # after the canonical role itself was source-declared.
    candidate_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", normalized_candidate))
    min_length = 2 if candidate_has_cjk else 4
    if len(normalized_candidate) >= min_length and normalized_candidate in normalized_label:
        return 2
    if len(normalized_label) >= min_length and normalized_label in normalized_candidate:
        return 1
    return 0


def resolve_runtime_actor_roles(
    asset: dict[str, Any] | None,
    runtime_actors: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return actors on canonical source role coordinates plus an audit receipt."""
    source_asset = _dict(asset)
    actors = [dict(row) for row in _list(runtime_actors) if isinstance(row, dict)]
    declared_roles = _source_declared_roles(source_asset)
    terms = _role_source_terms(source_asset, declared_roles)
    resolved: list[dict[str, Any]] = []
    coordinates: list[dict[str, Any]] = []

    for actor in actors:
        display_role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        account_ref = _text(
            actor.get("account_ref") or actor.get("email")
            or actor.get("username") or actor.get("id")
        )
        matches: list[tuple[int, str, str]] = []
        for role, aliases in terms.items():
            best_strength = 0
            best_alias = ""
            for alias in aliases:
                strength = _match_strength(display_role, alias)
                if strength > best_strength:
                    best_strength = strength
                    best_alias = _text(alias)
            if best_strength:
                matches.append((best_strength, role, best_alias))
        best = max((strength for strength, _, _ in matches), default=0)
        winners = sorted(
            {(role, alias) for strength, role, alias in matches if strength == best},
            key=lambda item: item[0],
        )
        output = dict(actor)
        if len(winners) == 1:
            canonical_role, matched_term = winners[0]
            output["display_role"] = display_role
            output["role"] = canonical_role
            output["role_identity_status"] = "RESOLVED"
            output["role_identity_source"] = "SOURCE_DECLARED_ROLE_PLUS_CANONICAL_LEXICON"
            status = "RESOLVED"
            candidates = [canonical_role]
        elif winners:
            matched_term = ""
            status = "AMBIGUOUS"
            candidates = [role for role, _ in winners]
            output["role_identity_status"] = status
            output["role_identity_candidates"] = candidates
        else:
            matched_term = ""
            status = "UNRESOLVED"
            candidates = []
            output["role_identity_status"] = status
        resolved.append(output)
        coordinates.append({
            "coordinate_id": _stable_id("actor_role_identity", account_ref, display_role),
            "account_ref": account_ref,
            "display_role": display_role,
            "canonical_role": _text(output.get("role")) if status == "RESOLVED" else "",
            "status": status,
            "matched_term": matched_term,
            "candidate_roles": candidates,
            "automatic_similarity_inference_allowed": False,
        })

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "actor_count": len(actors),
        "source_declared_role_count": len(declared_roles),
        "resolved_count": sum(1 for row in coordinates if row["status"] == "RESOLVED"),
        "ambiguous_count": sum(1 for row in coordinates if row["status"] == "AMBIGUOUS"),
        "unresolved_count": sum(1 for row in coordinates if row["status"] == "UNRESOLVED"),
        "source_declared_roles": sorted(declared_roles),
        "coordinates": coordinates,
        "fail_closed_on_ambiguity": True,
        "identity_policy": "SOURCE_DECLARED_ROLES_PLUS_CANONICAL_LEXICON",
    }
    receipt["receipt_id"] = _stable_id(
        "runtime_actor_role_identity_receipt",
        *[
            f"{row['account_ref']}:{row['display_role']}:{row['status']}:{row['canonical_role']}"
            for row in coordinates
        ],
    )
    return resolved, receipt


__all__ = ["RECEIPT_SCHEMA", "resolve_runtime_actor_roles"]
