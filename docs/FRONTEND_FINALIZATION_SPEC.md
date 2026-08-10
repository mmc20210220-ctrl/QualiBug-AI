# QualiBug 前端最终收口 SPEC

状态：P0/P1 可信主链已实现；完整环境门禁仍受 GitHub Actions `startup_failure` 阻断  
当前实现分支：`main`  
历史 P0 分支：`frontend/finalization-p0-20260810`

## 1. 背景

QualiBug 前端已经具备系统接入、企业资料、运行中心、问题清单、证据中心、发布门禁、覆盖矩阵和后台任务等完整页面。

当前阶段不再扩张页面数量，而是把既有能力收敛为一条无需人工讲解的客户主链：

`登录 -> 选择/创建客户 -> 接入系统 -> 导入资料 -> 运行前检查 -> 一键检测 -> 查看结论 -> 查看证据 -> 分派整改 -> 回归 -> 发布决策 -> 安全分发`

本 SPEC 以四个结果为中心：

1. **易用性**：用户始终知道当前状态和下一步。
2. **价值表达**：首屏先给业务结论，再给技术细节。
3. **分发能力**：报告、证据、协作和只读分享成为产品传播入口。
4. **可信交付**：不出现错误跳转、乱码展示、虚假动作、可预测凭据、假进度和前端伪协作状态。

## 2. 产品原则

### 2.1 单一主链

普通用户的核心任务只有：接入、检测、处理结果。Scope、Source、Fixture、Connector、运行模板、覆盖矩阵和后台任务属于高级能力，不应抢占首次体验主路径。

### 2.2 结果优先

Dashboard 第一屏优先回答：

- 本轮有没有已确认问题；
- 是否存在 P0 阻断；
- 影响哪些业务模块；
- 是否建议发布；
- 下一步应该做什么。

发现漏斗、主链契约、内部技术诊断和证据治理细节放在第二屏或折叠详情中。

### 2.3 真实状态

以下状态必须严格区分：

- 尚未执行；
- 正在准备；
- 服务端已确认真实扫描；
- 主链真实阶段进行中；
- 某阶段尚未进入或尚未实时上报；
- 某阶段已由权威结果对象确认完成；
- 正在等待最终回执；
- 部分覆盖；
- 执行被阻断；
- 执行异常；
- 未发现已确认问题；
- 已发现并形成客户可交付证据。

空结果不得展示为“没有问题”。服务端未提供的内部阶段不得根据计时器或百分比伪造进度。发布门禁给出 verdict 不等于最终报告已经完成，交付阶段只能在最终结果/报告收口后进入 completed。

### 2.4 动作必须真实

按钮名称必须与真实行为一致。普通页面跳转不得伪装成报告导出、任务创建或外部分享。

### 2.5 默认安全

- 不生成可预测默认密码；
- 分享结果默认脱敏；
- 生产环境不暴露测试凭据；
- 外部报告不包含原始密钥、Cookie、Token、请求体和数据库凭据；
- 公开分享只能读取冻结的只读快照；
- 分享 Token 不写入数据库明文、浏览器本地存储、页面路径或查询参数；
- 测试环境和只读熔断状态明确展示。

### 2.6 自动验证真相与人工协作分离

自动 Finding 的证据、结论和验证状态仍由真实扫描 / Replay / 回归链负责；人工协作只能维护负责人、处理状态、修复版本、研发反馈、风险接受、误报评审和外部任务引用，不得把自动失败手工改成“已修复”。

## 3. 目标信息架构

### 3.1 普通用户一级入口

1. 价值总览
2. 开始检测
3. 问题与证据
4. 发布与回归
5. 项目设置

现有路由保持兼容。覆盖矩阵、后台任务、Connector 明细和技术诊断继续存在，但降低视觉优先级。

### 3.2 首次接入四步

1. 接入被测系统：服务地址、测试账号、可选数据库。
2. 导入企业资料：PRD、接口规范、数据库结构、历史缺陷等。
3. 运行前检查：自动检查系统、凭据、资料、Fixture 和安全条件。
4. 一键真实检测：真实执行并如实展示执行、阻断、递延和异常。

### 3.3 结果闭环

