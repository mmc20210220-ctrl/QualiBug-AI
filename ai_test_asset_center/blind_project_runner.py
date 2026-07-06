from __future__ import annotations

"""Input-only project runner for benchmark / enterprise documents.

This is the strict path for the workflow:

    projects/<project>/input/  ->  QualiBug planning / discovery outputs

It deliberately refuses to read oracle, ground-truth, answer, seed or BUG_MATRIX
files.  It does not use demo/local BugLab bootstrap and it does not classify a
finding as a runtime-confirmed bug when no live target is configured.
"""

import hashlib
import json
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from .enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_files,
    load_enterprise_business_knowledge_asset,
)
from .input_grounded_candidate_compiler import write_grounded_candidate_outputs
from .grounded_probe_executor import run_grounded_probe_executor
from .project_context_compiler import ProjectContextCompiler
from .real_project_defect_discovery import run_real_project_discovery
from .real_project_onboarding import ROOT, _safe_project_id, run_onboarding_check

FORBIDDEN_TOKENS = {
    "oracle",
    "ground_truth",
    "bug_ground_truth",
    "all_bugs",
    "answer",
    "answers",
    "solution",
    "solutions",
    "seed",
    "seeds",
    "enabled_bugs",
    "bug_matrix",
}

DOC_ORDER = [
    "PRD.md",
    "BUSINESS_RULES.md",
    "DATABASE_DESIGN.md",
    "TEST_SCENARIOS.md",
    "RISK_SURFACE_MODEL.md",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_forbidden(path: Path) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")

    def matches(segment: str, token: str) -> bool:
        normalized = normalize(segment)
        if not normalized:
            return False
        return (
            normalized == token
            or normalized.startswith(f"{token}_")
            or normalized.endswith(f"_{token}")
            or f"_{token}_" in normalized
        )

    for part in path.parts:
        if any(matches(part, token) or matches(Path(part).stem, token) for token in FORBIDDEN_TOKENS):
            return True
    return False


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _looks_like_api_doc(text: str) -> bool:
    """Heuristic: does this text look like it contains API endpoint descriptions?"""
    if not text:
        return False
    # Count HTTP methods mentioned in the text
    method_count = sum(text.upper().count(m) for m in ("GET", "POST", "PUT", "PATCH", "DELETE"))
    # Also check for URL-like paths with HTTP methods
    path_count = len(re.findall(r'(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w/\-_{}]+', text, re.I))
    return method_count >= 3 or path_count >= 2


def _extract_openapi_paths_from_markdown(text: str) -> list[tuple[str, str, list[str]]]:
    """Convert Markdown API docs to OpenAPI path entries.

    Returns list of (method, path, tags) tuples. The tags are derived
    from the nearest Markdown heading before each route.
    """
    result: list[tuple[str, str, list[str]]] = []
    seen: set[tuple[str, str]] = set()
    current_section = ""
    for line in text.split("\n"):
        ls = line.strip()
        if ls.startswith("##"):
            current_section = ls.lstrip("# ").strip()
            continue
        m = re.match(r'(?:###\s+)?(GET|POST|PATCH|DELETE|PUT)\s+(/[^\s\n,]+)', ls, re.I)
        if not m:
            continue
        method = m.group(1).upper()
        path = m.group(2).rstrip("/")
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        tags = [current_section] if current_section else []
        if not tags:
            parts = path.strip("/").split("/")
            if len(parts) >= 2:
                tags = parts[:2]
        result.append((method, path, tags))
    return result


def _extract_test_accounts(input_dir: Path) -> dict[str, Any]:
    """Extract test account credentials from input docs.

    Scans for email/password patterns in non-PRD/non-API .md files.
    Calls the login API to obtain tokens for each account found.
    Handles both '| role | email | password |' and '| email | password | role |' tables.
    """
    accounts: dict[str, Any] = {}
    base_url = os.environ.get("QUALIBUG_TARGET_BASE_URL", "")
    if not base_url:
        return accounts

    for p in sorted(input_dir.glob("*.md")):
        name = p.name.lower()
        if name in {"prd.md", "api.md", "readme.md", "business_rules.md", "requirements.md"}:
            continue
        text = _read(p)
        # Try both table column orders:
        # Format A: | role | email | password | ...
        # Format B: | email | password | role | ...
        rows_a = re.findall(
            r'[|]\s*([^|]{2,20})\s*[|]\s*([^|\s]+?@[^|\s]+?)\s*[|]\s*([^|]{6,50})\s*[|]',
            text
        )
        rows_b = re.findall(
            r'[|]\s*([^|\s]+?@[^|\s]+?)\s*[|]\s*([^|]{6,50})\s*[|]\s*([^|]{2,20})\s*[|]',
            text
        )

        # Merge both formats
        found_tuples: list[tuple[str, str, str]] = []  # (role, email, password)
        for col1, col2, col3 in rows_a:
            if "@" in col2 and len(col3.strip()) >= 6:
                found_tuples.append((col1.strip(), col2.strip(), col3.strip()))
        for col1, col2, col3 in rows_b:
            if "@" in col1 and len(col2.strip()) >= 6:
                found_tuples.append((col3.strip(), col1.strip(), col2.strip()))

        if not found_tuples:
            continue

        for role_text, email, password in found_tuples:
            if "@" not in email or len(password) < 5:
                continue
            # Normalize role
            rl = role_text.strip().lower()
            if any(k in rl for k in ("admin", "管理", "超级管理")):
                role = "admin"
            elif any(k in rl for k in ("buyer", "买家", "用户", "普通")):
                role = "normal_user" if "normal_user" not in accounts else f"buyer{len(accounts)}"
            elif any(k in rl for k in ("seller", "卖家", "商家")):
                role = "seller"
            elif any(k in rl for k in ("warehouse", "仓库")):
                role = "warehouse"
            elif any(k in rl for k in ("finance", "财务")):
                role = "finance"
            elif any(k in rl for k in ("auditor", "审计")):
                role = "auditor"
            elif any(k in rl for k in ("disabled", "禁用")):
                role = "disabled"
            else:
                role = f"user_{email.split('@')[0]}"
            try:
                d = json.dumps({"email": email, "password": password}).encode()
                req = urllib.request.Request(
                    f"{base_url}/api/auth/login", data=d,
                    headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=5)
                token = json.loads(resp.read()).get("token", "")
                if token:
                    accounts[role] = {"email": email, "token": token}
            except Exception:
                pass
        if accounts:
            break
    return accounts


def _load_openapi_yaml_or_json(input_dir: Path) -> tuple[dict[str, Any], str]:
    for name in ("openapi.json", "swagger.json"):
        p = input_dir / name
        if p.exists():
            return json.loads(_read(p) or "{}"), name
    for name in ("openapi.yaml", "openapi.yml", "swagger.yaml", "swagger.yml"):
        p = input_dir / name
        if p.exists():
            return yaml.safe_load(_read(p) or "{}") or {}, name
    # Fallback: parse any Markdown file that looks like API docs into
    # OpenAPI-like paths structure. No filename assumptions — uses the
    # same _looks_like_api_doc heuristic already used by the normalizer.
    md_paths: list[tuple[str, str, list[str]]] = []
    for p in sorted(input_dir.glob("*.md")):
        if p.name.lower() in {"prd.md", "readme.md", "business_rules.md", "requirements.md"}:
            continue
        text = _read(p)
        if _looks_like_api_doc(text):
            md_paths = _extract_openapi_paths_from_markdown(text)
            if md_paths:
                break
    if md_paths:
        paths: dict[str, dict[str, dict[str, Any]]] = {}
        for method, path, tags in md_paths:
            paths.setdefault(path, {})[method.lower()] = {
                "operationId": f"{method}_{re.sub(r'[/{}]', '_', path).strip('_')}",
                "summary": "", "tags": tags,
            }
        return {"openapi": "3.0.0", "info": {"title": "Input Only Project"}, "paths": paths}, "api.md"
    return {}, ""


def _knowledge_asset_to_data_dictionary(asset: dict[str, Any] | None) -> dict[str, list[str]]:
    asset = asset if isinstance(asset, dict) else {}
    data_dictionary: dict[str, list[str]] = {}
    for row in asset.get("data_tables") or []:
        if not isinstance(row, dict):
            continue
        table_name = str(row.get("name") or row.get("table") or "").strip()
        if not table_name:
            continue
        columns = [str(item) for item in (row.get("columns") or []) if str(item).strip()]
        if columns:
            data_dictionary.setdefault(table_name, []).extend(columns)
    for row in asset.get("field_dictionary") or []:
        if not isinstance(row, dict):
            continue
        table_name = str(row.get("table") or "default").strip() or "default"
        field_name = str(row.get("field") or row.get("name") or "").strip()
        if field_name:
            data_dictionary.setdefault(table_name, []).append(field_name)
    return {
        table: sorted(dict.fromkeys(field for field in fields if field))
        for table, fields in data_dictionary.items()
    }


def _merge_openapi_with_knowledge_asset(openapi: dict[str, Any], asset: dict[str, Any] | None) -> dict[str, Any]:
    asset = asset if isinstance(asset, dict) else {}
    interfaces = [row for row in (asset.get("interfaces") or []) if isinstance(row, dict)]
    if not interfaces:
        return openapi if isinstance(openapi, dict) else {}

    merged = json.loads(json.dumps(openapi if isinstance(openapi, dict) else {}))
    merged.setdefault("openapi", "3.0.3")
    merged.setdefault("info", {"title": "input_only_knowledge_asset", "version": "1.0"})
    paths = merged.setdefault("paths", {})

    for row in interfaces:
        method = str(row.get("method") or "GET").lower()
        path = str(row.get("path") or "/").strip() or "/"
        if not path.startswith("/"):
            path = "/" + path
        if method not in {"get", "post", "put", "patch", "delete", "head", "options"}:
            continue
        operation = paths.setdefault(path, {}).setdefault(method, {})
        summary = str(row.get("summary") or row.get("title") or row.get("operation_id") or f"{method.upper()} {path}").strip()
        if summary and not operation.get("summary"):
            operation["summary"] = summary
        operation.setdefault("operationId", str(row.get("operation_id") or f"{method}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}"))
        existing_params = {
            str(item.get("name") or "")
            for item in (operation.get("parameters") or [])
            if isinstance(item, dict) and item.get("name")
        }
        for name in [str(item) for item in (row.get("parameters") or []) if str(item).strip()]:
            if name in existing_params:
                continue
            operation.setdefault("parameters", []).append({
                "name": name,
                "in": "path" if "{" + name + "}" in path else "query",
                "required": "{" + name + "}" in path,
                "schema": {"type": "string"},
            })
            existing_params.add(name)
        operation.setdefault("responses", {"200": {"description": "documented in enterprise knowledge asset"}})
        token_space = " ".join([path, summary, " ".join(str(x) for x in (row.get("parameters") or [])), " ".join(str(x) for x in (row.get("tags") or [])), " ".join(str(x) for x in (row.get("tokens") or []))])
        if re.search(r"auth|authorization|bearer|token|登录|鉴权|认证|权限", token_space, re.I) and not operation.get("security"):
            operation["security"] = [{"bearerAuth": []}]
    return merged


def _sync_input_only_knowledge_asset(project_id: str, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    input_dir = root / "platform_inputs" / project
    files = [path for path in sorted(input_dir.rglob("*")) if path.is_file()]
    result = {
        "enabled": False,
        "source_file_count": len(files),
        "summary": {},
        "ingest": {},
        "error": "",
        "asset": None,
    }
    if not files:
        return result
    actor = {"name": "blind_project_runner", "role": "project_owner"}
    try:
        ingest = ingest_enterprise_knowledge_files(project, files, root=root, actor=actor)
        asset = load_enterprise_business_knowledge_asset(project, root)
        if not asset or ingest.get("rebuild_recommended"):
            asset = build_enterprise_business_knowledge_asset(project, root)
        result.update({
            "enabled": bool(asset),
            "summary": (asset or {}).get("summary") or {},
            "ingest": {
                "created_count": len(ingest.get("created") or []),
                "duplicate_count": len(ingest.get("duplicates") or []),
                "error_count": len(ingest.get("errors") or []),
            },
            "asset": asset,
        })
    except Exception as exc:
        result["error"] = str(exc)[:500]
    return result


def _copy_input_only(source_input_dir: Path, dest_input_dir: Path) -> dict[str, Any]:
    source_input_dir = source_input_dir.resolve()
    if source_input_dir.name.lower() != "input":
        raise ValueError(f"source must be a projects/<project>/input directory, got: {source_input_dir}")
    if _is_forbidden(source_input_dir):
        raise ValueError(f"refusing forbidden source path: {source_input_dir}")

    dest_input_dir.mkdir(parents=True, exist_ok=True)
    allowed: list[dict[str, Any]] = []
    blocked: list[str] = []

    for path in sorted(source_input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_input_dir)
        if _is_forbidden(rel) or _is_forbidden(path.relative_to(source_input_dir.parent)):
            blocked.append(str(rel).replace("\\", "/"))
            continue
        if path.stat().st_size > 8_000_000:
            blocked.append(str(rel).replace("\\", "/") + "#too_large")
            continue
        out = dest_input_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
        allowed.append({
            "file": str(rel).replace("\\", "/"),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return {"allowed_input_files": allowed, "blocked_files": blocked}


def _normalize_platform_inputs(project_id: str, source_input_dir: Path, root: Path) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    dest = root / "platform_inputs" / project
    if dest.exists():
        shutil.rmtree(dest)
    manifest = _copy_input_only(source_input_dir, dest)

    merged_docs: list[str] = []
    for name in DOC_ORDER:
        p = dest / name
        if p.exists():
            merged_docs.append(f"\n\n# Source: {name}\n" + _read(p))
    # Include any other markdown input document except API docs, which stay as api docs.
    for p in sorted(dest.glob("*.md")):
        if p.name in set(DOC_ORDER) | {"prd.md"}:
            continue
        # Skip files that look like API docs (contain HTTP methods)
        text = _read(p)
        if _looks_like_api_doc(text):
            continue
        merged_docs.append(f"\n\n# Source: {p.name}\n" + text)
    (dest / "prd.md").write_text("\n".join(merged_docs), encoding="utf-8")

    # Find API doc from any .md file containing API endpoints (not just API.md)
    api_docs = _read(dest / "API.md") or _read(dest / "api.md")
    if not api_docs:
        for p in sorted(dest.glob("*.md")):
            text = _read(p)
            if _looks_like_api_doc(text):
                api_docs = text
                break
    if not api_docs:
        for p in sorted(dest.glob("*.txt")):
            text = _read(p)
            if _looks_like_api_doc(text):
                api_docs = text
                break
    if api_docs:
        (dest / "api.md").write_text(api_docs, encoding="utf-8")

    openapi, openapi_source = _load_openapi_yaml_or_json(dest)
    if openapi:
        (dest / "openapi.json").write_text(json.dumps(openapi, ensure_ascii=False, indent=2), encoding="utf-8")

    # Extract test accounts from any file that looks like account listings
    accounts = _extract_test_accounts(dest)
    if accounts:
        (dest / "test_accounts.json").write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")

    cfg = {
        "project_id": project,
        "project_name": source_input_dir.parent.name,
        "base_url": os.environ.get("QUALIBUG_TARGET_BASE_URL", ""),
        "openapi_source": "json",
        "discovery_mode": "safe",
        "safe_mode": False,
        "allow_destructive_tests": True,
        "request_timeout_seconds": 10,
        "max_probe_count": int(os.environ.get("QUALIBUG_MAX_PROBE_COUNT", "160") or 160),
        "input_only_mode": True,
        "forbidden_sources": sorted(FORBIDDEN_TOKENS),
    }
    (dest / "real_project_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest.update({
        "project_id": project,
        "source_input_dir": str(source_input_dir),
        "platform_input_dir": str(dest),
        "openapi_source": openapi_source,
        "openapi_path_count": len((openapi or {}).get("paths") or {}) if isinstance(openapi, dict) else 0,
        "leak_guard": "STRICT_INPUT_ONLY_NO_ORACLE_NO_GROUND_TRUTH_NO_BUG_MATRIX",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    (dest / "blind_input_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _compile_project_context(project_id: str, root: Path, knowledge_asset: dict[str, Any] | None = None) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    input_dir = root / "platform_inputs" / project
    prd = _read(input_dir / "prd.md")
    api_docs = _read(input_dir / "api.md")
    openapi = json.loads(_read(input_dir / "openapi.json") or "{}")
    openapi = _merge_openapi_with_knowledge_asset(openapi, knowledge_asset)
    data_dictionary = _knowledge_asset_to_data_dictionary(knowledge_asset)
    compiler = ProjectContextCompiler()
    ctx = compiler.compile(
        prd_text=prd,
        openapi_spec=openapi,
        api_docs_text=api_docs,
        data_dictionary=data_dictionary,
    )
    payload = compiler.to_dict(ctx)
    out = root / "platform_outputs" / project / "input_only_project_context.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "project_context": str(out),
        "entity_count": len(payload.get("entities") or []),
        "api_count": len(payload.get("apis") or []),
        "observer_count": len(payload.get("observers") or []),
        "candidate_invariant_count": len(payload.get("candidate_invariants") or []),
        "candidate_lifecycle_count": len(payload.get("candidate_lifecycle_transitions") or []),
        "knowledge_data_dictionary_count": len(data_dictionary),
        "knowledge_interface_count": len((knowledge_asset or {}).get("interfaces") or []),
    }


def _summarize_discovery(data: dict[str, Any]) -> dict[str, Any]:
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    return {
        "issue_count": int(metrics.get("issue_count") or len(issues)),
        "high_confidence_issues": int(metrics.get("high_confidence_issues") or 0),
        "suggested_release_blockers": int(metrics.get("suggested_release_blockers") or 0),
        "needs_human_review": int(metrics.get("needs_human_review") or len(issues)),
        "confirmed_runtime_bugs": sum(1 for item in issues if str(item.get("status") or "").lower() in {"confirmed", "validated", "validated_candidate"}),
        "risk_types": sorted({str(item.get("risk_type") or "unknown") for item in issues if isinstance(item, dict)})[:50],
    }


def _summarize_grounded_candidates(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    return {
        "issue_count": int(summary.get("candidate_count") or len(candidates)),
        "high_confidence_issues": sum(1 for item in candidates if float(item.get("confidence") or 0) >= 0.7),
        "suggested_release_blockers": sum(1 for item in candidates if str(item.get("severity") or "").upper() in {"P0", "P1"}),
        "needs_human_review": int(summary.get("needs_human_review") or len(candidates)),
        "confirmed_runtime_bugs": 0,
        "risk_types": sorted((summary.get("by_risk_type") or {}).keys()),
        "by_execution_policy": summary.get("by_execution_policy") or {},
        "candidate_mode": "document_grounded_input_only",
    }


def run_input_only_project(
    *,
    project_input_dir: str | Path,
    project_id: str | None = None,
    root: str | Path | None = None,
    base_url: str = "",
    execute_readonly: bool = False,
    probe_config: str | Path | None = None,
    max_probes: int = 0,
) -> dict[str, Any]:
    """Run the enterprise document-driven engine using only input/ files."""
    root_path = Path(root or ROOT).resolve()
    input_dir = Path(project_input_dir).resolve()
    project = _safe_project_id(project_id or input_dir.parent.name)
    manifest = _normalize_platform_inputs(project, input_dir, root_path)
    knowledge_sync = _sync_input_only_knowledge_asset(project, root_path)
    knowledge_asset = knowledge_sync.get("asset") if isinstance(knowledge_sync.get("asset"), dict) else None
    context_summary = _compile_project_context(project, root_path, knowledge_asset=knowledge_asset)
    onboarding = run_onboarding_check(project, root_path)

    output_dir = root_path / "platform_outputs" / project / "input_only_run"
    grounded = write_grounded_candidate_outputs(
        root_path / "platform_inputs" / project,
        output_dir,
        project_id=project,
        knowledge_asset=knowledge_asset,
    )

    # Phase A: document-grounded single-step discovery (always runs)
    probe_execution: dict[str, Any] | None = None
    if base_url or execute_readonly or os.environ.get("QUALIBUG_TARGET_BASE_URL"):
        probe_execution = run_grounded_probe_executor(
            probe_plan_path=output_dir / "grounded_probe_plan.json",
            out_dir=output_dir,
            base_url=base_url,
            probe_config=probe_config,
            execute_readonly=execute_readonly,
            max_probes=max_probes,
            input_dir=input_dir,
        )

    # Phase B: multi-step business flow discovery (always runs in parallel)
    flow_discovery: dict[str, Any] = {"metrics": {"issue_count": 0}, "items": []}
    try:
        flow_discovery = run_real_project_discovery(project, root_path)
    except Exception:
        pass
    discovery_summary = _summarize_grounded_candidates(grounded)
    flow_summary = _summarize_discovery(flow_discovery)
    discovery_summary["flow_probe_count"] = flow_summary.get("issue_count", 0)
    discovery_summary["flow_high_confidence"] = flow_summary.get("high_confidence_issues", 0)
    if probe_execution:
        execution_summary = probe_execution.get("summary") or {}
        discovery_summary["confirmed_runtime_bugs"] = int(execution_summary.get("validated_candidate_count") or 0)
        discovery_summary["protected_runtime_candidates"] = int(execution_summary.get("protected_count") or 0)
        discovery_summary["runtime_evidence_ready"] = int(execution_summary.get("validated_candidate_count") or 0) > 0
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "input_only_enterprise_docs",
        "project_id": project,
        "project_name": input_dir.parent.name,
        "strict_no_peek": True,
        "allowed_source_root": str(input_dir),
        "forbidden_sources": sorted(FORBIDDEN_TOKENS),
        "input_manifest": manifest,
        "knowledge_asset_summary": knowledge_sync.get("summary") or {},
        "knowledge_asset_sync": {
            "enabled": bool(knowledge_asset),
            "source_file_count": int(knowledge_sync.get("source_file_count") or 0),
            "ingest": knowledge_sync.get("ingest") or {},
            "error": knowledge_sync.get("error") or "",
        },
        "project_context_summary": context_summary,
        "onboarding_ok": bool(onboarding.get("ok")),
        "discovery_summary": discovery_summary,
        "grounded_candidate_summary": grounded.get("summary"),
        "grounded_probe_execution_summary": (probe_execution or {}).get("summary"),
        "flow_discovery_summary": flow_summary,
        "outputs": {
            "platform_input_dir": str(root_path / "platform_inputs" / project),
            "knowledge_asset": str(root_path / "platform_outputs" / project / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json"),
            "knowledge_asset_report": str(root_path / "platform_outputs" / project / "enterprise_knowledge_center" / "enterprise_business_knowledge_report.html"),
            "project_context": context_summary.get("project_context"),
            "grounded_candidates": str(output_dir / "grounded_candidates.json"),
            "grounded_candidates_md": str(output_dir / "grounded_candidates.md"),
            "grounded_probe_plan": str(output_dir / "grounded_probe_plan.json"),
            "runtime_validation_queue": str(output_dir / "runtime_validation_queue.json"),
            "runtime_validation_queue_md": str(output_dir / "runtime_validation_queue.md"),
            "grounded_probe_execution_report": str(output_dir / "grounded_probe_execution_report.json") if probe_execution else "",
            "grounded_probe_execution_report_md": str(output_dir / "grounded_probe_execution_report.md") if probe_execution else "",
            "grounded_probe_repro_ps1": str(output_dir / "grounded_probe_repro.ps1") if probe_execution else "",
            "grounded_probe_regression_pytest": str(output_dir / "grounded_probe_regression_pytest.py") if probe_execution else "",
            "flow_discovery_report": str(root_path / "platform_outputs" / project / "real_project" / "real_project_defect_report.html"),
            "flow_discovery_issues": str(root_path / "platform_outputs" / project / "real_project" / "discovered_issues.json"),
        },
        "note": "No oracle/ground_truth/BUG_MATRIX files were read. Without a configured live target, output is document-derived candidates, not runtime-confirmed bugs.",
    }
    (output_dir / "blind_input_run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "blind_input_run_report.md").write_text(render_input_only_report(report), encoding="utf-8")
    report["outputs"]["blind_input_run_report"] = str(output_dir / "blind_input_run_report.json")
    report["outputs"]["blind_input_run_report_md"] = str(output_dir / "blind_input_run_report.md")
    return report


def render_input_only_report(report: dict[str, Any]) -> str:
    manifest = report.get("input_manifest") or {}
    ctx = report.get("project_context_summary") or {}
    ds = report.get("discovery_summary") or {}
    files = "\n".join(f"- `{item.get('file')}` sha256={item.get('sha256','')[:12]}" for item in manifest.get("allowed_input_files") or [])
    risks = ", ".join(ds.get("risk_types") or [])
    return f"""# Input-only QualiBug Run — {report.get('project_name')}

## Guardrail

- strict_no_peek: `{report.get('strict_no_peek')}`
- allowed source root: `{report.get('allowed_source_root')}`
- leak guard: `{manifest.get('leak_guard')}`
- blocked files: `{len(manifest.get('blocked_files') or [])}`

## Input files used

{files or '- none'}

## Compiled project context

- entities: {ctx.get('entity_count')}
- APIs: {ctx.get('api_count')}
- observers: {ctx.get('observer_count')}
- candidate invariants: {ctx.get('candidate_invariant_count')}
- lifecycle candidates: {ctx.get('candidate_lifecycle_count')}

## Discovery output

- issue candidates: {ds.get('issue_count')}
- high confidence candidates: {ds.get('high_confidence_issues')}
- needs human review: {ds.get('needs_human_review')}
- runtime confirmed bugs: {ds.get('confirmed_runtime_bugs')}
- suggested release blockers: {ds.get('suggested_release_blockers')}
- risk types: {risks or 'none'}

> Without a configured live target, QualiBug does not label these as runtime-confirmed bugs. They are document-derived business-risk candidates and executable probe plans.
"""
