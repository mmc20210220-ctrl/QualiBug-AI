# V10 平台输入 / 平台输出模式

V10 去掉“去被测系统目录执行脚本”的概念。

正确企业模式：

```text
AI Test Asset Center/
├── platform_inputs/项目名/      # 输入制品
├── platform_workspace/项目名/   # 运行工作区
└── platform_outputs/项目名/     # 输出报告
```

被测系统只提供制品，不接受测试平台写文件。
