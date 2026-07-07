from __future__ import annotations

"""Attach P3 seed-bug benchmark reports to scan output.

This patch keeps the core scan entrypoint stable. When callers provide
``campaign_context['p3_seed_defects']`` the wrapper evaluates the scan result
against the supplied seed defects, appends ``p3_seed_bug_benchmark`` to the
returned result, and rewrites ``platform_outputs/<project>/scan_result.json``.
"""

import functools
import json
import re
from pathlib import Path
from typing import Any, Callable


_PATCHED = False
_ORIGINAL_SCAN: Callable[..., dict[str, Any]] | None = None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _seed_defects(context: dict[str, Any]) -> list[dict[str, Any]]:
    raw = context.get("p3_seed_defects") or context.get("seed_bug_defects") or context.get("seed_defects")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _benchmark_input(result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    observations = context.get("http_observations") or context.get("p3_http_observations")
    if isinstance(observations, list):
        payload["http_observations"] = observations
    return payload


def _attach_benchmark(result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    seeds = _seed_defects(context)
    if not seeds or not isinstance(result, dict) or result.get("success") is not True:
        return result
    try:
        from .p3_seed_bug_benchmark import evaluate_seed_bug_benchmark

        result["p3_seed_bug_benchmark"] = evaluate_seed_bug_benchmark(_benchmark_input(result, context), seeds)
    except Exception as exc:
        result["p3_seed_bug_benchmark"] = {
            "schema_version": "p3-seed-bug-benchmark-v1",
            "grade": "error",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "total_seed_defects": len(seeds),
            "found_count": 0,
            "missed_count": len(seeds),
            "detection_rate": 0.0,
        }
    return result


def _rewrite_scan_result(result: dict[str, Any], root: Path, project: str) -> None:
    if "p3_seed_bug_benchmark" not in result:
        return
    _write_json(root / "platform_outputs" / _safe_project(project) / "scan_result.json", result)
    report_path = result.get("report_path")
    if report_path:
        path = Path(str(report_path))
        if path.exists():
            try:
                report = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(report, dict):
                    report["p3_seed_bug_benchmark"] = result["p3_seed_bug_benchmark"]
                    _write_json(path, report)
            except Exception:
                pass


def install_p3_benchmark_scan_patch() -> bool:
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
        context = dict(campaign_context or {})
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
            campaign_context=context,
        )
        if isinstance(result, dict):
            result = _attach_benchmark(result, context)
            try:
                _rewrite_scan_result(result, Path(root or Path.cwd()), str(project or ""))
            except Exception:
                pass
        return result

    setattr(scan_module, "scan", patched_scan)
    _PATCHED = True
    return True


def restore_p3_benchmark_scan_patch() -> bool:
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
