from __future__ import annotations

"""Install a conservative runtime-scenario gate in front of the scan entrypoint.

This patch exists to avoid large rewrites of ``ai_test_asset_center.__main__``.
It wraps ``scan`` so customer-provided runtime_scenario_contract payloads are
validated before the V12 pipeline can receive a live base_url.
"""

import functools
from pathlib import Path
from typing import Any, Callable


_PATCHED = False
_ORIGINAL_SCAN: Callable[..., dict[str, Any]] | None = None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _runtime_block_contract(context: dict[str, Any], gaps: list[dict[str, str]], base_runtime_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = _as_dict(context.get("source_manifest"))
    source_manifest = {
        "source_id": str(manifest.get("source_id") or ""),
        "source_hash": str(manifest.get("source_hash") or ""),
        "source_version_id": str(manifest.get("source_version_id") or ""),
        "source_origin": str(manifest.get("source_origin") or ""),
    }
    codes = [str(item.get("code") or "") for item in gaps if str(item.get("code") or "")]
    runtime_contract = dict(base_runtime_contract or {})
    runtime_contract.update(
        {
            "status": "blocked",
            "reason": "runtime_scenario_contract_blocked",
            "source_manifest": runtime_contract.get("source_manifest") or source_manifest,
            "missing_requirements": sorted(set(codes)),
            "approved_base_url": "",
        }
    )
    return runtime_contract


def _blocked_scan(scan_module: Any, project: str, root: Path, started: float, gaps: list[dict[str, str]], context: dict[str, Any], save_report: bool, output_dir: Path | None) -> dict[str, Any]:
    runtime_contract = _runtime_block_contract(context, gaps)
    return scan_module._blocked_result(project, root, started, gaps, runtime_contract, context, save_report, output_dir)


def install_scan_runtime_gate() -> bool:
    global _PATCHED, _ORIGINAL_SCAN
    if _PATCHED:
        return True
    try:
        import time
        from .runtime_scenario_contract_gate import runtime_scenario_contract_gaps
        from . import __main__ as scan_module
    except Exception:
        return False
    original = getattr(scan_module, "scan", None)
    if not callable(original):
        return False
    _ORIGINAL_SCAN = original

    @functools.wraps(original)
    def guarded_scan(
        project: str,
        root: Path | None = None,
        *,
        prd_text: str = "",
        api_doc_path: str = "",
        api_doc_text: str = "",
        base_url: str = "",
        ci_gate: bool = False,
        multi_layer: bool = True,
        output_dir: Path | None = None,
        save_report: bool = True,
        campaign_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = dict(campaign_context or {})
        if base_url and _as_dict(context.get("runtime_scenario_contract")):
            gaps = runtime_scenario_contract_gaps(context)
            if gaps:
                resolved_root = Path(root or Path.cwd())
                safe_project = str(project or "").strip()
                if not safe_project:
                    return {"success": False, "error": "project is required"}
                return _blocked_scan(scan_module, safe_project, resolved_root, time.time(), gaps, context, save_report, output_dir)
        return original(
            project,
            root=root,
            prd_text=prd_text,
            api_doc_path=api_doc_path,
            api_doc_text=api_doc_text,
            base_url=base_url,
            ci_gate=ci_gate,
            multi_layer=multi_layer,
            output_dir=output_dir,
            save_report=save_report,
            campaign_context=context,
        )

    setattr(scan_module, "scan", guarded_scan)
    _PATCHED = True
    return True


def restore_scan_runtime_gate() -> bool:
    global _PATCHED, _ORIGINAL_SCAN
    if not _PATCHED or _ORIGINAL_SCAN is None:
        return True
    try:
        from . import __main__ as scan_module
        setattr(scan_module, "scan", _ORIGINAL_SCAN)
    except Exception:
        return False
    _PATCHED = False
    _ORIGINAL_SCAN = None
    return True
