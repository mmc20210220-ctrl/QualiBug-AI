from __future__ import annotations

"""
QualiBug 三层 LLM 架构 — Verifier 层 Prompt

Verifier 的职责：从 Reasoner 的风险假设出发，结合实际 API 响应数据，
判定每条假设是否成立。只看证据说话，不确定就是不确定。

这是当前 QualiBug 完全缺失的一层——之前引擎输出完 findings 就结束了，
没有对每条发现做证据质量的后验证。
"""

# ---------------------------------------------------------------------------
# Verifier 层系统提示词
# ---------------------------------------------------------------------------

VERIFIER_SYSTEM_PROMPT = """你是一个企业业务系统的"证据验证引擎"。
你的输入是风险推理引擎产生的假设，和实际的 API 响应/观测数据。
你的任务是对每条假设给出确定的结论。

你对每条假设有三种可能的判决：
1. confirmed — 有充分证据表明确实存在 Bug
2. falsified — 证据表明假设不成立（系统行为正确）
3. inconclusive — 证据不足以判定

铁律：
1. 只看实际返回的数据，不要推测"可能是"。
2. 如果数据不足以判定的，坦率说 inconclusive，不要编结论。
3. confirmed 需要至少 2 个独立证据点。
4. 数值比较需要用精确值（金额精确匹配，时间 ±5 秒）。
5. 不要重复假设本身——你的工作是验证假设，不是复述假设。
6. 如果某个已验证的 Bug 和其他已确认的 Bug 有关联，在 related_findings 中标明。
7. 输出 ONLY valid JSON。"""


# ---------------------------------------------------------------------------
# VER1: 通用假设验证 — 接收 Reasoner 产出 + API 数据，输出确认结论
# ---------------------------------------------------------------------------

VERIFIER_GENERAL_PROMPT = """验证以下风险假设是否在实际系统中成立。

## 待验证假设

{hypotheses}

## 实际 API 响应数据

{api_responses}

## 运行时观测日志

{runtime_observations}

## 验证任务

对每条假设：

1. 确认验证方法是否已执行：
   - 如果 api_responses 中有对应端点的返回 → 进行判定
   - 如果 api_responses 中没有对应数据 → 标记为 inconclusive + 说明缺少什么数据

2. 判定标准：
   - confirmed: 实际数据与假设的 symptoms_if_broken 完全吻合，且排除了合理解释
   - falsified: 实际数据不符合任何 symptoms_if_broken
   - inconclusive: 数据不足以判定，或存在多个解释

3. 证据质量打分（0-1）：
   - 1.0: 精确数值不匹配，排除了浮点误差/时区/格式差异
   - 0.8: 模式高度吻合但有一个替代解释未排除
   - 0.5: 部分吻合，需要更多数据
   - 0.2: 只有间接证据

## 输出

```json
{{
  "verifications": [
    {{
      "hypothesis_id": "原始假设 ID",
      "verdict": "confirmed|falsified|inconclusive",
      "confidence": 0.0-1.0,
      "evidence_points": [
        {{
          "type": "api_response|log_entry|state_comparison",
          "source": "具体数据来源",
          "value": "观测到的值",
          "relevance": "这个证据为什么支持或反对假设"
        }}
      ],
      "why_this_verdict": "一句话解释判定理由",
      "if_confirmed": {{
        "severity": "P0|P1|P2|P3",
        "reproduction_steps": ["可复现步骤"],
        "impact_assessment": "影响评估（资损金额/影响用户数/数据风险）"
      }},
      "if_inconclusive": {{
        "missing_data": ["还需要什么数据才能判定"],
        "suggested_next_probe": "建议的下一步验证"
      }},
      "related_confirmed_bugs": ["关联的已确认 Bug ID"]
    }}
  ],
  "summary": {{
    "total_hypotheses": 0,
    "confirmed": 0,
    "falsified": 0,
    "inconclusive": 0,
    "new_high_severity_bugs": 0
  }}
}}
```"""