1. 查看已确认问题；
2. 查看完整证据与回放；
3. 分配负责人并记录整改状态；
4. 记录修复版本与研发反馈；
5. 发起 Release 回归；
6. 真实回放 / 回归确认自动验证状态；
7. 形成发布建议；
8. 通过脱敏报告、证据包或临时只读链接安全分发。

## 4. P0 已实现范围

### P0-1 首次接入导航一致性

- JourneyStrip 的“导入企业资料”进入 `/materials`；
- “接入被测系统”继续进入 `/settings`；
- Dashboard 企业理解区的“完善系统接入”继续进入 `/settings`，因为该动作同时处理环境、账号和凭据阻断；
- 所有跳转保持当前项目参数；
- 不新增第二套接入状态。

### P0-2 用户可见中文完整性

- 对 Materials 快速连接遗留乱码在统一 Toast 展示边界进行兼容转换；
- 覆盖空 URL、非法 URL、非 HTTP(S)、Manifest 缺少范围字段和无可用 URL 连接器；
- 不为了修正展示文案重写复杂 Materials 主链；
- 已有契约确保已知乱码均有可读中文映射。

### P0-3 动作真实性

- 发布门禁底部按钮实际返回 Dashboard，因此文案为“返回价值总览”；
- Dashboard 保留真实 HTML 报告生成逻辑；
- 聚合报告支持打印 / 保存为 PDF；
- 不把页面跳转伪装成“导出报告”。

### P0-4 工作区默认凭据安全

- 移除 `${tenantId}123` 确定性密码；
- 使用 `window.crypto.getRandomValues` 生成 24 位临时密码；
- 同一临时密码贯通创建工作区和首次自动登录；
- 临时密码不写入日志、URL、本地存储或页面状态。

### P0-5 技术口径收口

- README 与实际 Vite + React 19 + React Router 架构一致；
- 删除 Next.js/RSC 和 `/_next` 等失效说明；
- 明确开发端口 5174、后端端口 8088 和 `/api` 代理口径；
- 以 `npm run ci:gate` 作为统一前端门禁。

### P0-6 自动回归契约

`test:frontend-finalization` 已接入 `ci:gate`，锁定：

- 企业资料步骤路由指向 `/materials`；
- ReleaseGate 不存在“导出报告”假动作；
- Settings 不存在可预测默认密码；
- 安全临时密码贯通创建和自动登录；
- Bearer Token 表单 setter 不回归；
- 已知乱码具备可读中文映射；
- README 不再声明 Next.js/RSC。

## 5. P1 已实现范围

### P1-1 接入向导 — 已实现

已完成：

- Settings 顶部按真实状态展示系统地址、测试账号、企业资料、可选数据库；
- 必需步骤按真实完成度计算；
- 服务保存后自动刷新接入完成度；
- `sources` 与 `source_inventory` 两种资料口径兼容；
- 新建服务表单自动保存本次浏览器会话中的**非敏感草稿**；
- 账号密码、Bearer Token、API Key、数据库用户名和数据库密码不进入草稿；
- 高级配置保持折叠；
- `test:settings-onboarding` 已进入统一门禁。

### P1-2 运行过程可视化 — 六阶段原生真实边界已打通

已完成：

