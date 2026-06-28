from __future__ import annotations

"""Phase104F: frontend integration handoff bundle packager.

Phase104A-E made the Enterprise Command Center backend usable by a real
frontend team: local writable API, OpenAPI contract, contract gate, generated
frontend workspace, and runtime smoke.  This module packages those pieces into a
single handoff directory/zip that can be sent to a frontend engineer or CI job.

The handoff bundle contains:

* the Phase104D frontend workspace under ``workspace/``;
* Phase104C API contract acceptance reports under ``contract_acceptance/``;
* Phase104E runtime smoke reports under ``runtime_smoke/``;
* quickstart/runbook/checklist handoff docs under ``handoff/``;
* a manifest, SHA256 checksums, and a zip archive.

Security posture:
* all bundled generated artifacts are scanned for forbidden raw credential
  examples;
* the package can be validated again after transfer by recomputing checksums;
* validation fails on customer-unsafe traceback or raw credential patterns.
"""

import argparse
import hashlib
import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase104_api_contract_acceptance import PHASE104C_VERSION, run_api_contract_acceptance
from ai_test_asset_center.phase104_api_contract_exporter import PHASE104B_VERSION
from ai_test_asset_center.phase104_command_center_http_api import PHASE104A_VERSION
from ai_test_asset_center.phase104_frontend_integration_workspace import (
    PHASE104D_VERSION,
    build_frontend_integration_workspace,
    validate_frontend_integration_workspace,
)
from ai_test_asset_center.phase104_frontend_runtime_smoke import PHASE104E_VERSION, run_frontend_runtime_smoke

PHASE104F_VERSION = "phase104f-frontend-handoff-bundle-v1"

BUNDLE_ZIP_NAME = "phase104_frontend_handoff_bundle.zip"
CHECKSUMS_NAME = "CHECKSUMS.sha256"
MANIFEST_JSON_NAME = "phase104_frontend_handoff_manifest.json"
MANIFEST_MD_NAME = "phase104_frontend_handoff_manifest.md"

REQUIRED_BUNDLE_FILES: tuple[str, ...] = (
    "workspace/workspace_manifest.json",
    "workspace/README_FRONTEND_INTEGRATION.md",
    "workspace/INTEGRATION_CHECKLIST.md",
    "workspace/contract/openapi.json",
    "workspace/contract/API_CONTRACT.md",
    "workspace/src/api/qualibugClient.ts",
    "workspace/src/api/pageDataAdapters.ts",
    "workspace/src/api/qualibugWorkflowSmoke.ts",
    "workspace/src/types/qualibug.ts",
    "contract_acceptance/api_contract_acceptance_report.json",
    "contract_acceptance/api_contract_acceptance_report.md",
    "runtime_smoke/frontend_runtime_smoke_report.json",
    "runtime_smoke/frontend_runtime_smoke_report.md",
    "handoff/README_FRONTEND_HANDOFF.md",
    "handoff/DEV_QUICKSTART.md",
    "handoff/FRONTEND_RUNBOOK.md",
    "handoff/HANDOFF_CHECKLIST.md",
    MANIFEST_JSON_NAME,
    MANIFEST_MD_NAME,
    CHECKSUMS_NAME,
    BUNDLE_ZIP_NAME,
)

FORBIDDEN_BUNDLE_PATTERNS: tuple[str, ...] = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "clientSecret=raw",
    "password=",
    "SESSION=raw",
    "Bearer raw",
    "DemoPasswordShouldBeRedacted",
    "Traceback (most recent call last)",
)


@dataclass(frozen=True)
class BundleValidationCheck:
    key: str
    passed: bool
    detail: str
    severity: str = "critical"


@dataclass
class FrontendHandoffValidationReport:
    passed: bool
    score: int
    version: str
    output_dir: str
    scenario: str
    checks: list[BundleValidationCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "output_dir": self.output_dir,
                "scenario": self.scenario,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
            }
        )


