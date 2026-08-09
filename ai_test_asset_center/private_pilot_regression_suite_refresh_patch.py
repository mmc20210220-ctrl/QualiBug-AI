from __future__ import annotations

"""Refresh the regression suite after a private-pilot scan.

Confirmed findings are convertible into durable regression probes via
``regression_suite_builder``. After a scan produces confirmed finding artifacts,
this module refreshes the smoke/release/full regression suite through a
first-class ``scan`` post-hook so the next "run regression" action uses the
latest delivered bugs.

Post-processing only:
- it does not create findings;
- it does not execute regression requests;
- it only rebuilds the suite manifest from existing evidence-backed sources;
- scan failures and blocked scans are left unchanged.
"""

import json
from pathlib import Path
from typing import Any, Callable

PATCH_SOURCE = "ai_test_asset_center.private_pilot_regression_suite_refresh_patch"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_project(value: str) -> str:
    import re

    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return normalized or "unscoped"


def _extract_project_root(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, Path]:
    project = str(kwargs.get("project") or (args[0] if args else "") or "").strip()
    root_value = kwargs.get("root")
    if root_value is None and len(args) > 1:
        root_value = args[1]
    root = Path(root_value or Path.cwd())
    return project, root


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


def _scan_has_customer_confirmed_findings(result: dict[str, Any]) -> bool:
    findings = result.get("findings") if isinstance(result.get("findings"), list) else []
    for item in findings:
        if not isinstance(item, dict):
            continue
        if str(item.get("confirmation_status") or "").lower() == "confirmed":
            return True
        if str(item.get("customer_delivery_status") or "").lower() == "defect":
            return True
        if str(item.get("bug_status") or "").lower() == "reproduced" and bool(item.get("gate_passed")):
            return True
    return False


def _confirmed_ledger_exists(project: str, root: Path) -> bool:
    path = root / "platform_workspace" / _safe_project(project) / "defect_discovery" / "confirmed_findings.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        return isinstance(payload, dict) and bool(payload)
    except Exception:
        return False


def refresh_regression_suite_after_scan(
    result: dict[str, Any],
    *,
    project: str,
    root: Path,
    builder: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    if not project:
        return result
    if result.get("success") is False:
        return result
    execution_status = str(result.get("execution_status") or "").strip().lower()
    if execution_status == "blocked":
        return result

    safe_project = _safe_project(project)
    should_refresh = _scan_has_customer_confirmed_findings(result) or _confirmed_ledger_exists(safe_project, root)
    if not should_refresh:
        result["regression_suite_refresh"] = {
            "status": "skipped",
            "patch_source": PATCH_SOURCE,
            "reason": "no_confirmed_findings_or_confirmed_ledger",
        }
        return result

    try:
        if builder is None:
            from ai_test_asset_center.regression_suite_builder import build_regression_suite
            builder = build_regression_suite
        suite = builder(safe_project, root=root, options={})
        summary = _as_dict(suite.get("summary"))
        refresh = {
            "status": "refreshed",
            "patch_source": PATCH_SOURCE,
            "suite_ref": f"platform_outputs/{safe_project}/regression_suite/regression_suite.json",
            "summary": {
                "total_probe_count": int(summary.get("total_probe_count") or 0),
                "smoke_count": int(summary.get("smoke_count") or 0),
                "release_count": int(summary.get("release_count") or 0),
                "full_count": int(summary.get("full_count") or 0),
                "confirmed_ledger_probe_count": int(summary.get("confirmed_ledger_probe_count") or 0),
                "ci_gate_recommendation": str(summary.get("ci_gate_recommendation") or ""),
            },
            "honesty_rule": "Regression suite refresh only materializes probes from existing evidence-backed sources; it does not execute regression or claim the fix passed.",
        }
        result["regression_suite_refresh"] = refresh
        result["regression_suite"] = {
            "ref": refresh["suite_ref"],
            **refresh["summary"],
        }
    except Exception as exc:
        result["regression_suite_refresh"] = {
            "status": "refresh_failed",
            "patch_source": PATCH_SOURCE,
            "reason": type(exc).__name__,
        }
        return result

    try:
        from .scan_result_store import write_scan_result

        write_scan_result(root / "platform_outputs" / safe_project / "scan_result.json", result)
    except Exception:
        # Never mask the original scan result just because a post-processing write failed.
        pass
    return result


def _regression_suite_refresh_hook(result: dict[str, Any], *, project: str, root: Path) -> dict[str, Any]:
    return refresh_regression_suite_after_scan(result, project=project, root=root)


def install_regression_suite_refresh_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    from ai_test_asset_center import __main__ as scanner_module
    from ai_test_asset_center.scan_post_hooks import register_scan_post_hook

    if getattr(scanner_module, "_REGRESSION_SUITE_REFRESH_PATCHED", False):
        return

    register_scan_post_hook("regression_suite_refresh", _regression_suite_refresh_hook)
    scanner_module._ORIGINAL_REGRESSION_SUITE_REFRESH_SCAN = None  # type: ignore[attr-defined]
    scanner_module._REGRESSION_SUITE_REFRESH_PATCHED = True  # type: ignore[attr-defined]
    scanner_module._REGRESSION_SUITE_REFRESH_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_regression_suite_refresh_patch() -> None:
    from ai_test_asset_center import __main__ as scanner_module
    from ai_test_asset_center.scan_post_hooks import register_scan_post_hook

    register_scan_post_hook("regression_suite_refresh", None)
    scanner_module._ORIGINAL_REGRESSION_SUITE_REFRESH_SCAN = None  # type: ignore[attr-defined]
    scanner_module._REGRESSION_SUITE_REFRESH_PATCHED = False  # type: ignore[attr-defined]