- 前端请求层发出真实 `submitted / completed / failed` 生命周期事件；
- 后端复用项目级 `project_scan_lease` 作为“服务端是否真的在扫描”的事实源；
- `/api/v1/continuous/status` 只读投影当前 live scan lease；
- 外部状态不暴露 lease token、PID、thread ID、tenant ID 和 actor 原始信息；
- 前端在 `submitted` 状态每秒读取服务端真实状态；
- 明确区分“请求已提交但服务端尚未登记扫描”和“服务端已确认真实扫描”；
- dead/stale lease 不再显示成正在运行；
- 新增 `qualibug.scan-stage-progress.v1` 项目级原子阶段快照，只有真实主链函数或权威结果投影可以推进状态；
- `enterprise_understanding` 与 `scenario_planning` 由真实 `build_discovery_plan()` 主链调用进入/返回时上报；二者在同一规划函数内真实重叠，因此 UI 不伪装成顺序百分比；
- `runtime_execution` 由真实 `run_experiment_candidate()` 进入/返回时上报；
- `evidence_collection` 在 experiment runner 开始观察时进入 active，并在真实 `_persist_execution_evidence()` 进入证据归一化/持久化时继续保持 active；证据包持久化成功后 completed，持久化异常则 failed；
- `test_data_assessment` 在真实 `_test_data_receipt_verifier(root, project)` 构造权威收据校验器时进入 active；`test_data_plan` 形成并进入 `_evaluate_release_gate()` 后，根据真实计划状态转为 completed / blocked / failed；
- `delivery_finalization` 在真实 `_evaluate_release_gate()` 进入时 active；门禁引擎异常时 failed；门禁成功返回后即使 verdict 为 `fail/blocked` 也继续保持 active，因为最终报告和结果仍在收口；
- first-class `scan_stage_finalization` post-hook 在 public `scan()` 返回调用方之前消费最终 `evidence_bundle / test_data_plan / release_gate / report_path`，只在最终结果/报告收口后把 `delivery_finalization` 标为 completed；
- 发布结论 `fail/blocked` 是业务门禁结论，不等于阶段执行失败；只有 release gate 自身 `failed/error/invalid` 或执行异常才把 delivery stage 标记为 failed；
- stage snapshot 只有在 live scan lease 存在时才通过状态 API 暴露，历史 stage 文件不会冒充当前扫描；
- 未知 stage / 非法百分比状态会被后端拒绝；pending 阶段不会因为时间经过自动推进；
- 阶段遥测持久化为 fail-soft：写盘失败记录 warning，但绝不能改变真实扫描结果；编程错误（未知 stage、假百分比）仍然 fail loud；
- post-hook 同样属于非权威可观测投影，失败不得遮蔽核心 scan result；
- completed 后依据真实 `campaign_status / test_data_plan / execution_status / HAR evidence / grade / coverage` 展示最终结果；
- blocked、partial、plan_only、有 Finding 等结果不会因为 HTTP 200 被错误显示为绿色成功；
- `test:run-lifecycle`、`test:live-scan-status`、`tests/test_live_scan_status_projection.py`、`tests/test_scan_stage_progress.py`、`tests/test_scan_stage_finalization_hook.py` 与 `tests/test_scan_execution_outcome_stage_progress.py` 已建立。

当前六阶段权威边界：

- **企业资料理解**：`build_discovery_plan()` 提供 active + completed / failed；
- **场景与义务生成**：`build_discovery_plan()` 提供 active + completed / failed；
- **测试数据准备 / 就绪核验**：`_test_data_receipt_verifier()` 提供 active，`_evaluate_release_gate()` 根据真实 `test_data_plan` 提供 completed / blocked / failed；
- **真实探针执行**：`run_experiment_candidate()` 提供 active + completed / failed；
- **结果观察与证据收集**：experiment runner 提供观察开始，`_persist_execution_evidence()` 提供真实持久化 active + completed / failed；
- **交付门禁与报告**：`_evaluate_release_gate()` 提供 active / failed，最终 `scan_stage_finalization` 在 report/result 收口后提供 completed。

原则：所有阶段都由拥有真实执行权威和天然 `root/project` 作用域的后端函数推进；不得用全局变量、线程本地、前端计时器、固定秒数或虚构百分比模拟阶段。阶段遥测只解释“真实走到哪里”，不参与 Finding、Release Gate 或扫描成功与否的判定。

### P1-3 角色化价值视图 — 已实现

- 管理视图：优先发布风险、阻断等级和项目决策；
- 测试视图：优先证据、验收和回归闭环；
- 技术视图：直接展开既有真实技术诊断区；
- 三种视图复用同一份结果，只改变信息优先级，不建立三套业务状态；
- 视图偏好仅作为浏览器会话体验设置，不作为业务数据；
- `test:dashboard-role-views` 已进入统一门禁。

### P1-4 Finding 协作闭环 — 已实现后端 SSOT

已完成：

- 复用 SQLite `findings.status` 作为**自动验证状态** SSOT；
- 新增稳定 `finding_persistence_id` crosswalk，把 display-ready Finding 安全映射回 SQLite Finding；
- 身份映射要求唯一，无法唯一匹配时 fail closed，不通过标题猜测写入相似 Bug；
- 修复 Replay 结构性问题：前端仍可按 display ID 发起回放，但明确结论写回 SQLite 时使用稳定 `finding_persistence_id`；
- 无法解析持久化 ID 时，Replay 得到明确结论也不会误写其他 Bug；
- 新增独立 `finding_collaboration` 持久化表；
- 支持人工处理状态、负责人、修复版本、研发反馈、风险接受、误报评审、处置说明、外部任务链接；
- `external_issue_url` 只接受绝对 HTTP(S) URL；
- 人工协作 API 明确禁止修改 `verification_status`；
- 前端保存后重新拉取服务端真相，不用 localStorage / sessionStorage 伪装企业协作；
- Finding 卡片同时展示自动验证状态、真实回归状态和人工处理状态；
- 已新增 SQLite 集成测试与 `test:finding-collaboration` 门禁。