# ---------------------------------------------------------------------------
# VER2: Bug 分类与指纹提取 — 确认 Bug 后自动沉淀检测信号
# ---------------------------------------------------------------------------

VERIFIER_CLASSIFICATION_PROMPT = """对以下已确认的 Bug 进行分类，并生成可复用的检测指纹。

## 已确认的 Bug

{finding}

## 历史 Bug 指纹库（用于对比和去重）

{bug_history}

## 任务

1. **分类**：映射到最精确的 Bug 类别
2. **相似性**：在历史指纹库中查找相似的 Bug
3. **泛化**：这个 Bug 的检测模式能否泛化到其他场景？
4. **优先级**：是否应该加入回归测试套件？
5. **学习**：什么关键词/语义信号能更早地发现这类 Bug？

## 输出

```json
{{
  "classification": {{
    "primary_category": "causality|reconciliation|invariant|lifecycle|saga|consistency|event_chain|population|outcome|metamorphic|temporal|counterexample",
    "sub_category": "具体模式名",
    "severity": "P0|P1|P2|P3",
    "is_novel": true/false
  }},
  "similar_confirmed_bugs": ["bug_ids"],
  "generalized_fingerprint": {{
    "pattern_name": "人类可读的 Bug 类名",
    "detection_signals": ["关键词", "字段模式", "语义条件"],
    "false_positive_risks": ["何时这个模式实际上是正确的"],
    "suggested_oracle": "什么不变量/检查能发现它"
  }},
  "promotion_recommendation": {{
    "promote_to_regression": true/false,
    "reason": "为什么这个 Bug 值得持续监控"
  }},
  "auto_learn": {{
    "should_auto_add_to_pattern_library": true/false,
    "min_confidence_threshold": 0.0-1.0,
    "applicable_industries": ["all", "fintech", "ecommerce", "..."],
    "suggested_pre_seeded_pattern": {{完整的新 PRE_SEEDED_PATTERNS 条目}}
  }}
}}
```"""


# ---------------------------------------------------------------------------
# VER3: 跨引擎关联验证 — 发现不同引擎的独立证据指向同一个 Bug
# ---------------------------------------------------------------------------

VERIFIER_CROSS_ENGINE_PROMPT = """分析以下来自不同引擎的发现，找出可能指向同一个根因 Bug 的独立证据。

## 引擎 A 的发现（如 causality）

{engine_a_findings}

## 引擎 B 的发现（如 reconciliation）

{engine_b_findings}

## 引擎 C 的发现（如 lifecycle）

{engine_c_findings}

## 任务

1. 找出不同引擎的发现中，指向**同一业务实体、同一操作**的独立证据
2. 合并为复合 Bug——多个独立引擎的证据相互印证，置信度更高
3. 标注：哪些发现是同一个根因的不同症状

## 输出

```json
{{
  "composite_bugs": [
    {{
      "composite_id": "COMP-XXX",
      "root_cause": "根本原因（一句话）",
      "contributing_findings": [
        {{"engine": "causality", "finding_id": "CAUSAL-001", "symptom": "金额不守恒"}},
        {{"engine": "reconciliation", "finding_id": "RECON-003", "symptom": "列表与详情金额不一致"}}
      ],
      "merged_severity": "P0|P1（取各发现中最严重的）",
      "merged_confidence": 0.0-1.0,
      "why_composite": "为什么这些独立发现指向同一个根因"
    }}
  ],
  "standalone_findings": ["与其他发现无明显关联的 finding IDs"],
  "cross_engine_insight": "跨引擎综合分析"
}}
```"""


# ---------------------------------------------------------------------------
# Prompt 注册表 — Verifier 层
# ---------------------------------------------------------------------------

VERIFIER_PROMPTS = {
    "general": VERIFIER_GENERAL_PROMPT,
    "classification": VERIFIER_CLASSIFICATION_PROMPT,
    "cross_engine": VERIFIER_CROSS_ENGINE_PROMPT,
}
