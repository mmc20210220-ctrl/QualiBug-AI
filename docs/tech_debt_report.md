# QualiBug AI v95.0.0 — 技术债务报告

> 审查日期: 2026-07-18 | 代码规模: 508 .py 文件, ~259,000 行

---

## P0 — 生产稳定性风险

### 1. 生产环境使用 stdlib HTTP Server

| 项目 | 详情 |
|---|---|
| **文件** | `ai_test_asset_center/private_pilot_service.py:194-212` |
| **现状** | `http.server.ThreadingHTTPServer` 作为生产服务器 |
| **严重性** | 高 — 影响所有生产部署 |

**具体问题:**

```
文件: private_pilot_service.py, 第 194-212 行
server = ThreadingHTTPServer((host, selected_port), PrivatePilotHandler)
```

| 缺失能力 | 后果 |
|---|---|
| 无连接池 / worker 复用 | 每请求创建新线程，高并发下线程爆炸 |
| 无 request_queue_size 配置 | backlog 默认 ~5，突发流量直接丢连接 |
| 无优雅关闭 | `server_close()` 直接断开进行中的请求 |
| 无 SO_REUSEADDR | 重启时 "address already in use" 风险 |
| 无请求超时 | 慢客户端可无限占用线程 |
| 无请求体大小限制 | 恶意大请求可耗尽内存 |
| 无速率限制 | 无 HTTP 层防护 |

**建议方案:** 迁移到 Uvicorn/Gunicorn ASGI，或至少 `ThreadingHTTPServer` + `ThreadPoolExecutor`。项目已有 `uvicorn` 依赖（pyproject.toml 第 25 行）。

---

### 2. V12 Pipeline — HAR 条目并发污染

| 项目 | 详情 |
|---|---|
| **文件** | `ai_test_asset_center/v12_compat_helpers.py:30-41` |
| **严重性** | 高 — 并发扫描会导致数据交叉污染 |

**问题:**

```python
# v12_compat_helpers.py, 第 30 行 — 模块级可变全局变量
_v12_har_entries: list[dict[str, Any]] = []

# 第 35 行 — 无锁写入
def _record_v12_har(method, url, status, body, actor="", elapsed_ms=0.0):
    _v12_har_entries.append({...})   # 无锁，无线程安全

# 第 49 行 — 无锁读取
def _v12_har_report():
    for item in _v12_har_entries:    # 迭代中可能被修改
        ...
```

**关键发现:**
- 列表**只增不删** — 旧版 `v12_pipeline.py` 中的复位代码 (`global _v12_har_entries; _v12_har_entries = []`) 在提交 `882a3d5` 中随 `_run_legacy_champion_domain()` 一起被删除
- `v12_pipeline.py:52` 的同名变量已是**死代码**（不再被读写）
- `_record_v12_har()` 被 `v12_legacy_scenario_exec.py` 的 `__execute_scenario_once()` 在活动执行路径中调用
- 整个调用链 (`scan() → _scan_impl() → run_v12_pipeline → ... → _record_v12_har`) **无任何并发保护**
- 当前 CLI 单进程部署下安全，但任何多线程/多扫描并发场景都将触发此问题
- 搜索整个代码库：**无 `threading.Lock`、`RLock` 或任何并发原语**在 pipeline 相关代码中

**建议方案:** `threading.local()` 或扫描上下文对象传递，每次扫描开始显式复位。

---

### 3. 凭证加密静默明文回退

| 项目 | 详情 |
|---|---|
| **文件** | `ai_test_asset_center/credential_crypto.py:68-69` |
| **严重性** | 高 — 未配置密钥时凭证以明文存储，无警告 |

```python
# credential_crypto.py, 第 68-69 行
def encrypt(plaintext: str) -> str:
    master = _master_key()
    if not master:
        return plaintext   # 静默回退到明文，零警告
```

- 如果 `QUALIBUG_CRED_ENC_KEY` 环境变量未设置，所有"加密"操作直接返回明文
- `decrypt()` 遇到加密 blob 而无密钥时会抛异常，但明文模式下永远不会触发
- 密钥生成路径 `ensure_local_credential_encryption_key()` 虽然使用 `secrets.token_urlsafe(48)` 生成 384 位随机密钥，但这是部署时的行为，不是代码自动保证的

**建议方案:** 启动时强制检查密钥存在性，缺失时拒绝启动或至少发出 `WARNING` 级别日志。

---

## P1 — 架构风险

### 4. 15+ Monkey-Patch 启动链

| 项目 | 详情 |
|---|---|
| **文件** | `ai_test_asset_center/private_pilot_entrypoint.py:152-167` |
| **严重性** | 中-高 — 脆弱、难调试、无失败恢复 |

