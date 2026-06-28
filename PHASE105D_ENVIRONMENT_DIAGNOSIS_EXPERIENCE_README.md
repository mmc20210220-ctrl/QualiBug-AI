# Phase105D 环境诊断中心真实 UI

Phase105D 继续聚焦前端显示层，建设客户环境诊断中心页面。它用于把 URL、DNS、HTTP、认证、Session、API Smoke、客户补料、安全执行模式这些偏技术的信息翻译成客户能理解的“现在能不能测、哪里阻断、下一步补什么”。

## 输出

```text
environment_diagnosis.html
assets/qualibug_environment_diagnosis.css
assets/qualibug_environment_diagnosis.js
data/environment_diagnosis_experience_data.json
README_ENVIRONMENT_DIAGNOSIS_EXPERIENCE.md
environment_diagnosis_experience_manifest.json
environment_diagnosis_experience_acceptance_report.json
environment_diagnosis_experience_acceptance_report.md
```

## 运行

```powershell
python -m ai_test_asset_center.phase105_environment_diagnosis_experience --scenario manufacturing --output-dir .\outputs\phase105_environment_diagnosis_experience
Start-Process .\outputs\phase105_environment_diagnosis_experience\environment_diagnosis.html
```

## 只验收

```powershell
python -m ai_test_asset_center.phase105_environment_diagnosis_experience --validate-only --output-dir .\outputs\phase105_environment_diagnosis_experience
```

## 设计重点

- 首页展示可测性评分和结论。
- URL / DNS / HTTP / 认证 / API Smoke 分层展示。
- API Smoke 失败不直接等同于 Bug，而是解释为权限、路径、租户绑定或认证上下文问题。
- 客户补料项必须明确说明为什么需要、影响哪些业务链路、建议客户提供什么。
- 默认不展示 password、access_token、refresh_token、cookie、session、Authorization、client_secret 原值。
