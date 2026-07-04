import ast, json

path = "D:/QualiBug-AI/QualiBug_frontend_worktree/ai_test_asset_center/autonomous_pipeline.py"
with open(path) as f:
    content = f.read()

# Change 1: counterexample safe_live
old = "        cex_result = run_counterexample_discovery(project, root)"
new = """        cex_options: dict[str, Any] = {}
        if not cfg.get("safe_mode", False) and base_url:
            cex_options["execution_mode"] = "safe_live"
        cex_result = run_counterexample_discovery(project, root, options=cex_options)"""
content = content.replace(old, new)

# Change 2: invariant safe_live
old = "        inv_result = run_business_invariant_mining(project, root)"
new = """        inv_options: dict[str, Any] = {}
        if not cfg.get("safe_mode", False) and base_url:
            inv_options["execution_mode"] = "safe_live"
        inv_result = run_business_invariant_mining(project, root, options=inv_options)"""
content = content.replace(old, new)

# Change 3: sandbox in validation
old = "        execution = execute_bug_validation_queue(project, root, queue)"
new = """        allow_sandbox = bool(cfg.get("allow_destructive_tests")) and not cfg.get("safe_mode", False)
        execution = execute_bug_validation_queue(project, root, queue, allow_sandbox=allow_sandbox, base_url=base_url)"""
content = content.replace(old, new)

# Change 4: sandbox result integration
old = "        all_findings = apply_validation_results_to_findings(all_findings, queue, execution)"
new = """        all_findings = apply_validation_results_to_findings(all_findings, queue, execution)
        # Merge sandbox probe bugs into findings
        for sr in execution.get("results", []):
            if sr.get("execution_kind") == "sandbox_probe" and sr.get("executed"):
                ev = sr.get("evidence", {})
                req = ev.get("request", {})
                resp = ev.get("response", {})
                body = str(resp.get("body_excerpt", ""))
                status = resp.get("status_code", 0)
                if "injected" in body.lower() or status >= 500:
                    import re
                    m = re.search(r'bug_id["\\'"]?\\s*:\\s*["\\']([^"\\']+)', body)
                    bid = m.group(1) if m else ""
                    all_findings.append({
                        "severity": "P0" if status >= 500 else "P1",
                        "title": "[Sandbox] " + req.get("method","?") + " " + req.get("url","?") + " HTTP" + str(status),
                        "category": "sandbox_probe", "source": "runtime_probe",
                        "method": req.get("method", ""),
                        "path": req.get("url", "").replace(base_url, "") if base_url else req.get("url", ""),
                        "description": body[:500], "confidence_score": 0.95 if bid else 0.85,
                        "evidence": ("Sandbox Bug#" + bid) if bid else ("Sandbox HTTP" + str(status)),
                    })"""
content = content.replace(old, new)

# Change 5: add os import
content = content.replace("import json\nimport re\nimport time", "import json\nimport os\nimport re\nimport time")

# Change 6: full discovery engine
old = """    # 2g. LLM Oracle Hypotheses
    llm_oracle_hypotheses: list[dict[str, Any]] = []
    try:
        from .llm_reasoning import compile_oracle_hypotheses
        known_paths = _openapi_paths(api_doc)
        llm_oracle_hypotheses = compile_oracle_hypotheses(
            prd_text=prd, api_schema=api_doc,
            heuristic_findings=all_findings, known_paths=known_paths,
        )
    except Exception:
        pass

    report["stage2_discovery"] = {"""