@dataclass(frozen=True)
class FrontendHandoffBundle:
    passed: bool
    score: int
    version: str
    generated_at: str
    scenario: str
    api_base_url: str
    output_dir: str
    zip_path: str
    workspace_passed: bool
    contract_acceptance_passed: bool
    runtime_smoke_passed: bool
    redaction_status: str
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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_bundle_files(root: Path, *, include_zip: bool = False, include_checksums: bool = False) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not include_zip and rel == BUNDLE_ZIP_NAME:
            continue
        if not include_checksums and rel == CHECKSUMS_NAME:
            continue
        files.append(path)
    return files


def _file_manifest(root: Path) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for path in _iter_bundle_files(root, include_zip=False, include_checksums=False):
        rel = path.relative_to(root).as_posix()
        items[rel] = {"size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}
    return items


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_all_text(root: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for path in _iter_bundle_files(root, include_zip=False, include_checksums=True):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".md", ".json", ".ts", ".tsx", ".js", ".env", ".example", ".sha256", ""}:
            continue
        try:
            items.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return items


def scan_bundle_for_secret_leaks(root: str | Path) -> list[str]:
    base = Path(root)
    findings: list[str] = []
    for rel, text in _read_all_text(base):
        lowered = text.lower()
        for pattern in FORBIDDEN_BUNDLE_PATTERNS:
            if pattern.lower() in lowered:
                findings.append(f"{rel}: contains forbidden pattern {pattern}")
    return findings


def write_checksums(root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    lines: list[str] = []
    for path in _iter_bundle_files(root, include_zip=False, include_checksums=False):
        rel = path.relative_to(root).as_posix()
        digest = _sha256_file(path)
        checksums[rel] = digest
        lines.append(f"{digest}  {rel}")
    _write_text(root / CHECKSUMS_NAME, "\n".join(lines) + "\n")
    return checksums


def verify_checksums(root: str | Path) -> tuple[bool, list[str]]:
    base = Path(root)
    path = base / CHECKSUMS_NAME
    if not path.exists():
        return False, ["CHECKSUMS.sha256 missing"]
    findings: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
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


def create_bundle_zip(root: Path) -> Path:
    zip_path = root / BUNDLE_ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _iter_bundle_files(root, include_zip=False, include_checksums=True):
            archive.write(path, path.relative_to(root).as_posix())
    return zip_path


def render_frontend_handoff_readme(bundle: FrontendHandoffBundle) -> str:
    return f"""# Phase104F 前端联调交接包

本目录是 QualiBug Phase104 前端联调交接包，用于把后端本地 API、OpenAPI 合同、TypeScript client、页面适配器、验收报告和运行时 smoke 结果交给前端团队。

## 结论

- 结果：{'通过' if bundle.passed else '未通过'}
- 得分：{bundle.score}/100
- 场景：`{bundle.scenario}`
- API Base URL：`{bundle.api_base_url}`
- Workspace 验收：{bundle.workspace_passed}
- API 合同验收：{bundle.contract_acceptance_passed}
- 运行时 Smoke：{bundle.runtime_smoke_passed}
- 脱敏状态：{bundle.redaction_status}

## 关键入口

- `workspace/README_FRONTEND_INTEGRATION.md`：前端工作区说明
- `workspace/contract/openapi.json`：OpenAPI 合同
- `workspace/src/api/qualibugClient.ts`：前端 API client
- `workspace/src/api/pageDataAdapters.ts`：页面 ViewModel 适配器
- `runtime_smoke/frontend_runtime_smoke_report.md`：运行时联调验证报告
- `contract_acceptance/api_contract_acceptance_report.md`：API 合同验收报告
- `CHECKSUMS.sha256`：交接包完整性校验

本包不包含真实客户 token、cookie、password、session 或 client_secret 原值。
"""


def render_dev_quickstart(api_base_url: str) -> str:
    return f"""# Phase104F 前端开发 Quickstart

## 1. 启动本地 API

```powershell
python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8790
```

## 2. 复制前端联调文件

将 `workspace/src/api` 和 `workspace/src/types` 复制到你的前端工程，或直接参考 `workspace` 目录开始联调。

## 3. 配置 API 地址

```text
VITE_QUALIBUG_API_BASE_URL={api_base_url.rstrip('/')}
```

## 4. 首批页面联调顺序

1. 项目列表
2. 质量驾驶舱
3. 客户环境诊断
4. AI 测试计划
5. 实时测试地图
6. 风险列表与风险详情
7. ROI 价值指标
8. 领导层成果战报

## 5. 联调验收

```powershell
python -m ai_test_asset_center.phase104_frontend_runtime_smoke --build-workspace --workspace-dir .\\outputs\\phase104_frontend_workspace --output-dir .\\outputs\\phase104_frontend_runtime_smoke
```
"""


def render_frontend_runbook() -> str:
    return """# Phase104F 前端联调 Runbook

## 联调前检查

- 确认 `workspace/contract/openapi.json` 存在。
- 确认 `workspace/src/api/qualibugClient.ts` 存在。
- 确认 `runtime_smoke/frontend_runtime_smoke_report.md` 结果通过。
- 确认 `CHECKSUMS.sha256` 校验通过。

## 页面数据适配建议

前端页面不要直接绑定后端原始 JSON。先通过 `pageDataAdapters.ts` 转换成页面 ViewModel，再绑定 UI。这样后端字段补充时，不会影响页面结构。

## 错误处理规范

所有 API 响应都使用：

```ts
{ success, data, error, meta }
```

页面只根据 `success` 判断主流程；失败时展示 `error.message`，不要展示技术 traceback。

## 安全要求

- 不要把真实客户凭证写入 `.env.example`、测试样例或截图。
- 不要把 token/cookie/session/client_secret 放进 Git。
- 对外演示只使用本包提供的脱敏数据。
"""


def render_handoff_checklist() -> str:
    checks = [
        "OpenAPI 合同已导出",
        "API 合同验收已通过",
        "前端工作区已生成",
        "页面数据适配器已生成",
        "运行时 smoke 已通过",
        "交接包 CHECKSUMS 已生成",
        "交接包 zip 已生成",
        "未发现原始凭证或 traceback 泄露",
    ]
    return "# Phase104F 前端交接清单\n\n" + "\n".join(f"- [ ] {item}" for item in checks) + "\n"


def render_manifest_markdown(bundle: FrontendHandoffBundle) -> str:
    lines = [
        "# Phase104F 前端联调交接包 Manifest",
        "",
        f"- 版本：`{bundle.version}`",
        f"- 生成时间：`{bundle.generated_at}`",
        f"- 场景：`{bundle.scenario}`",
        f"- 结论：{'通过' if bundle.passed else '未通过'}",
        f"- 得分：{bundle.score}/100",
        f"- 文件数：{bundle.file_count}",
        f"- Checksum 数：{bundle.checksum_count}",
        f"- Zip：`{Path(bundle.zip_path).name}`",
        "",
        "## 验收状态",
        "",
        f"- Workspace：{bundle.workspace_passed}",
        f"- Contract Acceptance：{bundle.contract_acceptance_passed}",
        f"- Runtime Smoke：{bundle.runtime_smoke_passed}",
        f"- Redaction：{bundle.redaction_status}",
        "",
        "## 核心文件",
        "",
    ]
    for rel in REQUIRED_BUNDLE_FILES:
        lines.append(f"- `{rel}`")
    lines.append("")
    return "\n".join(lines)


def _score_from_checks(checks: list[BundleValidationCheck]) -> int:
    if not checks:
        return 0
    passed = sum(1 for check in checks if check.passed)
    return int(round((passed / len(checks)) * 100))


def validate_frontend_handoff_bundle(output_dir: str | Path) -> FrontendHandoffValidationReport:
    root = Path(output_dir)
    checks: list[BundleValidationCheck] = []

    missing = [rel for rel in REQUIRED_BUNDLE_FILES if not (root / rel).exists()]
    checks.append(BundleValidationCheck("required_files", not missing, "required files present" if not missing else "missing: " + ", ".join(missing[:12])))

    manifest = _read_json(root / MANIFEST_JSON_NAME)
    checks.append(
        BundleValidationCheck(
            "manifest",
            manifest.get("version") == PHASE104F_VERSION and manifest.get("redaction_status") == "safe",
            f"version={manifest.get('version')} redaction={manifest.get('redaction_status')}",
        )
    )

    workspace_report = validate_frontend_integration_workspace(root / "workspace") if (root / "workspace").exists() else None
    checks.append(
        BundleValidationCheck(
            "workspace_validation",
            bool(workspace_report and workspace_report.passed),
            f"score={workspace_report.score if workspace_report else 0}",
        )
    )

    contract_report = _read_json(root / "contract_acceptance" / "api_contract_acceptance_report.json")
    checks.append(
        BundleValidationCheck(
            "contract_acceptance",
            contract_report.get("passed") is True and int(contract_report.get("score") or 0) >= 90,
            f"score={contract_report.get('score')} passed={contract_report.get('passed')}",
        )
    )

    smoke_report = _read_json(root / "runtime_smoke" / "frontend_runtime_smoke_report.json")
    checks.append(
        BundleValidationCheck(
            "runtime_smoke",
            smoke_report.get("passed") is True and int(smoke_report.get("score") or 0) >= 90,
            f"score={smoke_report.get('score')} passed={smoke_report.get('passed')}",
        )
    )

    checksum_ok, checksum_findings = verify_checksums(root)
    checks.append(
        BundleValidationCheck(
            "checksums",
            checksum_ok,
            "all checksums matched" if checksum_ok else "; ".join(checksum_findings[:8]),
        )
    )

    zip_path = root / BUNDLE_ZIP_NAME
    zip_ok = False
    zip_detail = "zip missing"
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
            required_in_zip = set(REQUIRED_BUNDLE_FILES) - {BUNDLE_ZIP_NAME}
            missing_in_zip = sorted(required_in_zip.difference(names))
            zip_ok = not missing_in_zip
            zip_detail = f"entries={len(names)}" if zip_ok else "missing in zip: " + ", ".join(missing_in_zip[:8])
        except zipfile.BadZipFile:
            zip_detail = "zip is not readable"
    checks.append(BundleValidationCheck("zip_archive", zip_ok, zip_detail))

    leaks = scan_bundle_for_secret_leaks(root) if root.exists() else ["bundle missing"]
    checks.append(
        BundleValidationCheck(
            "redaction",
            not leaks,
            "no forbidden raw credential or traceback patterns" if not leaks else "; ".join(leaks[:8]),
        )
    )

    score = _score_from_checks(checks)
    passed = all(check.passed or check.severity != "critical" for check in checks)
    return FrontendHandoffValidationReport(
        passed=passed,
        score=score,
        version=PHASE104F_VERSION,
        output_dir=str(root),
        scenario=str(manifest.get("scenario") or "unknown"),
        checks=checks,
        artifacts={
            "file_count": len(_iter_bundle_files(root, include_zip=True, include_checksums=True)) if root.exists() else 0,
            "redaction_status": "safe" if not leaks else "failed",
            "zip_path": str(zip_path) if zip_path.exists() else None,
        },
    )


def render_validation_markdown(report: FrontendHandoffValidationReport) -> str:
    lines = [
        "# Phase104F 前端交接包验收报告",
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
        lines.append(f"- [{'x' if check.passed else ' '}] `{check.key}`：{check.detail}")
    lines.append("")
    return "\n".join(lines)


def build_frontend_handoff_bundle(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
) -> FrontendHandoffBundle:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    workspace_dir = root / "workspace"
    contract_acceptance_dir = root / "contract_acceptance"
    runtime_smoke_dir = root / "runtime_smoke"
    handoff_dir = root / "handoff"

    build_frontend_integration_workspace(workspace_dir, api_base_url=api_base_url)
    workspace_report = validate_frontend_integration_workspace(workspace_dir)
    contract_acceptance = run_api_contract_acceptance(
        contract_dir=workspace_dir / "contract",
        output_dir=contract_acceptance_dir,
        build_first=False,
        scenario=scenario,
        live_smoke=True,
    )
    runtime_report = run_frontend_runtime_smoke(
        workspace_dir=workspace_dir,
        output_dir=runtime_smoke_dir,
        scenario=scenario,
        api_base_url=api_base_url,
        build_workspace=False,
    )

    redaction_findings = scan_bundle_for_secret_leaks(root)
    redaction_status = "safe" if not redaction_findings else "failed"
    passed = workspace_report.passed and contract_acceptance.get("passed") is True and runtime_report.passed and redaction_status == "safe"
    base_score = min(workspace_report.score, int(contract_acceptance.get("score") or 0), runtime_report.score)
    if redaction_status != "safe":
        base_score = min(base_score, 70)

    preliminary = FrontendHandoffBundle(
        passed=passed,
        score=base_score,
        version=PHASE104F_VERSION,
        generated_at=_now(),
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(root / BUNDLE_ZIP_NAME),
        workspace_passed=workspace_report.passed,
        contract_acceptance_passed=contract_acceptance.get("passed") is True,
        runtime_smoke_passed=runtime_report.passed,
        redaction_status=redaction_status,
        file_count=0,
        checksum_count=0,
        artifact_summary={
            "runtime_versions": {
                "api": PHASE104A_VERSION,
                "contract_exporter": PHASE104B_VERSION,
                "contract_acceptance": PHASE104C_VERSION,
                "workspace": PHASE104D_VERSION,
                "runtime_smoke": PHASE104E_VERSION,
            },
            "workspace_file_count": workspace_report.artifacts.get("file_count"),
            "runtime_step_count": runtime_report.step_count,
            "contract_score": contract_acceptance.get("score"),
            "secret_leak_findings": redaction_findings,
        },
    )

    _write_text(handoff_dir / "README_FRONTEND_HANDOFF.md", render_frontend_handoff_readme(preliminary))
    _write_text(handoff_dir / "DEV_QUICKSTART.md", render_dev_quickstart(api_base_url))
    _write_text(handoff_dir / "FRONTEND_RUNBOOK.md", render_frontend_runbook())
    _write_text(handoff_dir / "HANDOFF_CHECKLIST.md", render_handoff_checklist())

    # Re-scan after writing handoff docs.
    redaction_findings = scan_bundle_for_secret_leaks(root)
    redaction_status = "safe" if not redaction_findings else "failed"
    passed = passed and redaction_status == "safe"
    score = base_score if redaction_status == "safe" else min(base_score, 70)

    manifest_payload = {
        "version": PHASE104F_VERSION,
        "generated_at": _now(),
        "scenario": scenario,
        "api_base_url": api_base_url.rstrip("/"),
        "passed": passed,
        "score": score,
        "workspace_passed": workspace_report.passed,
        "contract_acceptance_passed": contract_acceptance.get("passed") is True,
        "runtime_smoke_passed": runtime_report.passed,
        "redaction_status": redaction_status,
        "secret_leak_findings": redaction_findings,
        "files": _file_manifest(root),
        "required_files": list(REQUIRED_BUNDLE_FILES),
        "versions": preliminary.artifact_summary["runtime_versions"],
    }
    _write_text(root / MANIFEST_JSON_NAME, _json_text(manifest_payload))
    bundle_shell = FrontendHandoffBundle(
        passed=passed,
        score=score,
        version=PHASE104F_VERSION,
        generated_at=str(manifest_payload["generated_at"]),
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(root / BUNDLE_ZIP_NAME),
        workspace_passed=workspace_report.passed,
        contract_acceptance_passed=contract_acceptance.get("passed") is True,
        runtime_smoke_passed=runtime_report.passed,
        redaction_status=redaction_status,
        file_count=len(_file_manifest(root)),
        checksum_count=0,
        artifact_summary=preliminary.artifact_summary,
    )
    _write_text(root / MANIFEST_MD_NAME, render_manifest_markdown(bundle_shell))
    checksums = write_checksums(root)
    zip_path = create_bundle_zip(root)

    validation = validate_frontend_handoff_bundle(root)
    final_passed = passed and validation.passed
    final_score = min(score, validation.score)
    final_bundle = FrontendHandoffBundle(
        passed=final_passed,
        score=final_score,
        version=PHASE104F_VERSION,
        generated_at=str(manifest_payload["generated_at"]),
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(zip_path),
        workspace_passed=workspace_report.passed,
        contract_acceptance_passed=contract_acceptance.get("passed") is True,
        runtime_smoke_passed=runtime_report.passed,
        redaction_status=redaction_status,
        file_count=len(_iter_bundle_files(root, include_zip=True, include_checksums=True)),
        checksum_count=len(checksums),
        artifact_summary={**preliminary.artifact_summary, "validation": validation.to_dict()},
    )

    # Rewrite handoff summary and manifest with final validation result, then refresh checksums/zip.
    _write_text(handoff_dir / "README_FRONTEND_HANDOFF.md", render_frontend_handoff_readme(final_bundle))
    manifest_payload.update(
        {
            "passed": final_bundle.passed,
            "score": final_bundle.score,
            "file_count": final_bundle.file_count,
            "checksum_count": final_bundle.checksum_count,
            "validation": validation.to_dict(),
            "files": _file_manifest(root),
        }
    )
    _write_text(root / MANIFEST_JSON_NAME, _json_text(manifest_payload))
    _write_text(root / MANIFEST_MD_NAME, render_manifest_markdown(final_bundle))
    checksums = write_checksums(root)
    zip_path = create_bundle_zip(root)

    final_validation = validate_frontend_handoff_bundle(root)
    final_bundle = FrontendHandoffBundle(
        passed=final_bundle.passed and final_validation.passed,
        score=min(final_bundle.score, final_validation.score),
        version=PHASE104F_VERSION,
        generated_at=final_bundle.generated_at,
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(zip_path),
        workspace_passed=workspace_report.passed,
        contract_acceptance_passed=contract_acceptance.get("passed") is True,
        runtime_smoke_passed=runtime_report.passed,
        redaction_status=redaction_status,
        file_count=len(_iter_bundle_files(root, include_zip=True, include_checksums=True)),
        checksum_count=len(checksums),
        artifact_summary={**preliminary.artifact_summary, "validation": final_validation.to_dict()},
    )
    return final_bundle


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate a Phase104F frontend integration handoff bundle.")
    parser.add_argument("--output-dir", default="outputs/phase104_frontend_handoff_bundle", help="Directory for generated handoff bundle.")
    parser.add_argument("--scenario", choices=["manufacturing", "ecommerce", "saas"], default="manufacturing", help="Runtime smoke seed scenario.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="API base URL written into frontend examples.")
    parser.add_argument("--validate-only", action="store_true", help="Validate an existing handoff bundle without regenerating it.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    root = Path(args.output_dir)
    if args.validate_only:
        report = validate_frontend_handoff_bundle(root)
        _write_text(root / "frontend_handoff_bundle_acceptance_report.json", _json_text(report.to_dict()))
        _write_text(root / "frontend_handoff_bundle_acceptance_report.md", render_validation_markdown(report))
        print(_json_text(report.to_dict()))
        return 0 if report.passed else 2
    bundle = build_frontend_handoff_bundle(output_dir=root, scenario=args.scenario, api_base_url=args.api_base_url)
    report = validate_frontend_handoff_bundle(root)
    _write_text(root / "frontend_handoff_bundle_acceptance_report.json", _json_text(report.to_dict()))
    _write_text(root / "frontend_handoff_bundle_acceptance_report.md", render_validation_markdown(report))
    print(_json_text(bundle.to_dict()))
    return 0 if bundle.passed and report.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PHASE104F_VERSION",
    "FrontendHandoffBundle",
    "FrontendHandoffValidationReport",
    "build_frontend_handoff_bundle",
    "validate_frontend_handoff_bundle",
    "verify_checksums",
    "scan_bundle_for_secret_leaks",
    "main",
]
