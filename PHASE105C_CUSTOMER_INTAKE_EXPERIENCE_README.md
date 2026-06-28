# Phase105C 客户资料导入页体验

Phase105C 将前端显示层继续推进到产品入口页：客户资料导入。

## 目标

把客户上传企业资料、选择行业、确认业务链路、补充账号角色、确认安全边界、生成测试计划这条链路做成可展示、可验收、可复制的静态前端页面。

## 生成内容

- `customer_intake.html`
- `assets/qualibug_customer_intake.css`
- `assets/qualibug_customer_intake.js`
- `data/customer_intake_experience_data.json`
- `README_CUSTOMER_INTAKE_EXPERIENCE.md`
- `customer_intake_experience_manifest.json`
- `customer_intake_experience_acceptance_report.json`
- `customer_intake_experience_acceptance_report.md`

## 使用

```powershell
python -m ai_test_asset_center.phase105_customer_intake_experience --scenario manufacturing --output-dir .\outputs\phase105_customer_intake_experience
Start-Process .\outputs\phase105_customer_intake_experience\customer_intake.html
```

只复验已有输出：

```powershell
python -m ai_test_asset_center.phase105_customer_intake_experience --validate-only --output-dir .\outputs\phase105_customer_intake_experience
```

## 安全

页面和数据文件默认通过脱敏函数处理，并扫描 token、cookie、session、client_secret、traceback 等原始泄露模式。
