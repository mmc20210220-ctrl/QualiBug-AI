from __future__ import annotations

from typing import Any


def dump_yaml(data: Any, indent: int = 0) -> str:
    """A tiny YAML writer for simple dict/list/scalar structures.

    We avoid external dependencies so the MVP can run with only pytest.
    """
    space = "  " * indent

    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{space}{key}:")
                lines.append(dump_yaml(value, indent + 1))
            else:
                lines.append(f"{space}{key}: {format_scalar(value)}")
        return "\n".join(lines)

    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, dict):
                lines.append(f"{space}-")
                lines.append(dump_yaml(item, indent + 1))
            elif isinstance(item, list):
                lines.append(f"{space}-")
                lines.append(dump_yaml(item, indent + 1))
            else:
                lines.append(f"{space}- {format_scalar(item)}")
        return "\n".join(lines)

    return f"{space}{format_scalar(data)}"


def format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in [":", "#", "{", "}", "[", "]", "\n"]):
        return repr(text)
    return text
