from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import urlopen

from ai_test_asset_center.private_pilot_service import run_private_pilot_service


CommandRunner = Callable[[list[str], Path, int], subprocess.CompletedProcess[str]]

CUSTOMER_VISIBLE_TEXT_FILES = [
    "README.md",
    "DELIVERY_SCOPE.md",
    "PHASE90_RELEASE_NOTES.md",
    "PRODUCT_90_AUDIT.md",
    "PRODUCT_90_VERIFICATION.md",
    "PHASE68_VERIFICATION.md",
    "PHASE69_RELEASE_NOTES.md",
    "PHASE69_VERIFICATION.md",
    "PHASE70_RELEASE_NOTES.md",
    "PHASE70_VERIFICATION.md",
    "PHASE71_RELEASE_NOTES.md",
    "PHASE71_VERIFICATION.md",
    "PHASE71_DEEP_ANALYSIS.md",
    "PHASE71_PACKAGE_RECEIPT.md",
    "PHASE72_RELEASE_NOTES.md",
    "PHASE72_VERIFICATION.md",
    "PHASE72_PACKAGE_RECEIPT.md",
    "PHASE73_RELEASE_NOTES.md",
    "PHASE73_VERIFICATION.md",
    "PHASE73_PACKAGE_RECEIPT.md",
    "PHASE74_RELEASE_NOTES.md",
    "PHASE74_VERIFICATION.md",
    "PHASE74_PACKAGE_RECEIPT.md",
    "PHASE75_RELEASE_NOTES.md",
    "PHASE75_VERIFICATION.md",
    "PHASE75_PACKAGE_RECEIPT.md",
    "PHASE76_RELEASE_NOTES.md",
    "PHASE76_VERIFICATION.md",
    "PHASE76_PACKAGE_RECEIPT.md",
    "PHASE61_RELEASE_NOTES.md",
    "PHASE65_RELEASE_MANIFEST.json",
    "PHASE64_RELEASE_NOTES.md",
    "PHASE64_RELEASE_MANIFEST.json",
    "PHASE65_RELEASE_NOTES.md",
    "PHASE65_RELEASE_MANIFEST.json",
    "docs/PHASE65_FINANCIAL_LEDGER_CONSERVATION.md",
    "docs/PHASE64_ROLE_ACCESS_BOUNDARY.md",
    "docs/PHASE70_INVENTORY_RESERVATION_CONSERVATION.md",
    "docs/PHASE71_PROJECT_SCOPE_ISOLATION.md",
    "docs/PHASE72_WORLD_MODEL_CONCURRENCY_LEARNING.md",
    "docs/PHASE74_AGENT_DISCOVERY_LOOP.md",
    "docs/PHASE75_AGENT_EXPERIMENT_COMPILER.md",
    "docs/PHASE76_AGENT_BUSINESS_FLOW_ORCHESTRATOR.md",
    ".env.local.example",
    ".github/workflows/release-verify.yml",
    "aitestops/cli.py",
    "aitestops/release_verifier.py",
    "ai_test_asset_center/private_pilot_service.py",
    "ai_test_asset_center/product_ui.py",
    "deploy/docker-compose.private.yml",
    "deploy/private.env.example",
    "docs/PHASE61_PRODUCT_UI.md",
    "docs/CI_RELEASE_VERIFICATION.md",
    "docs/GA_READINESS_AUDIT.md",
    "docs/OPERATIONS_RUNBOOK.md",
    "deploy/README.md",
    "requirements-optional.txt",
]

MOJIBAKE_MARKERS = [
    "\u6d7c",
    "\u9359",
    "\u93c3",
    "\u9422",
    "\u7ee9",
    "\u95c7",
    "\u9239",
    "\u921e",
    "\ufffd",
]

PAGE_SHELL_REQUIRED_TOKENS = [
    "data-product-ui='phase61-product-ui'",
    "class='app-shell'",
    "class='sidebar'",
    "class='topbar'",
    "class='content'",
    "class='hero'",
    "data-download",
    "href='/dashboard?project=",
    "href='/knowledge?project=",
    "href='/control-plane?project=",
    "href='/release?project=",
    "href='/benchmark?project=",
    "QualiBug",
]

PAGE_FORBIDDEN_TOKENS = [
    "X-QualiBug-Actor':'admin",
    '"X-QualiBug-Actor":"admin"',
    "X-QualiBug-Role':'admin",
    '"X-QualiBug-Role":"admin"',
]

