"""Exact runtime SQL-DDL authority for binding identity graphs.

Only source-declared technical structure is projected. Identifier similarity never creates
relationships; a body reference can benefit only from an explicit SQL FOREIGN KEY.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import quote

from ._parsing_mechanics import _sql_column_type, _sql_table_body_declarations

AUTHORITY = "DATABASE_MODEL_SOURCE_DECLARATION"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _unquote(value: str) -> str:
    token = _text(value)
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', '`'}:
        return token[1:-1]
    if len(token) >= 2 and token[0] == '[' and token[-1] == ']':
        return token[1:-1]
    return token


def _qualified(value: str) -> tuple[str, str]:
    parts = [_unquote(part) for part in re.split(r"\s*\.\s*", _text(value)) if _text(part)]
    return (parts[-2], parts[-1]) if len(parts) >= 2 else ("", parts[-1] if parts else "")


def _line_locator(text: str, filename: str, offset: int, suffix: str) -> str:
    line = text.count("\n", 0, max(0, offset)) + 1
    return f"{filename}#line={line};{suffix}"


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    quote_char = ""
    index = open_index
    while index < len(text):
        char = text[index]
        if quote_char:
            if char == quote_char:
                quote_char = ""
            elif char == "\\":
                index += 1
        elif char in {'"', "'", '`'}:
            quote_char = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _identifier_list(value: str) -> list[str]:
    return [
        _unquote(part.strip())
        for part in value.split(',')
        if _unquote(part.strip())
    ]


def _reference(fragment: str) -> tuple[str, str, list[str]] | None:
    match = re.search(
        r'''(?is)\bREFERENCES\s+(?P<table>(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*))?)\s*\((?P<columns>[^)]*)\)''',
        fragment,
    )
    if not match:
        return None
    schema, table = _qualified(match.group("table"))
    return schema, table, _identifier_list(match.group("columns"))


def _evidence(source_id: str, locator: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_locator": locator,
        "exact": bool(source_id and locator),
    }

