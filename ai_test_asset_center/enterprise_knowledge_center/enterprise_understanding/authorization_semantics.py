"""Resolve source-backed fact authorization semantics once for all understanding layers.

Authorization is narrower than business responsibility and workflow governance.  This module
is the single authority for accepted-fact authorization projection used by both Actor Model
and Business Behavior IR.  An explicit unresolved authorization declaration is fail-closed:
it never falls back to modal verbs or textual allow/deny guesses.
"""
from __future__ import annotations

import re
from typing import Any

from .schema import as_dict, as_list, text

_AUTHORIZATION_DECISIONS = frozenset({"ALLOW", "DENY"})
_GOVERNANCE_DECISIONS = frozenset({"REQUIRE_APPROVAL", "REQUIRE_CONFIRMATION"})
_DECISION_ALIASES = {
    "ALLOWED": "ALLOW",
    "GRANT": "ALLOW",
    "GRANTED": "ALLOW",
    "PERMIT": "ALLOW",
    "PERMITTED": "ALLOW",
    "DENIED": "DENY",
    "FORBID": "DENY",
    "FORBIDDEN": "DENY",
    "PROHIBIT": "DENY",
    "PROHIBITED": "DENY",
    "APPROVAL_REQUIRED": "REQUIRE_APPROVAL",
    "CONFIRMATION_REQUIRED": "REQUIRE_CONFIRMATION",
}
_EXPLICIT_AUTH_ALLOW_RE = re.compile(
    r"(?:有权|拥有权限|授予权限|授权(?:给|由|可)?|允许|准许|permit|authori[sz]ed|has\s+permission)",
    re.I,
)
_EXPLICIT_AUTH_DENY_RE = re.compile(
    r"(?:无权|没有权限|无权限|未授权|未经授权|不允许|禁止访问|拒绝访问|"
    r"not\s+authori[sz]ed|no\s+permission|permission\s+denied)",
    re.I,
)
_APPROVAL_RE = re.compile(
    r"(?:需要|必须|须|需).{0,8}(?:审批|审核)|require.{0,8}approval",
    re.I,
)
_CONFIRMATION_RE = re.compile(
    r"(?:需要|必须|须|需).{0,8}(?:确认)|require.{0,8}confirmation",
    re.I,
)


def _normalized_decision(value: Any) -> str:
    decision = text(value).upper()
    return _DECISION_ALIASES.get(decision, decision)


def _fact_actor_refs(fact: dict[str, Any]) -> list[str]:
    return [
        text(value)
        for value in as_list(as_dict(fact.get("subject")).get("actor_refs"))
        if text(value) and text(value) != "系统"
    ]


def _resolved(
    decision: str,
    *,
    semantic_kind: str,
    derivation: str,
    authority_declared: bool,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "semantic_kind": semantic_kind,
        "authority_declared": authority_declared,
        "resolution_status": "RESOLVED",
        "reason_code": "",
        "derivation": derivation,
        "text_fallback_used": derivation != "explicit_authorization_semantics",
        "automatic_inference_allowed": False,
    }


def resolve_fact_authorization(fact: dict[str, Any]) -> dict[str, Any]:
    """Return one fail-closed authorization/governance decision for an accepted fact.

    Contract:
    - explicit ``authorization_semantics`` is authoritative;
    - explicit unknown/invalid decisions remain UNKNOWN and block authorization projection;
    - generic MUST/MUST_NOT responsibility or prohibition is not authorization;
    - actor-scoped data boundaries may express authorization when modality is directional;
    - approval/confirmation are workflow governance, not Actor ALLOW/DENY contracts.
    """
    explicit_raw = fact.get("authorization_semantics")
    explicit = as_dict(explicit_raw)
    if isinstance(explicit_raw, dict) and explicit:
        decision = _normalized_decision(explicit.get("decision") or explicit.get("effect"))
        if decision in _AUTHORIZATION_DECISIONS:
            return _resolved(
                decision,
                semantic_kind="AUTHORIZATION",
                derivation="explicit_authorization_semantics",
                authority_declared=True,
            )
        if decision in _GOVERNANCE_DECISIONS:
            return _resolved(
                decision,
                semantic_kind="GOVERNANCE",
                derivation="explicit_authorization_semantics",
                authority_declared=False,
            )
        return {
            "decision": "UNKNOWN",
            "declared_decision": decision or "UNKNOWN",
            "semantic_kind": "AUTHORIZATION",
            "authority_declared": True,
            "resolution_status": "UNRESOLVED",
            "reason_code": "FACT_AUTHORIZATION_DECISION_UNRESOLVED",
            "derivation": "explicit_authorization_semantics",
            "text_fallback_used": False,
            "automatic_inference_allowed": False,
        }

    raw = text(fact.get("raw_statement"))
    if _APPROVAL_RE.search(raw):
        return _resolved(
            "REQUIRE_APPROVAL",
            semantic_kind="GOVERNANCE",
            derivation="explicit_workflow_approval_text",
            authority_declared=False,
        )
    if _CONFIRMATION_RE.search(raw):
        return _resolved(
            "REQUIRE_CONFIRMATION",
            semantic_kind="GOVERNANCE",
            derivation="explicit_workflow_confirmation_text",
            authority_declared=False,
        )

    actors = _fact_actor_refs(fact)
    if not actors:
        return {
            "decision": "UNSPECIFIED",
            "semantic_kind": "NONE",
            "authority_declared": False,
            "resolution_status": "NOT_DECLARED",
            "reason_code": "",
            "derivation": "no_actor_authorization_declaration",
            "text_fallback_used": False,
            "automatic_inference_allowed": False,
        }
    if _EXPLICIT_AUTH_DENY_RE.search(raw):
        return _resolved(
            "DENY",
            semantic_kind="AUTHORIZATION",
            derivation="explicit_authorization_deny_text",
            authority_declared=True,
        )
    if _EXPLICIT_AUTH_ALLOW_RE.search(raw):
        return _resolved(
            "ALLOW",
            semantic_kind="AUTHORIZATION",
            derivation="explicit_authorization_allow_text",
            authority_declared=True,
        )

    modality = text(fact.get("modality")).upper()
    scope = as_dict(fact.get("scope"))
    scoped_boundary = bool(any(text(value) for value in scope.values()))
    if scoped_boundary and modality in {"MUST_NOT", "FORBIDDEN", "PROHIBITED", "DENY"}:
        return _resolved(
            "DENY",
            semantic_kind="AUTHORIZATION",
            derivation="actor_scoped_authorization_boundary",
            authority_declared=True,
        )
    if scoped_boundary and modality in {"MAY", "CAN", "ALLOW", "PERMITTED", "ONLY_IF"}:
        return _resolved(
            "ALLOW",
            semantic_kind="AUTHORIZATION",
            derivation="actor_scoped_authorization_boundary",
            authority_declared=True,
        )

    return {
        "decision": "UNSPECIFIED",
        "semantic_kind": "NONE",
        "authority_declared": False,
        "resolution_status": "NOT_DECLARED",
        "reason_code": "",
        "derivation": "business_modality_without_authorization_authority",
        "text_fallback_used": False,
        "automatic_inference_allowed": False,
    }


__all__ = ["resolve_fact_authorization"]
