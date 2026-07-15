"""Semantic bug discovery: PRD/OpenAPI analysis + API probing + LLM reasoning."""
from __future__ import annotations
import json as _json
from pathlib import Path
from typing import Any


def run_semantic_analysis(
    project: str,
    root: Path,
    existing_findings: list[dict[str, Any]],
    health_base_url: str,
) -> list[dict[str, Any]]:
    """Run deep semantic analysis: load PRD+OpenAPI, probe endpoints, use LLM for gap analysis."""
    findings: list[dict[str, Any]] = list(existing_findings)

    try:
        from .enterprise_knowledge_center import _load_registry, _paths as _kc_paths
        import yaml as _yaml_lib
        import urllib.request as _ur2
        import urllib.error as _ue2

        reg = _load_registry(project, root)
        kc_paths = _kc_paths(project, root)

        # Load PRD and OpenAPI from source_inventory
        prd_text = ""
        oapi_text = ""
        asset_path = root / "platform_outputs" / project / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json"
        if asset_path.exists():
            try:
                asset = _json.loads(asset_path.read_text(encoding="utf-8"))
                inventory = asset.get("source_inventory", [])
                for src in inventory:
                    stype = str(src.get("source_type", "")).lower()
                    name = src.get("original_name", "")
                    sid = src.get("source_id", "")
                    ver = src.get("version", 1)
                    sp = kc_paths["source_dir"] / "{0}_v{1}_{2}".format(sid, ver, name)
                    if not sp.exists():
                        continue
                    text = sp.read_text(encoding="utf-8", errors="replace")
                    effective_type = stype
                    if effective_type in ("historical_bug", "unknown", ""):
                        if (name.endswith((".yaml", ".yml", ".json"))
                                and ("openapi" in text.lower()[:200] or "swagger" in text.lower()[:200])):
                            effective_type = "openapi"
                        elif name.endswith(".md") or "prd" in name.lower():
                            effective_type = "prd"
                    if effective_type in ("prd", "mrd"):
                        if not prd_text or len(text) > len(prd_text):
                            prd_text = text
                    elif effective_type == "openapi":
                        if not oapi_text or len(text) > len(oapi_text):
                            oapi_text = text
            except Exception:
                pass

        # Parse OpenAPI and probe endpoints to collect observed data
        oapi = None
        observed_data = "No API responses collected"
        if oapi_text:
            try:
                oapi = _yaml_lib.safe_load(oapi_text) if not oapi_text.strip().startswith("{") else _json.loads(oapi_text)
            except Exception:
                try:
                    oapi = _json.loads(oapi_text)
                except Exception:
                    oapi = None
            if oapi and isinstance(oapi, dict):
                paths = oapi.get("paths", {})
                observed_parts = []
                for ep_path, methods in list(paths.items())[:8]:
                    if "get" not in methods:
                        continue
                    full_url = health_base_url.rstrip("/") + str(ep_path).replace("{project}", "real_project_demo")
                    try:
                        req_hdrs = {"X-QualiBug-Actor": "admin", "X-QualiBug-Role": "admin"}
                        r = _ur2.urlopen(_ur2.Request(full_url, headers=req_hdrs), timeout=5)
                        body = r.read().decode("utf-8", errors="replace")[:300]
                        observed_parts.append("{}: HTTP {}\n{}".format(ep_path, r.status, body[:200]))
                    except Exception:
                        pass
                if observed_parts:
                    observed_data = "\n---\n".join(observed_parts)

        # LLM semantic gap analysis: PRD vs OpenAPI with real API data
        has_prd = bool(prd_text)
        has_oapi = bool(oapi_text)
        # Always run when source material is available — LLM provides orthogonal
        # insights not covered by heuristic engines, even when findings are rich.
        if has_prd and has_oapi:
            try:
                from .llm_reasoning import reason as _llm_reason
                llm_res = _llm_reason("causality", {
                    "prd_text": prd_text[:3000],
                    "api_schema": oapi_text[:3000],
                    "observed_data": observed_data[:3000],
                    "heuristic_findings": str(findings)[:2000],
                })
                parsed = None
                if isinstance(llm_res, dict) and llm_res.get("findings"):
                    parsed = llm_res
                elif isinstance(llm_res, str):
                    try:
                        parsed = _json.loads(llm_res)
                    except Exception:
                        pass
                if parsed and parsed.get("findings"):
                    for f in parsed["findings"]:
                        f["llm_participated"] = True
                        f["source"] = "llm_semantic_analysis"
                        findings.append(f)
            except Exception:
                pass

    except Exception:
        pass

    return findings
