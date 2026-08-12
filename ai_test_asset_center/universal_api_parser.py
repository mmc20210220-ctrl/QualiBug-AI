"""Safe public facade for the universal API parser.

The historical implementation is preserved byte-for-byte in
``universal_api_parser_mainline_base``.  This boundary only makes string path
probing fail-soft so compact OpenAPI JSON is never sent to ``stat(2)`` as a
multi-kilobyte filename.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from . import universal_api_parser_mainline_base as _base

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


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
