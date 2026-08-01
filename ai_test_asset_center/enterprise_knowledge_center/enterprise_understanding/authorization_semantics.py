"""Resolve source-backed fact authorization semantics once for all understanding layers.

Authorization is narrower than business responsibility and workflow governance.  This module
is the single authority for accepted-fact authorization projection used by both Actor Model
and Business Behavior IR.  An explicit unresolved authorization declaration is fail-closed:
it never falls back to modal verbs or textual allow/deny guesses.
"""
from __future__ import annotations

import re
from typing import Any

from .schema import as_dict, as_list, text, unique_text

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
# Only identity/resource ownership coordinates make a scoped actor statement an
# authorization boundary. Time, quantity and process-phase scopes remain business rules.
_IDENTITY_SCOPE_KEYS = frozenset(
    {
        "organization",
        "organization_scope",
        "org",
        "org_scope",
        "tenant",
        "tenant_scope",
        "data_scope",
        "resource_scope",
        "ownership",
        "owner_scope",
        "department",
        "department_scope",
        "warehouse",
        "warehouse_scope",
        "project",
        "project_scope",
        "region",
        "region_scope",
        "self_only",
        "own_data_only",
        "本人",
        "本组织",
        "本部门",
        "本租户",
        "本仓库",
    }
)
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
_SOURCE_MODAL_ALLOW_RE = re.compile(
    r"(?:可以|允许|有权|准许|\bmay\b|\bcan\b|\bpermitted\b)",
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


def _fact_action(fact: dict[str, Any]) -> str:
    action = as_dict(fact.get("action"))
    return text(action.get("canonical") or action.get("raw"))


def _fact_resource_refs(fact: dict[str, Any]) -> list[str]:
    subject = as_dict(fact.get("subject"))
    object_part = as_dict(fact.get("object"))
    return unique_text(
        [
            *as_list(subject.get("entity_refs")),
            *as_list(object_part.get("entity_refs")),
        ]
    )


def _authorization_coordinate_complete(fact: dict[str, Any]) -> bool:
    return bool(
        _fact_actor_refs(fact)
        and _fact_action(fact)
        and _fact_resource_refs(fact)
    )


def _identity_scope_declared(scope: dict[str, Any]) -> bool:
    for raw_key, raw_value in scope.items():
        key = text(raw_key).lower()
        if key in _IDENTITY_SCOPE_KEYS and text(raw_value):
            return True
    return False


def _resolved(
    decision: str,
    *,
    semantic_kind: str,
    derivation: str,
    authority_declared: bool,
    text_fallback_used: bool | None = None,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "semantic_kind": semantic_kind,
        "authority_declared": authority_declared,
        "resolution_status": "RESOLVED",
        "reason_code": "",
        "derivation": derivation,
        "text_fallback_used": (
            derivation != "explicit_authorization_semantics"
            if text_fallback_used is None
            else bool(text_fallback_used)
        ),
        "automatic_inference_allowed": False,
    }


def derive_source_authorization_semantics(
    fact: dict[str, Any],
) -> dict[str, Any]:
    """Project only source-explicit actor authorization onto an existing fact.

    This function does not create a fact or infer a role from business participation.
    It only classifies an already source-backed actor/action/resource coordinate when
    the same statement explicitly grants access, explicitly denies access, or carries
    an identity/data-scope boundary. Generic MUST/MUST_NOT business obligations remain
    business rules.
    """

    raw = text(fact.get("raw_statement"))
    actors = _fact_actor_refs(fact)
    if not raw or not actors:
        return {}

    if _EXPLICIT_AUTH_DENY_RE.search(raw):
        return {
            "decision": "DENY",
            "source_backed": True,
            "derivation": "explicit_authorization_deny_text",
            "coordinate_complete": _authorization_coordinate_complete(fact),
        }
    if _EXPLICIT_AUTH_ALLOW_RE.search(raw):
        return {
            "decision": "ALLOW",
            "source_backed": True,
            "derivation": "explicit_authorization_allow_text",
            "coordinate_complete": _authorization_coordinate_complete(fact),
        }

    modality = text(fact.get("modality")).upper()
    scope = as_dict(fact.get("scope"))
    if _identity_scope_declared(scope):
        if modality in {"MUST_NOT", "FORBIDDEN", "PROHIBITED", "DENY"}:
            return {
                "decision": "DENY",
                "source_backed": True,
                "derivation": "actor_scoped_authorization_boundary",
                "coordinate_complete": _authorization_coordinate_complete(fact),
            }
        if modality in {"MAY", "CAN", "ALLOW", "PERMITTED", "ONLY_IF"}:
            return {
                "decision": "ALLOW",
                "source_backed": True,
                "derivation": "actor_scoped_authorization_boundary",
                "coordinate_complete": _authorization_coordinate_complete(fact),
            }

    if (
        modality in {"MAY", "CAN", "ALLOW", "PERMITTED"}
        and _SOURCE_MODAL_ALLOW_RE.search(raw)
        and _authorization_coordinate_complete(fact)
    ):
        return {
            "decision": "ALLOW",
            "source_backed": True,
            "derivation": "source_modal_authorization",
            "coordinate_complete": True,
        }
    return {}


def resolve_fact_authorization(fact: dict[str, Any]) -> dict[str, Any]:
    """Return one fail-closed authorization/governance decision for an accepted fact.

    Contract:
    - explicit ``authorization_semantics`` is authoritative;
    - explicit unknown/invalid decisions remain UNKNOWN and block authorization projection;
    - generic MUST/MUST_NOT responsibility or prohibition is not authorization;
    - actor-scoped identity/data boundaries may express authorization when modality is directional;
    - time, quantity and process scopes never become authorization by themselves;
    - approval/confirmation are workflow governance, not Actor ALLOW/DENY contracts.
    """
    explicit_raw = fact.get("authorization_semantics")
    explicit = as_dict(explicit_raw)
    if isinstance(explicit_raw, dict):
        decision = _normalized_decision(explicit.get("decision") or explicit.get("effect"))
        derivation = text(explicit.get("derivation")) or "explicit_authorization_semantics"
        if decision in _AUTHORIZATION_DECISIONS:
            return _resolved(
                decision,
                semantic_kind="AUTHORIZATION",
                derivation=derivation,
                authority_declared=True,
                text_fallback_used=False,
            )
        if decision in _GOVERNANCE_DECISIONS:
            return _resolved(
                decision,
                semantic_kind="GOVERNANCE",
                derivation=derivation,
                authority_declared=False,
                text_fallback_used=False,
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
    source_semantics = derive_source_authorization_semantics(fact)
    source_decision = _normalized_decision(source_semantics.get("decision"))
    if source_decision in _AUTHORIZATION_DECISIONS:
        return _resolved(
            source_decision,
            semantic_kind="AUTHORIZATION",
            derivation=text(source_semantics.get("derivation"))
            or "source_authorization_semantics",
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


__all__ = [
    "derive_source_authorization_semantics",
    "resolve_fact_authorization",
]
