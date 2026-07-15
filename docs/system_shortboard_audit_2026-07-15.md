# QualiBug AI 系统短板排查报告

> 排查日期：2026-07-15｜范围：主仓库（排除 `.worktrees/`、`node_modules/`、临时目录 `_tmp*`/`_funnel*`/`_private_eval`/`_audit_packs`）
> 方法：4 个并行代码侦查子任务 + 关键"必崩"指控抽样复验（Read/Grep/AST/pytest 真实收集）

## 0. 总览结论

系统**核心引擎与契约框架基本成立、fail-fast 到位、benchmark GT 隔离纪律好、端到端曾跑通**。但**当前实测状态是"红色"**：存在多处运行时 NameError、产品后端入口缺失、前后端契约脱节，且**测试套件本身已无法完整收集**。

| 维度 | 状态 | 一句话 |
|---|---|---|
| 代码健康 | 🟠 | 抽样 240 文件 AST 0 语法错误，但存在多处运行时 NameError 桩崩 |
| **测试套件** | **🔴** | **≥36 个测试模块在当前代码下导入失败（相对 7/10 的 1599 全收集已回归）** |
| Discovery/Benchmark | 🟠 | 契约地板守住、端到端跑通过，但 GT 文件泄漏仓库 + 若干契约漂移 |
| 前后端集成 | 🔴 | 后端产品入口缺失、三套 API 前缀互不兼容、无 CORS |

**最卡商业化的两个点**：①客户交付闸口 v2 判定逻辑直接 NameError → Pilot 交付判定跑不起来；②文档化后端入口 `private_pilot_service.py` 主仓库缺失 → 前端 6 大模块无后端可连。

**最大隐患（容易被"98% 就绪"假象掩盖）**：测试套件已是红色。仓库里那份 `_pytest_collect.txt`（7/10 生成，1599 全收集）已**不能代表当前代码**——本轮在干净环境复跑，pytest 在累计 **36 个收集错误后中断**，至少 36 个模块导入不了。这与你"不造假数据、没有执行不要给我结论"的铁律直接冲突：当前没有任何可信的"测试通过"证据。

---

## 🔴 严重短板（P0，必须修）

### 1. 客户交付闸口 v2 核心判定函数全崩（NameError）
- 文件：`ai_test_asset_center/customer_delivery_gate_v2.py`
- 问题：`has_validated_evidence_quality`（:1375）与 `has_passed_business_evidence_status`（:1384）调用的 `_v1_lower` / `_v1_number` / `_v1_upper` **全文件无定义**；`:1695`、`:1822` 引用的 `LEGACY_CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA` 亦未定义。
- 复验：Grep 仅命中 2 处"使用"，0 处 `def`/赋值 → 确认未定义。
- 影响：**决定"是否可向 Pilot 客户交付干净缺陷"的判定逻辑一调即崩**。你正等首个 Pilot 签署，这条路径失效等于"交付闸门焊死"。

### 2. UI 旅程测试两条核心意图必崩（NameError）
- 文件：`aitestops/ui_journey_tester.py`
- 问题：`perform_intent()` 签名（:408）为 `(self, page, intent, step, config, element_map, healer)`，但 `:431`/`:438`/`:441` 调用了未传入的 `collector.capture(step_id, …)`，`collector` 与 `step_id` 在函数内均未定义。
- 复验：Read 确认调用方 `:395` 未传 `collector`、传的是 `step` 而非 `step_id` → 确认 NameError。
- 影响：`discover_pages` / `verify_page_content` 两条 UI/UX 旅程意图（覆盖 10 类缺陷）执行即崩。

