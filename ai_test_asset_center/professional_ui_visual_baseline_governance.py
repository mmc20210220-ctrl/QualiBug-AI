"""Project-scope governance for formal visual baseline files.

Only two source-declared relative namespaces are valid:

* ``visual_baselines/...`` resolves below the project's immutable input area;
* ``approved_visual_baselines/...`` resolves below the project's approved
  workspace baseline area.

Arbitrary project PNG files, absolute paths, traversal, symlink escapes and
ambiguous fallback lookup are rejected before image decoding.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from . import professional_ui_visual_baseline as _visual

INPUT_PREFIX = "visual_baselines"
APPROVED_PREFIX = "approved_visual_baselines"
_INSTALL_MARKER = "_qualibug_visual_baseline_governance_installed"
_ORIGINAL_REF_VALIDATOR = "_qualibug_visual_ref_validator_before_governance"
_ORIGINAL_PATH_RESOLVER = "_qualibug_visual_path_resolver_before_governance"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _scope_for_ref(root: Path, project: str, ref: str) -> tuple[Path, Path]:
    root_path = Path(root).resolve()
    project_key = _visual._professional._browser._safe_project(project)
    path = PurePosixPath(ref)
    if not path.parts:
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_SCOPE_INVALID"
        )
    if path.parts[0] == INPUT_PREFIX:
        scope = (root_path / "platform_inputs" / project_key / INPUT_PREFIX).resolve()
        suffix = path.parts[1:]
    elif path.parts[0] == APPROVED_PREFIX:
        scope = (
            root_path
            / "platform_workspace"
            / project_key
            / APPROVED_PREFIX
        ).resolve()
        suffix = path.parts[1:]
    else:
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_SCOPE_INVALID"
        )
    if not suffix:
        raise _visual.VisualBaselineObservationError(
            "UI_VISUAL_BASELINE_REF_INVALID"
        )
    return scope, (scope / Path(*suffix)).resolve()


def install_visual_baseline_governance() -> None:
    if getattr(_visual, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _visual,
        _ORIGINAL_REF_VALIDATOR,
        _visual._validate_relative_baseline_ref,
    )
    original_resolve = getattr(
        _visual,
        _ORIGINAL_PATH_RESOLVER,
        _visual._safe_baseline_path,
    )
    setattr(_visual, _ORIGINAL_REF_VALIDATOR, original_validate)
    setattr(_visual, _ORIGINAL_PATH_RESOLVER, original_resolve)

    def validate_governed_ref(value: Any) -> str:
        ref = original_validate(value)
        path = PurePosixPath(ref)
        if not path.parts or path.parts[0] not in {
            INPUT_PREFIX,
            APPROVED_PREFIX,
        }:
            raise _visual._professional._browser.BrowserExecutionError(
                "browser_visual_baseline_scope_invalid"
            )
        if len(path.parts) < 2:
            raise _visual._professional._browser.BrowserExecutionError(
                "browser_visual_baseline_ref_invalid"
            )
        return ref

    def resolve_governed_path(root: Path, project: str, ref: str) -> Path:
        scope, candidate = _scope_for_ref(root, project, ref)
        if scope != candidate and scope not in candidate.parents:
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_BASELINE_SCOPE_ESCAPE"
            )
        if not candidate.is_file():
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_BASELINE_NOT_FOUND"
            )
        if candidate.suffix.lower() != ".png":
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_BASELINE_PNG_REQUIRED"
            )
        size = candidate.stat().st_size
        if size <= 0 or size > _visual.MAX_BASELINE_BYTES:
            raise _visual.VisualBaselineObservationError(
                "UI_VISUAL_BASELINE_SIZE_INVALID"
            )
        return candidate

    _visual._validate_relative_baseline_ref = validate_governed_ref
    _visual._safe_baseline_path = resolve_governed_path
    setattr(_visual, _INSTALL_MARKER, True)


__all__ = [
    "APPROVED_PREFIX",
    "INPUT_PREFIX",
    "install_visual_baseline_governance",
]
