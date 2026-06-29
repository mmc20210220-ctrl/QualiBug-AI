# Tasks
- [x] Task 1: 盘点并固化当前前端基线
  - [x] SubTask 1.1: 以最新仓库代码确认 `Phase105A`、`Phase106A`、`Phase106C/D` 已有能力与明显缺口
  - [x] SubTask 1.2: 明确核心交付入口、生成器依赖链和现有验收测试覆盖面
  - [x] SubTask 1.3: 把“脚手架级前端”与“商用展示层”之间的差距映射到具体页面与状态能力

- [x] Task 2: 升级全局产品壳与项目工作区
  - [x] SubTask 2.1: 完善导航、顶部状态区、项目切换、运行模式标识与后端健康状态展示
  - [x] SubTask 2.2: 增加统一的加载态、空态、失败态、离线态和危险动作确认
  - [x] SubTask 2.3: 保证项目切换后仍保持当前路由与工作区连续性

- [x] Task 3: 将核心页面改造成产品化界面
  - [x] SubTask 3.1: 改造质量驾驶舱、客户资料导入、环境诊断页面，替换主区域 JSON 直出
  - [x] SubTask 3.2: 改造业务流程地图、测试执行页面，补齐流程、节点、时间线和动作反馈
  - [x] SubTask 3.3: 改造风险证据与报告 ROI 页面，补齐业务影响、证据链、修复建议、导出/汇报状态

- [x] Task 4: 打通 Demo / 真实 API 双模式可信表达
  - [x] SubTask 4.1: 将健康检查结果接入顶部状态区和页面级状态块
  - [x] SubTask 4.2: 明确区分“已配置”“未验证”“离线”“在线”“错误”几类模式
  - [x] SubTask 4.3: 保证真实 API 请求失败时回退到明确错误或离线反馈，而不是伪健康展示

- [x] Task 5: 建立商用品质验收与回归保护
  - [x] SubTask 5.1: 补充或更新前端合同测试、路由冒烟测试和 API 模式测试
  - [x] SubTask 5.2: 增加“页面主视图不能以原始 JSON dump 为主”的回归断言
  - [x] SubTask 5.3: 验证响应式可用性、脱敏与构建通过，收敛为可交付验收结果

- [x] Task 6: 确认工作区基线确实来自最新仓库代码
  - [x] SubTask 6.1: 盘点并解释当前 `git status` 中的修改/新增（哪些是本地生成物、哪些应纳入版本控制）
    - 修改（21）：集中在 `ai_test_asset_center/*` 的发现/分析能力与 `Phase106` 前端运行时、`aitestops/cli.py`、以及对应测试用例文件，属于应纳入版本控制的源码与回归测试变更。
    - 新增（27）：集中在前端/性能/稳定性/兼容性等 discovery adapter 与 registry/oracle/schema 模块、Playwright 离线包脚本、以及对应 tests 与交付规格文档（`.trae/specs/deliver-commercial-frontend/*`、`.trae/documents/*`），均属于应纳入版本控制的源码/测试/规格内容。
    - 未发现典型本地生成物（如 `dist/`、`build/`、`__pycache__/`、日志、临时产物）混入 `git status` 输出。
  - [x] SubTask 6.2: 与远端仓库同步并确认 HEAD 为最新（fetch/rebase 或 merge 后复核）
    - 已执行 `git fetch --prune origin`。
    - 远端默认分支为 `main`（`origin` 的 `HEAD branch: main`；远端 `HEAD` symref 指向 `refs/heads/main`）。
    - 本地 `HEAD` commit 为 `5276eab75deed7274ffdffb86579267b412c9ac8`，与远端 `HEAD` 返回的 commit 一致。
    - 备注：本地当前分支显示 `[origin/main: gone]`，且远端未返回 `refs/heads/main` 的常规行（仅返回 `HEAD` 相关信息）；因此本次以“远端 HEAD commit 一致”作为同步性依据，而不是以 `origin/main` ref 作为比较基准。
  - [x] SubTask 6.3: 以fetch+HEAD一致+pytest/manifest/checksum证明可复现
    - 不以“工作区 clean”作为可复现必要条件，以可执行证据链（pytest/manifest/checksum）为准。

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 2
- Task 5 depends on Task 3
- Task 5 depends on Task 4
