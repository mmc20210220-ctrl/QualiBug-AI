from __future__ import annotations

"""Phase104G: frontend release readiness ledger.

Phase104F produces a self-contained frontend handoff bundle.  This module adds
one more formal release gate around that package so the team can answer four
questions before giving the integration materials to a frontend team, a pilot
customer, or a CI pipeline:

* is the handoff bundle present and internally valid;
* did API contract acceptance and frontend runtime smoke pass;
* can the bundle be verified by SHA256 after transfer;
* is there a release note, cutover plan, rollback plan, and signoff ledger.

The output is intentionally dependency-free and file-based: JSON/Markdown
reports, checksums, and a zip archive.  No external server, Node runtime, or
browser is required.
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
from ai_test_asset_center.phase104_command_center_http_api import PHASE104A_VERSION
from ai_test_asset_center.phase104_frontend_handoff_bundle import (
    BUNDLE_ZIP_NAME as HANDOFF_ZIP_NAME,
    PHASE104F_VERSION,
    build_frontend_handoff_bundle,
    scan_bundle_for_secret_leaks as scan_frontend_handoff_for_secret_leaks,
    validate_frontend_handoff_bundle,
    render_validation_markdown as render_frontend_handoff_validation_markdown,
    verify_checksums as verify_frontend_handoff_checksums,
)

PHASE104G_VERSION = "phase104g-frontend-release-readiness-v1"

RELEASE_MANIFEST_JSON = "phase104_frontend_release_manifest.json"
RELEASE_MANIFEST_MD = "phase104_frontend_release_manifest.md"
RELEASE_REPORT_JSON = "release_readiness_report.json"
RELEASE_REPORT_MD = "release_readiness_report.md"
RELEASE_CHECKSUMS = "CHECKSUMS.sha256"
RELEASE_ZIP_NAME = "phase104_frontend_release_readiness_bundle.zip"
HANDOFF_DIR_NAME = "handoff_bundle"

REQUIRED_RELEASE_FILES: tuple[str, ...] = (
    f"{HANDOFF_DIR_NAME}/phase104_frontend_handoff_manifest.json",
    f"{HANDOFF_DIR_NAME}/phase104_frontend_handoff_manifest.md",
    f"{HANDOFF_DIR_NAME}/frontend_handoff_bundle_acceptance_report.json",
    f"{HANDOFF_DIR_NAME}/frontend_handoff_bundle_acceptance_report.md",
    f"{HANDOFF_DIR_NAME}/CHECKSUMS.sha256",
    f"{HANDOFF_DIR_NAME}/{HANDOFF_ZIP_NAME}",
    "release/FRONTEND_RELEASE_NOTES.md",
    "release/FRONTEND_CUTOVER_PLAN.md",
    "release/FRONTEND_ROLLBACK_PLAN.md",
    "release/FRONTEND_SIGNOFF_LEDGER.md",
    "release/FRONTEND_RELEASE_CHECKLIST.md",
    RELEASE_MANIFEST_JSON,
    RELEASE_MANIFEST_MD,
    RELEASE_REPORT_JSON,
    RELEASE_REPORT_MD,
    RELEASE_CHECKSUMS,
    RELEASE_ZIP_NAME,
)

FORBIDDEN_RELEASE_PATTERNS: tuple[str, ...] = (
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
    RELEASE_CHECKSUMS,
    RELEASE_ZIP_NAME,
    RELEASE_REPORT_JSON,
    RELEASE_REPORT_MD,
)


@dataclass(frozen=True)
class ReleaseReadinessCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "quality"
    severity: str = "critical"


@dataclass
class FrontendReleaseReadinessReport:
    passed: bool
    score: int
    version: str
    scenario: str
    output_dir: str
    checks: list[ReleaseReadinessCheck] = field(default_factory=list)
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
class FrontendReleaseReadinessBundle:
    passed: bool
    score: int
    version: str
    generated_at: str
    scenario: str
    api_base_url: str
    output_dir: str
    zip_path: str
    handoff_passed: bool
    handoff_checksum_ok: bool
    redaction_status: str
    release_gate_count: int
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


def _is_checksum_excluded(root: Path, path: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    return rel in CHECKSUM_EXCLUDED_ROOT_FILES


def _iter_release_files(root: Path, *, include_zip: bool = False, include_checksums: bool = False, include_reports: bool = False) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not include_zip and rel == RELEASE_ZIP_NAME:
            continue
        if not include_checksums and rel == RELEASE_CHECKSUMS:
            continue
        if not include_reports and rel in {RELEASE_REPORT_JSON, RELEASE_REPORT_MD}:
            continue
        files.append(path)
    return files


def _file_manifest(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {"size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        for path in _iter_release_files(root, include_zip=False, include_checksums=False, include_reports=False)
    }


def _read_all_text(root: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for path in _iter_release_files(root, include_zip=False, include_checksums=True, include_reports=True):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() not in {".md", ".json", ".ts", ".tsx", ".js", ".env", ".example", ".sha256", ""}:
            continue
        try:
            items.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return items


def scan_release_for_secret_leaks(root: str | Path) -> list[str]:
    base = Path(root)
    findings: list[str] = []
    for rel, text in _read_all_text(base):
        lowered = text.lower()
        for pattern in FORBIDDEN_RELEASE_PATTERNS:
            if pattern.lower() in lowered:
                findings.append(f"{rel}: contains forbidden pattern {pattern}")
    if (base / HANDOFF_DIR_NAME).exists():
        findings.extend(f"{HANDOFF_DIR_NAME}/{item}" for item in scan_frontend_handoff_for_secret_leaks(base / HANDOFF_DIR_NAME))
    return sorted(set(findings))


def write_release_checksums(root: str | Path) -> dict[str, str]:
    base = Path(root)
    checksums: dict[str, str] = {}
    lines: list[str] = []
    for path in _iter_release_files(base, include_zip=False, include_checksums=False, include_reports=False):
        if _is_checksum_excluded(base, path):
            continue
        rel = path.relative_to(base).as_posix()
        digest = _sha256_file(path)
        checksums[rel] = digest
        lines.append(f"{digest}  {rel}")
    _write_text(base / RELEASE_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_release_checksums(root: str | Path) -> tuple[bool, list[str]]:
    base = Path(root)
    checksum_path = base / RELEASE_CHECKSUMS
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


def create_release_zip(root: str | Path) -> Path:
    base = Path(root)
    zip_path = base / RELEASE_ZIP_NAME
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _iter_release_files(base, include_zip=False, include_checksums=True, include_reports=True):
            archive.write(path, path.relative_to(base).as_posix())
    return zip_path


def render_frontend_release_notes(bundle: FrontendReleaseReadinessBundle) -> str:
    return f"""# Phase104G 前端联调发布说明

