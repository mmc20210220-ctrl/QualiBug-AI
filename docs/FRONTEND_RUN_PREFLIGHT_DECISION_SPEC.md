# QualiBug Run Center 运行前检查首屏 SPEC

状态：已实现  
范围：仅前端运行前检查展示、上下文解释、CTA 与 fail-closed 行为；不改变后端 Preflight、扫描执行、安全门禁、测试数据规划或 Bug 发现算法。

## 1. 目标

客户进入 Run Center 后，第一屏必须直接回答：

1. 当前项目是否已有可用的系统接入上下文；
2. 是否存在真实 active 企业资料输入；
3. 是否存在当前启用服务上的测试凭据配置；
4. 审批写场景是否完成前端可信同步；
5. **后端 Preflight 是否明确允许提交扫描。**

其中前四项只是辅助解释，第五项才是运行就绪权威。

## 2. 唯一运行权威

当前后端合同：

```ts
ScanPreflight = {
  ok: boolean;
  ready: boolean;
  reasons: Array<{ code: string; message: string }>;
}
```

因此前端必须遵守：

- 只有 `preflight.ready === true` 才可以显示“运行前检查已通过”；
- `ready !== true` 时不得因为系统地址、资料、凭据看起来齐全而自行放行；
- Preflight 请求失败必须 fail-closed，显示“无法确认是否可以开始检测”；
- Preflight 没有提供分维度通过状态时，前端不得伪造“系统连通性通过 / 登录通过 / 资料门禁通过”三个独立后端结论。

## 3. 辅助事实

### 3.1 目标系统接入

只说明当前项目是否观察到启用且有 `base_url` 的服务配置。

允许：

- `2 个已启用`
- `未观察到启用目标`
- `无法确认`

不得把“有 URL”写成“系统已连通”。真实可访问性仍由后端 Preflight 与实际执行证明。

### 3.2 测试凭据配置

只统计**当前启用服务**中的真实 auth 配置。

不得：

- 把已禁用服务的旧凭据计入当前运行上下文；
- 把“已配置凭据”写成“登录验证通过”；
- 把没有凭据自动升级成前端运行阻断，因为部分验证可能不需要登录。

### 3.3 企业资料输入

只把真实 `status === active` 的 source 计为当前可用输入事实。

规则：

- 资料类型完全开放；
- 不限定 PRD / API / DB / UI / UX 等固定集合；
- 非 active 资料不会被包装成可用输入；
- 没有 active source 是否阻断运行，只由后端 Preflight 决定。

Run Center 自动选择 API source 时，也只能从 active source 中选择；否则不传显式 `source_id`，继续让后端保持权威。

### 3.4 审批写场景

审批写场景同步属于扫描提交前的身份一致性保护，不是新的后端 Preflight 算法。

状态：

- 可信同步中：暂不提交；
- 同步失败：暂不提交，并允许用户查看安全熔断；
- 已同步：运行合同后续仍需再次确认真实绑定；
- 强制只读：本次跳过审批写场景。

不得把“前端同步成功”写成“写场景执行成功”。

## 4. 首屏状态

### 4.1 Preflight 检查中

标题：`正在确认是否可以开始检测`

- CTA 禁用；
- 不提前出现绿色运行结论。

### 4.2 Preflight 读取失败

标题：`当前无法确认是否可以开始检测`

主动作：`重新检查运行条件`

读取失败不能解释为“没有阻断”。

### 4.3 Preflight 未通过

标题：`运行前检查未通过，暂不启动检测`

首屏只显示：

- 第一个**后端上报**阻断；
- 后端原始阻断数量；
- `查看运行阻断`。

不得把第一个上报原因宣传成“最高严重度根因”，因为当前合同没有 blocker priority 字段。

完整阻断详情区继续显示全部 `message + code`。

### 4.4 Preflight 通过但审批场景同步中/失败

必须明确说明：

- 后端 Preflight 已通过；
- 当前暂停只是前端写场景身份一致性保护；
- 不能把该状态反写成“后端 Preflight 未通过”。

### 4.5 可以运行

只有：

```text
preflight.ready === true
AND
审批写场景可信同步没有阻断（或本次强制只读）
```

才显示：

`运行前检查已通过，可以开始检测`

主动作：`执行标准扫描`

扫描提交后仍可能产生后端安全阻断、plan-only、partial coverage 等真实结果，前端必须继续如实展示。

## 5. CTA 原则

首屏只突出一个主动作：

- 检查失败 -> `重新检查运行条件`
- 后端阻断 -> `查看运行阻断`
- 审批场景失败 -> `查看安全熔断选项`
- 检查中 -> 禁用等待
- Ready -> `执行标准扫描`

阻断详情中可以提供辅助入口：

- `核对企业资料` -> `/materials`
- `核对接入信息` -> `/settings`
- `重新检查`

前端不根据 blocker code 猜测根因并自动跳到某个错误页面。

## 6. 信息层级

Run Center 首屏顺序：

```text
页面标题
-> 运行前检查结论
-> 运行辅助事实
-> 当前运行结论 / 阻断
-> 唯一主动作
-> 完整阻断详情（仅有阻断时）
-> 本次自动选择与安全边界（折叠）
-> 异常覆盖与安全熔断（折叠）
-> 真实运行结果
```

旧的独立五张 readiness 卡不再拥有自己的“可以开始”判断，避免和 Preflight 产生第二套 readiness truth。

## 7. 失败状态隔离

Preflight 读取错误与真实扫描执行错误必须使用不同状态：

- `preflightError`：当前无法确认运行条件；
- `error`：真实扫描提交/执行失败。

不能因为刷新 Preflight 失败就显示“验证未启动”的运行错误口径。

## 8. 移动端

- 桌面辅助事实可以五列；
- 中等宽度降为两列；
- <= 640px 降为单列；
- 主动作和重新检查按钮单列全宽；
- blocker 与 authority badge 不得横向溢出。

## 9. CI 合同

`test:run-preflight-decision` 必须锁定：

- backend `ready` 是唯一正向权威；
- Preflight 读取失败 fail-closed；
- 运行 handler 再次检查 `preflightReady`；
- disabled service 凭据不计入当前上下文；
- 只统计 active 企业资料；
- 自动 API source 只从 active source 选择；
- 资料类型保持 open-ended；
- 不使用前端 score/confidence 阈值决定 readiness；
- 第一个 blocker 只叫“首个上报”，不能伪造优先级；
- 首屏组件必须说明辅助事实不能放行扫描；
- 移动端必须单列收口。

`test:settings-onboarding` 与 `test:customer-action-guidance` 继续锁定同一 backend-preflight authority。

## 10. 非目标

本 SPEC 不定义：

- Preflight 后端如何判断 ready；
- blocker code 如何生成或排序；
- 系统连通性探针；
- 登录成功判定；
- 企业资料语义理解质量；
- 测试数据生成；
- 扫描场景规划；
- Bug 发现算法；
- Release Gate。

前端只负责把真实运行条件解释清楚，并确保**不能因为 UI 看起来准备好了就绕过后端 Preflight。**
