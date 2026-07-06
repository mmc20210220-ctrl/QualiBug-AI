"""QualiBug unified, source-grounded enterprise scan entry point."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .enterprise_campaign import has_real_confirmation_receipt
from .enterprise_test_data_plan import build_campaign_test_data_plan


_SOURCE_EXTENSIONS = {".json", ".yaml", ".yml", ".md", ".txt"}
_MAX_SOURCE_BYTES = 5_000_000
_MAX_SOURCE_FILES = 200
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if callable(configure):
            try:
                configure(errors="replace")
            except Exception:
                pass


_configure_console_encoding()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _gap(code: str, detail: str) -> dict[str, str]:
    return {"kind": "SOURCE_INPUT_GAP", "code": code, "detail": detail}


def _safe_project(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return normalized or "unscoped"


def _sha256(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _load_schema_assets(root: Path, project: str) -> str:
    directory = root / "platform_workspace" / _safe_project(project) / "input"
    chunks: list[str] = []
    for path in sorted(directory.glob("*.sql")) if directory.exists() else []:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:1_000_000])
        except OSError:
            continue
    return "\n\n".join(chunks)


def _find_registered_asset(root: Path, project: str, content_hash: str) -> dict[str, str]:
    """Find an exact immutable match in the project-owned asset inventory."""
    project_root = root / "platform_workspace" / _safe_project(project) / "input"
    if not project_root.exists() or not project_root.is_dir():
        return {}
    inspected = 0
    try:
        entries = sorted(project_root.rglob("*"))
    except OSError:
        return {}
    for path in entries:
        if inspected >= _MAX_SOURCE_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _SOURCE_EXTENSIONS:
            continue
        inspected += 1
        try:
            if path.stat().st_size > _MAX_SOURCE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _sha256(content) != content_hash:
            continue
        return {
            "source_id": f"project_asset:{path.relative_to(root).as_posix()}",
            "source_hash": content_hash,
            "source_origin": "registered_project_asset",
        }
    return {}


def _source_manifest(root: Path, project: str, context: dict[str, Any], api_doc_path: str, api_doc_text: str) -> dict[str, str]:
    """Resolve provenance without treating an arbitrary local path as registered input."""
    manifest = _as_dict(context.get("source_manifest"))
    source_id = str(manifest.get("source_id") or "").strip()
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    actual_hash = _sha256(api_doc_text)
    source_origin = "declared_manifest" if source_id or source_hash else ""

    # An uploaded/registered project asset may be recognized by exact content only.
    # A caller-owned external file must carry its own complete source manifest.
    if not source_id and not source_hash:
        registered = _find_registered_asset(root, project, actual_hash)
        source_id = registered.get("source_id", "")
        source_hash = registered.get("source_hash", "")
        source_origin = registered.get("source_origin", "external_path_unregistered" if api_doc_path else "inline_unregistered")

    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "actual_hash": actual_hash,
        "source_origin": source_origin,
    }


def _source_contract(manifest: dict[str, str]) -> list[dict[str, str]]:
    missing = [name for name in ("source_id", "source_hash") if not manifest.get(name)]
    if missing:
        return [_gap(
            "SOURCE_PROVENANCE_MISSING",
            "Every enterprise scan requires a registered project asset or an explicit source_id and immutable SHA-256 source_hash.",
        )]
    if not _SHA256_RE.fullmatch(manifest["source_hash"]):
        return [_gap("SOURCE_HASH_INVALID", "source_hash must be a lowercase SHA-256 digest for the submitted source content.")]
    if manifest["source_hash"] != manifest["actual_hash"]:
        return [_gap("SOURCE_HASH_MISMATCH", "The source_hash does not match submitted source content.")]
    return []


def _runtime_contract(context: dict[str, Any], base_url: str, manifest: dict[str, str]) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    public_manifest = {
        "source_id": manifest.get("source_id", ""),
        "source_hash": manifest.get("source_hash", ""),
        "source_origin": manifest.get("source_origin", ""),
    }
    if not base_url:
        return "", [], {"status": "plan_only", "reason": "runtime_target_missing", "source_manifest": public_manifest}
    missing: list[dict[str, str]] = []
    if not public_manifest["source_id"] or not public_manifest["source_hash"]:
        missing.append(_gap("SOURCE_PROVENANCE_MISSING", "A registered source is required before runtime probing."))
    if not str(context.get("scope_id") or "").strip():
        missing.append(_gap("CAMPAIGN_SCOPE_MISSING", "An explicit campaign scope_id is required before runtime probing."))
    if not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        missing.append(_gap("ENVIRONMENT_REFERENCE_MISSING", "An approved environment_ref is required before runtime probing."))
    test_data = _as_dict(context.get("test_data_contract"))
    if test_data.get("strategy") in {"create_disposable", "approved_fixture_setup"} and test_data.get("write_approved") is not True:
        missing.append(_gap("WRITE_APPROVAL_MISSING", "Write-capable test-data strategies require explicit write approval."))
    if missing:
        return "", missing, {"status": "blocked", "reason": "runtime_contract_missing", "source_manifest": public_manifest}
    return base_url.rstrip("/"), [], {"status": "approved", "reason": "", "source_manifest": public_manifest}


def _source_catalog(api_doc: str) -> str:
    labels: set[str] = set()
    for line in str(api_doc or "").splitlines():
        match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s|`]+)", line, re.I)
        if match:
            parts = [part for part in match.group(1).strip("/").split("/") if part and not part.startswith("{") and part.lower() not in {"api", "v1", "v2", "v3"}]
            if parts:
                labels.add(parts[0])
    return "\n".join(f"# Source asset: {item}" for item in sorted(labels))


def _classify_findings(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    confirmed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for value in items if isinstance(items, list) else []:
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if has_real_confirmation_receipt(row):
            row["confirmation_status"] = "confirmed"
            confirmed.append(row)
        else:
            row.setdefault("execution_status", "not_executed")
            row["confirmation_status"] = str(row.get("confirmation_status") or "candidate")
            candidates.append(row)
    return confirmed, candidates


def _blocked_result(project: str, root: Path, started: float, gaps: list[dict[str, str]], runtime_contract: dict[str, Any], context: dict[str, Any], save_report: bool, output_dir: Optional[Path]) -> dict[str, Any]:
    manifest = _as_dict(runtime_contract.get("source_manifest"))
    first_code = str(_as_dict(gaps[0]).get("code") or "SOURCE_CONTRACT_BLOCKED") if gaps else "SOURCE_CONTRACT_BLOCKED"
    campaign = {
        "campaign_id": "",
        "campaign_status": "blocked",
        "scope_id": str(context.get("scope_id") or ""),
        "environment_ref": str(context.get("environment_ref") or context.get("target_environment") or ""),
        "source_id": str(manifest.get("source_id") or ""),
        "source_hash": str(manifest.get("source_hash") or ""),
        "source_origin": str(manifest.get("source_origin") or ""),
        "confirmed_slice_count": 0,
        "coverage_deferred_reason": first_code.lower(),
        "next_campaign_reason": "supply_registered_immutable_source" if first_code == "SOURCE_PROVENANCE_MISSING" else "correct_source_manifest_or_runtime_contract",
    }
    test_data_plan = build_campaign_test_data_plan(campaign, [], _as_dict(context.get("test_data_contract")))
    result: dict[str, Any] = {
        "success": True,
        "scan_id": f"scan_{_safe_project(project)}_{int(started * 1000)}",
        "project": project,
        "grade": "blocked",
        "score": 0.0,
        "coverage": 0.0,
        "total_findings": 0,
        "total_candidates": 0,
        "total_ms": int((time.time() - started) * 1000),
        "layers": {
            "source_grounded_discovery": {"tool": "blocked", "findings": 0, "candidates": 0, "ms": 0, "execution_status": "blocked"},
            "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required"},
        },
        "findings": [],
        "candidate_findings": [],
        "db_findings": [],
        "e2e_findings": [],
        "ui_findings": [],
        "deep_findings": [],
        "spectrum": {},
        "input_gaps": gaps,
        "coverage_gaps": gaps + list(test_data_plan.get("coverage_gaps") or []),
        "runtime_contract": runtime_contract,
        "test_data_plan": test_data_plan,
        "campaign": campaign,
        "behavior_slice_ledger": {"stop_reason": first_code.lower(), "selected_slice_ids": [], "confirmed_slice_ids": []},
        "incremental_discovery": {"status": "blocked", "stop_reason": first_code.lower()},
        "execution_status": "blocked",
        "db_verification": {"status": "blocked", "reason": first_code.lower(), "findings": []},
        "ci_gate": {"status": "not_evaluated", "reason": first_code.lower()},
        "auto_har": {"status": "no_traffic"},
        "v12": {},
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {
            "project": project,
            "real_findings": [],
            "risk_clues": [],
            "campaign": campaign,
            "coverage_gaps": result["coverage_gaps"],
            "runtime_contract": runtime_contract,
            "test_data_plan": test_data_plan,
            "execution_status": "blocked",
        })
        result["report_path"] = str(report_path)
    _write_json(root / "platform_outputs" / _safe_project(project) / "scan_result.json", result)
    return result


def scan(
    project: str,
    root: Optional[Path] = None,
    *,
    prd_text: str = "",
    api_doc_path: str = "",
    api_doc_text: str = "",
    base_url: str = "",
    ci_gate: bool = False,
    multi_layer: bool = True,
    output_dir: Optional[Path] = None,
    save_report: bool = True,
    campaign_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run the single enterprise-safe discovery and evidence pipeline."""
    root = Path(root or Path.cwd())
    project = str(project or "").strip()
    if not project:
        return {"success": False, "error": "project is required"}
    if api_doc_path and not api_doc_text:
        try:
            api_doc_text = Path(api_doc_path).read_text(encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"api_doc_path is unreadable: {exc}"}
    if not str(api_doc_text or "").strip():
        return {"success": False, "error": "api_doc_text or api_doc_path is required"}

    started = time.time()
    context = dict(campaign_context or {})
    manifest = _source_manifest(root, project, context, api_doc_path, api_doc_text)
    context["source_manifest"] = {
        "source_id": manifest["source_id"],
        "source_hash": manifest["source_hash"],
        "source_origin": manifest["source_origin"],
    }
    provenance_gaps = _source_contract(manifest)
    approved_base_url, runtime_gaps, runtime_contract = _runtime_contract(context, base_url, manifest)
    if provenance_gaps:
        return _blocked_result(project, root, started, provenance_gaps + runtime_gaps, runtime_contract, context, save_report, output_dir)

    input_gaps: list[dict[str, str]] = []
    if not str(prd_text or "").strip():
        input_gaps.append(_gap("PRD_SOURCE_MISSING", "No requirement source was supplied; only API/schema facts can be planned."))
        prd_text = _source_catalog(api_doc_text)
    schema_text = _load_schema_assets(root, project)
    if not schema_text:
        input_gaps.append(_gap("DATABASE_SCHEMA_MISSING", "No project-scoped schema asset is available for data observation planning."))
    input_gaps.extend(runtime_gaps)

    diagnostics: dict[str, Any] = {"ready": True, "checks": []}
    try:
        from .scan_diagnostics import run_preflight
        diagnostics = run_preflight({}, api_doc_text)
    except Exception as exc:
        diagnostics = {"ready": False, "checks": [], "summary": f"preflight_unavailable:{type(exc).__name__}"}

    try:
        from .v12_pipeline import run_v12_pipeline
        v12 = run_v12_pipeline(
            project=project,
            root=root,
            prd_text=prd_text,
            api_spec_text=api_doc_text,
            db_schema_text=schema_text,
            base_url=approved_base_url,
            campaign_context=context,
        )
    except Exception as exc:
        return {"success": False, "error": f"v12_pipeline_failed:{type(exc).__name__}:{exc}"}

    confirmed, candidates = _classify_findings(v12.get("findings"))
    phases = _as_dict(v12.get("phases"))
    state_graph = _as_dict(phases.get("state_graph"))
    execution = _as_dict(phases.get("execution"))
    graph_gaps = state_graph.get("coverage_gaps", []) if isinstance(state_graph.get("coverage_gaps"), list) else []
    campaign = _as_dict(v12.get("campaign"))
    incremental = _as_dict(phases.get("incremental_discovery"))
    test_data_plan = build_campaign_test_data_plan(
        campaign,
        incremental.get("selected_slices") if isinstance(incremental.get("selected_slices"), list) else [],
        _as_dict(context.get("test_data_contract")),
    )
    coverage_gaps = input_gaps + [item for item in graph_gaps if isinstance(item, dict)] + list(test_data_plan.get("coverage_gaps") or [])
    execution_status = str(execution.get("status") or "not_executed")
    duration_ms = int((time.time() - started) * 1000)
    result: dict[str, Any] = {
        "success": True,
        "scan_id": f"scan_{_safe_project(project)}_{int(started * 1000)}",
        "project": project,
        "grade": "inconclusive" if not confirmed else "evidence_ready",
        "score": 0.0,
        "coverage": 0.0,
        "total_findings": len(confirmed),
        "total_candidates": len(candidates),
        "total_ms": duration_ms,
        "layers": {
            "source_grounded_discovery": {
                "tool": "V12 enterprise campaign",
                "findings": len(confirmed),
                "candidates": len(candidates),
                "ms": int(v12.get("total_duration_ms") or duration_ms),
                "execution_status": execution_status,
                "campaign_id": campaign.get("campaign_id", ""),
            },
            "legacy_domain_layers": {
                "tool": "disabled",
                "findings": 0,
                "candidates": 0,
                "ms": 0,
                "reason": "source_bound_scope_fixture_actor_cleanup_contract_required" if multi_layer else "not_requested",
            },
        },
        "findings": confirmed,
        "candidate_findings": candidates,
        "db_findings": [],
        "e2e_findings": [],
        "ui_findings": [],
        "deep_findings": [],
        "spectrum": {},
        "preflight_diagnostics": diagnostics,
        "input_gaps": input_gaps,
        "coverage_gaps": coverage_gaps,
        "runtime_contract": runtime_contract,
        "test_data_plan": test_data_plan,
        "campaign": campaign,
        "behavior_slice_ledger": v12.get("behavior_slice_ledger", {}),
        "incremental_discovery": incremental,
        "execution_status": execution_status,
        "db_verification": {
            "status": "plan_only" if schema_text else "blocked",
            "reason": "source_bound_observation_contract_required" if schema_text else "database_schema_source_missing",
            "findings": [],
        },
        "ci_gate": {
            "status": "not_evaluated" if ci_gate else "not_requested",
            "reason": "confirmed_receipts_and_approved_baseline_required" if ci_gate else "",
        },
        "auto_har": v12.get("auto_har", {}),
        "v12": v12,
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {
            "project": project,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "real_findings": confirmed,
            "risk_clues": candidates,
            "campaign": campaign,
            "coverage_gaps": coverage_gaps,
            "runtime_contract": runtime_contract,
            "test_data_plan": test_data_plan,
            "behavior_slice_ledger": result["behavior_slice_ledger"],
            "execution_status": execution_status,
        })
        result["report_path"] = str(report_path)
    _write_json(root / "platform_outputs" / _safe_project(project) / "scan_result.json", result)
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="QualiBug enterprise source-grounded scanner")
    parser.add_argument("scan", nargs="?", default="scan")
    parser.add_argument("--project", required=True)
    parser.add_argument("--api-doc")
    parser.add_argument("--api-doc-text")
    parser.add_argument("--prd", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--scope-id", default="")
    parser.add_argument("--environment-ref", default="")
    parser.add_argument("--source-id", default="")
    parser.add_argument("--source-hash", default="")
    parser.add_argument("--test-data-strategy", default="blocked_with_testability_gap")
    parser.add_argument("--ci-gate", action="store_true")
    parser.add_argument("--no-multi-layer", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    context = {
        "scope_id": args.scope_id,
        "environment_ref": args.environment_ref,
        "source_manifest": {"source_id": args.source_id, "source_hash": args.source_hash},
        "test_data_contract": {"strategy": args.test_data_strategy},
    }
    result = scan(
        project=args.project,
        api_doc_path=args.api_doc or "",
        api_doc_text=args.api_doc_text or "",
        prd_text=args.prd,
        base_url=args.base_url,
        ci_gate=args.ci_gate,
        multi_layer=not args.no_multi_layer,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        save_report=not args.no_report,
        campaign_context=context,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result.get("success"):
        campaign = result.get("campaign", {})
        print(f"QualiBug scan: {result['project']}")
        print(f"Confirmed: {result['total_findings']} | Candidates: {result['total_candidates']} | Execution: {result['execution_status']}")
        print(f"Campaign: {campaign.get('campaign_id', 'n/a')} ({campaign.get('campaign_status', 'n/a')})")
    else:
        print(f"Error: {result.get('error', 'scan failed')}", file=sys.stderr)
    raise SystemExit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
