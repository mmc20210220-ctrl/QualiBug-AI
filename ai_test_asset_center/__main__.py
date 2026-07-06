"""QualiBug unified source-grounded scan entry point.

The public ``scan`` API routes discovery through V12 behavior slices and
enterprise Campaign governance. No caller can execute a target unless it
supplies an explicit source, scope and environment contract. Results without a
complete delivery receipt remain candidates or coverage gaps.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .enterprise_campaign import has_real_confirmation_receipt


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


_configure_console_encoding()


def _input_gap(code: str, detail: str) -> dict[str, str]:
    return {"kind": "SOURCE_INPUT_GAP", "code": code, "detail": detail}


def _safe_project(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return result or "unscoped"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_schema_assets(root: Path, project: str) -> str:
    input_dir = root / "platform_workspace" / _safe_project(project) / "input"
    blocks: list[str] = []
    for path in sorted(input_dir.glob("*.sql")) if input_dir.exists() else []:
        try:
            blocks.append(path.read_text(encoding="utf-8", errors="replace")[:1_000_000])
        except OSError:
            continue
    return "\n\n".join(blocks)


def _source_catalog(api_doc: str) -> str:
    """Produce source labels only; never infer rules from a route name."""
    labels: set[str] = set()
    for line in str(api_doc or "").splitlines():
        match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s|`]+)", line, re.I)
        if not match:
            continue
        parts = [part for part in match.group(1).strip("/").split("/") if part and not part.startswith("{") and part.lower() not in {"api", "v1", "v2", "v3"}]
        if parts:
            labels.add(parts[0])
    return "\n".join(f"# Source asset: {item}" for item in sorted(labels))


def _source_manifest(context: dict[str, Any], api_doc_path: str, api_doc_text: str) -> dict[str, str]:
    manifest = _as_dict(context.get("source_manifest"))
    source_id = str(manifest.get("source_id") or "").strip()
    source_hash = str(manifest.get("source_hash") or "").strip()
    if api_doc_path and not source_id:
        source_id = Path(api_doc_path).name
    if api_doc_path and not source_hash:
        source_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
    return {"source_id": source_id[:160], "source_hash": source_hash[:128]}


def _runtime_contract(context: dict[str, Any], base_url: str, api_doc_path: str, api_doc_text: str) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    """Require explicit enterprise authorization before any target call."""
    if not base_url:
        return "", [], {"status": "plan_only", "reason": "runtime_target_missing"}
    manifest = _source_manifest(context, api_doc_path, api_doc_text)
    gaps: list[dict[str, str]] = []
    if not manifest["source_id"] or not manifest["source_hash"]:
        gaps.append(_input_gap("SOURCE_PROVENANCE_MISSING", "A source_id and immutable source_hash are required before runtime probing."))
    if not str(context.get("scope_id") or "").strip():
        gaps.append(_input_gap("CAMPAIGN_SCOPE_MISSING", "An explicit campaign scope_id is required before runtime probing."))
    if not str(context.get("environment_ref") or context.get("target_environment") or "").strip():
        gaps.append(_input_gap("ENVIRONMENT_REFERENCE_MISSING", "An approved environment_ref is required before runtime probing."))
    if gaps:
        return "", gaps, {"status": "blocked", "reason": "runtime_contract_missing", "source_manifest": manifest}
    return base_url.rstrip("/"), [], {"status": "approved", "source_manifest": manifest}


def _classify_v12_findings(items: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    confirmed: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        finding = dict(raw)
        if has_real_confirmation_receipt(finding):
            finding["confirmation_status"] = "confirmed"
            confirmed.append(finding)
        else:
            finding.setdefault("execution_status", "not_executed")
            finding["confirmation_status"] = str(finding.get("confirmation_status") or "candidate")
            candidates.append(finding)
    return confirmed, candidates


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


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
    """Run the single enterprise-safe discovery and evidence pipeline.

    ``multi_layer`` is retained only for response compatibility. Fixed-domain
    legacy probes are never invoked; unsupported work becomes a testability gap.
    """
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
    input_gaps: list[dict[str, str]] = []
    if not str(prd_text or "").strip():
        input_gaps.append(_input_gap("PRD_SOURCE_MISSING", "No requirement source was supplied; only API/schema facts can be planned."))
        prd_text = _source_catalog(api_doc_text)
    schema_text = _load_schema_assets(root, project)
    if not schema_text:
        input_gaps.append(_input_gap("DATABASE_SCHEMA_MISSING", "No project-scoped schema asset is available for data observation planning."))
    approved_base_url, runtime_gaps, runtime_contract = _runtime_contract(context, base_url, api_doc_path, api_doc_text)
    input_gaps.extend(runtime_gaps)
    if runtime_contract.get("source_manifest"):
        context["source_manifest"] = runtime_contract["source_manifest"]

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

    confirmed, candidates = _classify_v12_findings(v12.get("findings"))
    phases = _as_dict(v12.get("phases"))
    state_graph = _as_dict(phases.get("state_graph"))
    execution = _as_dict(phases.get("execution"))
    graph_gaps = state_graph.get("coverage_gaps", []) if isinstance(state_graph.get("coverage_gaps"), list) else []
    coverage_gaps = input_gaps + [item for item in graph_gaps if isinstance(item, dict)]
    campaign = _as_dict(v12.get("campaign"))
    execution_status = str(execution.get("status") or "not_executed")
    duration_ms = int((time.time() - started) * 1000)
    layers: dict[str, Any] = {
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
    }
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
        "layers": layers,
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
        "campaign": campaign,
        "behavior_slice_ledger": v12.get("behavior_slice_ledger", {}),
        "incremental_discovery": phases.get("incremental_discovery", {}),
        "execution_status": execution_status,
        "db_verification": {"status": "plan_only" if schema_text else "blocked", "reason": "source_bound_observation_contract_required" if schema_text else "database_schema_source_missing", "findings": []},
        "ci_gate": {"status": "not_evaluated" if ci_gate else "not_requested", "reason": "confirmed_receipts_and_approved_baseline_required" if ci_gate else ""},
        "auto_har": v12.get("auto_har", {}),
        "v12": v12,
    }
    if save_report:
        report_root = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = report_root / "intelligence_report.json"
        _write_json(report_path, {
            "project": project,
            "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "real_findings": confirmed,
            "risk_clues": candidates,
            "campaign": campaign,
            "coverage_gaps": coverage_gaps,
            "runtime_contract": runtime_contract,
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
    }
    result = scan(project=args.project, api_doc_path=args.api_doc or "", api_doc_text=args.api_doc_text or "", prd_text=args.prd, base_url=args.base_url, ci_gate=args.ci_gate, multi_layer=not args.no_multi_layer, output_dir=Path(args.output_dir) if args.output_dir else None, save_report=not args.no_report, campaign_context=context)
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
