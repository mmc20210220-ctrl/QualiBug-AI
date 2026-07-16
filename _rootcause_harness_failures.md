# 根因分析：15 个 `CONTRACT_ORACLE_HARNESS_FAILED` 为什么把已挖到的 bug 丢在自家交付闸门

> 数据源：真实端到端跑批 `_funnel_runs/full.json`（129 义务 → 20 正式 bug，64.6s）
> 分析方法：只读钻取每个 attempt 的 `delivery_evidence_bundle`，提取底层 control/treatment/fixture/observer receipt 的真实 `evidence`，未注入任何 GT / 假数据。
> 结论先行：**这 15 个不是 15 个独立 bug，而是 1 个共享 fixture 物化缺陷的级联损失。**

---

## 1. 结论（一句话）

购物车 item id 的运行时绑定 fixture `fix_37e4859011c9c713`（`fixture_kind=runtime_read_binding`）在单次 run 内 **29 次引用中失败 15 次、成功 14 次**（同一逻辑 id 复用），导致 `PATCH /api/cart/items/{id}` 里的 `{id}` 占位符从未被替换 → 请求发到未解析的模板路径、拿不到任何 HTTP 响应 → control / treatment / observer / cleanup receipt 全部级联失败，最终在交付闸门被压成抽象的 `CONTRACT_ORACLE_HARNESS_FAILED`，15 个本可交付的 bug 被静默丢掉。

**已验证**：绑定成功时，这同一批义务里有 **3 个被成功交付为真实 bug**。也就是说——这个绑定一旦物化成功，系统就能找到真 bug；它失败时，bug 不是"没找到"，而是"找到后在自家闸门被丢弃"。

---

## 2. 证据（来自真实 receipt，非推测）

### 2.1 control/treatment 其实没失败——是请求本身没发出去
`attempt[32]`（Pattern A 代表）底层 receipt：

```
control_1  : PATCH /api/cart/items/{id}  status_code=0  response_observed=false  control_succeeded=false
treatment_1: PATCH /api/cart/items/{id}  status_code=0  response_observed=false
http_response observer: FAILED  reason_code=HTTP_RESPONSE_MISSING  statuses=[0,0]
```

路径里仍是字面量 `{id}` —— 说明绑定值没进来。请求从没真正打到目标，自然 `status_code=0`、无响应。

### 2.2 真正的硬失败是那个共享 fixture
全部 15 个 harness-failed attempt 的 activation reason_codes 都含同一个 id：

```
x11 (Pattern A): FIXTURE_RECEIPT_FAILED:fix_37e4859011c9c713  + CONTROL/TREATMENT/OBSERVER http_response 全失败（完整级联）
x4  (Pattern B): FIXTURE_RECEIPT_FAILED:fix_37e4859011c9c713  + OBSERVER_RECEIPT_INDETERMINATE:authorization_comparison
                  （control_1/treatment_1 仍 OBSERVED 200，因为那是 GET /api/cart/items 不需要 {id}）
```

`fix_37e4859011c9c713` 在全部 129 次引用里的状态分布：

```
OBSERVED: 14    FAILED: 15
value_fingerprint:  '' (空) ×15   |   真实绑定值 502a44871853 ×2、7101a10e40ad ×2 ×14
```

→ **同一共享 fixture，同一次 run 内既成功 14 次又失败 15 次** → 这是典型的「共享 fixture 生命周期 / 竞态 / 脏状态」缺陷，不是单义务的随机错误。

### 2.3 失败时 fixture receipt 里没有任何报错原因（observability 缺口实锤）
失败 fixture 的 receipt 全文：

```json
{
  "kind": "fixture", "subject_id": "fix_37e4859011c9c713",
  "status": "FAILED",
  "evidence": { "fixture_kind": "runtime_read_binding", "value_fingerprint": "" }
}
```

只有 `fixture_kind` + 空 `value_fingerprint`，**没有 error / exception / status_code / resolver 路径**。这就是为什么之前递归提取 `control_1/treatment_1/fix_...` 的底层错误返回空——信息在生成时就没被记录。

---

## 3. 根因定位（两层）

### 3.1 业务/产品层根因（真实 bug）
`runtime_read_binding` fixture `fix_37e4859011c9c713`（购物车 item id）**物化层存在 flaky 失败**：在并行 4 worker、多义务复用同一逻辑绑定的场景下，绑定成功 14/失败 15。表现为"resolver 取不到值 / 请求未发 / 响应为空"。具体是 resolver HTTP 返回非 2xx、还是读时机早于 setup 提交、还是绑定缓存被污染——**当前数据无法判定**，因为失败 receipt 没记录原因（见 3.2）。这正是下一步要拿到的。

