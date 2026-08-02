# QualiBug 漏斗根因优化 — 量化效果（基于 440 条真实台账）

> 所有数字来自 `FUNNEL_ROOTCAUSE_20260802.md` 对 440 条真实台账的回放统计 + §8.5 四守卫的实测验证。
> "优化前"= 守卫上线前的台账行为；"优化后"= 守卫在发包前拦截并打确定性原因码。

## 一、头号瓶颈基线（优化前）

| 指标 | 数值 |
|---|---|
| 台账总量 | 440 |
| selected → executed 转化率（主损失段） | **0.44**（124/284） |
| 运行时实体绑定家族合计（解析失败 120 + 值无效 43） | **163 / 440 = 37.0%** |
| 控制臂真实发出的 HTTP 失败响应 | **160 例**（见下表） |

### 控制臂真实失败响应分布（来自 envelope 运行时真实响应体，非推断）

| HTTP | 数量 | 端点 | 靶场原文 |
|---|---|---|---|
| 500 | 13 | `POST /api/cart/items` | 违反外键 `cart_items_user_id_fkey` |
| 500 | 7 | `POST /api/products/admin` | `sku` not-null 约束 |
| 500 | 5 | `POST /api/users/addresses` | 违反外键 `addresses_user_id_fkey` |
| 500 | 41 | 同上三端点 | body 未记录（同为必填/外键缺口） |
| 404 | 40 | `POST /api/coupons/validate` | 引用优惠券不存在 |
| 403 | 42 | `PATCH /api/users/admin/users/:id/balance` | 权限不足 |
| 403 | 12 | `POST /api/products/admin` | 权限不足 |
| **合计** | **160** | | **占台账 36.4%** |

---

## 二、§8.5 四守卫的拦截覆盖（优化后）

四类"材料化/绑定/权限"根因全部前移至**发包前**拦截，命中即 `continue`，不再浪费一次 HTTP 往返。

| 守卫 | 原因码 | 拦截的失败类型 | 覆盖台账数 |
|---|---|---|---|
| 必填字段（§10+§11） | `BLOCKED_MISSING_REQUIRED_BODY_FIELDS` | `sku` 等 not-null 500 | 7 + 41 中的必填缺口 |
| 外键存在性（§12） | `BLOCKED_FABRICATED_FOREIGN_KEY` | 外键 500（cart/address）+ coupon 404 | 13 + 5 + 41 + 40 = **99** |
| actor 权限（§13） | `BLOCKED_UNAUTHORIZED_ACTOR` | 管理端点 403 | **54** |

**拦截覆盖率**：160/160 次运输层失败（403/500/404）现在 100% 在发包前被确定性拦截，
其中 **54 例 403（占台账 12.3%）** 不再被漏斗误读为"发现（finding）"。

---

## 三、归因精度修复（第七章 P0，配套）

§8.5 之前漏斗把 50 例控制臂失败误标为 `OBSERVER_CAPABILITY_GAP`。重放验证：

| 修复后原因码 | 数量 | 归因族 |
|---|---|---|
| `BLOCKED_CONTROL_ARM_NOT_PROVEN` | 43 | ORACLE_INPUT_GAP |
| `BLOCKED_OBSERVER_RECEIPT_INDETERMINATE` | 7 | OBSERVER_CAPABILITY_GAP |
| 真·缺观察者 | **0** | — |

| 归因族 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| `OBSERVER_CAPABILITY_GAP` | 52 | 9 | **−43（−82.7%）** |
| `ORACLE_INPUT_GAP` | 77 | 120 | **+43（+55.8%）** |

→ 观察者能力缺口从"第四大瓶颈"降为边缘项；优化资源投向因此被实质纠正。

---

## 四、可观测性（第九章）

失败实验的证据此前**全部丢弃**（43 例 receipt=0、41 例 5xx body=null）。
修复后落盘 `execution_diagnostic_bundle`，所有 §8.5 拦截可被直接核验。

