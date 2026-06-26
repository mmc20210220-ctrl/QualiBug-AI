from __future__ import annotations

"""
Document Change Watcher — automatic re-analysis when specs change.

Enterprise PRDs, MRDs, and API docs are living documents. When they change,
QualiBug must automatically:
1. Detect the change (file hash comparison)
2. Determine what's affected (which services, which oracles)
3. Re-run industry inference on changed documents
4. Re-generate oracles for affected contracts
5. Flag stale previous findings for re-validation

Architecture:
    File watcher (hash-based)
        │
        ├── PRD changed → re-run industry inference → update business model
        ├── OpenAPI changed → re-parse contracts → re-generate oracles
        └── Both changed → full re-analysis
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _safe_project_id, _read_text, _write_json, _load_json


# ---------------------------------------------------------------------------
# Document snapshot and change detection
# ---------------------------------------------------------------------------

class DocumentChangeWatcher:
    """Tracks document versions and detects what needs re-analysis."""

    def __init__(self, project_id: str, root: Path | None = None):
        self.project_id = _safe_project_id(project_id)
        self.root = root or ROOT
        self._snapshot_path = self.root / "platform_workspace" / project_id / "doc_snapshot.json"

    def snapshot(self, documents: dict[str, str]) -> dict[str, Any]:
        """Take a snapshot of current document hashes.

        Args:
            documents: {name: file_path} mapping (e.g. {"prd": "docs/prd.md", "openapi": "docs/api.yaml"})
        """
        hashes: dict[str, str] = {}
        for name, path_str in documents.items():
            path = Path(path_str) if path_str else None
            if path and path.exists():
                content = _read_text(path)
                hashes[name] = _hash_content(content)
            else:
                hashes[name] = ""

        snapshot = {
            "project_id": self.project_id,
            "snapshot_at": _now(),
            "files": hashes,
            "document_count": len(hashes),
        }
        self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self._snapshot_path, snapshot)
        return snapshot

    def changes_since_last_snapshot(self, documents: dict[str, str]) -> dict[str, Any]:
        """Compare current documents against the last snapshot.

        Returns:
            {
                "has_changes": bool,
                "changed_files": ["prd", "openapi", ...],
                "added_files": [...],
                "removed_files": [...],
                "requires": {
                    "industry_re_inference": bool,
                    "oracle_re_generation": bool,
                    "full_re_analysis": bool,
                }
            }
        """
        previous = _load_json(self._snapshot_path, {})
        prev_hashes = previous.get("files", {})

        current_hashes: dict[str, str] = {}
        changed: list[str] = []
        added: list[str] = []
        removed: list[str] = []

        for name, path_str in documents.items():
            path = Path(path_str) if path_str else None
            if path and path.exists():
                content = _read_text(path)
                current_hashes[name] = _hash_content(content)

                if name not in prev_hashes:
                    added.append(name)
                elif current_hashes[name] != prev_hashes[name]:
                    changed.append(name)
            else:
                current_hashes[name] = ""
                if name in prev_hashes and prev_hashes[name]:
                    removed.append(name)

        # Determine what needs re-analysis
        prd_changed = "prd" in changed or "prd" in added
        openapi_changed = "openapi" in changed or "openapi" in added
        mrd_changed = "mrd" in changed or "mrd" in added

        return {
            "has_changes": bool(changed or added or removed),
            "changed_files": changed,
            "added_files": added,
            "removed_files": removed,
            "requires": {
                "industry_re_inference": prd_changed or mrd_changed,
                "oracle_re_generation": openapi_changed,
                "full_re_analysis": prd_changed and openapi_changed,
            },
            "previous_snapshot_at": previous.get("snapshot_at", "never"),
        }

    def auto_react(self, documents: dict[str, str]) -> dict[str, Any]:
        """Full auto-reaction: snapshot, detect changes, trigger re-analysis.

        Returns a dict with actions taken.
        """
        changes = self.changes_since_last_snapshot(documents)
        actions: list[str] = []

        if not changes["has_changes"]:
            return {"status": "no_changes", "actions": [], "changes": changes}

        if changes["requires"]["industry_re_inference"]:
            actions.append("re_run_industry_inference")
        if changes["requires"]["oracle_re_generation"]:
            actions.append("re_generate_oracles")
        if changes["requires"]["full_re_analysis"]:
            actions.append("full_re_analysis")

        # Re-snapshot after processing
        self.snapshot(documents)

        return {
            "status": "changes_detected",
            "actions_taken": actions,
            "changes": changes,
        }


# ---------------------------------------------------------------------------
# Git integration: detect changed files from git diff
# ---------------------------------------------------------------------------

def detect_changed_documents_from_git(
    project_id: str,
    base_branch: str = "main",
    root: Path | None = None,
) -> dict[str, Any]:
    """Use git diff to detect which project documents changed.

    This is the CI integration point: on every PR/push, detect what changed
    and only re-analyze the affected services.
    """
    import subprocess
    root = root or ROOT

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_branch + "...HEAD"],
            cwd=str(root),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {"error": "git_diff_failed", "stderr": result.stderr[:500]}

        changed_files = [f.strip() for f in result.stdout.split("\n") if f.strip()]
    except Exception as e:
        return {"error": str(e)[:200]}

    # Classify changes
    prd_changed = any("prd" in f.lower() or "mrd" in f.lower() for f in changed_files)
    openapi_changed = any("openapi" in f.lower() or "swagger" in f.lower() or f.endswith((".yaml", ".yml", ".json")) for f in changed_files)
    code_changed = any(f.endswith(".py") or f.endswith(".js") or f.endswith(".java") or f.endswith(".go") for f in changed_files)

    # Determine affected services (by directory convention)
    affected_services: set[str] = set()
    for f in changed_files:
        parts = Path(f).parts
        if len(parts) > 1 and parts[0] in ("services", "src", "app", "modules"):
            affected_services.add(parts[1] if len(parts) > 1 else parts[0])

    return {
        "changed_files": changed_files[:50],
        "changed_count": len(changed_files),
        "prd_changed": prd_changed,
        "openapi_changed": openapi_changed,
        "code_changed": code_changed,
        "affected_services": sorted(affected_services)[:20],
        "requires": {
            "industry_re_inference": prd_changed,
            "oracle_re_generation": openapi_changed,
            "regression_tests": code_changed,
            "incremental_only": not (prd_changed and openapi_changed),
        },
    }


# ---------------------------------------------------------------------------
# Multi-format document ingestion
# ---------------------------------------------------------------------------

def ingest_document(path: str | Path) -> dict[str, Any]:
    """Ingest a document from any supported format and extract structured text.

    Supported formats:
    - .md / .txt: read directly
    - .json / .yaml / .yml: parse as OpenAPI or config
    - .docx: extract text (requires python-docx or pypdf fallback)
    - .pdf: extract text (requires pypdf)
    - .html: strip tags and extract text
    """
    path = Path(path) if isinstance(path, str) else path
    if not path.exists():
        return {"ok": False, "error": "file_not_found", "path": str(path)}

    suffix = path.suffix.lower()
    result: dict[str, Any] = {"ok": True, "path": str(path), "format": suffix}

    try:
        if suffix in (".md", ".txt", ".markdown"):
            result["text"] = path.read_text(encoding="utf-8", errors="replace")
            result["line_count"] = len(result["text"].splitlines())

        elif suffix in (".json",):
            raw = path.read_text(encoding="utf-8", errors="replace")
            result["text"] = raw
            try:
                result["parsed"] = json.loads(raw)
                result["is_openapi"] = "openapi" in raw.lower() or "swagger" in raw.lower()
            except json.JSONDecodeError:
                result["parsed"] = None

        elif suffix in (".yaml", ".yml"):
            raw = path.read_text(encoding="utf-8", errors="replace")
            result["text"] = raw
            result["is_openapi"] = "openapi" in raw.lower() or "swagger" in raw.lower()

        elif suffix in (".pdf",):
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                text_parts = []
                for page in reader.pages:
                    t = page.extract_text()
                    if t:
                        text_parts.append(t)
                result["text"] = "\n".join(text_parts)
                result["page_count"] = len(reader.pages)
            except ImportError:
                result["text"] = _read_text(path)
                result["warning"] = "pypdf not installed, raw text extraction may include binary noise"

        elif suffix in (".docx",):
            try:
                from docx import Document
                doc = Document(str(path))
                text_parts = [p.text for p in doc.paragraphs]
                result["text"] = "\n".join(text_parts)
            except ImportError:
                result["text"] = _read_text(path)
                result["warning"] = "python-docx not installed, raw text extraction may be noisy"

        elif suffix in (".html", ".htm"):
            raw = path.read_text(encoding="utf-8", errors="replace")
            # Simple HTML tag stripping
            import re
            text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL | re.I)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            result["text"] = text

        else:
            # Unknown format — try as text
            result["text"] = _read_text(path)
            result["warning"] = f"Unknown format {suffix}, treated as plain text"

    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)[:300]

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
