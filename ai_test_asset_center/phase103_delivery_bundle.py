from __future__ import annotations

"""Phase103X: customer-safe demo delivery bundle packager.

Phase103R-W built the Enterprise Command Center foundation, API facade, seed
runner, static UI, local preview server, and acceptance gate.  This module adds
one final field-delivery step: package a complete demo/customer-handoff bundle
that sales, implementation, QA, and product teams can send or run locally.

The bundle is intentionally dependency-free and customer-safe:

* it includes static preview pages for each scenario,
* it includes page-ready JSON seed data,
* it includes acceptance reports proving pages/API/redaction gates passed,
* it includes sales/demo scripts and customer handoff checklists,
* it writes a zip archive for easy sharing,
* it never writes token/cookie/password/session/client_secret raw values.
"""

import argparse
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_demo_runner import DEMO_SCENARIOS, export_demo_bundle, seed_demo_project
from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase103_preview_acceptance import validate_preview_site
from ai_test_asset_center.phase103_preview_server import Phase103PreviewSite

PHASE103X_VERSION = "phase103x-demo-delivery-bundle-v1"
DEFAULT_SCENARIOS: tuple[str, ...] = ("manufacturing", "ecommerce", "saas")
SECRET_GUARD_PATTERNS: tuple[str, ...] = (
    "raw-manufacturing-token",
    "raw-ecommerce-token",
    "raw-saas-token",
    "DemoPasswordShouldBeRedacted",
    "SESSION=raw",
    "raw-client-secret",
    "ecommerce-client-secret",
    "saas-client-secret",
    "access_token=",
    "refresh_token=",
)


@dataclass(frozen=True)
class DeliveryScenarioManifest:
    """Manifest entry for one packaged demo scenario."""

    scenario: str
    project_id: str
    display_name: str
    site_dir: str
    data_dir: str
    acceptance_report: str
    acceptance_passed: bool
    acceptance_score: int
    entrypoint: str
    api_manifest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "project_id": self.project_id,
            "display_name": self.display_name,
            "site_dir": self.site_dir,
            "data_dir": self.data_dir,
            "acceptance_report": self.acceptance_report,
            "acceptance_passed": self.acceptance_passed,
            "acceptance_score": self.acceptance_score,
            "entrypoint": self.entrypoint,
            "api_manifest": self.api_manifest,
        }