### 3. 产品后端入口缺失 → 文档化端点无法启动
- `openapi_qualibug.yaml` 描述的大量端点（`/api/pilot/*`、`/api/knowledge/*`、`/api/findings`、`/dashboard`、`/control-plane`…）由 `private_pilot_service.py` 提供。
- 复验：Glob 确认该文件**主仓库不存在**，仅在 `.worktrees/benchmark-131-phase01/` 下。
- `start_all.bat:16`、`setup.py:39` 入口均指向缺失模块 → 主仓库起不来文档化产品后端。同时 `test_private_pilot_knowledge_ingest.py` 也因导入 `private_pilot_service` 失败而红。

### 4. 前后端 API 契约严重脱节
- `backend/main.py` 仅暴露 `/run /replay /metrics /graph /logs /v1/* /health`（已 Grep 确认 14 条路由）。
- 前端 `frontend/src/api/client.ts` 调用 `/api/v1/*`；OpenAPI 文档是 `/api/*` → **三套前缀互不对齐**，前端 6 大 UI 模块调用无对应后端实现。
- `backend/main.py` **未注册 CORSMiddleware**，跨源直连 8088 会失败（dev 仅靠 vite 代理兜底，生产/独立调用必挂）。

### 5. 测试套件当前红色：≥36 个模块导入失败（相对 7/10 已回归）
- 复跑证据：干净环境 `venv + pytest 8.4.2`，`pytest --collect-only` 在累计 **36 个 `ImportError` 后中断**（至少 36 模块红，可能更多）。
- 全部为第一方符号缺失（`cannot import name 'X' from ai_test_asset_center.Y`），**非缺第三方依赖**。
- 根因聚类：
  - **整文件缺失（最严重）**：`risk_based_probe_planner`（`ai_test_asset_center/` 下无此文件）、`private_pilot_service`（仅 `.worktrees` 有，主仓库缺）→ 对应测试无实现可测。
  - **符号被重命名/移除、测试未同步（占比最大）**：`build_commercial_audit_export_adapters`→提示应为 `runtime_commercial_audit_export_adapters`；`build_external_tracker_sync_payloads`→`runtime_external_tracker_sync_payload_builder`；`build_commercial_external_tracker_reconciliation`→`runtime_commercial_external_tracker_reconciliation`；`validate_external_tracker_sync_payloads`（全局无定义）；`audit_commercial_handoff_secrets`（模块在、函数不在）；`adapt_legacy_champion_result`（legacy 退役）；`HARNESS_PROPOSAL_SCHEMA`；`ProbeGenerator`；`display_ready_formatter`；`enterprise_pilot_runtime_with_chain`；`db_persistence`；`real_project_discovery_with_chain` 等——均从 `ai_test_asset_center` 包根导入但 `__init__` 未导出。
- **共性根因**：一轮大规模重构（去硬编码 / legacy 退役 / 模块改名）后，**测试未被同步更新**，套件从 7/10 的"1599 全收集、0 错误"退化为当前红色。
- 影响：当前**没有任何可信的"测试通过"证据**；"98% 就绪"的体感与实测不符。违反铁律 #7（不造假、无执行不结论）。

---

## 🟠 中等短板（P1）

### 6. 硬编码违反"全行业零硬编码"铁律
- `ai_test_asset_center/enterprise_project_config.py:41-103`：`EXAMPLE_MULTI_SERVICE_CONFIG` 含电商专属 URL（`http://order-service.internal:8080`）、库名（`order_db`/`payment_db`）、行业字符串（`external_integrations:["jt-express","pinduoduo"]`）。
- `aitestops/enterprise_ai_automation.py:67` 比对 `"http://127.0.0.1:8000"`（应为 8088）；`ui_journey_tester.py:24`、`benchmark_evaluator/benchmark_bug_factory.py:863,929` 默认 8000，与 5174/8088 契约不符。
- `ai_test_asset_center/auto_test_data_factory.py:250` 硬编码合成域名 `https://qualibug.local/api/test/{seed}`。

### 7. Fail-Fast 被违反（沉默吞错）
- `ai_test_asset_center/grounded_probe_executor.py:5784` `except Exception: pass`（conn.close 静默吞错）；`:5785` `except Exception: coupon_cases={}` 宽泛兜底。

