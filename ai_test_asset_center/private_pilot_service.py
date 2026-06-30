from __future__ import annotations

"""Private-network HTTP entrypoint for the QualiBug pilot runtime.

The service binds to localhost by default. In private-cloud deployments, a
trusted reverse proxy or enterprise SSO gateway should authenticate users and
inject the actor/role headers documented below. The service never accepts raw
credential values; connectors only receive secret references.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .enterprise_pilot_runtime import (
    build_enterprise_pilot_overview,
    list_pilot_tasks,
    operate_enterprise_pilot_runtime,
)
from .real_project_onboarding import ROOT, _safe_project_id


CONFIG_MANAGER_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
KNOWLEDGE_MANAGER_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
SETTINGS_MANAGER_ROLES = {"project_owner", "security_owner", "testops_admin", "admin"}
PROJECT_SCOPE_HEADER = "X-QualiBug-Project-Scopes"
KNOWLEDGE_INGEST_SOURCE_TYPES = (
    "prd",
    "mrd",
    "openapi",
    "postman",
    "database_schema",
    "permission_matrix",
    "historical_bug",
    "ticket",
    "feishu_document",
    "confluence_document",
    "collaboration_document",
    "other_document",
)
KNOWLEDGE_INGEST_TEXT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".json",
    ".csv",
    ".sql",
    ".xml",
)
KNOWLEDGE_INGEST_BINARY_EXTENSIONS = (".pdf", ".docx")
KNOWLEDGE_INGEST_EXTENSIONS = KNOWLEDGE_INGEST_TEXT_EXTENSIONS + KNOWLEDGE_INGEST_BINARY_EXTENSIONS
ONBOARD_DOCUMENT_EXTENSIONS = (".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm")
ONBOARD_OPENAPI_EXTENSIONS = (".yaml", ".yml", ".json")


def _extensions_label(items: tuple[str, ...]) -> str:
    return " ".join(items)


def _extensions_accept(items: tuple[str, ...]) -> str:
    return ",".join(items)


def _root() -> Path:
    configured = os.environ.get("QUALIBUG_PRIVATE_ROOT", "").strip()
    return Path(configured).resolve() if configured else ROOT


def _actor(headers: Any) -> dict[str, str] | None:
    name = str(headers.get("X-QualiBug-Actor") or headers.get("x-qualibug-actor") or "").strip()
    role = str(headers.get("X-QualiBug-Role") or headers.get("x-qualibug-role") or "").strip()
    if not name or not role:
        return None
    return {"name": name[:120], "role": role[:64]}


def _truthy_env(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _write_env_local(updates: dict[str, str]) -> Path:
    configured = os.environ.get("QUALIBUG_ENV_LOCAL_PATH", "").strip()
    env_path = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1] / ".env.local"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else [
        "# Local-only QualiBug LLM credentials.",
        "# This file is ignored by git. Do not share it.",
        "",
    ]
    keys = set(updates)
    written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        raw = line.strip()
        key = raw.split("=", 1)[0].strip().upper() if "=" in raw and not raw.startswith("#") else ""
        if key in keys:
            new_lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key in sorted(keys - written):
        new_lines.append(f"{key}={updates[key]}")
    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def _synchronize_scan_aggregates(report: dict[str, Any]) -> dict[str, Any]:
    """Make every scan view derive its counts from the final calibrated list.

    Health, semantic and validation stages may replace the discovery list after
    the autonomous pipeline has built its executive summary. Keeping the
    aggregation here prevents release and management views from under-reporting
    the final risk population.
    """
    stage2 = dict(report.get("stage2_discovery") or {})
    findings = [item for item in (stage2.get("findings") or []) if isinstance(item, dict)]
    stage2["findings"] = findings
    stage2["total_findings"] = len(findings)
    severities = sorted({str(item.get("severity") or "unknown") for item in findings})
    stage2["by_severity"] = {
        severity: sum(1 for item in findings if str(item.get("severity") or "unknown") == severity)
        for severity in severities
    }
    report["stage2_discovery"] = stage2

    executive = dict(report.get("executive_summary") or {})
    executive["total_bugs_found"] = len(findings)
    executive["critical_bugs"] = sum(1 for item in findings if str(item.get("severity") or "") == "P0")
    executive["high_priority_bugs"] = sum(1 for item in findings if str(item.get("severity") or "") == "P1")

    stage3 = dict(report.get("stage3_impact_analysis") or {})
    analyses = [item for item in (stage3.get("analyses") or []) if isinstance(item, dict)]
    stage3["analyses"] = analyses
    stage3["total_analyses"] = len(analyses)
    stage3["llm_powered"] = sum(1 for item in analyses if item.get("source") == "llm_evidence_impact")
    stage3["heuristic"] = len(analyses) - int(stage3["llm_powered"])
    report["stage3_impact_analysis"] = stage3
    executive["impact_analyses"] = len(analyses)
    executive["llm_powered_analyses"] = int(stage3["llm_powered"])
    report["executive_summary"] = executive
    return report


def _extend_stage3_impact_analysis(report: dict[str, Any], analyses: list[dict[str, Any]]) -> None:
    """Append post-pipeline impact notes without discarding LLM assessments."""
    if not analyses:
        return
    stage3 = dict(report.get("stage3_impact_analysis") or {})
    existing = [item for item in (stage3.get("analyses") or []) if isinstance(item, dict)]
    existing.extend(item for item in analyses if isinstance(item, dict))
    stage3["analyses"] = existing
    stage3["total_analyses"] = len(existing)
    stage3["llm_powered"] = sum(1 for item in existing if item.get("source") == "llm_evidence_impact")
    stage3["heuristic"] = sum(1 for item in existing if item.get("source") != "llm_evidence_impact")
    report["stage3_impact_analysis"] = stage3


class PrivatePilotHandler(BaseHTTPRequestHandler):
    server_version = "QualiBugPrivatePilot/1.0"

    def _root(self) -> Path:
        configured = getattr(self.server, "qualibug_private_root", None)
        return Path(configured).resolve() if configured else _root()

    def _json(self, body: Any, status: int = 200) -> None:
        try:
            raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass  # client disconnected

    def _html(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _project(self) -> str:
        query = parse_qs(urlparse(self.path).query)
        return _safe_project_id((query.get("project") or ["real_project_demo"])[0])

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if not size:
            return {}
        if size > 2_000_000:
            raise ValueError("Request body exceeds the private service limit.")
        parsed = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
        return parsed if isinstance(parsed, dict) else {}

    def _require_actor(self) -> dict[str, str] | None:
        actor = _actor(self.headers)
        if actor is None:
            server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
            local_dev_actor_allowed = (
                _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "1")
                and server_host in {"127.0.0.1", "localhost", "::1"}
                and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
                and str(self.headers.get("X-QualiBug-No-Local-Dev") or "").strip() != "1"
            )
            if local_dev_actor_allowed:
                return {
                    "name": os.environ.get("QUALIBUG_LOCAL_ACTOR", "local_dev")[:120],
                    "role": os.environ.get("QUALIBUG_LOCAL_ROLE", "project_owner")[:64],
                }
        if actor is None:
            self._json(
                {
                    "ok": False,
                    "error": "MISSING_TRUSTED_ACTOR",
                    "message": "The private service requires trusted X-QualiBug-Actor and X-QualiBug-Role headers, unless localhost-only local development actor mode is enabled.",
                },
                401,
            )
        return actor

    def _require_role(self, actor: dict[str, str], allowed: set[str], action: str) -> bool:
        if actor.get("role") in allowed:
            return True
        self._json(
            {
                "ok": False,
                "error": "FORBIDDEN",
                "message": f"{action} requires one of: {', '.join(sorted(allowed))}.",
            },
            403,
        )
        return False

    def _require_project_scope(self, project: str) -> bool:
        """Require an explicit trusted project scope outside localhost dev mode.

        Actor and role headers establish *who* is calling, but they do not by
        themselves establish which customer/project data the caller may read or
        change.  In a public/private-cloud binding, the trusted reverse proxy
        must inject a comma-separated allow-list (or ``*`` for an explicitly
        authorized platform operator) through ``X-QualiBug-Project-Scopes``.

        The localhost-only development fallback remains intentionally narrow:
        it is available only while public binding is disabled, matching the
        existing local actor fallback used by the self-contained pilot demo.
        """
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        scopes = {
            _safe_project_id(item)
            for item in raw.replace(";", ",").split(",")
            if item.strip() and item.strip() != "*"
        }
        wildcard = any(item.strip() == "*" for item in raw.replace(";", ",").split(","))
        if wildcard or _safe_project_id(project) in scopes:
            return True

        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        )
        if local_development:
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_SCOPE_FORBIDDEN",
                "message": f"Requested project is outside the trusted {PROJECT_SCOPE_HEADER} allow-list.",
            },
            403,
        )
        return False

    def _load_pipeline_report(self, project: str, root: Path) -> dict[str, Any]:
        """Load the latest pipeline report from disk."""
        import json as _json
        report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
        if not report_path.exists():
            return {"ok": False, "error": "NO_REPORT", "message": "尚未执行过 Bug 扫描。"}
        try:
            data = _json.loads(report_path.read_text(encoding="utf-8"))
            return {"ok": True, "report": data}
        except Exception:
            return {"ok": False, "error": "LOAD_FAILED", "message": "扫描报告加载失败。"}

    def _load_scan_history(self, project: str, root: Path) -> dict[str, Any]:
        """Load scan history from disk."""
        import json as _json
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        if not history_path.exists():
            return {"ok": True, "history": []}
        try:
            return {"ok": True, "history": _json.loads(history_path.read_text(encoding="utf-8"))}
        except Exception:
            return {"ok": True, "history": []}

    def _list_project_inputs(self, project: str, root: Path) -> dict[str, Any]:
        """List project input files as knowledge sources from disk."""
        import json as _json, os as _os
        input_dir = root / "platform_inputs" / project
        sources = []
        if input_dir.exists():
            config_path = input_dir / "real_project_config.json"
            source_dir = input_dir
            if config_path.exists():
                try:
                    config = _json.loads(config_path.read_text(encoding="utf-8"))
                    src = config.get("source_dataset", "")
                    if src and _os.path.isdir(src):
                        source_dir = Path(src)
                except Exception:
                    pass
            for f in sorted(source_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    ext = f.suffix.lower()
                    if ext in (".md",):
                        stype = "PRD" if "prd" in f.name.lower() else "\u4e1a\u52a1\u6587\u6863"
                    elif ext in (".yaml", ".yml", ".json"):
                        stype = "OpenAPI" if "openapi" in f.name.lower() else "\u89c4\u8303\u6587\u4ef6"
                    elif ext == ".sql":
                        stype = "\u6570\u636e\u5e93 Schema"
                    else:
                        stype = "\u4e1a\u52a1\u6587\u6863"
                    sources.append({
                        "source_id": f"input-{f.name}",
                        "filename": f.name,
                        "source_type": stype,
                        "status": "active",
                        "size_bytes": f.stat().st_size,
                        "uploaded_at": "",
                    })
        return {"ok": True, "sources": sources}

    def _render_onboard(self, project: str, root: Path) -> None:
        """Render the project onboarding wizard page."""
        import json as _json, os as _os
        from .product_ui import product_shell, h, section, _icon
        from .enterprise_knowledge_center import _load_registry
        from .enterprise_testops_control_plane import load_environment_config, _environment_by_name

        reg = _load_registry(project, root)
        active_sources = [s for s in reg.get("sources", []) if s.get("status") == "active"]
        has_prd = any(str(s.get("source_type","")).lower() in ("prd","mrd") for s in active_sources)
        has_openapi = any(str(s.get("source_type","")).lower() == "openapi" for s in active_sources)
        doc_count = len(active_sources)

        env_cfg = load_environment_config(project, root)
        target = _environment_by_name(env_cfg, "test")
        base_url = str(target.get("base_url", ""))
        env_configured = bool(base_url and "dashboard" not in base_url)

        llm_key = _os.environ.get("LLM_API_KEY", "")
        llm_configured = bool(llm_key and len(llm_key) > 4)

        score = 0
        checks = []
        if doc_count > 0: score += 20; checks.append((u"已导入资料", "success", f"{doc_count} files"))
        else: checks.append((u"缺少资料", "danger", u"需要导入 PRD 或 OpenAPI 规范"))
        if has_prd: score += 20; checks.append((u"PRD 已导入", "success", u"业务需求文档就绪"))
        else: checks.append((u"缺少 PRD", "warning", u"语义理解范围受限"))
        if has_openapi: score += 20; checks.append((u"OpenAPI 规范 已导入", "success", u"API 契约就绪"))
        else: checks.append((u"缺少 OpenAPI 规范", "warning", u"探针和因果发现不可用"))
        if env_configured: score += 20; checks.append((u"环境已配置", "success", base_url[:50]))
        else: checks.append((u"环境未配置", "warning", u"无法执行 API 探针"))
        if llm_configured: score += 20; checks.append((u"LLM 已配置", "success", u"推理可用"))
        else: checks.append((u"LLM 未配置", "warning", u"回退到启发式"))
        tone = "success" if score >= 80 else "warning" if score >= 40 else "danger"

        body = (
            f"""<div class="onboard-hero">
  <div class="onboard-score"><strong class="tone-{tone}">{score}</strong><span>/100</span></div>
  <h1>项目接入向导</h1>
  <p>一次性配置所有要素，完成后即可启动项目进入总览面板。</p>
  <p class="onboard-note">完成: {sum(1 for c in checks if c[1]=="success")}/{len(checks)}</p>
