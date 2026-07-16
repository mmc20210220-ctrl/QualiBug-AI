# 深入排查：QualiBug 产品瓶颈现在卡在哪里

> 数据源：带 observability 修复后的真实 run `_funnel_runs/full.json`（13:15，129 义务，104.5s，20 bug）
> 方法：只读钻取 `obligation_attempt_ledger.attempts` 的 terminal reason / reason_detail / source_refs / contract evidence receipts，无 GT 无假数据。
> 修复验证：15 个失败 fixture receipt 现已全部带 `binding_detail`（observability 修复在真实 run 生效）。

---

## 一、瓶颈不是延迟/LLM，是「写义务的执行能力」

```
129 义务选中 ──compile/binding 拦 46──► 83 执行 ──governed 拦 29──► 54 执行 ──delivery 拦 15──► 39 交付 + 20 bug
                                          │                          │
                                   (NON_REVERSIBLE 36        (HARNESS_FAILED 15
                                    MISSING_OBSERVER 28       已定位=共享 id 绑定)
                                    MISSING_BINDING 8
                                    MISSING_FIXTURE 3)
```

- 执行率仅 42%（54/129），**75 个义务被拦死（58%）**，全部是 **POST/DELETE 写义务**。
- 延迟非瓶颈：全程 104.5s，governed_execution p50 仅 168ms。
- 20 个 bug 全来自**读义务**（GET 购物车鉴权）——产品擅长读鉴权测试，但**在写义务上大面积失能**，而写义务正是高价值的越权/状态篡改类 bug。

## 二、75 个被拦义务的精确画像（按阻塞原因）

### 1) BLOCKED_NON_REVERSIBLE_WRITE ×36（最大单一拦点）
- **全部 36 个** source 都含 `DELETE /api/cart/items/:id`；另含 `DELETE /api/products/admin/:sku`(17)、`POST /api/payments/pay`(11)、`POST /api/refunds/:id/approve`(8)、inventory consume/release 等。
- reason_detail 全是 `cleanup_unresolved:`（**空**——又一个 observability 缺口：没说为什么 cleanup 无法解析）。
- 全部卡在 **compile 阶段**（没进执行）。risk_family：authorization 21 / validation 13 / state 2。
- **分两类**：
  - ~19 个需要 `:id`（购物车 DELETE）——和下面的 `id` 绑定缺陷同根，**可通过修绑定 + 一次性 setup 解锁**。
  - ~17 个是真正的不可逆业务写（支付/退款/库存/商品删除）——治理正确 fail-closed；在已声明非生产的 benchmark 上，需放开「受控一次性 setup（先建后删）」才能测这些高价值端点的越权。

### 2) BLOCKED_MISSING_OBSERVER ×28（第二大）
- 全是 POST 写：`POST /api/cart/items`(63 处)、`POST /api/payments/admin/manual-success`(20)、`POST /api/products/admin`(20)。
- 21 个无 reason_detail，7 个显式 `write_observer`。卡在 execution(21)/compile(7)。risk_family：authorization 22 / validation 6。
- **契约/设计缺口**：这些写义务**没有 observer 能检测违规**（如写后状态/越权校验）。需要补 write_observer 覆盖。

### 3) BLOCKED_MISSING_BINDING ×8
- `POST /api/refunds`、`/api/inventory/reserve`、`/api/orders` 路径绑定物化失败，卡在 execution。与 `id` 绑定同类问题（路径占位符无法解析）。

### 4) BLOCKED_MISSING_FIXTURE ×3
- `validation_requires_source_example_and_request_schema`——校验义务缺源请求示例 + schema。**源文档缺口**。

## 三、最深单一根因：购物车 item `id` 绑定「调通但取不到值」

observability 修复后，15 个 HARNESS_FAILED 的失败 fixture receipt 直接带出了原因：

| 数量 | binding_detail | resolver_path | status_code | 含义 |
|---|---|---|---|---|
| **11** | `runtime_read_binding_unresolved:id:resolver_status_200:/api/cart/items` | `/api/cart/items` | **200** | resolver GET 成功，但**响应里提取不出 id**（购物车列表空 / 字段路径不匹配） |
| 4 | `runtime_read_binding_unresolved:id` | (空) | 0 | 该绑定压根没声明 resolver |

**机制**：`id` 绑定本应从 `GET /api/cart/items` 读出一个购物车项的 id，但 11 次拿到 200 却取不到值 → `{id}` 永不替换 → `DELETE/PATCH /api/cart/items/{id}` 请求发到未解析模板路径、status_code=0、无响应 → control/treatment/http_response observer/cleanup 全级联失败 → 闸门压成 `CONTRACT_ORACLE_HARNESS_FAILED`。

**这一个 `id` 绑定同时是**：
- 15 个 HARNESS_FAILED 的直接根因；
- 36 个 NON_REVERSIBLE 里购物车 DELETE 的 cleanup 依赖（DELETE 需要 `{id}`，绑不上 → cleanup_unresolved）；
- 部分 MISSING_BINDING 的同源问题。

→ **不是 resolver 报错，是购物车里没东西可读**（测试 setup 没先建购物车项，或建了但 actor 上下文/时机不对，或响应结构与提取路径不符）。这是典型的 **setup-timing / 提取路径** 缺陷。

## 四、最高 ROI 修复（按收益排序）

1. **修购物车 `id` 绑定**（一次修，连带回收 15 bug + 解锁购物车 DELETE 义务）：
   - 让绑定 resolver 前置一个 setup 先建购物车项（fixture_setup），或从 create 响应里直接取 id，或修正 `_select_runtime_binding` 的响应字段提取路径以匹配 `/api/cart/items` 真实返回结构。
   - 现在有了 `binding_detail`，下次 run 可直接确认修好后 resolver 能取到值。
2. **补 write_observer**（解锁 28）：为 POST 创建类义务加「写后状态/越权」观察者，让写义务能判定违规。
3. **放开受控一次性 setup 写**（解锁 ~17 个真正不可逆业务写）：在已声明非生产 test 的 benchmark 上，允许治理 sandbox 执行「先建后删」一次性夹具，以测支付/退款/库存/商品删除端点的越权（符合 AGENTS.md 非生产写契约）。
4. **补源请求示例 + schema**（解锁 3 个校验义务）。
5. **observability 续作**：`cleanup_unresolved:` 目前是空的——应让 NON_REVERSIBLE 的 reason_detail 带出具体是哪个绑定/哪个写无法补偿（同 harness 修复模式）。

## 五、一句话结论

> **瓶颈不在算力，在「写义务的契约/治理层」**：75 个写义务被拦，最大单一根因是**购物车 `id` 运行时绑定「HTTP 200 但取不到值」**（11/15）——这一个缺陷连带卡死 15 个已挖到的 bug、并阻塞购物车 DELETE 类义务。修好这一个绑定 + 补 write_observer + 在非生产靶场放开受控一次性写，预计可把执行率从 42% 拉到 70%+、正式 bug 从 20 提升到 40+。

## 附：分析脚本（只读，无假数据）
`_deep_bottleneck_dive.py`(75 被拦画像) · `_verify_id_binding_systemic.py`(id 绑定系统性验证) · `_id_binding_failure_mode.py`(resolver 失败模式) · `_probe_blocked_structure.py`(结构探查) · `_extract_harness_rootcause.py`(15 harness 钻取)
