# QualiBug AI Enterprise Edition — 产品架构图

> Phase 61 Complete | private deployment | MES BugLab training target

---

## 一、全局分层架构

```
Layer 1  输入层         PRD.md + OpenAPI.json + Project Config + .env
         ─────────────────────────────────────────────────────────────
Layer 2  编排层         loop_daemon.py → run_loop_worker.py
                        autonomous_evolution_orchestrator.py
                        loop_watchdog.py（存活监控）
         ─────────────────────────────────────────────────────────────
Layer 3  核心发现管线    discovery_engine.py + self_improving_loop.py
         ┌─────────────────────────────────────────────────────────┐
         │ Stage 0  ProjectContext 编译（本地 regex，~0.1s）         │
         │ Stage 1  Reader 业务事实提取（DeepSeek LLM / cache HIT）   │
         │ Stage 2  Reasoner 11 引擎并行推理（4 workers, ~120-180s）  │
         │ Stage 3  Executor API 探针执行（51 routes, ~5-10s）        │
         │ Stage 4  Verifier 证据判定（confirmed/falsified/inconclusive）│
         │ Loop     Self-Improving: Observe → Diagnose → Improve → Verify │
         └─────────────────────────────────────────────────────────┘
         ─────────────────────────────────────────────────────────────
Layer 4  基础设施层     DeepSeek LLM API（deepseek-v4-pro, timeout>=300s）
                        MES BugLab Target（FastAPI :8000, 51 routes）
                        llm_reasoning.py（urllib stdlib，零外部 HTTP 依赖）
                        SQLite lease guard（防重复执行）
         ─────────────────────────────────────────────────────────────
Layer 5  持久化层       platform_outputs/{project}/
                        .discovery_result.json | .loop_heartbeat.json
                        .loop_lease.db | loop_daemon.log
```

---

## 二、核心管线 — 单轮数据流（discover 方法）

```
PRD + OpenAPI + Config
        │
        ▼
Stage 0  project_context_compiler.compile()
         → 本地 regex 提取 entities, relations, API capabilities
         → 输出: ProjectContext（18 entities, bindings, invariants）
        耗时: ~0.1s
        │
        ▼
Stage 1  project_context_artifact.get_or_build()
         ├─ Cache HIT  → 零 API 调用，直接返回 18 entities
         └─ Cache MISS → stage_read() 调用 DeepSeek LLM（150-200s）
         输出: entities[], candidate_lifecycles[], artifact_status
        耗时: HIT ~0.1s | MISS ~180s
        │
        ▼
Stage 2  stage_reason_all_v2._stage_reason_all_v2()
         ThreadPoolExecutor(max_workers=4)
         每引擎独立 ReasoningClient（deepcopy config, timeout>=300s）
         ┌─────────────┬──────────────┬────────────────┬───────────────┐
         │ causality   │ invariant    │ reconciliation │ counterexample│
         │ consistency │ population   │ outcome        │ temporal      │
         │ saga        │ event_chain  │ metamorphic    │               │
         └─────────────┴──────────────┴────────────────┴───────────────┘
         每引擎 2 次 retry 机会，max 15 hypotheses/引擎
         输出: ~100-150 hypotheses，JSON truncated recovery
        耗时: ~120-180s total（4 workers 并行）
        │
        ▼
Stage 3  Executor
         build route map → execute probes against MES Target
         safe_mode: 只 GET + 幂等 POST
         51 routes → ~50 probes/round
         login: POST /api/auth/login (admin/admin123)
        耗时: ~5-10s
        │
        ▼
Stage 4  Verifier——stage_verify
         比对 API 响应 vs 假设预期
         - confirmed:  证据支持假设（多视图不一致、输入校验缺失等）
         - falsified:  证据否定假设
         - inconclusive: 证据不足以判定
         输出: DiscoveryFinding[]
        耗时: ~1-3s
        │
        ▼
Loop     Self-Improving:
         Observe（本轮发现）→ Diagnose（分析失败引擎）
         → Improve（调整 prompt / 增加 token）
         → Verify（重新运行对比）
```

---

## 三、部署与运行时架构

```
┌────────────────────────────────────────────────────────────────┐
│                    loop_daemon.py 守护进程                       │
│                                                                 │
│  while not shutdown:                                           │
│    run_loop_worker.main()    ←── 调用 worker                    │
│    read .discovery_result    ←── 读取本轮结果                    │
│    sleep(120s)               ←── cooldown                      │
│                                                                 │
│  Signal: SIGINT / SIGTERM → _shutdown = True                   │
└───────────────────────────┬────────────────────────────────────┘
                            │
        ┌───────────────────┴───────────────────┐
        ▼                                       ▼
┌───────────────────────────┐       ┌───────────────────────────┐
│   run_loop_worker.py      │       │   LoopRuntimeSession      │
│   单次执行（1-round mode）  │       │   跨进程 Lease + Heartbeat │
│                            │       │                           │
│   autonomous_evolution_    │       │   .loop_lease.db          │
│     orchestrator           │◄──────│   (owner_id+PID+expires)   │
│         │                  │       │                           │
│   self_improving_loop.run()│       │   daemon heartbeat pump   │
│         │                  │       │   (30s interval, daemon    │
│         ▼                  │       │    thread)                │
│   .discovery_result.json   │       └───────────────────────────┘
└───────────────────────────┘
         │
         │ Executor HTTP probes →  │  Reader/Reasoner LLM calls →
         ▼                         ▼
┌───────────────────┐     ┌───────────────────┐
│  MES BugLab Target │     │  DeepSeek LLM API  │
│  FastAPI :8000     │     │  api.deepseek.com  │
│  51 API routes     │     │  deepseek-v4-pro   │
│  SQLite in-memory  │     │  urllib stdlib     │
└───────────────────┘     └───────────────────┘
```

