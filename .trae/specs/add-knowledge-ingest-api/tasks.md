# Tasks
- [x] Task 1: 盘点现有知识中心导入链路与 HTTP API 缺口
  - [x] SubTask 1.1: 确认当前 `phase104_command_center_http_api` 已暴露的项目/知识相关接口
  - [x] SubTask 1.2: 梳理现有知识中心 ingest 入口、输入格式和返回结果
  - [x] SubTask 1.3: 明确前端拖拽上传所需最小契约与失败语义

- [x] Task 2: 实现后端资料导入接口
  - [x] SubTask 2.1: 新增资料导入路由与请求/响应模型
  - [x] SubTask 2.2: 把请求适配到现有知识中心 ingest 链路，避免重复实现核心结构化逻辑
  - [x] SubTask 2.3: 为项目不存在、空内容、非法参数等场景补齐明确错误返回

- [x] Task 3: 对接前端资料上传调用
  - [x] SubTask 3.1: 让前端上传逻辑调用新的可用接口
  - [x] SubTask 3.2: 收敛成功/失败提示，避免直接暴露原始后端 JSON
  - [x] SubTask 3.3: 导入成功后刷新资料列表并保持“状态表达真实”

- [x] Task 4: 增加验证与回归
  - [x] SubTask 4.1: 为新导入接口增加后端自动化测试
  - [x] SubTask 4.2: 为前端上传调用增加最小回归验证
  - [x] SubTask 4.3: 实跑一次拖拽或等价上传流程，确认不再出现 404 占位接口问题

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 2
- Task 4 depends on Task 3
