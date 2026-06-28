# Phase105F 风险与证据链详情真实 UI

Phase105F 将前端显示层推进到“风险证明”环节：风险列表把技术 Bug 翻译成业务风险，证据链详情证明风险真实、可复现、可修复、可复验。

## 生成

```powershell
python -m ai_test_asset_center.phase105_risk_evidence_experience --scenario manufacturing --output-dir .\outputs\phase105_risk_evidence_experience
Start-Process .\outputs\phase105_risk_evidence_experience\risk_evidence.html
```

## 只验收已有输出

```powershell
python -m ai_test_asset_center.phase105_risk_evidence_experience --validate-only --output-dir .\outputs\phase105_risk_evidence_experience
```

## 输出文件

- `risk_evidence.html`
- `assets/qualibug_risk_evidence.css`
- `assets/qualibug_risk_evidence.js`
- `data/risk_evidence_experience_data.json`
- `README_RISK_EVIDENCE_EXPERIENCE.md`
- `risk_evidence_experience_manifest.json`
- `risk_evidence_experience_acceptance_report.json`
- `risk_evidence_experience_acceptance_report.md`

## 页面重点

- 风险与 Bug 列表
- 业务影响
- 阻断上线
- 证据链详情
- 复现步骤
- 请求响应摘要
- 快照对比
- 修复建议
- 关闭条件
- 默认脱敏