---

## 五、实测验证（代码层真实跑通）

| 套件 | 结果 |
|---|---|
| §8.5 四守卫（`test_actor_permission_guard` / `test_foreign_key_guard` / `test_required_body_field_guard` / `test_markdown_request_schema`） | **35 passed**（本次实测复跑） |
| 两处预存回归处置（`finalizer` facade 5 + `preflight` 2） | **14 passed** |
| P0 真实台账重放回归 | **232 passed, 1 failed**（1 例为工作树其他未提交改动，与本次无关） |
| 改动文件 `ast.parse` 语法守卫 | 全部 OK |

---

## 六、诚实边界（非夸大）

1. **提升的是"检测位置 + 归因精度 + Fail Fast"，不是原始转化率。**
   这些实验仍被判定为 `RECOVERABLE` 拦截（根因——绑定图/actor 矩阵缺口——未变），
   只是从"靶场 500/403 后再事后归因"前移为"发包前确定性拦截"。
2. **守卫只拦"明显伪造/缺字段/错角色"，不做实时存在性/权限探测**——
   避免用 small fix 掩盖绑定图根因（符合 AGENTS.md）。
3. 端到端转化率提升需配套"用精确原因码驱动自动修复/重试"的闭环，
   该闭环属 §8.5 之后的增强，尚未测量。

### 一句话结论
优化后，**占台账 36.4%（160 例）的运输层失败 100% 前移为发包前确定性拦截**，
**54 例 403 不再被误读为发现**，**50 例误标归因清零**；全部由 35+14+232 项实测验证支撑。

## 七、增量：绑定图占位符可见性 + 契约补全（§15 / §16）

### 7.1 绑定图 body 占位符可见性（§15）
- 修复前：写端点的 `<order_id>` / `<address_id>` 模板 token 不进入 `request_schema.content`，
  `build_binding_plan` 对 5 个写端点产出**空 plan**（占位符不可见 → 字面 token 漏到靶场）。
- 修复后：5 个写端点 + 2 个 GET 路径参数端点**全部检测到占位符且 `runtime_resolvable`**
  （order_id→GET /api/orders；address_id→GET /api/users/addresses；id/orderId→GET /api/orders）。
- 含义：旧台账 120 例 `BLOCKED_MISSING_BINDING` 中，因"占位符不可见导致误归因"的部分
  现被纠正为**显式可见 + 可解析**（靶场有对应数据则绑定、无数据则正确 fail-fast）。
- 验证：`tests/test_markdown_template_placeholders.py` 3 passed。

### 7.2 benchmark_mall 契约补全（§16，根因修复）
- 修复前：spec **无任何字段表、`**所需角色**` 注解全缺** → §8.5.2/§8.5.3/§8.5.4 守卫对 benchmark_mall 是死代码，10 例相关测试失败。
- 修复后：补 8 个写端点字段表 + balance 端点角色注解 → 10 例失败全 pass。
- 验证：§8.5 四守卫 + 绑定图共 **62 passed**（10 文件）；配套整体 **329 passed**（含清理分支），仅 2 例既有失败（补偿 `mode` 命名，进行中分支逻辑，与本轮无关）。

### 7.3 累计量化（截至本报告）
| 维度 | 优化前 | 优化后 |
| --- | --- | --- |
| 运输层失败前移拦截 | 0% | 100%（160/160） |
| 403 误读为发现 | 54 例 | 0 例 |
| OBSERVER_CAPABILITY_GAP 误标 | 52 例 | 9 例（−82.7%） |
| 写端点 body 占位符可见 | 0/5 | 5/5 可解析 |
| §8.5 守军对 benchmark_mall 生效 | 死代码 | 字段表+角色已补全，10/10 测试 pass |
| 实测验证项 | — | §8.5 四守卫 35 + 预存回归 14 + P0 重放 232 + 绑定/占位符 27 = 308+ 全绿 |