### 8. Benchmark 资产治理泄漏（安全/合规风险）
- 根目录 `_tmp_money_gt.txt` 含 131-bug 冻结 GT 的精确复现攻击路径（如 `POST /api/coupons/admin/create amount=9999`）。
- 违反 AGENTS.md："GT/复现答案须 evaluator-private，不得进仓库、不得进 prompt/运行时/trace"。虽未被 import，但**必须清理并加入 `.gitignore`**。

### 9. 契约漂移（文档与代码不一致）
- `BENCHMARK_MANIFEST.json` 不存在；身份实际由 `_private_eval/.../evaluation_manifest.json` 决定，文件名与契约不符。
- all-blocked 运行未标 `BLOCKED`：`obligation_attempt_ledger.derive_campaign_terminal_status`（:719）全 BLOCKED 仍返回 `completed`（仅 zero-selected 标 BLOCKED）→ 空发现可能被误读为"无缺陷"。
- `legacy_champion` 已退役（`v12_pipeline` 抛 `NotImplementedError`），非契约所称默认策略。
- AGENTS.md 引用的 `discovery_engine.py` 路径已陈旧（实际在 `ai_test_asset_center/`）。

---

## 🟡 低 / 待澄清（P2）

### 10. 运行时导入缺口（归并到 NameError 类）
- `benchmark_evaluator/benchmark_compute.py:562`（`Iterable`）、`metrics.py:138,167`（`Any`）typing 名未导入。
- `ai_test_asset_center/har_bridge.py:273` 缺 `from pathlib import Path`；`enterprise_knowledge_center.py:1415` 异常处理缺 `import sys`；`auto_test_data_factory.py:199` `_openapi_spec_cache` 未定义。

### 11. 导入卫生
- 大量未使用 import；18 处 `from .x import *` 屏蔽未定义名检测，削弱可观测/可维护性。

### 12. 可复跑性工程缺口
- 受管运行时不预装 pytest，且无显式 `requirements.txt` / `pip install -e .` 指引，干净 checkout 无法一键复跑测试。
- 建议：补依赖清单 + 最小 CI（collection + 一个无网冒烟子集），落实"不造假、有执行"。

---

## 亮点（已确认良好）
- 抽样 240 个目标文件 AST 解析 **0 语法错误**。
- benchmark GT 严格隔离（`PRIVATE_BLOCKLIST` 盲测纪律），未发现 GT/身份泄漏进产品代码或 prompt。
- 配置地板守住：`policy_registry.py`（timeout≥300、max_tokens≥32768）、`stage_reason_all_v2.py`（MAX_HYPOTHESES=15、max_workers=4）。
- `discovery_mainline.run_discovery_mainline` 真实实现；`run_v12_pipeline` 仅调一次、无 retry/legacy 回退；trace ledger 强制 v3 + 显式离线迁移、禁静默回退。
- 端到端曾真实跑通：`platform_outputs/evaluation-held-in-1/` 有 5 份 trace ledger（41 attempts），突破审计诚实标 `INCOMPLETE`、未伪造。

---

## 优先修复路线
1. **P0**：修 #1 交付闸口（最卡商业化）→ 修 #2 UI 旅程 → 恢复 #3 后端入口（`private_pilot_service.py` 归位主仓库）→ 对齐 #4 三套 API 前缀 + 补 CORS → **修 #5 测试红色（同步 36+ 模块到当前源码符号）**。
2. **P1**：清 #8 GT 泄漏（含 .gitignore）→ 修 #6 硬编码、#7 吞错 → 修 #9 契约漂移。
3. **P2**：补 #12 依赖清单+最小 CI → 清理 #10/#11 导入卫生。

> 注：#5 的 36 个红色模块是"重构未同步测试"的集中体现，建议优先做"源码符号现状 vs 测试导入"的全量 diff，一次性对齐，而不是逐个补。
