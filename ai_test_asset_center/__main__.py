"""QualiBug unified, source-grounded enterprise scan entry point.

A scan may only be driven by an immutable, attributable source asset. Sources
are resolved from the enterprise source registry first, then from a project-owned
asset mirror, or from an explicitly supplied SHA-256 manifest. Any confirmed
finding must also have a persisted, integrity-verifiable evidence bundle.
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


def _load_schema_assets(root: Path, project: str) -> str:
    directory = root / "platform_workspace" / _safe_project(project) / "input"
    chunks: list[str] = []
    for path in sorted(directory.glob("*.sql")) if directory.exists() else []:
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:1_000_000])
        except OSError:
            continue
    return "\n\n".join(chunks)


def _registry_manifest(root: Path, project: str, api_doc_text: str) -> dict[str, str]:
    try:
        from .enterprise_source_registry import SourceRegistryError, resolve_source_manifest
        manifest = resolve_source_manifest(project, api_doc_text, root=root)
    except (ImportError, OSError, ValueError):
        return {}
    except SourceRegistryError:
        return {}
    if not isinstance(manifest, dict) or not str(manifest.get("source_id") or "").strip() or not str(manifest.get("source_hash") or "").strip():
        return {}
    return {
        "source_id": str(manifest.get("source_id") or "")[:160],
        "source_hash": str(manifest.get("source_hash") or "")[:128],
        "source_version_id": str(manifest.get("source_version_id") or "")[:80],
        "source_origin": str(manifest.get("source_origin") or "registered_source_registry")[:80],
    }


def _load_registered_source(project: str, root: Path, context: dict[str, Any]) -> str:
    manifest = _as_dict(context.get("source_manifest"))
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    if not _SHA256_RE.fullmatch(source_hash):
        return ""
    try:
        from .enterprise_source_registry import SourceRegistryError, load_source_content
        return load_source_content(project, source_hash, root=root)
    except (ImportError, OSError, ValueError, SourceRegistryError):
        return ""


def _find_project_asset(root: Path, project: str, content_hash: str) -> dict[str, str]:
    """Migration resolver for an exact project-owned input asset."""
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
            "source_version_id": f"legacy_{content_hash[:24]}",
            "source_origin": "registered_project_asset",
        }
    return {}


def _source_manifest(root: Path, project: str, context: dict[str, Any], api_doc_path: str, api_doc_text: str) -> dict[str, str]:
    declared = _as_dict(context.get("source_manifest"))
    source_id = str(declared.get("source_id") or "").strip()
    source_hash = str(declared.get("source_hash") or "").strip().lower().removeprefix("sha256:").strip()
    source_version_id = str(declared.get("source_version_id") or "").strip()
    actual_hash = _sha256(api_doc_text)
    source_origin = str(declared.get("source_origin") or "").strip()
    if source_id or source_hash:
        source_origin = source_origin or "declared_manifest"
    else:
        registered = _registry_manifest(root, project, api_doc_text) or _find_project_asset(root, project, actual_hash)
        source_id = registered.get("source_id", "")
        source_hash = registered.get("source_hash", "")
        source_version_id = registered.get("source_version_id", "")
        source_origin = registered.get("source_origin", "external_path_unregistered" if api_doc_path else "inline_unregistered")
    return {
        "source_id": source_id[:160],
        "source_hash": source_hash[:128],
        "source_version_id": source_version_id[:80],
        "actual_hash": actual_hash,
        "source_origin": source_origin[:80],
    }


def _source_contract(manifest: dict[str, str]) -> list[dict[str, str]]:
    if not manifest.get("source_id") or not manifest.get("source_hash"):
        return [_gap("SOURCE_PROVENANCE_MISSING", "Every enterprise scan requires a registered project asset or an explicit source_id and immutable SHA-256 source_hash.")]
    if not _SHA256_RE.fullmatch(manifest["source_hash"]):
        return [_gap("SOURCE_HASH_INVALID", "source_hash must be a lowercase SHA-256 digest for the submitted source content.")]
    if manifest["source_hash"] != manifest["actual_hash"]:
        return [_gap("SOURCE_HASH_MISMATCH", "The source_hash does not match submitted source content.")]
    return []


def _runtime_contract(context: dict[str, Any], base_url: str, manifest: dict[str, str]) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    public_manifest = {
        "source_id": manifest.get("source_id", ""),
        "source_hash": manifest.get("source_hash", ""),
        "source_version_id": manifest.get("source_version_id", ""),
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


def _scan_preflight_guide(
    *,
    context: dict[str, Any],
    base_url: str,
    manifest: dict[str, str],
    runtime_contract: dict[str, Any],
    test_data_plan: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    runtime_observed: bool = False,
) -> dict[str, Any]:
    test_data = _as_dict(context.get("test_data_contract"))
    checks = [
        {
            "key": "source_manifest",
            "label": "immutable_source_manifest",
            "status": "ready" if manifest.get("source_id") and manifest.get("source_hash") else "missing",
            "required": True,
            "detail": manifest.get("source_id") or "register customer materials before scanning",
        },
        {
            "key": "target_base_url",
            "label": "target_environment_url",
            "status": "configured_unverified" if base_url else "missing",
            "required": bool(base_url),
            "detail": base_url or "plan_only_scan_has_no_runtime_target",
        },
        {
            "key": "scope_id",
            "label": "approved_scope",
            "status": "ready" if str(context.get("scope_id") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(context.get("scope_id") or ""),
        },
        {
            "key": "environment_ref",
            "label": "environment_reference",
            "status": "ready" if str(context.get("environment_ref") or context.get("target_environment") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(context.get("environment_ref") or context.get("target_environment") or ""),
        },
        {
            "key": "test_data_strategy",
            "label": "test_data_strategy",
            "status": "ready" if str(test_data.get("strategy") or "").strip() else "missing",
            "required": bool(base_url),
            "detail": str(test_data.get("strategy") or ""),
        },
        {
            "key": "execution_approval",
            "label": "readonly_execution_approval",
            "status": "ready" if str(context.get("execution_approval_id") or "").strip() else ("not_required" if not base_url else "missing"),
            "required": bool(base_url),
            "detail": str(context.get("execution_approval_id") or ""),
        },
        {
            "key": "actor_credentials",
            "label": "test_actor_or_role_credentials",
            "status": "configured_unverified" if _as_dict(context.get("actor_contract") or context.get("test_actor_contract")) else "not_configured",
            "required": False,
            "detail": "configured actors still require runtime login or token evidence",
        },
        {
            "key": "url_reachability",
            "label": "url_reachability",
            "status": "ready" if runtime_observed else ("not_checked" if not diagnostics else ("ready" if diagnostics.get("ready") else "failed")),
            "required": bool(base_url),
            "detail": "runtime_traffic_captured" if runtime_observed else str((diagnostics or {}).get("summary") or "no runtime health check was executed"),
        },
    ]
    if test_data_plan:
        checks.append({
            "key": "test_data_contract",
            "label": "test_data_contract",
            "status": str(test_data_plan.get("status") or "missing"),
            "required": bool(base_url),
            "detail": ",".join(str(item) for item in test_data_plan.get("missing_requirements", []) or []),
        })
    missing = [item["key"] for item in checks if item.get("required") and item.get("status") in {"missing", "failed", "blocked_with_testability_gap"}]
    runtime_status = str(runtime_contract.get("status") or "")
    return {
        "status": "ready" if not missing and runtime_status == "approved" else ("plan_only" if not base_url else "blocked"),
        "runtime_contract_status": runtime_status,
        "missing": missing,
        "checks": checks,
        "healthy_claim_allowed": not missing and runtime_status == "approved",
    }


def _source_catalog(api_doc: str) -> str:
    labels: set[str] = set()
    for line in str(api_doc or "").splitlines():
        match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s|`]+)", line, re.I)
        if not match:
            continue
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


def _test_data_receipt_verifier(root: Path, project: str):
    def verify(kind: str, receipt_id: str, campaign_id: str, scope_id: str, environment_ref: str) -> bool:
        try:
            from .enterprise_test_data_receipts import verify_test_data_receipt
            verdict = verify_test_data_receipt(project, receipt_id, root=root, kind=kind, campaign_id=campaign_id, scope_id=scope_id, environment_ref=environment_ref)
            return bool(verdict.get("valid"))
        except Exception:
            return False
    return verify


def _persist_execution_evidence(project: str, root: Path, scan_id: str, campaign: dict[str, Any], runtime_contract: dict[str, Any], execution_status: str, v12: dict[str, Any]) -> dict[str, Any]:
    from .evidence_artifact_store import persist_evidence_bundle
    return persist_evidence_bundle(
        project,
        root=root,
        run_id=scan_id,
        campaign=campaign,
        runtime_contract=runtime_contract,
        execution_status=execution_status,
        auto_har=_as_dict(v12.get("auto_har")),
        evidence_graphs=v12.get("evidence_graphs") if isinstance(v12.get("evidence_graphs"), list) else [],
        findings=v12.get("findings") if isinstance(v12.get("findings"), list) else [],
    )


def _evaluate_release_gate(*, project: str, root: Path, campaign: dict[str, Any], execution_status: str, runtime_contract: dict[str, Any], evidence_bundle: dict[str, Any], test_data_plan: dict[str, Any], findings: list[dict[str, Any]], coverage_gaps: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    from .release_gate import evaluate_release_gate
    gate_policy = {"campaign_not_closed_verdict": "not_ready"}
    gate_policy.update(_as_dict(policy))
    verification: dict[str, Any] = {}
    if str(evidence_bundle.get("status") or "") == "persisted" and str(evidence_bundle.get("bundle_id") or ""):
        try:
            from .evidence_artifact_store import verify_evidence_bundle
            verification = verify_evidence_bundle(project, str(evidence_bundle["bundle_id"]), root=root)
        except Exception as exc:
            verification = {"valid": False, "code": f"EVIDENCE_BUNDLE_VERIFICATION_ERROR:{type(exc).__name__}"}
    return evaluate_release_gate(
        campaign=campaign,
        execution_status=execution_status,
        runtime_contract=runtime_contract,
        evidence_bundle=evidence_bundle,
        evidence_bundle_verification=verification,
        test_data_plan=test_data_plan,
        findings=findings,
        coverage_gaps=coverage_gaps,
        policy=gate_policy,
    )


def _blocked_result(project: str, root: Path, started: float, gaps: list[dict[str, str]], runtime_contract: dict[str, Any], context: dict[str, Any], save_report: bool, output_dir: Optional[Path]) -> dict[str, Any]:
    manifest = _as_dict(runtime_contract.get("source_manifest"))
    first_code = str(_as_dict(gaps[0]).get("code") or "SOURCE_CONTRACT_BLOCKED") if gaps else "SOURCE_CONTRACT_BLOCKED"
    campaign = {
        "campaign_id": "", "campaign_status": "blocked", "scope_id": str(context.get("scope_id") or ""),
        "environment_ref": str(context.get("environment_ref") or context.get("target_environment") or ""),
        "source_id": str(manifest.get("source_id") or ""), "source_hash": str(manifest.get("source_hash") or ""),
        "source_version_id": str(manifest.get("source_version_id") or ""), "source_origin": str(manifest.get("source_origin") or ""),
        "confirmed_slice_count": 0, "coverage_deferred_reason": first_code.lower(),
        "next_campaign_reason": "supply_registered_immutable_source" if first_code == "SOURCE_PROVENANCE_MISSING" else "correct_source_manifest_or_runtime_contract",
    }
    test_data_plan = build_campaign_test_data_plan(campaign, [], _as_dict(context.get("test_data_contract")), receipt_verifier=_test_data_receipt_verifier(root, project))
    coverage_gaps = gaps + list(test_data_plan.get("coverage_gaps") or [])
    evidence_bundle = {"status": "not_created", "reason": "scan_blocked"}
    release_gate = _evaluate_release_gate(project=project, root=root, campaign=campaign, execution_status="blocked", runtime_contract=runtime_contract, evidence_bundle=evidence_bundle, test_data_plan=test_data_plan, findings=[], coverage_gaps=coverage_gaps, policy=_as_dict(context.get("release_policy")))
    if first_code in {"SOURCE_PROVENANCE_MISSING", "SOURCE_HASH_INVALID", "SOURCE_HASH_MISMATCH"}:
        release_gate = {**release_gate, "verdict": "fail", "status": "blocked"}
    preflight_guide = _scan_preflight_guide(context=context, base_url="", manifest={**manifest, "actual_hash": manifest.get("source_hash", "")}, runtime_contract=runtime_contract, test_data_plan=test_data_plan)
    result: dict[str, Any] = {
        "success": True, "scan_id": f"scan_{_safe_project(project)}_{int(started * 1000)}", "project": project,
        "grade": "blocked", "score": 0.0, "coverage": 0.0, "total_findings": 0, "total_candidates": 0,
        "total_ms": int((time.time() - started) * 1000),
        "layers": {"source_grounded_discovery": {"tool": "blocked", "findings": 0, "candidates": 0, "ms": 0, "execution_status": "blocked"}, "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required"}},
        "findings": [], "candidate_findings": [], "db_findings": [], "e2e_findings": [], "ui_findings": [], "deep_findings": [], "spectrum": {},
        "input_gaps": gaps, "coverage_gaps": coverage_gaps, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign,
        "behavior_slice_ledger": {"stop_reason": first_code.lower(), "selected_slice_ids": [], "confirmed_slice_ids": []},
        "incremental_discovery": {"status": "blocked", "stop_reason": first_code.lower()}, "execution_status": "blocked",
        "db_verification": {"status": "blocked", "reason": first_code.lower(), "findings": []},
        "ci_gate": {"status": "not_evaluated", "reason": first_code.lower()}, "auto_har": {"status": "no_traffic"},
        "evidence_bundle": evidence_bundle, "release_gate": release_gate, "scan_preflight_guide": preflight_guide, "v12": {},
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {"project": project, "real_findings": [], "risk_clues": [], "campaign": campaign, "coverage_gaps": coverage_gaps, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "execution_status": "blocked", "evidence_bundle": evidence_bundle, "release_gate": release_gate})
        result["report_path"] = str(report_path)
    _write_json(root / "platform_outputs" / _safe_project(project) / "scan_result.json", result)
    return result


def scan(project: str, root: Optional[Path] = None, *, prd_text: str = "", api_doc_path: str = "", api_doc_text: str = "", base_url: str = "", ci_gate: bool = False, multi_layer: bool = True, output_dir: Optional[Path] = None, save_report: bool = True, campaign_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run the single enterprise-safe discovery and evidence pipeline."""
    root = Path(root or Path.cwd())
    project = str(project or "").strip()
    if not project:
        return {"success": False, "error": "project is required"}
    context = dict(campaign_context or {})
    if api_doc_path and not api_doc_text:
        try:
            api_doc_text = Path(api_doc_path).read_text(encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"api_doc_path is unreadable: {exc}"}
    if not str(api_doc_text or "").strip():
        api_doc_text = _load_registered_source(project, root, context)
    if not str(api_doc_text or "").strip():
        return {"success": False, "error": "api_doc_text, api_doc_path, or a registered source_manifest is required"}

    started = time.time()
    manifest = _source_manifest(root, project, context, api_doc_path, api_doc_text)
    context["source_manifest"] = {"source_id": manifest["source_id"], "source_hash": manifest["source_hash"], "source_version_id": manifest["source_version_id"], "source_origin": manifest["source_origin"]}
    provenance_gaps = _source_contract(manifest)
    approved_base_url, runtime_gaps, initial_runtime_contract = _runtime_contract(context, base_url, manifest)
    if provenance_gaps:
        return _blocked_result(project, root, started, provenance_gaps + runtime_gaps, initial_runtime_contract, context, save_report, output_dir)

    input_gaps: list[dict[str, str]] = []
    if not str(prd_text or "").strip():
        input_gaps.append(_gap("PRD_SOURCE_MISSING", "No requirement source was supplied; only API/schema facts can be planned."))
        prd_text = _source_catalog(api_doc_text)
    schema_text = _load_schema_assets(root, project)
    if not schema_text:
        input_gaps.append(_gap("DATABASE_SCHEMA_MISSING", "No project-scoped schema asset is available for data observation planning."))
    input_gaps.extend(runtime_gaps)

    diagnostics: dict[str, Any] = {"ready": True, "checks": []}
    diagnostics_config: dict[str, Any] = {}
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
        profile = registry.get("test_profile") if isinstance(registry, dict) else {}
        if isinstance(profile, dict):
            diagnostics_config = dict(profile)
    except Exception:
        diagnostics_config = {}
    try:
        from .scan_diagnostics import run_preflight

        if base_url and not diagnostics_config.get("api_base_url"):
            diagnostics_config["api_base_url"] = base_url
        diagnostics = run_preflight(diagnostics_config, api_doc_text)
    except Exception as exc:
        diagnostics = {"ready": False, "checks": [], "summary": f"preflight_unavailable:{type(exc).__name__}"}

    try:
        from .v12_pipeline import run_v12_pipeline
        v12 = run_v12_pipeline(project=project, root=root, prd_text=prd_text, api_spec_text=api_doc_text, db_schema_text=schema_text, base_url=approved_base_url, campaign_context=context)
    except Exception as exc:
        return {"success": False, "error": f"v12_pipeline_failed:{type(exc).__name__}:{exc}"}

    runtime_contract = _as_dict(v12.get("runtime_contract")) or initial_runtime_contract
    confirmed, candidates = _classify_findings(v12.get("findings"))
    phases = _as_dict(v12.get("phases"))
    state_graph = _as_dict(phases.get("state_graph"))
    execution = _as_dict(phases.get("execution"))
    campaign = _as_dict(v12.get("campaign"))
    incremental = _as_dict(phases.get("incremental_discovery"))
    execution_status = str(execution.get("status") or "not_executed")
    scan_id = f"scan_{_safe_project(project)}_{int(started * 1000)}"
    try:
        evidence_bundle = _persist_execution_evidence(project, root, scan_id, campaign, runtime_contract, execution_status, v12)
    except Exception as exc:
        evidence_bundle = {"status": "persistence_failed", "reason": type(exc).__name__}
        if confirmed:
            for item in confirmed:
                item["confirmation_status"] = "inconclusive"
                item["evidence_persistence_status"] = "failed"
            candidates.extend(confirmed)
            confirmed = []
        input_gaps.append(_gap("EVIDENCE_BUNDLE_PERSISTENCE_FAILED", "Runtime evidence could not be persisted with integrity guarantees; customer-deliverable confirmation is blocked."))

    if str(runtime_contract.get("status") or "") == "blocked":
        requirements = runtime_contract.get("missing_requirements") if isinstance(runtime_contract.get("missing_requirements"), list) else []
        for code in requirements:
            if not any(gap.get("code") == str(code) for gap in input_gaps):
                input_gaps.append(_gap(str(code), "Runtime execution approval or contract requirement is not satisfied."))
    graph_gaps = state_graph.get("coverage_gaps", []) if isinstance(state_graph.get("coverage_gaps"), list) else []
    test_data_plan = build_campaign_test_data_plan(campaign, incremental.get("selected_slices") if isinstance(incremental.get("selected_slices"), list) else [], _as_dict(context.get("test_data_contract")), receipt_verifier=_test_data_receipt_verifier(root, project))
    coverage_gaps = input_gaps + [item for item in graph_gaps if isinstance(item, dict)] + list(test_data_plan.get("coverage_gaps") or [])
    release_gate = _evaluate_release_gate(project=project, root=root, campaign=campaign, execution_status=execution_status, runtime_contract=runtime_contract, evidence_bundle=evidence_bundle, test_data_plan=test_data_plan, findings=confirmed, coverage_gaps=coverage_gaps, policy=_as_dict(context.get("release_policy")))
    preflight_guide = _scan_preflight_guide(
        context=context,
        base_url=base_url,
        manifest=manifest,
        runtime_contract=runtime_contract,
        test_data_plan=test_data_plan,
        diagnostics=diagnostics,
        runtime_observed=str(_as_dict(v12.get("auto_har")).get("status") or "") == "captured",
    )
    grade = "blocked" if str(runtime_contract.get("status") or "") == "blocked" or execution_status == "blocked" else ("inconclusive" if not confirmed else "evidence_ready")
    duration_ms = int((time.time() - started) * 1000)
    result: dict[str, Any] = {
        "success": True, "scan_id": scan_id, "project": project, "grade": grade, "score": 0.0, "coverage": 0.0,
        "total_findings": len(confirmed), "total_candidates": len(candidates), "total_ms": duration_ms,
        "layers": {
            "source_grounded_discovery": {"tool": "V12 enterprise campaign", "findings": len(confirmed), "candidates": len(candidates), "ms": int(v12.get("total_duration_ms") or duration_ms), "execution_status": execution_status, "campaign_id": campaign.get("campaign_id", "")},
            "legacy_domain_layers": {"tool": "disabled", "findings": 0, "candidates": 0, "ms": 0, "reason": "source_bound_scope_fixture_actor_cleanup_contract_required" if multi_layer else "not_requested"},
        },
        "findings": confirmed, "candidate_findings": candidates, "db_findings": [], "e2e_findings": [], "ui_findings": [], "deep_findings": [], "spectrum": {},
        "preflight_diagnostics": diagnostics, "input_gaps": input_gaps, "coverage_gaps": coverage_gaps,
        "scan_preflight_guide": preflight_guide,
        "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "campaign": campaign,
        "behavior_slice_ledger": v12.get("behavior_slice_ledger", {}), "incremental_discovery": incremental,
        "execution_status": execution_status,
        "db_verification": {"status": "plan_only" if schema_text else "blocked", "reason": "source_bound_observation_contract_required" if schema_text else "database_schema_source_missing", "findings": []},
        "ci_gate": {"status": "not_evaluated" if ci_gate else "not_requested", "reason": "confirmed_receipts_and_approved_baseline_required" if ci_gate else ""},
        "auto_har": v12.get("auto_har", {}), "evidence_bundle": evidence_bundle, "release_gate": release_gate, "v12": v12,
    }
    if save_report:
        output = Path(output_dir) if output_dir else root / "platform_outputs" / _safe_project(project)
        report_path = output / "intelligence_report.json"
        _write_json(report_path, {"project": project, "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "real_findings": confirmed, "risk_clues": candidates, "campaign": campaign, "coverage_gaps": coverage_gaps, "scan_preflight_guide": preflight_guide, "runtime_contract": runtime_contract, "test_data_plan": test_data_plan, "behavior_slice_ledger": result["behavior_slice_ledger"], "execution_status": execution_status, "evidence_bundle": evidence_bundle, "release_gate": release_gate})
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
    parser.add_argument("--source-version-id", default="")
    parser.add_argument("--execution-approval-id", default="")
    parser.add_argument("--execution-mode", default="safe_read_only")
    parser.add_argument("--test-data-strategy", default="blocked_with_testability_gap")
    parser.add_argument("--ci-gate", action="store_true")
    parser.add_argument("--no-multi-layer", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    context = {
        "scope_id": args.scope_id, "environment_ref": args.environment_ref,
        "source_manifest": {"source_id": args.source_id, "source_hash": args.source_hash, "source_version_id": args.source_version_id},
        "execution_approval_id": args.execution_approval_id, "execution_mode": args.execution_mode,
        "test_data_contract": {"strategy": args.test_data_strategy},
    }
    result = scan(project=args.project, api_doc_path=args.api_doc or "", api_doc_text=args.api_doc_text or "", prd_text=args.prd, base_url=args.base_url, ci_gate=args.ci_gate, multi_layer=not args.no_multi_layer, output_dir=Path(args.output_dir) if args.output_dir else None, save_report=not args.no_report, campaign_context=context)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif result.get("success"):
        campaign = result.get("campaign", {})
        print(f"QualiBug scan: {result['project']}")
        print(f"Confirmed: {result['total_findings']} | Candidates: {result['total_candidates']} | Execution: {result['execution_status']}")
        print(f"Release gate: {result.get('release_gate', {}).get('verdict', 'not_ready')}")
        print(f"Campaign: {campaign.get('campaign_id', 'n/a')} ({campaign.get('campaign_status', 'n/a')})")
    else:
        print(f"Error: {result.get('error', 'scan failed')}", file=sys.stderr)
    raise SystemExit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