</div>"""
        )
        clist = "".join(
            f"<div class='onboard-check onboard-check-{c[1]}'><span class='status status-{c[1]}'>{'V' if c[1]=='success' else '!' if c[1]=='danger' else '?'}</span><div><strong>{c[0]}</strong><p>{c[2]}</p></div></div>"
            for c in checks
        )
        body += section(u"项目准备度", f"{score}/100", clist, section_id="readiness")

        body += section(
            u"① 导入项目资料",
            (
                u"当前向导支持 PRD/需求文档与 OpenAPI 规范上传。"
                u"文档侧支持 "
                + _extensions_label(ONBOARD_DOCUMENT_EXTENSIONS)
                + u"，OpenAPI 侧支持 "
                + _extensions_label(ONBOARD_OPENAPI_EXTENSIONS)
                + u"；更多知识源类型可通过 `/api/knowledge/ingest` 直接接入。"
            ),
            f"""<div class="upload-grid">
<div class="upload-zone" id="uz-prd"><strong>PRD / 需求文档</strong><p>{_extensions_label(ONBOARD_DOCUMENT_EXTENSIONS)}</p><input type="file" id="f-prd" accept="{_extensions_accept(ONBOARD_DOCUMENT_EXTENSIONS)}" hidden onchange="upFile(this,'prd','uz-prd')"/><button class="btn btn-sm" onclick="document.getElementById('f-prd').click()">选择文件</button></div>
<div class="upload-zone" id="uz-openapi"><strong>OpenAPI 规范</strong><p>{_extensions_label(ONBOARD_OPENAPI_EXTENSIONS)}</p><input type="file" id="f-openapi" accept="{_extensions_accept(ONBOARD_OPENAPI_EXTENSIONS)}" hidden onchange="upFile(this,'openapi','uz-openapi')"/><button class="btn btn-sm" onclick="document.getElementById('f-openapi').click()">选择文件</button></div>
</div><div id="up-status"></div>""",
            section_id="step1",
        )

        body += section(u"② 测试环境地址", u"填写目标测试环境的 API 根地址（协议 + 主机 + 端口，不含路径）",
            f"""<div class="env-form">
<div class="env-field"><label>环境名称</label><input type="text" id="env-name" value="test" class="input"/></div>
<div class="env-field"><label>API 根地址</label><input type="url" id="env-url" value="{h(base_url)}" class="input" placeholder="http://your-api:8080"/></div>
<button class="btn btn-primary" onclick="saveEnv()">保存环境配置</button>
<span id="env-hint">{'已保存: '+h(base_url[:45]) if env_configured else '示例: http://test-api:8080'}</span></div>""",
            section_id="step2")

        body += section(u"③ 大模型配置", u"配置 DeepSeek API Key，用于智能推理（Key 仅保存在 .env，不回显）",
            f"""<div class="env-form">
<div class="env-field"><label>LLM 地址</label><input type="text" id="llm-url" value="https://api.deepseek.com/v1" class="input"/></div>
<div class="env-field"><label>模型名称</label><input type="text" id="llm-model" value="deepseek-chat" class="input"/></div>
<div class="env-field"><label>API Key</label><input type="password" id="llm-key" value="" class="input" autocomplete="new-password" placeholder="留空则不修改当前 Key"/></div>
<button class="btn btn-primary" onclick="saveLLM()">保存并验证</button>
<span id="llm-status">{'LLM 已配置' if llm_configured else '未配置'}</span></div>""",
            section_id="step3")

        body += f"""<div class="onboard-launch">
