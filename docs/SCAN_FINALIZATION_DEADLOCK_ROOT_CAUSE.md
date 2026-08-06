# 扫描收尾死锁根因报告（Scan Finalization Lifecycle Deadlock）

日期：2026-08-06
状态：**可观测化完成、间歇性复现中、根因候选已收敛、待复现定位**

## 1. 现象（已确证事实）

大扫描业务工作完成后，HTTP 请求永不返回：

| 事实 | 证据 |
|---|---|
| 业务结果已落盘 | 结果文件 236MB（12:28:02）、DB 扫描行（12:28:04）、报告（11:55:55） |
| 客户端未收到任何响应 | 客户端阻塞在 `_read_status`（状态行未到达），40 分钟后超时 |
| 扫描线程阻塞 | 等待原因 EventPairLow、CPU 0、py-spy 无法展开其栈 |
| 租约未释放 | 进程被杀后租约残留（pid 15844 死亡才被判定 stale） |
| 无异常痕迹 | 挂起窗口（12:28-12:52）结构化日志为零 |
| 必须重启后端才恢复 | 重启后健康、结果可读 |

## 2. 调用路径（实际代码）

```
HTTP POST /api/v1/scan
→ PrivatePilotHandler.do_POST
→ _handle_v12_scan
  → project_scan_lease (acquire, token=…)
    → _execute_v12_scan
      → scan()                          # 管线：知识资产→IR→义务→执行→报告
      → _issue_runtime_approval_for_result
      → _persist_scan_result            # ← 挂起窗口起点（12:28:04 save_scan 提交后）
        → _bound_scan_report            # 读报告 + scan_id 绑定
        → _collect_findings
        → db_persist.save_scan          # DB 行提交（12:28:04）
        → db_persist.merge_findings_cumulative
        → increment_scan_counter
        → spectrum 写盘
        → _update_continuous_state
      → self._json({…})                 # 紧凑投影响应（~20KB）
  ← project_scan_lease (release: shutil.rmtree)
→ handle_one_request → self.wfile.flush()
```

## 3. 已排除的假设（均有实证）

| 假设 | 排除依据 |
|---|---|
| 响应体过大阻塞写 | 投影响应实测 ~20KB；replay 全链 4 秒完成 |
| 持久化链本身挂起 | 236MB 真实结果 replay `_persist_scan_result` 全链 4 秒完成；两次带埋点真实运行 persist 629ms 闭环 |
| SQLite 锁 | `_conn` 为 WAL + busy_timeout=30s，有界 |
| 子进程等待 | 收尾路径无 subprocess |
| 日志队列 | 无 QueueHandler（RotatingFileHandler + StreamHandler） |
| 磁盘满 | 41GB 空闲 |
| 异常路径 | 无 ERROR 级日志、无 500 响应 |
| 规模决定 | Run 4 与 Run 2 体量一致（225MB/694 义务 vs 236MB/690），Run 4 正常闭环 |

## 4. 已确证的时间线（带埋点运行）

Run 3（13:36:35）与 Run 4（14:34:10）完整收尾时间线（毫秒级）：

```
persist_started → persist_bound_report(343ms) → persist_collect_findings
→ persist_save_scan(368ms) → persist_merge_cumulative(382ms)
→ persist_scan_counter(396ms) → persist_continuous_state(413ms)
→ persist_done(414ms) → response_building → response_written(2ms)
→ lease_released
```

**收尾链两次真实运行均健康闭环**。死锁为间歇性（观测 3 次运行中 1 次挂起），与规模无确定性关联。

## 5. 根因结论（诚实陈述）

**已证明**：挂起发生在 `save_scan` 提交（12:28:04）之后、响应写出之前的收尾窗口；该窗口在两次带埋点运行中均以 <1s 闭环。

**未最终归因**：Run 2 挂起时无生命周期埋点（INFO 级埋点被产品日志 WARNING 根级别静默丢弃——这是本任务发现的第一个真问题），无法追溯其精确阶段。挂起形态（EventPairLow、CPU 0、py-spy 无法展开、无连接）指向**原生级等待**——最可能是扫描线程与后台线程（连接器自动同步 / page-agent 桥 / LLM 客户端）之间的锁或 IO 事件竞态，但该候选需要复现才能证实。

**已修复的次生问题**（本次交付）：
1. 生命周期埋点以 WARNING 级输出（产品日志根级别为 WARNING，INFO 会被静默丢弃——此前任何收尾挂起都不可观测）；
2. 新增停滞看门狗（120s）：响应未写出时记录最后阶段，复现即可归因。

## 6. 修复说明（本次变更）

| 文件 | 变更 |
|---|---|
| `ai_test_asset_center/private_pilot_scan_handlers.py` | `_finalization_event`（WARNING 级结构化生命周期事件，SPEC §6 格式）；`_response_stall_watchdog`（诊断性停滞告警，不改控制流）；persist 每步 + 响应 + 租约释放埋点 |
| `tests/test_scan_finalization_lifecycle.py` | 4 项回归：阶段埋点、收尾链完整性、投影失败降级不阻塞（SPEC §5.4）、租约重复释放安全（SPEC §5.3） |

**终态所有权**：同步 HTTP 扫描协议下，终态即响应本身；所有者是 `_execute_v12_scan` 收尾段（persist→响应），租约释放位于 `finally`（幂等，token 校验）。异常路径：persist 失败 → 500 + `result_available_but_not_committed`；投影失败 → degraded 状态，永不阻塞响应。

## 7. 剩余风险

| 风险 | 触发条件 | 影响 | 当前保护 | 后续建议 |
|---|---|---|---|---|
| Run 2 挂起根因未最终归因 | 间歇性（~1/3），与规模无确定关联 | 大扫描请求永不返回，需重启后端 | 埋点 + 看门狗可归因；恢复需重启（租约 stale 自动回收） | 复现后按埋点定位；若指向锁竞态，收敛锁范围或使收尾路径无锁 |
| 埋点覆盖窗口 | 挂起若发生在 `scan()` 管线内部（persist 之前） | 归因延迟 | 管线自身有阶段日志 | 管线阶段也接入生命周期事件 |
| 客户端断开 | 扫描中客户端断连 | 响应写失败（已捕获 OSError 族） | `_json` 捕获 ConnectionReset/Aborted | 按 SPEC §10 验证订阅者注销 |

## 8. 验证状态

- 单元/回归：生命周期 4/4 通过；handler/上下文/Oracle 相关 44 项全绿
- 真实扫描连续三轮（Run 3 / Run 4 / Run 5）全部自动返回、无重启、无挂起，完整时间线捕获（SPEC §14.4 稳定性验收达成）：

| Run | scan_id | 结果 | 收尾耗时 | 响应 |
|---|---|---|---|---|
| 3 | scan_…1785992018352 | 8 findings, evidence_ready | persist 414ms → response 1ms | 正常 |
| 4 | scan_…1785995199453 | 8 findings, evidence_ready | persist 629ms → response 2ms | 正常 |
| 5 | scan_…1785998169737 | 8 findings, evidence_ready | persist 552ms → response 2ms | 正常 |

- 停滞看门狗已验证自取消（响应写出后不再误报）；若挂起复现，`scan.finalization.stalled` 将携带最后阶段
- 待完成：Run 2 挂起的归因需复现（埋点已就位，复现即定位）
