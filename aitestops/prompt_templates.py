from __future__ import annotations


SYSTEM_PROMPT = """你是企业级 AI TestOps 架构师。你必须只输出严格 JSON，不要输出 Markdown，不要输出解释。
你的目标不是直接写测试代码，而是生成可审计、可校验、可转换为自动化脚本的测试资产。
禁止输出真实个人信息、真实账号、真实密码、真实手机号、真实身份证、真实银行卡。
测试数据只能使用 synthetic_only 数据需求。
"""


ASSET_GENERATION_PROMPT = """请根据下面需求，生成企业 AI 自动化测试资产。只输出一个 JSON 对象。

必须符合这个顶层结构：
{
  "analysis": {
    "business_rules": ["string"],
    "risks": [
      {
        "risk_id": "string",
        "risk": "string",
        "priority": "P0|P1|P2|P3",
        "reason": "string",
        "recommended_test_type": ["api|ui|security|contract|data|permission|regression"]
      }
    ]
  },
  "test_cases": [
    {
      "case_id": "LOGIN_001",
      "title": "string",
      "priority": "P0|P1|P2|P3",
      "type": "api|ui|security|contract|data|permission|regression",
      "risk_refs": ["risk_id"],
      "data_profile": "active_normal_user",
      "steps": ["string"],
      "expected": ["string"],
      "automation_candidate": true
    }
  ],
  "test_data_profiles": {
    "active_normal_user": {
      "entity": "user",
      "role": "user|admin|operator|guest",
      "status": "active|locked|expired|disabled",
      "password_state": "valid|invalid|expired",
      "create_strategy": "factory",
      "cleanup": "auto",
      "privacy_level": "synthetic_only"
    }
  },
  "test_dsl": [
    {
      "case_id": "LOGIN_001",
      "title": "string",
      "type": "api",
      "priority": "P0",
      "data_profile": "active_normal_user",
      "actions": [
        {"action": "create_user", "as": "user", "role": "user", "status": "active"},
        {"action": "login", "username": "{{user.username}}", "password": "{{user.password}}", "as": "login_result"}
      ],
      "assertions": [
        {"target": "login_result.success", "operator": "equals", "value": true}
      ]
    }
  ]
}

当前 MVP 执行器只支持这些 action：
- create_user: 必须包含 as, role, status
- login: 必须包含 username, password, as
- login_wrong_password: 必须包含 times, as
- check_access: 必须包含 username, resource, as

当前 MVP 断言只支持 operator=equals。
请至少生成 3 条用例，覆盖登录成功、账号锁定、普通用户不能访问管理员资源。

需求如下：
{requirement_text}
"""


FAILURE_ANALYSIS_PROMPT = """请分析下面测试失败日志，只输出严格 JSON：
{
  "failure_type": "real_bug|test_data_issue|script_issue|environment_issue|unknown",
  "confidence": 0.0,
  "suspected_owner": "frontend|backend|qa|devops|unknown",
  "summary": "string",
  "evidence": ["string"],
  "suggested_bug_title": "string",
  "severity": "P0|P1|P2|P3",
  "next_actions": ["string"]
}

失败日志：
{failure_log}
"""
