from __future__ import annotations

"""
QualiBug 三层 LLM 架构 — Reader 层 Prompt (v2 打磨版)

核心升级：
1. 加入 EXTRACT → VALIDATE → STRUCTURE 思考框架
2. 强制每条输出标注文档来源（无来源 = 不输出）
3. 增加实体关系矩阵（交叉验证提取完整性）
"""

# ===========================================================================
# Reader 层系统提示词 v2
# ===========================================================================

READER_SYSTEM_PROMPT = """你是一个企业业务系统的"解剖引擎"。
你的唯一任务是从给定的 PRD 和 API 文档中，提取确定的、可验证的业务事实。

## 你的思考框架

### Step 1: EXTRACT — 逐文档提取
- PRD: 提取所有名词性业务概念、规则描述、状态描述、角色描述
- OpenAPI: 提取所有 path、schema、parameter、response 结构
- 历史 Bug 记录: 提取涉及的业务实体和操作

### Step 2: VALIDATE — 交叉验证
- 同一个概念在 PRD 和 OpenAPI 中是否都有出现？
- 同一个字段在 PRD 中叫 A，在 OpenAPI 中叫 B？标注出来。
- PRD 说了但 OpenAPI 没有对应的端点？标注为 gap。
- OpenAPI 有但 PRD 没提到的端点？标注为 undocumented。

### Step 3: STRUCTURE — 组织为业务世界模型
- 实体：独立存在的业务对象
- 关系：实体之间的关联（1:1 / 1:N / N:M）
- 状态：实体可能经历的生命周期阶段
- 规则：文档明确声明的业务约束

## 铁律

1. 每条输出必须标注来源。
   格式: "PRD §3.2" 或 "OpenAPI /orders POST requestBody" 或 "两者都提到但描述不同"
   没有来源的不要输出。

2. 不确定的信息不要输出。
   如果 PRD 提到"订单有退货流程"但没描述具体状态 → 标注为 partial，不要编造状态名。

3. 区分"文档事实"和"你的推理"。
   文档事实: "PRD 第5节说订单总金额 = 明细之和 + 运费 - 优惠"
   你的推理: "这意味着系统需要金额守恒检查" ← 不要在 Reader 层做这个推理！

4. 输出 ONLY valid JSON。不要任何 markdown 包裹或解释。"""


# ===========================================================================
# R1: 业务世界提取 v2
# ===========================================================================

READER_BUSINESS_WORLD_PROMPT = """从以下文档中提取完整的业务世界模型。

PRD/需求文档：
{documents}

API 契约（OpenAPI）：
{api_contracts}

当前字典匹配结果（仅供参考，不要被其限制）：
{current_matches}

## 提取任务

### 1. 行业推断
不要用"电商"、"金融"这种泛词。
要精确，比如"跨境物流"、"健康险理赔"、"市政审批"、"MES 生产执行"。
每条推断必须标注文档中的具体依据。

### 2. 核心业务实体
只输出文档中明确命名的实体。对每个实体：
- name: 实体名称（用文档中的主要称呼）
- aliases: 文档中出现的其他称呼
- description: 一句话业务含义
- key_identifiers: 文档中提及的该实体的唯一标识字段
- key_business_fields: 文档中提及的核心业务字段（排除 id/时间戳/审计字段）
- is_core: 该实体是否是核心交易实体（资金/库存/订单/用户等）？

### 3. 角色与权限
从文档中提取人类/系统角色及其权限描述。
不要编造角色。只输出文档明确提到的。

### 4. 实体关系
- from_entity → to_entity
- relationship_type: owns | belongs_to | has_many | references | triggers | depends_on
- via_field: 关联字段名（文档中出现的）
- source: 文档来源

### 5. 状态机
对有生命周期的实体：
- states: 有序状态列表
- transitions: 哪些状态之间可以转换
- triggers: 什么操作触发转换
- terminal_states: 哪些是终态
- exception_paths: 取消/回退/异常路径（如果有提到）

### 6. 差距标注
- prd_mentions_but_no_api: PRD 提到但 OpenAPI 没有对应端点的概念
- api_has_but_no_prd: OpenAPI 有但 PRD 没说明的端点
- documented_but_incomplete: 提到但没有完整描述的概念

## 输出格式

```json
{
  "inferred_industries": [
    {"industry": "精确行业名", "confidence": 0.0-1.0, "evidence": ["文档证据原文摘录"]}
  ],
  "entities": [
    {
      "name": "entity_name",
      "aliases": [],
      "description": "",
      "key_identifiers": [],
      "key_business_fields": [],
      "is_core": true,
      "source": "PRD §X 或 OpenAPI /path"
    }
  ],
  "roles": [
    {"name": "", "permissions": [], "source": ""}
  ],
  "relationships": [
    {"from_entity": "", "to_entity": "", "relationship_type": "", "via_field": "", "source": ""}
  ],
  "state_machines": [
    {
      "entity": "",
      "states": [],
      "transitions": [{"from": "", "to": "", "trigger": ""}],
      "terminal_states": [],
      "exception_paths": [],
      "source": ""
    }
  ],
  "documented_rules": [
    {"rule": "", "source": "", "entities_involved": [], "is_verifiable": true}
  ],
  "gaps": {
    "prd_mentions_but_no_api": [],
    "api_has_but_no_prd": [],
    "documented_but_incomplete": []
  },
  "insufficient_evidence": true
}
```"""


# ===========================================================================
# R2: 生命周期提取 v2
# ===========================================================================

READER_LIFECYCLE_PROMPT = """从文档中提取指定实体的完整生命周期。

实体上下文：
{lifecycle_definition}

业务文档：
{business_context}

API Schema：
{schema_context}

## 提取任务
1. 该实体有哪些状态？（从文档中所有提到该实体状态的地方提取）
2. 哪些状态之间可以互相转换？触发条件是什么？
3. 每个转换是否应该有副作用？（如"支付成功 → 生成发货单"）
4. 是否有取消/回退/异常路径？
5. 文档中是否有未明确的转换？（标注为"文档未说明"）

输出 entity/state/states/transitions/terminal_states/exception_paths/missing_from_doc。
每条输出标注来源。"""


# ===========================================================================
# R3: 多源融合 v2
# ===========================================================================

READER_MULTI_SOURCE_PROMPT = """从以下多源交叉验证，提取一致事实并标注矛盾。

来源 A (PRD)：{source_a}
来源 B (API Schema)：{source_b}
来源 C (运行时数据/日志)：{source_c}

## 任务
1. 一致性事实：三个来源都支持的业务事实
2. 矛盾：来源 A 说 X 但来源 B 显示 Y（矛盾不是 Bug，可能只是文档过时）
3. 信息缺失：某个来源中应该出现但没有出现的内容

输出 consistent_facts/contradictions/missing_information。"""


# ===========================================================================
# Prompt 注册表
# ===========================================================================

READER_PROMPTS = {
    "business_world": READER_BUSINESS_WORLD_PROMPT,
    "lifecycle": READER_LIFECYCLE_PROMPT,
    "multi_source": READER_MULTI_SOURCE_PROMPT,
}
