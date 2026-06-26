# 多行业 Benchmark

这是 QualiBug 的公开、可复现多行业评测样例。每个样例只包含可公开的最小资料：

- `PRD.md`：业务规则和关键风险；
- `openapi.json`：接口语义；
- `accounts.json`：角色与凭证引用，不含密码或 Token；
- `schema.sql`：最小数据结构；
- `known_high_value_bug_seeds.json`：仅用于风险种子命中率与 Oracle 覆盖代理评测。

覆盖 CRM、ERP、金融、医疗、教育、SaaS 多租户和电商。报告只输出文档理解与风险种子覆盖代理指标，不能等同于真实客户生产缺陷发现率。

运行：

```bash
python -m ai_test_asset_center.enterprise_testops_control_plane --benchmark --project benchmark_demo
```
