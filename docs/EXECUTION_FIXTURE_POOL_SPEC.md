# 执行段性能根因与修复规格 — Run 内共享夹具池

状态：SPEC（降级为 P2——round7 阶段分布证伪了量级假设）
证据修正（2026-08-23 晚）：round7 operation_phase 分布 =
treatment×623 / control×569 / cleanup×158 / **fixture_setup 仅 60+2**。
26min 执行段主体是**真实臂流量**（多步计划合法写入）× 本地延迟，
并非夹具写放大。池化收益上限 ≈ 62 次创建的节省，边际价值低；
保留本规格作为结构改进备选，不再作为性能主杠杆。
前置事实：B1=26.3min/1369 控制写/52写每分/静默仅4%；
结论修正：引擎与臂流量健康；性能工作到此已闭环，
后续优化应基于 [exec-trace]/[plan-trace] 新数据再立项。

## 为什么不能按 (endpoint, actor) 盲目池化
`inventory/transfer` 等流程需在同一 collection 创建两个不同实体；
"同端点第二次创建→复用首个"会静默改变被测语义（假测试）。键必须携带意图。

## 实施规格（函数级锚点）

### P0 键设计 — 绑定计划指纹
- 计算位置：`experiment_fixture_materializer_core._auto_fixture_create_for_binding_target`
  返回前，对 `fixture_setup` 做内容指纹：
  `sha256(json{op_id, op_path, actor_refs排序, body_template规范化})`
  - `body_template` 规范化：剔除 `_request_example(op)` 中被
    `disposable_identity_nonce("schema_unique",…)` 加后缀的唯一键字段值
    （唯一后缀本就是防冲突手段，不参与意图）；保留其余字段值。
- 池作用域：**串行组**（ContextVar，scheduler 提交任务时以
  `contextvars.copy_context().run(_run_group,…)` 传播；组间不共享——
  跨组只读共享列为 P2，需要"消费方对夹具无写步"判定先行）。

### P1 执行拦截 — validated_fixture_setup 执行点
- 定位：runtime_binding_materializer_base.`validated_fixture_setup`
  （实际发起 governed control write 并产出 identity 的位置）。
- 流程：
  1. 组池 lookup(fingerprint)：命中且存储态 `alive=True` →
     跳过 HTTP，直接以存储的 `{status:201, body:<首次响应体>}` 形状返回，
     打标 `shared_fixture_reused=true`；usage_count+=1。
  2. 未命中 → 正常执行；2xx 后将响应体与 cleanup_operations 存入池。
- 清理：**沿用消费者各自补偿不动**（第二个以上 DELETE 得 404，
  既有阶梯已视为成功）；组结束由 scheduler 在 `_run_group` 返回后调用
  `pool.drain(group_id)` 仅输出统计回执，不额外发请求。
  ——即 v1 不引入跨实验清理协调（风险集中区），牺牲部分 teardown 冗余量
  （DELETE 404 快速失败，量级远小于 POST 创建链）。

### P1 可观测性
- `[exec-trace] fixture_pool group=<id> hits=<n> creates_saved=<n>`
  WARNING 级（与既定 exec-trace 约定一致）。
- 审计行补 `shared_fixture_reused` 字段（sandbox_write_executor_base
  的三处 record 构造点），保持审计完整可追溯。

### P2（后续）
- 跨组只读共享：消费方实验的全部 WRITE 步路径均不命中夹具资源家族
  （collection 前缀匹配）方可跨组借用；并发读安全由"无写步"保证。
- reasoner MISS 根因：对比 round7 `[plan-trace] reasoner hit=` 行的
  reason 字段（input_changed / no_prior / prior_status_*），针对性修指纹稳定性。

## 验收门
1. 单测：同指纹两次物化 → 第二次零 HTTP、绑定值一致、reused 标记；
   不同 body（非唯一键字段不同）→ 不复用。
2. 回归：fixture/cleanup 相关既有套件全绿
   （test_h25_evaluation_fixture_cleanup 等）。
3. 一次小规模验证 run：B1 段控制写数量显著下降（预期 ≥40%）且
   findings 数不低于基线；[exec-trace] 分段账本完整。

## 本文件之外已完成并推送的同根因修复
- 端点熔断器 / 调度拓扑日志 / reseal 条件化 / 凭据缓存与三级 base_url /
  linker 窗口+调用数双预算 / transition 输入上限 / scheduler 缩进截断修复
  （提交 5dc94b31, 5b7445e6, 8c671355, d1f376a7）。
