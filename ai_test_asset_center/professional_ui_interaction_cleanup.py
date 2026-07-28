"""Governed interactive UI execution with browser-side cleanup equivalence.

This module extends the existing formal UI authority. It does not create a second
finding path. Interactive browser plans are admitted only when they declare:

* ``approved_sandbox_write`` execution;
* explicit treatment and cleanup phases;
* source-declared state probes captured before treatment and after cleanup;
* one browser compensation strategy and exact equivalence requirement.

The target policy remains the repository-wide write authority. Production,
unknown environments, URL drift, read-only mode and missing target approval are
all fail-closed before a browser is launched.

A treatment assertion may become a formal UI violation only after cleanup has an
ACCEPTED equivalence receipt. Cleanup failure or uncertainty makes the observer
INDETERMINATE, never a customer finding.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from . import browser_execution as _browser
from . import formal_ui_surface as _formal
from . import formal_ui_surface_guard as _guard
from . import professional_ui_readonly as _professional
from . import ui_execution_adapter as _adapter
from .multimodal_locator import MultimodalLocatorError, validate_locator_intent
from .sandbox_write_executor import sandbox_write_allowed
from .sandbox_write_executor_base import target_policy_decision

INSTALL_MARKER = "_qualibug_controlled_ui_interaction_installed"
ORIGINAL_VALIDATOR = "_qualibug_validator_before_controlled_ui_interaction"
ORIGINAL_EXECUTOR = "_qualibug_executor_before_controlled_ui_interaction"
ORIGINAL_COMPILER = "_qualibug_compiler_before_controlled_ui_interaction"
ORIGINAL_ADAPTER = "_qualibug_adapter_before_controlled_ui_interaction"
ORIGINAL_FORMAL_EXECUTION = "_qualibug_formal_execution_before_controlled_ui_interaction"
ORIGINAL_OBSERVER = "_qualibug_observer_before_controlled_ui_interaction"

CLEANUP_RECEIPT_SCHEMA = "qualibug.ui-cleanup-equivalence-receipt.v1"
WRITE_MODE = "approved_sandbox_write"
READ_ONLY_MODE = "safe_read_only"
INTERACTIVE_ACTIONS = frozenset({
    "click",
    "fill",
    "check",
    "uncheck",
    "select_option",
    "press",
})
NAVIGATION_ACTIONS = frozenset({"goto", "wait_for_load", "screenshot"})
PHASES = ("setup", "treatment", "assertion", "cleanup")
_PHASE_RANK = {phase: index for index, phase in enumerate(PHASES)}
PROBE_PROPERTIES = frozenset({
    "text",
    "value",
    "checked",
    "count",
    "attribute",
    "visible",
    "enabled",
    "url",
})
_MAX_PROBES = 32
_MAX_STEPS = 120
_REQUEST_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_controlled_ui_request_context",
    default={},
)
_LAST_CLEANUP_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_controlled_ui_cleanup_context",
    default={},
)


class ControlledUIPlanError(ValueError):
    """A source-declared interactive plan is not safely executable."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_relative_artifact(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return ""


def _interaction_contract(plan: dict[str, Any]) -> dict[str, Any]:
    return _dict(plan.get("interaction_contract"))


def _source_cleanup_contract_error(plan: dict[str, Any]) -> str:
    if _text(plan.get("execution_mode")) != WRITE_MODE:
        return "UI_INTERACTION_WRITE_MODE_REQUIRED"
    if plan.get("write_approved") is not True:
        return "UI_INTERACTION_WRITE_APPROVAL_MISSING"
    contract = _interaction_contract(plan)
    if _text(contract.get("cleanup_strategy")) != "browser_compensation":
        return "UI_INTERACTION_CLEANUP_STRATEGY_INVALID"
    if _text(contract.get("equivalence")) != "source_declared_state_probes":
        return "UI_INTERACTION_EQUIVALENCE_AUTHORITY_INVALID"
    if _text(contract.get("target_scope")) != "approved_nonproduction_target":
        return "UI_INTERACTION_TARGET_SCOPE_INVALID"
    probes = [row for row in _list(plan.get("state_probes")) if isinstance(row, dict)]
    if not probes:
        return "UI_INTERACTION_STATE_PROBES_MISSING"
    if len(probes) > _MAX_PROBES:
        return "UI_INTERACTION_STATE_PROBE_LIMIT_EXCEEDED"
    steps = [row for row in _list(plan.get("steps")) if isinstance(row, dict)]
    if not steps:
        return "UI_INTERACTION_STEPS_MISSING"
    treatment = [
        row for row in steps
        if _text(row.get("phase")).lower() == "treatment"
        and _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
    ]
    cleanup = [
        row for row in steps
        if _text(row.get("phase")).lower() == "cleanup"
        and _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
    ]
    if not treatment:
        return "UI_INTERACTION_TREATMENT_MISSING"
    if not cleanup:
        return "UI_INTERACTION_CLEANUP_STEPS_MISSING"
    return ""


