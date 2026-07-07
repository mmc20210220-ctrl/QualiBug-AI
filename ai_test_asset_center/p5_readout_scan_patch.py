from __future__ import annotations

"""Attach P5 executive readout pack to scan output."""

import functools
import json
import re
from pathlib import Path
from typing import Any, Callable


_PATCHED = False
_ORIGINAL_SCAN: Callable[..., dict[str, Any]] | None = None


def _safe_project(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return normalized or "unscoped"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _attach_pack(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or "p4_pilot_success_gate" not in result:
        return result
    try:
        from .p5_executive_readout_pack import build_p5_executive_readout_pack

        result["p5_executive_readout_pack"] = build_p5_executive_readout_pack(result)
    except Exception as exc:
        result["p5_executive_readout_pack"] = {
            "schema_version": "p5-executive-readout-pack-v1",
            "customer_safe": True,
            "decision": "error",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    return result


def _rewrite_scan_result(result: dict[str, Any], root: Path, project: str) -> None:
    if "p5_executive_readout_pack" not in result:
        return
    _write_json(root / "platform_outputs" / _safe_project(project) / "scan_result.json", result)
    report_path = result.get("report_path")
    if report_path:
        path = Path(str(report_path))
        if path.exists():
            try:
                report = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(report, dict):
                    report["p5_executive_readout_pack"] = result["p5_executive_readout_pack"]
                    _write_json(path, report)
            except Exception:
                pass


def install_p5_readout_scan_patch() -> bool:
    global _PATCHED, _ORIGINAL_SCAN
    if _PATCHED:
        return True
    try:
        from . import __main__ as scan_module
    except Exception:
        return False
    original = getattr(scan_module, "scan", None)
    if not callable(original):
        return False
    _ORIGINAL_SCAN = original

    @functools.wraps(original)
    def patched_scan(
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
        result = original(
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
            campaign_context=campaign_context,
        )
        if isinstance(result, dict):
            result = _attach_pack(result)
            try:
                _rewrite_scan_result(result, Path(root or Path.cwd()), str(project or ""))
            except Exception:
                pass
        return result

    setattr(scan_module, "scan", patched_scan)
    _PATCHED = True
    return True


def restore_p5_readout_scan_patch() -> bool:
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