fd_block = """    # 2g. LLM Oracle Hypotheses
    llm_oracle_hypotheses: list[dict[str, Any]] = []
    try:
        from .llm_reasoning import compile_oracle_hypotheses
        known_paths = _openapi_paths(api_doc)
        llm_oracle_hypotheses = compile_oracle_hypotheses(
            prd_text=prd, api_schema=api_doc,
            heuristic_findings=all_findings, known_paths=known_paths,
        )
    except Exception:
        pass

    # 2h. Full Discovery Engine
    full_discovery_summary: dict[str, Any] = {"status": "not_run"}
    discovery_mode = str(cfg.get("discovery_mode", "")).lower()
    if discovery_mode == "full" and base_url and not cfg.get("safe_mode", False):
        try:
            os.environ["QUALIBUG_USE_ANALYZERS"] = "1"
            industry = str(cfg.get("industry", "")).strip()
            project_name = str(cfg.get("project_name", "")).strip()
            ctx_parts = []
            if industry: ctx_parts.append("- \\u884c\\u4e1a\\uff1a" + industry)
            if project_name: ctx_parts.append("- \\u7cfb\\u7edf\\uff1a" + project_name)
            prd_preview = prd[:2000] if prd else ""
            if prd_preview: ctx_parts.append("- \\u4e1a\\u52a1\\u6587\\u6863\\uff1a" + prd_preview)
            ctx = "\\n".join(ctx_parts)
            if ctx: os.environ["QUALIBUG_PROJECT_CONTEXT_GUARD"] = ctx
            from .discovery_engine import AutonomousDiscoveryEngine
            engine = AutonomousDiscoveryEngine(base_url=base_url)
            prior = all_findings.copy() if all_findings else None
            discovery_result = engine.discover(prd, api_doc, prior_findings=prior)
            engine_findings = discovery_result.get("findings", [])
            engine_internal = getattr(engine, "findings", [])
            internal_by_id = {}
            for ef in engine_internal:
                eid = getattr(ef, "hypothesis_id", "")
                if eid: internal_by_id[eid] = ef
            actual_paths = _openapi_paths(api_doc)
            valid = []
            runtime_confirmed = 0
            for f in engine_findings:
                fid = str(f.get("id", ""))
                verdict = str(f.get("verdict", ""))
                title = str(f.get("title", ""))
                severity = f.get("severity", "P1")
                internal = internal_by_id.get(fid)
                full_ev = getattr(internal, "evidence", {}) if internal else {}
                calls = full_ev.get("calls", [])
                finding = {"title": title, "severity": severity, "category": "llm_hypothesis",
                    "confidence_score": float(f.get("confidence", 0.5)), "source": "discovery_engine", "verdict": verdict}
                if verdict == "confirmed" and calls:
                    first = calls[0] if calls else {}
                    ci = first.get("call", "")
                    adm = first.get("results", {}).get("admin", {})
                    finding["source"] = "runtime_probe"
                    finding["category"] = "confirmed_by_probe"
                    parts = ci.split(" ", 1) if ci else ["", ""]
                    finding["method"] = parts[0] if len(parts) > 0 else ""
                    finding["path"] = parts[1] if len(parts) > 1 else ""
                    finding["description"] = json.dumps({"http_status": adm.get("status", 0),
                        "response_body": str(adm.get("body", {}))[:500], "total_calls": len(calls),
                        "expected": str(getattr(internal, "expected", ""))[:200] if internal else "",
                        "actual": str(getattr(internal, "actual", ""))[:200] if internal else ""}, ensure_ascii=False)
                    runtime_confirmed += 1
                fpath = finding.get("path", "")
                if not fpath: valid.append(finding)
                elif fpath in actual_paths: valid.append(finding)
                elif any(fpath.startswith(ap.replace("{id}","").replace("{action}","").rstrip("/")) for ap in actual_paths if "{" in ap): valid.append(finding)
            existing_titles = {str(f.get("title", "")) for f in all_findings}
            new_f = [f for f in valid if str(f.get("title", "")) not in existing_titles]
            all_findings.extend(new_f)
            full_discovery_summary = {"status": "completed", "engine_finding_count": len(engine_findings),
                "new_findings_added": len(new_f), "runtime_confirmed": runtime_confirmed,
                "engine_report": getattr(engine, "_last_engine_report", {})}
        except Exception as exc:
            full_discovery_summary = {"status": "failed", "error": str(exc)[:300]}

    report["stage2_discovery"] = {"""

content = content.replace(old, fd_block)

# Change 7: add full_discovery to stage2 report
old = '        "probe_plan_count": len(probe_plan),\n        "duration_seconds":'
new = '        "probe_plan_count": len(probe_plan),\n        "full_discovery": full_discovery_summary,\n        "duration_seconds":'
content = content.replace(old, new)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

with open(path) as f:
    ast.parse(f.read())
print("All changes applied - Syntax OK!")
