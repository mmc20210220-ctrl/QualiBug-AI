"""Legacy page renders and SPA static serving."""
from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .private_pilot_project_assets import _known_project_exists


def _within(candidate: Path, allowed: Path) -> bool:
    try:
        candidate.resolve().relative_to(allowed.resolve())
        return True
    except (OSError, ValueError):
        return False


class PageRenderMixin:
    def _load_scan_history(self, project: str, root: Path) -> dict[str, Any]:
        history_path = (
            root
            / "platform_outputs"
            / project
            / "pipeline_reports"
            / "scan_history.json"
        )
        if history_path.exists() and history_path.is_file():
            try:
                payload = json.loads(history_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return {
                    "ok": False,
                    "error": "SCAN_HISTORY_INVALID",
                    "message": str(exc)[:300],
                    "history": [],
                }
            if not isinstance(payload, list):
                return {
                    "ok": False,
                    "error": "SCAN_HISTORY_INVALID",
                    "history": [],
                }
            return {"ok": True, "history": payload}
        latest_path = (
            root
            / "platform_outputs"
            / project
            / "pipeline_reports"
            / "latest_pipeline_report.json"
        )
        if not latest_path.exists() or not latest_path.is_file():
            return {"ok": True, "history": []}
        try:
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "error": "LATEST_PIPELINE_REPORT_INVALID",
                "message": str(exc)[:300],
                "history": [],
            }
        return {
            "ok": True,
            "history": [latest] if isinstance(latest, dict) else [],
            "compatibility_mode": "legacy_findings_report_v1",
            "canonical_api_family": "/api/v1/projects/{projectId}/*",
        }

    def _list_project_inputs(self, project: str, root: Path) -> dict[str, Any]:
        """List only files physically contained in this project's input root."""

        project_input = (root / "platform_inputs" / project).resolve()
        if not project_input.exists() or not project_input.is_dir():
            return {"ok": True, "sources": []}
        source_dir = project_input
        config_path = project_input / "real_project_config.json"
        ignored_external_dataset = ""
        if config_path.exists() and config_path.is_file():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                config = {}
            if isinstance(config, dict):
                declared = str(config.get("source_dataset") or "").strip()
                if declared:
                    candidate = Path(declared)
                    if not candidate.is_absolute():
                        candidate = project_input / candidate
                    candidate = candidate.resolve()
                    if _within(candidate, project_input) and candidate.is_dir():
                        source_dir = candidate
                    else:
                        ignored_external_dataset = declared

        sources: list[dict[str, Any]] = []
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if not _within(path, project_input):
                continue
            extension = path.suffix.lower()
            name = path.name.lower()
            if extension == ".md":
                source_type = "PRD" if "prd" in name else "业务文档"
            elif extension in {".yaml", ".yml", ".json"}:
                source_type = "OpenAPI" if "openapi" in name else "规范文件"
            elif extension == ".sql":
                source_type = "数据库 Schema"
            else:
                source_type = "业务文档"
            stat = path.stat()
            relative = path.relative_to(project_input).as_posix()
            sources.append(
                {
                    "source_id": f"input-{relative}",
                    "filename": relative,
                    "source_type": source_type,
                    "status": "active",
                    "size_bytes": stat.st_size,
                    "created_at_utc": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)
                    ),
                    "uploaded_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)
                    ),
                }
            )
        return {
            "ok": True,
            "sources": sources,
            "external_source_dataset_ignored": ignored_external_dataset,
            "source_root": str(source_dir.relative_to(root.resolve())).replace("\\", "/"),
        }

    def _render_onboard(self, project: str, root: Path) -> None:
        from .product_ui import product_shell, section

        known = _known_project_exists(root, project)
        status = "Known project" if known else "Project has not been imported yet"
        body = section(
            "Project onboarding",
            "Import PRD, OpenAPI and business documents, then configure the target environment before running discovery.",
            (
                f"<p class='text-muted'>{status}</p>"
                f"<p><a class='btn btn-primary' href='/materials?project={project}'>Open materials</a> "
                f"<a class='btn btn-secondary' href='/settings?project={project}'>Open settings</a></p>"
            ),
            section_id="onboarding",
        )
        return self._html(
            product_shell(
                title="Project onboarding",
                project_id=project,
                active="",
                eyebrow="Onboarding",
                headline="Start project onboarding",
                description="Complete the minimum grounded inputs.",
                body=body,
            )
        )

    def _render_findings(self, project: str, root: Path) -> None:
        del root
        from .product_ui import product_shell, section

        body = section(
            "Findings",
            "Open the product frontend for the full evidence chain and replay workflow.",
            (
                f"<p><a class='btn btn-primary' href='/findings?project={project}'>Open findings</a> "
                f"<a class='btn btn-secondary' href='/evidence?project={project}'>Open evidence</a></p>"
            ),
            section_id="findings",
        )
        return self._html(
            product_shell(
                title="Findings",
                project_id=project,
                active="findings",
                eyebrow="Evidence",
                headline="Validated findings",
                description="Customer-safe validated product risks.",
                body=body,
            )
        )

    def _render_report_html(self, project: str, root: Path) -> None:
        from .customer_delivery_guard import persist_customer_delivery_guard
        from .customer_report_boundary import sanitize_customer_report_html
        from .customer_safe_report import render_customer_safe_report_html

        html = sanitize_customer_report_html(
            render_customer_safe_report_html(project, root)
        )
        persist_customer_delivery_guard(project, root)
        return self._html(html)

    def _render_settings(self, project: str, root: Path) -> None:
        del root
        from .product_ui import h, product_shell, section

        llm_health = self._llm_health()
        llm_status = str(llm_health.get("status") or "offline")
        llm_label = str(llm_health.get("label") or "Not configured")
        body = section(
            "System settings",
            "Use the product frontend for customer, topology, connector and LLM configuration.",
            (
                f"<p>LLM status: <strong>{h(llm_label)}</strong> ({h(llm_status)})</p>"
                f"<p><a class='btn btn-primary' href='/settings?project={project}'>Open settings</a></p>"
            ),
            section_id="settings",
        )
        return self._html(
            product_shell(
                title="System settings",
                project_id=project,
                active="settings",
                eyebrow="Settings",
                headline="System configuration",
                description="Secrets are never rendered to the browser.",
                body=body,
                llm_status=llm_status,
            )
        )

    def _serve_frontend(
        self,
        parsed: "urllib.parse.ParseResult",
        root: Path,
    ) -> None:
        del root
        dist_value = os.environ.get("QUALIBUG_FRONTEND_DIST")
        dist = (
            Path(dist_value)
            if dist_value
            else Path(__file__).resolve().parent.parent / "frontend" / "dist"
        ).resolve()
        relative = parsed.path.lstrip("/")
        if relative in {"", "index.html"}:
            target = dist / "index.html"
        elif relative.startswith("assets/"):
            target = (dist / relative).resolve()
        else:
            target = dist / "index.html"
        if not _within(target, dist):
            return self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        if not target.exists() or not target.is_file():
            return self._json(
                {
                    "ok": False,
                    "error": "UI_NOT_BUILT",
                    "message": "frontend/dist 未构建或未配置。",
                },
                404,
            )
        try:
            data = target.read_bytes()
        except OSError:
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store" if target.name == "index.html" else "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)
