"""Explicit formal-UI contract extraction for UI/UX enterprise sources.

Ordinary prototypes declare labels, components and visual states. They do not declare an
executable browser expectation. This extension recognizes only source-authored contracts in
one of two shapes:

* JSON/YAML-style objects under ``ui_formal_contracts`` / ``ui_contracts``;
* Markdown/Excel rows that explicitly name the API operation, actor, page path and one
  simple ``expect_text`` or ``expect_url`` expectation.

JSON contracts may use the complete professional read-only assertion vocabulary and governed
interactive plans. No selector, text, page route, actor, cleanup path or API operation is
inferred. Incomplete rows are retained as visible gaps rather than silently upgraded into test
obligations.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from ..professional_ui_interaction_cleanup import INTERACTIVE_ACTIONS
from ..professional_ui_interaction_privacy_guard import EVIDENCE_POLICY
from ..professional_ui_readonly import PROFESSIONAL_EXPECTATIONS
from ..professional_ui_responsive_accessibility import (
    ACCESSIBILITY_ACTION,
    CONFIG_ACTIONS,
)
from . import _parsing
from . import _parsing_mechanics as _core

_INSTALL_MARKER = "_qualibug_formal_ui_contract_parser_installed"
_ORIGINAL_MARKER = "_qualibug_original_uiux_specs_from_text"
_NAVIGATION_ACTIONS = frozenset({"goto", "wait_for_load", "screenshot"})
_EXPECTATION_ACTIONS = frozenset({
    *PROFESSIONAL_EXPECTATIONS,
    ACCESSIBILITY_ACTION,
})
_ALLOWED_ACTIONS = frozenset({
    *_NAVIGATION_ACTIONS,
    *_EXPECTATION_ACTIONS,
    *CONFIG_ACTIONS,
    *INTERACTIVE_ACTIONS,
})
_TABLE_EXPECTATION_ACTIONS = frozenset({"expect_text", "expect_url"})
_WRITE_MODE = "approved_sandbox_write"
_READ_ONLY_MODE = "safe_read_only"
_PHASE_RANK = {phase: index for index, phase in enumerate(
    ("setup", "treatment", "assertion", "cleanup")
)}
_LOCATOR_EXPECTATIONS = frozenset({
    "expect_text",
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
})


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
    return "ui_contract_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _pick(row: dict[str, Any], *names: str) -> Any:
    by_key = {_norm_key(key): value for key, value in row.items()}
    for name in names:
        key = _norm_key(name)
        if key in by_key:
            return by_key[key]
    return None


def _source_ref(source_id: str, locator: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "version": "",
        "locator": locator,
        "kind": "formal_ui_contract",
        "quote_hash": "",
    }


def _json_roots(text: str) -> list[tuple[str, Any]]:
    roots: list[tuple[str, Any]] = []
    stripped = _text(text)
    if stripped.startswith(("{", "[")):
        try:
            roots.append(("document", json.loads(stripped)))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    for index, block in enumerate(_parsing._json_blocks(text), start=1):
        roots.append((f"json_block:{index}", block))
    return roots


def _contract_rows_from_json(text: str) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for root_locator, root in _json_roots(text):
        candidates: list[Any] = []
        if isinstance(root, dict):
            for key in ("ui_formal_contracts", "ui_contracts"):
                candidates.extend(_list(root.get(key)))
            if _text(root.get("schema_version")).startswith(
                "qualibug.ui-formal-contract"
            ):
                candidates.append(root)
        elif isinstance(root, list):
            candidates.extend(root)
        for index, candidate in enumerate(candidates, start=1):
            if isinstance(candidate, dict):
                rows.append((
                    f"{root_locator}:ui_formal_contracts[{index}]",
                    dict(candidate),
                ))
    return rows


def _table_contract_rows(text: str) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for index, raw in enumerate(_parsing._markdown_table_rows(text), start=1):
        row = dict(raw)
        action = _text(
            _pick(row, "action", "expectation", "assertion", "动作", "断言")
        ).lower()
        if action not in _TABLE_EXPECTATION_ACTIONS:
            continue
        start_url = _text(
            _pick(row, "start_url", "ui_path", "page_path", "url", "页面路径")
        )
        operation_ref = _text(
            _pick(row, "operation_ref", "operation_id", "api_operation", "接口标识")
        )
        method = _text(_pick(row, "method", "http_method", "请求方法")).upper()
        operation_path = _text(
            _pick(row, "operation_path", "api_path", "endpoint", "接口路径")
        )
        actor_ref = _text(_pick(row, "actor_ref", "actor_id", "角色标识"))
        actor_role = _text(_pick(row, "actor_role", "role", "actor", "角色"))
        provider = _text(_pick(row, "provider", "browser_provider", "执行器"))
        step: dict[str, Any] = {"action": action}
        if action == "expect_text":
            step["selector"] = _text(
                _pick(row, "selector", "locator", "元素选择器")
            )
            step["text"] = _text(
                _pick(row, "expected_text", "text", "期望文本")
            )
        else:
            step["pattern"] = _text(
                _pick(row, "expected_url", "url_pattern", "pattern", "期望地址")
            )
        timeout = _pick(row, "timeout_ms", "timeout", "超时毫秒")
        if _text(timeout):
            try:
                step["timeout_ms"] = int(timeout)
            except (TypeError, ValueError):
                step["timeout_ms"] = timeout
        rows.append((f"table_row:{index}", {
            "contract_id": _text(
                _pick(row, "contract_id", "request_id", "id", "合同标识")
            ),
            "title": _text(_pick(row, "title", "name", "场景名称")),
            "operation_ref": operation_ref,
            "method": method,
            "operation_path": operation_path,
            "actor_ref": actor_ref,
            "actor_role": actor_role,
            "ui_request": {
                "request_id": _text(
                    _pick(row, "request_id", "contract_id", "id", "请求标识")
                ),
                "title": _text(_pick(row, "title", "name", "场景名称")),
                "provider": provider,
                "start_url": start_url,
                "execution_mode": _text(
                    _pick(row, "execution_mode", "执行模式")
                ) or _READ_ONLY_MODE,
                "browser_plan": {
                    "steps": [
                        {"action": "goto", "url": start_url},
                        step,
                    ],
                },
                "success_criteria": {"action": action},
            },
        }))
    return rows


def _normalize_request(row: dict[str, Any]) -> dict[str, Any]:
    request = copy.deepcopy(_dict(row.get("ui_request")))
    if not request:
        request = {
            "request_id": _text(row.get("request_id") or row.get("contract_id")),
            "title": _text(row.get("title") or row.get("name")),
            "provider": _text(row.get("provider")),
            "start_url": _text(row.get("start_url") or row.get("url")),
            "execution_mode": _text(row.get("execution_mode")) or _READ_ONLY_MODE,
            "browser_plan": copy.deepcopy(_dict(row.get("browser_plan"))),
            "success_criteria": copy.deepcopy(_dict(row.get("success_criteria"))),
        }
    return request


def _has_locator(step: dict[str, Any]) -> bool:
    return bool(_text(step.get("selector")) or _dict(step.get("locator_intent")))


def _expectation_structure_gaps(
    expectations: list[dict[str, Any]],
) -> list[str]:
    missing: list[str] = []
    for index, step in enumerate(expectations, start=1):
        action = _text(step.get("action")).lower()
        if action in _LOCATOR_EXPECTATIONS and not _has_locator(step):
            missing.append(f"{action}[{index}].selector_or_locator_intent")
        if action == "expect_text" and not _text(step.get("text")):
            missing.append(f"expect_text[{index}].text")
        elif action == "expect_url" and not _text(
            step.get("pattern") or step.get("url")
        ):
            missing.append(f"expect_url[{index}].pattern")
        elif action in {"expect_value", "expect_role", "expect_accessible_name"}:
            if not _text(step.get("expected")):
                missing.append(f"{action}[{index}].expected")
        elif action == "expect_attribute":
            if not (_text(step.get("name")) and _text(step.get("expected"))):
                missing.append(f"expect_attribute[{index}].name_and_expected")
        elif action == "expect_css":
            if not (_text(step.get("property")) and _text(step.get("expected"))):
                missing.append(f"expect_css[{index}].property_and_expected")
        elif action == "expect_count" and not any(
            key in step for key in ("count", "min_count", "max_count")
        ):
            missing.append(f"expect_count[{index}].count_or_range")
        elif action == "expect_dimensions" and not any(
            key in step
            for key in ("min_width", "max_width", "min_height", "max_height")
        ):
            missing.append(f"expect_dimensions[{index}].dimension_range")
        elif action == ACCESSIBILITY_ACTION and not _list(step.get("rules")):
            missing.append(f"{ACCESSIBILITY_ACTION}[{index}].rules")
    return missing


def _interactive_structure_gaps(
    *,
    request: dict[str, Any],
    browser_plan: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[str]:
    missing: list[str] = []
    mode = _text(
        request.get("execution_mode") or browser_plan.get("execution_mode")
    )
    if mode != _WRITE_MODE:
        missing.append("execution_mode=approved_sandbox_write")
    if browser_plan.get("write_approved") is not True:
        missing.append("browser_plan.write_approved=true")
    contract = _dict(browser_plan.get("interaction_contract"))
    required_contract = {
        "cleanup_strategy": "browser_compensation",
        "equivalence": "source_declared_state_probes",
        "target_scope": "approved_nonproduction_target",
        "evidence_policy": EVIDENCE_POLICY,
    }
    for key, expected in required_contract.items():
        if _text(contract.get(key)) != expected:
            missing.append(f"interaction_contract.{key}={expected}")
    if not [row for row in _list(browser_plan.get("state_probes")) if isinstance(row, dict)]:
        missing.append("browser_plan.state_probes")

    previous_rank = -1
    treatment_count = 0
    cleanup_count = 0
    assertion_count = 0
    for index, step in enumerate(steps, start=1):
        action = _text(step.get("action")).lower()
        phase = _text(step.get("phase")).lower()
        if phase not in _PHASE_RANK:
            missing.append(f"steps[{index}].phase")
            continue
        rank = _PHASE_RANK[phase]
        if rank < previous_rank:
            missing.append("browser_plan.phase_order")
        previous_rank = max(previous_rank, rank)
        if action in INTERACTIVE_ACTIONS:
            if phase == "treatment":
                treatment_count += 1
            elif phase == "cleanup":
                cleanup_count += 1
            else:
                missing.append(f"steps[{index}].interactive_phase")
        elif action in _EXPECTATION_ACTIONS:
            if phase != "assertion":
                missing.append(f"steps[{index}].expectation_phase=assertion")
            assertion_count += 1
        elif action in CONFIG_ACTIONS and phase != "setup":
            missing.append(f"steps[{index}].configuration_phase=setup")
    if treatment_count == 0:
        missing.append("treatment_interaction")
    if cleanup_count == 0:
        missing.append("cleanup_interaction")
    if assertion_count == 0:
        missing.append("professional_ui_expectation")
    return missing


def _validate_contract(
    raw: dict[str, Any],
    *,
    source_id: str,
    locator: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    request = _normalize_request(raw)
    provider = _text(request.get("provider")).lower()
    start_url = _text(request.get("start_url"))
    browser_plan = _dict(request.get("browser_plan"))
    steps = [
        dict(step)
        for step in _list(browser_plan.get("steps"))
        if isinstance(step, dict)
    ]
    operation_ref = _text(raw.get("operation_ref") or raw.get("operation_id"))
    method = _text(raw.get("method") or raw.get("http_method")).upper()
    operation_path = _text(
        raw.get("operation_path") or raw.get("api_path") or raw.get("endpoint")
    )
    actor_ref = _text(raw.get("actor_ref") or raw.get("actor_id"))
    actor_role = _text(raw.get("actor_role") or raw.get("role"))

    missing: list[str] = []
    if provider != "playwright_browser_plan":
        missing.append("provider=playwright_browser_plan")
    if not start_url:
        missing.append("start_url")
    if not browser_plan or not steps:
        missing.append("browser_plan.steps")
    if not operation_ref and not (method and operation_path):
        missing.append("operation_ref_or_method_path")
    if not actor_ref and not actor_role:
        missing.append("actor_ref_or_actor_role")

    actions = [_text(step.get("action")).lower() for step in steps]
    unsupported = sorted({action for action in actions if action not in _ALLOWED_ACTIONS})
    if unsupported:
        missing.append("supported_ui_actions:" + ",".join(unsupported))
    expectations = [
        step
        for step in steps
        if _text(step.get("action")).lower() in _EXPECTATION_ACTIONS
    ]
    if not expectations:
        # Legacy requirement label retained for existing reports/tests; it now means
        # any source-declared professional UI expectation, not only these two actions.
        missing.append("expect_text_or_expect_url")
    missing.extend(_expectation_structure_gaps(expectations))

    interactive = any(action in INTERACTIVE_ACTIONS for action in actions)
    mode = _text(
        request.get("execution_mode") or browser_plan.get("execution_mode")
    ) or _READ_ONLY_MODE
    if interactive:
        missing.extend(_interactive_structure_gaps(
            request=request,
            browser_plan=browser_plan,
            steps=steps,
        ))
    elif mode != _READ_ONLY_MODE:
        missing.append("execution_mode=safe_read_only")

    contract_id = _text(raw.get("contract_id") or request.get("request_id")) or _stable_id(
        source_id,
        locator,
        operation_ref or f"{method}:{operation_path}",
        actor_ref or actor_role,
        start_url,
    )
    missing = list(dict.fromkeys(missing))
    if missing:
        return None, {
            "gap_type": "formal_ui_contract_incomplete",
            "reason_code": "FORMAL_UI_CONTRACT_INCOMPLETE",
            "contract_id": contract_id,
            "source_id": source_id,
            "source_locator": locator,
            "missing_requirements": missing,
            "status": "unsupported",
        }

    request["request_id"] = _text(request.get("request_id")) or contract_id
    request["provider"] = provider
    request["start_url"] = start_url
    request["execution_mode"] = mode
    request["browser_plan"] = {
        **browser_plan,
        "execution_mode": mode,
        "steps": steps,
    }
    request["metadata"] = {
        **_dict(request.get("metadata")),
        "source_declared": True,
        "source_id": source_id,
        "source_locator": locator,
    }
    return {
        "schema_version": "qualibug.ui-formal-contract.v2",
        "contract_id": contract_id,
        "title": _text(raw.get("title") or request.get("title")) or contract_id,
        "operation_ref": operation_ref,
        "method": method,
        "operation_path": operation_path,
        "actor_ref": actor_ref,
        "actor_role": actor_role,
        "ui_request": request,
        "source_refs": [_source_ref(source_id, locator)],
        "source_id": source_id,
        "source_locator": locator,
        "status": "accepted",
        "derivation": "explicit",
        "confidence": 1.0,
    }, None


def extract_formal_ui_contracts(
    text: str,
    *,
    source_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = [*_contract_rows_from_json(text), *_table_contract_rows(text)]
    for locator, raw in candidates:
        contract, gap = _validate_contract(raw, source_id=source_id, locator=locator)
        if gap:
            gaps.append(gap)
            continue
        if not contract:
            continue
        contract_id = _text(contract.get("contract_id"))
        if contract_id in seen:
            continue
        seen.add(contract_id)
        contracts.append(contract)
    return contracts, gaps


def install_formal_ui_contract_parser() -> None:
    """Patch the UI-spec extractor additively; package import performs no target I/O.

    ``_parse_source`` lives in ``_parsing_mechanics`` and resolves
    ``_uiux_specs_from_text`` from that module's globals, so the add-on must
    patch the mechanics module (``_core``) rather than the ``_parsing`` facade —
    a facade-only patch is a silent no-op after the mechanics split.
    """
    if getattr(_core, _INSTALL_MARKER, False):
        return
    original = getattr(
        _core,
        _ORIGINAL_MARKER,
        _core._uiux_specs_from_text,
    )
    setattr(_core, _ORIGINAL_MARKER, original)

    def uiux_specs_with_formal_contracts(
        text: str,
        source_id: str,
        source_type: str,
        filename: str,
    ) -> list[dict[str, Any]]:
        specs = [
            dict(row)
            for row in original(text, source_id, source_type, filename)
        ]
        if source_type not in {"uiux_spec", "uiux_svg"}:
            return specs
        contracts, gaps = extract_formal_ui_contracts(text, source_id=source_id)
        if not specs:
            return specs
        specs[0]["formal_ui_contracts"] = contracts
        specs[0]["formal_ui_contract_gaps"] = gaps
        specs[0]["formal_ui_contract_count"] = len(contracts)
        return specs

    _core._uiux_specs_from_text = uiux_specs_with_formal_contracts
    setattr(_core, _INSTALL_MARKER, True)


__all__ = [
    "extract_formal_ui_contracts",
    "install_formal_ui_contract_parser",
]
