"""Knowledge ingest / delete handlers for PrivatePilotHandler."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_project_assets import (
    KNOWLEDGE_INGEST_EXTENSIONS,
    KNOWLEDGE_INGEST_SOURCE_TYPES,
    resolve_knowledge_source_type,
)
from .private_pilot_scan_prep import _run_ingest_auto_scan


class IngestHandlersMixin:
    def _handle_ingest(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Ingest raw customer material and let the backend classify it."""
        import base64
        from .enterprise_knowledge_center import ingest_enterprise_knowledge_documents

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

        # Decode once and persist the original bytes so PDF/DOCX/XLSX uploads are
        # not corrupted by an unnecessary UTF-8 text round-trip.
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception:
            return self._json({"ok": False, "error": "DECODE_FAILED", "message": "文件内容解码失败，请重新选择原始文件。"}, 400)

        input_dir = root / "platform_workspace" / project / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        out_path = input_dir / filename
        out_path.write_bytes(raw)

        # The document intelligence layer extracts text first. Classification and
        # source-registry hashing must use that extracted text rather than binary
        # PDF/DOCX bytes decoded as replacement characters.
        from .document_change_watcher import ingest_document

        doc_info = ingest_document(str(out_path))
        if not isinstance(doc_info, dict) or doc_info.get("ok") is not True:
            message = str(doc_info.get("error") or "document intelligence returned an invalid result") if isinstance(doc_info, dict) else "document intelligence result must be an object"
            out_path.unlink(missing_ok=True)
            return self._json(
                {"ok": False, "error": "DOCUMENT_INGEST_FAILED", "message": message},
                500,
            )

        extracted_text = str(doc_info.get("text") or "")
        if not extracted_text and suffix not in {".pdf", ".docx", ".xlsx", ".xls"}:
            extracted_text = raw.decode("utf-8", errors="replace")
        try:
            doc_type, type_resolution = resolve_knowledge_source_type(
                filename,
                extracted_text,
                explicit_type or None,
            )
        except ValueError as exc:
            out_path.unlink(missing_ok=True)
            return self._json(
                {
                    "ok": False,
                    "error": "UNSUPPORTED_SOURCE_TYPE",
                    "message": str(exc),
                    "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
                },
                400,
            )

        source_manifest: dict[str, Any] = {}
        knowledge_updated = False
        source_id = ""
        ingest_status = "pending"
        auto_scan_reason = ""
        ingest_phase = "knowledge_center"
        defer_auto_scan = body.get("defer_auto_scan") is True
        finalize_batch = body.get("finalize_batch") is True
        try:
            ingest_result = ingest_enterprise_knowledge_documents(
                project,
                [{"file_path": str(out_path), "filename": filename, "source_type": doc_type}],
                root=root,
                actor=actor,
            )
            if not isinstance(ingest_result, dict):
                raise TypeError("knowledge ingest result must be an object")
            if "ok" not in ingest_result:
                raise ValueError("knowledge ingest result missing ok=true")
            if ingest_result.get("ok") is not True and ingest_result.get("ok") is not False:
                raise ValueError("knowledge ingest result ok must be a boolean")
            if ingest_result.get("ok") is False:
                out_path.unlink(missing_ok=True)
                errors = ingest_result.get("errors") if isinstance(ingest_result.get("errors"), list) else []
                first_error = errors[0].get("error") if errors and isinstance(errors[0], dict) else "unknown"
                return self._json(
                    {"ok": False, "error": "INGEST_FAILED", "message": "资料导入失败：" + str(first_error)},
                    500,
                )

            knowledge_updated = True
            created = ingest_result.get("created", [])
            duplicates = ingest_result.get("duplicates", [])
            if not isinstance(created, list):
                raise ValueError("knowledge ingest result created must be a list")
            if not isinstance(duplicates, list):
                raise ValueError("knowledge ingest result duplicates must be a list")
            ingest_status = "created"
            if created and isinstance(created[0], dict):
                source_id = str(created[0].get("source_id") or "")
            elif duplicates and isinstance(duplicates[0], dict):
                source_id = str(duplicates[0].get("source_id") or "")
                ingest_status = "duplicate"

            ingest_phase = "source_registry"
            from .enterprise_source_registry import register_source_asset, resolve_source_manifest

            source_asset_id = source_id or f"{doc_type}:{filename}"
            source_manifest = resolve_source_manifest(project, extracted_text, root=root)
            if not source_manifest:
                source_manifest = register_source_asset(
                    project,
                    source_asset_id,
                    extracted_text,
                    source_type=doc_type,
                    root=root,
                    actor=actor,
                    origin="knowledge_ingest",
                    filename=filename,
                    metadata={
                        "knowledge_source_id": source_id,
                        "storage_mode": "verbatim_bytes",
                        "input_path": str(out_path.relative_to(root)) if str(out_path).startswith(str(root)) else str(out_path),
                        "source_type_resolution": type_resolution,
                    },
                )
            if not isinstance(source_manifest, dict):
                raise TypeError("source manifest must be an object")
            manifest_source_id = str(source_manifest.get("source_id") or "").strip()
            manifest_source_hash = str(source_manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
            if not manifest_source_id or re.fullmatch(r"[0-9a-f]{64}", manifest_source_hash) is None:
                raise ValueError("source manifest missing valid source_id/source_hash")

            ingest_phase = "cache_invalidation"
            knowledge_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if knowledge_cache.exists():
                knowledge_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists():
                dash_html.unlink()

            # Every meaningful source can change the combined business model. A
            # multi-file customer selection defers intermediate scans and starts
            # one combined background analysis after the last file is indexed.
            ingest_phase = "auto_scan_validation"
            non_scan_types = {"other_document", "application_log", "har"}
            should_auto_scan = not defer_auto_scan and (
                finalize_batch or doc_type not in non_scan_types
            )
            if defer_auto_scan:
                auto_scan_reason = "批量资料仍在导入，等待最后一份资料后统一启动后台理解。"

            if should_auto_scan and doc_type in {"openapi", "markdown_api", "postman"}:
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
                    "phase": ingest_phase,
                    "knowledge_updated": knowledge_updated,
                    "source_id": source_id,
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
        return self._json({
            "ok": True,
            "source_id": source_id,
            "ingest_status": ingest_status,
            "auto_scan": auto_scan,
            "auto_scan_reason": auto_scan_reason,
            "filename": filename,
            "doc_type": doc_type,
            "source_type_resolution": type_resolution,
            "size_bytes": len(raw),
            "path": str(out_path),
            "storage_mode": "verbatim_bytes",
            "source_manifest": source_manifest,
            "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
            "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
            "doc_info": doc_info,
            "knowledge_updated": knowledge_updated,
            "message": f"'{filename}' 已导入，后台自动识别为 {doc_type}。",
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