## 发布结论

- 结果：{'通过' if bundle.passed else '未通过'}
- 得分：{bundle.score}/100
- 场景：`{bundle.scenario}`
- API Base URL：`{bundle.api_base_url}`
- 交接包验收：{bundle.handoff_passed}
- 交接包 checksum：{bundle.handoff_checksum_ok}
- 脱敏状态：{bundle.redaction_status}

## 本次发布范围

- Phase104A 可写本地 HTTP API
- Phase104B OpenAPI 合同与 TypeScript client
- Phase104C API 合同验收门禁
- Phase104D 前端联调工作区
- Phase104E 前端运行时 smoke
- Phase104F 前端交接包
- Phase104G 发布就绪账本、签收、回滚和 checksum 复验

## 发布原则

本包只用于前端联调、内部演示和试点前验证。任何真实客户环境接入前，必须重新执行客户环境诊断、凭证脱敏检查和运行时 smoke。
"""


def render_cutover_plan(api_base_url: str) -> str:
    return f"""# Phase104G 前端联调 Cutover Plan

## 目标

把前端工程从静态数据切换到本地 V1 API：`{api_base_url.rstrip('/')}`。

## 步骤

1. 后端启动本地 API：`python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8790`
2. 前端导入 `handoff_bundle/workspace/src/api` 与 `handoff_bundle/workspace/src/types`。
3. 前端配置 `VITE_QUALIBUG_API_BASE_URL`。
4. 页面按顺序联调：项目列表、驾驶舱、环境诊断、测试计划、实时地图、风险详情、ROI、成果战报。
5. 运行 `phase104_frontend_runtime_smoke` 生成 smoke 报告。
6. 前后端共同确认本目录 `release_readiness_report.md`。

