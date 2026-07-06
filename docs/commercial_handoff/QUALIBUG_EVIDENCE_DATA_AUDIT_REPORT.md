# QualiBug 证据链与前后端数据统一收尾审计报告

## 结论

本轮审计确认：当前前端主要通过 `frontend/src/api/client.ts:getFindings()` 请求 `/api/v1/projects/{project}/command-center`，后端由 `ai_test_asset_center/private_pilot_service.py:_build_command_center()` 汇聚多路风险，再交给 `ai_test_asset_center/display_ready_formatter.py:format_findings_display_ready()` 统一成 `data.risks` 给前端渲染。

真正的问题不是完全没有统一出口，而是：

1. 统一出口之前仍然混入多路来源：V12 报告、DB 校验、性能、全谱、多层、E2E、Deep、UI、累积未关闭 finding。
2. 原始候选数量被 `canonical_total = max(展示数量, 原始报告数量)` 放大，导致前端可能展示“总数/评分”与真实可渲染风险不一致。
3. `total_bugs_found` 原来等于 `canonical_total`，会把“候选/线索/未补证据项”包装成“已找到 Bug”。
4. 前端用 `||` 做统计兜底，导致 0 个已确认 Bug 会回退成 `total_findings`，形成展示口径混乱。
5. 原始报告高 evidence score 可能继续抬高展示层可信度，造成“证据不完整但看起来很可靠”。

## 本轮已修改

### 1. 后端增加 display-ready 数据合同

文件：`ai_test_asset_center/display_ready_formatter.py`

新增 `display_contract`：

- `materialized_risk_count`：最终进入 `data.risks` 的展示风险数。
- `raw_candidate_risk_count`：原始报告候选数，只做参考，不再当成已展示 Bug。
- `ready_bug_count`：只有 `bug_status == reproduced` 且 `gate_passed == true` 才算已复现 Bug。
- `needs_validation_count`：`suspected + risk_clue`，进入待补证据/待复核池。
- `not_reproduced_count`：未复现，不进入客户缺陷交付。
- `status_counts` / `evidence_level_counts` / `severity_counts`：前端统计口径统一来源。
- `integrity_hash`：对 display-ready 后核心字段做哈希，便于排查“后端返回与前端展示是否一致”。

### 2. 证据可信度改为门控后评分

文件：`ai_test_asset_center/display_ready_formatter.py`

修改 `_compute_scores()`：

- 原始报告的 `value_metrics.evidence_trust_score` 只能作为上限，不能抬高证据可信度。
- 证据可信度改为基于 display-ready 后的 `evidence_quality.score` 与 `bug_status` 加权计算。
- `risk_clue`、`suspected`、`not_reproduced` 不再能靠原始高分显示成高可信。

### 3. 后端 command-center 统计口径收敛

文件：`ai_test_asset_center/private_pilot_service.py`

核心变化：

- `canonical_total` 改为 `len(display_risks)`，不再用 raw total 放大展示总数。
- `executive_summary.total_bugs_found` 改为 `ready_bug_count`。
- `executive_summary.total_findings` 表示最终 materialized 风险记录数。
- 新增 `raw_candidate_findings`、`needs_validation_findings`、`not_reproduced_findings`。
- `scan_meta.score` 不再默认 97，改为证据门控后的 `evidence_trust`。
- `system_grade` 不再默认 A+，按证据可信度 A/B/C/D。
- 顶层返回 `data_contract`，声明前端只能渲染 `data.risks`。

### 4. 前端统计改为读取统一合同，不再用 `||` 误回退

文件：`frontend/src/api/data.ts`

核心变化：

- 新增 `firstFiniteNumber()` 与 `asFiniteNumber()`。
- 统计优先读取 `data_contract.materialized_risk_count`。
- P0 统计不再用 `||` 把 0 误回退。
- Release Gate 只把 `reproduced + gate_passed` 当作真正阻塞缺陷，线索不再被当成客户交付 Bug。

### 5. 修复一个前端类型安全问题

文件：`frontend/src/components/EvidenceTimeline.tsx`

修复 `structured: Record<string, unknown>` 直接进入 JSX 导致 TypeScript 报错的问题，保证前端 typecheck 通过。

### 6. 新增商业化证据合同测试

文件：`tests/test_evidence_audit_consistency.py`

新增两类测试：

- 原始报告 9 个候选，但 display-ready 只有 1 个线索时，不能显示成 9 个已复现 Bug。
- 只有 `reproduced + gate_passed` 才计入 `ready_bug_count`。

## 已验证

```bash
pytest -q tests/test_evidence_audit_consistency.py
# 46 passed

cd frontend && npm run typecheck
# passed
```

前端 build 验证受当前压缩包内 `node_modules` 影响：

```text
vite: Permission denied
@rolldown/binding-linux-x64-gnu missing
```

这属于依赖安装/平台可选依赖缺失，不是本轮代码修改导致。建议在本地执行：

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

## 落地后的商业展示口径

建议前端/报告统一用以下三层表达：

1. 已复现 Bug：`ready_bug_count`，客户可直接验收和派单。
2. 待补证据线索：`needs_validation_count`，内部继续跑复现、补日志、补 DB 快照。
3. 未复现：`not_reproduced_count`，默认不进入客户缺陷交付。

不要再把 raw candidate count、AI 推理候选数、历史累计线索直接叫做“Bug 数”。