def _validate_locator_fields(row: dict[str, Any], *, allow_url: bool = False) -> None:
    if allow_url and _text(row.get("property")).lower() == "url":
        return
    selector = _text(row.get("selector"))
    intent = row.get("locator_intent")
    if not selector and not isinstance(intent, dict):
        raise _browser.BrowserExecutionError("browser_locator_missing")
    if selector and intent:
        raise _browser.BrowserExecutionError("browser_locator_authority_ambiguous")
    if isinstance(intent, dict):
        try:
            row["locator_intent"] = validate_locator_intent(intent)
        except MultimodalLocatorError as exc:
            raise _browser.BrowserExecutionError(str(exc)) from exc


def _validate_probe(raw: dict[str, Any], seen: set[str]) -> dict[str, Any]:
    probe = copy.deepcopy(raw)
    probe_id = _text(probe.get("probe_id") or probe.get("id"), limit=160)
    if not probe_id:
        raise _browser.BrowserExecutionError("browser_state_probe_id_missing")
    if probe_id in seen:
        raise _browser.BrowserExecutionError("browser_state_probe_id_duplicate")
    seen.add(probe_id)
    prop = _text(probe.get("property")).lower()
    if prop not in PROBE_PROPERTIES:
        raise _browser.BrowserExecutionError(
            f"browser_state_probe_property_unsupported:{prop or 'missing'}"
        )
    probe["probe_id"] = probe_id
    probe["property"] = prop
    if prop == "attribute":
        name = _text(probe.get("name"), limit=200)
        if not name:
            raise _browser.BrowserExecutionError("browser_state_probe_attribute_missing")
        probe["name"] = name
    _validate_locator_fields(probe, allow_url=True)
    return probe


def _sensitive_fill(step: dict[str, Any]) -> bool:
    if step.get("sensitive") is True:
        return True
    corpus = " ".join(
        (
            _text(step.get("selector")).lower(),
            _canonical(step.get("locator_intent")).lower(),
            _text(step.get("field_kind")).lower(),
        )
    )
    return any(token in corpus for token in ("password", "passwd", "secret", "token"))


def _validate_interactive_step(step: dict[str, Any], action: str) -> None:
    _validate_locator_fields(step)
    if action == "fill":
        value_ref = _text(step.get("value_ref"), limit=240)
        has_literal = "value" in step
        if bool(value_ref) == bool(has_literal):
            raise _browser.BrowserExecutionError(
                "browser_fill_requires_exactly_one_value_or_value_ref"
            )
        if _sensitive_fill(step) and not value_ref:
            raise _browser.BrowserExecutionError(
                "browser_sensitive_fill_requires_value_ref"
            )
    elif action == "select_option":
        value_ref = _text(step.get("value_ref"), limit=240)
        has_value = "value" in step
        has_label = "label" in step
        has_index = "index" in step
        if sum((bool(value_ref), has_value, has_label, has_index)) != 1:
            raise _browser.BrowserExecutionError(
                "browser_select_requires_one_source_value"
            )
    elif action == "press":
        key = _text(step.get("key"), limit=80)
        if not key:
            raise _browser.BrowserExecutionError("browser_press_key_missing")


