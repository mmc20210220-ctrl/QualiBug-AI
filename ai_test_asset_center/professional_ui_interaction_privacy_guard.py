"""Evidence-minimization guard for governed UI interaction.

Interactive browser traces and HAR files can persist request bodies, cookies,
DOM snapshots and typed values. Formal interaction therefore uses a stricter
evidence policy than read-only UI checks:

* no HAR persistence;
* no Playwright trace persistence;
* screenshots mask password/secret fields and every source-declared sensitive
  interaction locator;
* persisted console messages and network URLs are fingerprinted after assertions
  have consumed their in-memory values.

The formal cleanup and expectation receipts remain authoritative. This guard only
reduces artifact exposure; it does not create or judge findings.
"""
from __future__ import annotations

import contextvars
import copy
import json
from pathlib import Path
from typing import Any

from . import professional_ui_interaction_cleanup as _interaction

EVIDENCE_POLICY = "masked_screenshots_no_trace_no_har"
_INSTALL_MARKER = "_qualibug_controlled_ui_privacy_guard_installed"
_ORIGINAL_LAUNCH = "_qualibug_original_interaction_browser_launch_before_privacy"
ORIGINAL_EXECUTOR = "_qualibug_original_interaction_executor_before_privacy"
ORIGINAL_CONTRACT_CHECK = "_qualibug_original_interaction_contract_check_before_privacy"
_SENSITIVE_STEPS: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "qualibug_interaction_sensitive_steps",
    default=[],
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


class _NoopTracing:
    def start(self, *args: Any, **kwargs: Any) -> None:
        return None

    def stop(self, *args: Any, **kwargs: Any) -> None:
        return None


class _PrivacyPage:
    def __init__(self, page: Any) -> None:
        self._page = page

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)

    def _mask_locators(self) -> list[Any]:
        locators: list[Any] = [
            self._page.locator("input[type=password]"),
            self._page.locator("[data-sensitive=true]"),
            self._page.locator("[autocomplete=current-password]"),
            self._page.locator("[autocomplete=new-password]"),
            self._page.locator("[autocomplete=one-time-code]"),
        ]
        for step in _SENSITIVE_STEPS.get():
            try:
                locator, _strategy = _interaction._candidate(self._page, step)
            except Exception:
                continue
            locators.append(locator)
        return locators

    def screenshot(self, *args: Any, **kwargs: Any) -> Any:
        options = dict(kwargs)
        existing = [row for row in _list(options.get("mask")) if row is not None]
        options["mask"] = [*existing, *self._mask_locators()]
        return self._page.screenshot(*args, **options)


class _PrivacyContext:
    def __init__(self, context: Any) -> None:
        self._context = context
        self.tracing = _NoopTracing()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)

    def new_page(self, *args: Any, **kwargs: Any) -> _PrivacyPage:
        return _PrivacyPage(self._context.new_page(*args, **kwargs))


class _PrivacyBrowser:
    def __init__(self, browser: Any) -> None:
        self._browser = browser

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)

    def new_context(self, *args: Any, **kwargs: Any) -> _PrivacyContext:
        options = dict(kwargs)
        options.pop("record_har_path", None)
        options.pop("record_har_content", None)
        options.pop("record_har_mode", None)
        return _PrivacyContext(self._browser.new_context(*args, **options))


def _sensitive_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for row in _list(_dict(plan).get("steps")):
        if not isinstance(row, dict):
            continue
        action = _text(row.get("action")).lower()
        if action == "fill" and _interaction._sensitive_fill(row):
            output.append(copy.deepcopy(row))
    return output


