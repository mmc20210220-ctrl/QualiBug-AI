# Phase105G：领导层报告 + ROI 价值中心真实 UI

Phase105G 继续聚焦前端显示层，把 AI 测试结果转成管理层能直接汇报的页面：是否建议上线、为什么、哪些风险阻断、节省多少测试工时、风险价值区间是多少、下一步由谁处理、复验探针是什么。

## 输出

```text
report_roi.html
assets/qualibug_report_roi.css
assets/qualibug_report_roi.js
data/report_roi_experience_data.json
README_REPORT_ROI_EXPERIENCE.md
report_roi_experience_manifest.json
report_roi_experience_acceptance_report.json
report_roi_experience_acceptance_report.md
```

## 运行

```powershell
python -m ai_test_asset_center.phase105_report_roi_experience --scenario manufacturing --output-dir .\outputs\phase105_report_roi_experience
Start-Process .\outputs\phase105_report_roi_experience\report_roi.html
```

只复验已有输出：

```powershell
python -m ai_test_asset_center.phase105_report_roi_experience --validate-only --output-dir .\outputs\phase105_report_roi_experience
```

## 验收重点

- 领导层报告：执行摘要、上线建议、证据可信度、可复制摘要。
- ROI 价值中心：节省工时、AI 等价测试点、业务影响区间、计算说明。
- 风险价值：Top 风险业务影响和证据可信度。
- 下一步动作：负责人建议、优先级、复验探针。
- 安全脱敏：不得输出 token / cookie / password / session / client_secret / traceback 原值。
