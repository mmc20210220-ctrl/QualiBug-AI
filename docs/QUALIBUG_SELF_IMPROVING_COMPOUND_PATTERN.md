# QualiBug 自改进复利模式：基于 Fable 5 14步框架的落地设计

> 参考文章：[Codez (@0xCodez) — Build self-improving agent system with Fable 5 in 14 steps](https://x.com/0xCodez/status/2065089060104720776)
> 设计日期：2026-07-18
> 适用版本：QualiBug 95.0.0+

---

## 一、问题诊断：QualiBug 就是"把 Fable 5 当 Sonnet 4.6 用"的那个用户

文章的核心洞察一针见血：

> 大多数人把 Fable 5 当成带更大上下文窗口的 Sonnet 4.6 来用。他们写提示词，它能用 5 分钟，然后关掉标签页。10 个用户里有 9 个从未运行过能复利增长的智能体系统。

**这恰恰就是 QualiBug 当前的运行模式：**

| Fable 5 浪费模式 | QualiBug 等价行为 |
|---|---|
| 每次写提示词 → 跑 5 分钟 → 关标签 | 每次 Campaign → Stage 0-4 一次性跑完 → 停 |
| 没有 Loop | 没有 "重跑 + 改进" 的迭代循环 |
| 没有 STATE.md | 每个 Campaign 从零开始，不继承上一次的经验 |
| 没有 Skills 积累 | 11 个推理引擎每次都重新推导，不沉淀 |
| 自我批评代替独立验证 | Stage 4 Verifier 自己评判自己生成的假设 |
| 没有 Routines | 无定时/事件触发的持续学习 |

**结果就是当前的产品基线：Recall 4.58%、Precision 28.57%、F1 7.89%。**

真正的问题不是 LLM 不够聪明，而是 **系统不会从每次运行中学习**。第 47 次 Campaign 和第 1 次 Campaign 完全一样——这在自改进系统看来，等于把学费重复交了 47 遍。

---

## 二、核心框架：QualiBug 自改进复利堆栈

### 2.1 Fable 5 四层架构 → QualiBug 映射

```
┌──────────────────────────────────────────────────────────────────┐
│  Fable 5 模式                      │  QualiBug 落地              │
├──────────────────────────────────────────────────────────────────┤
│  L4 · 自改进                        │  Discovery 自改进层         │
│  视觉自检 / 评估循环 / 规则提炼      │  证据回放评分 / 重复Campaign │
│  教训写入记忆 / 循环闭合             │  逐轮提升 / 规则回写 / 闭环  │
├──────────────────────────────────────────────────────────────────┤
│  L3 · 记忆                          │  Campaign 记忆层            │
│  状态文件 / Skills / 知识库 / 教训   │  STATE.md / BugPattern Skill│
│  会话间持久化                       │  行业知识库 / 跨项目记忆     │
├──────────────────────────────────────────────────────────────────┤
│  L2 · 编排                          │  Discovery 编排层           │
│  /goal & Outcomes / 动态工作流       │  goal-driven 主链路 / 扇出  │
│  Routines / 定时&事件触发            │  综合 / 对抗验证 / 定时扫描  │
├──────────────────────────────────────────────────────────────────┤
│  L1 · 原语                          │  Discovery 原语层           │
│  Fable 5 / 子Agent / Worktrees      │  DeepSeek V4 / 11推理引擎   │
│  Tools / 工具                       │  执行探针 / 行为空间 / 证据  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 当前已具备 vs. 需要构建

| 层级 | 已具备 ✅ | 需要构建 ❌ |
|---|---|---|
| **L1 原语** | DeepSeek V4 Pro, 11引擎并行推理, HTTP探针执行器, 行为空间编译器, SQLite租约 | — L1 基本到位 |
| **L2 编排** | 4-Stage 管线 (Compile→Read→Reason→Execute→Verify) | `/goal` 目标驱动循环, Dynamic Workflows 扇出综合, Routines 定时/事件触发 |
| **L3 记忆** | — (每次 Campaign 从零开始) | STATE.md, BugPattern Skills, 行业知识库, 跨 Campaign 教训 |
| **L4 自改进** | — (没有迭代改进机制) | 评估循环 (重跑→测分→改进), 规则提炼, 视觉自检, 反模式库 |

---

## 三、核心设计：三步构建 QualiBug 自改进系统

### 3.1 原语一：Loop — 目标驱动的自改进循环

#### Fable 5 原语

```
DISCOVER → PLAN → EXECUTE → VERIFY → ITERATE
通过验证就交付，未通过就重新循环
```

关键约束：**写代码的 Agent 不是打分的 Agent**。独立验证者子 Agent 只看产物和评分标准。

#### QualiBug 落地设计

当前 QualiBug 已经有一条符合这个形状的管线：

```
Stage 0: 编译行为IR   →  DISCOVER
Stage 1-2: 读取+推理 →  PLAN  
Stage 3: 执行探针     →  EXECUTE
Stage 4: 证据判定     →  VERIFY
```

**缺失的是 ITERATE（迭代）环节**。当前管线是一条直线，不是环。

**新增架构：Discovery Mainline Loop**

```
┌──────────────────────────────────────────────────────────┐
│                 Discovery Mainline Loop                   │
│                                                          │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐              │
│  │ Round 1 │───▶│ Round 2 │───▶│ Round 3 │──▶  ...      │
│  │ RECALL  │    │ RECALL  │    │ RECALL  │              │
│  │ 4.58%   │    │ +Δ      │    │ +2Δ     │              │
│  └────┬────┘    └────┬────┘    └────┬────┘              │
│       │              │              │                    │
│       ▼              ▼              ▼                    │
│  ┌──────────────────────────────────────┐                │
│  │        L3 记忆层 (跨轮次持久化)       │                │
│  │  STATE.md + BugPattern Skills + KB   │                │
│  │  每轮结束写入 → 下轮开始加载          │                │
│  └──────────────────────────────────────┘                │
│                                                          │
│  退出条件（类比 /goal 的停止条件）：                       │
│  - 新增 Bug < ε （无显著新发现）                          │
│  - 累计轮次 >= N_MAX                                     │
│  - 外部评测 Recall >= 30% AND Precision >= 50%           │
│  - 超过每日预算上限                                       │
└──────────────────────────────────────────────────────────┘
```

**独立验证者模式（对抗验证）**：

当前 QualiBug 的 Stage 4 Verifier 负责评判自己的 Stage 1-2 推理产物。这违反了"写的不审、审的不写"的铁律。

**新增：独立验证者子管线**

```
当前（自我批评）：
  同一个 DeepSeek V4 → 生成假设 → 执行探针 → 自己验证 → 结论
  
改进后（对抗验证）：
  DeepSeek V4（制作者）→ 生成假设+执行探针
  DeepSeek V4（独立验证者）→ 只看探针结果+原始资料，不看推理过程
  → 必须引用证据链中的具体数据点
  → 引用必须可追溯到原始探针响应
  → 不能使用制作者推理中的任何结论
```

**路由策略（成本模型）**：

```
┌──────────────────────────────────────────────────────┐
│ 角色            │ 模型            │ 职责              │
├──────────────────────────────────────────────────────┤
│ 编排者          │ DeepSeek V4 Pro │ L2 编排+主链路    │
│ (Orchestrator)  │ (当前主力)      │ 循环控制+规则提炼  │
├──────────────────────────────────────────────────────┤
│ 制作者          │ DeepSeek V4 Pro │ Stage 1-2         │
│ (Writer)        │                 │ Reader+11引擎推理  │
├──────────────────────────────────────────────────────┤
│ 独立验证者      │ DeepSeek V4 Pro │ Stage 4 Verifier   │
│ (Independent    │ (独立上下文)    │ 只看产物+评分标准  │
│  Verifier)      │                 │                    │
├──────────────────────────────────────────────────────┤
│ 评分子Agent     │ 轻量模型        │ 评估循环中的       │
│ (Grader)        │ (可降级)        │ 自动评分          │
├──────────────────────────────────────────────────────┤
│ 扇出工作者      │ 轻量模型        │ 批量证据格式化    │
│ (Worker)        │ (可降级)        │ 冗余假设淘汰      │
└──────────────────────────────────────────────────────┘
```

### 3.2 原语二：Dynamic Workflows — 动态工作流

#### Fable 5 原语

系统自己生成 JS harness，按任务动态生成 agent()、parallel()、pipeline()，三种核心模式：
- **扇出-综合**：拆分 N 个独立片段，并行运行，汇总
- **对抗式验证**：每个制作者配独立验证者
- **循环直至完成**：循环直到满足停止条件

#### QualiBug 落地设计

**模式一：扇出-综合（Fan-out-Synthesize）**

当前 QualiBug 的 11 个推理引擎是并行运行，但结果合并是简单的去重+拼接。没有综合（synthesize）步骤。

```
改进后：

  行为空间承诺池
       │
       ▼
  ┌─────────────────────────────────────┐
  │  扇出：每个承诺 → 独立推理上下文     │
  │  ┌──────┐ ┌──────┐ ┌──────┐       │
  │  │ 因果  │ │ 守恒  │ │ 隔离  │  ...  │
  │  │ 引擎  │ │ 引擎  │ │ 引擎  │       │
  │  └──┬───┘ └──┬───┘ └──┬───┘       │
  │     │        │        │            │
  │     ▼        ▼        ▼            │
  │  假设A     假设B    假设C  ...      │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  综合引擎（NEW）：                    │
  │  - 去重：相同实质的假设合并           │
  │  - 冲突检测：A说应该X，B说应该Y      │
  │  - 互补检测：A+B 组合揭示新假设      │
  │  - 优先级排序：按风险族×业务价值     │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  执行队列：去重+优先级排序后的假设    │
  └─────────────────────────────────────┘
```

**模式二：对抗式验证（Adversarial Verification）**

当前 Verifier 审查制作者自己产生的假设。改造为对抗模式：

```
  制作者 Context A（Stage 1-2 完整输出）
       │
       ▼
  假设 H1, H2, H3, ... 
       │
       ├──── 只传假设+证据 → 独立验证者 Context B ──▶ 判定
       │
       └──── 假设被否 → 回写至 STATE.md:"以下推理路径产生FALSE POSITIVE"
```

**模式三：循环直至完成（Loop Until Done）**

```
  /goal "本 Campaign 在靶场 X 达到 Recall >= 30% AND Precision >= 50%"
  
  Round 1: recall 4.58%, precision 28.57%
    → 回写 STATE.md: 遗漏的 Bug 类别 [...], 误报根因 [...]
    → 更新 BugPattern Skills
    → 重新编译 Behavior IR（加入新学习的模式）
    
  Round 2: recall X%, precision Y%
    → ...
    
  Round N: 满足停止条件 → Campaign 完成
```

### 3.3 原语三：Routines — 例行程序

#### Fable 5 原语

三种触发模式：定时（cron）、API 事件触发、GitHub 事件触发。笔记本合上，系统继续在云端跑。

#### QualiBug 落地设计

**Routine 1：每日评估循环（定时触发）**

```
  /schedule daily at 2am
  
  Goal: 
  1. 对固定靶场重跑最新 Discovery Mainline
  2. 对比昨日 STATE.md，识别新增的误报和遗漏
  3. 把新失败模式提炼进 BugPattern Skills
  4. 把新发现的能力边界写入 STATE.md
  5. 生成趋势图：Recall/Precision/F1 逐日变化曲线
  6. 如果 F1 连续 3 天下降 → 告警
```

**Routine 2：客户部署自学习（事件触发）**

```
  Trigger: 新 Campaign 完成
  Goal:
  1. 提取本次 Campaign 的独特发现模式（不同于已知 Skills 的）
  2. 如果是 True Positive → 将其特征提炼为新 Skill 规则
  3. 如果是 False Positive → 记录为反模式，写入"不要这样推理"规则
  4. 更新行业知识库（Finance / E-Commerce / Healthcare / ...）
```

**Routine 3：发布门禁扫描（CI/CD 触发）**

```
  Trigger: QualiBug 自身代码 push 到 main
  Goal:
  1. 对回归靶场运行快速扫描
  2. 对比上一版本的 Recall/Precision/F1
  3. 如果退化超过 5% → 阻断合并
  4. 生成 Delta 报告：哪些引擎退化了、哪些改进了
```

---

## 四、核心设计：记忆层（五阶段递进）

### 4.1 Fable 5 记忆进阶模型

```
阶段 5 ─ 查阅：下一任务中，读规则，不重新推导
  │
阶段 4 ─ 提炼：把验证变成通用规则
  │
阶段 3 ─ 验证：把诊断变成已核对事实
  │
阶段 2 ─ 调查：弄清失败的根本原因
  │
阶段 1 ─ 失败：记录失败细节

实测数据：
  Sonnet 4.6 → 停在阶段 1
  Opus 4.7   → 停在阶段 3（验证覆盖率 7-33%，中位数 17%）
  Fable 5    → 走完全程（最佳验证覆盖率 73%）
```

### 4.2 QualiBug STATE.md 设计

```markdown
# Discovery Memory · <target_project_id>

## Verified Facts (阶段 3 — 不再猜测)

### 数据模型
- user_id 关联 auth_users.uid 而非 auth_users.id（验证来源：API GET /users 200 + DB SELECT）
- 金额单位为分而非元（验证来源：/orders 返回 {amount_cents: 1999}）
- test 数据库使用 Stripe sandbox key；生产使用真实 key（来源：enviroment config）

### 行为模型
- POST /orders 返回 201 时订单未激活，需 PATCH /orders/{id}/activate 后状态才变为 active
- 退款状态机：pending → approved → processed，中间不能跳转
- 租户隔离通过 X-Tenant-ID header 实现，header 缺失时默认租户 0

### 探针执行规则
- API 限流：100 req/min，超限返回 429 + Retry-After: 60
- 所有写操作需在非生产环境执行，且记录 before/after 快照

## General Rules (阶段 4 — 提炼的通用规则)

### 推理规则
- 当 API 文档声称 `is_admin=true` 可跳过权限检查时，立即标记为 Authorization Risk
- 金额字段使用 `int` 而非 `decimal` 时，优先检查 Conservation（整数除法的舍入丢失）
- 多步骤业务操作（创建订单→支付→发货→签收）中每次状态转换都要检查幂等性
- GET 端点返回敏感字段时，标记为 Privacy Risk（即使文档声称"前端会过滤"）

### 执行规则
- 并发测试的 window_size 默认 100ms，RTT > 50ms 时调整为 200ms
- 分页 API 先取 page=1, page_size=1 确认总数，再全量取以控制探针数量

## Bug Detection Patterns (Skills 化的已知模式)

### Pattern: IDOR via Sequential ID
- 触发条件：资源 ID 为自增整数 + 无独立鉴权
- 验证方法：切换租户/用户后访问相邻 ID
- 上次命中：2026-07-15，E-Commerce 靶场，检出 3 个
- 引擎：Authorization + Isolation 联合

### Pattern: Currency Rounding Loss
- 触发条件：金额字段 int 类型 + API 含 split/distribute 操作
- 验证方法：N 等分后求和，检验总和 == 原始值
- 上次命中：2026-07-16，Finance 靶场，检出 2 个
- 引擎：Conservation

### Pattern: Missing Optimistic Lock
- 触发条件：PUT/PATCH 端点无 ETag/If-Match/version 字段
- 验证方法：并发 PATCH 同一资源，检查后写入是否覆盖前写入
- 上次命中：2026-07-14，Healthcare 靶场，检出 1 个
- 引擎：Concurrency

## Open Failures (阶段 1→2 — 调查中)

### 2026-07-17 · False Positive: Order Status Transition
- 现象：Conservation 引擎误报订单支付金额不一致
- 初步判断：订单存在 partial_refund 状态，引擎未理解退款语义
- 复现步骤：见 debug/fp-order-transition.md
- 下一步：确认是 Behavior IR 编译问题还是推理规则缺失

### 2026-07-17 · Missed Bug: Tenant Data Leak via Export
- 现象：靶场已知 Bug 未被任何引擎检测到
- 初步判断：Export 端点的响应格式为 CSV，当前管道只解析 JSON
- 下一步：扩展探针解析器支持 CSV/XML 响应格式

## Lessons Learned (阶段 4 提炼)

- DeepSeek V4 在复杂 SQL 生成时偶尔遗漏 JOIN 条件，需要 Stage 2 输出中包含完整的 SQL 验证步骤
- HTTPS 自签名证书导致约 3% 探针失败，已通过在 sandbox executor 中添加 verify=False 解决
- Windows 环境下 Docker 网络的 localhost 不能直接访问 host，需要用 host.docker.internal

## Last Session (阶段 5 — 续上)

2026-07-17 02:30 UTC · 3 轮完成 · 靶场 X
Recall: 4.58% → 8.21% (+3.63pp) · Precision: 28.57% → 41.33% (+12.76pp)
新增 Pattern: Currency Rounding Loss（Skill 已更新）
未解决的 FP: Order Status Transition（debug 文件已创建）
下次优先：扩展 CSV 响应解析器，重新扫描 Healthcare 靶场的 Tenant 隔离
```

### 4.3 Skills 化：BugPattern 知识库

参考 Fable 5 的 "把教训写进 Skill，而不只是聊天" 原则：

**Skills 目录结构：**
```
platform_workspace/skills/
├── bug_patterns/
│   ├── authorization_idor.md       ← IDOR 检测模式
│   ├── conservation_rounding.md    ← 金额舍入错误模式
│   ├── concurrency_race.md        ← 竞态条件模式
│   ├── isolation_tenant_leak.md   ← 租户数据泄漏模式
│   ├── validation_input_missing.md ← 输入校验缺失模式
│   └── ...
├── industry_kb/
│   ├── finance_rules.md           ← 金融行业特定规则
│   ├── ecommerce_rules.md         ← 电商行业特定规则
│   ├── healthcare_rules.md        ← 医疗行业特定规则
│   └── saas_general_rules.md      ← SaaS 通用规则
├── anti_patterns/
│   ├── fp_conservation_partial_refund.md  ← 已知误报模式
│   ├── fp_temporal_timezone_mismatch.md   ← 已知误报模式
│   └── ...
└── eval_suites/
    ├── smoke_eval.jsonl           ← 快速冒烟评估案例
    ├── regression_eval.jsonl      ← 回归评估案例
    └── gate_d_eval.jsonl          ← Gate D 门禁案例
```

**Skills 复利契约**：

每次 Campaign 运行后：
1. 每个 True Positive 的新模式 → 检查是否已有 Skill 覆盖 → 若无，创建新 Skill
2. 每个 False Positive → 写入 `anti_patterns/`，下次 Campaign 加载时预过滤
3. 每个 Missed Bug（已知存在但未检出）→ 分析根因，更新对应 Skill
4. 跨行业部署时 → 检查 `industry_kb/` 中是否有可迁移规则

---

## 五、核心设计：自改进层（评估循环 + 规则提炼）

### 5.1 评估循环

这是 Fable 5 框架中最关键的一层。当前 QualiBug 没有评估循环——每次 Campaign 的结果不被用于系统性改进。

**新增：Discovery Eval Loop**

```
┌─────────────────────────────────────────────────────────────┐
│                    Discovery Eval Loop                       │
│                                                             │
│  触发方式：                                                  │
│  - Routine: 每日凌晨 2:00 自动运行                           │
│  - Manual: 开发者手动触发 /eval 命令                         │
│  - CI: 代码 push 时自动运行                                  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Step 1: 对固定评估靶场运行完整 Campaign               │    │
│  │ - 使用相同的 source materials                         │    │
│  │ - 使用相同的配置（除变更部分外）                        │    │
│  │ - 生成完整的 findings + evidence                      │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Step 2: 独立评分（对抗验证模式）                       │    │
│  │ - 评分者（独立 DeepSeek V4 上下文）                    │    │
│  │ - 只接收：target ground truth + findings + evidence   │    │
│  │ - 不看：Stage 1-2 推理过程 + 上一轮结果               │    │
│  │ - 产出：Recall / Precision / F1 / Risk Family 细分    │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Step 3: 比较 & 诊断                                   │    │
│  │ - 对比上一轮评分 → Delta 报告                         │    │
│  │ - 逐 Bug 分析：哪些被修复、哪些退化                    │    │
│  │ - 逐引擎分析：causality +Xpp, invariance -Ypp         │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Step 4: 规则提炼 & 回写                                │    │
│  │ - 新增 True Positive → 提炼 Pattern → 写入 Skill      │    │
│  │ - 新增 False Positive → 分析根因 → 写入 anti_pattern  │    │
│  │ - 新 Missed Bug → 调查原因 → 更新 Skill + STATE.md    │    │
│  │ - 更新 STATE.md 中的趋势数据                          │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Step 5: 门禁判定                                       │    │
│  │ - F1 连续 3 天上升 → 保持方向                          │    │
│  │ - F1 连续 3 天下降 → 告警 + 暂停自动更新规则            │    │
│  │ - Recall >= 30% AND Precision >= 50% → Gate D 通过    │    │
│  │ - 生成 Daily Digest → 写入 STATE.md + 通知             │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  关键约束：                                                  │
│  - 评分者 NEVER 看到制作者的推理过程                         │
│  - 规则写入必须经过审核（自动化门禁 + 人工抽查）             │
│  - 每日预算上限：防止无限循环烧 token                       │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 视觉自验证

Fable 5 能够用自己的视觉能力检查 UI 输出。QualiBug 可以将其应用于证据验证：

```
  制作者 Agent → 执行探针 → 生成 HTML 证据报告
       │
       ▼
  视觉验证者 Agent → 截图 HTML 证据报告
       │
       ▼
  对照检查：
  - 证据链完整性（request → response → expected → actual 完整？）
  - 数据一致性（JSON 中的值和 HTML 显示的表格值一致？）
  - 脱敏合规（截图中是否有未脱敏的密码/JWT/密钥？）
  - UI 可读性（客户交付物是否排版正常？）
```

### 5.3 Eval Suite 设计

```
benchmark_evaluator/
├── eval_targets/
│   ├── mes_buglab/           ← 主评估靶场（已存在）
│   ├── ecommerce_sandbox/    ← 电商沙箱靶场
│   └── finance_sandbox/      ← 金融沙箱靶场
├── ground_truth/
│   ├── mes_known_bugs.json   ← 已知 Bug 真值
│   └── .../
├── eval_suites/
│   ├── smoke_daily.jsonl     ← 每日快速评估（~30 分钟）
│   ├── regression_weekly.jsonl ← 每周回归评估（~4 小时）
│   └── gate_d_full.jsonl     ← Gate D 完整门禁
└── scoring/
    ├── scorer.py             ← 独立评分器
    └── delta_reporter.py     ← Delta 对比报告
```

---

## 六、商业化加速：从 7.89% F1 到自动驾驶

### 6.1 复利增长模型

当前 F1 = 7.89% 是一个"起点"，不是"天花板"。自改进系统的本质是让这个数字逐轮复利增长：

```
Round  0: F1 = 7.89%   ← 当前基线（无记忆、无迭代、无Skills）
Round  5: F1 ≈ 15-20%  ← 基础记忆层生效（不再从零开始）
Round 10: F1 ≈ 25-30%  ← Skills 开始发挥（已知模式复用）
Round 20: F1 ≈ 35-45%  ← 评估循环稳定运转（规则持续提炼）
Round 30: F1 ≈ 50%+    ← Gate D 达标，进入受控试点
```

**判断依据**：
- Fable 5 在 Continual Learning Bench 中验证覆盖率 73%，相当于把零记忆的 Sonnet 从 ~0% 提升到 73%
- 核心变量不是模型权重（权重固定），而是系统记忆与规则积累
- QualiBug 的 11 引擎架构本身就是"扇出"模式，加记忆层后每个引擎都能独立积累经验

### 6.2 商业化三阶段路线图

#### Phase 1: Memory Foundation（1-2 周）

**目标**：让 QualiBug 从一个"无状态管道"变成"有记忆的系统"

| 动作 | 产出 | 商业价值 |
|---|---|---|
| 实现 STATE.md 读写 | 每 Campaign 结束自动更新，新 Campaign 自动加载 | 知识不丢失，客户靶场的教训迁移到下一个靶场 |
| 建立 BugPattern Skills 框架 | 10 个初始 Skills（IDOR, Conservation, Concurrency, ...） | Demo 时可展示"我们的系统有跨行业学习能力" |
| 独立评分器 | 对抗验证评分，不接触制作者上下文 | Precision 立即可见的改善（减少自欺欺人） |
| Fan-out-Synthesize | 11 引擎结果合并去重+优先级排序 | 减少冗余探针，降低每次 Campaign 的 token 成本 |

#### Phase 2: Compound Loop（3-4 周）

**目标**：让系统逐轮自我改进，Recall/Precision 进入可商业化区间

| 动作 | 产出 | 商业价值 |
|---|---|---|
| Discovery Mainline Loop | /goal 驱动的多轮 Campaign | F1 从 7.89% 逐步提升 |
| Daily Eval Routine | 每日凌晨自动评估+趋势追踪 | 内部指标透明，可以给投资人看增长曲线 |
| Anti-Pattern Library | 已知误报模式自动预过滤 | Precision 大幅提升 |
| Cost-Aware Routing | 按任务复杂度路由模型 | token 成本可控 |

#### Phase 3: Autonomous Commercial（5-8 周）

**目标**：Gate D 达标，进入受控私有试点

| 动作 | 产出 | 商业价值 |
|---|---|---|
| Gate D 达标 | Recall >= 30%, Precision >= 50% | 正式商业化资格 |
| Customer Deployment Routine | 客户部署后自动学习其特定模式 | 每部署一家 → 系统更强 → 下家更好卖 |
| Industry Knowledge Base | Finance / E-Commerce / Healthcare 专用规则 | 行业垂直销售素材 |
| Release Gate Integration | CI/CD 触发自动扫描+安全门禁 | 帮助企业把 QualiBug 嵌入 DevOps 流程 |

### 6.3 商业叙事转变

| 当前叙事（弱） | 改进后叙事（强） |
|---|---|
| "我们帮你找 Bug" | "我们帮你建立一个越用越聪明、自动发现 Bug 的系统" |
| "目前 F1 只有 7.89%" | "系统每部署一家客户，F1 就自动提升；你的靶场会让整个平台更强" |
| "全行业适用"（是口号） | "金融、电商、医疗已验证的规则库；新行业 48 小时冷启动" |
| "一次扫描" | "持续守护：每日自动评估、CI 触发、生产前门禁" |

---

## 七、实施优先级矩阵

```
                    高商业价值
                        │
         ┌──────────────┼──────────────┐
         │  P0: 立刻做   │  P1: 下周做  │
         │              │              │
  低 ────┼──────────────┼──────────────┼──── 高实现成本
  实现   │              │              │
  成本   │  P2: 有空做   │  P3: 规划中  │
         │              │              │
         └──────────────┼──────────────┘
                        │
                    低商业价值
```

| 优先级 | 条目 | 实现成本 | 商业回报 | 理由 |
|---|---|---|---|---|
| **P0** | 独立验证者 | 中（1-2天） | 极高 | 立即减少自欺欺人，Precision 可预期提升 |
| **P0** | STATE.md | 低（半天） | 极高 | 开启记忆能力的最小可行步骤 |
| **P0** | Fan-out-Synthesize | 中（1-2天） | 高 | 减少冗余和冲突，提高探针效率 |
| **P1** | BugPattern Skills 框架 | 中（2-3天） | 高 | 建立"越用越聪明"的产品故事 |
| **P1** | Discovery Mainline Loop | 高（3-5天） | 极高 | 核心复利引擎，但依赖 P0 先落地 |
| **P1** | Daily Eval Routine | 中（2天） | 高 | 内部指标透明+Demo 素材 |
| **P2** | Anti-Pattern Library | 中（2天） | 中 | 需要一定量的 Campaign 数据积累 |
| **P2** | Cost-Aware Routing | 低（1天） | 中 | 降低成本，非核心路径 |
| **P3** | 视觉自验证 | 高（3-5天） | 中 | 需要截图+多模态能力 |
| **P3** | Customer Deployment Routine | 高（5-7天） | 极高 | 需要 Phase 1-2 先完成 |
| **P3** | Industry KB | 高（持续） | 高 | 需要多客户数据积累 |

---

## 八、快速启动：明天就能做的三件事

### 1. 实现 STATE.md（半天）

在 `platform_workspace/` 下创建 `discovery_memory/STATE.md`，结构参照第 4.2 节设计。

在 `discovery_mainline.py` 或 Campaign 管线的末尾增加一个 hook：`_persist_campaign_memory()`，写入本轮的关键发现。

在下次 Campaign 启动时增加一个前置步骤：`_load_campaign_memory()`，把 STATE.md 的内容注入行为空间编译阶段。

### 2. 拆分 Stage 4 Verifier（1 天）

当前 Stage 4 Verifier 接收 Stage 1-2 的完整输出。修改为：
- 制作者上下文：Stage 1-2（不变）
- 验证者上下文：只接收 (假设, 探针结果, 原始资料)，不接收推理过程
- 验证者 Prompt 改写为"你是独立的审计者，请仅基于以下证据和原始资料判断假设是否成立"

### 3. 添加 /goal 命令（2 天）

在 `qualibug` CLI 或 Campaign API 中添加一个 `/goal` 接口：

```
POST /api/v1/campaigns/goal
{
  "target_id": "mes_buglab_v1",
  "goal": {
    "metric": "f1_score",
    "target": 0.15,
    "max_rounds": 5,
    "timeout_hours": 24
  }
}
```

每轮结束后自动评估 F1，未达标则加载上一轮的 STATE.md 和 Skills，开始下一轮。

---

## 九、参考：Fable 5 14 步 → QualiBug 落地对应表

| Fable 5 步骤 | QualiBug 对应 | 状态 |
|---|---|---|
| 01. Mythos 级模型 | DeepSeek V4 Pro 作为编排者 | ✅ 已有 |
| 02. 自改进 ≠ 自学习 | 系统积累记忆，模型权重不变 | ✅ 理解 |
| 03. 四层复利堆栈 | L1-L4 映射完成 | ✅ 设计 |
| 04. 成本-能力矩阵 | DeepSeek V4 Pro × 轻量模型路由 | 🔧 部分 |
| 05. /goal vs Outcomes | Discovery Mainline Loop | ❌ 待建 |
| 06. 验证者子 Agent | 独立 Verifier 上下文 | ❌ 待建 |
| 07. Dynamic Workflows | Fan-out-Synthesize + 对抗验证 | ❌ 待建 |
| 08. Worktrees | Git worktree 隔离并行 Campaign | 🔜 计划中 |
| 09. Routines | Daily Eval + CI 触发扫描 | ❌ 待建 |
| 10. 五阶段记忆 | STATE.md 五段结构 | ❌ 待建 |
| 11. 状态文件 | `discovery_memory/STATE.md` | ❌ 待建 |
| 12. Skills 复利 | `bug_patterns/` + `industry_kb/` | ❌ 待建 |
| 13. 视觉自验证 | 证据报告截图检查 | 🔜 远期 |
| 14. 安全边界 | 生产环境 fail-closed（已有） | ✅ 已有 |

---

> **自改进是系统的属性，不是模型的属性。构建系统。**
>
> — 改编自 @0xCodez
