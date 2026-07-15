from __future__ import annotations

"""Phase104H: CI quality gate exporter for the Phase104 frontend release chain.

Phase104G can prove that a frontend release readiness bundle is complete,
checksummed, and safe.  This module makes that gate repeatable in local scripts
and GitHub Actions by producing a CI handoff folder with:

* a GitHub Actions workflow template;
* a local PowerShell gate script;
* a CI runbook and release policy;
* an in-process validation report for the Phase104G release readiness bundle;
* checksums and a zip archive for transfer.

The implementation stays dependency-free and uses the existing Phase104 gates in
process, so it can run in a clean Python environment without Node, browsers, or
external services.
"""

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase104_frontend_release_readiness import (
    PHASE104G_VERSION,
    RELEASE_ZIP_NAME as FRONTEND_RELEASE_ZIP_NAME,
    build_frontend_release_readiness,
    scan_release_for_secret_leaks,
    validate_frontend_release_readiness,
    verify_release_checksums,
)

PHASE104H_VERSION = "phase104h-ci-quality-gate-v1"

CI_MANIFEST_JSON = "phase104_ci_quality_gate_manifest.json"
CI_MANIFEST_MD = "phase104_ci_quality_gate_manifest.md"
CI_REPORT_JSON = "ci_quality_gate_report.json"
CI_REPORT_MD = "ci_quality_gate_report.md"
CI_CHECKSUMS = "CHECKSUMS.sha256"
CI_ZIP_NAME = "phase104_ci_quality_gate_bundle.zip"
RELEASE_DIR_NAME = "frontend_release_readiness"

REQUIRED_CI_FILES: tuple[str, ...] = (
    ".github/workflows/qualibug_phase104_quality_gate.yml",
    "scripts/Run-Phase104QualityGate.ps1",
    "scripts/run_phase104_quality_gate.py",
    "docs/CI_QUALITY_GATE_RUNBOOK.md",
    "docs/CI_RELEASE_POLICY.md",
    "docs/GITHUB_ACTIONS_SETUP.md",
    f"{RELEASE_DIR_NAME}/phase104_frontend_release_manifest.json",
    f"{RELEASE_DIR_NAME}/release_readiness_report.json",
    f"{RELEASE_DIR_NAME}/CHECKSUMS.sha256",
    f"{RELEASE_DIR_NAME}/{FRONTEND_RELEASE_ZIP_NAME}",
    CI_MANIFEST_JSON,
    CI_MANIFEST_MD,
    CI_REPORT_JSON,
    CI_REPORT_MD,
    CI_CHECKSUMS,
    CI_ZIP_NAME,
)

FORBIDDEN_CI_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Traceback (most recent call last)",
)

CHECKSUM_EXCLUDED_ROOT_FILES: tuple[str, ...] = (
    CI_CHECKSUMS,
    CI_ZIP_NAME,
    CI_REPORT_JSON,
    CI_REPORT_MD,
)


@dataclass(frozen=True)
class CIQualityGateCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "platform"
    severity: str = "critical"


@dataclass
class CIQualityGateReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[CIQualityGateCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "scenario": self.scenario,
                "output_dir": self.output_dir,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
            }
        )


