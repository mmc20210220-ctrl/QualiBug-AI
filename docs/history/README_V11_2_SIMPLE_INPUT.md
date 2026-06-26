# V11.2 极简输入补丁

这版把测试平台页面输入简化为两个核心输入：

1. PRD / 需求文档
2. 接口文档 / OpenAPI

接口文档支持两种方式：

- 直接粘贴 OpenAPI / Swagger JSON
- 填 OpenAPI URL，例如 `http://127.0.0.1:8000/openapi.json`

其他信息不作为初始必填输入：健康检查 URL、Git Diff、失败日志、CI 产物、Trace 截图都属于后续阶段自动追加或高级配置。

## 使用方式

在 AI Test Asset Center 项目根目录执行：

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\ai_test_asset_center_v11_2_simple_input_patch.zip" -DestinationPath . -Force
.\RUN_OPEN_PLATFORM.cmd
```

打开页面后，只填两个框：

- PRD / 需求文档
- 接口文档 / OpenAPI

然后点击“生成 AI 测试分析”。

## 输出目录

- `platform_inputs\enterprise_shop`
- `platform_workspace\enterprise_shop`
- `platform_outputs\enterprise_shop`
