# Discovery Python 模块收敛方案

`ai_test_asset_center` 的规模不能靠一次性重写或按文件名批量删除解决。
当前采用可审计的 **Strangler（绞杀式收敛）**：先建立入口、依赖、运行证据和职责清单，
再逐批迁移边界，最后删除经过静态与运行时双重证明的模块。

## 单一事实源

- 架构入口与职责覆盖：`ai_test_asset_center/architecture_roots.json`
- 静态清单实现：`ai_test_asset_center/architecture_inventory.py`
- 可重复执行命令：`python tools/architecture_inventory.py`
- 当前审计快照：`platform_outputs/architecture/discovery_module_inventory.json`

完整逐模块快照属于可再生诊断产物，写入已忽略的 `platform_outputs/`，避免把每次源码
变化造成的上万行 JSON diff 重新变成仓库冗余。命令只生成诊断 JSON，绝不删除或改写
源文件。架构指标始终标记为
`ARCHITECTURE_DIAGNOSTIC_ONLY`，外部发现质量始终保持 `NOT_MEASURED`；模块数量下降
不能冒充真实发现率提升。

## 当前基线

2026-07-12 的确定性静态快照为：

该快照明确标记为 `WORKTREE_CONTENT_ADDRESSED`，记录全部参与图计算的 Python 源码
指纹、包源码指纹、配置指纹、生成器指纹和 Git 状态。本次基线来自
`7060495` 之后的 Phase 3 `DIRTY` 工作树，因此只代表该内容指纹对应的诊断现场，不能
充当删除或质量晋级证据。

| 指标 | 当前值 | 含义 |
|---|---:|---|
| Python 模块 | 429 | `ai_test_asset_center` 包内模块 |
| Python 行数 | 244,576 | 包内物理行数，仅用于架构诊断 |
| 静态退休候选 | 64 | 无受支持入口、评测、工具、测试或外部静态引用 |
| 超大边界 | 21 | 超过 1,500 行，优先拆职责，不等同于可删除 |
| 运行时 patch 安装模块 | 16 | 运行时行为权威仍受安装顺序影响 |
| 活跃发现入口 | 3 | 其中 1 条为 canonical、1 条为兼容权威、1 条为 adapter |
| 重复发现权威 | 1 | compatibility 权威尚未被外部证据淘汰 |

这 64 个候选目前全部禁止自动删除，原因有两项：尚未提供覆盖所有受支持入口的完整
运行时 import trace；受支持路径内仍存在非字面量动态导入或插件发现。清单会把这两项
不确定性明确写成阻断状态，不会把“静态没搜到”伪装成“安全删除”。

`COMPLETE` trace 不是一个可手写自证的布尔值。它必须绑定当前全部 Python 源码与配置
指纹与 `pyproject.toml` 脚本声明指纹，携带 collector/version/session identity，并为
每个产品、评测、工具、安装脚本 callable 和测试根入口提供独立的完整 session、命令
指纹和环境指纹。同一模块上的两个 callable 是两个根入口，不能共用完成证明。缺少任一
根入口、trace 来自旧
源码，或根入口未实际出现在导入模块集合时，工具都会 fail fast，不能进入删除复核。

本轮已先消除三个确定性边界问题，而不是等待整包删减：

- 删除产品树中的 `p3_benchmark_scan_patch.py` 和 `scan_runtime_gate_patch.py`（已无安装点；
  runtime 契约门禁与 `compile_runtime_scenarios` 为一等公民；seed-bug 评测留在
  `benchmark_evaluator`，产品 `scan()` 拒绝 evaluator-private seed/observation 字段）；
- `ai_test_asset_center` 包导入改为零副作用，runtime scenario 由显式编译器接入主线；
- patch chain 安装改为幂等并按严格逆序恢复，测试校验真实 callable identity，而不是只看
  marker flag。

因此模块处理的首要 KPI 不是“一次删到多少文件”，而是并列权威、运行时 patch、超大
边界和不可追溯动态导入是否持续下降。为了拆开巨型模块而新增一个单职责 leaf module
可以接受；为了绕过现有 SSOT 再新增一条发现、评测、Gate 或投影路径不可接受。

## 收敛顺序

1. **固定权威入口。** 新能力只进入 canonical mainline；compatibility 入口只能委托，
   不得再形成独立调度、执行、Oracle、Gate 或质量投影。
2. **按职责围住旧代码。** 模块只能属于 `core`、`adapter`、`compatibility`、
   `diagnostic` 或 `retirement_candidate`。新建并列 `*_patch.py` 视为架构回退。
3. **先拆超大边界。** 依次抽离 campaign/planning、governed execution、
   assertion/oracle/gate、projection/persistence、HTTP transport；每次只移动一个可验证
   职责，保持公开协议不变。
4. **取得运行证据。** 在产品 5174/8088、评测 CLI、doctor、acceptance smoke 和测试入口
   上采集完整 import trace。`PARTIAL` trace 只能继续阻断，不能批准删除。
5. **逐批退休。** 只有静态不可达、完整 trace 未观察、无动态导入不确定性、测试通过且
   经人工删除复核的模块才能进入删除提交。删除提交必须小批量、可回滚，并重新生成清单。
6. **用外部收据验能力。** 每批架构变更后运行相同输入的 champion/candidate replay 与
   shadow；只有 evaluator-private 收据证明无 split 回退且至少一项改善，才允许晋级。

## 删除门禁

| 清单状态 | 含义 |
|---|---|
| `BLOCKED_DYNAMIC_IMPORT_REVIEW_REQUIRED` | 动态导入边界未知，禁止删除 |
| `BLOCKED_RUNTIME_TRACE_REQUIRED` | 未提交运行时证据，禁止删除 |
| `BLOCKED_RUNTIME_TRACE_INCOMPLETE` | trace 不完整，禁止删除 |
| `BLOCKED_RUNTIME_OBSERVED` | 运行中实际加载，不能作为退休候选 |
| `MANUAL_DELETION_REVIEW_REQUIRED` | 静态与运行证据均通过，但仍需人工复核和独立删除提交 |

任何门禁都不会自动执行删除。清单输出的职责分类是排查顺序和迁移输入，不是产品质量
结论，也不是商业上线证据。