**完整补丁安装链（按顺序）:**

| # | 补丁 | 安装函数 | 实际作用 |
|---|---|---|---|
| 1 | 命令中心运行时 | `install_command_center_runtime_support()` | 扫描上下文 + 凭证 + 诊断钩子 |
| 2 | 扫描 Campaign 上下文 | `install_extracted_scan_campaign_context_patch()` | 设置 `_SCAN_CAMPAIGN_CONTEXT_PATCHED` |
| 3 | 凭证安全 | `install_extracted_credential_safety_patch()` | 凭证加密兼容标记 |
| 4 | 扫描结果修复 | `install_scan_result_repair_patch()` | 注册扫描后钩子修复证据持久化 |
| 5 | 回归 Oracle | `install_regression_oracle_patch()` | 推断 HTTP 状态 oracle |
| 6 | 回归套件刷新 | `install_regression_suite_refresh_patch()` | 扫描后自动更新回归套件 |
| 7 | 系统行为空间 (含 6 子补丁) | `install_system_behavior_runtime_patch_chain()` | BSG钩子+场景+Oracle+发现+回归 |
| 8 | 覆盖率矩阵 | `install_coverage_matrix_patch()` | 命令中心注入覆盖率数据 |
| 9 | 回归运行可见性 | `install_regression_run_visibility_patch()` | 命令中心注入回归结果 |
| 10 | 无修复建议(显示层) | `install_display_ready_no_fix_advice_patch()` | 显示格式化兼容 |
| 11 | 无修复建议(数据层) | `install_no_fix_advice_patch()` | 剥离修复建议字段 |
| 12 | 覆盖率引导 | `install_coverage_steering_patch()` | 行为切片重排序 |
| 13 | 浏览器 UI 烟雾测试 | `install_browser_ui_smoke_patch()` | Playwright UI 健康检查 |
| 14 | 客户报告 | `install_customer_report_patch()` | 报告渲染兼容 |
| 15 | 部署合约 | `install_deployment_contract_patch()` | 部署健康合同 |

**问题分析:**

- 补丁之间**无显式排序约束**，但某些补丁通过恢复-重新安装实现隐式依赖（如 #2、#3）
- 任一个顶级安装器抛出异常 → **服务器直接启动失败**，无 catch-all
- 子补丁（系统行为空间）使用 `try/except: return` 静默跳过，但不记录失败
- 所有补丁使用 "第一类钩子" 模式（注册回调 + 设置 `_*_PATCHED` 标志），零个函数符号替换，因此恢复相对简单
- 恢复链按**安装逆序**执行

**建议方案:** 引入正式的插件注册系统/扩展点（`register_extension`），替代当前的 ad-hoc monkey-patching。

---

### 5. 两套后端入口点并存

| 项目 | 详情 |
|---|---|
| **文件** | `backend/main.py` (FastAPI) vs `private_pilot_service.py` (stdlib HTTP) |
| **严重性** | 中 — 运维混淆 |

| 维度 | `backend/main.py` | `private_pilot_service.py` |
|---|---|---|
| 框架 | FastAPI | `http.server.ThreadingHTTPServer` |
| 引擎 | `core/engine.py` (纯模拟 v11) | 真实扫描引擎 |
| 端点 | `/run`, `/replay`, `/v1/scans`... | Dashboard/SPA + API |
| 认证 | `QUALIBUG_API_TOKEN` / JWT 策略 | JWT (AuthScopeMixin) |
| 状态 | "仅兼容/实验接口" | 生产主入口 |

**问题:** `backend/main.py` 虽然声明为实验性，但仍暴露 `/v1/scans`、`/v1/source-assets/register` 等企业端点，使用模拟引擎。运维人员可能混淆哪个是正式入口。

**建议方案:** 明确废弃 `backend/main.py`，或将模拟引擎替换为真实引擎后作为 Uvicorn 版本的唯一入口。

---

### 6. `core/engine.py` — 纯模拟引擎

| 项目 | 详情 |
|---|---|
| **文件** | `core/engine.py:67-131` |
| **严重性** | 中 — 误导性生产代码 |

```python
class Engine:
    def __init__(self):
        self.version = "v11"
        self.redis = RedisClient()    # 内存模拟
        self.pg = PostgresClient()    # 内存模拟
        self.kafka = KafkaClient()    # 内存模拟

    def worker(self, task):
        return {
            "status": "simulated_not_executed",
            "execution_status": "not_executed",
            "evidence_level": "synthetic",
            "simulation": True,
        }
```

- Redis/Postgres/Kafka 客户端全部是 `source="memory"` 的内存模拟
- `worker()` 明确返回 `simulated_not_executed`，**从不执行真实 HTTP 请求**
- 仅被 `backend/main.py` (FastAPI) 使用

