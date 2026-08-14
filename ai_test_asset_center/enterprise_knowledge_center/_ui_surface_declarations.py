"""UI surface declaration chain: visible UI material -> surface entities -> executable checks.

AGENTS.md four-link requirement for ``ui_state_consistency``: a UI defect class is
reachable only when the source material declares the surface (a UI browser plan) and the
experiment can execute it. This module is the **surface declaration link** of that chain:

* it parses visible UI requirement/design documents (screens, regions, role visibility
  matrices, action/state matrices, typed requirements, Gherkin oracles) into generic
  **surface entities** — pages, controls, buttons — with no hardcoded page names;
* it compiles surface obligations (page display state, button behaviour, menu isolation)
  into **executable checks**: either a governed browser plan (Playwright steps with
  professional expectations) or a DOM assertion (declared control/state vocabulary judged
  against the rendered page);
* read-only surface checks compile as ``safe_read_only`` browser plans that need no
  cleanup; interactive surface checks (a click that changes state) require the source
  document to declare cleanup equivalence (``approved_sandbox_write`` +
  ``interaction_contract`` + persistent state probes), otherwise they fail closed into a
  named gap — never a silently degraded read-only probe.

The parser is data-driven: every control name, role, state and URL comes from the
document itself. No benchmark page, feature or rule vocabulary is hardcoded here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from . import _parsing_mechanics as _core

_INSTALL_MARKER = "_qualibug_ui_surface_declaration_parser_installed"
_ORIGINAL_MARKER = "_qualibug_original_uiux_requirements_from_json"
_READ_ONLY_MODE = "safe_read_only"
_WRITE_MODE = "approved_sandbox_write"

# Professional expectations that are pure DOM assertions (no interaction).
_READ_ONLY_EXPECTATIONS = frozenset({
    "expect_text",
    "expect_url",
    "expect_visible",
    "expect_hidden",
    "expect_enabled",
    "expect_disabled",
    "expect_value",
    "expect_checked",
    "expect_unchecked",
    "expect_count",
    "expect_attribute",
    "expect_css",
    "expect_role",
    "expect_accessible_name",
    "expect_dimensions",
    "expect_in_viewport",
    "expect_not_obscured",
    "expect_no_horizontal_overflow",
    "expect_no_console_errors",
    "expect_no_failed_requests",
})

# Visibility values a role matrix may declare for a control.
_VISIBLE_STATES = frozenset({"visible", "visible_enabled", "enabled"})
_HIDDEN_STATES = frozenset({"hidden", "absent", "disabled"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_key(value: Any) -> str:
    return "".join(ch.lower() for ch in _text(value) if ch.isalnum() or ch == "_")


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    return "ui_surface_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _source_ref(source_id: str, locator: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "version": "",
        "locator": locator,
        "kind": "ui_surface_declaration",
        "quote_hash": "",
    }


def _pick(row: dict[str, Any], *names: str) -> Any:
    by_key = {_norm_key(key): value for key, value in row.items()}
    for name in names:
        key = _norm_key(name)
        if key in by_key:
            return by_key[key]
    return None


def _screen_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("screens", "pages", "页面"):
        for item in _list(payload.get(key)):
            if isinstance(item, dict) and _text(_pick(item, "id", "screen_id")):
                rows.append(dict(item))
    return rows


def _matrix(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict) and value:
            return dict(value)
    return {}


# ── surface entity extraction ──


def _control_entities(payload: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    """Controls/features from role-visibility and action-state matrices.

    Generic: a ``role_visibility``-style matrix (role -> feature -> visibility) yields
    per-role menu-isolation surface entities; an ``order_action_matrix``-style matrix
    (state -> action -> enabled/disabled) yields per-state button-behaviour surface
    entities. Key names are recognised by shape, never by industry vocabulary.
    """
    entities: list[dict[str, Any]] = []
    role_matrix = _matrix(payload, "role_visibility", "role_visibility_matrix", "role_menu", "menu_visibility")
    if role_matrix:
        for role, features in role_matrix.items():
            if not isinstance(features, dict) or not _text(role):
                continue
            for feature, visibility in features.items():
                if not _text(feature):
                    continue
                visibility_text = _text(visibility).lower()
                expected = (
                    "hidden"
                    if any(token in visibility_text for token in _HIDDEN_STATES)
                    else "visible"
                )
                entities.append({
                    "entity_id": _stable_id("control", source_id, role, feature),
                    "entity_type": "control",
                    "source_id": source_id,
                    "name": _text(feature),
                    "role": _text(role),
                    "expected_state": expected,
                    "declared_visibility": visibility_text,
                    "basis": "role_visibility_matrix",
                    "source_locator": f"role_visibility.{role}.{feature}",
                })
    action_matrix = _matrix(
        payload,
        "order_action_matrix",
        "action_matrix",
        "button_matrix",
        "state_action_matrix",
        "action_state_matrix",
    )
    if action_matrix:
        for state, actions in action_matrix.items():
            if not isinstance(actions, dict) or not _text(state):
                continue
            for action, state_value in actions.items():
                if not _text(action):
                    continue
                state_text = _text(state_value).lower()
                expected = (
                    "disabled"
                    if any(token in state_text for token in _HIDDEN_STATES)
                    else "enabled"
                )
                entities.append({
                    "entity_id": _stable_id("button", source_id, state, action),
                    "entity_type": "button",
                    "source_id": source_id,
                    "name": _text(action),
                    "state_context": _text(state),
                    "expected_state": expected,
                    "declared_state": state_text,
                    "basis": "action_state_matrix",
                    "source_locator": f"action_matrix.{state}.{action}",
                })
    return entities


def _requirement_entities(payload: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    """Typed requirement rows -> surface entities (page display state obligations).

    Requirements that declare a visible/forbidden state vocabulary (``negative_examples``
    or a state rule) become page-display-state surface entities; the vocabulary is the
    document's own words, never inferred.
    """
    entities: list[dict[str, Any]] = []
    for item in _list(payload.get("requirements")):
        if not isinstance(item, dict):
            continue
        req_id = _text(_pick(item, "id", "requirement_id"))
        rule = _text(_pick(item, "rule", "statement", "text"))
        req_type = _text(_pick(item, "type", "requirement_type"))
        if not req_id or not rule:
            continue
        negative = [
            _text(value)
            for value in _list(_pick(item, "negative_examples", "forbidden_states"))
            if _text(value)
        ]
        if not negative:
            # A requirement that declares interaction semantics (confirmation /
            # second confirmation / click flow) is an interactive surface
            # obligation: it cannot become a read-only DOM assertion. It is
            # kept as a distinct entity so the compiler can fail it closed
            # with a named gap instead of silently dropping it (AGENTS.md: no
            # silent truncation; interactive UI steps need cleanup
            # equivalence).
            if any(
                token in f"{req_type} {rule}".lower()
                for token in (
                    "confirm",
                    "确认",
                    "二次",
                    "interactive",
                    "交互",
                    "click",
                    "点击",
                    "submit",
                    "提交",
                )
            ):
                entities.append({
                    "entity_id": _stable_id("interactive", source_id, req_id),
                    "entity_type": "interactive_obligation",
                    "source_id": source_id,
                    "name": req_id,
                    "rule": rule,
                    "requirement_type": req_type,
                    "screen": _text(_pick(item, "screen", "page", "screen_id")),
                    "basis": "typed_requirement",
                    "source_locator": f"requirements[{req_id}]",
                })
            continue
        entities.append({
            "entity_id": _stable_id("display", source_id, req_id),
            "entity_type": "page_display_state",
            "source_id": source_id,
            "name": req_id,
            "rule": rule,
            "requirement_type": req_type,
            "negative_examples": negative,
            "screen": _text(_pick(item, "screen", "page", "screen_id")),
            "basis": "typed_requirement",
            "source_locator": f"requirements[{req_id}]",
        })
    return entities


def _oracle_entities(payload: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    """Gherkin oracles -> surface entities with executable expectation statements."""
    entities: list[dict[str, Any]] = []
    for item in _list(payload.get("oracles")):
        if not isinstance(item, dict):
            continue
        oracle_id = _text(_pick(item, "id", "oracle_id"))
        then_rows = [
            _text(value)
            for value in _list(_pick(item, "then", "expected", "expectations"))
            if _text(value)
        ]
        given = _text(_pick(item, "given", "precondition"))
        when = _text(_pick(item, "when", "action"))
        if not oracle_id or not then_rows:
            continue
        entities.append({
            "entity_id": _stable_id("oracle", source_id, oracle_id),
            "entity_type": "oracle",
            "source_id": source_id,
            "name": oracle_id,
            "given": given,
            "when": when,
            "expectations": then_rows,
            "basis": "gherkin_oracle",
            "source_locator": f"oracles[{oracle_id}]",
        })
    return entities


def extract_ui_surface_entities(
    payload: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    """Parse visible UI material into generic surface entities (pages/controls/buttons)."""
    entities: list[dict[str, Any]] = []
    for screen in _screen_rows(payload):
        screen_id = _text(_pick(screen, "id", "screen_id"))
        entities.append({
            "entity_id": _stable_id("page", source_id, screen_id),
            "entity_type": "page",
            "source_id": source_id,
            "name": _text(_pick(screen, "name", "title")) or screen_id,
            "screen_id": screen_id,
            "url": _text(_pick(screen, "url", "path", "route")),
            "regions": [
                _text(value)
                for value in _list(_pick(screen, "regions", "areas"))
                if _text(value)
            ],
            "viewport": dict(_pick(screen, "viewport") or {}),
            "basis": "screens_declaration",
            "source_locator": f"screens[{screen_id}]",
        })
    entities.extend(_control_entities(payload, source_id))
    entities.extend(_requirement_entities(payload, source_id))
    entities.extend(_oracle_entities(payload, source_id))
    return entities


# ── executable check compilation ──


def _locator_intent_for(control_name: str) -> dict[str, str]:
    """A locator intent is built ONLY from the document's own control name.

    The name is treated as visible text first (``get_by_text``) — the document named the
    control, the page renders that name. No CSS selector, test id or role is invented.
    """
    return {"text": _text(control_name)}


def _read_only_plan_steps(start_url: str) -> list[dict[str, Any]]:
    return [{"action": "goto", "url": start_url}]


def _compile_control_check(
    entity: dict[str, Any],
    start_url: str,
) -> dict[str, Any]:
    expected = _text(entity.get("expected_state"))
    if entity.get("entity_type") == "button":
        action = "expect_disabled" if expected == "disabled" else "expect_enabled"
    else:
        action = "expect_hidden" if expected == "hidden" else "expect_visible"
    step: dict[str, Any] = {
        "action": action,
        "locator_intent": _locator_intent_for(_text(entity.get("name"))),
        "timeout_ms": 5000,
    }
    return {
        "check_id": _text(entity.get("entity_id")),
        "check_kind": "control_state",
        "control": _text(entity.get("name")),
        "expected_state": expected,
        "role": _text(entity.get("role")),
        "state_context": _text(entity.get("state_context")),
        "plan_steps": [*_read_only_plan_steps(start_url), step],
    }


def _compile_display_check(
    entity: dict[str, Any],
    start_url: str,
) -> dict[str, Any]:
    negative = [_text(value) for value in _list(entity.get("negative_examples")) if _text(value)]
    steps: list[dict[str, Any]] = _read_only_plan_steps(start_url)
    for forbidden in negative:
        steps.append({
            "action": "expect_hidden",
            "locator_intent": _locator_intent_for(forbidden),
            "timeout_ms": 5000,
        })
    return {
        "check_id": _text(entity.get("entity_id")),
        "check_kind": "page_display_state",
        "rule": _text(entity.get("rule")),
        "negative_examples": negative,
        "screen": _text(entity.get("screen")),
        "plan_steps": steps,
    }


def _compile_oracle_check(
    entity: dict[str, Any],
    start_url: str,
) -> dict[str, Any]:
    expectations = [_text(value) for value in _list(entity.get("expectations")) if _text(value)]
    steps: list[dict[str, Any]] = _read_only_plan_steps(start_url)
    for expectation in expectations:
        steps.append({
            "action": "expect_text",
            "locator_intent": _locator_intent_for(expectation[:60]),
            "timeout_ms": 5000,
        })
    return {
        "check_id": _text(entity.get("entity_id")),
        "check_kind": "oracle_expectation",
        "given": _text(entity.get("given")),
        "when": _text(entity.get("when")),
        "expectations": expectations,
        "plan_steps": steps,
    }


def compile_ui_surface_checks(
    payload: dict[str, Any],
    source_id: str,
    *,
    start_url_resolver: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile surface entities into executable checks (read-only browser plans).

    Returns (checks, gaps). Every check is a read-only browser plan whose steps are
    professional DOM assertions — no interaction, so no cleanup is required. A surface
    entity without a resolvable page URL becomes a named gap; interactive obligations
    (declared write/confirmation flows) fail closed because the document must declare
    cleanup equivalence before a click/fill plan may be compiled — this module never
    invents a compensation path.
    """
    checks: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    pages = {}
    for row in _screen_rows(payload):
        screen_key = _text(_pick(row, "id", "screen_id"))
        if screen_key and _text(_pick(row, "url", "path", "route")):
            pages[screen_key] = row
    default_url = ""
    if isinstance(start_url_resolver, dict):
        default_url = _text(start_url_resolver.get("url") or "")
    if not pages and default_url:
        pages["_default"] = {"url": default_url}

    for entity in extract_ui_surface_entities(payload, source_id):
        entity_type = _text(entity.get("entity_type"))
        if entity_type == "page":
            continue
        if entity_type == "interactive_obligation":
            # Fail closed: an interactive surface obligation (confirmation /
            # click flow) is only executable when the source document itself
            # declares the write-mode browser contract with cleanup
            # equivalence (interaction_contract + persistent state probes).
            # This module never invents a compensation path — the obligation
            # stays a visible gap until the material declares it.
            gaps.append({
                "gap_type": "ui_surface_interaction_requires_cleanup_equivalence",
                "reason_code": "UI_SURFACE_INTERACTION_CLEANUP_NOT_DECLARED",
                "check_id": _text(entity.get("entity_id")),
                "name": _text(entity.get("name")),
                "rule": _text(entity.get("rule")),
                "screen": _text(entity.get("screen")),
                "source_locator": _text(entity.get("source_locator")),
                "status": "unsupported",
            })
            continue
        start_url = ""
        if entity_type == "page_display_state":
            screen = _text(entity.get("screen"))
            if screen and screen in pages:
                start_url = _text(pages[screen].get("url"))
            elif default_url:
                start_url = default_url
            else:
                gaps.append({
                    "gap_type": "ui_surface_page_url_missing",
                    "reason_code": "UI_SURFACE_PAGE_URL_MISSING",
                    "check_id": _text(entity.get("entity_id")),
                    "source_locator": _text(entity.get("source_locator")),
                    "status": "unsupported",
                })
                continue
            checks.append(_compile_display_check(entity, start_url))
        elif entity_type == "oracle":
            start_url = default_url or (
                _text(next(iter(pages.values())).get("url")) if pages else ""
            )
            if not start_url:
                gaps.append({
                    "gap_type": "ui_surface_page_url_missing",
                    "reason_code": "UI_SURFACE_PAGE_URL_MISSING",
                    "check_id": _text(entity.get("entity_id")),
                    "source_locator": _text(entity.get("source_locator")),
                    "status": "unsupported",
                })
                continue
            checks.append(_compile_oracle_check(entity, start_url))
        elif entity_type in {"control", "button"}:
            start_url = default_url or (
                _text(next(iter(pages.values())).get("url")) if pages else ""
            )
            if not start_url:
                gaps.append({
                    "gap_type": "ui_surface_page_url_missing",
                    "reason_code": "UI_SURFACE_PAGE_URL_MISSING",
                    "check_id": _text(entity.get("entity_id")),
                    "source_locator": _text(entity.get("source_locator")),
                    "status": "unsupported",
                })
                continue
            checks.append(_compile_control_check(entity, start_url))
    return checks, gaps


