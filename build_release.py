#!/usr/bin/env python
"""Build a clean, auditable private-deployment archive for QualiBug.

The builder intentionally has no import-time side effects.  It excludes runtime
state, credentials, local test artefacts and internal MES ground truth before
creating the archive, then re-audits the ZIP itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BUILD_DIR = PROJECT_ROOT / "build" / "phase91_release"
DEFAULT_ZIP = PROJECT_ROOT / "build" / "QualiBug_Phase91_Private_Deployment.zip"

# Match POSIX-normalized relative paths.  Exclusions are intentionally broad:
# customer delivery must never contain local credentials, test data, state or
# compiled artefacts.
EXCLUDED_PARTS = {
    ".git", ".pytest_cache", "__pycache__", "platform_outputs", "platform_workspace",
    "build", "dist", "node_modules", ".idea", ".vscode", ".agents", "mes_target",
    "mes_oracle", "tools", "tmp", "temp", "logs", "runtime", "candidate_worktrees", "platform_inputs",
}
EXCLUDED_NAMES = {
    ".env", ".env.local", "bug_memory.jsonl", ".loop_heartbeat.json",
    ".discovery_result.json", ".loop_runtime.sqlite", ".loop_runtime_final.json", "verify_findings.py",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".db", ".sqlite", ".sqlite3", ".log", ".zip", ".7z", ".rar"}

# Credential-like values only.  Names such as "api_key" in source are not
# secrets; values must resemble a usable credential to fail the audit.
SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "provider_key"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "aws_access_key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I), "private_key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]{20,}\b", re.I), "bearer_token"),
    # Require a quoted, literal-looking value for generic assignments.  Runtime
    # variables such as ``token = _normal_token(...)`` are not secrets and must
    # not make a private deployment archive impossible to build. Provider-key
    # and private-key patterns above still catch real credential material.
    (re.compile(r"(?i)(?:api[_-]?key|secret|password|token)\s*[=:]\s*['\"][A-Za-z0-9._~+\-/=]{24,}['\"]"), "assigned_secret"),
)


def _normalized(path: Path) -> str:
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def should_exclude(relative: Path) -> bool:
    parts = set(relative.parts)
    if parts & EXCLUDED_PARTS:
        return True
    if any(part.lower().lstrip(".").startswith("pytest_tmp") for part in relative.parts):
        return True
    name = relative.name
    if name in EXCLUDED_NAMES:
        return True
    if name.startswith(".env") and name != ".env.example" and name != ".env.local.example":
        return True
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    norm = _normalized(relative)
    if norm.startswith(("tests/", "test_outputs/")):
        # Customer delivery does not ship CI test fixtures / test output.
        return True
    if name.endswith((".tmp", ".bak", "~")):
        return True
    return False


def iter_release_files(root: Path = PROJECT_ROOT) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if should_exclude(rel):
            continue
        yield path


def scan_file_for_secrets(path: Path) -> list[dict[str, str | int]]:
    # Binary content is intentionally not scanned as it is excluded by suffix.
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    findings: list[dict[str, str | int]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append({"type": label, "line": line_no, "sample": line[:160]})
                break
    return findings


def build_release(*, root: Path = PROJECT_ROOT, output_zip: Path = DEFAULT_ZIP, build_dir: Path = DEFAULT_BUILD_DIR) -> dict:
    root = root.resolve()
    output_zip = output_zip.resolve()
    build_dir = build_dir.resolve()
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    excluded: list[str] = []
    sensitive: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        # ZIP entry names must be valid UTF-8.  Ignore corrupted filesystem
        # residue rather than producing an archive that a Windows customer
        # cannot open.  The exclusion is recorded for auditability.
        try:
            normalized_rel = _normalized(rel)
            normalized_rel.encode("utf-8")
        except UnicodeEncodeError:
            excluded.append(f"<non_utf8_path:{rel!r}>")
            continue
        if should_exclude(rel):
            excluded.append(normalized_rel)
            continue
        findings = scan_file_for_secrets(path)
        if findings:
            sensitive.append({"file": _normalized(rel), "findings": findings})
            continue
        target = build_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        included.append(normalized_rel)

    if sensitive:
        shutil.rmtree(build_dir, ignore_errors=True)
        return {
            "ok": False,
            "reason": "sensitive_content_detected",
            "sensitive_findings": sensitive,
            "included_count": 0,
            "excluded_count": len(excluded),
        }

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file():
                archive.write(path, _normalized(path.relative_to(build_dir)))

    audit = audit_release_package(output_zip)
    if not audit["ok"]:
        output_zip.unlink(missing_ok=True)
        return {
            "ok": False,
            "reason": "archive_audit_failed",
            "archive_audit": audit,
            "included_count": len(included),
            "excluded_count": len(excluded),
        }

    manifest = {
        "phase": "phase91_cognitive_memory_graph",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive": str(output_zip),
        "archive_sha256": hashlib.sha256(output_zip.read_bytes()).hexdigest(),
        "archive_size_bytes": output_zip.stat().st_size,
        "included_count": len(included),
        "excluded_count": len(excluded),
        "included_files": sorted(included),
        "excluded_files": sorted(excluded),
        "archive_audit": audit,
        "delivery_boundary": "No credentials, runtime state, customer data, MES ground truth, caches, logs or test artefacts are included.",
    }
    manifest_path = output_zip.parent / "PHASE91_PACKAGE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, **manifest, "manifest_path": str(manifest_path)}


def audit_release_package(archive_path: Path) -> dict:
    if not archive_path.exists():
        return {"ok": False, "reason": "archive_missing", "violations": []}
    violations: list[dict[str, str]] = []
    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            rel = Path(name)
            if should_exclude(rel):
                violations.append({"file": name, "reason": "excluded_path_present"})
                continue
            if rel.name.startswith(".env") and rel.name not in {".env.example", ".env.local.example"}:
                violations.append({"file": name, "reason": "credential_file_present"})
    return {"ok": not violations, "archive": str(archive_path), "file_count": len(zipfile.ZipFile(archive_path).namelist()), "violations": violations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and audit a clean QualiBug private-deployment ZIP")
    parser.add_argument("--output", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--audit", type=Path, help="Audit an existing ZIP without building")
    args = parser.parse_args(argv)
    if args.audit:
        result = audit_release_package(args.audit)
    else:
        result = build_release(output_zip=args.output, build_dir=args.build_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