def _scrub_result(result: dict[str, Any]) -> dict[str, Any]:
    scrubbed = copy.deepcopy(_dict(result))
    console = []
    for row in _list(scrubbed.get("console")):
        if not isinstance(row, dict):
            continue
        console.append({
            "type": _text(row.get("type")),
            "text_fingerprint": _interaction._fingerprint(_text(row.get("text"))),
        })
    network = []
    for row in _list(scrubbed.get("network")):
        if not isinstance(row, dict):
            continue
        network.append({
            "method": _text(row.get("method")),
            "status": int(row.get("status") or 0),
            "url_fingerprint": _interaction._fingerprint(_text(row.get("url"))),
        })
    scrubbed["console"] = console
    scrubbed["network"] = network
    scrubbed["trace_ref"] = ""
    scrubbed["har_ref"] = ""
    scrubbed["evidence_privacy"] = {
        "policy": EVIDENCE_POLICY,
        "har_persisted": False,
        "trace_persisted": False,
        "sensitive_screenshot_masking": True,
        "console_text_persisted": False,
        "network_url_persisted": False,
        "raw_request_body_persisted": False,
        "raw_response_body_persisted": False,
    }
    return scrubbed


def _rewrite_execution_artifact(
    *,
    result: dict[str, Any],
    root: Path,
) -> None:
    artifact_ref = _text(result.get("artifact_dir"))
    if not artifact_ref:
        return
    root_path = Path(root).resolve()
    artifact_dir = (root_path / artifact_ref).resolve()
    if root_path != artifact_dir and root_path not in artifact_dir.parents:
        return
    path = artifact_dir / "browser_execution.json"
    if not path.is_file():
        return
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def install_controlled_ui_interaction_privacy_guard() -> None:
    if getattr(_interaction, _INSTALL_MARKER, False):
        return
    original_launch = getattr(
        _interaction,
        _interaction.ORIGINAL_LAUNCH if hasattr(_interaction, "ORIGINAL_LAUNCH") else ORIGINAL_LAUNCH,
        None,
    )
    if original_launch is None:
        original_launch = _interaction._launch_browser
    setattr(_interaction, ORIGINAL_LAUNCH, original_launch)
    original_executor = getattr(
        _interaction,
        ORIGINAL_EXECUTOR,
        _interaction.execute_controlled_browser_plan,
    )
    setattr(_interaction, ORIGINAL_EXECUTOR, original_executor)
    original_contract_check = getattr(
        _interaction,
        ORIGINAL_CONTRACT_CHECK,
        _interaction._source_cleanup_contract_error,
    )
    setattr(_interaction, ORIGINAL_CONTRACT_CHECK, original_contract_check)

    def contract_check_with_privacy(plan: dict[str, Any]) -> str:
        error = original_contract_check(plan)
        if error:
            return error
        policy = _text(
            _dict(_interaction._interaction_contract(plan)).get("evidence_policy")
        )
        if policy != EVIDENCE_POLICY:
            return "UI_INTERACTION_EVIDENCE_POLICY_INVALID"
        return ""

    def launch_private_browser() -> tuple[Any, Any, str]:
        runtime, browser, error = original_launch()
        return runtime, _PrivacyBrowser(browser) if browser is not None else None, error

    def execute_with_minimized_evidence(
        project_id: str,
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
        *,
        root: Path,
        run_id: str = "",
    ) -> dict[str, Any]:
        mode = _text(_dict(plan).get("execution_mode") or "safe_read_only")
        if mode != _interaction.WRITE_MODE:
            return original_executor(
                project_id,
                plan,
                runtime_contract,
                root=root,
                run_id=run_id,
            )
        token = _SENSITIVE_STEPS.set(_sensitive_steps(plan))
        try:
            result = original_executor(
                project_id,
                plan,
                runtime_contract,
                root=root,
                run_id=run_id,
            )
        finally:
            _SENSITIVE_STEPS.reset(token)
        scrubbed = _scrub_result(result)
        _rewrite_execution_artifact(result=scrubbed, root=Path(root))
        return scrubbed

    _interaction._source_cleanup_contract_error = contract_check_with_privacy
    _interaction._launch_browser = launch_private_browser
    _interaction.execute_controlled_browser_plan = execute_with_minimized_evidence
    # The browser provider resolves this global at request execution time.
    _interaction._browser.execute_browser_plan = execute_with_minimized_evidence
    setattr(_interaction, _INSTALL_MARKER, True)


__all__ = [
    "EVIDENCE_POLICY",
    "install_controlled_ui_interaction_privacy_guard",
]
