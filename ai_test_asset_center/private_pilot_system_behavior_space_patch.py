from __future__ import annotations

"""Runtime wiring for the System Behavior Space Model and learning loop.

The existing V12 builder already creates state-machine slices.  This patch keeps
that behavior intact and attaches the broader behavior-space model to the same
behavior contract so every private-pilot scan carries the real product goal:
open-ended system-promise discovery across API, DB, UI, auth and cross-surface
consistency — not a fixed bug-family list.

It also threads project/root into the builder through a narrow runtime context so
project learning memory can boost future probes.  After every V12 scan it
persists fresh learning memory from confirmed defects, evidence chains and
regression outcomes.
"""

import contextvars
from pathlib import Path
from typing import Any

from ai_test_asset_center import business_state_graph as _bsg
from ai_test_asset_center.behavior_learning_memory import (
    apply_learning_to_probe_candidates,
    load_behavior_learning_memory,
    persist_behavior_learning_memory,
)
from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
    build_system_behavior_space,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_system_behavior_space_patch"
_LEARNING_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_system_behavior_learning_context",
    default={},
)


def _learning_context_from_builder(builder: Any) -> tuple[str, Path | None]:
    project = str(getattr(builder, "system_behavior_space_project", "") or "").strip()
    root_value = getattr(builder, "system_behavior_space_root", None)
    root = Path(root_value) if root_value else None
    if project and root:
        return project, root
    current = _LEARNING_CONTEXT.get({})
    project = str(current.get("project") or "").strip()
    root_value = current.get("root")
    root = Path(root_value) if root_value else None
    return project, root


def install_system_behavior_space_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    if getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", False):
        return

    original_build = getattr(_bsg.BusinessStateGraphBuilder, "build")
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "behavior_contract")

    def _build_with_system_behavior_space(self: Any, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, Any]:
        graphs = original_build(self, prd_text, api_spec_text, db_schema_text)
        try:
            space = build_system_behavior_space(prd_text, api_spec_text, db_schema_text).to_dict()
            project, root = _learning_context_from_builder(self)
            if project and root:
                memory = load_behavior_learning_memory(project, root)
                if memory:
                    space = apply_learning_to_probe_candidates(space, memory)
            self.system_behavior_space = space
        except Exception as exc:
            self.system_behavior_space = {
                "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
                "status": "unavailable",
                "reason": f"system_behavior_space_build_failed:{type(exc).__name__}",
                "summary": {
                    "object_count": 0,
                    "promise_count": 0,
                    "probe_candidate_count": 0,
                    "coverage_gap_count": 1,
                },
            }
        return graphs

    def _behavior_contract_with_system_behavior_space(self: Any) -> dict[str, Any]:
        contract = original_contract(self)
        space = getattr(self, "system_behavior_space", None)
        if isinstance(space, dict) and space:
            contract["system_behavior_space"] = space
            summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
            space_summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
            summary["system_behavior_space_version"] = str(space.get("version") or SYSTEM_BEHAVIOR_SPACE_VERSION)
            summary["system_promise_count"] = int(space_summary.get("promise_count") or 0)
            summary["system_probe_candidate_count"] = int(space_summary.get("probe_candidate_count") or 0)
            summary["system_behavior_object_count"] = int(space_summary.get("object_count") or 0)
            summary["system_behavior_goal"] = "open_ended_system_promise_discovery_across_all_surfaces"
            summary["learning_memory_version"] = str(space_summary.get("learning_memory_version") or "")
            summary["learning_signal_count"] = int(space_summary.get("learning_signal_count") or 0)
            summary["learning_boosted_probe_count"] = int(space_summary.get("learning_boosted_probe_count") or 0)
            contract["summary"] = summary
            gaps = contract.get("coverage_gaps") if isinstance(contract.get("coverage_gaps"), list) else []
            system_gaps = space.get("coverage_gaps") if isinstance(space.get("coverage_gaps"), list) else []
            for gap in system_gaps:
                if isinstance(gap, dict):
                    gaps.append({**gap, "source": "system_behavior_space"})
            contract["coverage_gaps"] = gaps
        return contract

    _bsg.BusinessStateGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE = original_build  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder._ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE = original_contract  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder.build = _build_with_system_behavior_space  # type: ignore[method-assign]
    _bsg.BusinessStateGraphBuilder.behavior_contract = _behavior_contract_with_system_behavior_space  # type: ignore[method-assign]
    _install_v12_learning_lifecycle_patch()
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = True  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def _install_v12_learning_lifecycle_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_LEARNING_LIFECYCLE_PATCHED", False):
        return
    original = getattr(_v12, "run_v12_pipeline", None)
    if not callable(original):
        return

    def _run_v12_pipeline_with_learning_context(project: str, root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _LEARNING_CONTEXT.set({"project": str(project or ""), "root": Path(root)})
        try:
            result = original(project, root, *args, **kwargs)
        finally:
            _LEARNING_CONTEXT.reset(token)
        try:
            memory = persist_behavior_learning_memory(project, Path(root))
            if isinstance(result, dict):
                result["behavior_learning_memory"] = {
                    "version": str(memory.get("version") or ""),
                    "summary": memory.get("summary") if isinstance(memory.get("summary"), dict) else {},
                    "learning_goal": str(memory.get("learning_goal") or ""),
                }
        except Exception as exc:
            if isinstance(result, dict):
                result["behavior_learning_memory"] = {
                    "version": "behavior_learning_memory.v1",
                    "status": "unavailable",
                    "reason": f"behavior_learning_memory_refresh_failed:{type(exc).__name__}",
                }
        return result

    _v12._ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_LEARNING = original  # type: ignore[attr-defined]
    _v12.run_v12_pipeline = _run_v12_pipeline_with_learning_context  # type: ignore[method-assign]
    _v12._SYSTEM_BEHAVIOR_LEARNING_LIFECYCLE_PATCHED = True  # type: ignore[attr-defined]


def prepare_system_behavior_space_learning_context(builder: Any, *, project: str, root: Path) -> Any:
    """Attach project/root learning context before ``builder.build(...)``.

    BusinessStateGraphBuilder intentionally stays generic; the V12 pipeline owns
    project/root.  This helper is a direct bridge for tests or explicit callers;
    the private-pilot runtime also sets a ContextVar around run_v12_pipeline.
    """
    setattr(builder, "system_behavior_space_project", str(project or ""))
    setattr(builder, "system_behavior_space_root", Path(root))
    return builder


def restore_system_behavior_space_patch() -> None:
    original_build = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE", None)
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE", None)
    if callable(original_build):
        _bsg.BusinessStateGraphBuilder.build = original_build  # type: ignore[method-assign]
    if callable(original_contract):
        _bsg.BusinessStateGraphBuilder.behavior_contract = original_contract  # type: ignore[method-assign]
    try:
        from ai_test_asset_center import v12_pipeline as _v12

        original_v12 = getattr(_v12, "_ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_LEARNING", None)
        if callable(original_v12):
            _v12.run_v12_pipeline = original_v12  # type: ignore[method-assign]
        _v12._SYSTEM_BEHAVIOR_LEARNING_LIFECYCLE_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = False  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