**建议方案:** 从代码库中移除或移至 `tests/` 目录，命名为 `mock_engine.py`。

---

### 7. 巨型模块 — 可维护性瓶颈

| 排名 | 文件 | 行数 | 函数数(~) | 主要问题 |
|---|---|---|---|---|
| 1 | `grounded_probe_executor.py` | 6,686 | ~110 | 超大函数 + 密集条件分支 |
| 2 | `defect_discovery.py` | 5,713 | ~130 | 20+ if/elif 链、重复风险分类逻辑 |
| 3 | `semantic_scenario_generator.py` | 3,740 | ~80 方法 | 单体类承担过多职责 |
| 4 | `enterprise_knowledge_center.py` | 3,711 | ~100 | 20+ if/elif 来源分类链 |
| 5 | `display_ready_formatter.py` | 3,294 | ~70 | 深度耦合的格式化管道 |

**合计 23,144 行** 集中在前 5 个文件中。

**典型膨胀模式:**

- **`defect_discovery.py`** — `invariant_statement()` 中 21 个连续 `if risk == "xxx"` 语句，应用字典映射表替代
- **`defect_discovery.py`** — `DiscoveryEngine.run()` 一个方法 235 行
- **`semantic_scenario_generator.py`** — `SemanticScenarioGenerator` 一个类 ~80 方法，应拆分为 `WriteScenarioGenerator`、`PermissionSliceGenerator`、`IsolationSliceGenerator` 等
- **`enterprise_knowledge_center.py`** — `_parse_source()` / `_classify_source()` 超长函数，20+ if/elif

---

## P2 — 可维护性

### 8. 根目录 62 个调试/临时文件

| 类别 | 数量 | 总大小(~) |
|---|---|---|
| `_tmp_*` 测试运行转储 | 23 | ~5.6 MB |
| `_tmp_*` 诊断脚本 | 6 | — |
| `_*` 分析/探测脚本 | 16 | — |
| `_funnel_*` 基准测试 | 5 | ~2.5 MB |
| 日志文件 | 4 | — |
| 报告 | 2 | — |
| 损坏文件 (`nul`) | 1 | 102 字节 |
| **合计** | **62** | **>10 MB** |

**建议:** 清理后添加到 `.gitignore`。

---

### 9. `v12_pipeline.py` 中的死代码和过时注释

| 问题 | 文件:行号 |
|---|---|
| `_v12_har_entries` 声明为死代码 | `v12_pipeline.py:52` |
| 注释引用不存在的第 1414 行复位代码 | `v12_pipeline.py:55-58` |
| 实际生效的 `_v12_har_entries` 在另一文件且无复位 | `v12_compat_helpers.py:30` |

---

## P3 — 安全性

### 10. 多重认证机制并存

- JWT 认证 (推荐) — `AuthScopeMixin`
- 静态 API Token — `QUALIBUG_API_TOKEN` (旧版兼容模式)
- 策略 JSON Token — `QUALIBUG_ACCESS_POLICY_JSON`

静态 token 在生产环境中存在泄漏和无法轮换的风险。

### 11. `scan_diagnostics.py` 文件计数器竞争条件

| 文件 | `scan_diagnostics.py:492-504` |
|---|---|
| **问题** | `increment_scan_counter()` 读取 JSON → 递增 → 写回，无文件锁 |
| **影响** | 多进程并发扫描时计数器可能丢失更新 |

---

## 修复优先级建议

| 优先级 | 序号 | 问题 | 预计工作量 | 风险降低 |
|---|---|---|---|---|
| P0 | #2 | HAR 并发污染 | 2-3 天 | 消除并发数据安全风险 |
| P0 | #1 | HTTP Server 生产就绪 | 5-7 天 | 提升所有部署的可靠性 |
| P0 | #3 | 凭证加密强制化 | 1-2 天 | 消除凭证明文存储风险 |
| P1 | #4 | Monkey-patch 整理 | 7-10 天 | 降低启动失败和调试难度 |
| P1 | #7 | 巨型模块拆分 | 每文件 3-7 天 | 提升可维护性 |
| P1 | #5 | 统一后端入口 | 3-5 天 | 消除运维困惑 |
| P1 | #6 | 移除模拟引擎 | 0.5 天 | 减少误导 |
| P2 | #8 | 根目录清理 | 0.5 天 | 提升仓库整洁度 |
| P2 | #9 | 死代码清理 | 0.5 天 | |
| P3 | #10 | 认证机制统一 | 3-5 天 | 降低安全攻击面 |
| P3 | #11 | 文件锁修复 | 0.5 天 | 消除计数器竞争 |

---

*报告由 WorkBuddy 自动生成，基于代码静态分析和运行时健康检查。*
