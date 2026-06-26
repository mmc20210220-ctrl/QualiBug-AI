# V11.3 可视化报告补丁

目标：把平台输出从“文件列表/Markdown”升级成“可视化报告”。

## 覆盖运行

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\ai_test_asset_center_v11_3_visual_report_patch.zip" -DestinationPath . -Force
.\RUN_OPEN_PLATFORM.cmd
```

页面里填写：

1. PRD / 需求文档
2. 接口文档 / OpenAPI URL 或 JSON

点击“生成 AI 测试分析”。

## 输出

可视化报告：

```text
platform_outputs\enterprise_shop\visual_report.html
platform_outputs\enterprise_shop\visual_report_data.json
```

页面内会直接显示可视化报告，也可以点击“新窗口打开”。
