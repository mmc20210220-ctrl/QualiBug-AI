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
        """Render the customer-safe HTML report and persist the delivery guard."""
        from .customer_delivery_guard import persist_customer_delivery_guard
        from .customer_report_boundary import sanitize_customer_report_html
        from .customer_safe_report import render_customer_safe_report_html

        html = sanitize_customer_report_html(render_customer_safe_report_html(project, root))
        try:
            persist_customer_delivery_guard(project, root)
        except Exception:
            # Report rendering must remain available; the HTML still shows the
            # human-readable release gate and handoff boundary if guard persistence
            # fails due to filesystem permissions or a transient write error.
            pass
        return self._html(html)


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