## 成功标准

- API 合同验收通过。
- 前端运行时 smoke 通过。
- 交接包 checksum 通过。
- 未发现原始凭证或 traceback 泄露。
"""


def render_rollback_plan() -> str:
    return """# Phase104G 前端联调 Rollback Plan

## 触发条件

- 本地 API 返回 envelope 不符合 `{ success, data, error, meta }`。
- 前端 smoke 或合同验收失败。
- checksum 复验失败。
- 发现未脱敏凭证、客户数据或技术 traceback 泄露。

## 回滚动作

1. 停止使用可写 API 联调入口。
2. 回退到 Phase103U/Phase103V 的静态预览包进行演示。
3. 保留失败的 smoke 报告和 release_readiness_report，禁止覆盖。
4. 修复后重新生成 Phase104F 交接包，再重新执行 Phase104G 发布就绪账本。

## 不允许做的事

- 不允许手动修改验收报告后继续发布。
- 不允许跳过 checksum 或脱敏检查。
- 不允许把真实客户凭证写入示例文件。
"""


def render_signoff_ledger(bundle: FrontendReleaseReadinessBundle) -> str:
    rows = [
        ("Product Owner", "确认页面联调范围与演示价值", "待签收"),
        ("Backend Owner", "确认 Phase104A API 与合同一致", "待签收"),
        ("Frontend Owner", "确认 client、adapter、workspace 可接入", "待签收"),
        ("QA Owner", "确认合同验收、runtime smoke、checksum 通过", "待签收"),
        ("Security Reviewer", "确认未发现原始凭证或 traceback 泄露", "待签收"),
    ]
    lines = [
        "# Phase104G 前端联调签收账本",
        "",
        f"- 发布版本：`{bundle.version}`",
        f"- 发布结论：{'通过' if bundle.passed else '未通过'}",
        f"- 生成时间：`{bundle.generated_at}`",
        "",
        "| 角色 | 签收内容 | 状态 |",
        "|---|---|---|",
    ]
    lines.extend(f"| {role} | {scope} | {status} |" for role, scope, status in rows)
    lines.append("")
    return "\n".join(lines)


def render_release_checklist() -> str:
    checks = [
        "Phase104F 前端交接包存在",
        "Phase104F 交接包验收通过",
        "Phase104F checksum 复验通过",
        "Phase104C API 合同验收通过",
        "Phase104E 前端运行时 smoke 通过",
        "OpenAPI、TypeScript client、页面 adapter 存在",
        "Cutover plan 已阅读",
        "Rollback plan 已阅读",
        "签收账本已分配角色",
        "未发现原始凭证或 traceback 泄露",
    ]
    return "# Phase104G 前端联调发布检查清单\n\n" + "\n".join(f"- [ ] {item}" for item in checks) + "\n"


def _score_from_checks(checks: list[ReleaseReadinessCheck]) -> int:
    if not checks:
        return 0
    passed = sum(1 for check in checks if check.passed)
    return int(round((passed / len(checks)) * 100))


def _build_manifest_payload(bundle: FrontendReleaseReadinessBundle, root: Path) -> dict[str, Any]:
    return redact_value(
        {
            "version": PHASE104G_VERSION,
            "generated_at": bundle.generated_at,
            "scenario": bundle.scenario,
            "api_base_url": bundle.api_base_url,
            "passed": bundle.passed,
            "score": bundle.score,
            "handoff_passed": bundle.handoff_passed,
            "handoff_checksum_ok": bundle.handoff_checksum_ok,
            "redaction_status": bundle.redaction_status,
            "release_gate_count": bundle.release_gate_count,
            "file_count": bundle.file_count,
            "checksum_count": bundle.checksum_count,
            "required_files": list(REQUIRED_RELEASE_FILES),
            "files": _file_manifest(root),
            "versions": {
                "phase104a": PHASE104A_VERSION,
                "phase104f": PHASE104F_VERSION,
                "phase104g": PHASE104G_VERSION,
            },
            "artifact_summary": bundle.artifact_summary,
        }
    )


def render_release_manifest_markdown(bundle: FrontendReleaseReadinessBundle) -> str:
    lines = [
        "# Phase104G 前端联调发布就绪 Manifest",
        "",
        f"- 版本：`{bundle.version}`",
        f"- 生成时间：`{bundle.generated_at}`",
        f"- 场景：`{bundle.scenario}`",
        f"- 结论：{'通过' if bundle.passed else '未通过'}",
        f"- 得分：{bundle.score}/100",
        f"- 交接包验收：{bundle.handoff_passed}",
        f"- 交接包 checksum：{bundle.handoff_checksum_ok}",
        f"- 脱敏状态：{bundle.redaction_status}",
        f"- Zip：`{Path(bundle.zip_path).name}`",
        "",
        "## 必备文件",
        "",
    ]
    lines.extend(f"- `{rel}`" for rel in REQUIRED_RELEASE_FILES)
    lines.append("")
    return "\n".join(lines)


def render_readiness_report_markdown(report: FrontendReleaseReadinessReport) -> str:
    lines = [
        "# Phase104G 前端发布就绪验收报告",
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


def validate_frontend_release_readiness(output_dir: str | Path) -> FrontendReleaseReadinessReport:
    root = Path(output_dir)
    handoff_dir = root / HANDOFF_DIR_NAME
    checks: list[ReleaseReadinessCheck] = []

    missing = [rel for rel in REQUIRED_RELEASE_FILES if not (root / rel).exists()]
    checks.append(ReleaseReadinessCheck("required_files", not missing, "required files present" if not missing else "missing: " + ", ".join(missing[:12]), "release-manager"))

    handoff_report = validate_frontend_handoff_bundle(handoff_dir) if handoff_dir.exists() else None
    checks.append(
        ReleaseReadinessCheck(
            "handoff_bundle_validation",
            bool(handoff_report and handoff_report.passed and handoff_report.score >= 90),
            f"score={handoff_report.score if handoff_report else 0} passed={handoff_report.passed if handoff_report else False}",
            "frontend-owner",
        )
    )

    handoff_checksum_ok, handoff_checksum_findings = verify_frontend_handoff_checksums(handoff_dir) if handoff_dir.exists() else (False, ["handoff bundle missing"])
    checks.append(
        ReleaseReadinessCheck(
            "handoff_bundle_checksums",
            handoff_checksum_ok,
            "handoff checksums matched" if handoff_checksum_ok else "; ".join(handoff_checksum_findings[:8]),
            "qa-owner",
        )
    )

    handoff_manifest = _read_json(handoff_dir / "phase104_frontend_handoff_manifest.json")
    checks.append(
        ReleaseReadinessCheck(
            "handoff_runtime_gates",
            handoff_manifest.get("contract_acceptance_passed") is True and handoff_manifest.get("runtime_smoke_passed") is True,
            f"contract={handoff_manifest.get('contract_acceptance_passed')} smoke={handoff_manifest.get('runtime_smoke_passed')}",
            "qa-owner",
        )
    )

    manifest = _read_json(root / RELEASE_MANIFEST_JSON)
    checks.append(
        ReleaseReadinessCheck(
            "release_manifest",
            manifest.get("version") == PHASE104G_VERSION and manifest.get("redaction_status") == "safe",
            f"version={manifest.get('version')} redaction={manifest.get('redaction_status')}",
            "release-manager",
        )
    )

    signoff_text = (root / "release" / "FRONTEND_SIGNOFF_LEDGER.md").read_text(encoding="utf-8") if (root / "release" / "FRONTEND_SIGNOFF_LEDGER.md").exists() else ""
    required_roles = ("Product Owner", "Backend Owner", "Frontend Owner", "QA Owner", "Security Reviewer")
    missing_roles = [role for role in required_roles if role not in signoff_text]
    checks.append(
        ReleaseReadinessCheck(
            "signoff_roles",
            not missing_roles,
            "all signoff roles present" if not missing_roles else "missing roles: " + ", ".join(missing_roles),
            "release-manager",
        )
    )

    plan_files = ["FRONTEND_RELEASE_NOTES.md", "FRONTEND_CUTOVER_PLAN.md", "FRONTEND_ROLLBACK_PLAN.md", "FRONTEND_RELEASE_CHECKLIST.md"]
    missing_plans = [name for name in plan_files if not (root / "release" / name).exists()]
    checks.append(
        ReleaseReadinessCheck(
            "release_plans",
            not missing_plans,
            "release, cutover, rollback and checklist docs present" if not missing_plans else "missing: " + ", ".join(missing_plans),
            "product-owner",
        )
    )

    release_checksum_ok, release_checksum_findings = verify_release_checksums(root)
    checks.append(
        ReleaseReadinessCheck(
            "release_checksums",
            release_checksum_ok,
            "release checksums matched" if release_checksum_ok else "; ".join(release_checksum_findings[:8]),
            "qa-owner",
        )
    )

    zip_path = root / RELEASE_ZIP_NAME
    zip_ok = False
    zip_detail = "zip missing"
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
            required_in_zip = set(REQUIRED_RELEASE_FILES) - {RELEASE_ZIP_NAME}
            missing_in_zip = sorted(required_in_zip.difference(names))
            zip_ok = not missing_in_zip
            zip_detail = f"entries={len(names)}" if zip_ok else "missing in zip: " + ", ".join(missing_in_zip[:8])
        except zipfile.BadZipFile:
            zip_detail = "zip is not readable"
    checks.append(ReleaseReadinessCheck("release_zip", zip_ok, zip_detail, "release-manager"))

    leaks = scan_release_for_secret_leaks(root) if root.exists() else ["release directory missing"]
    checks.append(
        ReleaseReadinessCheck(
            "redaction",
            not leaks,
            "no forbidden raw credential or traceback patterns" if not leaks else "; ".join(leaks[:8]),
            "security-reviewer",
        )
    )

    score = _score_from_checks(checks)
    passed = all(check.passed or check.severity != "critical" for check in checks)
    scenario = str(manifest.get("scenario") or handoff_manifest.get("scenario") or "unknown")
    return FrontendReleaseReadinessReport(
        passed=passed,
        score=score,
        version=PHASE104G_VERSION,
        scenario=scenario,
        output_dir=str(root),
        checks=checks,
        artifacts={
            "handoff_score": handoff_report.score if handoff_report else 0,
            "handoff_zip": str(handoff_dir / HANDOFF_ZIP_NAME) if (handoff_dir / HANDOFF_ZIP_NAME).exists() else None,
            "release_zip": str(zip_path) if zip_path.exists() else None,
            "redaction_status": "safe" if not leaks else "failed",
            "file_count": len(_iter_release_files(root, include_zip=True, include_checksums=True, include_reports=True)) if root.exists() else 0,
        },
    )


def _prepare_handoff_bundle(root: Path, *, scenario: str, api_base_url: str, source_handoff_dir: str | Path | None, build_first: bool) -> tuple[Path, Any, bool, list[str]]:
    handoff_dir = root / HANDOFF_DIR_NAME
    if build_first:
        if handoff_dir.exists():
            shutil.rmtree(handoff_dir)
        bundle = build_frontend_handoff_bundle(output_dir=handoff_dir, scenario=scenario, api_base_url=api_base_url)
    else:
        if source_handoff_dir:
            source = Path(source_handoff_dir)
            if source.resolve() != handoff_dir.resolve():
                if handoff_dir.exists():
                    shutil.rmtree(handoff_dir)
                shutil.copytree(source, handoff_dir)
        bundle = None
    handoff_report = validate_frontend_handoff_bundle(handoff_dir)
    checksum_ok, checksum_findings = verify_frontend_handoff_checksums(handoff_dir)
    return handoff_dir, bundle or handoff_report, checksum_ok, checksum_findings


def build_frontend_release_readiness(
    *,
    output_dir: str | Path,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8790",
    handoff_dir: str | Path | None = None,
    build_first: bool = True,
) -> FrontendReleaseReadinessBundle:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    release_dir = root / "release"

    prepared_handoff_dir, handoff_result, handoff_checksum_ok, handoff_checksum_findings = _prepare_handoff_bundle(
        root,
        scenario=scenario,
        api_base_url=api_base_url,
        source_handoff_dir=handoff_dir,
        build_first=build_first,
    )
    handoff_report = validate_frontend_handoff_bundle(prepared_handoff_dir)
    _write_text(prepared_handoff_dir / "frontend_handoff_bundle_acceptance_report.json", _json_text(handoff_report.to_dict()))
    _write_text(prepared_handoff_dir / "frontend_handoff_bundle_acceptance_report.md", render_frontend_handoff_validation_markdown(handoff_report))
    redaction_findings = scan_release_for_secret_leaks(root)
    redaction_status = "safe" if not redaction_findings else "failed"
    base_passed = handoff_report.passed and handoff_checksum_ok and redaction_status == "safe"
    base_score = min(handoff_report.score, 100 if handoff_checksum_ok else 70, 100 if redaction_status == "safe" else 70)
    generated_at = _now()

    bundle_shell = FrontendReleaseReadinessBundle(
        passed=base_passed,
        score=base_score,
        version=PHASE104G_VERSION,
        generated_at=generated_at,
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(root / RELEASE_ZIP_NAME),
        handoff_passed=handoff_report.passed,
        handoff_checksum_ok=handoff_checksum_ok,
        redaction_status=redaction_status,
        release_gate_count=0,
        file_count=0,
        checksum_count=0,
        artifact_summary={
            "handoff_score": handoff_report.score,
            "handoff_checksum_findings": handoff_checksum_findings,
            "handoff_artifacts": handoff_report.artifacts,
            "handoff_result_type": type(handoff_result).__name__,
            "versions": {"phase104a": PHASE104A_VERSION, "phase104f": PHASE104F_VERSION, "phase104g": PHASE104G_VERSION},
        },
    )

    _write_text(release_dir / "FRONTEND_RELEASE_NOTES.md", render_frontend_release_notes(bundle_shell))
    _write_text(release_dir / "FRONTEND_CUTOVER_PLAN.md", render_cutover_plan(api_base_url))
    _write_text(release_dir / "FRONTEND_ROLLBACK_PLAN.md", render_rollback_plan())
    _write_text(release_dir / "FRONTEND_SIGNOFF_LEDGER.md", render_signoff_ledger(bundle_shell))
    _write_text(release_dir / "FRONTEND_RELEASE_CHECKLIST.md", render_release_checklist())

    # Re-scan after writing release docs.
    redaction_findings = scan_release_for_secret_leaks(root)
    redaction_status = "safe" if not redaction_findings else "failed"
    prelim_bundle = FrontendReleaseReadinessBundle(
        passed=base_passed and redaction_status == "safe",
        score=base_score if redaction_status == "safe" else min(base_score, 70),
        version=PHASE104G_VERSION,
        generated_at=generated_at,
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(root / RELEASE_ZIP_NAME),
        handoff_passed=handoff_report.passed,
        handoff_checksum_ok=handoff_checksum_ok,
        redaction_status=redaction_status,
        release_gate_count=0,
        file_count=0,
        checksum_count=0,
        artifact_summary={**bundle_shell.artifact_summary, "secret_leak_findings": redaction_findings},
    )
    _write_text(root / RELEASE_MANIFEST_JSON, _json_text(_build_manifest_payload(prelim_bundle, root)))
    _write_text(root / RELEASE_MANIFEST_MD, render_release_manifest_markdown(prelim_bundle))

    # Placeholder reports make the zip complete; reports are intentionally excluded from checksum lines.
    placeholder_report = FrontendReleaseReadinessReport(
        passed=prelim_bundle.passed,
        score=prelim_bundle.score,
        version=PHASE104G_VERSION,
        scenario=scenario,
        output_dir=str(root),
        checks=[ReleaseReadinessCheck("preliminary", prelim_bundle.passed, "preliminary release readiness calculated")],
        artifacts={"redaction_status": redaction_status},
    )
    _write_text(root / RELEASE_REPORT_JSON, _json_text(placeholder_report.to_dict()))
    _write_text(root / RELEASE_REPORT_MD, render_readiness_report_markdown(placeholder_report))

    checksums = write_release_checksums(root)
    zip_path = create_release_zip(root)
    final_report = validate_frontend_release_readiness(root)

    final_bundle = FrontendReleaseReadinessBundle(
        passed=prelim_bundle.passed and final_report.passed,
        score=min(prelim_bundle.score, final_report.score),
        version=PHASE104G_VERSION,
        generated_at=generated_at,
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(zip_path),
        handoff_passed=handoff_report.passed,
        handoff_checksum_ok=handoff_checksum_ok,
        redaction_status=redaction_status,
        release_gate_count=len(final_report.checks),
        file_count=len(_iter_release_files(root, include_zip=True, include_checksums=True, include_reports=True)),
        checksum_count=len(checksums),
        artifact_summary={**prelim_bundle.artifact_summary, "validation": final_report.to_dict()},
    )

    _write_text(release_dir / "FRONTEND_RELEASE_NOTES.md", render_frontend_release_notes(final_bundle))
    _write_text(release_dir / "FRONTEND_SIGNOFF_LEDGER.md", render_signoff_ledger(final_bundle))
    _write_text(root / RELEASE_MANIFEST_JSON, _json_text(_build_manifest_payload(final_bundle, root)))
    _write_text(root / RELEASE_MANIFEST_MD, render_release_manifest_markdown(final_bundle))
    checksums = write_release_checksums(root)
    zip_path = create_release_zip(root)
    final_report = validate_frontend_release_readiness(root)
    _write_text(root / RELEASE_REPORT_JSON, _json_text(final_report.to_dict()))
    _write_text(root / RELEASE_REPORT_MD, render_readiness_report_markdown(final_report))
    zip_path = create_release_zip(root)

    return FrontendReleaseReadinessBundle(
        passed=final_bundle.passed and final_report.passed,
        score=min(final_bundle.score, final_report.score),
        version=PHASE104G_VERSION,
        generated_at=generated_at,
        scenario=scenario,
        api_base_url=api_base_url.rstrip("/"),
        output_dir=str(root),
        zip_path=str(zip_path),
        handoff_passed=handoff_report.passed,
        handoff_checksum_ok=handoff_checksum_ok,
        redaction_status=redaction_status,
        release_gate_count=len(final_report.checks),
        file_count=len(_iter_release_files(root, include_zip=True, include_checksums=True, include_reports=True)),
        checksum_count=len(checksums),
        artifact_summary={**prelim_bundle.artifact_summary, "validation": final_report.to_dict()},
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate a Phase104G frontend release readiness bundle.")
    parser.add_argument("--output-dir", default="outputs/phase104_frontend_release_readiness", help="Directory for generated release readiness bundle.")
    parser.add_argument("--scenario", choices=["manufacturing", "ecommerce", "saas"], default="manufacturing", help="Runtime smoke seed scenario.")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8790", help="API base URL written into cutover docs.")
    parser.add_argument("--handoff-dir", default=None, help="Existing Phase104F handoff bundle directory to copy and release.")
    parser.add_argument("--no-build-handoff", action="store_true", help="Do not rebuild the Phase104F handoff bundle; use --handoff-dir or output/handoff_bundle.")
    parser.add_argument("--validate-only", action="store_true", help="Validate an existing Phase104G release readiness bundle.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    root = Path(args.output_dir)
    if args.validate_only:
        report = validate_frontend_release_readiness(root)
        _write_text(root / RELEASE_REPORT_JSON, _json_text(report.to_dict()))
        _write_text(root / RELEASE_REPORT_MD, render_readiness_report_markdown(report))
        print(_json_text(report.to_dict()))
        return 0 if report.passed else 2
    bundle = build_frontend_release_readiness(
        output_dir=root,
        scenario=args.scenario,
        api_base_url=args.api_base_url,
        handoff_dir=args.handoff_dir,
        build_first=not args.no_build_handoff,
    )
    print(_json_text(bundle.to_dict()))
    return 0 if bundle.passed else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PHASE104G_VERSION",
    "FrontendReleaseReadinessBundle",
    "FrontendReleaseReadinessReport",
    "build_frontend_release_readiness",
    "validate_frontend_release_readiness",
    "verify_release_checksums",
    "scan_release_for_secret_leaks",
    "main",
]
