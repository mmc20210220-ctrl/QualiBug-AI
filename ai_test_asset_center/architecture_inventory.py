"""Deterministic, non-destructive module strangler inventory.

The inventory identifies architectural reachability and retirement candidates;
it never deletes files and never turns architecture metrics into discovery
quality claims.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import tomllib
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable


ARCHITECTURE_ROOTS_SCHEMA = "qualibug.architecture-roots.v1"
IMPORT_TRACE_SCHEMA = "qualibug.python-import-trace.v1"
INVENTORY_SCHEMA = "qualibug.architecture-inventory.v1"
RESPONSIBILITY_CLASSES = frozenset({
    "core",
    "adapter",
    "compatibility",
    "diagnostic",
    "retirement_candidate",
})
_IGNORED_PARTS = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest_tmp",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "_audit_packs",
    "_funnel_runs",
    "_private_eval",
    "build",
    "dist",
    "node_modules",
    "platform_outputs",
    "platform_workspace",
    "venv",
})
_IGNORED_PARTS_CASEFOLDED = frozenset(part.casefold() for part in _IGNORED_PARTS)
_ENTRYPOINT_STATUSES = frozenset({
    "adapter",
    "canonical",
    "compatibility",
    "deprecated",
})
_ADAPTER_TERMS = (
    "adapter",
    "api",
    "bridge",
    "cli",
    "connector",
    "entrypoint",
    "executor",
    "service",
    "transport",
)
_COMPATIBILITY_TERMS = (
    "compat",
    "legacy",
    "migration",
    "patch",
    "shim",
    "wrapper",
)
_DIAGNOSTIC_TERMS = (
    "audit",
    "benchmark",
    "diagnostic",
    "doctor",
    "evaluation",
    "health",
    "inventory",
    "metrics",
    "observability",
    "report",
    "trace",
)


class ArchitectureInventoryError(ValueError):
    """The source graph or its declared roots cannot be audited safely."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical(value).encode("utf-8"))


def _read_source_snapshot(path: Path) -> tuple[str, str]:
    try:
        # Python accepts an UTF-8 BOM as an encoding signature.  Strip only
        # that signature before handing source to ``ast.parse`` so the
        # inventory follows the interpreter's source-decoding semantics.
        raw = path.read_bytes()
        return raw.decode("utf-8-sig"), _sha256_bytes(raw)
    except (OSError, UnicodeError) as exc:
        raise ArchitectureInventoryError(
            f"python_source_unreadable:{path}:{type(exc).__name__}:{exc}"
        ) from exc


