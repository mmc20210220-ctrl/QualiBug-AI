"""Source-container inspection before compatible Office normalization.

The normalizer is a transport bridge, not a macro interpreter. This module inspects the
immutable source container before LibreOffice opens it, records exactly what was checked,
and blocks known embedded automation. It never treats a successful OOXML conversion as
proof that the original source contained no macros or scripts.
"""
from __future__ import annotations

import io
import zipfile
from typing import Any

from .contract import DocumentSource

OFFICE_CONTAINER_INSPECTION_SCHEMA = "qualibug.office-container-inspection.v1"
_OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
_MAX_ZIP_MEMBER_COUNT = 5_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_ZIP_COMPRESSION_RATIO = 200
_MAX_NESTED_OLE_MEMBER_BYTES = 20 * 1024 * 1024
_MAX_TOTAL_NESTED_OLE_BYTES = 64 * 1024 * 1024
_MAX_NESTED_OLE_MEMBERS = 64


def _normalized_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _zip_automation_indicators(names: list[str]) -> list[str]:
    indicators: list[str] = []
    for original in names:
        name = _normalized_path(original)
        lower = name.lower()
        parts = [part for part in lower.split("/") if part]
        if lower.endswith("vbaproject.bin"):
            indicators.append(f"ZIP_VBA_PROJECT:{name}")
        if parts and parts[0] in {"basic", "scripts"}:
            indicators.append(f"ZIP_SCRIPT_TREE:{name}")
        if "basic" in parts and lower.endswith((".xml", ".xba", ".xlb")):
            indicators.append(f"ZIP_BASIC_MODULE:{name}")
        if "scripts" in parts and lower.endswith((".py", ".js", ".java", ".class", ".xml")):
            indicators.append(f"ZIP_SCRIPT_MODULE:{name}")
    return sorted(set(indicators))


def _ole_stream_names(data: bytes) -> tuple[list[str], str]:
    try:
        import olefile
    except ImportError:
        return [], "OLEFILE_RUNTIME_UNAVAILABLE"
    try:
        stream = io.BytesIO(data)
        if not olefile.isOleFile(stream):
            return [], "NOT_OLE_CONTAINER"
        stream.seek(0)
        document = olefile.OleFileIO(stream)
        try:
            names = [
                "/".join(str(part) for part in path)
                for path in document.listdir(streams=True, storages=True)
            ]
        finally:
            document.close()
        return sorted(set(names)), "COMPLETE"
    except Exception as exc:
        return [], f"OLE_INSPECTION_FAILED:{type(exc).__name__}"


def _ole_automation_indicators(names: list[str], *, prefix: str = "") -> list[str]:
    indicators: list[str] = []
    for original in names:
        name = _normalized_path(original)
        upper_parts = [part.upper() for part in name.split("/") if part]
        upper_name = "/".join(upper_parts)
        has_vba_tree = "VBA" in upper_parts or "_VBA_PROJECT_CUR" in upper_parts
        has_macro_tree = "MACROS" in upper_parts
        project_stream = upper_parts and upper_parts[-1] in {"PROJECT", "PROJECTWM", "DIR"}
        if has_vba_tree or has_macro_tree or (project_stream and "VBA" in upper_name):
            indicators.append(f"{prefix}OLE_AUTOMATION_STREAM:{name}")
    return sorted(set(indicators))