---

## 四、模块清单

### 编排层
| 文件 | 职责 |
|------|------|
| `loop_daemon.py` | 守护进程：while loop + cooldown + signal handling |
| `run_loop_worker.py` | 单次 worker：orchestrator 入口，写入 result JSON |
| `ai_test_asset_center/autonomous_evolution_orchestrator.py` | 进化编排：policy 版本管理，evolution 触发判断 |
| `ai_test_asset_center/self_improving_loop.py` | 自进化循环：Observe/Diagnose/Improve/Verify，compile_context |
| `ai_test_asset_center/loop_runtime.py` | 跨进程 Lease + Heartbeat pump（SQLite + JSON） |
| `ai_test_asset_center/loop_watchdog.py` | 存活监控：heartbeat 超时检测，API health check |

### 核心管线
| 文件 | 职责 |
|------|------|
| `ai_test_asset_center/discovery_engine.py` | 主引擎：4 Stage pipeline + discover() + login |
| `ai_test_asset_center/stage_reason_all_v2.py` | Reasoner v2：11 engines x 4 workers，truncated JSON recovery |
| `ai_test_asset_center/project_context_compiler.py` | Stage 0：本地 regex 提取 entity/API/relation |
| `ai_test_asset_center/project_context_artifact.py` | Stage 1 cache：SHA256 缓存 + single-flight + stale fallback |
| `ai_test_asset_center/reasoner_prompt.py` | Reasoner prompt templates + system prompt + anti-hallucination guard |

### 基础设施
| 文件 | 职责 |
|------|------|
| `ai_test_asset_center/llm_reasoning.py` | LLM client：urllib stdlib，ReasoningConfig + ReasoningClient |
| `ai_test_asset_center/policy_registry.py` | Policy Registry：timeout/max_workers/max_tokens 等 guardrail |
| `ai_test_asset_center/policy_wiring.py` | Policy 读取：get_policy_value() with fallback |
| `mes_target/mes-buglab-target/backend/app/main.py` | MES 靶场：FastAPI，51 routes，Login + RBAC |

### 持久化
| 文件 | 格式 | 内容 |
|------|------|------|
| `.discovery_result.json` | JSON | findings, verdicts, rounds, improvements |
| `.loop_heartbeat.json` | JSON | step, detail, status, lease_expires_at |
| `.loop_lease.db` | SQLite | owner_id, PID, acquired_at, expires_at |
| `loop_daemon.log` | Text | daemon lifecycle, worker exit codes |
| `project_context_artifact/*.json` | JSON | cached Reader output (SHA256 keyed) |

---

## 五、关键 Guardrail（不可降低）

| 文件 | 行 | 值 | 原因 |
|------|-----|-----|------|
| `discovery_engine.py` | `__init__` | `timeout_seconds >= 300` | Reader prompt 8000 chars，DeepSeek 需 150-200s |
| `discovery_engine.py` | `__init__` | `max_tokens >= 32768` | Causality engine 产出 >41K chars JSON |
| `stage_reason_all_v2.py` | `MIN_REASONER_TIMEOUT_SECONDS` | `300` | 每个引擎调用 DeepSeek 的 timeout floor |
| `stage_reason_all_v2.py` | `MAX_HYPOTHESES` | `15` | 每引擎假设上限，超出截断 |
| `stage_reason_all_v2.py` | `max_workers` | `4` | 并行 worker 数，≤4 防止 API 限流 |
| `llm_reasoning.py` | `max_tokens` | `32768` | 确保 Long JSON 输出不被截断 |

---

## 六、Single Round 典型耗时

| 阶段 | Cache HIT | Cache MISS | 说明 |
|------|-----------|------------|------|
| Stage 0 Context | 0.1s | 0.1s | 纯本地 regex |
| Stage 1 Reader | 0.1s | 150-200s | MISS 时调用 DeepSeek |
| Stage 2 Reasoner | 120-180s | 120-180s | 11 engines x 4 workers 并行 |
| Stage 3 Executor | 5-10s | 5-10s | 51 routes HTTP probes |
| Stage 4 Verifier | 1-3s | 1-3s | 本地证据比对 |
| **总计** | **~130-200s** | **~280-390s** | Cache HIT 可节省 1 次 DeepSeek 调用 |
