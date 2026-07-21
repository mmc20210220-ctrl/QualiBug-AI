"""DB state audit scan post-hook.

Registers a named ``scan_post_hooks`` hook that runs IR-driven database
state audit after each scan.  The DSN is discovered generically from the
project's platform_inputs text files (no hardcoded paths or credentials).

Supports enterprise database topologies:
- Single database: one DSN discovered
- Multiple databases (分库): multiple DSNs discovered from config
- Module-named databases: {"order": "postgresql://...", "payment": "..."}

Install::

    from ai_test_asset_center.private_pilot_db_audit_patch import install
    install()

Uninstall::

    from ai_test_asset_center.private_pilot_db_audit_patch import uninstall
    uninstall()
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HOOK_NAME = "db_state_audit"

# Generic DSN extraction (same pattern as pipeline_db._dsn_from_text)
_DSN_RE = re.compile(r"(postgres(?:ql)?://[^\s`\"']+)", re.I)
# MySQL/MariaDB DSN pattern
_MYSQL_DSN_RE = re.compile(r"(mysql://[^\s`\"']+|mariadb://[^\s`\"']+)", re.I)


def _discover_dsn(project: str, root: Path) -> "str | dict[str, str]":
    """Discover database DSN(s) from project configuration.

    Returns either:
    - A single DSN string (single-database topology)
    - A dict {module_name: dsn} (multi-database topology)
    - Empty string if nothing found

    Priority:
    1. BENCHMARK_MANIFEST.json (non_docker_database_url / databases dict)
    2. platform_inputs db_config.json / databases.json (structured multi-DB)
    3. platform_inputs/<project>/ text files — scan for DSN URLs
    4. platform_inputs/ text files (flat layout)
    """
    import json as _json

    # 1. Check BENCHMARK_MANIFEST.json in common locations
    for manifest_dir in (root, root / project, root.parent / project, root.parent):
        manifest = manifest_dir / "BENCHMARK_MANIFEST.json"
        if manifest.is_file():
            try:
                data = _json.loads(manifest.read_text(encoding="utf-8"))
                # Multi-DB: "databases" dict {module: dsn}
                dbs = data.get("databases")
                if isinstance(dbs, dict) and dbs:
                    valid = {k: v for k, v in dbs.items() if isinstance(v, str) and v.strip()}
                    if valid:
                        logger.info("DB audit multi-DB config from manifest: %d databases", len(valid))
                        return valid
                # Single DSN keys
                for key in ("non_docker_database_url", "database_url", "dsn", "database"):
                    val = str(data.get(key) or "").strip()
                    if val.startswith(("postgresql://", "postgres://", "mysql://", "mariadb://")):
                        logger.info("DB audit DSN from manifest: %s", manifest)
                        return val
            except Exception:
                continue

    # 2. Check for structured multi-DB config files
    inputs_dir = root / "platform_inputs" / project
    if not inputs_dir.is_dir():
        inputs_dir = root / "platform_inputs"
    if inputs_dir.is_dir():
        for config_name in ("db_config.json", "databases.json", "db_topology.json"):
            config_path = inputs_dir / config_name
            if config_path.is_file():
                try:
                    cfg = _json.loads(config_path.read_text(encoding="utf-8"))
                    # Support {"databases": {module: dsn}} or flat {module: dsn}
                    dbs = cfg.get("databases", cfg) if isinstance(cfg, dict) else {}
                    if isinstance(dbs, dict):
                        valid = {
                            k: v for k, v in dbs.items()
                            if isinstance(v, str) and v.strip().startswith(
                                ("postgresql://", "postgres://", "mysql://", "mariadb://")
                            )
                        }
                        if valid:
                            logger.info("DB audit multi-DB from %s: %d databases", config_name, len(valid))
                            return valid
                except Exception:
                    continue

    # 3. Scan platform_inputs text files for DSN URLs
    if not inputs_dir.is_dir():
        return ""

    all_dsns: list[str] = []
    for pattern in ("*.md", "*.json", "*.txt", "*.yml", "*.yaml", "*.env"):
        for fpath in sorted(inputs_dir.glob(pattern)):
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                for m in _DSN_RE.finditer(text):
                    dsn = m.group(1).rstrip(".,;:)")
                    if dsn not in all_dsns:
                        all_dsns.append(dsn)
                for m in _MYSQL_DSN_RE.finditer(text):
                    dsn = m.group(1).rstrip(".,;:)")
                    if dsn not in all_dsns:
                        all_dsns.append(dsn)
            except Exception:
                continue

    if not all_dsns:
        return ""
    if len(all_dsns) == 1:
        logger.info("DB audit DSN discovered: 1 database")
        return all_dsns[0]

    # Multiple DSNs found: try to infer module names from DSN path
    result: dict[str, str] = {}
    for i, dsn in enumerate(all_dsns):
        # Extract database name from DSN path (e.g., postgresql://host:5432/order_db → order_db)
        module = _extract_db_name_from_dsn(dsn) or f"db_{i}"
        result[module] = dsn
    logger.info("DB audit multi-DSN discovered: %d databases", len(result))
    return result


def _extract_db_name_from_dsn(dsn: str) -> str:
    """Extract database name from DSN path for module naming."""
    try:
        # postgresql://user:pass@host:port/dbname?params
        path_part = dsn.split("//", 1)[-1]  # after //
        if "/" in path_part:
            db_part = path_part.rsplit("/", 1)[-1]
            db_name = db_part.split("?")[0].strip()
            return db_name or ""
    except Exception:
        pass
    return ""


def _db_state_audit_hook(
    result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Scan post-hook: run IR-driven DB state audit and enrich findings.

    Supports single-DB and multi-DB (分库分表) topologies.
    """
    if not isinstance(result, dict):
        return result

    # Extract Behavior IR from scan result
    v12 = result.get("v12") if isinstance(result.get("v12"), dict) else {}
    behavior_ir = v12.get("behavior_ir") if isinstance(v12.get("behavior_ir"), dict) else {}
    if not behavior_ir:
        behavior_ir = result.get("behavior_ir") if isinstance(result.get("behavior_ir"), dict) else {}
    if not behavior_ir:
        logger.debug("DB audit: no behavior_ir in scan result, skipping")
        return result

    # Discover DSN(s) generically — may return str or dict[str, str]
    dsn_config = _discover_dsn(project, root)
    if not dsn_config:
        logger.debug("DB audit: no DSN discovered for project %s, skipping", project)
        return result

    # Run IR-driven DB audit (handles single DSN, list, or dict)
    try:
        from .db_state_audit import run_db_state_audit
        db_findings = run_db_state_audit(behavior_ir, dsn_config)
    except Exception as exc:
        logger.warning("DB audit failed: %s", exc)
        return result

    if not db_findings:
        return result

    logger.info("DB audit produced %d findings for project %s", len(db_findings), project)

    # Merge into result
    result["db_findings"] = db_findings

    # Also add to top-level findings for evaluator consumption
    existing = result.get("findings")
    if isinstance(existing, list):
        result["findings"] = existing + db_findings
    else:
        result["findings"] = db_findings

    # Update total count
    result["total_findings"] = len(result.get("findings", []))

    return result


def install() -> None:
    """Register the DB state audit scan post-hook."""
    from ai_test_asset_center.scan_post_hooks import register_scan_post_hook
    register_scan_post_hook(_HOOK_NAME, _db_state_audit_hook)
    logger.info("DB state audit post-hook installed")


def uninstall() -> None:
    """Unregister the DB state audit scan post-hook."""
    from ai_test_asset_center.scan_post_hooks import register_scan_post_hook
    register_scan_post_hook(_HOOK_NAME, None)
    logger.info("DB state audit post-hook uninstalled")