状态轴定义：

- `verification_status`：`open / resolved / falsified`，只由真实执行 / Replay / 回归链负责；
- `handling_status`：`new / triaged / in_progress / fix_ready / risk_review / false_positive_review`，由人工协作维护；
- `disposition`：`none / accepted_risk / false_positive`，是人工处置记录，不覆盖自动验证真相。

### P1-5 证据和报告分发 — 核心安全分发已实现

已完成：

- 单问题脱敏文本证据包；
- 单问题脱敏打印页 / 浏览器保存 PDF；
- 聚合 HTML 风险报告；
- 聚合报告打印 / 保存 PDF；
- 聚合报告和单问题打印页统一进行动态文本 HTML 转义；
- Authorization、Bearer、Cookie、Set-Cookie、Token、API Key、Password、敏感 Query 参数、JWT 等模式脱敏；
- 原始 curl 仅保留在登录后的证据中心，不直接进入外发证据包；
- 新增**有时效、可撤销、只读的证据分享链接**；
- 分享内容是创建当刻冻结的脱敏字段白名单快照，不授予项目后台能力；
- 分享 Token 使用 `secrets.token_urlsafe(32)`，SQLite 只存 SHA-256 哈希；
- 明文 Token 只在创建响应中返回一次，不进入历史列表；
- 分享有效期 5 分钟～7 天，前端提供 1 小时 / 24 小时 / 3 天 / 7 天；
- 撤销后原 Token 立即失效；过期后自动失效；
- 分享 URL 使用 `/shared-evidence#<token>`，Token 不进入 HTTP path/query；
- 公共页从 `window.location.hash` 读取 Token，并通过 `credentials: omit` 调用匿名只读解析接口；
- 匿名解析不创建或迁移数据库表；
- `/shared-evidence` 是唯一放在 `RequireAuth` 外的结果页面，其余项目页面仍需登录；
- 无稳定 `finding_persistence_id` 时禁止生成分享链接；
- 已新增后端 SQLite 分享测试、`test:evidence-distribution` 和 `test:evidence-share`。

仍未实现且不得伪装为已实现：

- 飞书、企业微信、钉钉直接推送；
- Jira / GitHub / GitLab 自动创建或同步 Issue；
- 企业级统一分享域名、组织级审计和管理员批量撤销；
- 分享链接访问统计 / 转化漏斗。

这些能力需要真实第三方 Connector、凭据授权、审计与组织权限合同后再接入。当前可在 Finding 协作记录中保存已有的外部任务 URL。

## 6. P2 后续范围

- 多项目质量趋势；
- 组织级风险和 ROI；
- 周报和持续质量摘要；
- 合作伙伴客户视图；
- 白标或联合品牌报告；
- 邀请漏斗、分享访问漏斗和报告转化漏斗；
- 第三方协作 Connector 的组织级配置与审计；
- 若真实客户运行时长证明有必要，再在现有六阶段权威边界内部增加更细的子阶段 / SSE 流式事件；必须来自真实函数与收据，不做装饰性百分比动画。

## 7. 非目标

本轮不做：

- 重写前端框架；
- 重写核心检测算法；
- 删除现有高级页面；
- 引入第二套自动 Finding 真相状态；
- 用静态假数据替代真实后端；
- 为视觉效果增加新的重型动画；
- 将内部运行状态伪装为客户结论；
- 用浏览器本地状态伪装企业协作；
- 用 Session JWT 冒充外部分享 Token；
- 为没有真实 Connector 的第三方平台展示假“已同步”。

## 8. 验收标准

### 8.1 功能验收