@dataclass
class DeliveryBundleManifest:
    """Top-level manifest for a Phase103 delivery bundle."""

    version: str = PHASE103X_VERSION
    bundle_name: str = "QualiBug_Phase103_Enterprise_Command_Center_Delivery_Bundle"
    scenarios: list[DeliveryScenarioManifest] = field(default_factory=list)
    commercial_files: list[str] = field(default_factory=list)
    safety_files: list[str] = field(default_factory=list)
    zip_path: str | None = None
    redaction_status: str = "safe"

    @property
    def passed(self) -> bool:
        return bool(self.scenarios) and all(item.acceptance_passed for item in self.scenarios) and self.redaction_status == "safe"

    @property
    def average_acceptance_score(self) -> int:
        if not self.scenarios:
            return 0
        return int(round(sum(item.acceptance_score for item in self.scenarios) / len(self.scenarios)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "bundle_name": self.bundle_name,
            "passed": self.passed,
            "average_acceptance_score": self.average_acceptance_score,
            "scenario_count": len(self.scenarios),
            "scenarios": [item.to_dict() for item in self.scenarios],
            "commercial_files": list(self.commercial_files),
            "safety_files": list(self.safety_files),
            "zip_path": self.zip_path,
            "redaction_status": self.redaction_status,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Phase103X 企业质量指挥中心交付演示包",
            "",
            f"- 版本：{self.version}",
            f"- 结论：{'通过' if self.passed else '未通过'}",
            f"- 场景数：{len(self.scenarios)}",
            f"- 平均验收分：{self.average_acceptance_score}/100",
            f"- 脱敏状态：{self.redaction_status}",
            "",
            "## 场景",
            "",
            "| 场景 | 项目 | 验收 | 分数 | 入口 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in self.scenarios:
            lines.append(
                f"| {item.display_name} | {item.project_id} | {'通过' if item.acceptance_passed else '未通过'} | {item.acceptance_score} | {item.entrypoint} |"
            )
        lines.extend(
            [
                "",
                "## 使用方式",
                "",
                "1. 解压交付包。",
                "2. 进入 `scenarios/<scenario>/site`。",
                "3. 直接打开 `index.html` 查看静态产品原型。",
                "4. 查看 `scenarios/<scenario>/acceptance_report.md` 确认证据链、API、报告和脱敏门禁。",
                "5. 查看 `commercial/` 目录中的演示话术、一页纸和客户交接清单。",
                "",
                "## 安全说明",
                "",
                "本交付包由统一脱敏路径生成，不包含 token、cookie、password、session、client_secret 原值或客户敏感业务数据原文。",
                "",
            ]
        )
        return "\n".join(lines)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_value(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _copy_acceptance(report: Any, scenario_root: Path) -> tuple[str, str]:
    json_path = scenario_root / "acceptance_report.json"
    md_path = scenario_root / "acceptance_report.md"
    _write_json(json_path, report.to_dict())
    md_path.write_text(report.to_markdown(), encoding="utf-8")
    return str(json_path), str(md_path)


def _render_one_pager(manifest: DeliveryBundleManifest) -> str:
    return "\n".join(
        [
            "# QualiBug AI 企业质量指挥中心 - 商业化一页纸",
            "",
            "## 一句话定位",
            "",
            "面向企业软件上线前、交付前和持续迭代过程的 AI 质量风险识别、证据生成、修复闭环与上线决策平台。",
            "",
            "## 核心价值",
            "",
            "- 看清风险：用质量驾驶舱和业务链路地图展示系统风险态势。",
            "- 证明风险：用脱敏证据链、复现步骤和修复建议降低误报担忧。",
            "- 支持上线：用上线建议和领导层成果战报辅助上线评审。",
            "- 量化价值：用等价测试点、节省工时、风险暴露区间证明采购价值。",
            "",
            "## 本交付包覆盖",
            "",
            f"- 演示场景：{len(manifest.scenarios)} 个",
            f"- 平均验收分：{manifest.average_acceptance_score}/100",
            "- 页面：质量驾驶舱、环境适配、测试计划、实时地图、风险发现、成果战报、ROI。",
            "- 安全：默认脱敏 token/cookie/password/session/client_secret。",
            "",
            "## 推荐销售演示路径",
            "",
            "质量驾驶舱 → 实时测试地图 → 高危风险卡片 → 证据链详情 → 成果战报 → ROI 价值分析 → 环境适配中心。",
            "",
        ]
    )


def _render_demo_script(manifest: DeliveryBundleManifest) -> str:
    scenario_lines = []
    for item in manifest.scenarios:
        scenario_lines.extend(
            [
                f"## {item.display_name}",
                "",
                "1. 打开 `site/index.html`。",
                "2. 先讲质量健康分、上线建议和阻断风险。",
                "3. 进入实时地图，说明 AI 正在沿业务链路发现风险。",
                "4. 点击风险，强调业务影响、证据完整度和复现稳定性。",
                "5. 打开成果战报，说明客户可直接用于上线评审和内部汇报。",
                "",
            ]
        )
    return "\n".join(
        [
            "# Phase103X 售前演示脚本",
            "",
            "## 开场话术",
            "",
            "今天展示的不是传统测试报告，而是企业质量指挥中心：它能把 AI 测试结果转成业务风险、上线建议、证据链和价值指标。",
            "",
            "## 标准演示路径",
            "",
            "1. 质量驾驶舱：30 秒看懂当前能不能上线。",
            "2. 实时 AI 测试地图：看见 AI 穿透业务链路。",
            "3. AI 风险发现：把 Bug 翻译成业务风险。",
            "4. 证据链详情：证明问题真实、可复现、可修复。",
            "5. 成果战报：给领导汇报上线建议和下一步行动。",
            "6. ROI 价值分析：用保守口径展示节省工时和潜在影响区间。",
            "",
            *scenario_lines,
            "## 收尾话术",
            "",
            "这套能力的价值不是单次发现 Bug，而是持续帮助企业看清风险、证明风险、推动修复、支持上线和量化价值。",
            "",
        ]
    )


def _render_customer_handoff_checklist() -> str:
    return "\n".join(
        [
            "# 客户交接与试点准备清单",
            "",
            "## 客户需要准备",
            "",
            "- 测试环境 base_url。",
            "- 测试账号：normal_user、admin_user、业务关键角色账号。",
            "- 认证方式：token/cookie/session/OAuth/SSO 流程说明。",
            "- 只读 API smoke 路径。",
            "- 核心业务链路清单。",
            "- 是否允许写入型测试及测试数据清理策略。",
            "",
            "## 我方交付确认",
            "",
            "- 环境适配状态能解释为什么可测或不可测。",
            "- 风险卡片使用业务语言，而不是接口日志。",
            "- 证据链默认脱敏。",
            "- 成果战报包含上线建议、风险摘要、下一步动作和价值指标。",
            "",
            "## 安全边界",
            "",
            "默认不展示 token、cookie、password、session、client_secret 原值；如需完整证据，应走客户内部授权流程。",
            "",
        ]
    )


def _write_commercial_assets(root: Path, manifest: DeliveryBundleManifest) -> list[str]:
    commercial = root / "commercial"
    commercial.mkdir(parents=True, exist_ok=True)
    files = {
        "01_one_pager.md": _render_one_pager(manifest),
        "02_sales_demo_script.md": _render_demo_script(manifest),
        "03_customer_handoff_checklist.md": _render_customer_handoff_checklist(),
    }
    written: list[str] = []
    for name, content in files.items():
        path = commercial / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def _scan_for_secret_patterns(root: Path, patterns: Sequence[str] = SECRET_GUARD_PATTERNS) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in patterns:
            if pattern and pattern in text:
                findings.append(f"{path.relative_to(root)}:{pattern}")
    return findings


def _zip_directory(root: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path != zip_path:
                archive.write(path, path.relative_to(root))


def build_delivery_bundle(
    *,
    scenarios: Sequence[str] = DEFAULT_SCENARIOS,
    output_dir: str | Path = "outputs/phase103_delivery_bundle",
    create_zip: bool = True,
) -> dict[str, Any]:
    """Build a customer-safe delivery bundle for selected demo scenarios."""
    selected = tuple(scenarios or DEFAULT_SCENARIOS)
    for scenario in selected:
        if scenario not in DEMO_SCENARIOS:
            raise ValueError(f"unsupported scenario: {scenario}")

    root = Path(output_dir)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    scenarios_root = root / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)

    manifest = DeliveryBundleManifest()

    for scenario in selected:
        scenario_root = scenarios_root / scenario
        site_dir = scenario_root / "site"
        data_dir = scenario_root / "data"
        scenario_root.mkdir(parents=True, exist_ok=True)

        site = Phase103PreviewSite(scenario=scenario, static_dir=site_dir)
        export_demo_bundle(site.bundle, data_dir)
        _write_json(scenario_root / "preview_manifest.json", site.preview_manifest)

        acceptance = validate_preview_site(scenario=scenario, static_dir=site_dir)
        acceptance_json, acceptance_md = _copy_acceptance(acceptance, scenario_root)

        display_name = str(DEMO_SCENARIOS[scenario].get("display_name") or scenario)
        manifest.scenarios.append(
            DeliveryScenarioManifest(
                scenario=scenario,
                project_id=site.project_id,
                display_name=display_name,
                site_dir=str(site_dir.relative_to(root)),
                data_dir=str(data_dir.relative_to(root)),
                acceptance_report=str(Path(acceptance_md).relative_to(root)),
                acceptance_passed=acceptance.passed,
                acceptance_score=acceptance.score,
                entrypoint=str((site_dir / "index.html").relative_to(root)),
                api_manifest=str((scenario_root / "preview_manifest.json").relative_to(root)),
            )
        )

    manifest.commercial_files = [str(Path(path).relative_to(root)) for path in _write_commercial_assets(root, manifest)]
    readme_path = root / "README_DELIVERY_BUNDLE.md"
    readme_path.write_text(manifest.to_markdown(), encoding="utf-8")
    manifest.safety_files.append(str(readme_path.relative_to(root)))

    leaks = _scan_for_secret_patterns(root)
    if leaks:
        manifest.redaction_status = "failed"
        _write_json(root / "redaction_findings.json", {"findings": leaks})
    else:
        manifest.redaction_status = "safe"

    _write_json(root / "delivery_manifest.json", manifest.to_dict())
    (root / "delivery_manifest.md").write_text(manifest.to_markdown(), encoding="utf-8")

    if create_zip:
        zip_path = root.with_suffix(".zip")
        _zip_directory(root, zip_path)
        manifest.zip_path = str(zip_path)
        _write_json(root / "delivery_manifest.json", manifest.to_dict())
        (root / "delivery_manifest.md").write_text(manifest.to_markdown(), encoding="utf-8")

    return manifest.to_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a customer-safe Phase103 delivery demo bundle.")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(DEMO_SCENARIOS),
        help="Scenario to package. Repeat to include multiple. Defaults to all scenarios.",
    )
    parser.add_argument("--output-dir", default="outputs/phase103_delivery_bundle", help="Output directory for the delivery bundle.")
    parser.add_argument("--no-zip", action="store_true", help="Do not create a zip archive next to the output directory.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = build_delivery_bundle(
        scenarios=tuple(args.scenario or DEFAULT_SCENARIOS),
        output_dir=args.output_dir,
        create_zip=not args.no_zip,
    )
    print(json.dumps({"passed": manifest["passed"], "average_acceptance_score": manifest["average_acceptance_score"], "zip_path": manifest.get("zip_path")}, ensure_ascii=False, indent=2))
    return 0 if manifest["passed"] else 1


__all__ = [
    "DEFAULT_SCENARIOS",
    "DeliveryBundleManifest",
    "DeliveryScenarioManifest",
    "PHASE103X_VERSION",
    "SECRET_GUARD_PATTERNS",
    "build_delivery_bundle",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