def _zip_limits(infos: list[zipfile.ZipInfo]) -> tuple[bool, list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    if len(infos) > _MAX_ZIP_MEMBER_COUNT:
        errors.append(
            {
                "code": "OFFICE_ZIP_MEMBER_LIMIT_EXCEEDED",
                "observed": len(infos),
                "limit": _MAX_ZIP_MEMBER_COUNT,
            }
        )
    total_uncompressed = sum(max(0, int(info.file_size)) for info in infos if not info.is_dir())
    if total_uncompressed > _MAX_ZIP_UNCOMPRESSED_BYTES:
        errors.append(
            {
                "code": "OFFICE_ZIP_UNCOMPRESSED_SIZE_LIMIT_EXCEEDED",
                "observed": total_uncompressed,
                "limit": _MAX_ZIP_UNCOMPRESSED_BYTES,
            }
        )
    for info in infos:
        if info.is_dir() or info.file_size <= 0:
            continue
        compressed = max(1, int(info.compress_size))
        ratio = int(info.file_size) / compressed
        if ratio > _MAX_ZIP_COMPRESSION_RATIO:
            errors.append(
                {
                    "code": "OFFICE_ZIP_COMPRESSION_RATIO_LIMIT_EXCEEDED",
                    "member": info.filename,
                    "observed_ratio": round(ratio, 2),
                    "limit": _MAX_ZIP_COMPRESSION_RATIO,
                }
            )
            break
    return not errors, errors


def inspect_office_container(source: DocumentSource) -> dict[str, Any]:
    """Inspect one immutable source without executing or interpreting embedded automation."""

    data = bytes(source.data or b"")
    indicators: list[str] = []
    inspected_members = 0
    nested_ole_members = 0
    nested_ole_bytes = 0
    errors: list[dict[str, Any]] = []
    stripped = data.lstrip()
    if source.suffix == ".rtf" or stripped.startswith(b"{\\rtf"):
        container_kind = "RTF_TEXT"
        inspection_complete = True
    else:
        container_kind = "OPAQUE_CONTAINER"
        inspection_complete = False
        errors.append({"code": "OPAQUE_OFFICE_CONTAINER_AUTOMATION_NOT_VERIFIABLE"})

    if data.startswith(b"PK"):
        container_kind = "ZIP_PACKAGE"
        inspection_complete = True
        errors = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                inspected_members = len(names)
                indicators.extend(_zip_automation_indicators(names))
                limits_ok, limit_errors = _zip_limits(infos)
                if not limits_ok:
                    inspection_complete = False
                    errors.extend(limit_errors)
                for info in infos[:_MAX_ZIP_MEMBER_COUNT]:
                    if nested_ole_members >= _MAX_NESTED_OLE_MEMBERS:
                        inspection_complete = False
                        errors.append(
                            {
                                "code": "NESTED_OLE_MEMBER_LIMIT_EXCEEDED",
                                "limit": _MAX_NESTED_OLE_MEMBERS,
                            }
                        )
                        break
                    if info.is_dir() or info.file_size <= 0 or info.file_size > _MAX_NESTED_OLE_MEMBER_BYTES:
                        continue
                    if nested_ole_bytes + int(info.file_size) > _MAX_TOTAL_NESTED_OLE_BYTES:
                        inspection_complete = False
                        errors.append(
                            {
                                "code": "NESTED_OLE_TOTAL_SIZE_LIMIT_EXCEEDED",
                                "limit": _MAX_TOTAL_NESTED_OLE_BYTES,
                            }
                        )
                        break
                    lower = info.filename.lower()
                    if not lower.endswith((".bin", ".ole", ".dat")):
                        continue
                    compressed = max(1, int(info.compress_size))
                    if int(info.file_size) / compressed > _MAX_ZIP_COMPRESSION_RATIO:
                        continue
                    try:
                        member = archive.read(info)
                    except Exception as exc:
                        errors.append(
                            {
                                "code": "NESTED_OFFICE_MEMBER_READ_FAILED",
                                "member": info.filename,
                                "detail": f"{type(exc).__name__}: {exc}"[:300],
                            }
                        )
                        inspection_complete = False
                        continue
                    if not member.startswith(_OLE_SIGNATURE):
                        continue
                    nested_ole_members += 1
                    nested_ole_bytes += len(member)
                    stream_names, status = _ole_stream_names(member)
                    if status != "COMPLETE":
                        inspection_complete = False
                        errors.append({"code": status, "member": info.filename})
                        continue
                    indicators.extend(
                        _ole_automation_indicators(stream_names, prefix=f"{info.filename}:")
                    )
        except Exception as exc:
            inspection_complete = False
            errors.append(
                {
                    "code": "ZIP_CONTAINER_INSPECTION_FAILED",
                    "detail": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
    elif data.startswith(_OLE_SIGNATURE):
        container_kind = "OLE_COMPOUND_FILE"
        errors = []
        stream_names, status = _ole_stream_names(data)
        inspected_members = len(stream_names)
        if status != "COMPLETE":
            inspection_complete = False
            errors.append({"code": status})
        else:
            inspection_complete = True
            indicators.extend(_ole_automation_indicators(stream_names))

    indicators = sorted(set(indicators))
    automation_present = bool(indicators)
    if automation_present:
        status = "BLOCKED_EMBEDDED_AUTOMATION_PRESENT"
    elif inspection_complete:
        status = "PASS_NO_KNOWN_AUTOMATION_ARTIFACTS"
    elif container_kind == "OPAQUE_CONTAINER":
        status = "PARTIAL_OPAQUE_CONTAINER_NOT_INSPECTABLE"
    else:
        status = "PARTIAL_AUTOMATION_INSPECTION_INCOMPLETE"

    return {
        "schema": OFFICE_CONTAINER_INSPECTION_SCHEMA,
        "status": status,
        "source_id": source.source_id,
        "source_filename": source.filename,
        "source_hash": source.content_hash,
        "source_suffix": source.suffix,
        "container_kind": container_kind,
        "inspection_complete": inspection_complete,
        "inspected_member_count": inspected_members,
        "nested_ole_member_count": nested_ole_members,
        "nested_ole_byte_count": nested_ole_bytes,
        "automation_artifact_detected": automation_present,
        "automation_indicators": indicators,
        "error_count": len(errors),
        "errors": errors,
        "resource_limits": {
            "max_zip_member_count": _MAX_ZIP_MEMBER_COUNT,
            "max_zip_uncompressed_bytes": _MAX_ZIP_UNCOMPRESSED_BYTES,
            "max_zip_compression_ratio": _MAX_ZIP_COMPRESSION_RATIO,
            "max_nested_ole_member_bytes": _MAX_NESTED_OLE_MEMBER_BYTES,
            "max_total_nested_ole_bytes": _MAX_TOTAL_NESTED_OLE_BYTES,
            "max_nested_ole_members": _MAX_NESTED_OLE_MEMBERS,
        },
        "automation_code_executed": False,
        "automation_semantics_interpreted": False,
        "absence_of_known_indicators_is_not_proof_of_no_behavior": True,
        "business_semantics_added": False,
    }


__all__ = ["OFFICE_CONTAINER_INSPECTION_SCHEMA", "inspect_office_container"]
