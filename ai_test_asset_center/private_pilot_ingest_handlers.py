"""Knowledge ingest and deletion HTTP authorities."""
from __future__ import annotations

import base64
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_project_assets import (
    KNOWLEDGE_INGEST_EXTENSIONS,
    KNOWLEDGE_INGEST_SOURCE_TYPES,
)
from .private_pilot_request_limits import (
    MAX_KNOWLEDGE_UPLOAD_BYTES,
    content_length,
)
from .private_pilot_scan_prep import _run_ingest_auto_scan


def _archive_response_projection(
    ingest_result: dict[str, Any],
    doc_info: dict[str, Any],
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
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


def _safe_filename(value: Any) -> str:
    filename = Path(str(value or "enterprise_source.txt")).name.strip()
    if filename in {"", ".", ".."}:
        return "enterprise_source.txt"
    return filename[:240]


def _canonical_storage_paths(result: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for row in result.get("created") or []:
        if not isinstance(row, dict):
            continue
        value = str(row.get("stored_path") or "").strip()
        if value and value not in paths:
            paths.append(value)
    return paths


def _header_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


# Human-readable messages for upload byte-decoding failures. These preserve the
# observability of the pre-mixin ingestion boundary: the machine-readable error
# code stays authoritative, but a customer-facing reason must not disappear.
_INGEST_DECODE_MESSAGES: dict[str, str] = {
    "DECODE_FAILED": "Base64 解码失败，请检查文件内容。",
    "MISSING_CONTENT": "缺少文件内容。",
    "EMPTY_UPLOAD": "上传内容为空。",
    "UPLOAD_TOO_LARGE": "上传文件过大。",
}


class IngestHandlersMixin:
    def _read_ingest_request(self) -> dict[str, Any]:
        """Read raw upload bytes or the legacy bounded JSON envelope."""

        media_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if media_type != "application/octet-stream":
            return self._body()
        size = content_length(self.headers)
        if size <= 0:
            raise ValueError("knowledge upload body is empty")
        if size > MAX_KNOWLEDGE_UPLOAD_BYTES:
            raise ValueError(
                f"knowledge upload exceeds {MAX_KNOWLEDGE_UPLOAD_BYTES} byte limit"
            )
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValueError("knowledge upload ended before Content-Length bytes were read")
        filename = unquote(
            str(self.headers.get("X-QualiBug-Filename") or "enterprise_source.bin")
        )
        return {
            "filename": filename,
            "type": str(self.headers.get("X-QualiBug-Source-Type") or ""),
            "defer_auto_scan": _header_true(
                self.headers.get("X-QualiBug-Defer-Auto-Scan")
            ),
            "finalize_batch": _header_true(
                self.headers.get("X-QualiBug-Finalize-Batch")
            ),
            "_raw_content": raw,
            "transport_encoding": "raw_octet_stream",
        }

    def _upload_bytes(self, body: dict[str, Any]) -> bytes:
        raw_value = body.get("_raw_content")
        if isinstance(raw_value, (bytes, bytearray, memoryview)):
            raw = bytes(raw_value)
        else:
            encoded = str(body.get("content") or body.get("data") or "")
            if not encoded:
                raise ValueError("MISSING_CONTENT")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValueError("DECODE_FAILED") from exc
        if not raw:
            raise ValueError("EMPTY_UPLOAD")
        if len(raw) > MAX_KNOWLEDGE_UPLOAD_BYTES:
            raise ValueError("UPLOAD_TOO_LARGE")
        return raw

    def _handle_ingest(
        self,
        project: str,
        body: dict[str, Any],
        root: Path,
        actor: dict[str, str],
    ) -> None:
        """Stage one upload and delegate activation to the canonical transaction."""

        if not self._require_known_project(project, root):
            return None
        explicit_type = str(
            body.get("type") or body.get("doc_type") or ""
        ).strip().lower()
        filename = _safe_filename(body.get("filename") or body.get("name"))
        suffix = Path(filename).suffix.lower()
        if suffix not in KNOWLEDGE_INGEST_EXTENSIONS:
            return self._json(
                {
                    "ok": False,
                    "error": "UNSUPPORTED_EXTENSION",
                    "message": (
                        f"暂不支持 {suffix or '无扩展名'} 文件，请上传支持的原始资料格式。"
                    ),
                    "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
                },
                400,
            )
        try:
            raw = self._upload_bytes(body)
        except ValueError as exc:
            code = str(exc)
            status = 413 if code == "UPLOAD_TOO_LARGE" else 400
            payload: dict[str, Any] = {
                "ok": False,
                "error": code,
                "max_bytes": MAX_KNOWLEDGE_UPLOAD_BYTES,
            }
            message = _INGEST_DECODE_MESSAGES.get(code)
            if message:
                payload["message"] = message
            return self._json(payload, status)

        staging_root = (
            root / "platform_workspace" / project / ".ingest_staging"
        ).resolve()
        project_workspace = (root / "platform_workspace" / project).resolve()
        if project_workspace != staging_root and project_workspace not in staging_root.parents:
            raise ValueError("ingest staging path escaped project workspace")
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_path: Path | None = None
        ingest_phase = "staging"
        knowledge_updated = False
        source_id = ""
        source_ids: list[str] = []
        doc_type = ""
        type_resolution = ""
        transport = "document"
        doc_info: dict[str, Any] = {}
        ingest_result: dict[str, Any] = {}
        source_manifest: dict[str, Any] = {}
        auto_scan_reason = ""
        ingest_status = "pending"
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=staging_root,
                prefix="upload_",
                suffix=suffix or ".bin",
                delete=False,
            ) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                staging_path = Path(stream.name)

            ingest_phase = "canonical_ingest"
            from .private_pilot_ingest_authority import ingest_uploaded_enterprise_material

            try:
                authority_result = ingest_uploaded_enterprise_material(
                    project=project,
                    root=root,
                    actor=actor,
                    out_path=staging_path,
                    filename=filename,
                    raw=raw,
                    explicit_type=explicit_type,
                )
            except ValueError as exc:
                message = str(exc)
                if message.startswith("DOCUMENT_INGEST_FAILED:"):
                    return self._json(
                        {
                            "ok": False,
                            "error": "DOCUMENT_INGEST_FAILED",
                            "message": message[len("DOCUMENT_INGEST_FAILED:"):],
                        },
                        500,
                    )
                raise
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
            expanded_count, archive_receipts, archive_expansion = (
                _archive_response_projection(ingest_result, doc_info)
            )
            if authority_result.get("ok") is not True:
                errors = (
                    ingest_result.get("errors")
                    if isinstance(ingest_result.get("errors"), list)
                    else []
                )
                first = errors[0] if errors and isinstance(errors[0], dict) else {}
                detail = (
                    first.get("error")
                    or first.get("detail")
                    or first.get("code")
                    or "unknown"
                )
                return self._json(
                    {
                        "ok": False,
                        "error": "INGEST_FAILED",
                        "message": "资料导入失败：" + str(detail),
                        "transport": transport,
                        "expanded_document_count": expanded_count,
                        "archive_receipts": archive_receipts,
                        "archive_expansion": archive_expansion,
                        "stable_upload_overwritten": False,
                    },
                    500,
                )
            created = ingest_result.get("created") or []
            duplicates = ingest_result.get("duplicates") or []
            if not isinstance(created, list) or not isinstance(duplicates, list):
                raise ValueError("knowledge ingest result source lists are invalid")
            if any(not isinstance(row, dict) for row in [*created, *duplicates]):
                raise ValueError("knowledge ingest result contains invalid source rows")
            knowledge_updated = bool(created)
            ingest_status = (
                "created" if created else "duplicate" if duplicates else "accepted"
            )

            ingest_phase = "cache_invalidation"
            for cache in (
                root
                / "platform_workspace"
                / project
                / "defect_discovery"
                / "enterprise_business_knowledge_asset.json",
                root
                / "platform_outputs"
                / project
                / "enterprise_pilot_runtime"
                / "enterprise_pilot_center.html",
            ):
                cache.unlink(missing_ok=True)

            defer_auto_scan = body.get("defer_auto_scan") is True
            finalize_batch = body.get("finalize_batch") is True
            non_scan_types = {"other_document", "application_log", "har"}
            should_auto_scan = not defer_auto_scan and (
                finalize_batch
                or transport == "archive"
                or doc_type not in non_scan_types
            )
            if defer_auto_scan:
                auto_scan_reason = (
                    "批量资料仍在导入，等待最后一份资料后统一启动后台理解。"
                )
            if should_auto_scan and transport != "archive" and doc_type in {
                "openapi",
                "markdown_api",
                "postman",
            }:
                try:
                    from .universal_api_parser import parse_to_openapi

                    parsed = parse_to_openapi(str(staging_path))
                    paths = parsed.get("paths", {}) if isinstance(parsed, dict) else {}
                    if not paths:
                        should_auto_scan = False
                        auto_scan_reason = (
                            "接口资料未检测到有效端点，资料已保留并标记为待补充。"
                        )
                except Exception as exc:
                    should_auto_scan = False
                    auto_scan_reason = (
                        f"接口资料解析失败，资料已保留并标记为待补充：{exc}"
                    )
            if should_auto_scan:
                ingest_phase = "auto_scan_schedule"
                thread = threading.Thread(
                    target=_run_ingest_auto_scan,
                    kwargs={
                        "root": root,
                        "project": project,
                        "body": {
                            key: value
                            for key, value in body.items()
                            if key != "_raw_content"
                        },
                        "raw": bytes(raw),
                        "doc_type": doc_type,
                        "source_manifest": dict(source_manifest),
                    },
                    name=f"qualibug-ingest-scan-{project}",
                    daemon=True,
                )
                thread.start()
                ingest_status += "_auto_scanning"
            elif defer_auto_scan:
                ingest_status += "_batch_pending"
            elif auto_scan_reason:
                ingest_status += "_scan_skipped"
        except Exception as exc:
            _write_json_object_atomic(
                root
                / "platform_outputs"
                / project
                / "knowledge_ingest_last_error.json",
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
                    "stable_upload_overwritten": False,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            raise
        finally:
            if staging_path is not None:
                staging_path.unlink(missing_ok=True)
            try:
                if staging_root.exists() and not any(staging_root.iterdir()):
                    staging_root.rmdir()
            except OSError:
                pass

        auto_scan = (
            "triggered"
            if "auto_scanning" in ingest_status
            else "deferred"
            if "batch_pending" in ingest_status
            else "skipped"
            if "scan_skipped" in ingest_status
            else "not_applicable"
        )
        expanded_count, archive_receipts, archive_expansion = (
            _archive_response_projection(ingest_result, doc_info)
        )
        storage_paths = _canonical_storage_paths(ingest_result)
        message = (
            f"'{filename}' 已安全展开并导入 {expanded_count} 份资料。"
            if transport == "archive"
            else f"'{filename}' 已导入，识别为 {doc_type}。"
        )
        return self._json(
            {
                "ok": True,
                "source_id": source_id,
                "source_ids": source_ids,
                "ingest_status": ingest_status,
                "auto_scan": auto_scan,
                "auto_scan_reason": auto_scan_reason,
                "filename": filename,
                "doc_type": doc_type,
                "transport": transport,
                "transport_encoding": body.get("transport_encoding") or "base64_json",
                "source_type_resolution": type_resolution,
                "size_bytes": len(raw),
                "path": storage_paths[0] if storage_paths else "",
                "storage_paths": storage_paths,
                "storage_mode": "canonical_immutable_source",
                "source_manifest": source_manifest,
                "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
                "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
                "doc_info": doc_info,
                "knowledge_updated": knowledge_updated,
                "expanded_document_count": expanded_count,
                "archive_receipts": archive_receipts,
                "archive_expansion": archive_expansion,
                "second_source_registration_performed": False,
                "stable_upload_overwritten": False,
                "message": message,
            }
        )

    def _handle_delete(
        self,
        project: str,
        body: dict[str, Any],
        root: Path,
        actor: dict[str, str],
    ) -> None:
        from .enterprise_knowledge_center import delete_enterprise_knowledge_source

        source_id = str(body.get("source_id") or "").strip()
        if not source_id:
            return self._json(
                {
                    "ok": False,
                    "error": "MISSING_SOURCE_ID",
                    "message": "Missing source_id.",
                },
                400,
            )
        try:
            result = delete_enterprise_knowledge_source(
                project, source_id, root, actor
            )
        except KeyError:
            return self._json(
                {
                    "ok": False,
                    "error": "NOT_FOUND",
                    "message": f"Source {source_id} was not found or already deleted.",
                },
                404,
            )
        for cache in (
            root
            / "platform_workspace"
            / project
            / "defect_discovery"
            / "enterprise_business_knowledge_asset.json",
            root
            / "platform_outputs"
            / project
            / "enterprise_pilot_runtime"
            / "enterprise_pilot_center.html",
        ):
            cache.unlink(missing_ok=True)
        filename = str(result.get("original_name") or source_id)
        return self._json(
            {
                "ok": True,
                "source_id": source_id,
                "filename": filename,
                "removed_paths": result.get("removed_paths") or [],
                "message": f"'{filename}' permanently deleted.",
            }
        )
