"""Knowledge ingest / delete handlers for PrivatePilotHandler."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_project_assets import (
    KNOWLEDGE_INGEST_EXTENSIONS,
    KNOWLEDGE_INGEST_SOURCE_TYPES,
)
from .private_pilot_scan_prep import _run_ingest_auto_scan


def _archive_response_projection(
    ingest_result: dict[str, Any],
    doc_info: dict[str, Any],
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """Project the canonical nested archive receipt onto the stable HTTP response."""

    archive_expansion = dict(ingest_result.get("archive_expansion") or {})
    expanded_count = int(
        ingest_result.get("expanded_document_count")
        or archive_expansion.get("document_count")
        or doc_info.get("expanded_document_count")
        or 0
    )
    raw_receipts = (
        ingest_result.get("archive_receipts")
        or archive_expansion.get("packages")
        or doc_info.get("archive_receipts")
        or []
    )
    receipts = [dict(row) for row in raw_receipts if isinstance(row, dict)]
    return expanded_count, receipts, archive_expansion


class IngestHandlersMixin:
    def _handle_ingest(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Ingest one customer transport artifact through the canonical knowledge authority."""
        import base64

        if not self._require_known_project(project, root):
            return

        explicit_type = str(body.get("type") or body.get("doc_type") or "").strip().lower()
        filename = Path(str(body.get("filename") or body.get("name") or "enterprise_source.txt")).name
        filename = filename or "enterprise_source.txt"
        suffix = Path(filename).suffix.lower()
        if suffix not in KNOWLEDGE_INGEST_EXTENSIONS:
            return self._json(
                {
                    "ok": False,
                    "error": "UNSUPPORTED_EXTENSION",
                    "message": f"暂不支持 {suffix or '无扩展名'} 文件，请直接上传支持的原始资料格式。",
                    "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
                },
                400,
            )

        content_b64 = str(body.get("content") or body.get("data") or "")
        if not content_b64:
            return self._json({"ok": False, "error": "MISSING_CONTENT", "message": "缺少文件内容。"}, 400)

        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception:
            return self._json(
                {"ok": False, "error": "DECODE_FAILED", "message": "文件内容解码失败，请重新选择原始文件。"},
                400,
            )

        input_dir = root / "platform_workspace" / project / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        out_path = input_dir / filename
        out_path.write_bytes(raw)

        source_manifest: dict[str, Any] = {}
        knowledge_updated = False
        source_id = ""
        source_ids: list[str] = []
        ingest_status = "pending"
        auto_scan_reason = ""
        ingest_phase = "upload_dispatch"
        defer_auto_scan = body.get("defer_auto_scan") is True
        finalize_batch = body.get("finalize_batch") is True
        doc_type = ""
        type_resolution = ""
        doc_info: dict[str, Any] = {}
        transport = "document"
        ingest_result: dict[str, Any] = {}
        try:
            from .private_pilot_ingest_authority import ingest_uploaded_enterprise_material

            authority_result = ingest_uploaded_enterprise_material(
                project=project,
                root=root,
                actor=actor,
                out_path=out_path,
                filename=filename,
                raw=raw,
                explicit_type=explicit_type,
            )
            if not isinstance(authority_result, dict):
                raise TypeError("upload ingest authority result must be an object")
            ingest_result = dict(authority_result.get("ingest_result") or {})
            doc_type = str(authority_result.get("doc_type") or "")
            type_resolution = str(authority_result.get("type_resolution") or "")
            doc_info = dict(authority_result.get("doc_info") or {})
            transport = str(authority_result.get("transport") or "document")
            source_manifest = dict(authority_result.get("source_manifest") or {})
            source_ids = [
                str(value)
                for value in authority_result.get("source_ids") or []
                if str(value).strip()
            ]
            source_id = str(authority_result.get("source_id") or "")
            expanded_count, archive_receipts, archive_expansion = _archive_response_projection(
                ingest_result,
                doc_info,
            )
            if authority_result.get("ok") is not True:
                errors = ingest_result.get("errors") if isinstance(ingest_result.get("errors"), list) else []
                first = errors[0] if errors and isinstance(errors[0], dict) else {}
                detail = first.get("error") or first.get("detail") or first.get("code") or "unknown"
                out_path.unlink(missing_ok=True)
                return self._json(
                    {
                        "ok": False,
                        "error": "INGEST_FAILED",
                        "message": "资料导入失败：" + str(detail),
                        "transport": transport,
                        "expanded_document_count": expanded_count,
                        "archive_receipts": archive_receipts,
                        "archive_expansion": archive_expansion,
                    },
                    500,
                )

            created = ingest_result.get("created") or []
            duplicates = ingest_result.get("duplicates") or []
            if not isinstance(created, list) or not isinstance(duplicates, list):
                raise ValueError("knowledge ingest result source lists are invalid")
            knowledge_updated = bool(created)
            ingest_status = "created" if created else "duplicate" if duplicates else "accepted"

            ingest_phase = "cache_invalidation"
            knowledge_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if knowledge_cache.exists():
                knowledge_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists():
                dash_html.unlink()

            # Every meaningful source can change the combined business model. A package
            # or multi-file selection triggers one scan against the composed corpus.
            ingest_phase = "auto_scan_validation"
            non_scan_types = {"other_document", "application_log", "har"}
            should_auto_scan = not defer_auto_scan and (
                finalize_batch or transport == "archive" or doc_type not in non_scan_types
            )
            if defer_auto_scan:
                auto_scan_reason = "批量资料仍在导入，等待最后一份资料后统一启动后台理解。"

            if should_auto_scan and transport != "archive" and doc_type in {
                "openapi",
                "markdown_api",
                "postman",
            }:
                try:
                    from .universal_api_parser import parse_to_openapi

                    parsed = parse_to_openapi(str(out_path))
                    paths = parsed.get("paths", {}) if isinstance(parsed, dict) else {}
                    if not paths:
                        should_auto_scan = False
                        auto_scan_reason = "该接口资料未检测到有效端点，资料已保留并标记为待补充。"
                except Exception as exc:
                    should_auto_scan = False
                    auto_scan_reason = f"接口资料解析失败，资料已保留并标记为待补充：{exc}"

            if should_auto_scan:
                ingest_phase = "auto_scan_schedule"
                import threading as _threading

                _threading.Thread(
                    target=_run_ingest_auto_scan,
                    kwargs={
                        "root": root,
                        "project": project,
                        "body": dict(body),
                        "raw": raw,
                        "doc_type": doc_type,
                        "source_manifest": dict(source_manifest),
                    },
                    daemon=True,
                ).start()
                ingest_status = f"{ingest_status}_auto_scanning"
            elif defer_auto_scan:
                ingest_status = f"{ingest_status}_batch_pending"
            elif auto_scan_reason:
                ingest_status = f"{ingest_status}_scan_skipped"
        except Exception as exc:
            _write_json_object_atomic(
                root / "platform_outputs" / project / "knowledge_ingest_last_error.json",
                {
                    "schema": "qualibug.knowledge-ingest-failure.v1",
                    "project": project,
                    "filename": filename,
                    "doc_type": doc_type,
                    "source_type_resolution": type_resolution,
                    "transport": transport,
                    "phase": ingest_phase,
                    "knowledge_updated": knowledge_updated,
                    "source_id": source_id,
                    "source_ids": source_ids,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            raise

        auto_scan = (
            "triggered"
            if "auto_scanning" in ingest_status
            else "deferred"
            if "batch_pending" in ingest_status
            else "skipped"
            if "scan_skipped" in ingest_status
            else "not_applicable"
        )
        expanded_count, archive_receipts, archive_expansion = _archive_response_projection(
            ingest_result,
            doc_info,
        )
        message = (
            f"'{filename}' 已安全展开并导入 {expanded_count} 份资料。"
            if transport == "archive"
            else f"'{filename}' 已导入，后台自动识别为 {doc_type}。"
        )
        return self._json({
            "ok": True,
            "source_id": source_id,
            "source_ids": source_ids,
            "ingest_status": ingest_status,
            "auto_scan": auto_scan,
            "auto_scan_reason": auto_scan_reason,
            "filename": filename,
            "doc_type": doc_type,
            "transport": transport,
            "source_type_resolution": type_resolution,
            "size_bytes": len(raw),
            "path": str(out_path),
            "storage_mode": "verbatim_bytes",
            "source_manifest": source_manifest,
            "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
            "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
            "doc_info": doc_info,
            "knowledge_updated": knowledge_updated,
            "expanded_document_count": expanded_count,
            "archive_receipts": archive_receipts,
            "archive_expansion": archive_expansion,
            "second_source_registration_performed": False,
            "message": message,
        })

    def _handle_delete(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Delete a knowledge source by source_id."""
        from .enterprise_knowledge_center import delete_enterprise_knowledge_source

        source_id = str(body.get("source_id") or "").strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID", "message": "Missing source_id."}, 400)
        try:
            result = delete_enterprise_knowledge_source(project, source_id, root, actor)
        except KeyError:
            return self._json({"ok": False, "error": "NOT_FOUND", "message": f"Source {source_id} was not found or already deleted."}, 404)
        try:
            asset_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if asset_cache.exists():
                asset_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists():
                dash_html.unlink()
        except OSError:
            pass
        filename = str(result.get("original_name") or source_id)
        return self._json({
            "ok": True,
            "source_id": source_id,
            "filename": filename,
            "removed_paths": result.get("removed_paths") or [],
            "message": f"'{filename}' permanently deleted.",
        })