def _per_spec_contracts(
    payload: dict[str, Any],
    source_id: str,
    spec: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile contracts for ONE declared page.

    Screen-scoped entities (requirements/oracles that name a screen) are attached only to
    the spec whose ``ui_spec_id`` matches that screen; global entities (role-visibility
    controls, action-state buttons, screen-less oracles) ride on every declared page so
    their read-only DOM assertions run against each declared URL. Every contract's
    ``start_url`` is the spec's own declared URL — a page URL is never guessed.
    """
    spec_url = _text(_pick(spec, "url"))
    screen_id = ""
    spec_id = _text(spec.get("ui_spec_id") or "")
    if ":" in spec_id:
        screen_id = spec_id.rsplit(":", 1)[-1]
    scoped: dict[str, Any] = {"payload": payload, "source_id": source_id}
    checks, gaps = compile_ui_surface_checks(
        payload,
        source_id,
        start_url_resolver={"url": spec_url},
    )
    kept_checks: list[dict[str, Any]] = []
    kept_gaps: list[dict[str, Any]] = []
    for check in checks:
        check_screen = _text(check.get("screen"))
        if check_screen and check_screen != screen_id:
            # Screen-scoped check belongs to another declared page.
            continue
        kept_checks.append(check)
    for gap in gaps:
        if _text(gap.get("screen")) and _text(gap.get("screen")) != screen_id:
            continue
        kept_gaps.append(gap)
    return _contracts_from_checks(kept_checks, source_id), kept_gaps


def _contracts_from_checks(
    checks: list[dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for check in checks:
        steps = [dict(row) for row in _list(check.get("plan_steps")) if isinstance(row, dict)]
        if not steps:
            continue
        contract_id = _text(check.get("check_id"))
        if contract_id in seen:
            continue
        seen.add(contract_id)
        contracts.append({
            "schema_version": "qualibug.ui-formal-contract.v2",
            "contract_id": contract_id,
            "title": f"UI surface check: {_text(check.get('control') or check.get('rule') or contract_id)}",
            "check_kind": _text(check.get("check_kind")),
            "control": _text(check.get("control")),
            "role": _text(check.get("role")),
            "state_context": _text(check.get("state_context")),
            "negative_examples": list(_list(check.get("negative_examples"))),
            "expectations": list(_list(check.get("expectations"))),
            "ui_request": {
                "request_id": contract_id,
                "title": f"UI surface check: {_text(check.get('control') or contract_id)}",
                "provider": "playwright_browser_plan",
                "start_url": _text(steps[0].get("url") or ""),
                "execution_mode": _READ_ONLY_MODE,
                "browser_plan": {
                    "execution_mode": _READ_ONLY_MODE,
                    "steps": steps,
                },
                "success_criteria": {"action": "all_ui_surface_expectations"},
                "metadata": {"source_declared": True, "surface_declaration": True},
            },
            "source_refs": [_source_ref(source_id, _text(check.get("check_id")))],
            "source_id": source_id,
            "status": "accepted",
            "derivation": "explicit",
            "confidence": 1.0,
        })
    return contracts


def compile_ui_surface_contracts(
    payload: dict[str, Any],
    source_id: str,
    *,
    start_url_resolver: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile surface entities into governed formal UI contracts (browser plans).

    Read-only surface checks compile into ``playwright_browser_plan`` contracts in
    ``safe_read_only`` mode — the read-only guard accepts them and no cleanup is needed.
    Interactive surface obligations are refused with a named gap unless the document
    itself declares the full cleanup-equivalence contract; this module never fabricates
    compensation.
    """
    checks, gaps = compile_ui_surface_checks(
        payload, source_id, start_url_resolver=start_url_resolver
    )
    return _contracts_from_checks(checks, source_id), gaps


# ── parser patch ──


def install_ui_surface_declaration_parser() -> None:
    """Attach surface declarations and compiled contracts to UIUX requirement specs.

    ``_parse_source`` lives in ``_parsing_mechanics`` and resolves
    ``_uiux_requirements_from_json`` from that module's globals, so the add-on
    must patch the mechanics module (``_core``) rather than the ``_parsing``
    facade — a facade-only patch is a silent no-op after the mechanics split.
    """
    if getattr(_core, _INSTALL_MARKER, False):
        return
    original = getattr(
        _core,
        _ORIGINAL_MARKER,
        _core._uiux_requirements_from_json,
    )
    setattr(_core, _ORIGINAL_MARKER, original)

    def with_surface_declarations(
        payload: dict[str, Any],
        source_id: str,
        filename: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        specs, rules = original(payload, source_id, filename)
        if not isinstance(payload, dict) or not specs:
            return specs, rules
        entities = extract_ui_surface_entities(payload, source_id)
        for spec in specs:
            contracts, gaps = _per_spec_contracts(payload, source_id, spec)
            spec["surface_entities"] = copy.deepcopy(entities)
            spec["surface_contracts"] = copy.deepcopy(contracts)
            spec["surface_contract_gaps"] = copy.deepcopy(gaps)
            spec["surface_entity_count"] = len(entities)
            spec["surface_contract_count"] = len(contracts)
        return specs, rules

    _core._uiux_requirements_from_json = with_surface_declarations
    setattr(_core, _INSTALL_MARKER, True)


__all__ = [
    "compile_ui_surface_checks",
    "compile_ui_surface_contracts",
    "extract_ui_surface_entities",
    "install_ui_surface_declaration_parser",
]
