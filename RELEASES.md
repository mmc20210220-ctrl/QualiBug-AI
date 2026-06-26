# Phase75 — Agent Experiment Compiler & Safe Evidence Dispatch

- Converts persistent Agent Loop hypotheses into reproducible experiment packets.
- Stores packet state and executor receipts in the same canonical SQLite ledger.
- Supports explicit sandbox fixture catalogs and blocks unresolved dependencies rather than guessing data.
- Delegates all writes to the existing approved disposable-sandbox executor; runtime evidence still requires human confirmation.


## Phase74 — Autonomous Business Bug Discovery Agent Loop

- Added a single canonical SQLite ledger for unknown business hypotheses, evidence, approval blockers, human verdicts and regression guards.
- Added CSV review projection and next-best-experiment manifest.
- Reused business world model, document contract compiler and concurrency sandbox planning rather than creating a new parallel detection stack.
- Preserved the rule that static and LLM candidates are never formal Bugs without deterministic runtime evidence and human review.

# Phase73 — Document Contract Compilation and AST Source Evidence

- Version: `Phase73`
- Scope: PRD/API-derived sandbox contract compilation and optional
  comment-free, method-sensitive AST source evidence.
- Release state: Engineering validated. Static evidence remains separate from
  deterministic runtime replay for stateful business effects.

# Phase71 — Explicit Project-Scoped Data Isolation

- Added an explicit GET-only project/workspace selector Oracle to the existing
  consistency/isolation engine.
- Detected and remediated an authenticated cross-project report-read P0 in the
  private pilot service.
- Required trusted reverse-proxy project allow-lists on public/private-cloud
  bindings and extended self-dogfood coverage to prove the boundary.

# Phase69 — Evidence-First LLM Hypothesis Boundary Unification

- Unified all legacy business reasoning engines behind one LLM-output adapter.
- Model suggestions are stored only as bounded, redacted `unverified_hypothesis` records and require deterministic read-only replay.
- Prevented raw model output from changing formal findings, severity counts, evidence registries, learning memory, validation queues or release gates.
- Verified the full regression suite as `95/95 passed` in a measured isolated run.

# Phase65 — Financial Ledger Conservation

- Reused the business-causality conservation engine for explicit voucher-level double-entry and accounting-period roll-forward contracts.
- Added P0 evidence for unbalanced journals, period opening/closing discontinuity and invalid balance movement formulas.
- Enforced the shared safety verdict for direct causality execution and downgraded LLM output to unverified hypotheses only.

# Phase64 — Role Access Boundary and Field-level Authorization

- Reused the consistency/isolation engine for explicit role access and field-redaction Oracle contracts.
- Added P0 evidence for route-level authorization bypass, non-empty restricted views and forbidden field exposure.
- Applied the shared live-execution safety gate to direct consistency-engine runs; LLM suggestions remain unverified hypotheses.

# Phase63 — Temporal Partition Conservation

- Reused the metamorphic differential engine to prove that explicitly declared
  left-closed/right-open business time windows conserve the whole range.
- Added complete-response safeguards to prevent partial pages from becoming
  false-positive business defects.
- Prioritized verified range-boundary counterexamples as high-value business
  risk without adding a parallel runtime or UI layer.

# Phase62 — Adversarial Metamorphic Differential Verification

- Added explicit business-partition conservation as a bounded read-only
  differential relation.
- Prevented LLM suggestions from being promoted into evidence-backed defects.
- Enforced the shared non-production safety boundary before live discovery
  reachability and login activity.
- Added the missing GitHub release-verification workflow.

## Phase61 — 企业级产品 UI 与运营体验统一

- 使用无依赖共享 UI 壳统一试点运营、控制中心、知识中心、发布门禁和多行业评测。
- 新增 `/control-plane`、`/knowledge`、`/release`、`/benchmark` 私有服务页面，以及对应只读 JSON 查询入口。
- 统一项目上下文、导航、风险状态、卡片、表格、响应式布局和快照导出。
- 不改变权限、审批、凭证引用、审计链、生产保护或现有业务质量能力。
- 验证：完整回归 71/71 通过，核心产品页与只读 API 本地验证通过。


## Phase60 — 企业试点运行时与私有化交付底座

- 新增项目级运行配置、连接器登记/同步、幂等任务队列、独立审批与运行审计。
- 复用 Phase58/59 的企业知识与控制平面，不新增平行业务模型或文档库。
- 新增本地私有服务、Docker Compose 部署模板和中文试点运营看板。
- 生产类环境的写入、造数和直接缺陷执行继续被运行时拦截。
- 验证：专属测试 5/5 通过，安全本地试点 Demo 通过且网络请求 0。


## Phase76 — Agent Business-Flow Orchestrator

- Adds project-mapped, multi-step business-flow experiments to the persistent Agent Loop.
- Reuses the Phase74 SQLite ledger for flow packets, receipts, evidence and review routing.
- Requires explicit flow mappings and existing disposable-Sandbox approval before writes.
- Supports captures, cross-step template values, before/after snapshots and rollback/conservation assertions.
- Does not track a known Bug total or auto-confirm observations.

# Releases

默认提供本地完整交付压缩包与同名 `.sha256` 校验文件；不自动上传 GitHub。

当前版本：

- Version: `Phase76`
- Title: `Agent Business-Flow Orchestrator`
- Asset: `QualiBug_AI_Enterprise_Edition_Phase76_AgentLoop_FlowOrchestrator_Complete.zip`
- Integrity: 使用同名 `.sha256` 文件校验
- Release state: Engineering validated; multi-step flow evidence is verified only in an isolated disposable Sandbox

上一版本：

- Version: `Phase70`
- Title: `Inventory Reservation and Available-Stock Conservation`
- Asset: `QualiBug_AI_Enterprise_Edition_Phase70_Complete.zip`
- Integrity: 使用同名 `.sha256` 文件校验

上一版本：

- Version: `Phase67`
- Title: `Evidence-First LLM Oracle Compilation & Scan Aggregate Repair`
- Asset: `QualiBug_AI_Enterprise_Edition_Phase67_Complete.zip`
- Integrity: 使用同名 `.sha256` 文件校验

上一版本：

- Version: `Phase65`
- Title: `Financial Ledger Conservation Oracle`
- Asset: `QualiBug_AI_Enterprise_Edition_Phase65_Complete.zip`
- Integrity: 使用同名 `.sha256` 文件校验

上一版本：

- Version: `Phase64`
- Title: `Role Access Boundary and Field-level Authorization`
- Asset: `QualiBug_AI_Enterprise_Edition_Phase64_Complete.zip`
- Integrity: 使用同名 `.sha256` 文件校验

更早版本：

- Version: `Phase63`
- Title: `Temporal Partition Conservation Oracle`
- Asset: `QualiBug_AI_Enterprise_Edition_Phase63_Complete.zip`
- Integrity: 使用同名 `.sha256` 文件校验

更早版本：

- Version: `Phase60`
- Title: `Enterprise Pilot Runtime & Private Deployment`
- Asset: `QualiBug_AI_Phase60_Enterprise_Pilot_Runtime.zip`
- Integrity: 使用同名 `.sha256` 文件校验

更早版本：

- Version: `Phase59`
- Title: `Enterprise TestOps Control Plane`
- Asset: `QualiBug_AI_Phase59_Enterprise_TestOps_Control_Plane.zip`

更早版本：

- Version: `Phase58`
- Title: `Enterprise Knowledge Unified Ingestion`
- Asset: `QualiBug_AI_Phase58_Enterprise_Knowledge_Unified_Ingestion.zip`