def _validate_write_plan(
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise _browser.BrowserExecutionError("browser_plan_invalid")
    error = _source_cleanup_contract_error(plan)
    if error:
        raise _browser.BrowserExecutionError(error)
    if _text(_dict(runtime_contract).get("status")).lower() != "approved":
        raise _browser.BrowserExecutionError("browser_runtime_contract_not_approved")
    base_url = _text(_dict(runtime_contract).get("approved_base_url"))
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _browser.BrowserExecutionError("browser_approved_base_url_invalid")

    sources = [row for row in _list(plan.get("steps")) if isinstance(row, dict)]
    if len(sources) > _MAX_STEPS:
        raise _browser.BrowserExecutionError("browser_step_limit_exceeded")
    normalized: list[dict[str, Any]] = []
    previous_rank = -1
    treatment_interactions = 0
    cleanup_interactions = 0
    expectation_count = 0

    readonly_actions = set(_professional.READ_ONLY_ACTIONS)
    expectation_actions = set(_professional.PROFESSIONAL_EXPECTATIONS)
    for position, source in enumerate(sources, start=1):
        row = copy.deepcopy(source)
        action = _text(row.get("action")).lower()
        phase = _text(row.get("phase")).lower()
        if phase not in _PHASE_RANK:
            raise _browser.BrowserExecutionError(
                f"browser_interaction_phase_invalid:{phase or position}"
            )
        rank = _PHASE_RANK[phase]
        if rank < previous_rank:
            raise _browser.BrowserExecutionError("browser_interaction_phase_order_invalid")
        previous_rank = rank
        if action not in readonly_actions | set(INTERACTIVE_ACTIONS):
            raise _browser.BrowserExecutionError(
                f"browser_action_unsupported:{action or position}"
            )
        if action == "goto":
            target = _text(row.get("url"))
            if not target:
                raise _browser.BrowserExecutionError("browser_goto_url_missing")
            resolved = urljoin(base_url.rstrip("/") + "/", target)
            if not _browser._same_approved_origin(base_url, resolved):
                raise _browser.BrowserExecutionError(
                    "browser_target_outside_approved_base_url"
                )
            row["url"] = resolved
        elif action in INTERACTIVE_ACTIONS:
            if phase not in {"treatment", "cleanup"}:
                raise _browser.BrowserExecutionError(
                    f"browser_interaction_phase_not_mutating:{phase}"
                )
            _validate_interactive_step(row, action)
            if phase == "treatment":
                treatment_interactions += 1
            else:
                cleanup_interactions += 1
        elif action in expectation_actions:
            if phase != "assertion":
                raise _browser.BrowserExecutionError(
                    f"browser_expectation_phase_invalid:{phase}"
                )
            _professional._validate_professional_step(row, action)
            expectation_count += 1
        elif action not in NAVIGATION_ACTIONS and action not in {
            "set_viewport",
            "set_media",
        }:
            _professional._validate_professional_step(row, action)
        row["action"] = action
        row["phase"] = phase
        row["step_index"] = position
        normalized.append(row)

    if treatment_interactions < 1:
        raise _browser.BrowserExecutionError("browser_treatment_interaction_missing")
    if cleanup_interactions < 1:
        raise _browser.BrowserExecutionError("browser_cleanup_interaction_missing")
    if expectation_count < 1:
        raise _browser.BrowserExecutionError("browser_source_expectation_missing")

    seen: set[str] = set()
    probes = [
        _validate_probe(row, seen)
        for row in _list(plan.get("state_probes"))
        if isinstance(row, dict)
    ]
    if not probes:
        raise _browser.BrowserExecutionError("browser_state_probes_missing")

    return {
        "execution_mode": WRITE_MODE,
        "base_url": base_url.rstrip("/"),
        "write_approved": True,
        "interaction_contract": copy.deepcopy(_interaction_contract(plan)),
        "state_probes": probes,
        "storage_state_ref": _text(plan.get("storage_state_ref"), limit=500),
        "steps": normalized,
    }


def validate_controlled_browser_plan(
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    mode = _text(_dict(plan).get("execution_mode") or READ_ONLY_MODE)
    if mode == READ_ONLY_MODE:
        original = getattr(_browser, ORIGINAL_VALIDATOR)
        return original(plan, runtime_contract)
    if mode != WRITE_MODE:
        raise _browser.BrowserExecutionError("browser_execution_mode_invalid")
    return _validate_write_plan(plan, runtime_contract)


def _candidate(page: Any, row: dict[str, Any]) -> tuple[Any, str]:
    return _professional._candidate(page, row)


def _require_unique(locator: Any, action: str) -> None:
    count = int(locator.count())
    if count == 0:
        raise RuntimeError(f"UI_INTERACTION_TARGET_MISSING:{action}")
    if count != 1:
        raise RuntimeError(f"UI_INTERACTION_TARGET_AMBIGUOUS:{action}:{count}")


def _runtime_value(runtime_contract: dict[str, Any], ref: str) -> str:
    bindings = _dict(runtime_contract.get("ui_input_bindings"))
    if ref not in bindings:
        raise RuntimeError("UI_INPUT_BINDING_MISSING")
    value = bindings.get(ref)
    if isinstance(value, dict):
        value = value.get("value")
    if value is None:
        raise RuntimeError("UI_INPUT_BINDING_EMPTY")
    return str(value)


def _step_value(step: dict[str, Any], runtime_contract: dict[str, Any]) -> str:
    ref = _text(step.get("value_ref"), limit=240)
    if ref:
        return _runtime_value(runtime_contract, ref)
    return str(step.get("value") or "")


def _execute_interaction(
    *,
    page: Any,
    step: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    action = _text(step.get("action")).lower()
    locator, strategy = _candidate(page, step)
    _require_unique(locator, action)
    timeout = int(step.get("timeout_ms") or 10_000)
    receipt: dict[str, Any] = {
        "action": action,
        "phase": _text(step.get("phase")),
        "locator_strategy": strategy,
        "locator_intent_fingerprint": _fingerprint(
            step.get("locator_intent") or step.get("selector")
        ),
        "raw_input_value_included": False,
    }
    if action == "click":
        locator.click(timeout=timeout)
    elif action == "fill":
        value = _step_value(step, runtime_contract)
        locator.fill(value, timeout=timeout)
        receipt["input_binding_fingerprint"] = _fingerprint(
            _text(step.get("value_ref")) or {"literal": value}
        )
        receipt["sensitive_input"] = _sensitive_fill(step)
    elif action == "check":
        locator.check(timeout=timeout)
    elif action == "uncheck":
        locator.uncheck(timeout=timeout)
    elif action == "select_option":
        if _text(step.get("value_ref")):
            locator.select_option(
                _runtime_value(runtime_contract, _text(step.get("value_ref"))),
                timeout=timeout,
            )
            receipt["input_binding_fingerprint"] = _fingerprint(
                _text(step.get("value_ref"))
            )
        elif "value" in step:
            locator.select_option(value=str(step.get("value")), timeout=timeout)
        elif "label" in step:
            locator.select_option(label=str(step.get("label")), timeout=timeout)
        else:
            locator.select_option(index=int(step.get("index")), timeout=timeout)
    elif action == "press":
        locator.press(_text(step.get("key")), timeout=timeout)
    else:
        raise RuntimeError(f"UI_INTERACTION_ACTION_UNSUPPORTED:{action}")
    return receipt


def _execute_readonly_step(
    *,
    page: Any,
    step: dict[str, Any],
    artifact_dir: Path,
    console: list[dict[str, Any]],
    network: list[dict[str, Any]],
) -> dict[str, Any]:
    action = _text(step.get("action")).lower()
    receipt: dict[str, Any] = {
        "action": action,
        "phase": _text(step.get("phase")),
    }
    if action == "goto":
        response = page.goto(
            step["url"],
            wait_until=_text(step.get("wait_until") or "networkidle"),
            timeout=int(step.get("timeout_ms") or 30_000),
        )
        receipt.update({
            "url": _browser._redact_url(step["url"]),
            "status": response.status if response else 0,
        })
        return receipt
    if action == "wait_for_load":
        page.wait_for_load_state(
            _text(step.get("state") or "networkidle"),
            timeout=int(step.get("timeout_ms") or 30_000),
        )
        return receipt
    if action == "screenshot":
        output = artifact_dir / f"step_{int(step.get('step_index') or 0)}.png"
        page.screenshot(path=str(output), full_page=bool(step.get("full_page", True)))
        receipt["screenshot"] = output.name
        return receipt
    receipt.update(
        _professional._execute_expectation(
            page=page,
            step=step,
            console=console,
            network=network,
        )
    )
    return receipt


def _probe_material(page: Any, probe: dict[str, Any]) -> dict[str, Any]:
    prop = _text(probe.get("property")).lower()
    if prop == "url":
        return {
            "property": prop,
            "value_fingerprint": _fingerprint(_browser._redact_url(page.url)),
        }
    locator, strategy = _candidate(page, probe)
    count = int(locator.count())
    base = {
        "property": prop,
        "locator_strategy": strategy,
        "locator_intent_fingerprint": _fingerprint(
            probe.get("locator_intent") or probe.get("selector")
        ),
        "matched_count": count,
    }
    if prop == "count":
        base["value_fingerprint"] = _fingerprint({"count": count})
        return base
    if count != 1:
        raise RuntimeError(f"UI_STATE_PROBE_TARGET_NOT_UNIQUE:{probe['probe_id']}:{count}")
    if prop == "text":
        value: Any = _text(locator.inner_text())
    elif prop == "value":
        value = _text(locator.input_value())
    elif prop == "checked":
        value = bool(locator.is_checked())
    elif prop == "attribute":
        value = _text(locator.get_attribute(_text(probe.get("name"))))
    elif prop == "visible":
        value = bool(locator.is_visible())
    elif prop == "enabled":
        value = bool(locator.is_enabled())
    else:
        raise RuntimeError(f"UI_STATE_PROBE_PROPERTY_UNSUPPORTED:{prop}")
    base["value_fingerprint"] = _fingerprint(value)
    return base


def _capture_probes(page: Any, probes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        _text(probe.get("probe_id")): _probe_material(page, probe)
        for probe in probes
    }


def _cleanup_receipt(
    *,
    run_id: str,
    request_context: dict[str, Any],
    policy: dict[str, Any],
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    cleanup_steps: list[dict[str, Any]],
    cleanup_error: str,
) -> dict[str, Any]:
    comparisons = []
    for probe_id in sorted(set(before) | set(after)):
        before_row = _dict(before.get(probe_id))
        after_row = _dict(after.get(probe_id))
        equivalent = bool(before_row and after_row and before_row == after_row)
        comparisons.append({
            "probe_id": probe_id,
            "property": _text(before_row.get("property") or after_row.get("property")),
            "before_fingerprint": _fingerprint(before_row) if before_row else "",
            "after_fingerprint": _fingerprint(after_row) if after_row else "",
            "equivalent": equivalent,
        })
    accepted = bool(
        comparisons
        and all(row["equivalent"] for row in comparisons)
        and not cleanup_error
        and cleanup_steps
    )
    status = "ACCEPTED" if accepted else "INDETERMINATE"
    reason = ""
    if cleanup_error:
        reason = "UI_CLEANUP_EXECUTION_FAILED"
    elif not comparisons:
        reason = "UI_CLEANUP_STATE_PROBES_MISSING"
    elif not all(row["equivalent"] for row in comparisons):
        reason = "UI_CLEANUP_EQUIVALENCE_MISMATCH"
    elif not cleanup_steps:
        reason = "UI_CLEANUP_STEPS_NOT_EXECUTED"
    canonical = {
        "schema_version": CLEANUP_RECEIPT_SCHEMA,
        "run_id": _text(run_id),
        "request_id": _text(request_context.get("request_id")),
        "actor_ref": _text(request_context.get("actor_ref")),
        "target_policy_decision_id": _text(policy.get("decision_id")),
        "status": status,
        "reason_code": reason,
        "cleanup_step_count": len(cleanup_steps),
        "probe_comparisons": comparisons,
        "raw_state_included": False,
        "provider_findings_consumed": False,
    }
    return {
        **canonical,
        "receipt_id": "uic_" + _fingerprint(canonical)[:20],
    }


def _resolve_storage_state(
    *,
    root: Path,
    project: str,
    ref: str,
) -> str:
    if not ref:
        return ""
    candidate = (Path(root) / ref).resolve()
    allowed_roots = [
        (Path(root) / "platform_inputs" / project).resolve(),
        (Path(root) / "platform_workspace" / project).resolve(),
    ]
    if not any(
        candidate == allowed or allowed in candidate.parents for allowed in allowed_roots
    ):
        raise RuntimeError("UI_STORAGE_STATE_OUTSIDE_PROJECT_SCOPE")
    if candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise RuntimeError("UI_STORAGE_STATE_NOT_FOUND")
    if candidate.stat().st_size > 2_000_000:
        raise RuntimeError("UI_STORAGE_STATE_TOO_LARGE")
    return str(candidate)


def _launch_browser() -> tuple[Any, Any, str]:
    runtime = None
    browser = None
    error = ""
    try:
        from .auto_browser_setup import ensure_browser
    except Exception:
        ensure_browser = None
    if ensure_browser is not None:
        try:
            runtime, browser_or_error = ensure_browser(headless=True, timeout=30_000)
        except Exception as exc:
            runtime = None
            browser_or_error = f"browser_runtime_bootstrap_failed:{type(exc).__name__}"
        if runtime is not None:
            browser = browser_or_error
        else:
            error = _text(browser_or_error)
    if browser is None and not error:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            error = "playwright_import_missing"
        else:
            try:
                runtime = sync_playwright().start()
                browser = runtime.chromium.launch(headless=True)
            except Exception as exc:
                error = f"{type(exc).__name__}:{str(exc)[:300]}"
    return runtime, browser, error


def execute_controlled_browser_plan(
    project_id: str,
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str = "",
) -> dict[str, Any]:
    mode = _text(_dict(plan).get("execution_mode") or READ_ONLY_MODE)
    if mode == READ_ONLY_MODE:
        original = getattr(_browser, ORIGINAL_EXECUTOR)
        return original(project_id, plan, runtime_contract, root=root, run_id=run_id)

    validated = _validate_write_plan(plan, runtime_contract)
    project = _browser._safe_project(project_id)
    request_context = _dict(_REQUEST_CONTEXT.get())
    actor_ref = _text(request_context.get("actor_ref") or plan.get("actor_ref"))
    allowed, policy_reason = sandbox_write_allowed(
        root=Path(root),
        project=project,
        runtime_contract=runtime_contract,
        actor_token="",
        actor_identity=actor_ref,
    )
    policy = target_policy_decision(
        root=Path(root),
        project=project,
        runtime_contract=runtime_contract,
    )
    artifact_dir = (
        Path(root)
        / "platform_workspace"
        / project
        / "browser_runs"
        / _browser._safe_run_id(run_id)
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if not allowed:
        return {
            "status": "blocked",
            "reason": f"UI_WRITE_POLICY_BLOCKED:{policy_reason}",
            "execution_status": "not_executed",
            "confirmation_status": "blocked",
            "execution_mode": WRITE_MODE,
            "artifact_dir": _safe_relative_artifact(Path(root), artifact_dir),
            "steps": [],
            "cleanup_steps": [],
            "cleanup_receipt": {
                "schema_version": CLEANUP_RECEIPT_SCHEMA,
                "status": "INDETERMINATE",
                "reason_code": "UI_WRITE_POLICY_BLOCKED",
                "target_policy_decision_id": _text(policy.get("decision_id")),
                "raw_state_included": False,
            },
        }

    playwright_runtime, browser, browser_error = _launch_browser()
    if browser is None:
        return {
            "status": "blocked",
            "reason": f"BROWSER_RUNTIME_UNAVAILABLE:{browser_error or 'unknown'}",
            "execution_status": "not_executed",
            "confirmation_status": "blocked",
            "execution_mode": WRITE_MODE,
            "artifact_dir": _safe_relative_artifact(Path(root), artifact_dir),
            "steps": [],
            "cleanup_steps": [],
        }

    started = time.time()
    context = None
    primary_receipts: list[dict[str, Any]] = []
    cleanup_receipts: list[dict[str, Any]] = []
    console: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    before: dict[str, dict[str, Any]] = {}
    after: dict[str, dict[str, Any]] = {}
    primary_error = ""
    cleanup_error = ""
    mutation_started = False
    trace_path = artifact_dir / "trace.zip"
    har_path = artifact_dir / "network.har"
    failure_path = artifact_dir / "failure_before_cleanup.png"
    final_path = artifact_dir / "final_after_cleanup.png"

    try:
        context_kwargs: dict[str, Any] = {
            "record_har_path": str(har_path),
            "record_har_content": "embed",
        }
        storage_ref = _resolve_storage_state(
            root=Path(root),
            project=project,
            ref=_text(validated.get("storage_state_ref")),
        )
        if storage_ref:
            context_kwargs["storage_state"] = storage_ref
        context = browser.new_context(**context_kwargs)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.on(
            "console",
            lambda message: console.append({
                "type": message.type,
                "text": message.text[:4000],
            }),
        )
        page.on(
            "response",
            lambda response: network.append({
                "url": _browser._redact_url(response.url),
                "status": response.status,
                "method": response.request.method,
            }),
        )

        setup_steps = [row for row in validated["steps"] if row["phase"] == "setup"]
        treatment_steps = [
            row for row in validated["steps"] if row["phase"] == "treatment"
        ]
        assertion_steps = [
            row for row in validated["steps"] if row["phase"] == "assertion"
        ]
        cleanup_steps = [row for row in validated["steps"] if row["phase"] == "cleanup"]

        for step in setup_steps:
            receipt = {
                "step_index": int(step["step_index"]),
                "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            receipt.update(
                _execute_readonly_step(
                    page=page,
                    step=step,
                    artifact_dir=artifact_dir,
                    console=console,
                    network=network,
                )
            )
            primary_receipts.append(receipt)

        before = _capture_probes(page, validated["state_probes"])

        try:
            for step in treatment_steps:
                if step["action"] in INTERACTIVE_ACTIONS:
                    mutation_started = True
                receipt = {
                    "step_index": int(step["step_index"]),
                    "started_at_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                }
                if step["action"] in INTERACTIVE_ACTIONS:
                    receipt.update(
                        _execute_interaction(
                            page=page,
                            step=step,
                            runtime_contract=runtime_contract,
                        )
                    )
                else:
                    receipt.update(
                        _execute_readonly_step(
                            page=page,
                            step=step,
                            artifact_dir=artifact_dir,
                            console=console,
                            network=network,
                        )
                    )
                primary_receipts.append(receipt)

            for step in assertion_steps:
                receipt = {
                    "step_index": int(step["step_index"]),
                    "started_at_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                }
                receipt.update(
                    _execute_readonly_step(
                        page=page,
                        step=step,
                        artifact_dir=artifact_dir,
                        console=console,
                        network=network,
                    )
                )
                primary_receipts.append(receipt)
        except Exception as exc:
            primary_error = (
                str(exc)
                if isinstance(exc, _professional.ProfessionalUIExpectationError)
                else f"{type(exc).__name__}:{str(exc)[:300]}"
            )
            try:
                page.screenshot(path=str(failure_path), full_page=True)
            except Exception:
                pass

        if mutation_started:
            for step in cleanup_steps:
                receipt = {
                    "step_index": int(step["step_index"]),
                    "started_at_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                }
                try:
                    if step["action"] in INTERACTIVE_ACTIONS:
                        receipt.update(
                            _execute_interaction(
                                page=page,
                                step=step,
                                runtime_contract=runtime_contract,
                            )
                        )
                    else:
                        receipt.update(
                            _execute_readonly_step(
                                page=page,
                                step=step,
                                artifact_dir=artifact_dir,
                                console=console,
                                network=network,
                            )
                        )
                    cleanup_receipts.append(receipt)
                except Exception as exc:
                    cleanup_error = f"{type(exc).__name__}:{str(exc)[:300]}"
                    break
            if not cleanup_error:
                try:
                    after = _capture_probes(page, validated["state_probes"])
                except Exception as exc:
                    cleanup_error = f"{type(exc).__name__}:{str(exc)[:300]}"
        try:
            page.screenshot(path=str(final_path), full_page=True)
        except Exception:
            pass
        context.tracing.stop(path=str(trace_path))
    except Exception as exc:
        primary_error = primary_error or f"{type(exc).__name__}:{str(exc)[:300]}"
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        try:
            if playwright_runtime is not None:
                playwright_runtime.stop()
        except Exception:
            pass

    cleanup = _cleanup_receipt(
        run_id=run_id,
        request_context=request_context,
        policy=policy,
        before=before,
        after=after,
        cleanup_steps=cleanup_receipts,
        cleanup_error=cleanup_error,
    )
    cleanup_accepted = _text(cleanup.get("status")).upper() == "ACCEPTED"
    if not cleanup_accepted:
        status = "blocked"
        reason = (
            "UI_CLEANUP_EQUIVALENCE_UNPROVEN:"
            + _text(cleanup.get("reason_code") or "UNKNOWN")
        )
    elif primary_error:
        status = "failed"
        reason = primary_error
    else:
        status = "executed"
        reason = ""

    result = {
        "status": status,
        "reason": reason,
        "execution_status": (
            "executed"
            if status == "executed"
            else "failed"
            if status == "failed"
            else "not_confirmed"
        ),
        "confirmation_status": "candidate" if cleanup_accepted else "blocked",
        "execution_mode": WRITE_MODE,
        "artifact_dir": _safe_relative_artifact(Path(root), artifact_dir),
        "trace_ref": (
            _safe_relative_artifact(Path(root), trace_path)
            if trace_path.exists()
            else ""
        ),
        "har_ref": (
            _safe_relative_artifact(Path(root), har_path)
            if har_path.exists()
            else ""
        ),
        "screenshot_ref": (
            _safe_relative_artifact(Path(root), final_path)
            if final_path.exists()
            else ""
        ),
        "failure_screenshot_ref": (
            _safe_relative_artifact(Path(root), failure_path)
            if failure_path.exists()
            else ""
        ),
        "steps": primary_receipts,
        "cleanup_steps": cleanup_receipts,
        "cleanup_receipt": cleanup,
        "interaction_count": sum(
            1
            for row in validated["steps"]
            if row["action"] in INTERACTIVE_ACTIONS
            and row["phase"] == "treatment"
        ),
        "cleanup_interaction_count": sum(
            1
            for row in validated["steps"]
            if row["action"] in INTERACTIVE_ACTIONS
            and row["phase"] == "cleanup"
        ),
        "professional_ui_expectation_count": sum(
            1
            for row in validated["steps"]
            if row["action"] in _professional.PROFESSIONAL_EXPECTATIONS
        ),
        "console": console,
        "network": network,
        "provider_findings_consumed": False,
        "raw_state_included": False,
        "duration_ms": int((time.time() - started) * 1000),
    }
    (artifact_dir / "browser_execution.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return result


def _compile_controlled_ui_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    original = getattr(_formal, ORIGINAL_COMPILER)
    property_spec = _dict(_dict(envelope).get("property_spec"))
    request = _formal._declared_ui_request(property_spec)
    plan = _dict(request.get("browser_plan"))
    mode = _text(
        request.get("execution_mode")
        or plan.get("execution_mode")
        or READ_ONLY_MODE
    )
    has_interaction = any(
        _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
        for row in _list(plan.get("steps"))
        if isinstance(row, dict)
    )
    if has_interaction and mode != WRITE_MODE:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_TARGET_POLICY",
            "detail": "ui_interaction_requires_approved_sandbox_write",
        }
    if mode != WRITE_MODE:
        return original(envelope)
    error = _source_cleanup_contract_error(plan)
    if error:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_NON_REVERSIBLE_WRITE",
            "detail": error,
        }
    result = original(envelope)
    if _text(_dict(result).get("status")).upper() == "COMPILED":
        result = copy.deepcopy(result)
        assertion = _dict(result.get("assertion"))
        assertion["ui_interaction_cleanup_required"] = True
        assertion["ui_cleanup_receipt_schema"] = CLEANUP_RECEIPT_SCHEMA
        result["assertion"] = assertion
    return result


def _adapter_with_request_context(
    project_id: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    original = getattr(_adapter, ORIGINAL_ADAPTER)
    token = _REQUEST_CONTEXT.set({
        "request_id": _text(request.get("request_id")),
        "actor_ref": _text(request.get("actor_ref")),
        "source_ref_fingerprints": [
            _fingerprint(row)
            for row in _list(request.get("source_refs"))
            if isinstance(row, dict)
        ],
    })
    try:
        return original(
            project_id,
            request,
            runtime_contract,
            root=root,
            run_id=run_id,
        )
    finally:
        _REQUEST_CONTEXT.reset(token)


def _formal_execution_with_cleanup_gate(
    project: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    original = getattr(_formal, ORIGINAL_FORMAL_EXECUTION)
    execution = original(
        project,
        request,
        runtime_contract,
        root=root,
        run_id=run_id,
    )
    mode = _text(
        request.get("execution_mode")
        or _dict(request.get("browser_plan")).get("execution_mode")
    )
    cleanup_context: dict[str, Any] = {}
    if mode == WRITE_MODE:
        rows = [
            copy.deepcopy(row)
            for row in _list(_dict(execution).get("results"))
            if isinstance(row, dict)
        ]
        for row in rows:
            cleanup = copy.deepcopy(_dict(row.get("cleanup_receipt")))
            cleanup_context = {
                "cleanup_receipt": cleanup,
                "interaction_count": int(row.get("interaction_count") or 0),
                "cleanup_interaction_count": int(
                    row.get("cleanup_interaction_count") or 0
                ),
            }
            if _text(cleanup.get("status")).upper() != "ACCEPTED":
                row["status"] = "blocked"
                row["reason"] = (
                    "UI_CLEANUP_EQUIVALENCE_UNPROVEN:"
                    + _text(cleanup.get("reason_code") or "MISSING")
                )
        execution = copy.deepcopy(execution)
        execution["results"] = rows
    _LAST_CLEANUP_CONTEXT.set(cleanup_context)
    return execution


def _observer_with_cleanup_evidence(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    original = getattr(_formal, ORIGINAL_OBSERVER)
    token = _LAST_CLEANUP_CONTEXT.set({})
    try:
        receipt = original(envelope)
        cleanup_context = _dict(_LAST_CLEANUP_CONTEXT.get())
    finally:
        _LAST_CLEANUP_CONTEXT.reset(token)
    if not cleanup_context:
        return receipt
    evidence = copy.deepcopy(_dict(receipt.get("evidence")))
    ui_evidence = copy.deepcopy(_dict(evidence.get(_formal.EVIDENCE_KEY)))
    ui_evidence.update(cleanup_context)
    ui_evidence["cleanup_equivalence_required"] = True
    ui_evidence["cleanup_equivalence_accepted"] = (
        _text(_dict(cleanup_context.get("cleanup_receipt")).get("status")).upper()
        == "ACCEPTED"
    )
    evidence[_formal.EVIDENCE_KEY] = ui_evidence
    return _receipt(
        observer_id=_text(receipt.get("observer_id")) or _formal.OBSERVER_ID,
        status=_text(receipt.get("status")) or "INDETERMINATE",
        reason_code=_text(receipt.get("reason_code")),
        evidence=evidence,
        campaign_id=_text(receipt.get("campaign_id")),
        execution_id=_text(receipt.get("execution_id")),
    )


def install_controlled_ui_interaction() -> None:
    """Install governed interaction on the one formal UI authority."""
    if getattr(_formal, INSTALL_MARKER, False):
        return

    setattr(_browser, ORIGINAL_VALIDATOR, _browser.validate_browser_plan)
    setattr(_browser, ORIGINAL_EXECUTOR, _browser.execute_browser_plan)
    setattr(_formal, ORIGINAL_COMPILER, _formal._compile_ui_protocol)
    setattr(_adapter, ORIGINAL_ADAPTER, _adapter._playwright_request_result)
    setattr(_formal, ORIGINAL_FORMAL_EXECUTION, _formal._execute_ui_requests)

    from . import observer_contracts_base as _observers

    original_observer = _observers._REGISTERED_OBSERVER_HANDLERS.get(
        _formal.OBSERVER_ID
    )
    if not callable(original_observer):
        raise RuntimeError("formal_ui_observer_handler_missing")
    setattr(_formal, ORIGINAL_OBSERVER, original_observer)

    _browser.validate_browser_plan = validate_controlled_browser_plan
    _browser.execute_browser_plan = execute_controlled_browser_plan
    _adapter._playwright_request_result = _adapter_with_request_context
    _formal._execute_ui_requests = _formal_execution_with_cleanup_gate

    _guard._READ_ONLY_ACTIONS = frozenset({
        *_guard._READ_ONLY_ACTIONS,
        *INTERACTIVE_ACTIONS,
    })
    _formal._compile_ui_protocol = _compile_controlled_ui_protocol
    _observers._REGISTERED_OBSERVER_HANDLERS[_formal.OBSERVER_ID] = (
        _observer_with_cleanup_evidence
    )

    from .experiment_protocol_registry import register_family_protocol

    for family in (_formal.RISK_FAMILY, "visibility", "state"):
        register_family_protocol(
            family,
            _formal.PROTOCOL_TEMPLATE,
            compiler=_compile_controlled_ui_protocol,
            observers=(_formal.OBSERVER_ID,),
            assertion_kind=_formal.ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=False,
        )
    setattr(_formal, INSTALL_MARKER, True)


__all__ = [
    "CLEANUP_RECEIPT_SCHEMA",
    "INTERACTIVE_ACTIONS",
    "PROBE_PROPERTIES",
    "execute_controlled_browser_plan",
    "install_controlled_ui_interaction",
    "validate_controlled_browser_plan",
]
