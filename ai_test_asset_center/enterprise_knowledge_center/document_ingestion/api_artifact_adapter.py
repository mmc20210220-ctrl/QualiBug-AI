"""Source-preserving Document IR for OpenAPI, Postman and HAR artifacts.

This adapter owns container structure only. It recognizes source-declared API artifacts,
redacts credential values, and emits exact JSON-Pointer evidence. It deliberately does not
turn endpoints, requests or observations into business facts; the semantic layer remains the
only authority for that work.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections import Counter
from copy import deepcopy
from typing import Any, Iterable

import yaml

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .contract import (
    AdapterMatch,
    CAP_HEADING_HIERARCHY,
    CAP_LIST_HIERARCHY,
    CAP_TEXT_EXTRACTION,
    DocumentAdapter,
    DocumentSource,
    MODE_PRIMARY,
)

API_ARTIFACT_STRUCTURE_SCHEMA = "qualibug.api-artifact-structure.v1"
API_ARTIFACT_REDACTION_SCHEMA = "qualibug.api-artifact-redaction.v1"
_MAX_STRUCTURAL_BLOCKS = 20_000
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:authorization|proxy[-_ ]?authorization|cookie|set[-_ ]?cookie|"
    r"access[-_ ]?token|refresh[-_ ]?token|id[-_ ]?token|api[-_ ]?key|apikey|"
    r"client[-_ ]?secret|password|passwd|secret|bearer|private[-_ ]?key|session[-_ ]?id)"
)
_REDACTED = "<redacted>"


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _pointer(*parts: Any) -> str:
    return "/" + "/".join(_pointer_token(part) for part in parts) if parts else "/"


def _locator(filename: str, pointer: str) -> str:
    return f"{filename or 'api-artifact.json'}#json-pointer={pointer or '/'}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _decode_payload(source: DocumentSource) -> tuple[dict[str, Any] | None, str, str]:
    try:
        decoded = source.data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return None, "", f"UnicodeDecodeError: {exc}"
    try:
        if source.suffix in {".yaml", ".yml"}:
            value = yaml.safe_load(decoded)
        else:
            value = json.loads(decoded)
    except Exception as exc:
        return None, decoded, f"{type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, decoded, "API artifact root must be an object"
    return dict(value), decoded, ""


def _artifact_kind(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("paths"), dict) and (payload.get("openapi") or payload.get("swagger")):
        return "openapi"
    info = _dict(payload.get("info"))
    schema = _text(info.get("schema")).lower()
    if isinstance(payload.get("item"), list) and (
        "postman" in schema or info.get("_postman_id") or payload.get("variable") is not None
    ):
        return "postman"
    log = _dict(payload.get("log"))
    if isinstance(log.get("entries"), list) and (
        log.get("version") or log.get("creator") or log.get("pages") is not None
    ):
        return "har"
    return ""


def _sensitive_name(value: Any) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(_text(value)))


def _redact_url(value: str) -> str:
    text = str(value or "")
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return text
    if not parsed.query:
        return text
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted = [
        (key, _REDACTED if _sensitive_name(key) else item_value)
        for key, item_value in pairs
    ]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(redacted), parsed.fragment)
    )


def _redact_embedded_json(value: str) -> str:
    text = str(value or "")
    stripped = text.lstrip()
    if not stripped.startswith(("{", "[")):
        return text
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return json.dumps(_redacted_copy(parsed), ensure_ascii=False, separators=(",", ":"))


def _redacted_copy(value: Any, *, key_hint: str = "") -> Any:
    if isinstance(value, list):
        return [_redacted_copy(item, key_hint=key_hint) for item in value]
    if not isinstance(value, dict):
        if _sensitive_name(key_hint):
            return _REDACTED
        if key_hint.lower() in {"url", "raw"} and isinstance(value, str):
            return _redact_url(value)
        return value

    result: dict[str, Any] = {}
    identity_name = _text(value.get("name") or value.get("key"))
    identity_sensitive = _sensitive_name(identity_name)
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        key_lower = key.lower()
        if identity_sensitive and key_lower in {"value", "current", "initial", "default"}:
            result[key] = _REDACTED
            continue
        if _sensitive_name(key) and not isinstance(raw_value, (dict, list)):
            result[key] = _REDACTED
            continue
        if key_lower in {"url", "raw"} and isinstance(raw_value, str):
            result[key] = _redact_url(raw_value)
            continue
        if key_lower == "text" and isinstance(raw_value, str):
            result[key] = _redact_embedded_json(raw_value)
            continue
        result[key] = _redacted_copy(raw_value, key_hint=key)
    return result


def _block(
    source: DocumentSource,
    *,
    pointer: str,
    block_type: str,
    order: int,
    text: str,
    parent_id: str = "",
    key: str = "",
    value: str = "",
    node_kind: str = "",
    metadata: dict[str, Any] | None = None,
    excluded_from_projection: bool = True,
) -> dict[str, Any]:
    block_id = _stable_id("api_artifact_block", source.source_id, pointer, node_kind, order)
    row: dict[str, Any] = {
        "block_id": block_id,
        "type": block_type,
        "parent_id": parent_id,
        "order": order,
        "region": "body",
        "text": text,
        "source_locator": _locator(source.filename, pointer),
        "json_pointer": pointer,
        "node_kind": node_kind,
        "excluded_from_plain_text_projection": bool(excluded_from_projection),
        "structure_evidence": {
            "method": "source_declared_json_pointer",
            "artifact_structure_only": True,
            "business_semantics_added": False,
        },
        "evidence_address": {
            "address_kind": "EXACT_SOURCE_LOCATOR",
            "json_pointer": pointer,
        },
    }
    if key:
        row["key"] = key
    if value:
        row["value"] = value
    if metadata:
        row.update(metadata)
    return row


def _append_block(blocks: list[dict[str, Any]], block: dict[str, Any]) -> bool:
    if len(blocks) >= _MAX_STRUCTURAL_BLOCKS:
        return False
    blocks.append(block)
    return True


def _openapi_blocks(source: DocumentSource, payload: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    order = len(blocks)
    for path, raw_path_item in _dict(payload.get("paths")).items():
        path_item = _dict(raw_path_item)
        path_pointer = _pointer("paths", path)
        path_block = _block(
            source,
            pointer=path_pointer,
            block_type="HEADING",
            order=order + 1,
            text=str(path),
            node_kind="OPENAPI_PATH",
            metadata={"level": 1, "api_path": str(path)},
        )
        if not _append_block(blocks, path_block):
            return dict(counts)
        order += 1
        counts["path"] += 1
        path_parent = path_block["block_id"]
        for method, raw_operation in path_item.items():
            method_lower = str(method).lower()
            if method_lower not in _HTTP_METHODS or not isinstance(raw_operation, dict):
                continue
            operation = dict(raw_operation)
            operation_pointer = _pointer("paths", path, method)
            summary = _text(operation.get("summary") or operation.get("description"))
            operation_text = f"{method_lower.upper()} {path}" + (f" — {summary}" if summary else "")
            operation_block = _block(
                source,
                pointer=operation_pointer,
                block_type="KEY_VALUE",
                order=order + 1,
                text=operation_text,
                parent_id=path_parent,
                key="operation",
                value=operation_text,
                node_kind="OPENAPI_OPERATION",
                metadata={
                    "http_method": method_lower.upper(),
                    "api_path": str(path),
                    "operation_id": _text(operation.get("operationId")),
                    "declared_tags": [_text(item) for item in _list(operation.get("tags")) if _text(item)],
                },
            )
            if not _append_block(blocks, operation_block):
                return dict(counts)
            order += 1
            counts["operation"] += 1
            operation_parent = operation_block["block_id"]
            combined_parameters = [*_list(path_item.get("parameters")), *_list(operation.get("parameters"))]
            for index, raw_parameter in enumerate(combined_parameters):
                parameter = _dict(raw_parameter)
                parameter_pointer = _pointer("paths", path, method, "parameters", index)
                name = _text(parameter.get("name") or parameter.get("$ref") or f"parameter_{index}")
                location = _text(parameter.get("in")).upper()
                text = f"{location or 'PARAMETER'} {name} required={bool(parameter.get('required'))}"
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=parameter_pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=text,
                        parent_id=operation_parent,
                        key="parameter",
                        value=text,
                        node_kind="OPENAPI_PARAMETER",
                        metadata={
                            "parameter_name": name,
                            "parameter_location": location,
                            "required": bool(parameter.get("required")),
                        },
                    ),
                ):
                    return dict(counts)
                order += 1
                counts["parameter"] += 1
            request_body = _dict(operation.get("requestBody"))
            for media_type in _dict(request_body.get("content")):
                pointer = _pointer("paths", path, method, "requestBody", "content", media_type)
                text = f"request body {media_type} required={bool(request_body.get('required'))}"
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=text,
                        parent_id=operation_parent,
                        key="request_body",
                        value=text,
                        node_kind="OPENAPI_REQUEST_BODY",
                        metadata={"media_type": str(media_type), "required": bool(request_body.get("required"))},
                    ),
                ):
                    return dict(counts)
                order += 1
                counts["request_body"] += 1
            for status, raw_response in _dict(operation.get("responses")).items():
                response = _dict(raw_response)
                pointer = _pointer("paths", path, method, "responses", status)
                description = _text(response.get("description"))
                text = f"response {status}" + (f" — {description}" if description else "")
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=text,
                        parent_id=operation_parent,
                        key="response",
                        value=text,
                        node_kind="OPENAPI_RESPONSE",
                        metadata={"status_code": str(status)},
                    ),
                ):
                    return dict(counts)
                order += 1
                counts["response"] += 1
            for index, requirement in enumerate(_list(operation.get("security"))):
                schemes = [str(name) for name in _dict(requirement)]
                pointer = _pointer("paths", path, method, "security", index)
                text = "security " + ", ".join(schemes)
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=text,
                        parent_id=operation_parent,
                        key="security",
                        value=text,
                        node_kind="OPENAPI_SECURITY_REQUIREMENT",
                        metadata={"security_schemes": schemes, "credential_values_retained": False},
                    ),
                ):
                    return dict(counts)
                order += 1
                counts["security"] += 1
    return dict(counts)


def _postman_request_path(request: dict[str, Any]) -> str:
    url = request.get("url")
    if isinstance(url, dict):
        path = url.get("path")
        if isinstance(path, list):
            return "/" + "/".join(str(item) for item in path)
        raw = _text(url.get("raw"))
    else:
        raw = _text(url)
    if not raw:
        return "/"
    return re.sub(r"^https?://[^/]+", "", raw).split("?", 1)[0] or "/"


def _postman_blocks(source: DocumentSource, payload: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    order = len(blocks)

    def walk(items: Iterable[Any], pointer_parts: tuple[Any, ...], parent_id: str = "", folder_path: tuple[str, ...] = ()) -> bool:
        nonlocal order
        for index, raw_item in enumerate(items):
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            pointer = _pointer(*pointer_parts, index)
            name = _text(item.get("name")) or f"item_{index}"
            children = _list(item.get("item"))
            if children:
                folder_block = _block(
                    source,
                    pointer=pointer,
                    block_type="HEADING",
                    order=order + 1,
                    text=name,
                    parent_id=parent_id,
                    node_kind="POSTMAN_FOLDER",
                    metadata={"level": min(6, len(folder_path) + 1), "folder_path": [*folder_path, name]},
                )
                if not _append_block(blocks, folder_block):
                    return False
                order += 1
                counts["folder"] += 1
                if not walk(children, (*pointer_parts, index, "item"), folder_block["block_id"], (*folder_path, name)):
                    return False
                continue
            request = _dict(item.get("request"))
            if not request:
                continue
            method = _text(request.get("method") or "GET").upper()
            path = _postman_request_path(request)
            text = f"{method} {path} — {name}"
            request_block = _block(
                source,
                pointer=_pointer(*pointer_parts, index, "request"),
                block_type="KEY_VALUE",
                order=order + 1,
                text=text,
                parent_id=parent_id,
                key="request",
                value=text,
                node_kind="POSTMAN_REQUEST",
                metadata={
                    "http_method": method,
                    "api_path": path,
                    "request_name": name,
                    "folder_path": list(folder_path),
                    "body_mode": _text(_dict(request.get("body")).get("mode")),
                    "auth_type": _text(_dict(request.get("auth")).get("type")).upper(),
                    "credential_values_retained": False,
                },
            )
            if not _append_block(blocks, request_block):
                return False
            order += 1
            counts["request"] += 1
            request_parent = request_block["block_id"]
            url = _dict(request.get("url"))
            for field_name, node_kind in (("query", "POSTMAN_QUERY_PARAMETER"), ("variable", "POSTMAN_PATH_VARIABLE")):
                for field_index, raw_field in enumerate(_list(url.get(field_name))):
                    field = _dict(raw_field)
                    field_key = _text(field.get("key") or field.get("name") or f"field_{field_index}")
                    field_pointer = _pointer(*pointer_parts, index, "request", "url", field_name, field_index)
                    field_text = f"{field_name} {field_key} disabled={bool(field.get('disabled'))}"
                    if not _append_block(
                        blocks,
                        _block(
                            source,
                            pointer=field_pointer,
                            block_type="KEY_VALUE",
                            order=order + 1,
                            text=field_text,
                            parent_id=request_parent,
                            key=field_name,
                            value=field_text,
                            node_kind=node_kind,
                            metadata={"field_name": field_key, "disabled": bool(field.get("disabled")), "value_retained": False},
                        ),
                    ):
                        return False
                    order += 1
                    counts[field_name] += 1
            for header_index, raw_header in enumerate(_list(request.get("header"))):
                header = _dict(raw_header)
                header_name = _text(header.get("key") or header.get("name") or f"header_{header_index}")
                header_pointer = _pointer(*pointer_parts, index, "request", "header", header_index)
                header_text = f"header {header_name} disabled={bool(header.get('disabled'))}"
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=header_pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=header_text,
                        parent_id=request_parent,
                        key="header",
                        value=header_text,
                        node_kind="POSTMAN_HEADER",
                        metadata={"header_name": header_name, "disabled": bool(header.get("disabled")), "value_retained": False},
                    ),
                ):
                    return False
                order += 1
                counts["header"] += 1
            for event_index, raw_event in enumerate(_list(item.get("event"))):
                event = _dict(raw_event)
                listen = _text(event.get("listen")).lower()
                script = _dict(event.get("script"))
                lines = [str(line) for line in _list(script.get("exec"))]
                test_names = []
                for line in lines:
                    test_names.extend(re.findall(r"pm\.test\(\s*['\"]([^'\"]+)", line))
                event_pointer = _pointer(*pointer_parts, index, "event", event_index)
                event_text = f"script {listen or 'unknown'} lines={len(lines)} tests={len(test_names)}"
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=event_pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=event_text,
                        parent_id=request_parent,
                        key="script",
                        value=event_text,
                        node_kind="POSTMAN_SCRIPT",
                        metadata={
                            "script_kind": listen,
                            "script_line_count": len(lines),
                            "declared_test_names": test_names[:100],
                            "script_source_retained_in_metadata": False,
                        },
                    ),
                ):
                    return False
                order += 1
                counts["script"] += 1
            for response_index, raw_response in enumerate(_list(item.get("response"))):
                response = _dict(raw_response)
                response_pointer = _pointer(*pointer_parts, index, "response", response_index)
                status = response.get("code")
                response_name = _text(response.get("name") or response.get("status"))
                response_text = f"example response {status or 'unknown'}" + (f" — {response_name}" if response_name else "")
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=response_pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=response_text,
                        parent_id=request_parent,
                        key="response_example",
                        value=response_text,
                        node_kind="POSTMAN_RESPONSE_EXAMPLE",
                        metadata={"status_code": status, "example_body_retained_in_metadata": False},
                    ),
                ):
                    return False
                order += 1
                counts["response"] += 1
        return True

    walk(_list(payload.get("item")), ("item",))
    return dict(counts)


def _har_blocks(source: DocumentSource, payload: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    order = len(blocks)
    entries = _list(_dict(payload.get("log")).get("entries"))
    for index, raw_entry in enumerate(entries):
        entry = _dict(raw_entry)
        request = _dict(entry.get("request"))
        response = _dict(entry.get("response"))
        method = _text(request.get("method") or "GET").upper()
        url = _redact_url(_text(request.get("url")))
        path = "/"
        try:
            path = urllib.parse.urlsplit(url).path or "/"
        except ValueError:
            pass
        status = response.get("status")
        pointer = _pointer("log", "entries", index)
        text = f"{method} {path} -> {status if status is not None else 'unknown'}"
        block = _block(
            source,
            pointer=pointer,
            block_type="KEY_VALUE",
            order=order + 1,
            text=text,
            key="har_entry",
            value=text,
            node_kind="HAR_ENTRY",
            metadata={
                "http_method": method,
                "request_url": url,
                "api_path": path,
                "response_status": status,
                "elapsed_ms": entry.get("time"),
                "started_at": _text(entry.get("startedDateTime")),
                "response_mime_type": _text(_dict(response.get("content")).get("mimeType")),
                "credential_values_retained": False,
                "response_body_retained_in_metadata": False,
            },
        )
        if not _append_block(blocks, block):
            return dict(counts)
        order += 1
        counts["entry"] += 1
        parent_id = block["block_id"]
        for collection_name, rows, node_kind in (
            ("request_header", _list(request.get("headers")), "HAR_REQUEST_HEADER"),
            ("query", _list(request.get("queryString")), "HAR_QUERY_PARAMETER"),
            ("cookie", _list(request.get("cookies")), "HAR_REQUEST_COOKIE"),
            ("response_header", _list(response.get("headers")), "HAR_RESPONSE_HEADER"),
        ):
            for field_index, raw_field in enumerate(rows):
                field = _dict(raw_field)
                name = _text(field.get("name") or field.get("key") or f"field_{field_index}")
                field_pointer = _pointer("log", "entries", index, "request" if collection_name != "response_header" else "response", {
                    "request_header": "headers",
                    "query": "queryString",
                    "cookie": "cookies",
                    "response_header": "headers",
                }[collection_name], field_index)
                field_text = f"{collection_name} {name}"
                if not _append_block(
                    blocks,
                    _block(
                        source,
                        pointer=field_pointer,
                        block_type="KEY_VALUE",
                        order=order + 1,
                        text=field_text,
                        parent_id=parent_id,
                        key=collection_name,
                        value=field_text,
                        node_kind=node_kind,
                        metadata={"field_name": name, "value_retained": False},
                    ),
                ):
                    return dict(counts)
                order += 1
                counts[collection_name] += 1
    return dict(counts)


def _blocked_ir(source: DocumentSource, detail: str) -> dict[str, Any]:
    unsupported = [
        {
            "kind": "API_ARTIFACT_PARSE_FAILED",
            "count": 1,
            "status": "BLOCKED",
            "severity": "P0",
            "blocks_formal_understanding": True,
            "reason_code": "API_ARTIFACT_PARSE_FAILED",
            "detail": str(detail or "")[:500],
            "included_in_plain_text_authority": False,
        }
    ]
    return {
        "schema": DOCUMENT_IR_SCHEMA,
        "format": source.suffix.lstrip(".") or "api-artifact",
        "filename": source.filename,
        "plain_text": "",
        "blocks": [],
        "sections": [],
        "tables": [],
        "unsupported_content": unsupported,
        "artifact_structure": {
            "schema": API_ARTIFACT_STRUCTURE_SCHEMA,
            "artifact_kind": "unknown",
            "credential_values_retained": False,
        },
        "structure_receipt": {
            "schema": STRUCTURE_RECEIPT_SCHEMA,
            "status": "BLOCKED",
            "format": source.suffix.lstrip(".") or "api-artifact",
            "block_count": 0,
            "source_traceability_rate": 0.0,
            "block_type_distribution": {},
            "section_count": 0,
            "unsupported_content_count": 1,
            "unsupported_content": unsupported,
            "document_order_is_business_flow": False,
            "filename_is_business_context": False,
            "generic_text_fallback": False,
            "api_artifact_adapter": True,
        },
    }


class ApiArtifactDocumentAdapter(DocumentAdapter):
    name = "api-artifact-json-pointer-structure"
    parser_version = "1"
    priority = 95
    mode = MODE_PRIMARY
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
        }
    )

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        payload, _decoded, error = _decode_payload(source)
        if payload is None:
            if source.suffix == ".har":
                return AdapterMatch(
                    self.name,
                    96,
                    "har_suffix_parse_blocked",
                    tuple(sorted(self.capabilities)),
                    self.mode,
                    runtime_ready=True,
                    runtime_reason=error,
                )
            return None
        kind = _artifact_kind(payload)
        if not kind:
            return None
        score = {"openapi": 118, "postman": 116, "har": 120}[kind]
        return AdapterMatch(
            self.name,
            score,
            f"source_declared_{kind}_structure",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        payload, _decoded, error = _decode_payload(source)
        if payload is None:
            return _blocked_ir(source, error)
        kind = _artifact_kind(payload)
        if not kind:
            return _blocked_ir(source, "recognized adapter received a non-API artifact")

        sanitized = _redacted_copy(deepcopy(payload))
        canonical = json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=False)
        root_pointer = "/"
        root_block = _block(
            source,
            pointer=root_pointer,
            block_type="PARAGRAPH",
            order=1,
            text=canonical,
            node_kind="API_ARTIFACT_CANONICAL_PROJECTION",
            metadata={
                "artifact_kind": kind,
                "semantic_projection_authority": True,
                "canonical_json_projection": True,
                "credential_values_retained": False,
            },
            excluded_from_projection=False,
        )
        blocks = [root_block]
        if kind == "openapi":
            counts = _openapi_blocks(source, sanitized, blocks)
        elif kind == "postman":
            counts = _postman_blocks(source, sanitized, blocks)
        else:
            counts = _har_blocks(source, sanitized, blocks)

        truncated = len(blocks) >= _MAX_STRUCTURAL_BLOCKS
        unsupported: list[dict[str, Any]] = []
        status = "COMPLETE"
        if truncated:
            unsupported.append(
                {
                    "kind": "API_ARTIFACT_STRUCTURAL_BLOCK_LIMIT_REACHED",
                    "count": 1,
                    "status": "PARTIAL",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "reason_code": "API_ARTIFACT_STRUCTURAL_BLOCK_LIMIT_REACHED",
                    "included_in_plain_text_authority": True,
                    "limit": _MAX_STRUCTURAL_BLOCKS,
                }
            )
            status = "PARTIAL"
        block_counts = Counter(str(row.get("type") or "") for row in blocks)
        artifact_structure = {
            "schema": API_ARTIFACT_STRUCTURE_SCHEMA,
            "artifact_kind": kind,
            "node_counts": counts,
            "json_pointer_block_count": max(0, len(blocks) - 1),
            "canonical_projection_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "credential_values_retained": False,
            "redaction_schema": API_ARTIFACT_REDACTION_SCHEMA,
            "business_semantics_added": False,
        }
        return {
            "schema": DOCUMENT_IR_SCHEMA,
            "format": source.suffix.lstrip(".") or kind,
            "filename": source.filename,
            "plain_text": canonical,
            "blocks": blocks,
            "sections": [],
            "tables": [],
            "unsupported_content": unsupported,
            "artifact_structure": artifact_structure,
            "structure_receipt": {
                "schema": STRUCTURE_RECEIPT_SCHEMA,
                "status": status,
                "format": source.suffix.lstrip(".") or kind,
                "block_count": len(blocks),
                "source_traceability_rate": 1.0 if blocks else 0.0,
                "json_pointer_traceability_rate": 1.0 if blocks else 0.0,
                "block_type_distribution": dict(block_counts),
                "section_count": 0,
                "unsupported_content_count": len(unsupported),
                "unsupported_content": unsupported,
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
                "generic_text_fallback": False,
                "api_artifact_adapter": True,
                "artifact_kind": kind,
                "credential_values_retained": False,
                "business_semantics_added": False,
            },
        }


__all__ = [
    "API_ARTIFACT_STRUCTURE_SCHEMA",
    "API_ARTIFACT_REDACTION_SCHEMA",
    "ApiArtifactDocumentAdapter",
]
