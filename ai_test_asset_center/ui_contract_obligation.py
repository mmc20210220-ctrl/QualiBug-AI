"""Compile explicit UI scan contracts into formal mainline obligations.

The formal UI observer/protocol is already registered by ``formal_ui_surface``.
This module closes the missing generation link: a customer-submitted UI request
with source references, an exact business operation, an exact actor and an
explicit Playwright expectation becomes a Test Obligation before experiments are
compiled.

No fields are inferred:

* auto-generated screenshot requests have no source refs/expectation and are ignored;
* a URL or installed Playwright package never enables ``ui_browser``;
* operation and actor identities must resolve uniquely in Behavior IR;
* the browser plan must be read-only and contain expect_text or expect_url;
* writes in the business prerequisite retain the ordinary governed cleanup contract.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
from typing import Any

from .formal_ui_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
    install_formal_ui_surface,
)
from .formal_ui_surface_guard import install_formal_ui_read_only_guard
from .obligation_compiler_base import _cleanup_requirement
from .real_id_resolver_base import normalize_path_placeholders
from .test_obligation import dedupe_obligations, make_obligation


UI_CONTRACT_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "qualibug_ui_contract_obligation_context",
    default=None,
)
UI_CONTRACT_RECEIPT_SCHEMA = "qualibug.ui-contract-obligation-bridge.v1"
_SUPPORTED_EXPECTATIONS = frozenset({"expect_text", "expect_url"})
_INTERACTIVE_ACTIONS = frozenset({
    "click",
    "fill",
    "select",
    "select_option",
    "check",
    "uncheck",
    "press",
    "upload",
    "drag",
    "type",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:20]}"


def _gap(request_id: str, code: str, detail: str) -> dict[str, Any]:
    return {
        "gap_id": _stable_id("ui_gap", request_id, code, detail),
        "candidate_id": request_id,
        "code": code,
        "detail": detail[:500],
        "status": "unsupported",
    }


def _source_refs(request: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [dict(row) for row in _list(request.get("source_refs")) if isinstance(row, dict) and row]
    return refs


def _browser_steps(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = _dict(request.get("browser_plan"))
    return [dict(row) for row in _list(plan.get("steps")) if isinstance(row, dict)]


def _is_formal_candidate(request: dict[str, Any]) -> bool:
    """Whether a request asks for a verdict rather than ordinary smoke evidence."""
    if request.get("metadata", {}).get("auto_generated") is True:
        return False
    return bool(
        _source_refs(request)
        or _dict(request.get("success_criteria"))
        or any(
            _text(step.get("action")).lower() in _SUPPORTED_EXPECTATIONS
            for step in _browser_steps(request)
        )
    )


def _resolve_operation(
    request: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    business = _dict(request.get("business_operation"))
    ref = _text(
        request.get("operation_ref")
        or request.get("operation_id")
        or business.get("operation_ref")
        or business.get("operation_id")
    )
    method = _text(request.get("method") or business.get("method")).upper()
    path = _text(request.get("path") or business.get("path"))
    candidates = list(operations)
    if ref:
        candidates = [
            row
            for row in candidates
            if ref in {
                _text(row.get("id")),
                _text(row.get("operation_id")),
                *[_text(value) for value in _list(row.get("source_operation_refs"))],
            }
        ]
    if method:
        candidates = [row for row in candidates if _text(row.get("method")).upper() == method]
    if path:
        normalized = normalize_path_placeholders(path)
        candidates = [
            row
            for row in candidates
            if normalize_path_placeholders(_text(row.get("path") or row.get("raw_path"))) == normalized
        ]
    if not ref and not (method and path):
        return None, "ui_business_operation_identity_missing"
    if len(candidates) != 1:
        return None, f"ui_business_operation_match_count:{len(candidates)}"
    return candidates[0], ""


def _resolve_actor(
    request: dict[str, Any],
    actors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    actor_ref = _text(request.get("actor_ref"))
    actor_role = _text(request.get("actor_role") or request.get("role")).lower()
    if actor_ref:
        matches = [row for row in actors if _text(row.get("id")) == actor_ref]
    elif actor_role:
        matches = [
            row
            for row in actors
            if _text(row.get("role_key") or row.get("role")).lower() == actor_role
        ]
    else:
        return None, "ui_actor_identity_missing"
    if len(matches) != 1:
        return None, f"ui_actor_match_count:{len(matches)}"
    return matches[0], ""


def _validate_request(request: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if _text(request.get("provider")).lower() != "playwright_browser_plan":
        reasons.append("formal_ui_requires_playwright_browser_plan")
    if _text(request.get("execution_mode") or "safe_read_only") != "safe_read_only":
        reasons.append("formal_ui_requires_safe_read_only")
    source_refs = _source_refs(request)
    if not source_refs:
        reasons.append("ui_source_refs_missing")
    plan = _dict(request.get("browser_plan"))
    if not plan:
        reasons.append("ui_browser_plan_missing")
        return reasons
    if _text(plan.get("execution_mode") or "safe_read_only") != "safe_read_only":
        reasons.append("ui_browser_plan_not_read_only")
    steps = _browser_steps(request)
    if not steps:
        reasons.append("ui_browser_steps_missing")
        return reasons
    actions = [_text(row.get("action")).lower() for row in steps]
    if any(action in _INTERACTIVE_ACTIONS for action in actions):
        reasons.append("ui_interactive_cleanup_equivalence_not_implemented")
    expectations = [action for action in actions if action in _SUPPORTED_EXPECTATIONS]
    if not expectations:
        reasons.append("ui_source_expectation_missing")
    for step in steps:
        action = _text(step.get("action")).lower()
        if action == "expect_text" and not (
            _text(step.get("text"))
            and (
                _text(step.get("selector"))
                or isinstance(step.get("locator_intent"), dict)
            )
        ):
            reasons.append("ui_expect_text_binding_incomplete")
        if action == "expect_url" and not _text(step.get("pattern") or step.get("url")):
            reasons.append("ui_expect_url_binding_incomplete")
    return sorted(set(reasons))


def build_ui_contract_obligations(
    behavior_ir: dict[str, Any],
    campaign_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create formal UI obligations from explicit customer scan contracts."""
    install_formal_ui_surface()
    install_formal_ui_read_only_guard()

    context = _dict(campaign_context)
    requests = [
        dict(row)
        for row in _list(context.get("ui_execution_requests"))
        if isinstance(row, dict) and _is_formal_candidate(row)
    ]
    operations = [
        dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    ]
    actors = [
        dict(row)
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict) and _text(row.get("id"))
    ]
    relations = [
        dict(row)
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict)
    ]
    declared = {
        _text(value)
        for value in _list(context.get("declared_adapters"))
        if _text(value)
    }
    runtime_declared = {
        _text(value)
        for value in _list(_dict(context.get("_runtime_contract")).get("declared_adapters"))
        if _text(value)
    }
    declared.update(runtime_declared)

    obligations: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for index, request in enumerate(requests, start=1):
        request_id = _text(request.get("request_id") or request.get("id")) or f"ui_request_{index}"
        reasons = _validate_request(request)
        if "ui_browser" not in declared:
            reasons.append("ui_browser_adapter_not_declared")
        operation, operation_reason = _resolve_operation(request, operations)
        if operation_reason:
            reasons.append(operation_reason)
        actor, actor_reason = _resolve_actor(request, actors)
        if actor_reason:
            reasons.append(actor_reason)
        if reasons:
            gaps.extend(_gap(request_id, "BLOCKED_UI_CONTRACT_BINDING", reason) for reason in sorted(set(reasons)))
            continue

        operation_ref = _text(_dict(operation).get("id"))
        actor_ref = _text(_dict(actor).get("id"))
        cleanup = _cleanup_requirement(
            _dict(operation),
            operations,
            relations,
        )
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "invariant_ref": request_id,
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "ui_request": {
                **dict(request),
                "request_id": request_id,
                "provider": "playwright_browser_plan",
                "execution_mode": "safe_read_only",
                "source_refs": _source_refs(request),
            },
        }
        obligations.append(make_obligation(
            risk_family=RISK_FAMILY,
            subject_refs=[request_id, operation_ref, actor_ref],
            property_spec=property_spec,
            required_actors=[actor_ref],
            required_operations=[operation_ref],
            required_observers=[OBSERVER_ID],
            cleanup_requirement=cleanup,
            source_refs=_source_refs(request),
            relation_refs=[],
            confidence=float(request.get("confidence") or 1.0),
        ))

    return {
        "schema_version": UI_CONTRACT_RECEIPT_SCHEMA,
        "input_count": len(requests),
        "obligations": dedupe_obligations(obligations),
        "coverage_gaps": gaps,
        "compiled_count": len(obligations),
        "blocked_count": len({row["candidate_id"] for row in gaps}),
        "provider_findings_consumed": False,
    }