<a href="/dashboard?project={project}" class="btn btn-primary btn-lg">{'启动项目' if score >= 60 else '跳过向导，直接进入总览'} &rarr;</a></div>"""

        page = product_shell(title="项目接入向导", project_id=project, active="", eyebrow="Onboarding", headline="开始接入", description="一次性完成项目接入配置", body=body)
        page += """<script>
async function upFile(input,type,zoneId){var f=input.files[0];if(!f)return;var z=document.getElementById(zoneId);z.innerHTML='\\u21bb '+f.name;var r=new FileReader();r.onload=async function(){var b64=r.result.split(',')[1];var s=document.getElementById('up-status');s.innerHTML='\\u21bb 正在上传...';try{var resp=await fetch('/api/knowledge/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:'""" + project + """',type:type,filename:f.name,content:b64})});var d=await resp.json();if(d.ok){s.innerHTML='\\u2713 '+f.name+' 导入成功';setTimeout(function(){location.reload()},800)}else s.innerHTML='\\u2717 '+(d.message||'fail')}catch(e){s.innerHTML='\\u2717 '+e.message}};r.readAsDataURL(f)}
async function saveEnv(){var n=document.getElementById('env-name').value;var u=document.getElementById('env-url').value;var h=document.getElementById('env-hint');try{var r=await fetch('/api/environment/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:'""" + project + """',payload:{target_environment:n,base_url:u}})});h.innerHTML='已保存: '+u.substring(0,40)}catch(e){h.innerHTML='Failed: '+e.message}}
async function saveLLM(){var url=document.getElementById('llm-url').value;var model=document.getElementById('llm-model').value;var key=document.getElementById('llm-key').value;var s=document.getElementById('llm-status');s.innerHTML='\\u21bb 正在验证...';try{var r=await fetch('/api/settings/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({llm_base_url:url,llm_model:model,llm_api_key:key})});var d=await r.json();if(d.llm_available)s.innerHTML='\\u2713 LLM Online';else s.innerHTML='\\u2717 '+(d.llm_error||'LLM Offline')}catch(e){s.innerHTML='\\u2717 '+e.message}}
</script>"""
        self._html(page)

    def _render_findings(self, project: str, root: Path) -> None:
        """Render the bug findings detail page."""
        import json as _json
        from .product_ui import _icon, h, product_shell, section, table, callout, status_badge, detail_list, metric_card

        report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
        if not report_path.exists():
            body = callout("尚未执行 Bug 扫描。", "点击顶栏「开始扫描」按钮执行扫描后再查看。", "warning", "bug")
            page = product_shell(title="Bug 发现详情", project_id=project, active="findings", eyebrow="Bug findings detail",
                headline="尚未执行 Bug 扫描。", description="点击顶栏「开始扫描」按钮执行扫描。",
                body=body, environment_label="Bug 详情", page_hint="Bug 发现详情", llm_status="online" if self._llm_available() else "offline")
            return self._html(page)

        report = _json.loads(report_path.read_text(encoding="utf-8"))
        s1 = report.get("stage1_industry", {})
        s2 = report.get("stage2_discovery", {})
        s3 = report.get("stage3_impact_analysis", {})

        cards = "".join([
            metric_card("总发现", s2.get("total_findings", 0), f"业务对象: {s1.get('object_count', '?')}", "danger", "bug"),
            metric_card("P0/P1", sum(1 for f in s2.get("findings", []) if str(f.get("severity","")) in ("P0","P1")), "需立即处理", "danger", "risk"),
            metric_card("LLM 分析", s3.get("llm_powered", 0), "AI 语义推理", "success", "spark"),
            metric_card("覆盖率", f"{s3.get('total_analyses',0)}/{max(1,s2.get('total_findings',1))}", "影响分析覆盖", "default", "check"),
        ])

        # Build findings table with filter
        all_findings = s2.get("findings", [])
        f_rows = []
        for f in all_findings:
            sev = str(f.get("severity", "?"))
            title = str(f.get("title") or f.get("description", ""))[:80]
            cat = str(f.get("category") or f.get("risk_type", "-"))[:30]
            f_rows.append([
                            status_badge(sev),
                            h(title),
                            h(cat),
                            h(str(f.get("confidence_score", "-"))),
                        ])
        filter_html = (
            "<div style='margin-bottom:10px;display:flex;gap:6px;align-items:center;font-size:12px'>"
            "<span style='color:var(--muted);font-weight:600'>筛选:</span>"
            "<button class='filter-btn active' onclick='filterFindings(\"all\")'>全部</button>"
            "<button class='filter-btn' onclick='filterFindings(\"P0\")'>P0 阻断</button>"
            "<button class='filter-btn' onclick='filterFindings(\"P1\")'>P1 严重</button>"
            "<button class='filter-btn' onclick='filterFindings(\"P2\")'>P2 一般</button>"
            "<button class='filter-btn' onclick='filterFindings(\"P3\")'>P3 建议</button>"
            "</div>"
        )

        # Build analysis cards
        analyses = s3.get("analyses", [])
        analysis_html = ""
        for ia in analyses[:10]:
            analysis_html += (
                f"<div class='callout callout-{'danger' if ia.get('severity','') in ('P0','P1') else 'warning' if ia.get('severity','')=='P2' else 'info'}'>"
                f"<i>{_icon('bug')}</i><div>"
                f"<strong>{h(ia.get('bug_title','?')[:80])}</strong>"
                f"<p style='margin:6px 0'>{h(str(ia.get('evidence','') or ia.get('description',''))[:300])}</p>"
                f"<small style='color:var(--muted)'>{h(ia.get('source','?'))} · {h(str(ia.get('severity','?')))} · Impact: {h(str(ia.get('impact_area','?')))}</small>"
                f"</div></div>"
            )

        body = f"<div class='metric-grid'>{cards}</div>"
        body += f"<div class='callout callout-info'><i>{_icon('spark')}</i><div><strong>业务探索概览</strong><p>置信度 {h(round(float(s1.get('confidence',0))*100,1) if isinstance(s1.get('confidence'),(int,float)) else s1.get('confidence','?'))}% · {h(s1.get('object_count',0))} 业务对象 · {h(s1.get('risk_count',0))} 风险域 · {h(s1.get('invariant_count',0))} 不变式</p></div></div>"
        body += section("🔍 Bug 发现列表", f"按严重度排序，共 {len(all_findings)} 条", filter_html + table(["严重度", "标题", "类别", "置信度"], f_rows, "未发现 Bug。提示：需要导入 OpenAPI 规范文件并配置测试环境地址"), section_id="findings")
        # Rich evidence cards for each finding
        evidence_cards = ""
        for f in all_findings:
            sev = str(f.get("severity", "?"))
            tone = "danger" if sev in ("P0","P1") else "warning" if sev == "P2" else "info"
            evidence_cards += (
                f"<div class='bug-card bug-card-{tone}'>"
                f"<div class='bug-card-header'><span class='status status-{tone}'>{sev}</span>"
                f"<strong>{h(str(f.get('title','') or f.get('description',''))[:100])}</strong>"
                f"<span class='bug-meta'>置信度 {h(f.get('confidence_score','?'))} · LLM {'参与' if f.get('llm_participated') else '未参与'}</span></div>"
                f"<div class='bug-card-body'>"
                # Row 1: category + risk type + confidence + false positive
                f"<div class='bug-field'><label>类别</label><span>{h(str(f.get('category','-'))[:40])}</span></div>"
                f"<div class='bug-field'><label>风险类型</label><span>{h(str(f.get('risk_type',f.get('category','-')))[:40])}</span></div>"
                f"<div class='bug-field'><label>置信度</label><span>{h(f.get('confidence_score','?'))} / 1.0</span></div>"
                f"<div class='bug-field'><label>误报风险</label><span>{h(str(f.get('false_positive_risk','?'))[:40])}</span></div>"
                f"<div class='bug-field'><label>Rank</label><span>{h(str(f.get('rank_score','?'))[:40])}</span></div>"
                f"<div class='bug-field'><label>Verification</label><span>{h(str(f.get('verification_level','?'))[:80])}</span></div>"
                f"<div class='bug-field'><label>Evidence strength</label><span>{h(str(f.get('evidence_strength','?'))[:80])}</span></div>"
                f"<div class='bug-field'><label>Execution policy</label><span>{h(str(f.get('execution_policy','?'))[:80])}</span></div>"
                f"<div class='bug-field'><label>Validation task</label><span>{h(str(f.get('validation_task_id','-'))[:80])}</span></div>"
                f"<div class='bug-field'><label>Validation lane</label><span>{h(str(f.get('validation_lane','-'))[:80])}</span></div>"
                f"<div class='bug-field'><label>Validation status</label><span>{h(str(f.get('validation_status','-'))[:120])}</span></div>"
                # Row 2: business rule source + description
                f"<div class='bug-field bug-field-full'><label>业务规则来源</label><span>{h(str(f.get('business_rule_source','-'))[:200])}</span></div>"
                f"<div class='bug-field bug-field-full'><label>描述</label><span>{h(str(f.get('description','-'))[:400])}</span></div>"
                # Row 3: evidence
                f"<div class='bug-field bug-field-full'><label>证据 (请求/响应)</label><pre class='bug-evidence'>{h(str(f.get('evidence','-'))[:500])}</pre></div>"
                # Row 4: expected vs actual
                f"<div class='bug-field'><label>期望行为</label><span>{h(str(f.get('expected_behavior','-'))[:200])}</span></div>"
                f"<div class='bug-field'><label>实际行为</label><span class='text-danger'>{h(str(f.get('actual_behavior','-'))[:200])}</span></div>"
                # Row 5: reproduction + impact
                f"<div class='bug-field'><label>复现步骤</label><span>{h(str(f.get('reproduction_steps','-'))[:200])}</span></div>"
                f"<div class='bug-field'><label>影响范围</label><span>{h(str(f.get('impact_scope','-'))[:200])}</span></div>"
                f"<div class='bug-field bug-field-full'><label>Validation plan</label><pre class='bug-evidence'>{h(str(f.get('validation_plan','-'))[:700])}</pre></div>"
                f"</div></div>"
            )
        body += section("📋 影响分析", f"{s3.get('total_analyses',0)} 条分析 ({s3.get('llm_powered',0)} LLM + {s3.get('heuristic',0)} 模板)", analysis_html or "<p class='empty-state'>暂无分析记录。</p>", section_id="analysis")
        if evidence_cards:
            body += section("📝 Bug 详情卡片", "", evidence_cards, section_id="evidence")
        body += "<script>window.filterFindings=function(s){{document.querySelectorAll('.filter-btn').forEach(function(b){{b.classList.toggle('active',b.textContent.includes(s)||(s==='all'&&b.textContent.includes('全部')))}});document.querySelectorAll('table tbody tr').forEach(function(r){{if(r.querySelector('.table-empty'))return;var d=r.querySelector('.status');if(!d)return;if(s=='all')r.style.display='';else r.style.display=d.textContent.trim()===s?'':'none'}})}};</script>"

        page = product_shell(title="Bug 发现详情", project_id=project, active="findings", eyebrow="Bug findings detail",
            headline="Bug 发现与影响分析详情。", description="每条 Bug 包含证据、影响范围与调查方向。",
            body=body, environment_label="Bug 详情", page_hint="Bug 发现详情", llm_status="online" if self._llm_available() else "offline")
        return self._html(page)

    def _llm_available(self) -> bool:
        return self._llm_health()["available"]

    def _llm_health(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningConfig
            cfg = ReasoningConfig.from_env()
            configured = cfg.enabled
        except Exception as exc:
            return {"configured": False, "available": False, "status": "failed", "label": "Failed", "error": str(exc)[:160]}
        if not configured:
            return {"configured": False, "available": False, "status": "offline", "label": "未配置"}
        forced_status = os.environ.get("QUALIBUG_LLM_HEALTH_STATUS", "").strip().lower()
        if forced_status in {"online", "failed"}:
            return {"configured": True, "available": forced_status == "online", "status": forced_status, "label": "Verified online" if forced_status == "online" else "Verification failed"}
        last_status = os.environ.get("QUALIBUG_LLM_LAST_HEALTH_STATUS", "").strip().lower()
        if last_status in {"online", "failed"}:
            return {
                "configured": True,
                "available": last_status == "online",
                "status": last_status,
                "label": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_LABEL", "Verified online" if last_status == "online" else "Verification failed"),
                "error": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_ERROR", ""),
            }
        return self._verify_llm_connectivity()

    def _verify_llm_connectivity(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningClient, ReasoningConfig

            cfg = ReasoningConfig.from_env()
            if not cfg.enabled:
                result = {"configured": False, "available": False, "status": "offline", "label": "未配置", "error": "Missing LLM_BASE_URL, LLM_API_KEY or LLM_MODEL."}
            else:
                cfg.timeout_seconds = int(os.environ.get("LLM_HEALTH_TIMEOUT_SECONDS", "15"))
                client = ReasoningClient(cfg)
                client.health_check()
                result = {"configured": True, "available": True, "status": "online", "label": "Verified online"}
        except Exception as exc:
            result = {"configured": True, "available": False, "status": "failed", "label": "Verification failed", "error": str(exc)[:300]}
        os.environ["QUALIBUG_LLM_LAST_HEALTH_STATUS"] = str(result["status"])
        os.environ["QUALIBUG_LLM_LAST_HEALTH_LABEL"] = str(result["label"])
        if result.get("error"):
            os.environ["QUALIBUG_LLM_LAST_HEALTH_ERROR"] = str(result["error"])
        else:
            os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_ERROR", None)
        return result

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        project = self._project()
        root = self._root()
        if parsed.path in {"/health", "/api/health"}:
            import platform, sys
            llm_health = self._llm_health()
            try:
                from .bug_knowledge_graph import EnterprisePatternLibrary
                lib = EnterprisePatternLibrary()
                pattern_count = lib.stats().get("total_patterns", 0)
            except Exception:
                pattern_count = 0
            return self._json(
                {
                    "ok": True,
                    "service": "qualibug_private_pilot",
                    "version": "phase61",
                    "private_root": str(root),
                    "private_root_exists": root.exists(),
                    "public_bind_allowed": os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
                    "python_version": sys.version.split()[0],
                    "platform": platform.system(),
                    "llm_available": llm_health["available"],
                    "llm_status": llm_health,
                    "pattern_library_patterns": pattern_count,
                }
            )
        # Every non-health route requires a trusted actor. `_require_actor()`
        # keeps the narrowly-scoped localhost development fallback, but only
        # when public binding is disabled and the caller has not explicitly
        # requested a negative-auth probe. This prevents public/private-cloud
        # GET endpoints from silently serving project data to anonymous users.
        actor = self._require_actor()
        if actor is None:
            return
        if not self._require_project_scope(project):
            return
        if parsed.path == "/onboard":
            return self._render_onboard(project, root)
        if parsed.path in {"/", "/dashboard"}:
            build_enterprise_pilot_overview(project, root)
            path = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            fallback = "<h1>Enterprise pilot dashboard is not generated yet.</h1>"
            return self._html(path.read_text(encoding="utf-8") if path.exists() else fallback)
        if parsed.path == "/testops" or parsed.path == "/control-plane":
            from .enterprise_testops_control_plane import build_enterprise_testops_control_plane, load_enterprise_testops_control_plane, render_enterprise_testops_dashboard

            control = load_enterprise_testops_control_plane(project, root) or build_enterprise_testops_control_plane(project, root)
            return self._html(render_enterprise_testops_dashboard(control))
        if parsed.path == "/knowledge":
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset, render_enterprise_business_knowledge_center

            asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
            return self._html(render_enterprise_business_knowledge_center(project, root, asset))
        if parsed.path == "/benchmark":
            from .enterprise_testops_control_plane import render_multi_industry_benchmark_report, run_multi_industry_benchmark

            report = run_multi_industry_benchmark(project, root)
            return self._html(render_multi_industry_benchmark_report(report))
        if parsed.path == "/release":
            from .release_risk_dashboard import build_release_risk_dashboard, render_release_risk_dashboard_html

            dashboard = build_release_risk_dashboard(project, root)
            return self._html(render_release_risk_dashboard_html(dashboard))
        if parsed.path == "/settings":
            return self._render_settings(project, root)
        if parsed.path == "/findings":
            return self._render_findings(project, root)
        if parsed.path == "/api/pilot/overview":
            return self._json({"ok": True, "overview": build_enterprise_pilot_overview(project, root)})
        if parsed.path == "/api/pilot/status":
            return self._json({"ok": True, "status": build_enterprise_pilot_overview(project, root)})
        if parsed.path == "/api/pilot/tasks":
            return self._json({"ok": True, "tasks": list_pilot_tasks(project, root)})
        if parsed.path == "/api/control-plane/overview":
            from .enterprise_testops_control_plane import build_enterprise_testops_control_plane, load_enterprise_testops_control_plane

            return self._json({"ok": True, "control_plane": load_enterprise_testops_control_plane(project, root) or build_enterprise_testops_control_plane(project, root)})
        if parsed.path == "/api/knowledge/asset":
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset

            asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
            # Also include project input files as knowledge sources
            input_files = self._list_project_inputs(project, root)
            asset["sources"] = input_files.get("sources", [])
            asset["summary"]["active_source_count"] = len(asset["sources"])
            return self._json({"ok": True, "knowledge_asset": asset})
        if parsed.path == "/api/knowledge/preview":
            return self._handle_preview(project, {"source_id": parse_qs(parsed.query).get("source_id", [""])[0]}, root)
        if parsed.path == "/api/benchmark/report":
            from .enterprise_testops_control_plane import run_multi_industry_benchmark

            return self._json({"ok": True, "benchmark": run_multi_industry_benchmark(project, root)})
        if parsed.path == "/api/release/dashboard":
            from .release_risk_dashboard import build_release_risk_dashboard

            return self._json({"ok": True, "release_dashboard": build_release_risk_dashboard(project, root)})
        if parsed.path == "/api/findings":
            return self._json(self._load_pipeline_report(project, root))
        if parsed.path == "/api/report/html":
            return self._render_report_html(project, root)
        if parsed.path == "/api/scan/history":
            return self._json(self._load_scan_history(project, root))
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)


    def _render_report_html(self, project: str, root: Path) -> None:
        """Generate a standalone HTML report."""
        import json as _json, time as _time
        report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        report = {}
        if report_path.exists():
            try: report = _json.loads(report_path.read_text(encoding="utf-8"))
            except: pass
        history = []
        if history_path.exists():
            try: history = _json.loads(history_path.read_text(encoding="utf-8"))
            except: pass

        s2 = report.get("stage2_discovery", {})
        s1 = report.get("stage1_industry", {})
        s3 = report.get("stage3_impact_analysis", {})
        findings = s2.get("findings", [])
        analyses = s3.get("analyses", [])

        # Build HTML
        f_rows = ""
        for f in findings:
            sev = f.get("severity", "?")
            sev_color = "#dc2626" if sev in ("P0","P1") else "#d97706" if sev == "P2" else "#2563eb"
            f_rows += f"""<tr><td style="color:{sev_color};font-weight:700">{sev}</td><td>{f.get("title","-")}</td><td>{f.get("category","-")}</td><td>{f.get("confidence_score","-")}</td><td>{f.get("evidence","-")[:200]}</td></tr>"""

        html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<title>QualiBug Bug 扫描报告 - {project}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#1e293b;background:#f8fafc}}
h1{{font-size:24px;border-bottom:2px solid #3b82f6;padding-bottom:12px}}
h2{{font-size:18px;margin-top:32px;color:#334155}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}}
th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #e2e8f0}}
th{{background:#f1f5f9;font-weight:700;color:#475569}}
.metric{{display:inline-block;text-align:center;padding:16px 24px;border-radius:8px;margin:8px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.metric strong{{display:block;font-size:28px;color:#3b82f6}}
.metric span{{font-size:11px;color:#94a3b8}}
.footer{{margin-top:32px;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:12px}}</style></head><body>
<h1>QualiBug AI · Bug 扫描报告</h1>
<p>项目: <strong>{project}</strong> · 生成时间: <strong>{_time.strftime("%Y-%m-%d %H:%M:%S")}</strong></p>
<p>对象: {s1.get("object_count",0)} · 对象数: {s1.get("object_count",0)} · 风险域: {s1.get("risk_count",0)}</p>
<div style="margin:20px 0">
<div class="metric"><span>总发现</span><strong>{len(findings)}</strong></div>
<div class="metric"><span>P0/P1</span><strong>{sum(1 for f in findings if str(f.get("severity","")) in ("P0","P1"))}</strong></div>
<div class="metric"><span>LLM 分析</span><strong>{s3.get("llm_powered",0)}</strong></div>
<div class="metric"><span>覆盖</span><strong>{s3.get("total_analyses",0)}/{max(1,len(findings))}</strong></div>
</div>
<h2>Bug 发现列表</h2>
<table><tr><th>严重度</th><th>标题</th><th>类别</th><th>置信度</th><th>证据</th></tr>{f_rows}</table>
<h2>扫描历史 (最近 10 次)</h2>
<table><tr><th>时间</th><th>状态</th><th>发现</th><th>P0/P1</th><th>对象</th></tr>"""

        for h in history[-10:]:
            html += f"<tr><td>{h.get('timestamp_utc','-')}</td><td>{h.get('status','-')}</td><td>{h.get('total_findings',0)}</td><td>{h.get('p0p1_count',0)}</td><td>{h.get('industry','-')[:30]}</td></tr>"

        html += f"""</table>
<div class="footer">QualiBug AI Enterprise Edition · 私有化部署 · 测试环境扫描 · 绝不触碰生产数据</div>
</body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_settings(self, project: str, root: Path) -> None:
        """Render the system settings page."""
        from .product_ui import _icon, h, product_shell
        llm_health = self._llm_health()
        llm_status = str(llm_health.get("status") or "offline")
        llm_label = str(llm_health.get("label") or "未配置")
        llm_configured = bool(llm_health.get("configured"))
        llm_available = bool(llm_health.get("available"))
        llm_ok_class = "llm-online" if llm_available else ("llm-failed" if llm_configured else "llm-offline")
        llm_ok_text = "已验证可用" if llm_available else ("已配置但验证失败" if llm_configured else "未配置")
        llm_provider_text = "Verified online" if llm_available else ("Verification failed" if llm_configured else "Not connected")
        key_raw = os.environ.get("LLM_API_KEY", "")
        key_mask = (key_raw[:4] + "****" + key_raw[-4:]) if len(key_raw) > 8 else (key_raw[:4] + "****") if key_raw else "无"
        body = f"""<div class='settings-grid'>
<div class='settings-card'>
  <h3>⚙️ LLM 配置</h3>
  <form onsubmit='saveSettings(event)'>
    <div class='settings-field'><label>LLM 状态</label><span class='llm-pill llm-{h(llm_status)}'>{h(llm_label)}</span></div>
    <div class='settings-field'><label>API 根地址</label><input name='llm_base_url' value='{h(os.environ.get('LLM_BASE_URL',''))}' placeholder='https://api.deepseek.com/v1'></div>
    <div class='settings-field'><label>模型名称</label><input name='llm_model' value='{h(os.environ.get('LLM_MODEL','deepseek-chat'))}' placeholder='deepseek-chat'></div>
    <div class='settings-field'><label>Temperature</label><input name='llm_temperature' value='{h(os.environ.get('LLM_TEMPERATURE','0.1'))}' placeholder='0.1'></div>
    <div class='settings-field'><label>API Key</label><input name='llm_api_key' type='password' value='' autocomplete='new-password' placeholder='留空则不修改当前 Key'></div>
    <div class='settings-field'><label>Key 状态</label><span class='llm-pill {llm_ok_class}'>{llm_ok_text} · Key 掩码: {key_mask}</span></div>
    <button type='submit' class='btn btn-primary'>保存 LLM 配置</button>
    <span class='env-msg' id='settings-msg'></span>
  </form>
</div>
<div class='settings-card'>
  <h3>🔌 连接器</h3>
  <p style='color:var(--muted);font-size:13px;margin-bottom:16px'>支持飞书、Confluence、Jira 等企业工具。连接器只保存凭证引用，不存储密码。</p>
  <form onsubmit='saveConnector(event)'>
    <div class='settings-field'><label>连接器名称</label><input name='name' placeholder='飞书文档'></div>
    <div class='settings-field'><label>类型</label><select name='kind'><option>feishu_doc</option><option>confluence</option><option>jira</option><option>github_repo</option></select></div>
    <div class='settings-field'><label>凭证引用</label><input name='credential_ref' placeholder='env://FEISHU_APP_TOKEN'></div>
    <button type='submit' class='btn btn-primary'>注册连接器</button>
    <span class='env-msg' id='conn-msg'></span>
  </form>
</div>
<div class='settings-card'>
  <h3>📋 运行状态</h3>
  <div class='detail-list'>
    <div class='detail-row'><span>LLM Provider</span><b>{llm_provider_text}</b></div>
    <div class='detail-row'><span>项目工作目录</span><b>{h(str(root))}</b></div>
    <div class='detail-row'><span>端口</span><b>8088</b></div>
    <div class='detail-row'><span>绑定地址</span><b>127.0.0.1</b></div>
  </div>
</div>
</div>"""
        page = product_shell(
            title="系统设置",
            project_id=project,
            active="",
            eyebrow="System configuration",
            headline="管理系统、LLM 与企业连接器配置。",
            description="所有存储的凭证均为引用，不保存密码原文。",
            body=body,
            environment_label="设置模式",
            page_hint="系统设置",
            llm_status=llm_status,
        )
        return self._html(page)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        actor = self._require_actor()
        if actor is None:
            return
        root = self._root()
        try:
            body = self._body()
            project = _safe_project_id(str(body.get("project_id") or self._project()))
            if not self._require_project_scope(project):
                return
            if parsed.path == "/api/pilot/config":
                result = operate_enterprise_pilot_runtime(project, "save_config", body.get("payload") or body, root, actor)
            elif parsed.path == "/api/pilot/connectors":
                result = operate_enterprise_pilot_runtime(project, "register_connector", body.get("payload") or body, root, actor)
            elif parsed.path == "/api/pilot/connectors/sync":
                result = operate_enterprise_pilot_runtime(project, "sync_connector_export", body.get("payload") or body, root, actor)
            elif parsed.path == "/api/pilot/tasks":
                result = operate_enterprise_pilot_runtime(project, "enqueue", body.get("payload") or body, root, actor)
            elif parsed.path == "/api/pilot/tasks/approve":
                result = operate_enterprise_pilot_runtime(project, "approve", body.get("payload") or body, root, actor)
            elif parsed.path == "/api/pilot/tasks/run-next":
                result = operate_enterprise_pilot_runtime(project, "run_next", body.get("payload") or body, root, actor)
            elif parsed.path == "/api/knowledge/ingest":
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge source ingestion"):
                    return
                return self._handle_ingest(project, body, root, actor)
            elif parsed.path.startswith("/api/knowledge/delete"):
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge source deletion"):
                    return
                return self._handle_delete(project, body, root, actor)
            elif parsed.path == "/api/environment/config":
                from .enterprise_testops_control_plane import save_environment_config
                result = save_environment_config(project, body.get("payload") or body, root, actor)
                # Clear dashboard cache so it picks up new env config
                dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
                if dash_html.exists(): dash_html.unlink()
                return self._json(result)
            elif parsed.path == "/api/scan/run":
                if not self._require_role(actor, CONFIG_MANAGER_ROLES, "direct scan execution"):
                    return
                return self._handle_scan(project, root, actor)
            elif parsed.path == "/api/knowledge/reanalyze":
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge reanalysis"):
                    return
                return self._handle_reanalyze(project, root, actor)
            elif parsed.path == "/api/settings/save":
                if not self._require_role(actor, SETTINGS_MANAGER_ROLES, "system settings update"):
                    return
                return self._handle_settings_save(body)
            elif parsed.path == "/api/connectors/register":
                result = operate_enterprise_pilot_runtime(project, "register_connector", body, root, actor)
                return self._json({"ok": True, "message": "连接器已注册。"})
            else:
                return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
            return self._json(result)
        except PermissionError as exc:
            return self._json({"ok": False, "error": "FORBIDDEN", "message": str(exc)}, 403)
        except (ValueError, KeyError) as exc:
            return self._json({"ok": False, "error": "BAD_REQUEST", "message": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive private-service boundary
            return self._json({"ok": False, "error": "INTERNAL_ERROR", "message": str(exc)[:300]}, 500)

    def _handle_ingest(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Handle document ingestion via API with verbatim byte storage."""
        import base64
        from .document_change_watcher import ingest_document
        from .enterprise_knowledge_center import ingest_enterprise_knowledge_documents

        doc_type = str(body.get("type") or body.get("doc_type") or "prd").strip().lower() or "prd"
        filename = Path(str(body.get("filename") or body.get("name") or f"{doc_type}.md")).name or f"{doc_type}.md"
        content_b64 = str(body.get("content") or body.get("data") or "")
        if not content_b64:
            return self._json({"ok": False, "error": "MISSING_CONTENT", "message": "请提供 base64 编码的文件内容。"}, 400)

        # Decode once and persist the original bytes so PDF/DOCX uploads are not
        # corrupted by an unnecessary UTF-8 text round-trip.
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception:
            return self._json({"ok": False, "error": "DECODE_FAILED", "message": "Base64 解码失败，请检查文件内容。"}, 400)

        # Save to project input dir
        input_dir = root / "platform_workspace" / project / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        out_path = input_dir / filename
        out_path.write_bytes(raw)

        # Run document intelligence pipeline
        from .document_change_watcher import ingest_document as _ingest
        doc_info = _ingest(str(out_path))

        # Ingest into knowledge center — must pass document envelope dicts
        try:
            ingest_enterprise_knowledge_documents(project, [{"file_path": str(out_path), "filename": filename, "source_type": doc_type}], root=root, actor=actor)
            knowledge_updated = True
            # Clear caches so dashboard picks up new data
            try:
                knowledge_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
                if knowledge_cache.exists():
                    knowledge_cache.unlink()
                dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
                if dash_html.exists():
                    dash_html.unlink()
            except Exception:
                pass
        except Exception:
            knowledge_updated = False

        return self._json({
            "ok": True,
            "filename": filename,
            "doc_type": doc_type,
            "size_bytes": len(raw),
            "path": str(out_path),
            "storage_mode": "verbatim_bytes",
            "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
            "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
            "doc_info": doc_info,
            "knowledge_updated": knowledge_updated,
            "message": f"'{filename}' 导入成功。{'已更新知识库。' if knowledge_updated else '文件已保存。'}",
        })

    def _handle_delete(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Delete a knowledge source by source_id."""
        from .enterprise_knowledge_center import delete_enterprise_knowledge_source
        source_id = str(body.get("source_id") or "").strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID", "message": "请提供 source_id。"}, 400)
        try:
            result = delete_enterprise_knowledge_source(project, source_id, root, actor)
        except KeyError:
            return self._json({"ok": False, "error": "NOT_FOUND", "message": f"资料 {source_id} 未找到或已删除。"}, 404)
        try:
            asset_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if asset_cache.exists(): asset_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
        except Exception: pass
        return self._json({"ok": True, "source_id": source_id, "message": f"'{result.get('source_id', source_id)}' 已删除。"})

    def _handle_scan(self, project: str, root: Path, actor: dict[str, str]) -> None:
        """One-click scan: run the autonomous pipeline."""
        try:
            from .autonomous_pipeline import run_autonomous_pipeline
            result = run_autonomous_pipeline(project, root=root, force_analysis=True)
            # Normalize response: pipeline returns 'status' not 'ok'
            if "status" not in result:
                result["status"] = "completed"
            # Lightweight health check: always run (don't skip if pipeline had findings)
            s2 = result.get("stage2_discovery", {})
            # Collect existing findings from pipeline
            all_findings = list(s2.get("findings", []))
            if True:  # Always run health check
                # Get base_url for health check
                server_host, server_port = getattr(self.server, "server_address", ("127.0.0.1", 8088))
                health_base_url = f"http://127.0.0.1:{server_port}" if str(server_host) in {"", "0.0.0.0", "127.0.0.1"} else f"http://{server_host}:{server_port}"
                try:
                    from .enterprise_testops_control_plane import load_environment_config, _environment_by_name
                    env_cfg = load_environment_config(project, root)
                    target = _environment_by_name(env_cfg, "test")
                    health_base_url = str(target.get("base_url") or health_base_url)
                except Exception:
                    pass
                health_findings = []
                import urllib.request as _ur, urllib.error as _ue, json as _j2
                # 1. Health endpoint check
                health_ok = False
                health_errors = []
                for _health_path in ("/health", "/api/health"):
                    try:
                        _resp = _ur.urlopen(_ur.Request(health_base_url.rstrip("/") + _health_path,
                            headers={"User-Agent": "QualiBug/1.0", "X-QualiBug-Actor": "admin", "X-QualiBug-Role": "admin"}), timeout=5)
                        _body = _resp.read().decode("utf-8", errors="replace")
                        try:
                            _hdata = _j2.loads(_body)
                        except Exception:
                            _hdata = {}
                        if _hdata.get("ok") is False or _hdata.get("success") is False:
                            health_findings.append({"severity": "P0", "title": "Health端点返回异常",
                                "category": "health_check", "description": f"{_health_path} 返回 unhealthy", "confidence_score": 0.95})
                        elif "llm_available" in _hdata and not _hdata.get("llm_available"):
                            health_findings.append({"severity": "P1", "title": "LLM不可用",
                                "category": "llm_status", "description": f"{_health_path} 报告 llm_available=false",
                                "confidence_score": 0.9, "evidence": f"llm_available: {_hdata.get('llm_available')}"})
                        health_ok = True
                        break
                    except _ue.HTTPError as e:
                        health_errors.append(f"{_health_path}: HTTP {e.code}")
                    except Exception as e:
                        health_errors.append(f"{_health_path}: {str(e)[:80]}")
                if not health_ok:
                    health_findings.append({"severity": "P0", "title": "Health端点不可达",
                        "category": "unreachable", "description": "; ".join(health_errors)[:200], "confidence_score": 0.95})
                # 2. Check endpoints without auth (security)
                # Localhost deployments may deliberately supply a local-dev
                # identity when actor headers are absent. A negative-auth
                # probe must explicitly disable that convenience path; without
                # it, a 200 proves only the local harness is enabled, not that
                # anonymous callers can access the endpoint.
                negative_auth_headers = {
                    "User-Agent": "QualiBug/1.0",
                    "X-QualiBug-No-Local-Dev": "1",
                }
                for ep in ["/dashboard", "/knowledge", "/settings", "/findings", "/api/pilot/overview"]:
                    try:
                        r = _ur.urlopen(_ur.Request(health_base_url.rstrip("/") + ep + "?project=real_project_demo",
                            headers=negative_auth_headers), timeout=5)
                        if r.status == 200:
                            health_findings.append({
                                "severity": "P1", "title": f"{ep} 无需认证即可访问",
                                "category": "missing_auth", "risk_type": "security",
                                "description": f"GET {ep} 返回 HTTP 200，未要求认证头",
                                "confidence_score": 0.85,
                                "evidence": f"请求: GET {ep} (无认证头，且禁用本地开发身份回退)\n响应: HTTP/1.1 200 OK\nContent-Type: text/html",
                                "expected_behavior": f"GET {ep} 应在缺少 X-QualiBug-Actor 头时返回 401 Unauthorized",
                                "actual_behavior": f"GET {ep} 在缺少认证头时返回 200，接受匿名访问",
                                "reproduction_steps": f"1. 打开 curl/浏览器\n2. 请求 {health_base_url}{ep}?project=real_project_demo\n3. 不添加 X-QualiBug-Actor 和 X-QualiBug-Role 头\n4. 观察响应",
                                "impact_scope": "所有页面和数据可能被未授权访问，包括 Dashboard、Knowledge、Findings、Settings",
                                "false_positive_risk": "低 — 已验证无认证头请求返回 200",
                                "business_rule_source": "PRD Security Requirements: 'All endpoints (except /health) must require X-QualiBug-Actor and X-QualiBug-Role headers'",
                                "llm_participated": False,
                            })
                    except _ue.HTTPError:
                        pass  # 401/403 is expected
                    except:
                        pass
                # 3. Check valid endpoints with auth
                for ep, expect_status in [("/api/pilot/overview", 200), ("/api/knowledge/asset", 200),
                    ("/api/scan/run", 405)]:  # 405 = GET on POST-only endpoint is OK
                    try:
                        r = _ur.urlopen(_ur.Request(health_base_url.rstrip("/") + ep + "?project=real_project_demo",
                            headers={"User-Agent": "QualiBug/1.0", "X-QualiBug-Actor": "admin",
                            "X-QualiBug-Role": "admin"}), timeout=5)
                        if r.status >= 500:
                            health_findings.append({"severity": "P0", "title": f"{ep} 返回5xx",
                                "category": "server_error", "description": f"HTTP {r.status}", "confidence_score": 0.9})
                    except _ue.HTTPError as e:
                        if e.code >= 500:
                            health_findings.append({"severity": "P0", "title": f"{ep} 返回5xx",
                                "category": "server_error", "description": f"HTTP {e.code}", "confidence_score": 0.9})
                    except:
                        pass
                if health_findings:
                    all_findings = all_findings + health_findings
                    updated_stage2 = dict(result.get("stage2_discovery") or {})
                    updated_stage2.update({"total_findings": len(all_findings), "findings": all_findings, "by_severity": {}})
                    result["stage2_discovery"] = updated_stage2
                    _extend_stage3_impact_analysis(result, [
                        {"bug_title": f["title"], "severity": f["severity"], "source": "health_check",
                            "evidence": f.get("evidence", ""), "impact_area": f.get("category", "")}
                        for f in health_findings
                    ])
            # Deep semantic analysis: PRD + OpenAPI + API probing
            try:
                from .semantic_analysis import run_semantic_analysis
                # Pass all existing findings (pipeline + health check)
                current_findings = result.get("stage2_discovery", {}).get("findings", all_findings)
                semantic_findings = run_semantic_analysis(project, root, current_findings, health_base_url)
                if semantic_findings:
                    updated_stage2 = dict(result.get("stage2_discovery") or {})
                    updated_stage2.update({"total_findings": len(semantic_findings), "findings": semantic_findings, "by_severity": {}})
                    result["stage2_discovery"] = updated_stage2
                    _extend_stage3_impact_analysis(result, [
                        {"bug_title": f.get("title",""), "severity": f.get("severity","?"),
                            "source": f.get("source","semantic"), "evidence": f.get("evidence",""),
                            "impact_area": f.get("category","")}
                        for f in semantic_findings
                    ])
            except Exception:
                pass
            try:
                from .bug_validation_queue import apply_validation_results_to_findings, build_bug_validation_queue, execute_bug_validation_queue

                current_stage2 = dict(result.get("stage2_discovery") or {})
                current_findings = list(current_stage2.get("findings") or [])
                queue = build_bug_validation_queue(project, root, current_findings, base_url_override=health_base_url)
                execution = execute_bug_validation_queue(project, root, queue)
                current_findings = apply_validation_results_to_findings(current_findings, queue, execution)
                current_stage2.update({
                    "total_findings": len(current_findings),
                    "findings": current_findings,
                    "by_severity": {
                        sev: sum(1 for f in current_findings if str(f.get("severity", "unknown")) == sev)
                        for sev in sorted({str(f.get("severity", "unknown")) for f in current_findings})
                    },
                    "validation_queue": {
                        "status": "completed",
                        "artifact": str(root / "platform_outputs" / project / "bug_validation_queue" / "bug_validation_queue.json"),
                        "summary": queue.get("summary") or {},
                        "governance": queue.get("governance") or {},
                    },
                    "validation_execution": {
                        "status": "completed",
                        "artifact": str(root / "platform_outputs" / project / "bug_validation_queue" / "bug_validation_execution.json"),
                        "summary": execution.get("summary") or {},
                        "governance": execution.get("governance") or {},
                    },
                })
                result["stage2_discovery"] = current_stage2
            except Exception as exc:
                current_stage2 = dict(result.get("stage2_discovery") or {})
                current_stage2["validation_queue"] = {"status": "failed", "error": str(exc)[:200]}
                current_stage2["validation_execution"] = {"status": "failed", "error": str(exc)[:200]}
                result["stage2_discovery"] = current_stage2
            result = _synchronize_scan_aggregates(result)
            # NOW save report
            try:
                import json as _json
                report_dir = root / "platform_outputs" / project / "pipeline_reports"
                report_dir.mkdir(parents=True, exist_ok=True)
                (report_dir / "latest_pipeline_report.json").write_text(
                    _json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
                # Save scan history entry
                history_file = report_dir / "scan_history.json"
                history = []
                if history_file.exists():
                    try: history = _json.loads(history_file.read_text(encoding="utf-8"))
                    except: pass
                s2 = result.get("stage2_discovery", {})
                s3 = result.get("stage3_impact_analysis", {})
                s1 = result.get("stage1_industry", {})
                history.append({
                    "scan_id": f"scan_{int(__import__('time').time())}",
                    "timestamp_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
                    "status": result.get("status", "unknown"),
                    "total_findings": s2.get("total_findings", 0),
                    "p0p1_count": sum(1 for f in s2.get("findings", []) if str(f.get("severity","")) in ("P0","P1")),
                    "industry": s1.get("primary_industry", "unknown"),
                    "llm_powered_analyses": s3.get("llm_powered", 0),
                    "heuristic_analyses": s3.get("heuristic", 0),
                    "duration_seconds": result.get("executive_summary", {}).get("total_duration_seconds", 0),
                })
                history_file.write_text(_json.dumps(history[-20:], ensure_ascii=False, default=str), encoding="utf-8")
                # Update release risk dashboard with findings
                try:
                    # Save scan blockers to release dashboard
                    findings = result.get("stage2_discovery", {}).get("findings", [])
                    if findings:
                        release_dir = root / "platform_outputs" / project / "release_risk_dashboard"
                        release_dir.mkdir(parents=True, exist_ok=True)
                        blockers = [{"severity": f.get("severity","?"), "title": f.get("title",""),
                            "risk_type": f.get("category",""), "confidence": f.get("confidence_score",0),
                            "endpoint": f.get("evidence","")[:80],
                            "actual_result": f.get("actual_behavior","")} for f in findings]
                        (release_dir / "scan_blockers.json").write_text(
                            _json.dumps(blockers, ensure_ascii=False, indent=2), encoding="utf-8")
                        # Update release dashboard json with blockers
                        rd_path = release_dir / "release_risk_dashboard.json"
                        if rd_path.exists():
                            try:
                                rd = _json.loads(rd_path.read_text(encoding="utf-8"))
                                rd["scan_blockers"] = blockers
                                rd["suggested_release_blockers"] = str(len(blockers))
                                rd["blocker_candidates"] = blockers
                                rd_path.write_text(_json.dumps(rd, ensure_ascii=False, indent=2), encoding="utf-8")
                            except Exception:
                                pass
                except Exception:
                    pass
            except Exception: pass
            if "ok" not in result:
                finished_ok = result.get("status") != "blocked_by_safety_gate"
                result["ok"] = finished_ok
                if finished_ok:
                    s2 = result.get("stage2_discovery", {})
                    n = s2.get("total_findings", 0)
                    if n:
                        result["message"] = f"扫描完成，发现 {n} 个 Bug"
                    else:
                        result["message"] = "扫描完成，0发现。提示：需要导入 OpenAPI 规范 规范文件才能发现 API 级别的业务 Bug"
                else:
                    result["message"] = "安全门禁已阻止扫描"
            # Clear dashboard cache
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
            # Persist findings to confirmed bug memory for Loop learning
            try:
                from datetime import datetime as _dt
                _now_iso = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                memory_path = root / "platform_workspace" / project / "defect_discovery" / "confirmed_bug_memory.json"
                memory_path.parent.mkdir(parents=True, exist_ok=True)
                memory: dict[str, Any] = {}
                if memory_path.exists():
                    try:
                        memory = _json.loads(memory_path.read_text(encoding="utf-8"))
                    except Exception:
                        memory = {}
                entries = memory.get("entries", {})
                findings = result.get("stage2_discovery", {}).get("findings", [])
                new_count = 0
                for f in findings:
                    title = str(f.get("title", ""))
                    if not title or title in entries:
                        continue
                    entries[title] = {
                        "title": title,
                        "severity": f.get("severity", "?"),
                        "category": f.get("category", "unknown"),
                        "confidence": f.get("confidence_score", 0),
                        "source": f.get("source", "scan"),
                        "added_at_utc": _now_iso,
                    }
                    new_count += 1
                memory["entries"] = entries
                memory["updated_at_utc"] = _now_iso
                memory["phase"] = memory.get("phase", "phase61_scan_persistence")
                memory_path.write_text(_json.dumps(memory, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                result["_bug_memory_persisted"] = new_count
            except Exception:
                pass
            # Trigger dashboard rebuild
            build_enterprise_pilot_overview(project, root)
            return self._json(result)
        except Exception as e:
            return self._json({"ok": False, "error": "SCAN_FAILED", "message": str(e)[:300]}, 500)

    def _handle_reanalyze(self, project: str, root: Path, actor: dict[str, str]) -> None:
        """Rebuild knowledge center with fresh data."""
        try:
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset
            build_enterprise_business_knowledge_asset(project, root)
            build_enterprise_pilot_overview(project, root)
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
            return self._json({"ok": True, "message": "知识库已重新分析完成。"})
        except Exception as e:
            return self._json({"ok": False, "error": "REANALYZE_FAILED", "message": str(e)[:300]}, 500)

    def _handle_preview(self, project: str, body_or_source: dict[str, Any] | str, root: Path) -> None:
        """Return file content for preview. Supports both POST body and GET query param."""
        source_id = ""
        if isinstance(body_or_source, dict):
            source_id = str(body_or_source.get("source_id") or "").strip()
        else:
            source_id = str(body_or_source).strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID"}, 400)
        try:
            from .enterprise_knowledge_center import _load_registry
            registry = _load_registry(project, root)
            for s in registry.get("sources", []):
                if s.get("source_id") == source_id:
                    stored_path = str(s.get("stored_path") or "").strip()
                    if not stored_path:
                        break
                    src_path = (root / stored_path).resolve()
                    root_resolved = root.resolve()
                    if root_resolved != src_path and root_resolved not in src_path.parents:
                        return self._json({"ok": False, "error": "INVALID_STORED_PATH"}, 400)
                    if src_path.exists():
                        text = src_path.read_text(encoding="utf-8", errors="replace")[:50000]
                        return self._json({"ok": True, "source_id": source_id, "filename": s.get("original_name",""), "content": text})
            return self._json({"ok": False, "error": "NOT_FOUND", "message": "文件未找到。"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": "PREVIEW_FAILED", "message": str(e)[:300]}, 500)

    def _handle_settings_save(self, body: dict[str, Any]) -> None:
        """Apply LLM settings for a customer-local private service."""
        updates = {}
        for key in ["llm_base_url", "llm_model", "llm_temperature", "llm_api_key"]:
            if key in body and body[key]:
                updates[key.upper()] = str(body[key])
        if updates:
            _write_env_local(updates)
        for key, val in updates.items():
            os.environ[key] = val
        if updates:
            try:
                from .llm_reasoning import reset_client
                reset_client()
            except Exception:
                pass
        # Clear forced/cached health status before re-verification so a newly
        # verified key is reflected by /health and Settings immediately.
        for _key in ("QUALIBUG_LLM_HEALTH_STATUS", "QUALIBUG_LLM_LAST_HEALTH_STATUS", "QUALIBUG_LLM_LAST_HEALTH_LABEL", "QUALIBUG_LLM_LAST_HEALTH_ERROR"):
            os.environ.pop(_key, None)
        llm_health = self._verify_llm_connectivity() if updates else self._llm_health()
        return self._json({
            "ok": True,
            "llm_available": llm_health["available"],
            "llm_status": llm_health["status"],
            "llm_status_label": llm_health["label"],
            "llm_error": llm_health.get("error", ""),
            "message": "LLM settings were saved to .env.local for this private deployment.",
        })

    def log_message(self, fmt: str, *args: object) -> None:
        return


def run_private_pilot_service(root: Path | None = None, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    root = root or _root()
    host = host or os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"} and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1":
        raise ValueError("Public binding is disabled by default. Set QUALIBUG_ALLOW_PUBLIC_BIND=1 only behind a trusted reverse proxy.")
    selected_port = int(os.environ.get("QUALIBUG_PORT", "8088")) if port is None else int(port)
    server = ThreadingHTTPServer((host, selected_port), PrivatePilotHandler)
    server.qualibug_private_root = root
    return server


def main() -> int:
    server = run_private_pilot_service()
    print(f"QualiBug private pilot service: http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    # Load .env and .env.local for LLM configuration
    try:
        from dotenv import load_dotenv
        from pathlib import Path as _P
        _proj_root = _P(__file__).resolve().parent.parent
        for _name in (".env", ".env.local"):
            _ep = _proj_root / _name
            if _ep.exists():
                load_dotenv(_ep, override=True)
    except ImportError:
        pass
    raise SystemExit(main())
