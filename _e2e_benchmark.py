"""真实端到端验证:QualiBug 扫描运行中的 benchmark 靶场(localhost:8080)。

- 不读取 hidden_ground_truth(仅用 docs/ 作企业资料)
- execute_readonly=True:只发只读探针,不破坏靶场数据
- 真实对 http://localhost:8080 发 HTTP
"""
import json
import os
import shutil
from pathlib import Path

os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
os.environ["QUALIBUG_TARGET_BASE_URL"] = "http://localhost:8080"
os.environ["QUALIBUG_DB_DSN"] = "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"
os.environ["ENABLE_V12_STATE_GRAPH_ENGINE"] = "true"

from ai_test_asset_center.blind_project_runner import run_input_only_project  # noqa: E402

BENCH = Path(r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\qualibug_enterprise_benchmark_v0_5_windows_native_stable")
DOCS = BENCH / "docs"
E2E_ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_e2e_run")
STAGE_ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_e2e_stage")
PROJECT = "benchmark_mall"

# 源目录名须为 input,项目名取自其父目录名;放在 platform 根(_e2e_run)之外,
# 避免被 _normalize_platform_inputs 的 rmtree 连带删除。
input_dir = STAGE_ROOT / PROJECT / "input"
if input_dir.exists():
    shutil.rmtree(input_dir)
input_dir.mkdir(parents=True, exist_ok=True)
for md in sorted(DOCS.glob("*.md")):
    shutil.copy2(md, input_dir / md.name)
print("INPUT_FILES:", [p.name for p in sorted(input_dir.glob('*.md'))])

report = run_input_only_project(
    project_input_dir=input_dir,
    project_id=PROJECT,
    root=E2E_ROOT,
    base_url="http://localhost:8080",
    execute_readonly=True,
    allow_write_sandbox=True,
)

print("=" * 60)
print("DISCOVERY_SUMMARY:", json.dumps(report.get("discovery_summary"), ensure_ascii=False))
print("PROBE_EXEC_SUMMARY:", json.dumps(report.get("grounded_probe_execution_summary"), ensure_ascii=False))
print("FLOW_SUMMARY:", json.dumps(report.get("flow_discovery_summary"), ensure_ascii=False))
print("CANDIDATES_FILE:", report["outputs"].get("grounded_candidates"))
print("EXEC_REPORT_FILE:", report["outputs"].get("grounded_probe_execution_report"))
print("FLOW_ISSUES_FILE:", report["outputs"].get("flow_discovery_issues"))