def _module_name(repo_root: Path, path: Path) -> str:
    relative = path.relative_to(repo_root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts or not all(part.isidentifier() for part in parts):
        return ""
    return ".".join(parts)


def _python_sources(repo_root: Path) -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in sorted(repo_root.rglob("*.py")):
        relative = path.relative_to(repo_root)
        if any(
            part.casefold() in _IGNORED_PARTS_CASEFOLDED
            for part in relative.parts
        ):
            continue
        module = _module_name(repo_root, path)
        if not module:
            continue
        existing = modules.get(module)
        if existing is not None and existing != path:
            raise ArchitectureInventoryError(f"duplicate_python_module:{module}")
        modules[module] = path
    return modules


def _load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchitectureInventoryError(
            f"architecture_roots_invalid:{path}:{type(exc).__name__}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ArchitectureInventoryError("architecture_roots_not_object")
    if value.get("schema_version") != ARCHITECTURE_ROOTS_SCHEMA:
        raise ArchitectureInventoryError("architecture_roots_schema_invalid")
    package = _text(value.get("package"))
    if not package or not all(part.isidentifier() for part in package.split(".")):
        raise ArchitectureInventoryError("architecture_package_invalid")
    roots_value = value.get("supported_roots")
    if not isinstance(roots_value, dict):
        raise ArchitectureInventoryError("architecture_supported_roots_invalid")
    roots = roots_value
    for category in ("product", "evaluation", "tooling"):
        rows = roots.get(category)
        if (
            not isinstance(rows, list)
            or not all(
                isinstance(row, str)
                and _text(row)
                and all(part.isidentifier() for part in _text(row).split("."))
                for row in rows
            )
        ):
            raise ArchitectureInventoryError(
                f"architecture_supported_roots_invalid:{category}"
            )
    overrides_value = value.get("module_class_overrides")
    if not isinstance(overrides_value, dict):
        raise ArchitectureInventoryError(
            "architecture_module_class_overrides_invalid"
        )
    overrides = overrides_value
    if not all(
        isinstance(module, str)
        and _text(module)
        and all(part.isidentifier() for part in _text(module).split("."))
        for module in overrides
    ):
        raise ArchitectureInventoryError(
            "architecture_module_class_overrides_invalid"
        )
    invalid_classes = sorted({
        _text(item)
        for item in overrides.values()
        if _text(item) not in RESPONSIBILITY_CLASSES - {"retirement_candidate"}
    })
    if invalid_classes:
        raise ArchitectureInventoryError(
            f"architecture_module_class_invalid:{invalid_classes[0]}"
        )
    threshold_value = value.get("oversized_line_threshold")
    if isinstance(threshold_value, bool):
        raise ArchitectureInventoryError(
            "architecture_oversized_line_threshold_invalid"
        )
    try:
        threshold = int(threshold_value)
    except (TypeError, ValueError) as exc:
        raise ArchitectureInventoryError(
            "architecture_oversized_line_threshold_invalid"
        ) from exc
    if threshold <= 0:
        raise ArchitectureInventoryError(
            "architecture_oversized_line_threshold_invalid"
        )
    budget = value.get("architecture_budget")
    if budget is not None:
        if not isinstance(budget, dict):
            raise ArchitectureInventoryError("architecture_budget_invalid")
        for field in ("max_module_count", "max_python_line_count"):
            budget_value = budget.get(field)
            if isinstance(budget_value, bool):
                raise ArchitectureInventoryError(
                    f"architecture_budget_invalid:{field}"
                )
            try:
                numeric = int(budget_value)
            except (TypeError, ValueError) as exc:
                raise ArchitectureInventoryError(
                    f"architecture_budget_invalid:{field}"
                ) from exc
            if numeric <= 0:
                raise ArchitectureInventoryError(
                    f"architecture_budget_invalid:{field}"
                )
    entrypoints_value = value.get("discovery_entrypoints")
    if not isinstance(entrypoints_value, list) or not entrypoints_value:
        raise ArchitectureInventoryError("architecture_entrypoints_invalid")
    names: set[str] = set()
    identities: set[tuple[str, str]] = set()
    canonical_count = 0
    for raw in entrypoints_value:
        if not isinstance(raw, dict):
            raise ArchitectureInventoryError("architecture_entrypoint_invalid")
        name = _text(raw.get("name"))
        module = _text(raw.get("module"))
        callable_name = _text(raw.get("callable"))
        status = _text(raw.get("status")).lower()
        if (
            not name
            or not module
            or not callable_name
            or not all(part.isidentifier() for part in module.split("."))
            or not all(part.isidentifier() for part in callable_name.split("."))
            or status not in _ENTRYPOINT_STATUSES
        ):
            raise ArchitectureInventoryError("architecture_entrypoint_invalid")
        if name in names or (module, callable_name) in identities:
            raise ArchitectureInventoryError("architecture_entrypoint_duplicate")
        names.add(name)
        identities.add((module, callable_name))
        canonical_count += int(status == "canonical")
    if canonical_count != 1:
        raise ArchitectureInventoryError(
            "architecture_canonical_entrypoint_count_invalid"
        )
    return value


def _project_script_entries(repo_root: Path) -> list[dict[str, str]]:
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ArchitectureInventoryError(
            f"pyproject_invalid:{type(exc).__name__}:{exc}"
        ) from exc
    project = payload.get("project")
    if not isinstance(project, dict):
        return []

    def parse_target(value: Any) -> tuple[str, str]:
        if not isinstance(value, str):
            raise ArchitectureInventoryError("pyproject_script_target_invalid")
        module, separator, callable_value = value.partition(":")
        module = module.strip()
        callable_name = callable_value.split("[", 1)[0].strip()
        if (
            not separator
            or not module
            or not callable_name
            or not all(part.isidentifier() for part in module.split("."))
            or not all(part.isidentifier() for part in callable_name.split("."))
        ):
            raise ArchitectureInventoryError("pyproject_script_target_invalid")
        return module, callable_name

    entries: list[dict[str, str]] = []
    scripts_value = project.get("scripts", {})
    if not isinstance(scripts_value, dict):
        raise ArchitectureInventoryError("pyproject_scripts_invalid")
    for name, value in scripts_value.items():
        if not isinstance(name, str) or not name.strip():
            raise ArchitectureInventoryError("pyproject_script_name_invalid")
        module, callable_name = parse_target(value)
        entries.append({
            "category": "project_script",
            "group": "project.scripts",
            "name": name.strip(),
            "module": module,
            "callable": callable_name,
        })

    groups_value = project.get("entry-points", {})
    if not isinstance(groups_value, dict):
        raise ArchitectureInventoryError("pyproject_entrypoint_groups_invalid")
    for group_name, group_value in groups_value.items():
        if not isinstance(group_name, str) or not isinstance(group_value, dict):
            raise ArchitectureInventoryError("pyproject_entrypoint_group_invalid")
        for name, value in group_value.items():
            if not isinstance(name, str) or not name.strip():
                raise ArchitectureInventoryError("pyproject_script_name_invalid")
            module, callable_name = parse_target(value)
            entries.append({
                "category": "project_entrypoint",
                "group": group_name.strip(),
                "name": name.strip(),
                "module": module,
                "callable": callable_name,
            })
    return sorted(
        entries,
        key=lambda item: (
            item["category"],
            item["group"],
            item["name"],
            item["module"],
            item["callable"],
        ),
    )


def _existing_module(name: str, modules: set[str]) -> str:
    candidate = name
    while candidate:
        if candidate in modules:
            return candidate
        candidate = candidate.rpartition(".")[0]
    return ""


def _relative_base(module: str, path: Path, level: int) -> list[str]:
    parts = module.split(".")
    package = parts if path.name == "__init__.py" else parts[:-1]
    trim = max(0, level - 1)
    return package[: max(0, len(package) - trim)]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _resolved_call_name(name: str, aliases: dict[str, str]) -> str:
    head, separator, tail = name.partition(".")
    resolved = aliases.get(head, head)
    return f"{resolved}.{tail}" if separator else resolved


def _dynamic_relative_name(
    literal: str,
    *,
    module: str,
    path: Path,
    call: ast.Call,
) -> tuple[str, str]:
    if not literal.startswith("."):
        return literal, ""
    package = ".".join(_relative_base(module, path, 1))
    package_node: ast.AST | None = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "package":
            package_node = keyword.value
            break
    if isinstance(package_node, ast.Constant) and isinstance(package_node.value, str):
        package = package_node.value.strip()
    elif isinstance(package_node, ast.Name) and package_node.id == "__package__":
        pass
    elif package_node is not None:
        return "", "relative_dynamic_import_package_uncertain"
    try:
        return importlib.util.resolve_name(literal, package), ""
    except (ImportError, ValueError):
        return "", "relative_dynamic_import_invalid"


def _parse_module(
    *,
    module: str,
    path: Path,
    known_modules: set[str],
) -> dict[str, Any]:
    source, source_sha256 = _read_source_snapshot(path)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ArchitectureInventoryError(
            f"python_source_syntax_invalid:{path}:{exc.lineno}:{exc.msg}"
        ) from exc
    edges: set[str] = set()
    dynamic_literals: set[str] = set()
    dynamic_uncertain: list[dict[str, Any]] = []
    patch_installers: set[str] = set()
    callable_exports: set[str] = set()
    aliases: dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callable_exports.add(node.name)
        elif isinstance(node, ast.ClassDef):
            callable_exports.add(node.name)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    callable_exports.add(f"{node.name}.{child.name}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _existing_module(alias.name, known_modules)
                if target:
                    edges.add(target)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = _relative_base(module, path, node.level)
                if node.module:
                    parts.extend(node.module.split("."))
                base = ".".join(parts)
            else:
                base = _text(node.module)
            target = _existing_module(base, known_modules)
            if target:
                edges.add(target)
            for alias in node.names:
                child = f"{base}.{alias.name}" if base else alias.name
                child_target = _existing_module(child, known_modules)
                if child_target:
                    edges.add(child_target)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            normalized = node.name.lower()
            if normalized.startswith("install_") and "patch" in normalized:
                patch_installers.add(node.name)
        elif isinstance(node, ast.Call):
            name = _resolved_call_name(_call_name(node.func), aliases)
            if name in {"__import__", "importlib.import_module", "import_module"}:
                argument = node.args[0] if node.args else None
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    literal = argument.value.strip()
                    resolved, relative_error = _dynamic_relative_name(
                        literal,
                        module=module,
                        path=path,
                        call=node,
                    )
                    if relative_error:
                        dynamic_uncertain.append({
                            "kind": relative_error,
                            "line": int(getattr(node, "lineno", 0) or 0),
                            "call": name,
                            "literal": literal,
                        })
                        continue
                    target = _existing_module(resolved, known_modules)
                    if target:
                        edges.add(target)
                        dynamic_literals.add(target)
                    elif resolved.split(".", 1)[0] in {
                        item.split(".", 1)[0] for item in known_modules
                    }:
                        dynamic_uncertain.append({
                            "kind": "unresolved_project_literal_dynamic_import",
                            "line": int(getattr(node, "lineno", 0) or 0),
                            "call": name,
                            "literal": literal,
                            "resolved_name": resolved,
                        })
                else:
                    dynamic_uncertain.append({
                        "kind": "non_literal_dynamic_import",
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "call": name,
                    })
            elif name in {
                "importlib.metadata.entry_points",
                "metadata.entry_points",
                "pkgutil.iter_modules",
                "pkgutil.walk_packages",
            }:
                dynamic_uncertain.append({
                    "kind": "plugin_discovery",
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "call": name,
                })
            elif name in {
                "importlib.machinery.SourceFileLoader",
                "importlib.util.module_from_spec",
                "importlib.util.spec_from_file_location",
            } or name.endswith(".exec_module") or name.endswith(".load_module"):
                dynamic_uncertain.append({
                    "kind": "dynamic_loader",
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "call": name,
                })
    return {
        "edges": edges,
        "dynamic_literals": sorted(dynamic_literals),
        "dynamic_uncertain": dynamic_uncertain,
        "patch_installers": sorted(patch_installers),
        "callable_exports": sorted(callable_exports),
        "line_count": len(source.splitlines()),
        "source_sha256": source_sha256,
    }


def _reachable(roots: Iterable[str], graph: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    queue = deque(sorted({_text(root) for root in roots if _text(root)}))
    while queue:
        module = queue.popleft()
        if module in reached or module not in graph:
            continue
        reached.add(module)
        for dependency in sorted(graph[module]):
            if dependency not in reached:
                queue.append(dependency)
    # Importing a submodule also executes every package __init__ on its path.
    for module in list(reached):
        parts = module.split(".")
        for index in range(1, len(parts)):
            parent = ".".join(parts[:index])
            if parent in graph:
                reached.add(parent)
    return reached


def _git_source_state(repo_root: Path) -> dict[str, str]:
    command = ["git", "-C", str(repo_root)]
    def run(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [*command, *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    top = run(["rev-parse", "--show-toplevel"])
    if top is None:
        return {
            "repository_status": "NOT_AVAILABLE",
            "reason_code": "git_identity_unavailable",
        }
    if top.returncode != 0:
        return {"repository_status": "NOT_REPOSITORY", "reason_code": ""}
    try:
        actual_root = Path(top.stdout.strip()).resolve()
    except (OSError, RuntimeError):
        return {
            "repository_status": "NOT_AVAILABLE",
            "reason_code": "git_root_unresolvable",
        }
    if actual_root != repo_root:
        return {"repository_status": "NOT_REPOSITORY", "reason_code": ""}
    commit = run(["rev-parse", "HEAD"])
    status = run(["status", "--porcelain", "--untracked-files=normal"])
    if (
        commit is None
        or status is None
        or commit.returncode != 0
        or status.returncode != 0
    ):
        return {
            "repository_status": "NOT_AVAILABLE",
            "reason_code": "git_identity_command_failed",
        }
    return {
        "repository_status": "GIT_WORKTREE",
        "git_commit": commit.stdout.strip(),
        "worktree_status": "DIRTY" if status.stdout.strip() else "CLEAN",
        "reason_code": "",
    }


def _source_identity(
    *,
    repo_root: Path,
    package: str,
    source_paths: dict[str, Path],
    parsed: dict[str, dict[str, Any]],
    config: dict[str, Any],
    project_scripts: list[dict[str, str]],
) -> dict[str, Any]:
    rows = [
        {
            "module": module,
            "path": str(source_paths[module].relative_to(repo_root)).replace("\\", "/"),
            "sha256": _text(parsed[module].get("source_sha256")),
        }
        for module in sorted(source_paths)
    ]
    package_rows = [
        row
        for row in rows
        if row["module"] == package or row["module"].startswith(package + ".")
    ]
    generator_module = f"{package}.architecture_inventory"
    generator_sha = _text(_dict(parsed.get(generator_module)).get("source_sha256"))
    return {
        "scope": "WORKTREE_CONTENT_ADDRESSED",
        "python_source_fingerprint": _sha256_json(rows),
        "package_source_fingerprint": _sha256_json(package_rows),
        "config_fingerprint": _sha256_json(config),
        "project_scripts_fingerprint": _sha256_json(project_scripts),
        "python_source_count": len(rows),
        "package_source_count": len(package_rows),
        "generator_module": generator_module,
        "generator_sha256": generator_sha,
        "git": _git_source_state(repo_root),
    }


def _trace_root(
    *,
    category: str,
    group: str,
    name: str,
    module: str,
    callable_name: str,
) -> dict[str, str]:
    identity = {
        "category": _text(category),
        "group": _text(group),
        "name": _text(name),
        "module": _text(module),
        "callable": _text(callable_name),
    }
    if not identity["category"] or not identity["name"] or not identity["module"]:
        raise ArchitectureInventoryError("architecture_trace_root_invalid")
    return {
        "root_id": "root_" + _sha256_json(identity)[:24],
        **identity,
    }


def _runtime_trace(
    path: Path | None,
    *,
    source_fingerprint: str,
    config_fingerprint: str,
    project_scripts_fingerprint: str,
    required_roots: dict[str, dict[str, str]],
    known_modules: set[str],
) -> dict[str, Any]:
    if path is None:
        return {
            "status": "NOT_PROVIDED",
            "coverage_status": "NOT_PROVIDED",
            "trusted_for_deletion": False,
            "authentication": {"status": "NOT_PROVIDED"},
            "covered_roots": [],
            "missing_required_roots": sorted(required_roots),
            "modules": [],
            "collector": {},
            "root_sessions": [],
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArchitectureInventoryError(
            f"runtime_trace_invalid:{path}:{type(exc).__name__}:{exc}"
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != IMPORT_TRACE_SCHEMA:
        raise ArchitectureInventoryError("runtime_trace_schema_invalid")
    coverage = _text(value.get("coverage_status")).upper()
    if coverage not in {"COMPLETE", "PARTIAL"}:
        raise ArchitectureInventoryError("runtime_trace_coverage_invalid")
    if _text(value.get("source_fingerprint")) != source_fingerprint:
        raise ArchitectureInventoryError(
            "runtime_trace_source_fingerprint_mismatch"
        )
    if _text(value.get("config_fingerprint")) != config_fingerprint:
        raise ArchitectureInventoryError(
            "runtime_trace_config_fingerprint_mismatch"
        )
    if (
        _text(value.get("project_scripts_fingerprint"))
        != project_scripts_fingerprint
    ):
        raise ArchitectureInventoryError(
            "runtime_trace_project_scripts_fingerprint_mismatch"
        )
    collector = value.get("collector")
    if not isinstance(collector, dict) or any(
        not _text(collector.get(field))
        for field in ("name", "version", "session_id")
    ):
        raise ArchitectureInventoryError("runtime_trace_collector_invalid")
    if _text(collector.get("name")) != "qualibug.import-trace":
        raise ArchitectureInventoryError("runtime_trace_collector_invalid")
    modules = value.get("modules")
    if (
        not isinstance(modules, list)
        or not all(
            isinstance(item, str)
            and _text(item)
            and all(part.isidentifier() for part in _text(item).split("."))
            for item in modules
        )
    ):
        raise ArchitectureInventoryError("runtime_trace_modules_invalid")
    runtime_modules = sorted(set(_text(item) for item in modules))
    root_sessions = value.get("root_sessions")
    if not isinstance(root_sessions, list):
        raise ArchitectureInventoryError("runtime_trace_root_sessions_invalid")
    covered_roots: set[str] = set()
    normalized_sessions: list[dict[str, str]] = []
    for raw in root_sessions:
        if not isinstance(raw, dict):
            raise ArchitectureInventoryError("runtime_trace_root_session_invalid")
        root_id = _text(raw.get("root_id"))
        descriptor = required_roots.get(root_id)
        module = _text(raw.get("module"))
        callable_name = _text(raw.get("callable"))
        status = _text(raw.get("status")).upper()
        command_fingerprint = _text(raw.get("command_fingerprint"))
        environment_fingerprint = _text(raw.get("environment_fingerprint"))
        if (
            not isinstance(descriptor, dict)
            or module != _text(descriptor.get("module"))
            or callable_name != _text(descriptor.get("callable"))
            or module not in known_modules
            or status not in {"COMPLETE", "PARTIAL"}
            or not command_fingerprint
            or not environment_fingerprint
        ):
            raise ArchitectureInventoryError("runtime_trace_root_session_invalid")
        if root_id in covered_roots:
            raise ArchitectureInventoryError("runtime_trace_root_session_duplicate")
        covered_roots.add(root_id)
        normalized_sessions.append({
            "root_id": root_id,
            "category": _text(descriptor.get("category")),
            "group": _text(descriptor.get("group")),
            "name": _text(descriptor.get("name")),
            "module": module,
            "callable": callable_name,
            "status": status,
            "command_fingerprint": command_fingerprint,
            "environment_fingerprint": environment_fingerprint,
        })
    missing_roots = sorted(set(required_roots) - covered_roots)
    if coverage == "COMPLETE" and missing_roots:
        raise ArchitectureInventoryError(
            f"runtime_trace_root_coverage_missing:{missing_roots[0]}"
        )
    if coverage == "COMPLETE" and any(
        session["status"] != "COMPLETE" for session in normalized_sessions
    ):
        raise ArchitectureInventoryError("runtime_trace_root_session_incomplete")
    missing_root_modules = sorted({
        _text(required_roots[root_id].get("module"))
        for root_id in covered_roots
        if _text(required_roots[root_id].get("module")) not in set(runtime_modules)
    })
    if missing_root_modules:
        raise ArchitectureInventoryError(
            f"runtime_trace_root_module_missing:{missing_root_modules[0]}"
        )
    return {
        "status": (
            "UNAUTHENTICATED_COMPLETE"
            if coverage == "COMPLETE"
            else "UNAUTHENTICATED_PARTIAL"
        ),
        "coverage_status": coverage,
        "trusted_for_deletion": False,
        "authentication": {
            "status": "NOT_PROVIDED",
            "reason_code": "authenticated_import_trace_collector_required",
        },
        "source_fingerprint": source_fingerprint,
        "config_fingerprint": config_fingerprint,
        "project_scripts_fingerprint": project_scripts_fingerprint,
        "covered_roots": sorted(covered_roots),
        "missing_required_roots": missing_roots,
        "modules": runtime_modules,
        "project_modules": sorted(set(runtime_modules).intersection(known_modules)),
        "collector": {
            "name": _text(collector.get("name")),
            "version": _text(collector.get("version")),
            "session_id": _text(collector.get("session_id")),
        },
        "root_sessions": sorted(
            normalized_sessions,
            key=lambda item: item["root_id"],
        ),
    }


def _responsibility(
    module: str,
    *,
    override: str,
    retirement_candidate: bool,
) -> str:
    if retirement_candidate:
        return "retirement_candidate"
    if override:
        return override
    lowered = module.lower()
    if any(term in lowered for term in _COMPATIBILITY_TERMS):
        return "compatibility"
    if any(term in lowered for term in _DIAGNOSTIC_TERMS):
        return "diagnostic"
    if any(term in lowered for term in _ADAPTER_TERMS):
        return "adapter"
    return "core"


def _removal_gate(
    *,
    candidate: bool,
    runtime_observed: bool,
    runtime_trace: dict[str, Any],
    dynamic_uncertainty: bool,
) -> str:
    if not candidate:
        return "NOT_APPLICABLE"
    if dynamic_uncertainty:
        return "BLOCKED_DYNAMIC_IMPORT_REVIEW_REQUIRED"
    if runtime_trace["status"] == "NOT_PROVIDED":
        return "BLOCKED_RUNTIME_TRACE_REQUIRED"
    if runtime_trace.get("trusted_for_deletion") is not True:
        return "BLOCKED_RUNTIME_TRACE_AUTHENTICATION_REQUIRED"
    if runtime_trace["coverage_status"] != "COMPLETE":
        return "BLOCKED_RUNTIME_TRACE_INCOMPLETE"
    if runtime_observed:
        return "BLOCKED_RUNTIME_OBSERVED"
    return "MANUAL_DELETION_REVIEW_REQUIRED"


def _cyclic_components(
    graph: dict[str, set[str]],
    modules: set[str],
) -> list[list[str]]:
    """Return deterministic strongly connected components that contain cycles."""

    visited: set[str] = set()
    finish_order: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for target in sorted(graph.get(node, set()).intersection(modules)):
            visit(target)
        finish_order.append(node)

    for module in sorted(modules):
        visit(module)

    reverse: dict[str, set[str]] = {module: set() for module in modules}
    for source in modules:
        for target in graph.get(source, set()).intersection(modules):
            reverse[target].add(source)

    assigned: set[str] = set()
    components: list[list[str]] = []

    def collect(node: str, component: list[str]) -> None:
        if node in assigned:
            return
        assigned.add(node)
        component.append(node)
        for source in sorted(reverse.get(node, set())):
            collect(source, component)

    for module in reversed(finish_order):
        if module in assigned:
            continue
        component: list[str] = []
        collect(module, component)
        component.sort()
        if len(component) > 1 or (
            len(component) == 1 and component[0] in graph.get(component[0], set())
        ):
            components.append(component)
    return sorted(components, key=lambda item: (-len(item), item))


def _dependency_graph_diagnostics(
    *,
    graph: dict[str, set[str]],
    module_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    module_classes = {
        _text(row.get("module")): _text(row.get("responsibility"))
        for row in module_rows
    }
    modules = set(module_classes)
    fan_out = {
        module: len(graph.get(module, set()).intersection(modules))
        for module in modules
    }
    fan_in = {module: 0 for module in modules}
    for source in modules:
        for target in graph.get(source, set()).intersection(modules):
            fan_in[target] += 1
    cyclic = _cyclic_components(graph, modules)
    forbidden_targets = {
        "core": {"adapter", "compatibility", "diagnostic"},
        "adapter": {"compatibility", "diagnostic"},
        "compatibility": {"adapter", "compatibility", "diagnostic"},
        "diagnostic": {"adapter", "compatibility"},
    }
    violations: list[dict[str, str]] = []
    for source in sorted(modules):
        source_class = module_classes[source]
        for target in sorted(graph.get(source, set()).intersection(modules)):
            target_class = module_classes[target]
            if target_class in forbidden_targets.get(source_class, set()):
                violations.append({
                    "source": source,
                    "source_responsibility": source_class,
                    "target": target,
                    "target_responsibility": target_class,
                    "reason_code": "dependency_direction_forbidden",
                })

    def hubs(values: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"module": module, "count": count}
            for module, count in sorted(
                values.items(),
                key=lambda item: (-item[1], item[0]),
            )
            if count > 0
        ][:25]

    return {
        "cyclic_scc_count": len(cyclic),
        "modules_in_cycles": sum(len(component) for component in cyclic),
        "largest_cyclic_scc_size": max((len(component) for component in cyclic), default=0),
        "cyclic_sccs": [
            {"size": len(component), "modules": component}
            for component in cyclic
        ],
        "fan_in_hubs": hubs(fan_in),
        "fan_out_hubs": hubs(fan_out),
        "forbidden_dependency_direction_count": len(violations),
        "forbidden_dependency_directions": violations,
    }


def _architecture_budget(
    config: dict[str, Any],
    *,
    module_count: int,
    python_line_count: int,
) -> dict[str, Any]:
    configured = _dict(config.get("architecture_budget"))
    if not configured:
        return {"status": "NOT_CONFIGURED"}
    max_modules = int(configured["max_module_count"])
    max_lines = int(configured["max_python_line_count"])
    exceeded = [
        name
        for name, value, limit in (
            ("module_count", module_count, max_modules),
            ("python_line_count", python_line_count, max_lines),
        )
        if value > limit
    ]
    return {
        "status": "OVER_BUDGET" if exceeded else "WITHIN_BUDGET",
        "max_module_count": max_modules,
        "max_python_line_count": max_lines,
        "exceeded_metrics": exceeded,
        "policy": "non_growth_ceiling",
    }


def build_architecture_inventory(
    *,
    repo_root: Path | str,
    config_path: Path | str,
    runtime_trace_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a deterministic architecture inventory without mutating sources."""

    root = Path(repo_root).resolve()
    config = _load_config(Path(config_path).resolve())
    package = _text(config.get("package"))
    source_paths = _python_sources(root)
    known = set(source_paths)
    package_modules = sorted(
        module for module in known if module == package or module.startswith(package + ".")
    )
    if not package_modules:
        raise ArchitectureInventoryError(f"architecture_package_not_found:{package}")

    parsed = {
        module: _parse_module(
            module=module,
            path=path,
            known_modules=known,
        )
        for module, path in sorted(source_paths.items())
    }
    graph = {
        module: set(row["edges"])
        for module, row in parsed.items()
    }
    roots = _dict(config.get("supported_roots"))
    entrypoints = [dict(item) for item in _list(config.get("discovery_entrypoints"))]
    for entrypoint in entrypoints:
        module = _text(entrypoint.get("module"))
        callable_name = _text(entrypoint.get("callable"))
        if module not in parsed:
            raise ArchitectureInventoryError(
                f"architecture_entrypoint_module_missing:{module}"
            )
        if callable_name not in set(parsed[module]["callable_exports"]):
            raise ArchitectureInventoryError(
                f"architecture_entrypoint_callable_missing:{module}:{callable_name}"
            )
    active_entrypoints = [
        row for row in entrypoints if _text(row.get("status")).lower() != "deprecated"
    ]
    active_entrypoint_roots = sorted(
        {_text(row.get("module")) for row in active_entrypoints}
    )
    configured_roots = {
        category: sorted(set(_text(item) for item in _list(roots.get(category))))
        for category in ("product", "evaluation", "tooling")
    }
    project_scripts = _project_script_entries(root)
    for script in project_scripts:
        module = script["module"]
        callable_name = script["callable"]
        if module not in parsed:
            raise ArchitectureInventoryError(
                f"pyproject_script_module_missing:{module}"
            )
        if callable_name not in set(parsed[module]["callable_exports"]):
            raise ArchitectureInventoryError(
                f"pyproject_script_callable_missing:{module}:{callable_name}"
            )
    script_roots = sorted({item["module"] for item in project_scripts})
    all_declared = set(script_roots) | set(active_entrypoint_roots)
    for values in configured_roots.values():
        all_declared.update(values)
    missing_roots = sorted(module for module in all_declared if module not in known)
    if missing_roots:
        raise ArchitectureInventoryError(
            f"architecture_root_module_missing:{missing_roots[0]}"
        )

    product_reachable = _reachable(
        [
            *configured_roots["product"],
            *script_roots,
            *active_entrypoint_roots,
        ],
        graph,
    )
    evaluation_reachable = _reachable(configured_roots["evaluation"], graph)
    tooling_reachable = _reachable(configured_roots["tooling"], graph)
    test_roots = sorted(
        module
        for module, path in source_paths.items()
        if "tests" in {
            part.casefold() for part in path.relative_to(root).parts
        }
        and path.name.startswith("test_")
    )
    test_reachable = _reachable(test_roots, graph)
    trace_roots: list[dict[str, str]] = []
    for category, modules in configured_roots.items():
        for module in modules:
            trace_roots.append(_trace_root(
                category=category,
                group="architecture_roots",
                name=module,
                module=module,
                callable_name="",
            ))
    for script in project_scripts:
        trace_roots.append(_trace_root(
            category=script["category"],
            group=script["group"],
            name=script["name"],
            module=script["module"],
            callable_name=script["callable"],
        ))
    for entrypoint in active_entrypoints:
        trace_roots.append(_trace_root(
            category="discovery_entrypoint",
            group=_text(entrypoint.get("status")).lower(),
            name=_text(entrypoint.get("name")),
            module=_text(entrypoint.get("module")),
            callable_name=_text(entrypoint.get("callable")),
        ))
    for module in test_roots:
        trace_roots.append(_trace_root(
            category="test",
            group="pytest",
            name=module,
            module=module,
            callable_name="",
        ))
    trace_roots = sorted(trace_roots, key=lambda item: item["root_id"])
    required_trace_roots = {item["root_id"]: item for item in trace_roots}
    if len(required_trace_roots) != len(trace_roots):
        raise ArchitectureInventoryError("architecture_trace_root_duplicate")

    external_references: dict[str, set[str]] = defaultdict(set)
    for source, dependencies in graph.items():
        if source == package or source.startswith(package + "."):
            continue
        for dependency in dependencies:
            if dependency == package or dependency.startswith(package + "."):
                external_references[dependency].add(source)
    external_reference_roots = set(external_references)
    external_reachable = _reachable(external_reference_roots, graph)
    supported_reachable = (
        product_reachable
        | evaluation_reachable
        | tooling_reachable
        | test_reachable
        | external_reachable
    )

    uncertain_modules = sorted(
        module
        for module in supported_reachable
        if parsed.get(module, {}).get("dynamic_uncertain")
    )
    uncertainty_present = bool(uncertain_modules)
    source_identity = _source_identity(
        repo_root=root,
        package=package,
        source_paths=source_paths,
        parsed=parsed,
        config=config,
        project_scripts=project_scripts,
    )
    trace = _runtime_trace(
        Path(runtime_trace_path).resolve() if runtime_trace_path is not None else None,
        source_fingerprint=_text(source_identity.get("python_source_fingerprint")),
        config_fingerprint=_text(source_identity.get("config_fingerprint")),
        project_scripts_fingerprint=_text(
            source_identity.get("project_scripts_fingerprint")
        ),
        required_roots=required_trace_roots,
        known_modules=known,
    )
    runtime_modules = set(trace["modules"])
    overrides = {
        _text(module): _text(value)
        for module, value in _dict(config.get("module_class_overrides")).items()
    }
    unknown_overrides = sorted(module for module in overrides if module not in package_modules)
    if unknown_overrides:
        raise ArchitectureInventoryError(
            f"architecture_override_module_missing:{unknown_overrides[0]}"
        )

    module_rows: list[dict[str, Any]] = []
    for module in package_modules:
        path = source_paths[module]
        reachable_product = module in product_reachable
        reachable_evaluation = module in evaluation_reachable
        reachable_tooling = module in tooling_reachable
        reachable_tests = module in test_reachable
        reachable_external = module in external_reachable
        runtime_observed = module in runtime_modules
        referenced_externally = sorted(external_references.get(module, set()))
        candidate = not any((
            reachable_product,
            reachable_evaluation,
            reachable_tooling,
            reachable_tests,
            reachable_external,
            runtime_observed,
        ))
        responsibility = _responsibility(
            module,
            override=overrides.get(module, ""),
            retirement_candidate=candidate,
        )
        module_rows.append({
            "module": module,
            "path": str(path.relative_to(root)).replace("\\", "/"),
            "line_count": int(parsed[module]["line_count"]),
            "source_sha256": _text(parsed[module]["source_sha256"]),
            "responsibility": responsibility,
            "reachable_from_product": reachable_product,
            "reachable_from_evaluation": reachable_evaluation,
            "reachable_from_tooling": reachable_tooling,
            "reachable_from_tests": reachable_tests,
            "reachable_from_external_reference": reachable_external,
            "runtime_observed": runtime_observed,
            "external_reference_modules": referenced_externally,
            "static_dependencies": sorted(
                dependency
                for dependency in graph[module]
                if dependency == package or dependency.startswith(package + ".")
            ),
            "literal_dynamic_imports": list(parsed[module]["dynamic_literals"]),
            "dynamic_import_uncertainty": list(parsed[module]["dynamic_uncertain"]),
            "patch_installers": list(parsed[module]["patch_installers"]),
            "removal_gate": _removal_gate(
                candidate=candidate,
                runtime_observed=runtime_observed,
                runtime_trace=trace,
                dynamic_uncertainty=uncertainty_present,
            ),
        })

    threshold = int(config["oversized_line_threshold"])
    oversized = [
        row["module"]
        for row in module_rows
        if int(row["line_count"]) > threshold
    ]
    patch_modules = [
        row["module"] for row in module_rows if row["patch_installers"]
    ]
    authority_entrypoints = [
        row
        for row in active_entrypoints
        if _text(row.get("status")).lower()
        in {"canonical", "compatibility", "independent"}
    ]
    responsibility_counts = dict(
        sorted(Counter(row["responsibility"] for row in module_rows).items())
    )
    module_count = len(module_rows)
    python_line_count = sum(int(row["line_count"]) for row in module_rows)
    dependency_graph = _dependency_graph_diagnostics(
        graph=graph,
        module_rows=module_rows,
    )
    architecture_budget = _architecture_budget(
        config,
        module_count=module_count,
        python_line_count=python_line_count,
    )
    inventory = {
        "schema_version": INVENTORY_SCHEMA,
        "package": package,
        "quality_claim_status": "ARCHITECTURE_DIAGNOSTIC_ONLY",
        "external_discovery_quality": "NOT_MEASURED",
        "auto_delete_performed": False,
        "source_identity": source_identity,
        "trace_roots": trace_roots,
        "supported_roots": {
            **configured_roots,
            "active_entrypoints": active_entrypoint_roots,
            "project_scripts": script_roots,
            "project_script_entries": project_scripts,
            "tests": test_roots,
        },
        "runtime_trace": trace,
        "dynamic_import_uncertainty": {
            "present": uncertainty_present,
            "reachable_modules": uncertain_modules,
            "effect": (
                "all retirement deletions blocked pending dynamic import review"
                if uncertainty_present
                else "none"
            ),
        },
        "dependency_graph": dependency_graph,
        "diagnostics": {
            "module_count": module_count,
            "python_line_count": python_line_count,
            "architecture_budget": architecture_budget,
            "responsibility_counts": responsibility_counts,
            "retirement_candidate_count": int(
                responsibility_counts.get("retirement_candidate") or 0
            ),
            "discovery_entrypoint_count": len(active_entrypoints),
            "duplicate_discovery_entrypoint_count": max(
                0, len(authority_entrypoints) - 1
            ),
            "monkeypatch_authority_count": len(patch_modules),
            "monkeypatch_authority_modules": patch_modules,
            "oversized_line_threshold": threshold,
            "oversized_boundary_count": len(oversized),
            "oversized_boundary_modules": oversized,
        },
        "discovery_entrypoints": entrypoints,
        "modules": module_rows,
    }
    return inventory


def persist_architecture_inventory(
    inventory: dict[str, Any],
    output_path: Path | str,
) -> Path:
    """Persist deterministic diagnostics.  No source deletion is performed."""

    if _dict(inventory).get("schema_version") != INVENTORY_SCHEMA:
        raise ArchitectureInventoryError("architecture_inventory_schema_invalid")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path
