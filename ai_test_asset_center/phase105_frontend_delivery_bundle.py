from __future__ import annotations

"""Phase105L: frontend experience delivery bundle for QualiBug.

Phase105J assembled the end-to-end static frontend hub and Phase105K added an
interaction acceptance gate.  Phase105L packages both into a customer-demo-ready
frontend delivery bundle with handoff docs, checksums, validation reports and a
zip archive.  The goal is to make the frontend display layer easy to hand to a
sales engineer, frontend developer, or pilot customer without exposing raw
secrets.
"""

import argparse
import hashlib
import json
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase105_frontend_experience_hub_v2 import (
    FRONTEND_HUB_V2_MANIFEST,
    FRONTEND_HUB_V2_ZIP,
    PAGE_SPECS_V2,
    build_frontend_experience_hub_v2,
)
from ai_test_asset_center.phase105_frontend_interaction_acceptance import (
    FRONTEND_INTERACTION_ACCEPTANCE_JSON,
    FRONTEND_INTERACTION_ACCEPTANCE_MD,
    run_frontend_interaction_acceptance,
)

PHASE105L_VERSION = "phase105l-frontend-delivery-bundle-v1"

FRONTEND_DELIVERY_MANIFEST_JSON = "phase105_frontend_delivery_manifest.json"
FRONTEND_DELIVERY_MANIFEST_MD = "phase105_frontend_delivery_manifest.md"
FRONTEND_DELIVERY_REPORT_JSON = "frontend_delivery_acceptance_report.json"
FRONTEND_DELIVERY_REPORT_MD = "frontend_delivery_acceptance_report.md"
FRONTEND_DELIVERY_CHECKSUMS = "CHECKSUMS.sha256"
FRONTEND_DELIVERY_ZIP = "phase105_frontend_delivery_bundle.zip"

HUB_DIR = "hub_v2"
INTERACTION_ACCEPTANCE_DIR = "interaction_acceptance"
HANDOFF_DIR = "handoff"

REQUIRED_HANDOFF_FILES: tuple[str, ...] = (
    f"{HANDOFF_DIR}/README_FRONTEND_DELIVERY.md",
    f"{HANDOFF_DIR}/DEMO_RUNBOOK.md",
    f"{HANDOFF_DIR}/CUSTOMER_WALKTHROUGH_SCRIPT.md",
    f"{HANDOFF_DIR}/FRONTEND_DELIVERY_CHECKLIST.md",
)

REQUIRED_DELIVERY_FILES: tuple[str, ...] = (
    f"{HUB_DIR}/index.html",
    f"{HUB_DIR}/{FRONTEND_HUB_V2_MANIFEST}",
    f"{HUB_DIR}/pages/test_execution/test_execution.html",
    f"{HUB_DIR}/pages/risk_evidence/risk_evidence.html",
    f"{HUB_DIR}/pages/report_roi/report_roi.html",
    f"{INTERACTION_ACCEPTANCE_DIR}/{FRONTEND_INTERACTION_ACCEPTANCE_JSON}",
    f"{INTERACTION_ACCEPTANCE_DIR}/{FRONTEND_INTERACTION_ACCEPTANCE_MD}",
    *REQUIRED_HANDOFF_FILES,
    FRONTEND_DELIVERY_MANIFEST_JSON,
    FRONTEND_DELIVERY_MANIFEST_MD,
    FRONTEND_DELIVERY_CHECKSUMS,
)

