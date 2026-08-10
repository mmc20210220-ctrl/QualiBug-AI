# QualiBug Finding / Evidence 前端闭环 SPEC

状态：已实现  
范围：仅前端展示、导航与交互约束；不改变 Finding 成立、Evidence 生成、Regression 执行或 Release Gate 的后端判定。

## 1. 目标

客户从问题清单查看一条已确认问题时，必须能够沿同一条 Finding 完成：

`问题清单 -> 快速证据抽屉 -> 证据中心完整查看 -> 返回同一问题 -> 回归 -> 发布门禁`

前端不得因为页面切换丢失 Finding 身份，也不得把后端未知状态展示成确定的负面数值。

## 2. Finding 身份连续性

- 证据深链使用 `finding=<finding.id>`；
- `project` 与 `finding` 必须同时保留；
- Evidence Drawer 的“证据中心完整查看”必须进入指定 Finding；
- Evidence Center 切换问题时 URL 必须同步更新；
- Evidence Center 返回问题清单时必须保留当前 Finding；
- Findings 收到 `finding` 参数后自动展开该问题并清除会隐藏它的本地筛选；
- 如果 Finding 已不在当前已确认结果中，前端必须明确提示旧链接/状态变化，不允许按标题猜测相似问题。

## 3. 证据身份保护

如果 URL 指定 Finding A：

- A 有 Evidence Package：展示 A；
- A 是已确认 Finding 但尚无 Evidence Package：明确显示“该问题当前还没有可展示证据包”；
- A 已不在当前已确认结果：明确显示旧链接/状态变化；
- 不得静默展示 Finding B 的证据来填充详情区。

没有指定 Finding 时，才允许 Evidence Center 默认展示第一条真实证据包。

## 4. 证据评分真实性

- 后端真实返回 `0`：允许显示 `0/100`；
- 后端没有提供 score：显示“未评分”或 `—`；
- Evidence 列表、Evidence Drawer、QualityScore 圆环必须使用同一未知值语义；
- 禁止通过 `?? 0` 将 unknown 转成 0 分；
- 未评分时圆环不得绘制红色 0 分进度，避免制造“证据质量极差”的虚假结论。

## 5. Regression 前端门禁

Findings 与 Coverage 必须保持一致：

- 只有存在真实 `included_in_suite` / persisted regression obligation 才显示可执行回归；
- 没有真实回归义务时按钮 disabled；
- handler 即使被程序调用也必须 fail-closed；
- 不允许为了让按钮可用而构造 synthetic regression probe；
- Regression 结果仍以既有后端返回为唯一事实来源。

## 6. 项目导航工具

`navigateToProjectPath(pathname, projectId, currentSearch?)` 必须允许携带额外实体上下文，例如 `finding`。

要求：

- 自动写入/覆盖当前 `project`；
- 保留调用方显式提供的实体 query；
- 旧调用不提供 `currentSearch` 时行为保持不变；
- 不通过全局 sessionStorage 保存当前 Finding，避免跨客户污染。

## 7. 回归契约

`test:customer-action-guidance` 至少锁定：

- Findings 接受并展开 `finding`；
- 旧 Finding 不按标题猜测替代；
- Evidence 精确匹配指定 Finding；
- 指定 Finding 无证据时不静默 fallback；
- Evidence -> Findings 往返保留 `finding`；
- Evidence Drawer 提供指定 Finding 的完整证据深链；
- unknown evidence score 不得变成 0；
- Findings 无真实回归义务时按钮与 handler 均 fail-closed；
- project-navigation 支持 `project + entity query` 组合导航。

## 8. 非目标

本 SPEC 不负责：

- Finding 如何发现或确认；
- Evidence 如何采集或评分；
- Regression Probe 如何生成；
- Regression 如何执行；
- Release Gate 如何判定；
- Bug 召回率或后端测试能力。

上述全部由后端既有合同提供，前端只负责准确消费与连续表达。