- 首次接入资料入口正确；
- 工作区创建后仍可自动登录；
- 不再生成可预测密码；
- Materials 遗留乱码不会展示给用户；
- ReleaseGate 操作名称与真实行为一致；
- README 可按实际命令启动项目；
- 服务端真实持有 scan lease 时前端能确认运行，lease 未出现时不冒充正在扫描；
- 六阶段均只能由对应真实后端执行边界推进；
- test-data 收据核验进入后能看到 active，并由真实 `test_data_plan` 收口成 completed / blocked / failed；
- 真实证据持久化进入后能看到 active，持久化结果决定 completed / failed；
- release gate 进入后 delivery 为 active；门禁 verdict=fail/blocked 不被误报成“阶段执行失败”；
- 门禁成功返回但报告尚未收口时 delivery 继续 active，最终 report/result 收口后才 completed；
- 阶段遥测写盘失败不改变扫描结果；未知 stage / 假百分比仍被拒绝；
- Finding 无稳定持久化身份时，协作写入、明确 Replay 状态写回和只读分享都 fail closed；
- 人工协作不能修改自动验证状态；
- 分享链接过期 / 撤销后不可解析；
- 分享历史不返回明文 Token；
- 公开分享页不能访问项目 API 权限；
- 原始认证材料和原始 curl 不进入只读分享快照。

### 8.2 工程验收

完整环境必须运行：

```bash
cd frontend
npm ci
npm run ci:gate
```

门禁包含：

- TypeScript typecheck；
- ESLint；
- 品牌契约；
- 自主 UX 契约；
- 前端最终收口契约；
- Settings 接入向导契约；
- 运行生命周期契约；
- 服务端 live scan / 六阶段状态契约；
- Dashboard 角色视图契约；
- Finding 协作契约；
- 证据分发与脱敏契约；
- 可撤销只读分享契约；
- Materials 覆盖、验收和远端生命周期契约；
- 登录 E2E 契约；
- Vite build。

后端新增独立测试覆盖：

- project scan lease 的 live/stale 外部投影；
- `scan_stage_progress` 显式阶段迁移与非法伪进度拒绝；
- `build_discovery_plan()` 对企业理解/场景规划真实边界的驱动；
- `run_experiment_candidate()` 对真实执行边界的驱动；
- `_test_data_receipt_verifier()` 对测试数据 active 边界的驱动；
- `_persist_execution_evidence()` 对证据持久化 active / completed / failed 边界的驱动；
- `_evaluate_release_gate()` 对 test-data 收口和 delivery active / failed 边界的驱动；
- `scan_stage_finalization` 对最终证据、测试数据、交付报告结果的收口；
- release verdict 与 stage execution status 分离；
- delivery 必须保持 active 直到最终 report/result 收口；
- stage telemetry 持久化失败 fail-soft；
- stage snapshot 只在真实 live lease 期间进入 HTTP 状态；
- Finding 人工协作与自动验证状态分权；
- display ID → SQLite persistence ID crosswalk；
- 只读分享 Token hash-only 持久化；
- 分享脱敏、过期、撤销和匿名只读解析。

当前仓库 GitHub Actions 仍存在账户或仓库级 `startup_failure` / 无有效 job 状态的问题。该外部故障不得被表述为测试通过；Actions 恢复后必须补跑完整前后端门禁。

### 8.3 合入验收

- P0 历史收口曾通过独立分支完成；
- 当前 P1 根修按用户要求直接推进 `main`；
- 所有 GitHub 文件更新使用当前 blob SHA 乐观锁，不使用 force push；
- 如果并行 Agent 已修改同一文件，写入必须被拒绝后重新读取，而不是覆盖；
- 每次修改前后确认 `main` 最新提交，保留并行任务的非冲突改动；
- 完整环境门禁未执行时，不得声称“全量回归通过”。

## 9. 成功指标

上线后通过埋点衡量：

- 登录成功率；
- 工作区创建成功率；
- 系统接入完成率；
- 资料导入完成率；
- 首次扫描启动率；
- 服务端扫描租约确认率；
- 主链阶段实时上报率；
- 六阶段真实回执覆盖率；
- 首次扫描完成率；
- 问题详情打开率；
- Finding 持久化身份绑定率；
- 协作记录保存率；
- 证据回放率；
- 报告导出率；
- 只读分享创建率；
- 分享访问率；
- 分享撤销率；
- Release 回归启动率；
- 自动验证关闭率。

本轮只实现可信主链与可观测能力，不虚构尚未接入的业务转化数字。