FORBIDDEN_FRONTEND_DELIVERY_PATTERNS: tuple[str, ...] = (
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

DELIVERY_KEYWORDS: tuple[str, ...] = (
    "客户资料导入",
    "环境诊断",
    "业务流程地图",
    "AI 测试计划",
    "实时测试执行",
    "风险与证据链",
    "领导层报告",
    "ROI",
    "默认脱敏",
)


@dataclass(frozen=True)
class FrontendDeliveryCheck:
    key: str
    passed: bool
    detail: str
    owner: str = "frontend"
    severity: str = "critical"


@dataclass
class FrontendDeliveryAcceptanceReport:
    passed: bool
    score: int
    version: str
    bundle_dir: str
    output_dir: str
    checks: list[FrontendDeliveryCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return redact_value(
            {
                "passed": self.passed,
                "score": self.score,
                "version": self.version,
                "bundle_dir": self.bundle_dir,
                "output_dir": self.output_dir,
                "checks": [asdict(check) for check in self.checks],
                "artifacts": self.artifacts,
            }
        )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_dump(data: Any) -> str:
    return json.dumps(redact_value(data), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _scan_files(root: Path, patterns: Sequence[str]) -> list[str]:
    leaks: list[str] = []
    if not root.exists():
        return [f"missing_output_dir:{root}"]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".md", ".txt", ".yml", ".yaml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            continue
        for pattern in patterns:
            if pattern in text:
                leaks.append(f"{path.relative_to(root)} contains forbidden pattern {pattern}")
    return leaks


def scan_frontend_delivery_for_secret_leaks(bundle_dir: str | Path) -> list[str]:
    return _scan_files(Path(bundle_dir), FORBIDDEN_FRONTEND_DELIVERY_PATTERNS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_checksum_files(root: Path) -> list[Path]:
    excluded = {FRONTEND_DELIVERY_CHECKSUMS, FRONTEND_DELIVERY_ZIP}
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in excluded and not path.name.endswith(".pyc")
    ]


def write_checksums(bundle_dir: str | Path) -> dict[str, str]:
    root = Path(bundle_dir)
    checksums = {path.relative_to(root).as_posix(): _sha256(path) for path in _iter_checksum_files(root)}
    lines = [f"{digest}  {name}" for name, digest in sorted(checksums.items())]
    _write_text(root / FRONTEND_DELIVERY_CHECKSUMS, "\n".join(lines) + "\n")
    return checksums


def verify_checksums(bundle_dir: str | Path) -> list[str]:
    root = Path(bundle_dir)
    checksums_path = root / FRONTEND_DELIVERY_CHECKSUMS
    if not checksums_path.exists():
        return ["missing CHECKSUMS.sha256"]
    problems: list[str] = []
    for line in checksums_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            expected, rel = line.split("  ", 1)
        except ValueError:
            problems.append(f"invalid checksum line: {line}")
            continue
        path = root / rel
        if not path.exists():
            problems.append(f"missing file: {rel}")
            continue
        actual = _sha256(path)
        if actual != expected:
            problems.append(f"checksum mismatch: {rel}")
    return problems


def _zip_bundle(bundle: Path) -> str:
    zip_path = bundle / FRONTEND_DELIVERY_ZIP
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, path.relative_to(bundle).as_posix())
    return zip_path.name


def _render_handoff_readme(manifest: Mapping[str, Any]) -> str:
    page_rows = "\n".join(
        f"| {page.get('journey_stage', '—')} | {page.get('label', '—')} | {page.get('url', '—')} | {page.get('business_value', '—')} |"
        for page in manifest.get("pages", [])
        if isinstance(page, Mapping)
    )
    return f"""# Phase105L 前端显示层交付包

这个交付包把 Phase105J 前端体验 Hub V2 和 Phase105K 交互验收报告打包到一起，便于客户演示、售前讲解、前端开发交接和后续复验。

## 交付内容

- `hub_v2/index.html`：完整前端体验入口。
- `hub_v2/pages/...`：8 个核心静态页面。
- `interaction_acceptance/frontend_interaction_acceptance_report.md`：前端交互验收报告。
- `handoff/DEMO_RUNBOOK.md`：演示运行手册。
- `handoff/CUSTOMER_WALKTHROUGH_SCRIPT.md`：客户讲解脚本。
- `handoff/FRONTEND_DELIVERY_CHECKLIST.md`：交付检查清单。
- `CHECKSUMS.sha256`：交付文件完整性校验。
- `phase105_frontend_delivery_bundle.zip`：可交付归档。

## 页面旅程

| 阶段 | 页面 | 路径 | 业务价值 |
|---|---|---|---|
{page_rows}

## 安全说明

所有页面和数据默认脱敏，不展示原始 token、cookie、session、password、client_secret 或 Python traceback。环境、认证和证据只展示状态、摘要和已脱敏信息。
"""


def _render_demo_runbook() -> str:
    return """# Frontend Demo Runbook

## 一键生成交付包

```powershell
python -m ai_test_asset_center.phase105_frontend_delivery_bundle --output-dir .\\outputs\\phase105_frontend_delivery_bundle
Start-Process .\\outputs\\phase105_frontend_delivery_bundle\\hub_v2\\index.html
```

## 演示顺序

1. 打开 `hub_v2/index.html`，说明这是 QualiBug AI 企业质量指挥中心前端体验入口。
2. 进入“客户资料导入”，解释企业资料如何变成项目草案、业务模型、环境补料清单。
3. 进入“环境诊断中心”，解释为什么客户环境是否可测是第一关。
4. 进入“业务流程地图”，展示 AI 沿业务链路理解风险，而不是随机扫接口。
5. 进入“AI 测试计划 / 实时测试执行”，说明可执行探针、阻断探针、实时事件和证据回流。
6. 进入“风险与证据链”，证明风险真实、可复现、可修复、可复验。
7. 进入“领导层报告 / ROI”，用上线建议、节省工时和业务影响推动决策。
8. 打开 `interaction_acceptance/frontend_interaction_acceptance_report.md`，说明前端交互已通过验收门禁。

## 演示口径

这不是单纯 Bug 列表，而是企业上线前的质量风险、证据链、修复闭环、上线建议和 ROI 价值证明。
"""


def _render_walkthrough_script() -> str:
    return """# Customer Walkthrough Script

## 开场

我们先不看底层测试脚本，而是站在企业上线决策角度看：系统现在能不能测、能不能上线、风险在哪里、证据是否可信、修复后怎么复验，以及这次 AI 测试给企业节省了多少成本。

## 页面讲解词

- 客户资料导入：把 PRD、接口、流程、角色、安全边界转为 AI 可理解的测试项目。
- 环境诊断：URL、DNS、HTTP、认证、会话、API Smoke 有任何阻断都会明确告诉客户下一步补什么。
- 业务流程地图：用业务节点展示覆盖状态、风险爆点和证据回流，避免客户觉得 AI 在乱测。
- AI 测试计划 / 实时执行：展示准备测什么、正在测什么、哪些探针被环境阻断、哪些证据已经生成。
- 风险与证据链：每个风险都有业务影响、复现步骤、请求/响应摘要、修复建议和关闭条件。
- 领导层报告 / ROI：输出上线建议、主要风险、节省工时、潜在影响和下一步动作。

## 结束

这套前端显示层的目标，是让领导看得懂、技术信得过、客户知道下一步、销售能讲价值。
"""


def _render_delivery_checklist() -> str:
    checks = [
        "Hub V2 总入口可以打开",
        "8 个核心页面均存在且可跳转",
        "AI 测试计划 / 实时测试执行页已接入",
        "Phase105K 交互验收报告通过",
        "交付包包含演示脚本与客户讲解脚本",
        "CHECKSUMS.sha256 可复验文件完整性",
        "zip 归档包含 hub_v2、interaction_acceptance 和 handoff 文档",
        "未发现 token、cookie、session、client_secret、traceback 原文泄露",
    ]
    return "# Frontend Delivery Checklist\n\n" + "\n".join(f"- [ ] {item}" for item in checks) + "\n"


def _render_manifest_markdown(manifest: Mapping[str, Any]) -> str:
    rows = "\n".join(
        f"| {item} | {'存在' if (Path(str(manifest.get('bundle_dir', '.'))) / item).exists() else '待确认'} |"
        for item in REQUIRED_DELIVERY_FILES
    )
    return f"""# Phase105L 前端显示层交付包 Manifest

- 版本：{manifest.get('version')}
- 场景：{manifest.get('scenario')}
- 生成时间：{manifest.get('generated_at')}
- 交付状态：{'通过' if manifest.get('passed') else '待处理'}
- 交互验收分数：{manifest.get('interaction_acceptance_score')}
- 脱敏状态：{manifest.get('redaction_status')}

## 关键文件

| 文件 | 状态 |
|---|---|
{rows}
"""


def build_frontend_delivery_bundle(
    output_dir: str | Path,
    *,
    scenario: str = "manufacturing",
    api_base_url: str = "http://127.0.0.1:8088",
    create_zip: bool = True,
) -> dict[str, Any]:
    bundle = Path(output_dir)
    bundle.mkdir(parents=True, exist_ok=True)
    hub = bundle / HUB_DIR
    acceptance = bundle / INTERACTION_ACCEPTANCE_DIR
    handoff = bundle / HANDOFF_DIR

    hub_manifest = build_frontend_experience_hub_v2(hub, scenario=scenario, api_base_url=api_base_url, create_zip=True)
    interaction_result = run_frontend_interaction_acceptance(hub_dir=hub, output_dir=acceptance, build_first=False)
    interaction_report = interaction_result.get("acceptance", {}) if isinstance(interaction_result, Mapping) else {}

    manifest: dict[str, Any] = redact_value(
        {
            "version": PHASE105L_VERSION,
            "generated_at": _now(),
            "scenario": scenario,
            "api_base_url": api_base_url.rstrip("/"),
            "bundle_dir": str(bundle),
            "hub_dir": str(hub),
            "interaction_acceptance_dir": str(acceptance),
            "handoff_dir": str(handoff),
            "entrypoint": f"{HUB_DIR}/index.html",
            "page_count": len(PAGE_SPECS_V2),
            "pages": hub_manifest.get("pages", []),
            "interaction_acceptance_passed": bool(interaction_report.get("passed")),
            "interaction_acceptance_score": int(interaction_report.get("score", 0) or 0),
            "required_files": list(REQUIRED_DELIVERY_FILES),
            "redaction_status": "safe" if not scan_frontend_delivery_for_secret_leaks(bundle) else "leak_detected",
            "passed": bool(interaction_report.get("passed")) and not scan_frontend_delivery_for_secret_leaks(bundle),
        }
    )

    _write_text(handoff / "README_FRONTEND_DELIVERY.md", _render_handoff_readme(manifest))
    _write_text(handoff / "DEMO_RUNBOOK.md", _render_demo_runbook())
    _write_text(handoff / "CUSTOMER_WALKTHROUGH_SCRIPT.md", _render_walkthrough_script())
    _write_text(handoff / "FRONTEND_DELIVERY_CHECKLIST.md", _render_delivery_checklist())
    _write_text(bundle / FRONTEND_DELIVERY_MANIFEST_JSON, _json_dump(manifest))
    _write_text(bundle / FRONTEND_DELIVERY_MANIFEST_MD, _render_manifest_markdown(manifest))
    write_checksums(bundle)
    if create_zip:
        _zip_bundle(bundle)

    report = validate_frontend_delivery_bundle(bundle, output_dir=bundle)
    return _read_json(bundle / FRONTEND_DELIVERY_MANIFEST_JSON) | {"acceptance": report.to_dict()}


def validate_frontend_delivery_bundle(
    bundle_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    min_score: int = 90,
    require_zip: bool = True,
) -> FrontendDeliveryAcceptanceReport:
    bundle = Path(bundle_dir)
    output = Path(output_dir) if output_dir is not None else bundle
    output.mkdir(parents=True, exist_ok=True)
    checks: list[FrontendDeliveryCheck] = []

    def add(key: str, passed: bool, detail: str, severity: str = "critical") -> None:
        checks.append(FrontendDeliveryCheck(key=key, passed=passed, detail=detail, severity=severity))

    manifest = _read_json(bundle / FRONTEND_DELIVERY_MANIFEST_JSON)
    add("delivery_manifest", bool(manifest), "交付 manifest 可读取。" if manifest else "缺少或无法读取交付 manifest。")

    missing_required = [rel for rel in REQUIRED_DELIVERY_FILES if not (bundle / rel).exists()]
    add("required_files", not missing_required, "关键交付文件完整。" if not missing_required else "缺少关键文件：" + ", ".join(missing_required))

    hub_manifest = _read_json(bundle / HUB_DIR / FRONTEND_HUB_V2_MANIFEST)
    pages = hub_manifest.get("pages") if isinstance(hub_manifest.get("pages"), list) else []
    add("hub_v2_pages", len(pages) == len(PAGE_SPECS_V2), f"Hub V2 页面数 {len(pages)}/{len(PAGE_SPECS_V2)}。")

    missing_pages = [f"{HUB_DIR}/{spec.relative_dir}/{spec.entrypoint}" for spec in PAGE_SPECS_V2 if not (bundle / HUB_DIR / spec.relative_dir / spec.entrypoint).exists()]
    add("hub_page_files", not missing_pages, "8 个核心页面文件完整。" if not missing_pages else "缺少页面：" + ", ".join(missing_pages))

    interaction_report = _read_json(bundle / INTERACTION_ACCEPTANCE_DIR / FRONTEND_INTERACTION_ACCEPTANCE_JSON)
    interaction_score = int(interaction_report.get("score", 0) or 0)
    interaction_passed = bool(interaction_report.get("passed")) and interaction_score >= min_score
    add("interaction_acceptance", interaction_passed, f"Phase105K 交互验收 score={interaction_score}。" if interaction_passed else f"Phase105K 交互验收未通过或分数不足：{interaction_score}。")

    handoff_text = "\n".join((bundle / rel).read_text(encoding="utf-8", errors="ignore") for rel in REQUIRED_HANDOFF_FILES if (bundle / rel).exists())
    missing_keywords = [keyword for keyword in DELIVERY_KEYWORDS if keyword not in handoff_text]
    add("handoff_copy", not missing_keywords, "交付文档覆盖客户旅程、ROI 和默认脱敏文案。" if not missing_keywords else "交付文档缺少关键词：" + ", ".join(missing_keywords), severity="major")

    checksum_problems = verify_checksums(bundle)
    add("checksums", not checksum_problems, "CHECKSUMS.sha256 复验通过。" if not checksum_problems else "checksum 问题：" + "; ".join(checksum_problems))

    leaks = scan_frontend_delivery_for_secret_leaks(bundle)
    add("redaction_guard", not leaks, "未发现原始 token/cookie/session/client_secret/traceback 泄露。" if not leaks else "发现疑似泄露：" + "; ".join(leaks))

    zip_path = bundle / FRONTEND_DELIVERY_ZIP
    if require_zip:
        if not zip_path.exists():
            add("zip_archive", False, "缺少前端交付 zip 归档。", severity="major")
        else:
            try:
                with zipfile.ZipFile(zip_path) as archive:
                    names = set(archive.namelist())
                required_zip = {
                    f"{HUB_DIR}/index.html",
                    f"{HUB_DIR}/pages/test_execution/test_execution.html",
                    f"{INTERACTION_ACCEPTANCE_DIR}/{FRONTEND_INTERACTION_ACCEPTANCE_MD}",
                    f"{HANDOFF_DIR}/DEMO_RUNBOOK.md",
                    FRONTEND_DELIVERY_CHECKSUMS,
                }
                missing_zip = sorted(required_zip - names)
                add("zip_archive", not missing_zip, "zip 归档包含关键演示与验收文件。" if not missing_zip else "zip 缺少：" + ", ".join(missing_zip), severity="major")
            except zipfile.BadZipFile:
                add("zip_archive", False, "前端交付 zip 归档不可读。", severity="major")
    else:
        add("zip_archive", True, "本次验收未要求 zip 归档。", severity="minor")

    raw_score = round(sum(1 for check in checks if check.passed) / len(checks) * 100) if checks else 0
    passed = raw_score >= min_score and all(check.passed for check in checks if check.severity == "critical")
    report = FrontendDeliveryAcceptanceReport(
        passed=passed,
        score=raw_score,
        version=PHASE105L_VERSION,
        bundle_dir=str(bundle),
        output_dir=str(output),
        checks=checks,
        artifacts={
            "entrypoint": str(bundle / HUB_DIR / "index.html"),
            "manifest": str(bundle / FRONTEND_DELIVERY_MANIFEST_JSON),
            "interaction_acceptance": str(bundle / INTERACTION_ACCEPTANCE_DIR / FRONTEND_INTERACTION_ACCEPTANCE_MD),
            "zip": str(bundle / FRONTEND_DELIVERY_ZIP),
            "checksums": str(bundle / FRONTEND_DELIVERY_CHECKSUMS),
        },
    )
    write_frontend_delivery_acceptance_report(output, report)
    return report


def render_frontend_delivery_acceptance_markdown(report: FrontendDeliveryAcceptanceReport) -> str:
    rows = "\n".join(
        f"| {check.key} | {'PASS' if check.passed else 'FAIL'} | {check.severity} | {check.detail} |"
        for check in report.checks
    )
    return f"""# Phase105L 前端显示层交付包验收报告

- 验收状态：{'通过' if report.passed else '未通过'}
- 验收分数：{report.score}
- 版本：{report.version}
- 交付目录：`{report.bundle_dir}`

## 验收项

| 检查项 | 结果 | 严重级别 | 详情 |
|---|---|---|---|
{rows}

## 验收结论

Phase105L 用于确认前端显示层已经从“能打开页面”推进到“可演示、可交付、可复验”的客户交付包。它绑定 Hub V2、交互验收报告、演示脚本、客户讲解脚本、交付清单、校验和与 zip 归档。
"""


def write_frontend_delivery_acceptance_report(output_dir: str | Path, report: FrontendDeliveryAcceptanceReport) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_text(output / FRONTEND_DELIVERY_REPORT_JSON, _json_dump(report.to_dict()))
    _write_text(output / FRONTEND_DELIVERY_REPORT_MD, render_frontend_delivery_acceptance_markdown(report))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or validate Phase105 frontend experience delivery bundle.")
    parser.add_argument("--output-dir", default="outputs/phase105_frontend_delivery_bundle")
    parser.add_argument("--bundle-dir", default=None)
    parser.add_argument("--scenario", default="manufacturing")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--min-score", type=int, default=90)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target = Path(args.bundle_dir or args.output_dir)
    if args.validate_only:
        report = validate_frontend_delivery_bundle(target, output_dir=args.output_dir, min_score=args.min_score, require_zip=not args.no_zip)
        print(_json_dump(report.to_dict()))
        return 0 if report.passed else 1
    result = build_frontend_delivery_bundle(target, scenario=args.scenario, api_base_url=args.api_base_url, create_zip=not args.no_zip)
    print(_json_dump(result))
    acceptance = result.get("acceptance", {}) if isinstance(result, Mapping) else {}
    return 0 if acceptance.get("passed") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