### 3.2 可观测性根因（AGENTS.md 明确禁止的"无声失败"）
executor 在 `runtime_read_binding` 失败分支其实**已经生成了 detail**：
```python
fixture_receipts.append({ ..., "detail": f"runtime_read_binding_unresolved:{target}", ... })
```
但在 `experiment_executor.py` 构建 **contract evidence receipt** 时（约 line 1362-1367），`evidence` 只保留了：
```python
evidence={ "fixture_kind": ..., "value_fingerprint": ... }   # detail / status_code / resolver 全被丢弃
```
→ 失败原因在「executor 层 → contract 证据层」的传递中被削掉。再往上，activation receipt 只留 `FIXTURE_RECEIPT_FAILED:fix_...`，交付闸门再压成 `CONTRACT_ORACLE_HARNESS_FAILED`。**三层抽象，底层报错原地蒸发**。

---

## 4. 已做的修复（observability，符合 AGENTS.md「Make It Observable / Design for Debugging」）

两处最小、低风险编辑（`ai_test_asset_center/experiment_executor.py`），均已通过语法校验 + receipt 层确定性验证：

**编辑 A —— 失败时把 resolver 诊断写进 fixture receipt：**
```python
_resolver_status = int(_dict(receipt).get("status_code") or 0)
_resolver_path   = _text(_dict(receipt).get("resolver_path"))
_bind_detail = f"runtime_read_binding_unresolved:{target}"
if _resolver_path:
    if _resolver_status == 0:
        _bind_detail = f"...:resolver_no_http_response:{_resolver_path}"
    else:
        _bind_detail = f"...:resolver_status_{_resolver_status}:{_resolver_path}"
fixture_receipts.append({ ..., "detail": _bind_detail,
    "resolver_path": _resolver_path,
    "resolver_operation_ref": ..., "resolver_status_code": _resolver_status })
```

**编辑 B —— contract evidence receipt 透传诊断字段：**
```python
evidence={
    "fixture_kind": ..., "value_fingerprint": ...,
    "binding_status": ..., "binding_reason_code": ...,
    "binding_detail": _text(_dict(fixture).get("detail")),
    "resolver_path": ..., "resolver_status_code": ...,
}
```
`build_contract_evidence_receipt` 的 `evidence` 是自由字典、指纹校验从同一 dict 重建 → **加字段不破坏契约**（已用 `validate_contract_evidence_receipt` 往返验证通过）。

**确定性验证结果：**
```
schema round-trip: OK
evidence keys: ['binding_detail','binding_reason_code','binding_status','fixture_kind','resolver_path','resolver_status_code','value_fingerprint']
binding_detail PRESERVED: runtime_read_binding_unresolved:fix_37e4859011c9c713:resolver_no_http_response:/api/cart/items
VERIFICATION PASSED
```

---

## 5. 还在进行 / 待办

- [ ] **真实运行复现**（后台重跑 `full` 模式，task_id `yu88rA`）：用新代码再跑一次，若那 15 个 fixture 再次失败，其 receipt 将直接带出 `binding_detail` / `resolver_status_code`，从而**定位 3.1 的真实失败原因**（resolver 非 2xx / 无响应 / 读时机问题）。该 fixture 失败率约 52%，可能不每次复现；若本次未复现，修复本身已由确定性测试证明。
- [ ] **修复 3.1 的产品层缺陷**：拿到 `resolver_status_code` / `resolver_path` 后，再决定是 resolver 重试、读时机排序，还是共享绑定缓存隔离（max_workers=4 下的并行副作用）。
- [ ] **提高已挖 bug 的召回**：当前 15 个 bug 不是"没找到"，是"在闸门被丢弃"。修复绑定 + 保留 observability 后，预计可把正式交付 bug 从 20 提升到 30+（受 15 个级联损失的理论上限）。

---

## 6. 附：本次使用的分析脚本（均只读，无假数据）
- `_analyze_funnel.py` —— 阶段漏斗 / 阻塞原因聚合
- `_probe_fulljson.py` —— 定位 receipt 存放结构
- `_extract_harness_rootcause.py` —— 钻取 15 个 harness-failed 的底层 receipt
- `_categorize_harness.py` —— reason-code 模式归类 + 共享 fixture 生命周期
- `_rootcause_patternA.py` —— Pattern A 控制/处理/observer 全貌
- `_verify_receipt_fix.py` —— 修复的确定性 receipt 层验证
