# PoC 演示脚本

## 演示目标

证明 AI 自动化测试不是传统框架换壳，而是能减少测试资产生成和维护成本。

## 演示步骤

### 1. 展示需求

打开：

```text
examples/login_requirement.md
```

说明需求包含登录、账号锁定、权限控制和测试数据要求。

### 2. 生成测试资产

执行：

```bash
python -m aitestops.cli generate --requirement examples/login_requirement.md --out outputs/login
```

展示生成结果：

- risks.json
- test_cases.json
- test_data_profiles.json
- test_dsl.yaml
- generated_pytest_test.py
- generation_summary.md

### 3. 强调和传统自动化的区别

传统方式是人工写测试点、人工写数据、人工写脚本。

本 PoC 是：

```text
AI/规则引擎生成测试意图和资产 → 模板引擎生成可执行脚本
```

测试工程师主要负责规则、审核和治理。

### 4. 执行生成测试

```bash
pytest outputs/login/generated_pytest_test.py -q
```

展示：

```text
3 passed
```

### 5. 展示失败归因

```bash
python -m aitestops.cli analyze-failure --log examples/failure_log.txt --out outputs/failure_analysis.md
```

打开 `outputs/failure_analysis.md`。

说明 AI 可以基于日志、接口返回、页面状态和代码变更线索，给出失败分类、证据、责任域和缺陷标题草稿。

## 面试演示话术

> 这个 PoC 的重点不是多写几条自动化用例，而是验证一条 AI TestOps 闭环：需求进来后，系统自动生成风险、用例、数据需求和 DSL，再通过模板生成可执行测试。执行失败后，系统根据日志生成失败归因和缺陷草稿。这样可以减少测试分析、测试数据维护、脚本生成和失败排查成本。
