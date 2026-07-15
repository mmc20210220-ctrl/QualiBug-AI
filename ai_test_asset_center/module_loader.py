"""
QualiBug Module Loader — Commercial-grade module management.
Handles: config validation, module enable/disable, graceful degradation, error isolation.
"""
from __future__ import annotations
import json, sys, traceback
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ModuleResult:
    name: str
    findings: list[dict]
    ms: int = 0
    grade: str = "A"
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""


class ModuleLoader:
    """Loads and validates configuration, executes modules with error isolation."""

    def __init__(self, project_id: str, root: Path | None = None):
        self.project_id = project_id
        self.root = root or Path(".")
        self.config: dict = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._loaded = False

    def load_config(self) -> dict:
        """Load and validate connector_registry.json. Returns validated config or raises."""
        reg_path = self.root / "platform_workspace" / self.project_id / "enterprise_pilot_runtime" / "connector_registry.json"
        if not reg_path.exists():
            self.errors.append(f"connector_registry.json not found at {reg_path}")
            return {}

        self.config = json.loads(reg_path.read_text(encoding="utf-8"))
        self._validate()
        self._loaded = True
        return self.config

    def _validate(self) -> None:
        """Validate required configuration fields with friendly messages."""
        cfg = self.config
        
        # Check test_profile
        tp = cfg.get("test_profile", {})
        if not tp:
            self.errors.append("缺少 test_profile，请在设置中配置API地址和测试账号")
        else:
            if not tp.get("api_base_url"):
                self.errors.append("缺少 test_profile.api_base_url，请配置目标API地址")
            creds = tp.get("test_credentials", {})
            if not creds.get("buyer", {}).get("email"):
                self.warnings.append("未配置买家测试账号，权限/隔离测试将跳过")
            if not creds.get("admin", {}).get("email"):
                self.warnings.append("未配置管理员测试账号，管理端测试将跳过")
            db = tp.get("database", {})
            if not db.get("host"):
                self.warnings.append("未配置数据库连接，DB验证将跳过")

        # Check connectors
        connectors = cfg.get("connectors", [])
        if not connectors:
            self.errors.append("缺少 connectors，请至少配置一个API连接器")
        
        kinds = {c.get("kind") for c in connectors if c.get("enabled")}
        if "http_api" not in kinds:
            self.errors.append("未启用任何 http_api 连接器，API测试无法执行")
        if "database" not in kinds:
            self.warnings.append("未启用 database 连接器，DB验证层将自动禁用")

        # Check modules
        modules = cfg.get("modules", {})
        if not modules:
            # Auto-generate defaults
            cfg["modules"] = {
                "api_fuzzer": {"enabled": True},
                "db_verifier": {"enabled": "database" in kinds},
                "e2e_flow": {"enabled": True},
                "deep_verifier": {"enabled": True},
                "frontend_ui": {"enabled": False},
                "multi_layer": {"enabled": True},
            }

    def is_module_enabled(self, module_name: str) -> tuple[bool, str]:
        """Check if a module should run. Returns (enabled, reason_if_disabled)."""
        modules = self.config.get("modules", {})
        mod_cfg = modules.get(module_name, {})
        if not mod_cfg.get("enabled", True):
            return False, "模块已禁用"
        
        # Check requirements
        connectors = self.config.get("connectors", [])
        kinds = {c.get("kind") for c in connectors if c.get("enabled")}
        requires = mod_cfg.get("requires", [])
        for req in requires:
            if req not in kinds:
                return False, f"缺少连接器类型: {req}"
        
        return True, ""

    def run_module(self, name: str, fn: Callable, *args, **kwargs) -> ModuleResult:
        """Run a module with error isolation. Returns ModuleResult."""
        enabled, reason = self.is_module_enabled(name)
        if not enabled:
            return ModuleResult(name=name, findings=[], skipped=True, skip_reason=reason)

        import time
        t0 = time.time()
        try:
            findings = fn(*args, **kwargs)
            ms = int((time.time() - t0) * 1000)
            if not isinstance(findings, list):
                findings = []
            return ModuleResult(name=name, findings=findings, ms=ms)
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            err_msg = f"{type(e).__name__}: {e}"
            return ModuleResult(
                name=name, findings=[],
                errors=[err_msg],
                ms=ms,
                grade="F"
            )

    def get_connector(self, kind: str) -> dict | None:
        """Get first enabled connector of a given kind."""
        for c in self.config.get("connectors", []):
            if c.get("kind") == kind and c.get("enabled"):
                return c
        return None

    def get_test_profile(self) -> dict:
        return self.config.get("test_profile", {})

    def get_runtime_config(self) -> dict:
        return self.config.get("runtime", {})

    def print_diagnostics(self) -> None:
        """Print diagnostic summary for the user."""
        if self.errors:
            print("❌ 配置错误:")
            for e in self.errors:
                print(f"   - {e}")
        if self.warnings:
            print("⚠️  配置警告:")
            for w in self.warnings:
                print(f"   - {w}")
        if not self.errors:
            modules = self.config.get("modules", {})
            enabled = [m for m, c in modules.items() if c.get("enabled")]
            disabled = [m for m, c in modules.items() if not c.get("enabled")]
            print(f"✅ 配置有效 | {len(enabled)}模块启用 | {len(disabled)}模块禁用 | "
                  f"{len(self.config.get('connectors',[]))}连接器", flush=True)


# Singleton for scan pipeline
_loader: ModuleLoader | None = None


def get_loader(project_id: str = "", root: Path | None = None) -> ModuleLoader:
    global _loader
    if _loader is None or (project_id and _loader.project_id != project_id):
        _loader = ModuleLoader(project_id, root)
        _loader.load_config()
    return _loader