def bind_ui_contract_context(context: dict[str, Any] | None) -> contextvars.Token:
    return UI_CONTRACT_CONTEXT.set(dict(context or {}))


def reset_ui_contract_context(token: contextvars.Token) -> None:
    UI_CONTRACT_CONTEXT.reset(token)


def current_ui_contract_context() -> dict[str, Any]:
    return dict(UI_CONTRACT_CONTEXT.get() or {})


def install_ui_contract_obligation_bridge() -> None:
    """Patch planning's obligation compiler once, before experiment compilation."""
    from . import discovery_runtime_planning as planning

    marker = "_qualibug_ui_contract_obligation_bridge_installed"
    original_marker = "_qualibug_original_compile_obligations_from_behavior_ir"
    if getattr(planning, marker, False):
        return
    original = getattr(planning, original_marker, None)
    if original is None:
        original = planning.compile_obligations_from_behavior_ir
        setattr(planning, original_marker, original)

    def compile_with_ui_contracts(behavior_ir: dict[str, Any]) -> dict[str, Any]:
        base = dict(original(behavior_ir))
        extra = build_ui_contract_obligations(
            behavior_ir,
            current_ui_contract_context(),
        )
        combined = dedupe_obligations([
            *[dict(row) for row in _list(base.get("obligations")) if isinstance(row, dict)],
            *[dict(row) for row in _list(extra.get("obligations")) if isinstance(row, dict)],
        ])
        base["obligations"] = combined
        base["coverage_gaps"] = [
            *[dict(row) for row in _list(base.get("coverage_gaps")) if isinstance(row, dict)],
            *[dict(row) for row in _list(extra.get("coverage_gaps")) if isinstance(row, dict)],
        ]
        base["ui_contract_obligation_receipt"] = extra
        base["obligation_count"] = len(combined)
        return base

    planning.compile_obligations_from_behavior_ir = compile_with_ui_contracts
    setattr(planning, marker, True)


__all__ = [
    "UI_CONTRACT_RECEIPT_SCHEMA",
    "bind_ui_contract_context",
    "build_ui_contract_obligations",
    "current_ui_contract_context",
    "install_ui_contract_obligation_bridge",
    "reset_ui_contract_context",
]
