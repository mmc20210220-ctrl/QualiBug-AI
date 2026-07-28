"""Governed complex browser interactions on the existing cleanup authority.

V1 adds four capabilities without creating another finding path:

* project-scoped immutable file upload bindings;
* hash-only downloads deleted immediately after observation;
* source-declared popup URL observation with mandatory close;
* uniquely identified, approved-origin iframe scope for interactions, probes and
  existing deterministic expectations.

Every complex action remains inside ``approved_sandbox_write`` and therefore keeps
persistent cleanup equivalence mandatory. Download or popup mismatch is not promoted
into a formal defect in this version; it fails execution and remains indeterminate.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from . import formal_ui_surface_guard as _formal_guard
from . import professional_ui_interaction_cleanup as _interaction
from . import professional_ui_interaction_privacy_guard as _privacy
from . import professional_ui_readonly as _professional
from .enterprise_knowledge_center import _formal_ui_contracts as _contracts

SET_INPUT_FILES = "set_input_files"
CLICK_DOWNLOAD = "click_download"
CLICK_POPUP = "click_popup"
COMPLEX_INTERACTIVE_ACTIONS = frozenset({
    SET_INPUT_FILES,
    CLICK_DOWNLOAD,
    CLICK_POPUP,
})
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_FILE_BYTES = 25_000_000
MAX_UPLOAD_TOTAL_BYTES = 50_000_000
MAX_DOWNLOAD_BYTES = 50_000_000
_INSTALL_MARKER = "_qualibug_complex_ui_interactions_installed"
_ORIGINAL_VALIDATE = "_qualibug_interaction_validator_before_complex_actions"
_ORIGINAL_EXECUTE = "_qualibug_interaction_executor_before_complex_actions"
_ORIGINAL_CANDIDATE = "_qualibug_professional_candidate_before_frame_scope"
_ORIGINAL_PROBE = "_qualibug_probe_material_before_frame_scope"
_ORIGINAL_BROWSER_EXECUTOR = "_qualibug_browser_executor_before_complex_context"
_ORIGINAL_SENSITIVE_STEPS = "_qualibug_sensitive_steps_before_complex_actions"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_complex_ui_runtime_context",
    default={},
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
    code: str,
) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise _interaction._browser.BrowserExecutionError(code)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _interaction._browser.BrowserExecutionError(code) from exc
    if not minimum <= number <= maximum:
        raise _interaction._browser.BrowserExecutionError(code)
    return number


def _origin(value: Any) -> str:
    parsed = urlparse(_text(value, limit=2000))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _approved_origins(runtime_contract: dict[str, Any], field: str) -> set[str]:
    origins = {_origin(runtime_contract.get("approved_base_url"))}
    origins.update(
        _origin(value)
        for value in _list(runtime_contract.get(field))
        if _origin(value)
    )
    return {value for value in origins if value}


def _validate_frame_scope(step: dict[str, Any]) -> None:
    selector = _text(step.get("frame_selector"), limit=500)
    origin = _origin(step.get("frame_origin"))
    if bool(selector) != bool(origin):
        raise _interaction._browser.BrowserExecutionError(
            "browser_frame_selector_and_origin_required_together"
        )
    if selector:
        step["frame_selector"] = selector
        step["frame_origin"] = origin
        if step.get("frame_locator_intent"):
            raise _interaction._browser.BrowserExecutionError(
                "browser_frame_authority_ambiguous"
            )


def _validate_file_refs(step: dict[str, Any]) -> None:
    forbidden = {
        "file_path",
        "path",
        "files",
        "content",
        "payload",
        "base64",
    }
    if forbidden & set(step):
        raise _interaction._browser.BrowserExecutionError(
            "browser_upload_literal_file_material_forbidden"
        )
    raw_refs = step.get("file_refs")
    if not isinstance(raw_refs, list):
        raise _interaction._browser.BrowserExecutionError(
            "browser_upload_file_refs_list_required"
        )
    refs = [_text(value, limit=160) for value in raw_refs]
    if any(not value for value in refs) or len(set(refs)) != len(refs):
        raise _interaction._browser.BrowserExecutionError(
            "browser_upload_file_refs_invalid"
        )
    phase = _text(step.get("phase")).lower()
    if phase == "treatment" and not refs:
        raise _interaction._browser.BrowserExecutionError(
            "browser_upload_treatment_file_refs_missing"
        )
    if len(refs) > MAX_UPLOAD_FILES:
        raise _interaction._browser.BrowserExecutionError(
            "browser_upload_file_count_exceeded"
        )
    step["file_refs"] = refs


def _validate_complex_interaction(step: dict[str, Any], action: str) -> None:
    _interaction._validate_locator_fields(step)
    _validate_frame_scope(step)
    phase = _text(step.get("phase")).lower()
    if action == SET_INPUT_FILES:
        _validate_file_refs(step)
        return
    if phase != "treatment":
        raise _interaction._browser.BrowserExecutionError(
            f"browser_complex_interaction_treatment_only:{action}"
        )
    if action == CLICK_DOWNLOAD:
        step["max_download_bytes"] = _bounded_int(
            step.get("max_download_bytes"),
            default=MAX_DOWNLOAD_BYTES,
            minimum=1,
            maximum=MAX_DOWNLOAD_BYTES,
            code="browser_download_size_limit_invalid",
        )
        if step.get("delete_after_observation") is not True:
            raise _interaction._browser.BrowserExecutionError(
                "browser_download_delete_after_observation_required"
            )
        expected_sha = _text(step.get("expected_sha256"), limit=64).lower()
        if expected_sha and not _SHA256_RE.fullmatch(expected_sha):
            raise _interaction._browser.BrowserExecutionError(
                "browser_download_expected_sha256_invalid"
            )
        step["expected_sha256"] = expected_sha
        return
    if action == CLICK_POPUP:
        expected_url = _text(step.get("expected_url"), limit=2000)
        if not expected_url:
            raise _interaction._browser.BrowserExecutionError(
                "browser_popup_expected_url_missing"
            )
        if step.get("close_after_observation") is not True:
            raise _interaction._browser.BrowserExecutionError(
                "browser_popup_close_after_observation_required"
            )
        step["expected_url"] = expected_url
        step["wait_until"] = _text(
            step.get("wait_until") or "domcontentloaded",
            limit=40,
        )
        if step["wait_until"] not in {"commit", "domcontentloaded", "load", "networkidle"}:
            raise _interaction._browser.BrowserExecutionError(
                "browser_popup_wait_state_invalid"
            )
        return
    raise _interaction._browser.BrowserExecutionError(
        f"browser_action_unsupported:{action}"
    )


def _frame_surface(page: Any, step: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    selector = _text(step.get("frame_selector"), limit=500)
    if not selector:
        return page, {}
    context = _dict(_RUNTIME_CONTEXT.get())
    runtime_contract = _dict(context.get("runtime_contract"))
    outer = page.locator(selector)
    count = int(outer.count())
    if count == 0:
        raise RuntimeError("UI_FRAME_TARGET_MISSING")
    if count != 1:
        raise RuntimeError(f"UI_FRAME_TARGET_AMBIGUOUS:{count}")
    current_url = _text(getattr(page, "url", ""), limit=2000)
    source = _text(outer.get_attribute("src"), limit=2000)
    actual_url = urljoin(current_url, source) if source else current_url
    actual_origin = _origin(actual_url)
    expected_origin = _origin(step.get("frame_origin"))
    approved = _approved_origins(runtime_contract, "approved_frame_origins")
    if not expected_origin or expected_origin not in approved:
        raise RuntimeError("UI_FRAME_ORIGIN_NOT_APPROVED")
    if actual_origin != expected_origin:
        raise RuntimeError("UI_FRAME_ORIGIN_MISMATCH")
    return page.frame_locator(selector), {
        "frame_selector_fingerprint": _interaction._fingerprint(selector),
        "frame_origin_fingerprint": _interaction._fingerprint(expected_origin),
        "frame_target_unique": True,
        "raw_frame_selector_included": False,
        "raw_frame_origin_included": False,
    }


def _candidate_with_frame_scope(page: Any, row: dict[str, Any]) -> tuple[Any, str]:
    original = getattr(_professional, _ORIGINAL_CANDIDATE)
    surface, frame = _frame_surface(page, row)
    stripped = copy.deepcopy(row)
    stripped.pop("frame_selector", None)
    stripped.pop("frame_origin", None)
    locator, strategy = original(surface, stripped)
    if frame:
        strategy = "iframe_scoped:" + strategy
    return locator, strategy


def _path_has_symlink(candidate: Path, allowed_root: Path) -> bool:
    try:
        relative = candidate.relative_to(allowed_root)
    except ValueError:
        return True
    current = allowed_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _resolve_upload_files(
    refs: list[str],
    runtime_contract: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    if not refs:
        return [], []
    context = _dict(_RUNTIME_CONTEXT.get())
    root = Path(_text(context.get("root"))).resolve()
    project = _text(context.get("project"), limit=160)
    if not root or not project:
        raise RuntimeError("UI_UPLOAD_RUNTIME_CONTEXT_MISSING")
    bindings = _dict(runtime_contract.get("ui_file_bindings"))
    allowed_roots = [
        (root / "platform_inputs" / project).resolve(),
        (root / "platform_workspace" / project / "ui_upload_fixtures").resolve(),
    ]
    paths: list[str] = []
    evidence: list[dict[str, Any]] = []
    total = 0
    for ref in refs:
        binding = _dict(bindings.get(ref))
        if not binding:
            raise RuntimeError("UI_UPLOAD_FILE_BINDING_MISSING")
        if binding.get("approved") is not True and _text(
            binding.get("status")
        ).lower() != "approved":
            raise RuntimeError("UI_UPLOAD_FILE_BINDING_NOT_APPROVED")
        raw_path = _text(binding.get("file_path") or binding.get("path"), limit=2000)
        expected_sha = _text(binding.get("sha256"), limit=64).lower()
        if not raw_path or not _SHA256_RE.fullmatch(expected_sha):
            raise RuntimeError("UI_UPLOAD_FILE_BINDING_IDENTITY_INCOMPLETE")
        raw_candidate = Path(raw_path)
        candidate = (
            raw_candidate.resolve()
            if raw_candidate.is_absolute()
            else (root / raw_candidate).resolve()
        )
        allowed = next(
            (
                scope
                for scope in allowed_roots
                if candidate == scope or scope in candidate.parents
            ),
            None,
        )
        if allowed is None or _path_has_symlink(candidate, allowed):
            raise RuntimeError("UI_UPLOAD_FILE_OUTSIDE_PROJECT_SCOPE")
        if not candidate.is_file():
            raise RuntimeError("UI_UPLOAD_FILE_NOT_FOUND")
        size = int(candidate.stat().st_size)
        if size < 1 or size > MAX_UPLOAD_FILE_BYTES:
            raise RuntimeError("UI_UPLOAD_FILE_SIZE_INVALID")
        total += size
        if total > MAX_UPLOAD_TOTAL_BYTES:
            raise RuntimeError("UI_UPLOAD_TOTAL_SIZE_EXCEEDED")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != expected_sha:
            raise RuntimeError("UI_UPLOAD_FILE_HASH_MISMATCH")
        paths.append(str(candidate))
        evidence.append({
            "file_ref_fingerprint": _interaction._fingerprint(ref),
            "sha256": digest,
            "size_bytes": size,
            "content_type": _text(binding.get("content_type"), limit=120),
            "raw_file_ref_included": False,
            "raw_file_path_included": False,
            "raw_filename_included": False,
            "raw_file_content_included": False,
        })
    return paths, evidence


def _hash_download(download: Any, max_bytes: int) -> dict[str, Any]:
    failure = _text(download.failure(), limit=300)
    if failure:
        raise RuntimeError("UI_DOWNLOAD_FAILED")
    path_value = download.path()
    if not path_value:
        raise RuntimeError("UI_DOWNLOAD_PATH_UNAVAILABLE")
    path = Path(path_value)
    size = int(path.stat().st_size)
    if size < 0 or size > max_bytes:
        raise RuntimeError("UI_DOWNLOAD_SIZE_LIMIT_EXCEEDED")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    suffix = Path(_text(download.suggested_filename, limit=240)).suffix.lower()[:20]
    return {
        "download_sha256": digest,
        "download_size_bytes": size,
        "suggested_filename_fingerprint": _interaction._fingerprint(
            _text(download.suggested_filename, limit=240)
        ),
        "suggested_filename_suffix": suffix,
        "raw_download_path_included": False,
        "raw_download_filename_included": False,
        "raw_download_content_included": False,
        "download_persisted": False,
    }


def _execute_complex_interaction(
    *,
    page: Any,
    step: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    action = _text(step.get("action")).lower()
    locator, strategy = _interaction._candidate(page, step)
    _interaction._require_unique(locator, action)
    timeout = int(step.get("timeout_ms") or 10_000)
    _surface, frame_identity = _frame_surface(page, step)
    receipt: dict[str, Any] = {
        "action": action,
        "phase": _text(step.get("phase")),
        "locator_strategy": strategy,
        "locator_intent_fingerprint": _interaction._fingerprint(
            step.get("locator_intent") or step.get("selector")
        ),
        "raw_input_value_included": False,
        **frame_identity,
    }
    if action == SET_INPUT_FILES:
        refs = [_text(value, limit=160) for value in _list(step.get("file_refs"))]
        paths, evidence = _resolve_upload_files(refs, runtime_contract)
        locator.set_input_files(paths, timeout=timeout)
        receipt.update({
            "uploaded_file_count": len(evidence),
            "uploaded_files": evidence,
            "upload_binding_authority": "runtime_contract.ui_file_bindings",
        })
        return receipt
    if action == CLICK_DOWNLOAD:
        download = None
        try:
            with page.expect_download(timeout=timeout) as download_info:
                locator.click(timeout=timeout)
            download = download_info.value
            observed = _hash_download(
                download,
                int(step.get("max_download_bytes") or MAX_DOWNLOAD_BYTES),
            )
            expected_sha = _text(step.get("expected_sha256"), limit=64).lower()
            if expected_sha and observed["download_sha256"] != expected_sha:
                raise RuntimeError("UI_DOWNLOAD_SHA256_MISMATCH")
            receipt.update(observed)
            receipt["expected_sha256_declared"] = bool(expected_sha)
            return receipt
        finally:
            if download is not None:
                try:
                    download.delete()
                except Exception:
                    pass
    if action == CLICK_POPUP:
        expected = urljoin(
            _text(runtime_contract.get("approved_base_url")).rstrip("/") + "/",
            _text(step.get("expected_url")),
        )
        expected_origin = _origin(expected)
        approved = _approved_origins(runtime_contract, "approved_popup_origins")
        if expected_origin not in approved:
            raise RuntimeError("UI_POPUP_EXPECTED_ORIGIN_NOT_APPROVED")
        popup = None
        try:
            with page.expect_popup(timeout=timeout) as popup_info:
                locator.click(timeout=timeout)
            popup = popup_info.value
            popup.wait_for_load_state(
                _text(step.get("wait_until") or "domcontentloaded"),
                timeout=timeout,
            )
            actual = _text(popup.url, limit=2000)
            if _origin(actual) not in approved:
                raise RuntimeError("UI_POPUP_ACTUAL_ORIGIN_NOT_APPROVED")
            if actual != expected:
                raise RuntimeError("UI_POPUP_URL_MISMATCH")
            receipt.update({
                "popup_url_fingerprint": _interaction._fingerprint(actual),
                "popup_origin_fingerprint": _interaction._fingerprint(_origin(actual)),
                "popup_title_fingerprint": _interaction._fingerprint(
                    _text(popup.title(), limit=500)
                ),
                "popup_closed_after_observation": True,
                "raw_popup_url_included": False,
                "raw_popup_title_included": False,
            })
            return receipt
        finally:
            if popup is not None:
                try:
                    popup.close()
                except Exception:
                    pass
    raise RuntimeError(f"UI_INTERACTION_ACTION_UNSUPPORTED:{action}")


def _probe_with_frame_identity(page: Any, probe: dict[str, Any]) -> dict[str, Any]:
    original = getattr(_interaction, _ORIGINAL_PROBE)
    receipt = original(page, probe)
    if not _text(probe.get("frame_selector"), limit=500):
        return receipt
    _surface, identity = _frame_surface(page, probe)
    return {**receipt, **identity}


def _execute_with_complex_context(
    project_id: str,
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str = "",
) -> dict[str, Any]:
    original = getattr(_interaction, _ORIGINAL_BROWSER_EXECUTOR)
    token = _RUNTIME_CONTEXT.set({
        "root": str(Path(root).resolve()),
        "project": _interaction._browser._safe_project(project_id),
        "runtime_contract": runtime_contract,
    })
    try:
        return original(
            project_id,
            plan,
            runtime_contract,
            root=root,
            run_id=run_id,
        )
    finally:
        _RUNTIME_CONTEXT.reset(token)


def _sensitive_steps_with_uploads(plan: dict[str, Any]) -> list[dict[str, Any]]:
    original = getattr(_privacy, _ORIGINAL_SENSITIVE_STEPS)
    rows = list(original(plan))
    rows.extend(
        copy.deepcopy(row)
        for row in _list(_dict(plan).get("steps"))
        if isinstance(row, dict)
        and _text(row.get("action")).lower() == SET_INPUT_FILES
    )
    return rows


def _patch_loaded_action_aliases(actions: frozenset[str]) -> None:
    for module_name in (
        "ai_test_asset_center.professional_ui_coverage_projection",
        "ai_test_asset_center.source_ui_obligation_binding",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "INTERACTIVE_ACTIONS"):
            module.INTERACTIVE_ACTIONS = actions


def install_professional_ui_complex_interactions() -> None:
    if getattr(_interaction, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _interaction,
        _ORIGINAL_VALIDATE,
        _interaction._validate_interactive_step,
    )
    original_execute = getattr(
        _interaction,
        _ORIGINAL_EXECUTE,
        _interaction._execute_interaction,
    )
    original_candidate = getattr(
        _professional,
        _ORIGINAL_CANDIDATE,
        _professional._candidate,
    )
    original_probe = getattr(
        _interaction,
        _ORIGINAL_PROBE,
        _interaction._probe_material,
    )
    original_browser_executor = getattr(
        _interaction,
        _ORIGINAL_BROWSER_EXECUTOR,
        _interaction.execute_controlled_browser_plan,
    )
    original_sensitive_steps = getattr(
        _privacy,
        _ORIGINAL_SENSITIVE_STEPS,
        _privacy._sensitive_steps,
    )
    setattr(_interaction, _ORIGINAL_VALIDATE, original_validate)
    setattr(_interaction, _ORIGINAL_EXECUTE, original_execute)
    setattr(_professional, _ORIGINAL_CANDIDATE, original_candidate)
    setattr(_interaction, _ORIGINAL_PROBE, original_probe)
    setattr(_interaction, _ORIGINAL_BROWSER_EXECUTOR, original_browser_executor)
    setattr(_privacy, _ORIGINAL_SENSITIVE_STEPS, original_sensitive_steps)

    def validate_with_complex_actions(step: dict[str, Any], action: str) -> None:
        if action in COMPLEX_INTERACTIVE_ACTIONS:
            _validate_complex_interaction(step, action)
            return
        _validate_frame_scope(step)
        original_validate(step, action)

    def execute_with_complex_actions(
        *,
        page: Any,
        step: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        action = _text(step.get("action")).lower()
        if action in COMPLEX_INTERACTIVE_ACTIONS:
            return _execute_complex_interaction(
                page=page,
                step=step,
                runtime_contract=runtime_contract,
            )
        receipt = original_execute(
            page=page,
            step=step,
            runtime_contract=runtime_contract,
        )
        if _text(step.get("frame_selector"), limit=500):
            _surface, identity = _frame_surface(page, step)
            return {**receipt, **identity}
        return receipt

    actions = frozenset({
        *_interaction.INTERACTIVE_ACTIONS,
        *COMPLEX_INTERACTIVE_ACTIONS,
    })
    _interaction.INTERACTIVE_ACTIONS = actions
    _interaction._validate_interactive_step = validate_with_complex_actions
    _interaction._execute_interaction = execute_with_complex_actions
    _interaction._probe_material = _probe_with_frame_identity
    _professional._candidate = _candidate_with_frame_scope
    _privacy._sensitive_steps = _sensitive_steps_with_uploads
    _interaction.execute_controlled_browser_plan = _execute_with_complex_context
    _interaction._browser.execute_browser_plan = _execute_with_complex_context
    _formal_guard._READ_ONLY_ACTIONS = frozenset({
        *_formal_guard._READ_ONLY_ACTIONS,
        *COMPLEX_INTERACTIVE_ACTIONS,
    })
    _contracts.INTERACTIVE_ACTIONS = actions
    _contracts._ALLOWED_ACTIONS = frozenset({
        *_contracts._ALLOWED_ACTIONS,
        *COMPLEX_INTERACTIVE_ACTIONS,
    })
    _patch_loaded_action_aliases(actions)
    setattr(_interaction, _INSTALL_MARKER, True)


__all__ = [
    "CLICK_DOWNLOAD",
    "CLICK_POPUP",
    "COMPLEX_INTERACTIVE_ACTIONS",
    "SET_INPUT_FILES",
    "install_professional_ui_complex_interactions",
]
