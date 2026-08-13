"""Safe public facade for the universal API parser.

The historical implementation is preserved byte-for-byte in
``universal_api_parser_mainline_base``.  This boundary makes string path
probing fail-soft and preserves OpenAPI security declaration provenance before
operation records enter Behavior IR.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from . import universal_api_parser_mainline_base as _base
from .openapi_security_authority import stamp_openapi_operation_security

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


def _existing_string_path(value: str) -> Path | None:
    if "\n" in value or "\r" in value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        path = Path(candidate)
        return path if path.exists() else None
    except (OSError, ValueError):
        return None


def parse_to_openapi(text_or_path: str | Path) -> dict[str, Any]:
    text = ""
    filename = ""
    string_path = _existing_string_path(text_or_path) if isinstance(text_or_path, str) else None
    if isinstance(text_or_path, Path) or string_path is not None:
        path = text_or_path if isinstance(text_or_path, Path) else string_path
        assert path is not None
        filename = path.name
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        text = str(text_or_path)

    fmt = _base.detect_format(text, filename)
    try:
        print(f"  [INFO] universal_api_parser: detected format={fmt}", flush=True)
    except OSError:
        pass
    if fmt == "openapi3":
        return _base._normalize_openapi3(text)
    if fmt == "swagger2":
        return _base._convert_swagger2(text)
    if fmt == "postman":
        return _base._convert_postman(text)
    if fmt == "graphql":
        return _base._convert_graphql(text)
    if fmt == "grpc":
        return _base._convert_grpc(text)
    if fmt == "har":
        return _base._convert_har(text_or_path if (isinstance(text_or_path, Path) or string_path is not None) else text)
    if fmt == "markdown_api":
        return _base._convert_markdown_api(text)
    try:
        print("  [WARN] universal_api_parser: unknown format, returning empty spec", flush=True, file=_base.sys.stderr)
    except OSError:
        pass
    return _base._empty_spec()


# Functions defined in the preserved module resolve this global dynamically.
_base.parse_to_openapi = parse_to_openapi
_original_build_api_operations_from_text = _base.build_api_operations_from_text


def build_api_operations_from_text(
    api_spec_text: str,
    *,
    submitted_source_text: str = "",
) -> list[dict[str, Any]]:
    operations = _original_build_api_operations_from_text(
        api_spec_text,
        submitted_source_text=submitted_source_text,
    )
    source_documents = [("api_spec", str(api_spec_text or "").strip())]
    submitted = str(submitted_source_text or "").strip()
    if submitted and submitted != source_documents[0][1]:
        source_documents.append(("submitted_api_spec", submitted))
    for source_id, source_text in source_documents:
        if not source_text:
            continue
        spec = parse_to_openapi(source_text)
        scoped = [row for row in operations if str(row.get("source_id") or "") == source_id]
        stamp_openapi_operation_security(scoped, spec)
    return operations


_base.build_api_operations_from_text = build_api_operations_from_text


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