PHASE91_SCOPE = {
    "shared_design_system": "ai_test_asset_center/product_ui.py",
    "unified_pages": [
        "Enterprise Pilot Operations Center",
        "Enterprise TestOps Control Center",
        "Enterprise Business Knowledge Center",
        "Release Risk Dashboard",
        "Multi-industry Benchmark",
    ],
    "private_service_routes": [
        "/dashboard",
        "/control-plane",
        "/knowledge",
        "/release",
        "/benchmark",
    ],
    "read_only_api_routes": [
        "/api/pilot/overview",
        "/api/pilot/tasks",
        "/api/control-plane/overview",
        "/api/knowledge/asset",
        "/api/release/dashboard",
        "/api/benchmark/report",
    ],
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    command: list[str] | None = None
    duration_seconds: float = 0.0
    details: dict[str, Any] | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "command": self.command or [],
            "duration_seconds": round(self.duration_seconds, 3),
            "details": self.details or {},
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tail(text: str, limit: int = 1600) -> str:
    return (text or "")[-limit:]


def _pytest_summary(check: CheckResult | None) -> str:
    if not check:
        return "not_run"
    if not check.passed:
        return "failed"
    output = f"{check.stdout_tail}\n{check.stderr_tail}"
    match = re.search(r"(\d+)\s+passed(?:,\s*(\d+)\s+skipped)?\s+across", output)
    if match:
        skipped = int(match.group(2) or 0)
        return f"{match.group(1)}/{match.group(1)} passed" + (f", {skipped} skipped" if skipped else "")
    match = re.search(r"(\d+)\s+passed(?:,\s*\d+\s+\w+)*\s+in\s+[\d.]+s", output)
    return f"{match.group(1)}/{match.group(1)} passed" if match else "passed"


def _default_runner(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _command_check(name: str, command: list[str], cwd: Path, timeout: int, runner: CommandRunner) -> CheckResult:
    start = time.monotonic()
    try:
        completed = runner(command, cwd, timeout)
        return CheckResult(
            name=name,
            passed=completed.returncode == 0,
            command=command,
            duration_seconds=time.monotonic() - start,
            details={"returncode": completed.returncode},
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return CheckResult(
            name=name,
            passed=False,
            command=command,
            duration_seconds=time.monotonic() - start,
            details={"error": "timeout", "timeout_seconds": timeout},
            stdout_tail=_tail(str(exc.stdout or "")),
            stderr_tail=_tail(str(exc.stderr or "")),
        )


def compile_check(cwd: Path, runner: CommandRunner = _default_runner) -> CheckResult:
    targets = ["aitestops", "ai_test_asset_center", "benchmark_evaluator", "demo_system", "enterprise_bug_factory", "tests"]
    return _command_check("compileall", [sys.executable, "-m", "compileall", *targets, "-q"], cwd, 120, runner)


def pytest_check(cwd: Path, runner: CommandRunner = _default_runner) -> CheckResult:
    """Run every repository test file with bounded isolated subprocesses.

    Every ``tests/test_*.py`` file is executed. Isolating files avoids a known
    Windows/agent-host process-state hang while preserving the full test surface;
    no test is skipped or converted to xfail by this runner.
    """
    command = [sys.executable, "-m", "pytest", "-q"]
    if runner is not _default_runner:
        return _command_check("pytest", command, cwd, 900, runner)
    start = time.monotonic()
    log_path = cwd / "platform_workspace" / "release_verifier" / "pytest_phase91.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    test_files = sorted((cwd / "tests").glob("test_*.py"))
    if not test_files:
        return CheckResult(name="pytest", passed=False, command=command, details={"error": "no_test_files"})
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    passed = 0
    skipped = 0
    failures: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []
    with log_path.open("w", encoding="utf-8", errors="replace") as stream:
        for test_file in test_files:
            relative = str(test_file.relative_to(cwd)).replace("\\", "/")
            # Keep CI and remote-agent hosts observable during a long full-suite gate.
            print(f"[release pytest] {relative}", flush=True)
            file_command = [sys.executable, "-m", "pytest", "-q", relative]
            file_start = time.monotonic()
            try:
                completed = subprocess.run(
                    file_command, cwd=str(cwd), text=True, encoding="utf-8", errors="replace",
                    capture_output=True, timeout=120, env=env,
                )
                output = f"{completed.stdout}\n{completed.stderr}"
                stream.write(f"\n===== {relative} =====\n{output}\n")
                stream.flush()
                match = re.search(r"(\d+)\s+passed", output)
                skip_match = re.search(r"(\d+)\s+skipped", output)
                file_passed = int(match.group(1)) if match else 0
                file_skipped = int(skip_match.group(1)) if skip_match else 0
                passed += file_passed
                skipped += file_skipped
                row = {"file": relative, "returncode": completed.returncode, "passed": file_passed, "skipped": file_skipped, "duration_seconds": round(time.monotonic() - file_start, 3)}
                file_results.append(row)
                if completed.returncode != 0:
                    failures.append({**row, "stdout_tail": _tail(completed.stdout), "stderr_tail": _tail(completed.stderr)})
                    break
            except subprocess.TimeoutExpired as exc:
                stream.write(f"\n===== {relative} (TIMEOUT) =====\n{exc.stdout or ''}\n{exc.stderr or ''}\n")
                stream.flush()
                failures.append({"file": relative, "error": "timeout", "timeout_seconds": 120, "duration_seconds": round(time.monotonic() - file_start, 3), "stdout_tail": _tail(str(exc.stdout or '')), "stderr_tail": _tail(str(exc.stderr or ''))})
                break
        stream.write(f"\n===== SUMMARY =====\n{passed} passed, {skipped} skipped across {len(file_results)}/{len(test_files)} test files\n")
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
    return CheckResult(
        name="pytest",
        passed=not failures and len(file_results) == len(test_files),
        command=[sys.executable, "-m", "pytest", "-q", "<all-test-files-isolated>"],
        duration_seconds=time.monotonic() - start,
        details={"test_file_count": len(test_files), "completed_file_count": len(file_results), "passed_test_count": passed, "skipped_test_count": skipped, "mode": "full_suite_file_isolated", "log_path": str(log_path.relative_to(cwd)), "failures": failures, "files": file_results},
        stdout_tail=f"{passed} passed, {skipped} skipped across {len(file_results)}/{len(test_files)} test files\n{tail}",
        stderr_tail="",
    )

def product_ui_check(cwd: Path, runner: CommandRunner = _default_runner) -> CheckResult:
    return _command_check("product_ui_tests", [sys.executable, "-m", "pytest", "tests/test_product_ui.py", "-q"], cwd, 60, runner)


def customer_text_quality_check(cwd: Path) -> CheckResult:
    start = time.monotonic()
    findings: list[dict[str, Any]] = []
    for rel in CUSTOMER_VISIBLE_TEXT_FILES:
        path = cwd / rel
        if not path.exists():
            findings.append({"file": rel, "line": 0, "marker": "missing_file"})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for marker in MOJIBAKE_MARKERS:
                if marker in line:
                    findings.append({"file": rel, "line": line_no, "marker": marker, "sample": line[:160]})
                    break
    return CheckResult(
        name="customer_text_quality",
        passed=not findings,
        duration_seconds=time.monotonic() - start,
        details={"files_checked": CUSTOMER_VISIBLE_TEXT_FILES, "findings": findings[:50], "finding_count": len(findings)},
    )


def _page_text_findings(path: str, body: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for marker in MOJIBAKE_MARKERS:
        if marker in body:
            findings.append({"path": path, "marker": marker})
    return findings


def private_service_smoke_check() -> CheckResult:
    page_routes = [
        "/dashboard?project=release_verify_demo",
        "/control-plane?project=release_verify_demo",
        "/knowledge?project=release_verify_demo",
        "/release?project=release_verify_demo",
        "/benchmark?project=release_verify_demo",
    ]
    api_routes = [
        "/api/pilot/overview?project=release_verify_demo",
        "/api/pilot/tasks?project=release_verify_demo",
        "/api/control-plane/overview?project=release_verify_demo",
        "/api/knowledge/asset?project=release_verify_demo",
        "/api/release/dashboard?project=release_verify_demo",
        "/api/benchmark/report?project=release_verify_demo",
    ]
    routes = page_routes + api_routes
    start = time.monotonic()
    statuses: dict[str, int | str] = {}
    shell_checks: dict[str, bool] = {}
    shell_missing_tokens: dict[str, list[str]] = {}
    shell_forbidden_tokens: dict[str, list[str]] = {}
    page_text_findings: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temp:
        server = run_private_pilot_service(root=Path(temp), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
        try:
            for route in routes:
                with urlopen(base_url + route, timeout=20) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    path = route.split("?", 1)[0]
                    statuses[path] = response.status
                    if response.status != 200 or not body:
                        statuses[path] = f"bad_response:{response.status}"
                    if route in page_routes:
                        missing = [token for token in PAGE_SHELL_REQUIRED_TOKENS if token not in body]
                        forbidden = [token for token in PAGE_FORBIDDEN_TOKENS if token in body]
                        shell_missing_tokens[path] = missing
                        shell_forbidden_tokens[path] = forbidden
                        shell_checks[path] = not missing and not forbidden
                        page_text_findings.extend(_page_text_findings(path, body))
        except Exception as exc:  # pragma: no cover - defensive smoke boundary
            statuses["error"] = str(exc)[:300]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    passed = (
        bool(statuses)
        and all(value == 200 for value in statuses.values())
        and all(shell_checks.values())
        and not page_text_findings
    )
    return CheckResult(
        name="private_service_smoke",
        passed=passed,
        duration_seconds=time.monotonic() - start,
        details={
            "statuses": statuses,
            "shell_checks": shell_checks,
            "shell_missing_tokens": shell_missing_tokens,
            "shell_forbidden_tokens": shell_forbidden_tokens,
            "required_shell_tokens": PAGE_SHELL_REQUIRED_TOKENS,
            "forbidden_shell_tokens": PAGE_FORBIDDEN_TOKENS,
            "page_text_findings": page_text_findings[:50],
            "page_text_finding_count": len(page_text_findings),
            "route_count": len(routes),
        },
    )


def build_release_manifest(checks: list[CheckResult], *, include_tests: bool) -> dict[str, Any]:
    by_name = {check.name: check for check in checks}
    tests = by_name.get("pytest")
    product_ui = by_name.get("product_ui_tests")
    smoke = by_name.get("private_service_smoke")
    required_names = {"compileall", "product_ui_tests", "customer_text_quality", "private_service_smoke"}
    if include_tests:
        required_names.add("pytest")
    all_required_passed = all((by_name.get(name) and by_name[name].passed) for name in required_names)
    overall_status = "passed" if all_required_passed else "failed"
    completion_blockers: list[str] = []
    if not include_tests and all_required_passed:
        overall_status = "incomplete"
        completion_blockers.append("full pytest suite was skipped")
    return {
        "phase": "phase91_cognitive_memory_graph",
        "title": "QualiBug Cognitive Memory Graph & Risk Frontier",
        "generated_at_utc": _now(),
        "generated_by": "python -m aitestops.cli verify-release",
        "overall_status": overall_status,
        "release_ready": overall_status == "passed",
        "completion_blockers": completion_blockers,
        "scope": PHASE91_SCOPE,
        "engineering_constraints": {
            "new_frontend_framework": False,
            "new_external_ui_dependency": False,
            "preserves_existing_permissions": True,
            "preserves_approval_and_audit": True,
            "preserves_production_write_block": True,
            "credential_storage": "references_only",
        },
        "validation": {
            "compileall": "passed" if by_name.get("compileall", CheckResult("compileall", False)).passed else "failed",
            "full_test_suite": _pytest_summary(tests) if include_tests else "not_run",
            "product_ui_tests": _pytest_summary(product_ui),
            "customer_visible_text": "passed" if by_name.get("customer_text_quality") and by_name["customer_text_quality"].passed else "failed",
            "private_service_views": "dashboard, control-plane, knowledge, release, benchmark returned 200 with shared UI shell"
            if smoke and smoke.passed else "failed",
            "private_service_read_only_apis": "pilot, control-plane, knowledge, release, benchmark verified locally"
            if smoke and smoke.passed else "failed",
            "windows_sqlite_cleanup_regression": "covered_by_full_pytest" if tests and tests.passed else "not_proven",
        },
        "checks": [check.as_dict() for check in checks],
        "delivery_evidence": [
            "DELIVERY_SCOPE.md",
            "PHASE90_RELEASE_NOTES.md",
            "PRODUCT_90_VERIFICATION.md",
            "PHASE71_DEEP_ANALYSIS.md",
            "PHASE71_PACKAGE_RECEIPT.md",
            "PHASE72_RELEASE_NOTES.md",
            "PHASE72_VERIFICATION.md",
            "PHASE72_PACKAGE_RECEIPT.md",
            "docs/PHASE71_PROJECT_SCOPE_ISOLATION.md",
            "docs/PHASE72_WORLD_MODEL_CONCURRENCY_LEARNING.md",
            "PHASE73_RELEASE_NOTES.md",
            "PHASE73_VERIFICATION.md",
            "PHASE73_PACKAGE_RECEIPT.md",
            "PHASE74_RELEASE_NOTES.md",
            "PHASE74_VERIFICATION.md",
            "PHASE74_PACKAGE_RECEIPT.md",
            "docs/PHASE74_AGENT_DISCOVERY_LOOP.md",
            "PHASE75_RELEASE_NOTES.md",
            "PHASE75_VERIFICATION.md",
            "PHASE75_PACKAGE_RECEIPT.md",
            "docs/PHASE75_AGENT_EXPERIMENT_COMPILER.md",
            "docs/GA_READINESS_AUDIT.md",
        ],
        "delivery_boundary": "No GitHub upload. Runtime outputs, local demo data, caches, logs, databases, credentials and private benchmark ground truth are excluded.",
    }


def verify_release(
    *,
    cwd: Path | None = None,
    output: Path | None = None,
    include_tests: bool = True,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    root = (cwd or Path.cwd()).resolve()
    checks = [
        compile_check(root, runner),
        product_ui_check(root, runner),
        customer_text_quality_check(root),
        private_service_smoke_check(),
    ]
    if include_tests:
        checks.insert(1, pytest_check(root, runner))
    manifest = build_release_manifest(checks, include_tests=include_tests)
    target = output or (root / "PHASE91_RELEASE_MANIFEST.json")
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