@dataclass(frozen=True)
class CIQualityGateBundle:
    passed: bool
    score: int
    version: str
    generated_at: str
    scenario: str
    api_base_url: str
    output_dir: str
    zip_path: str
    release_readiness_passed: bool
    release_checksum_ok: bool
    redaction_status: str
    gate_count: int
    file_count: int
    checksum_count: int
    artifact_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return redact_value(asdict(self))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_text(value: Any) -> str:
    return json.dumps(redact_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_ci_files(root: Path, *, include_zip: bool = False, include_checksums: bool = False, include_reports: bool = False) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not include_zip and rel == CI_ZIP_NAME:
            continue
        if not include_checksums and rel == CI_CHECKSUMS:
            continue
        if not include_reports and rel in {CI_REPORT_JSON, CI_REPORT_MD}:
            continue
        files.append(path)
    return files


def _file_manifest(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {"size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        for path in _iter_ci_files(root, include_zip=False, include_checksums=False, include_reports=False)
    }


def _read_all_text(root: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for path in _iter_ci_files(root, include_zip=False, include_checksums=True, include_reports=True):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".md", ".json", ".ts", ".tsx", ".js", ".env", ".example", ".sha256", ".ps1", ".py", ".yml", ".yaml", ""}:
            continue
        try:
            items.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return items


def scan_ci_quality_gate_for_secret_leaks(root: str | Path) -> list[str]:
    base = Path(root)
    findings: list[str] = []
    for rel, text in _read_all_text(base):
        lowered = text.lower()
        for pattern in FORBIDDEN_CI_PATTERNS:
            if pattern.lower() in lowered:
                findings.append(f"{rel}: contains forbidden pattern {pattern}")
    release_dir = base / RELEASE_DIR_NAME
    if release_dir.exists():
        findings.extend(f"{RELEASE_DIR_NAME}/{item}" for item in scan_release_for_secret_leaks(release_dir))
    return sorted(set(findings))


def write_ci_checksums(root: str | Path) -> dict[str, str]:
    base = Path(root)
    checksums: dict[str, str] = {}
    lines: list[str] = []
    for path in _iter_ci_files(base, include_zip=False, include_checksums=False, include_reports=False):
        rel = path.relative_to(base).as_posix()
        if rel in CHECKSUM_EXCLUDED_ROOT_FILES:
            continue
        digest = _sha256_file(path)
        checksums[rel] = digest
        lines.append(f"{digest}  {rel}")
    _write_text(base / CI_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_ci_checksums(root: str | Path) -> tuple[bool, list[str]]:
    base = Path(root)
    checksum_path = base / CI_CHECKSUMS
    if not checksum_path.exists():
        return False, ["CHECKSUMS.sha256 missing"]
    findings: list[str] = []
    for lineno, line in enumerate(checksum_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            expected, rel = line.split("  ", 1)
        except ValueError:
            findings.append(f"line {lineno}: malformed checksum line")
            continue
        item = base / rel
        if not item.exists():
            findings.append(f"{rel}: missing")
            continue
        actual = _sha256_file(item)
        if actual != expected:
            findings.append(f"{rel}: checksum mismatch")
    return not findings, findings


def create_ci_zip(root: str | Path) -> Path:
    base = Path(root)
    zip_path = base / CI_ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _iter_ci_files(base, include_zip=False, include_checksums=True, include_reports=True):
            archive.write(path, path.relative_to(base).as_posix())
    return zip_path


def render_github_actions_workflow(scenario: str) -> str:
    return f"""name: QualiBug Phase104 Quality Gate

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:

jobs:
  phase104-quality-gate:
    runs-on: windows-latest
    timeout-minutes: 30
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Run full pytest suite
        shell: pwsh
        run: python -m pytest -q --basetemp .\\.pytest_tmp\\ci

      - name: Build frontend release readiness gate
        shell: pwsh
        run: python -m ai_test_asset_center.phase104_frontend_release_readiness --scenario {scenario} --output-dir .\\outputs\\phase104_frontend_release_readiness_ci

      - name: Verify frontend release readiness gate
        shell: pwsh
        run: python -m ai_test_asset_center.phase104_frontend_release_readiness --validate-only --output-dir .\\outputs\\phase104_frontend_release_readiness_ci

      - name: Build CI quality gate bundle
        shell: pwsh
        run: python -m ai_test_asset_center.phase104_ci_quality_gate --scenario {scenario} --no-build-release --release-dir .\\outputs\\phase104_frontend_release_readiness_ci --output-dir .\\outputs\\phase104_ci_quality_gate

      - name: Upload quality gate artifacts
        uses: actions/upload-artifact@v4
        with:
          name: phase104-ci-quality-gate
          path: outputs/phase104_ci_quality_gate
"""


def render_local_powershell_script(scenario: str, api_base_url: str) -> str:
    return fr"""$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot\..\..

Remove-Item -Recurse -Force .\.pytest_tmp -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force .\.pytest_tmp | Out-Null
$env:TEMP = "$PWD\.pytest_tmp"
$env:TMP = "$PWD\.pytest_tmp"

python -m pytest -q --basetemp .\.pytest_tmp\run
python -m ai_test_asset_center.phase104_frontend_release_readiness --scenario {scenario} --api-base-url {api_base_url} --output-dir .\outputs\phase104_frontend_release_readiness_ci
python -m ai_test_asset_center.phase104_frontend_release_readiness --validate-only --output-dir .\outputs\phase104_frontend_release_readiness_ci
python -m ai_test_asset_center.phase104_ci_quality_gate --scenario {scenario} --api-base-url {api_base_url} --no-build-release --release-dir .\outputs\phase104_frontend_release_readiness_ci --output-dir .\outputs\phase104_ci_quality_gate
python -m ai_test_asset_center.phase104_ci_quality_gate --validate-only --output-dir .\outputs\phase104_ci_quality_gate

Remove-Item -Recurse -Force .\.pytest_tmp -ErrorAction SilentlyContinue
"""


def render_python_runner_script() -> str:
    return """from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_test_asset_center.phase104_ci_quality_gate import build_ci_quality_gate, validate_ci_quality_gate


def main() -> int:
    parser = argparse.ArgumentParser(description='Run QualiBug Phase104 CI quality gate.')
    parser.add_argument('--output-dir', default='outputs/phase104_ci_quality_gate')
    parser.add_argument('--scenario', default='manufacturing')
    parser.add_argument('--api-base-url', default='http://127.0.0.1:8088')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()

    if args.validate_only:
        report = validate_ci_quality_gate(Path(args.output_dir))
        print(report.to_dict())
        return 0 if report.passed else 1

    bundle = build_ci_quality_gate(
        output_dir=Path(args.output_dir),
        scenario=args.scenario,
        api_base_url=args.api_base_url,
    )
    print(bundle.to_dict())
    return 0 if bundle.passed else 1


if __name__ == '__main__':
    sys.exit(main())
"""


def render_ci_runbook(bundle: CIQualityGateBundle) -> str:
    return f"""# Phase104H CI 质量门禁 Runbook

## 结论

- 结果：{'通过' if bundle.passed else '未通过'}
- 得分：{bundle.score}/100
- 场景：`{bundle.scenario}`
- API Base URL：`{bundle.api_base_url}`
- Phase104G 发布就绪：{bundle.release_readiness_passed}
- Phase104G checksum：{bundle.release_checksum_ok}
- 脱敏状态：{bundle.redaction_status}

## 本地运行

```powershell
.\\outputs\\phase104_ci_quality_gate\\scripts\\Run-Phase104QualityGate.ps1
```

## CI 必须执行的门禁

1. `python -m pytest -q`
2. `python -m ai_test_asset_center.phase104_frontend_release_readiness`
3. `python -m ai_test_asset_center.phase104_frontend_release_readiness --validate-only`
4. `python -m ai_test_asset_center.phase104_ci_quality_gate --validate-only`

任一失败都不能进入前端发布或客户演示。
"""


def render_ci_release_policy() -> str:
    return """# Phase104H CI 发布策略

## 阻断条件

- 全量 pytest 未通过。
- Phase104G release readiness 未通过。
- checksum 复验失败。
- API 合同验收或前端 runtime smoke 未通过。
- 检测到 token、cookie、password、session、client_secret 或 traceback 泄露。
- GitHub Actions workflow 或本地 PowerShell 门禁脚本缺失。

## 允许发布条件

- 本目录 `ci_quality_gate_report.json` 中 `passed=true`。
- `phase104_ci_quality_gate_manifest.json` 中 `redaction_status=safe`。
- `CHECKSUMS.sha256` 可复验。
- `phase104_ci_quality_gate_bundle.zip` 可解压并包含 release readiness 账本。
"""


def render_github_setup() -> str:
    return """# Phase104H GitHub Actions 接入说明

把本目录生成的 `.github/workflows/qualibug_phase104_quality_gate.yml` 复制到仓库根目录同名位置后，push 或 pull request 会自动执行 Phase104 质量门禁。

注意：示例 workflow 不需要真实客户凭证，不访问公网客户系统，只使用本地 in-process gate 和 demo seed 数据。
"""


def _score_from_checks(checks: list[CIQualityGateCheck]) -> int:
    if not checks:
        return 0
    passed = sum(1 for check in checks if check.passed)
    return int(round((passed / len(checks)) * 100))


def _build_manifest_payload(bundle: CIQualityGateBundle, root: Path) -> dict[str, Any]:
    return redact_value(
        {
            "version": PHASE104H_VERSION,
            "generated_at": bundle.generated_at,
            "scenario": bundle.scenario,
            "api_base_url": bundle.api_base_url,
            "passed": bundle.passed,
            "score": bundle.score,
            "release_readiness_passed": bundle.release_readiness_passed,
            "release_checksum_ok": bundle.release_checksum_ok,
            "redaction_status": bundle.redaction_status,
            "gate_count": bundle.gate_count,
            "file_count": bundle.file_count,
            "checksum_count": bundle.checksum_count,
            "required_files": list(REQUIRED_CI_FILES),
            "files": _file_manifest(root),
            "versions": {
                "phase104g": PHASE104G_VERSION,
                "phase104h": PHASE104H_VERSION,
            },
            "artifact_summary": bundle.artifact_summary,
        }
    )


def render_ci_manifest_markdown(bundle: CIQualityGateBundle) -> str:
    lines = [
        "# Phase104H CI 质量门禁 Manifest",
        "",
        f"- 版本：`{bundle.version}`",
        f"- 生成时间：`{bundle.generated_at}`",
        f"- 场景：`{bundle.scenario}`",
        f"- 结论：{'通过' if bundle.passed else '未通过'}",
        f"- 得分：{bundle.score}/100",
        f"- 发布就绪：{bundle.release_readiness_passed}",
        f"- 发布 checksum：{bundle.release_checksum_ok}",
        f"- 脱敏状态：{bundle.redaction_status}",
        f"- Zip：`{Path(bundle.zip_path).name}`",
        "",
        "## 必备文件",
        "",
    ]
    lines.extend(f"- `{rel}`" for rel in REQUIRED_CI_FILES)
    lines.append("")
    return "\n".join(lines)


def render_ci_report_markdown(report: CIQualityGateReport) -> str:
    lines = [
        "# Phase104H CI 质量门禁验收报告",
        "",
        f"- 结果：{'通过' if report.passed else '未通过'}",
        f"- 得分：{report.score}/100",
        f"- 场景：`{report.scenario}`",
        f"- 目录：`{report.output_dir}`",
        f"- 脱敏状态：{report.artifacts.get('redaction_status')}",
        "",
        "## 检查项",
        "",
    ]
    for check in report.checks:
        lines.append(f"- [{'x' if check.passed else ' '}] `{check.key}`（{check.owner}）：{check.detail}")
    lines.append("")
    return "\n".join(lines)


def validate_ci_quality_gate(output_dir: str | Path) -> CIQualityGateReport:
    root = Path(output_dir)
    release_dir = root / RELEASE_DIR_NAME
    checks: list[CIQualityGateCheck] = []

    missing = [rel for rel in REQUIRED_CI_FILES if not (root / rel).exists()]
    checks.append(CIQualityGateCheck("required_files", not missing, "required files present" if not missing else "missing: " + ", ".join(missing[:12]), "release-manager"))

    release_report = validate_frontend_release_readiness(release_dir) if release_dir.exists() else None
    checks.append(
        CIQualityGateCheck(
            "frontend_release_readiness",
            bool(release_report and release_report.passed and release_report.score >= 90),
            f"score={release_report.score if release_report else 0} passed={release_report.passed if release_report else False}",
            "qa-owner",
        )
    )

    release_checksum_ok, release_checksum_findings = verify_release_checksums(release_dir) if release_dir.exists() else (False, ["release readiness missing"])
    checks.append(
        CIQualityGateCheck(
            "frontend_release_checksums",
            release_checksum_ok,
            "release checksums matched" if release_checksum_ok else "; ".join(release_checksum_findings[:8]),
            "qa-owner",
        )
    )

    workflow_text = (root / ".github" / "workflows" / "qualibug_phase104_quality_gate.yml").read_text(encoding="utf-8") if (root / ".github" / "workflows" / "qualibug_phase104_quality_gate.yml").exists() else ""
    workflow_requirements = (
        "python -m pytest -q",
        "phase104_frontend_release_readiness",
        "phase104_ci_quality_gate",
        "actions/upload-artifact@v4",
    )
    missing_workflow = [item for item in workflow_requirements if item not in workflow_text]
    checks.append(
        CIQualityGateCheck(
            "github_actions_workflow",
            not missing_workflow,
            "workflow covers pytest, release gate, CI bundle, artifact upload" if not missing_workflow else "missing: " + ", ".join(missing_workflow),
            "devops-owner",
        )
    )

    ps_text = (root / "scripts" / "Run-Phase104QualityGate.ps1").read_text(encoding="utf-8") if (root / "scripts" / "Run-Phase104QualityGate.ps1").exists() else ""
    checks.append(
        CIQualityGateCheck(
            "local_powershell_gate",
            "python -m pytest -q" in ps_text and "phase104_ci_quality_gate --validate-only" in ps_text,
            "local PowerShell gate covers pytest and validate-only" if ps_text else "local gate missing",
            "devops-owner",
        )
    )

    runbook_text = (root / "docs" / "CI_QUALITY_GATE_RUNBOOK.md").read_text(encoding="utf-8") if (root / "docs" / "CI_QUALITY_GATE_RUNBOOK.md").exists() else ""
    policy_text = (root / "docs" / "CI_RELEASE_POLICY.md").read_text(encoding="utf-8") if (root / "docs" / "CI_RELEASE_POLICY.md").exists() else ""
    checks.append(
        CIQualityGateCheck(
            "ci_runbook_and_policy",
            "阻断条件" in policy_text and "CI 必须执行的门禁" in runbook_text,
            "runbook and release policy present" if runbook_text and policy_text else "runbook or policy missing",
            "release-manager",
        )
    )

    manifest = _read_json(root / CI_MANIFEST_JSON)
    checks.append(
        CIQualityGateCheck(
            "ci_manifest",
            manifest.get("version") == PHASE104H_VERSION and manifest.get("redaction_status") == "safe",
            f"version={manifest.get('version')} redaction={manifest.get('redaction_status')}",
            "release-manager",
        )
    )

    checksum_ok, checksum_findings = verify_ci_checksums(root)
    checks.append(
        CIQualityGateCheck(
            "ci_bundle_checksums",
            checksum_ok,
            "CI checksums matched" if checksum_ok else "; ".join(checksum_findings[:8]),
            "qa-owner",
        )
    )

    zip_path = root / CI_ZIP_NAME
    zip_ok = False
    zip_detail = "zip missing"
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
            needed = {
                ".github/workflows/qualibug_phase104_quality_gate.yml",
                "docs/CI_QUALITY_GATE_RUNBOOK.md",
                f"{RELEASE_DIR_NAME}/release_readiness_report.md",
            }
            missing_zip = sorted(needed - names)
            zip_ok = not missing_zip
            zip_detail = "zip contains CI and release artifacts" if zip_ok else "zip missing: " + ", ".join(missing_zip)
        except zipfile.BadZipFile:
            zip_detail = "zip is not readable"
    checks.append(CIQualityGateCheck("ci_zip_archive", zip_ok, zip_detail, "release-manager"))

    leaks = scan_ci_quality_gate_for_secret_leaks(root) if root.exists() else ["output dir missing"]
    checks.append(
        CIQualityGateCheck(
            "secret_and_traceback_scan",
            not leaks,
            "no forbidden secret or traceback patterns" if not leaks else "; ".join(leaks[:8]),
            "security-reviewer",
        )
    )

    score = _score_from_checks(checks)
    passed = all(check.passed for check in checks)
    report = CIQualityGateReport(
        passed=passed,
        score=score,
        version=PHASE104H_VERSION,
        scenario=str(manifest.get("scenario") or "unknown"),
        output_dir=str(root),
        checks=checks,
        artifacts={
            "redaction_status": "safe" if not leaks else "blocked",
            "release_readiness_passed": bool(release_report and release_report.passed),
            "release_checksum_ok": release_checksum_ok,
            "checksum_ok": checksum_ok,
            "zip_path": str(zip_path),
        },
    )
    _write_text(root / CI_REPORT_JSON, _json_text(report.to_dict()))
    _write_text(root / CI_REPORT_MD, render_ci_report_markdown(report))
    return report


def build_ci_quality_gate(
    *,
    output_dir: str | Path = "outputs/phase104_ci_quality_gate",
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    build_release: bool = True,
    release_dir: str | Path | None = None,
) -> CIQualityGateBundle:
    root = Path(output_dir)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    release_root = root / RELEASE_DIR_NAME
    if build_release:
        release_bundle = build_frontend_release_readiness(output_dir=release_root, scenario=scenario, api_base_url=api_base_url)
    else:
        if release_dir is None:
            raise ValueError("release_dir is required when build_release=False")
        source = Path(release_dir)
        if not source.exists():
            raise FileNotFoundError(f"release_dir does not exist: {source}")
        shutil.copytree(source, release_root)
        release_report = validate_frontend_release_readiness(release_root)
        release_bundle = None
        if not release_report.passed:
            _write_text(root / "docs" / "SOURCE_RELEASE_NOT_READY.md", render_ci_report_markdown(CIQualityGateReport(False, release_report.score, PHASE104H_VERSION, scenario, str(root), [CIQualityGateCheck("source_release", False, "source release readiness failed")], {})))

    _write_text(root / ".github" / "workflows" / "qualibug_phase104_quality_gate.yml", render_github_actions_workflow(scenario))
    _write_text(root / "scripts" / "Run-Phase104QualityGate.ps1", render_local_powershell_script(scenario, api_base_url))
    _write_text(root / "scripts" / "run_phase104_quality_gate.py", render_python_runner_script())

    release_report = validate_frontend_release_readiness(release_root)
    release_checksum_ok, release_checksum_findings = verify_release_checksums(release_root)
    leaks = scan_ci_quality_gate_for_secret_leaks(root)

    prelim_checks = [
        CIQualityGateCheck("release_readiness", release_report.passed and release_report.score >= 90, f"score={release_report.score} passed={release_report.passed}", "qa-owner"),
        CIQualityGateCheck("release_checksums", release_checksum_ok, "release checksums matched" if release_checksum_ok else "; ".join(release_checksum_findings[:8]), "qa-owner"),
        CIQualityGateCheck("secret_scan", not leaks, "no forbidden secret or traceback patterns" if not leaks else "; ".join(leaks[:8]), "security-reviewer"),
    ]
    prelim_score = _score_from_checks(prelim_checks)
    prelim_passed = all(check.passed for check in prelim_checks)

    bundle = CIQualityGateBundle(
        passed=prelim_passed,
        score=prelim_score,
        version=PHASE104H_VERSION,
        generated_at=_now(),
        scenario=scenario,
        api_base_url=api_base_url,
        output_dir=str(root),
        zip_path=str(root / CI_ZIP_NAME),
        release_readiness_passed=release_report.passed,
        release_checksum_ok=release_checksum_ok,
        redaction_status="safe" if not leaks else "blocked",
        gate_count=len(prelim_checks),
        file_count=0,
        checksum_count=0,
        artifact_summary={
            "release_report": str(release_root / "release_readiness_report.md"),
            "release_zip": str(release_root / FRONTEND_RELEASE_ZIP_NAME),
            "github_workflow": str(root / ".github" / "workflows" / "qualibug_phase104_quality_gate.yml"),
            "local_script": str(root / "scripts" / "Run-Phase104QualityGate.ps1"),
            "source_release_built": build_release,
            "source_release_dir": str(release_dir) if release_dir else str(release_root),
        },
    )

    _write_text(root / "docs" / "CI_QUALITY_GATE_RUNBOOK.md", render_ci_runbook(bundle))
    _write_text(root / "docs" / "CI_RELEASE_POLICY.md", render_ci_release_policy())
    _write_text(root / "docs" / "GITHUB_ACTIONS_SETUP.md", render_github_setup())

    manifest_payload = _build_manifest_payload(bundle, root)
    _write_text(root / CI_MANIFEST_JSON, _json_text(manifest_payload))
    _write_text(root / CI_MANIFEST_MD, render_ci_manifest_markdown(bundle))

    checksums = write_ci_checksums(root)
    zip_path = create_ci_zip(root)

    report = validate_ci_quality_gate(root)
    first_final_bundle = CIQualityGateBundle(
        passed=report.passed,
        score=report.score,
        version=PHASE104H_VERSION,
        generated_at=bundle.generated_at,
        scenario=scenario,
        api_base_url=api_base_url,
        output_dir=str(root),
        zip_path=str(zip_path),
        release_readiness_passed=bool(report.artifacts.get("release_readiness_passed")),
        release_checksum_ok=bool(report.artifacts.get("release_checksum_ok")),
        redaction_status=str(report.artifacts.get("redaction_status")),
        gate_count=len(report.checks),
        file_count=len(_iter_ci_files(root, include_zip=True, include_checksums=True, include_reports=True)),
        checksum_count=len(checksums),
        artifact_summary=bundle.artifact_summary | {"ci_report": str(root / CI_REPORT_MD), "ci_zip": str(zip_path)},
    )
    _write_text(root / CI_MANIFEST_JSON, _json_text(_build_manifest_payload(first_final_bundle, root)))
    _write_text(root / CI_MANIFEST_MD, render_ci_manifest_markdown(first_final_bundle))
    checksums = write_ci_checksums(root)
    zip_path = create_ci_zip(root)

    # Re-validate after the final manifest/checksum/zip refresh.  This avoids
    # returning a stale bundle when the first validation had to create reports.
    final_report = validate_ci_quality_gate(root)
    final_bundle = CIQualityGateBundle(
        passed=final_report.passed,
        score=final_report.score,
        version=PHASE104H_VERSION,
        generated_at=bundle.generated_at,
        scenario=scenario,
        api_base_url=api_base_url,
        output_dir=str(root),
        zip_path=str(zip_path),
        release_readiness_passed=bool(final_report.artifacts.get("release_readiness_passed")),
        release_checksum_ok=bool(final_report.artifacts.get("release_checksum_ok")),
        redaction_status=str(final_report.artifacts.get("redaction_status")),
        gate_count=len(final_report.checks),
        file_count=len(_iter_ci_files(root, include_zip=True, include_checksums=True, include_reports=True)),
        checksum_count=len(checksums),
        artifact_summary=bundle.artifact_summary | {"ci_report": str(root / CI_REPORT_MD), "ci_zip": str(zip_path)},
    )
    _write_text(root / CI_MANIFEST_JSON, _json_text(_build_manifest_payload(final_bundle, root)))
    _write_text(root / CI_MANIFEST_MD, render_ci_manifest_markdown(final_bundle))
    checksums = write_ci_checksums(root)
    zip_path = create_ci_zip(root)
    final_report = validate_ci_quality_gate(root)
    return CIQualityGateBundle(
        passed=final_report.passed,
        score=final_report.score,
        version=PHASE104H_VERSION,
        generated_at=bundle.generated_at,
        scenario=scenario,
        api_base_url=api_base_url,
        output_dir=str(root),
        zip_path=str(zip_path),
        release_readiness_passed=bool(final_report.artifacts.get("release_readiness_passed")),
        release_checksum_ok=bool(final_report.artifacts.get("release_checksum_ok")),
        redaction_status=str(final_report.artifacts.get("redaction_status")),
        gate_count=len(final_report.checks),
        file_count=len(_iter_ci_files(root, include_zip=True, include_checksums=True, include_reports=True)),
        checksum_count=len(checksums),
        artifact_summary=bundle.artifact_summary | {"ci_report": str(root / CI_REPORT_MD), "ci_zip": str(zip_path)},
    )

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate the Phase104H CI quality gate bundle.")
    parser.add_argument("--output-dir", default="outputs/phase104_ci_quality_gate")
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--no-build-release", action="store_true", help="Use --release-dir instead of building a fresh Phase104G release readiness bundle.")
    parser.add_argument("--release-dir", default=None)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    if args.validate_only:
        report = validate_ci_quality_gate(args.output_dir)
        print(_json_text(report.to_dict()))
        return 0 if report.passed else 1

    bundle = build_ci_quality_gate(
        output_dir=args.output_dir,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        build_release=not args.no_build_release,
        release_dir=args.release_dir,
    )
    print(_json_text(bundle.to_dict()))
    return 0 if bundle.passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

