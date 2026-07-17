"""Legacy HTML page renders and SPA static serving for PrivatePilotHandler."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .private_pilot_project_assets import _known_project_exists


class PageRenderMixin:
    def _load_scan_history(self, project: str, root: Path) -> dict[str, Any]:
        """Load scan history from disk."""
        import json as _json
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        if not history_path.exists():
            latest_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
            if latest_path.exists():
                try:
                    latest = _json.loads(latest_path.read_text(encoding="utf-8"))
                except Exception:
                    latest = {}
                return {
                    "ok": True,
                    "history": [latest],
                    "compatibility_mode": "legacy_findings_report_v1",
                    "canonical_api_family": "/api/v1/projects/{projectId}/*",
                }
            return {"ok": True, "history": []}
        try:
            return {"ok": True, "history": _json.loads(history_path.read_text(encoding="utf-8"))}
        except Exception:
            return {"ok": True, "history": []}

    def _list_project_inputs(self, project: str, root: Path) -> dict[str, Any]:
        """List project input files as knowledge sources from disk."""
        import json as _json, os as _os, time as _time
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
                        "created_at_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(f.stat().st_mtime)),
                        "uploaded_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(f.stat().st_mtime)),
                    })
        return {"ok": True, "sources": sources}

    def _render_onboard(self, project: str, root: Path) -> None:
        """Render a minimal project onboarding page."""
        from .product_ui import product_shell, section

        known = _known_project_exists(root, project)
        status = "Known project" if known else "Project has not been imported yet"
        body = section(
            "Project onboarding",
            "Import PRD, OpenAPI and business documents, then configure the target environment before running discovery.",
            f"<p class='text-muted'>{status}</p><p><a class='btn btn-primary' href='/materials?project={project}'>Open materials</a> <a class='btn btn-secondary' href='/settings?project={project}'>Open settings</a></p>",
            section_id="onboarding",
        )
        page = product_shell(
            title="Project onboarding",
            project_id=project,
            active="",
            eyebrow="Onboarding",
            headline="Start project onboarding",
            description="Complete the minimum inputs required for grounded discovery.",
            body=body,
        )
        self._html(page)

    def _render_findings(self, project: str, root: Path) -> None:
        """Render a minimal findings page for the private pilot server."""
        from .product_ui import product_shell, section

        body = section(
            "Findings",
            "Open the product frontend for the full evidence chain and remediation workflow.",
            f"<p><a class='btn btn-primary' href='/findings?project={project}'>Open findings</a> <a class='btn btn-secondary' href='/evidence?project={project}'>Open evidence</a></p>",
            section_id="findings",
        )
        page = product_shell(
            title="Findings",
            project_id=project,
            active="findings",
            eyebrow="Evidence",
            headline="Validated findings",
            description="Customer-safe summary of validated product risks.",
            body=body,
        )
        self._html(page)

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
<title>QualiBug Bug 鎵弿鎶ュ憡 - {project}</title>
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
<h1>QualiBug AI 路 Bug 鎵弿鎶ュ憡</h1>
<p>椤圭洰: <strong>{project}</strong> 路 鐢熸垚鏃堕棿: <strong>{_time.strftime("%Y-%m-%d %H:%M:%S")}</strong></p>
<p>瀵硅薄: {s1.get("object_count",0)} 路 瀵硅薄鏁? {s1.get("object_count",0)} 路 椋庨櫓鍩? {s1.get("risk_count",0)}</p>
<div style="margin:20px 0">
<div class="metric"><span>鎬诲彂鐜?/span><strong>{len(findings)}</strong></div>
<div class="metric"><span>P0/P1</span><strong>{sum(1 for f in findings if str(f.get("severity","")) in ("P0","P1"))}</strong></div>
<div class="metric"><span>LLM 鍒嗘瀽</span><strong>{s3.get("llm_powered",0)}</strong></div>
<div class="metric"><span>瑕嗙洊</span><strong>{s3.get("total_analyses",0)}/{max(1,len(findings))}</strong></div>
</div>
<h2>Bug 鍙戠幇鍒楄〃</h2>
<table><tr><th>涓ラ噸搴?/th><th>鏍囬</th><th>绫诲埆</th><th>缃俊搴?/th><th>璇佹嵁</th></tr>{f_rows}</table>
<h2>鎵弿鍘嗗彶 (鏈€杩?10 娆?</h2>
<table><tr><th>鏃堕棿</th><th>鐘舵€?/th><th>鍙戠幇</th><th>P0/P1</th><th>瀵硅薄</th></tr>"""

        for h in history[-10:]:
            html += f"<tr><td>{h.get('timestamp_utc','-')}</td><td>{h.get('status','-')}</td><td>{h.get('total_findings',0)}</td><td>{h.get('p0p1_count',0)}</td><td>{h.get('industry','-')[:30]}</td></tr>"

        html += f"""</table>
<div class="footer">QualiBug AI Enterprise Edition 路 绉佹湁鍖栭儴缃?路 娴嬭瘯鐜鎵弿 路 缁濅笉瑙︾鐢熶骇鏁版嵁</div>
</body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_settings(self, project: str, root: Path) -> None:
        """Render a minimal settings page for the private pilot server."""
        from .product_ui import product_shell, section, h

        llm_health = self._llm_health()
        llm_status = str(llm_health.get("status") or "offline")
        llm_label = str(llm_health.get("label") or "Not configured")
        body = section(
            "System settings",
            "Use the product frontend for full customer, topology, connector and LLM configuration.",
            f"<p>LLM status: <strong>{h(llm_label)}</strong> ({h(llm_status)})</p><p><a class='btn btn-primary' href='/settings?project={project}'>Open settings</a></p>",
            section_id="settings",
        )
        page = product_shell(
            title="System settings",
            project_id=project,
            active="settings",
            eyebrow="Settings",
            headline="System configuration",
            description="Secrets are never rendered back to the browser.",
            body=body,
            llm_status=llm_status,
        )
        self._html(page)

    def _serve_frontend(self, parsed: "urllib.parse.ParseResult", root: Path) -> None:
        """Serve the prebuilt customer pilot SPA (frontend/dist). Public — called before
        the auth gate so the login page itself is reachable. Path-traversal hardened."""
        import mimetypes
        _dist_env = os.environ.get("QUALIBUG_FRONTEND_DIST")
        _dist = Path(_dist_env) if _dist_env else (Path(__file__).resolve().parent.parent / "frontend" / "dist")
        _dist_resolved = _dist.resolve()
        _rel = parsed.path.lstrip("/")
        if _rel in ("", "index.html"):
            _target = _dist_resolved / "index.html"
        elif _rel.startswith("assets/"):
            _target = (_dist_resolved / _rel).resolve()
        else:
            # SPA client-side route (e.g. /login, /settings, /scan) -> shell
            _target = _dist_resolved / "index.html"
        if _target != _dist_resolved and _dist_resolved not in _target.parents:
            return self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        if not _target.exists() or not _target.is_file():
            return self._json({"ok": False, "error": "UI_NOT_BUILT",
                               "message": "frontend/dist 未构建，请先构建前端或设置 QUALIBUG_FRONTEND_DIST。"}, 404)
        try:
            _data = _target.read_bytes()
        except Exception:
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        _ctype = mimetypes.guess_type(str(_target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", _ctype)
        self.send_header("Content-Length", str(len(_data)))
        self.end_headers()
        self.wfile.write(_data)
