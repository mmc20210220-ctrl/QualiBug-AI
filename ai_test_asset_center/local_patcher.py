"""
Phase82: Simple Local Patcher — backup, edit, test, keep or restore.

No git worktree needed. For local dev only.
"""

import shutil, tempfile, subprocess, sys, json, time
from pathlib import Path
from dataclasses import dataclass


@dataclass
class PatchResult:
    success: bool
    patch_id: str
    file: str
    backup_path: str
    test_results: dict
    restored: bool = False
    error: str = ""


FORBIDDEN_FILES = {
    "safety_boundary.py",
    "unified_http_transport.py",
}

ALLOWED_FIX_TYPES = {
    "missing_paren": ("discovery_engine.py", "self_improving_loop.py", "stage_reason_all_v2.py"),
    "syntax_error": ("discovery_engine.py", "self_improving_loop.py", "*.py"),
    "import_error": ("*.py",),
    "config_default": ("discovery_engine.py", "stage_reason_all_v2.py"),
    "retry_logic": ("stage_reason_all_v2.py", "self_improving_loop.py"),
    "timeout_value": ("discovery_engine.py", "stage_reason_all_v2.py"),
}


class LocalPatcher:
    """Apply patches directly to local files with auto-rollback on failure."""

    def __init__(self, project_root: str = "."):
        self.root = Path(project_root).resolve()
        self.backup_dir = self.root / "platform_outputs" / ".patch_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def apply_and_validate(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        fix_type: str = "syntax_error",
    ) -> PatchResult:
        """Apply a patch, run tests, keep or rollback."""
        patch_id = f"patch-{int(time.time())}"

        # ── Safety checks ──
        file_name = Path(file_path).name
        if file_name in FORBIDDEN_FILES:
            return PatchResult(False, patch_id, file_path, "", {}, error="FORBIDDEN_FILE")

        allowed = ALLOWED_FIX_TYPES.get(fix_type, ())
        if allowed and not any(file_name == f or f == "*.py" for f in allowed):
            return PatchResult(False, patch_id, file_path, "", {}, error=f"FILE_NOT_ALLOWED for {fix_type}")

        full_path = self.root / file_path
        if not full_path.exists():
            return PatchResult(False, patch_id, file_path, "", {}, error="FILE_NOT_FOUND")

        # ── Backup ──
        backup_path = self.backup_dir / f"{patch_id}_{file_name}.bak"
        shutil.copy2(full_path, backup_path)

        try:
            # ── Apply ──
            content = full_path.read_text(encoding="utf-8")
            if old_string not in content:
                return PatchResult(False, patch_id, file_path, str(backup_path), {},
                                 error="OLD_STRING_NOT_FOUND")

            content = content.replace(old_string, new_string, 1)
            full_path.write_text(content, encoding="utf-8")

            # ── Validate syntax ──
            try:
                import ast
                ast.parse(content)
            except SyntaxError as e:
                self._restore(full_path, backup_path)
                return PatchResult(False, patch_id, file_path, str(backup_path), {},
                                 restored=True, error=f"SYNTAX_ERROR: {e}")

            # ── Run targeted tests ──
            test_result = self._run_tests(file_name)
            if not test_result.get("passed", False):
                self._restore(full_path, backup_path)
                return PatchResult(False, patch_id, file_path, str(backup_path),
                                 test_result, restored=True, error="TESTS_FAILED")

            # ── Success ──
            return PatchResult(True, patch_id, file_path, str(backup_path), test_result)

        except Exception as e:
            self._restore(full_path, backup_path)
            return PatchResult(False, patch_id, file_path, str(backup_path), {},
                             restored=True, error=str(e))

    def _restore(self, target: Path, backup: Path):
        if backup.exists():
            shutil.copy2(backup, target)

    def _run_tests(self, file_name: str) -> dict:
        test_map = {
            "discovery_engine.py": "tests/test_reasoner_stability.py tests/test_semantic_state_verifier.py tests/test_production_safety_gate.py",
            "stage_reason_all_v2.py": "tests/test_reasoner_stability.py",
            "self_improving_loop.py": "tests/test_semantic_state_verifier.py",
        }

        test_files = test_map.get(file_name, "tests/test_reasoner_stability.py tests/test_production_safety_gate.py")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", *test_files.split(), "-q", "--tb=line"],
                capture_output=True, text=True, timeout=60, cwd=str(self.root),
            )
            passed = "passed" in result.stdout.lower() and "failed" not in result.stdout.lower()
            return {"passed": passed, "stdout": result.stdout[:500], "stderr": result.stderr[:500]}
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Test timeout"}
        except Exception as e:
            return {"passed": False, "error": str(e)}


# ── Quick CLI ──

if __name__ == "__main__":
    patcher = LocalPatcher()

    # Example auto-fix: fix a missing parenthesis
    if len(sys.argv) >= 4:
        file_path, old_str, new_str = sys.argv[1], sys.argv[2], sys.argv[3]
        fix_type = sys.argv[4] if len(sys.argv) > 4 else "syntax_error"
        result = patcher.apply_and_validate(file_path, old_str, new_str, fix_type)
        print(json.dumps(result.__dict__, indent=2))
    else:
        print("Usage: python local_patcher.py <file> <old_string> <new_string> [fix_type]")
        print("Available fix_types:", list(ALLOWED_FIX_TYPES.keys()))
