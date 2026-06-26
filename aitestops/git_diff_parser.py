from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List
import re


@dataclass
class ChangedFile:
    path: str
    added_lines: int
    removed_lines: int
    touched_keywords: List[str]
    raw_snippets: List[str]


class GitDiffParser:
    """Parse a unified git diff without requiring git to be installed.

    V4 uses this as the entry point for impact analysis. In enterprise mode this
    can be replaced by GitLab/GitHub API, but the parser keeps the demo fully local.
    """

    KEYWORDS = [
        "health", "product", "products", "order", "orders", "admin", "user", "users",
        "permission", "role", "stock", "quantity", "price", "login", "auth",
    ]

    def parse_file(self, diff_path: Path) -> List[ChangedFile]:
        text = diff_path.read_text(encoding="utf-8")
        return self.parse_text(text)

    def parse_text(self, text: str) -> List[ChangedFile]:
        files: Dict[str, ChangedFile] = {}
        current: ChangedFile | None = None

        for line in text.splitlines():
            if line.startswith("diff --git "):
                path = self._path_from_diff_header(line)
                current = files.setdefault(path, ChangedFile(path=path, added_lines=0, removed_lines=0, touched_keywords=[], raw_snippets=[]))
                continue
            if current is None:
                continue
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                current.added_lines += 1
                self._collect(current, line[1:])
            elif line.startswith("-"):
                current.removed_lines += 1
                self._collect(current, line[1:])

        return list(files.values())

    def _collect(self, changed: ChangedFile, content: str) -> None:
        normalized = content.lower()
        for keyword in self.KEYWORDS:
            if keyword in normalized and keyword not in changed.touched_keywords:
                changed.touched_keywords.append(keyword)
        # Keep compact evidence only.
        if content.strip() and len(changed.raw_snippets) < 12:
            changed.raw_snippets.append(content.strip()[:180])

    @staticmethod
    def _path_from_diff_header(line: str) -> str:
        # diff --git a/demo_system/api_service.py b/demo_system/api_service.py
        parts = line.split()
        if len(parts) >= 4:
            right = parts[3]
            if right.startswith("b/"):
                return right[2:]
            return right
        return "unknown"


def changed_files_to_dict(files: List[ChangedFile]) -> List[Dict[str, object]]:
    return [asdict(f) for f in files]
