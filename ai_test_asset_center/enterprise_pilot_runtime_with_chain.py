from __future__ import annotations

"""Chain-aware enterprise pilot runtime wrapper.

Binds the chain-aware discovery runner through
``enterprise_pilot_runtime.set_real_project_discovery_runner`` instead of
replacing the ``run_real_project_discovery`` symbol.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from . import enterprise_pilot_runtime as _runtime
from .real_project_discovery_with_chain import run_real_project_discovery_with_chain
from .real_project_onboarding import ROOT

PATCH_SOURCE = "ai_test_asset_center.enterprise_pilot_runtime_with_chain"


def install_chain_aware_pilot_runtime_patch() -> None:
    """Select the chain-aware discovery runner for pilot runtime tasks."""
    if getattr(_runtime, "_MAIN_CHAIN_DISCOVERY_PATCHED", False):
        return
    original = _runtime.get_real_project_discovery_runner()
    _runtime._ORIGINAL_RUN_REAL_PROJECT_DISCOVERY = original  # type: ignore[attr-defined]
    _runtime.set_real_project_discovery_runner(run_real_project_discovery_with_chain)
    _runtime._MAIN_CHAIN_DISCOVERY_PATCHED = True  # type: ignore[attr-defined]
    _runtime._MAIN_CHAIN_DISCOVERY_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]
    _runtime._MAIN_CHAIN_DISCOVERY_MODE = "first_class_runner"  # type: ignore[attr-defined]


def chain_aware_pilot_runtime_patch_status() -> dict[str, Any]:
    active = _runtime.get_real_project_discovery_runner()
    return {
        "patched": bool(getattr(_runtime, "_MAIN_CHAIN_DISCOVERY_PATCHED", False)),
        "source": str(getattr(_runtime, "_MAIN_CHAIN_DISCOVERY_PATCH_SOURCE", "")),
        "mode": str(getattr(_runtime, "_MAIN_CHAIN_DISCOVERY_MODE", "") or ""),
        "has_original_discovery_runner": bool(getattr(_runtime, "_ORIGINAL_RUN_REAL_PROJECT_DISCOVERY", None)),
        "active_discovery_runner": getattr(active, "__name__", ""),
    }


def restore_chain_aware_pilot_runtime_patch() -> None:
    """Restore the prior discovery runner for isolated diagnostics/tests."""
    original = getattr(_runtime, "_ORIGINAL_RUN_REAL_PROJECT_DISCOVERY", None)
    _runtime.set_real_project_discovery_runner(original if callable(original) else None)
    _runtime._ORIGINAL_RUN_REAL_PROJECT_DISCOVERY = None  # type: ignore[attr-defined]
    _runtime._MAIN_CHAIN_DISCOVERY_PATCHED = False  # type: ignore[attr-defined]
    _runtime._MAIN_CHAIN_DISCOVERY_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_runtime, "_MAIN_CHAIN_DISCOVERY_MODE"):
        delattr(_runtime, "_MAIN_CHAIN_DISCOVERY_MODE")


def run_next_pilot_task_with_chain(project_id: str = "real_project_demo", root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    install_chain_aware_pilot_runtime_patch()
    return _runtime.run_next_pilot_task(project_id, root or ROOT, actor)


def operate_enterprise_pilot_runtime_with_chain(project_id: str, action: str, payload: dict[str, Any] | None = None, root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    install_chain_aware_pilot_runtime_patch()
    result = _runtime.operate_enterprise_pilot_runtime(project_id, action, payload, root, actor)
    if isinstance(result, dict):
        result["main_chain_runtime_patch"] = chain_aware_pilot_runtime_patch_status()
    return result


def build_enterprise_pilot_overview_with_chain(project_id: str = "real_project_demo", root: Path | None = None, rebuild_control: bool = False) -> dict[str, Any]:
    install_chain_aware_pilot_runtime_patch()
    overview = _runtime.build_enterprise_pilot_overview(project_id, root or ROOT, rebuild_control)
    if isinstance(overview, dict):
        overview["main_chain_runtime_patch"] = chain_aware_pilot_runtime_patch_status()
    return overview


def _cli() -> int:
    parser = argparse.ArgumentParser(description="QualiBug chain-aware enterprise pilot runtime")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--run-next", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--overview", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    install_chain_aware_pilot_runtime_patch()
    if args.status:
        print(json.dumps(chain_aware_pilot_runtime_patch_status(), ensure_ascii=False, indent=2))
        return 0
    if args.run_next:
        print(json.dumps(run_next_pilot_task_with_chain(args.project, root, {"name": "cli_operator", "role": "qa_lead"}), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(build_enterprise_pilot_overview_with_chain(args.project, root, rebuild_control=not args.overview), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
