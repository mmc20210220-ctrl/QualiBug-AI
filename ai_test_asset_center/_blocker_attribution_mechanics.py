"""Blocker Attribution — classify why each obligation is blocked.

SPEC v1.2 §6 + SPEC v1.2.1 §11: Two-Phase Blocker Attribution

Phase A (Reason Candidate): Map reason_code to attribution category.
Phase B (Evidence Refinement): Verify attribution against actual IR evidence,
    checking operations, source refs, binding satisfiability, adapters,
    actor config, conflicting sources, environment policy, irreversible writes.

Each obligation gets a unique primary_attribution + primary_reason,
with optional secondary_contributors.

Output: qualibug.blocker-attribution.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Attribution Categories ───────────────────────────────────────────────────

ATTRIBUTION_CATEGORIES = frozenset({
    "SOURCE_GAP",
    "BEHAVIOR_MODEL_GAP",
    "COMPILER_GAP",
    "OBSERVER_CAPABILITY_GAP",
    "BINDING_GRAPH_GAP",
    "FIXTURE_CAPABILITY_GAP",
    "ADAPTER_CAPABILITY_GAP",
    "ENVIRONMENT_GAP",
    "POLICY_SAFETY_BLOCK",
    "TARGET_SYSTEM_RESPONSE",
    "ORACLE_INPUT_GAP",
    "CLEANUP_CAPABILITY_GAP",
    "PERMISSION_GAP",
    "LLM_PROVIDER_GAP",
    "HYPOTHESIS_GENERATION_GAP",
    "CONTRACT_DERIVATION_GAP",
    "EMBEDDING_CAPABILITY_GAP",
    "LEARNING_FEEDBACK_GAP",
    "NORMAL_OUTCOME",
    "DISCOVERY_DIAGNOSTIC",
    "EXECUTION_BUDGET",
    "PLANNING_DEFERRED",
    "UNREGISTERED",
    "UNKNOWN",
})

RECOVERABILITY_VALUES = frozenset({
    "RECOVERABLE",
    "SOURCE_DEPENDENT",
    "ENVIRONMENT_DEPENDENT",
    "PERMANENTLY_BLOCKED",
    "UNKNOWN",
})

# ─── Reason Code → Attribution Mapping ────────────────────────────────────────

_REASON_ATTRIBUTION: dict[str, tuple[str, str, bool]] = {
    # reason_code: (attribution, recoverability, must_remain_blocked)
    "BLOCKED_MISSING_OBSERVER": ("OBSERVER_CAPABILITY_GAP", "RECOVERABLE", False),
    # The control arm was dispatched but its success was never proven, so the
    # oracle could not activate.  Attributing this to an observer gap sent
    # operators to declare read endpoints that were never the problem.
    "BLOCKED_CONTROL_ARM_NOT_PROVEN": ("ORACLE_INPUT_GAP", "RECOVERABLE", False),
    # An observer exists and ran, but its receipt was indeterminate.  Still an
    # observer-family gap, yet a different repair than a missing observer.
    "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE": ("OBSERVER_CAPABILITY_GAP", "RECOVERABLE", False),
    # The documented interface returned a framework-level 404 (route not
    # registered) — a documentation/implementation-drift defect on the target,
    # not a harness/observer gap. It remains a visible blocker so the drift
    # finding is reported rather than silently skipped.
    "BLOCKED_INTERFACE_NOT_IMPLEMENTED": ("TARGET_SYSTEM_RESPONSE", "SOURCE_DEPENDENT", True),
    "BLOCKED_MISSING_BINDING": ("BINDING_GRAPH_GAP", "RECOVERABLE", False),
    "BLOCKED_MISSING_FIXTURE": ("FIXTURE_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_MISSING_ACTOR": ("SOURCE_GAP", "SOURCE_DEPENDENT", False),
    "BLOCKED_MISSING_OPERATION": ("BEHAVIOR_MODEL_GAP", "RECOVERABLE", False),
    "BLOCKED_NON_REVERSIBLE_WRITE": ("CLEANUP_CAPABILITY_GAP", "SOURCE_DEPENDENT", False),
    "BLOCKED_INVALID_CLEANUP_PLAN": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_CLEANUP_CONTRACT_DRIFT": ("COMPILER_GAP", "RECOVERABLE", False),
    "BLOCKED_UNSUPPORTED_ADAPTER": ("ADAPTER_CAPABILITY_GAP", "ENVIRONMENT_DEPENDENT", False),
    "BLOCKED_CONFLICTING_SOURCE": ("SOURCE_GAP", "SOURCE_DEPENDENT", True),
    "BLOCKED_MISSING_ACTOR_SECRET": ("SOURCE_GAP", "SOURCE_DEPENDENT", False),
    "BLOCKED_ORACLE_INPUT_INCOMPLETE": ("ORACLE_INPUT_GAP", "RECOVERABLE", False),
    "MISSING_PRIMARY_OPERATION": ("BEHAVIOR_MODEL_GAP", "RECOVERABLE", False),
    "non_production_environment_required": ("ENVIRONMENT_GAP", "ENVIRONMENT_DEPENDENT", True),
    "HARNESS_FAILURE": ("TARGET_SYSTEM_RESPONSE", "UNKNOWN", False),
    "HARNESS_CLEANUP_EQUIVALENCE_FAILED": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_CLEANUP_EQUIVALENCE_INDETERMINATE": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE", False),
}


# The funnel and ledger must classify a terminal reason from the reason code
# itself.  Keep this registry beside the existing blocker-attribution authority
# so there is one mapping, rather than teaching each projection to infer a
# family from free-form detail text.  ``reason_family`` deliberately contains
# a few explicit non-blocking/deferred families which are not attribution
# categories (for example, a normal oracle rejection).
REASON_CODE_REGISTRY_SCHEMA = "qualibug.discovery-reason-code-registry.v1"


def _reason_definition(
    reason_family: str,
    *,
    recoverability: str = "UNKNOWN",
    is_blocking: bool = True,
    must_remain_blocked: bool = False,
) -> dict[str, Any]:
    return {
        "reason_family": reason_family,
        "recoverability": recoverability,
        "is_blocking": is_blocking,
        "must_remain_blocked": must_remain_blocked,
    }


REASON_CODE_REGISTRY: dict[str, dict[str, Any]] = {
    code: _reason_definition(
        attribution,
        recoverability=recoverability,
        must_remain_blocked=must_remain_blocked,
    )
    for code, (attribution, recoverability, must_remain_blocked)
    in _REASON_ATTRIBUTION.items()
}
REASON_CODE_REGISTRY.update({
    "ORACLE_NOT_VIOLATED": _reason_definition(
        "NORMAL_OUTCOME", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "ORACLE_NO_VIOLATION": _reason_definition(
        "NORMAL_OUTCOME", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "ASSERTION_NOT_VIOLATED": _reason_definition(
        "NORMAL_OUTCOME", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "SURFACE_DISCOVERY_OBSERVATION_ONLY": _reason_definition(
        "DISCOVERY_DIAGNOSTIC", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "FIELD_LEVEL_RULE_NOT_EXECUTABLE": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "STATE_RULE_PRECONDITION_NOT_ESTABLISHED": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "CONTRACT_ORACLE_HARNESS_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "HARNESS_CONNECTION_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "EXECUTION_OBSERVABILITY_GAP": _reason_definition("OBSERVER_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_POLICY": _reason_definition("POLICY_SAFETY_BLOCK", must_remain_blocked=True),
    "BLOCKED_TARGET_POLICY": _reason_definition("POLICY_SAFETY_BLOCK", must_remain_blocked=True),
    "BLOCKED_RUNTIME_TARGET": _reason_definition("POLICY_SAFETY_BLOCK", must_remain_blocked=True),
    "SLICE_BUDGET_REACHED": _reason_definition("EXECUTION_BUDGET", recoverability="RECOVERABLE"),
    "OBLIGATION_BUDGET_REACHED": _reason_definition("EXECUTION_BUDGET", recoverability="RECOVERABLE"),
    "OBLIGATION_NOT_IN_PLAN": _reason_definition("PLANNING_DEFERRED", recoverability="RECOVERABLE"),
    "DEFERRED": _reason_definition("PLANNING_DEFERRED", recoverability="RECOVERABLE"),
    "CLEANUP_COMPENSATION_FAILED": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "CLEANUP_EVIDENCE_INCOMPLETE": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "CLEANUP_WRITE_COVERAGE_MISMATCH": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "CLEANUP_ACTIVATION_REFERENCE_MISMATCH": _reason_definition(
        "CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"
    ),
    "CLEANUP_PROOF_DEFERRED_FIELD_ORACLE": _reason_definition(
        "CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"
    ),
    "LEGACY_EXECUTION_ERROR": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "ORACLE_EXCEPTION": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "POST_REQUEST_PRECONDITION_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "HARNESS_COVERAGE_FUNNEL_FAILED": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "VALIDATION_GATE_EXCEPTION": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "MAINLINE_RUNTIME_EXCEPTION": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "PARAMETER_BINDING_BLOCKED": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "COMPILE_RECEIPT_MISSING": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "COMPILE_STATUS_INVALID": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_COMPILE": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_EXECUTION": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "HARNESS_PRIORITIZATION_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "HARNESS_BLOCKER_ATTRIBUTION_FAILED": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_BINDING_CYCLE": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_BINDING_GRAPH_INVALID": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_BINDING_LOCATION_NOT_MATERIALIZABLE": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_COVERAGE_RECOVERY_RECEIPT_MISSING": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_EMPTY_CONSERVATION_TERMS": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_FIXTURE_DAG_DRIFT": _reason_definition("FIXTURE_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_FORBIDDEN_BINDING_SOURCE": _reason_definition("BINDING_GRAPH_GAP", recoverability="SOURCE_DEPENDENT"),
    "BLOCKED_OBSERVER_CONTRACT_DRIFT": _reason_definition("OBSERVER_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_OBSERVER_RESOLUTION_FAILED": _reason_definition("OBSERVER_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_FIXTURE_CONTRACT_FAILED": _reason_definition("FIXTURE_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "ASSERTION_INDETERMINATE": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_DATABASE_NUMERIC_HTTP_FALLBACK_OBSERVER_MISSING": _reason_definition(
        "OBSERVER_CAPABILITY_GAP", recoverability="RECOVERABLE"
    ),
    "HARNESS_CLEANUP_TRANSPORT_FAILED": _reason_definition(
        "CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"
    ),
    "BLOCKED_PLAN_STEP_IDENTITY_INVALID": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_PRECONDITION_UNREACHABLE": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_REGISTERED_PROTOCOL_INVALID": _reason_definition("ADAPTER_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_STEP_CLEANUP_UNCOVERED": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="SOURCE_DEPENDENT"),
    "BLOCKED_STEP_EVIDENCE_UNOBSERVABLE": _reason_definition("OBSERVER_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "DB_CLEANUP_AUTHORITY_NOT_DECLARED": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="SOURCE_DEPENDENT"),
    "DB_DEPENDENCY_GRAPH_INCOMPLETE": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "DB_PREIMAGE_NOT_CAPTURED": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="SOURCE_DEPENDENT"),
    "DB_ROW_IDENTITY_NOT_BOUND": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_MISSING_REQUIRED_BODY_FIELDS": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_FABRICATED_FOREIGN_KEY": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_FLOW_DATA_BINDING_INCOMPLETE": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_FLOW_DATA_BINDING_AMBIGUOUS": _reason_definition("BINDING_GRAPH_GAP", recoverability="SOURCE_DEPENDENT"),
    "BLOCKED_FLOW_DATA_EXECUTION_CONTRACT_INCOMPLETE": _reason_definition("BINDING_GRAPH_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_PRECONDITION_IDENTITY_OUTPUT_MISSING": _reason_definition("BINDING_GRAPH_GAP", recoverability="SOURCE_DEPENDENT"),
    "BLOCKED_PRECONDITION_IDENTITY_OUTPUT_AMBIGUOUS": _reason_definition("BINDING_GRAPH_GAP", recoverability="SOURCE_DEPENDENT"),
    "MULTI_LEVEL_DEPENDENCY_IDENTITY_SOURCE_MISSING": _reason_definition("BINDING_GRAPH_GAP", recoverability="SOURCE_DEPENDENT"),
    "MULTI_LEVEL_DEPENDENCY_IDENTITY_SOURCE_AMBIGUOUS": _reason_definition("BINDING_GRAPH_GAP", recoverability="SOURCE_DEPENDENT"),
    "BLOCKED_UNAUTHORIZED_ACTOR": _reason_definition("PERMISSION_GAP", recoverability="RECOVERABLE"),
})


# ─── Diagnostic Guidance (chain positioning) ─────────────────────────────────
#
# Every registered reason code gets human-readable operator guidance so the
# discovery-chain positioning view can answer "卡在哪、为什么、怎么修" in one
# place.  Guidance is SYNTHETIC DIAGNOSTIC TEXT — it is clearly marked
# ``guidance_kind`` and never satisfies the customer-delivery gate, never
# becomes finding evidence, and never enters prompts or runtime context.

GUIDANCE_KIND = "synthetic_diagnostic_guidance"


def _guidance(
    meaning: str,
    likely_root_cause: str,
    suggested_action: str,
) -> dict[str, str]:
    return {
        "meaning": meaning,
        "likely_root_cause": likely_root_cause,
        "suggested_action": suggested_action,
        "guidance_kind": GUIDANCE_KIND,
    }


# Family-level guidance: every registered code without a per-code override
# inherits its family's guidance.
_FAMILY_GUIDANCE: dict[str, dict[str, str]] = {
    "SOURCE_GAP": _guidance(
        "来源材料缺失或不足：契约要求的操作/actor/字段在已提交的来源材料中没有对应声明。",
        "提交的 API 文档/PRD/账号清单缺少该操作、actor 或字段的显式声明，或声明与运行配置不一致。",
        "补充来源材料（API_SPEC/PRD/测试账号），或在扫描配置中显式声明缺失的契约条目。",
    ),
    "BEHAVIOR_MODEL_GAP": _guidance(
        "行为模型（Behavior IR）缺口：来源材料存在但未形成可执行的操作/状态模型。",
        "来源解析（OpenAPI/PRD 归一化）未能产出操作身份，或操作身份与 IR 规范化键不一致。",
        "检查来源解析结果（universal_api_parser 输出），确认操作路径/方法与 IR 规范化一致。",
    ),
    "COMPILER_GAP": _guidance(
        "编译层缺口：义务或实验编译阶段校验失败。",
        "编译器的前置校验（身份/收据/守恒项）拒绝，通常是产物形状不完整或与注册协议模板不一致。",
        "查看编译收据的 detail 字段定位具体校验；这是产品侧缺陷，按编译错误修复。",
    ),
    "OBSERVER_CAPABILITY_GAP": _guidance(
        "观察能力缺口：缺少观察者、观察者收据不明确或观察表面不可用。",
        "缺少来源声明的读取操作作为观察端点，或观察者注册/适配器未就绪。",
        "在来源中声明效果读取端点（collection GET/HEAD），或检查观察者注册与适配器声明。",
    ),
    "BINDING_GRAPH_GAP": _guidance(
        "绑定图缺口：义务引用的实体/字段/关系/状态未绑定到精确来源身份。",
        "来源材料缺少绑定所需的精确身份（唯一键/归属关系/状态枚举），或绑定存在歧义。",
        "补充来源声明（唯一键、归属关系、状态枚举），消除绑定歧义后重跑。",
    ),
    "FIXTURE_CAPABILITY_GAP": _guidance(
        "夹具（fixture）缺口：构造/物化测试数据所需的创建操作或补偿链不完整。",
        "缺少来源声明的创建操作、依赖解析或可逆补偿；或 fixture DAG 漂移。",
        "声明创建操作与补偿关系，或使用已声明的可复用测试数据。",
    ),
    "ADAPTER_CAPABILITY_GAP": _guidance(
        "适配器缺口：实验需要未声明的观察适配器（如 event_observer_http / ui_browser / db_sql）。",
        "目标表面（事件端点/UI/数据库）未在部署声明中登记；产品绝不从 URL/文本推断适配器。",
        "在 runtime_contract.declared_adapters 或扫描配置中显式声明目标暴露的表面。",
    ),
    "ENVIRONMENT_GAP": _guidance(
        "环境缺口：目标环境类型未声明或与写入策略冲突。",
        "environment_type 缺失/未声明（产品不默认 test），或写入被非生产环境策略拦截。",
        "显式声明目标环境类型（非生产），确认运行契约已批准。",
    ),
    "POLICY_SAFETY_BLOCK": _guidance(
        "策略安全阻断：产品策略/目标策略禁止该操作，属设计内永久阻断。",
        "写入边界、目标授权或操作员策略显式拒绝；不是缺陷，是安全护栏生效。",
        "确认策略意图；如确需放行，需操作员显式调整策略（不改代码）。",
    ),
    "TARGET_SYSTEM_RESPONSE": _guidance(
        "目标系统响应问题：请求已到达目标但结果异常（连接失败/异常/环境错误）。",
        "目标服务不可达、返回异常、或运行环境故障；非产品逻辑错误。",
        "检查目标服务状态/网络/日志；确认目标环境可用后重跑。",
    ),
    "ORACLE_INPUT_GAP": _guidance(
        "判定（oracle）输入缺口：证据不足以激活或完成契约判定。",
        "观察证据缺失/不确定（INDETERMINATE），或控制臂未证明成功，无法形成判定。",
        "补充观察证据来源（效果读取/控制臂），检查观察收据完整性。",
    ),
    "CLEANUP_CAPABILITY_GAP": _guidance(
        "清理/补偿缺口：写入的清理或补偿能力缺失、不确定或失败。",
        "缺少来源声明的补偿/删除能力，或清理证明不完整、与契约漂移。",
        "为写入声明可逆补偿（DELETE/补偿操作/字段恢复），或接受残留（仅非生产）。",
    ),
    "PERMISSION_GAP": _guidance(
        "权限边界缺口：actor 无权限或权限模型无法满足义务要求。",
        "actor 的角色/权限声明与操作要求不匹配，或权限矩阵缺失。",
        "检查测试 actor 的角色授权声明与权限矩阵来源。",
    ),
    "LLM_PROVIDER_GAP": _guidance(
        "大模型服务不可用/失败：假设生成阶段无法调用模型。",
        "模型端点不可达、超时、限流、密钥无效或返回不可解析；模型降级为启发式路径。",
        "检查 LLM 配置（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL）与 provider 健康状态（前端状态灯）。",
    ),
    "HYPOTHESIS_GENERATION_GAP": _guidance(
        "假设生成缺口：reasoner 引擎全部失败或产出为空（理解阶段第一损失点）。",
        "模型不可用、prompt 模板与上下文不匹配、或引擎输出无法解析。",
        "查看引擎报告的 per-engine 错误码（http_*/parse_error 等），修复模型配置或模板契约。",
    ),
    "CONTRACT_DERIVATION_GAP": _guidance(
        "契约自动推导跳过：来源文本中的契约语句无法精确绑定操作/actor。",
        "语句缺少显式路径、操作不匹配、actor 歧义或事件字段不完整；推导从不猜测。",
        "补充来源声明（显式契约 JSON/表格），或补全语句中的路径与字段信息。",
    ),
    "EMBEDDING_CAPABILITY_GAP": _guidance(
        "嵌入能力不可用：非决策 embedding（去重/检索）未配置或失败。",
        "LLM_EMBEDDING_MODEL 未配置或 provider 不支持 /embeddings。",
        "配置 LLM_EMBEDDING_MODEL；该能力为可选增强，不影响确定性路径。",
    ),
    "LEARNING_FEEDBACK_GAP": _guidance(
        "学习反馈缺口：闭环反馈（确认缺陷归因/记忆）读取或写入失败。",
        "项目知识库不可用、项目身份缺失或反馈存储损坏。",
        "检查项目闭环保存储（platform_outputs/<project>/closed_loop）与 SQLite 知识库状态。",
    ),
    "NORMAL_OUTCOME": _guidance(
        "正常结局：判定未发现违反，非阻塞。",
        "契约/断言验证通过，属期望结果，不是损失。",
        "无需处理。",
    ),
    "DISCOVERY_DIAGNOSTIC": _guidance(
        "诊断性观察：仅记录观测，不进入义务执行。",
        "表面发现仅作诊断，非阻塞。",
        "无需处理。",
    ),
    "EXECUTION_BUDGET": _guidance(
        "执行预算：预算（slice/义务配额）耗尽导致未执行。",
        "预算上限是显式 operator 配置，不是缺陷。",
        "如需更广覆盖，调整预算配置（显式、收据化）。",
    ),
    "PLANNING_DEFERRED": _guidance(
        "规划推迟：义务未进入本轮计划（DEFERRED/OBLIGATION_NOT_IN_PLAN）。",
        "计划器按预算/优先级推迟，非阻塞。",
        "无需处理；如需覆盖，调整计划预算或优先级。",
    ),
    "UNREGISTERED": _guidance(
        "未登记原因码：该码不在统一目录中。",
        "产生该码的模块尚未登记（注册表完整性缺口）。",
        "将该码及其含义登记到 blocker_attribution（register_reason_code），保持目录完整。",
    ),
    "UNKNOWN": _guidance(
        "无法归因：原因码缺失或无法映射。",
        "缺少终端原因码或映射缺失；需要更多诊断信息。",
        "补充执行/编译收据，确保原因码随收据落库。",
    ),
}

# Per-code guidance overrides: where family-level text is not specific enough.
_CODE_GUIDANCE: dict[str, dict[str, str]] = {
    "BLOCKED_CONTROL_ARM_NOT_PROVEN": _guidance(
        "控制臂已派发但成功未被证明，oracle 无法激活。",
        "不是观察端点缺失，而是控制臂证据未闭合（此前错误归因到观察缺口）。",
        "检查控制臂执行收据与成功证明（控制对比实验的前置）。",
    ),
    "BLOCKED_INTERFACE_NOT_IMPLEMENTED": _guidance(
        "源材料声明的接口在目标运行时返回框架级 404（路由未注册）。",
        "接口文档声明了该路由，但部署的目标服务未实现——文档/实现漂移缺陷。",
        "确认目标版本与接口文档一致，或补充缺失的路由实现后重跑。",
    ),
    "BLOCKED_OBSERVER_RECEIPT_INDETERMINATE": _guidance(
        "观察者已运行但收据不确定。",
        "与缺失观察者不同：观察发生但证据不明确。",
        "检查观察收据的字段覆盖与状态判定，补充效果读取证据。",
    ),
    "BLOCKED_UNSUPPORTED_ADAPTER": _guidance(
        "实验需要未声明的观察适配器（如 event_observer_http/ui_browser/db_sql）。",
        "目标表面未在部署声明中登记；产品绝不从文本/URL 推断适配器。",
        "在扫描配置的 declared_adapters 中显式声明目标暴露的表面后重跑。",
    ),
    "BLOCKED_CONFLICTING_SOURCE": _guidance(
        "来源材料互相冲突（永久阻塞）。",
        "不同来源文档对同一契约给出矛盾声明。",
        "消除来源冲突（修订文档）后重跑；该码设计上永久阻塞直到来源一致。",
    ),
    "BLOCKED_NON_REVERSIBLE_WRITE": _guidance(
        "写入不可逆：无来源声明的补偿/清理能力。",
        "来源未声明该写入的 DELETE/补偿/字段恢复路径。",
        "为写入声明可逆补偿；生产目标写边界不可放宽。",
    ),
    "BLOCKED_MISSING_ACTOR": _guidance(
        "缺少 actor：义务需要的测试 actor 未声明或未绑定。",
        "测试账号清单（test_accounts.json/凭据库）缺少该角色。",
        "在测试账号清单中声明角色与登录凭据（角色绑定保持精确）。",
    ),
    "BLOCKED_MISSING_BINDING": _guidance(
        "绑定缺失：实体/字段/关系/状态未绑定到精确来源身份。",
        "来源材料缺少唯一键/归属/状态枚举等精确身份声明。",
        "补充来源声明消除绑定缺口（子码 FIELD_NOT_BOUND/ENTITY_NOT_BOUND 等定位具体维度）。",
    ),
    "non_production_environment_required": _guidance(
        "非生产环境要求（永久阻断）：写入仅允许在显式声明的非生产目标。",
        "目标环境类型缺失或为生产；未知环境默认拒绝写入。",
        "显式声明非生产环境类型并确认运行契约已批准。",
    ),
    "HARNESS_FAILURE": _guidance(
        "执行框架失败：请求已到达目标但执行异常。",
        "目标响应异常或运行环境故障，非产品逻辑错误。",
        "检查目标服务与运行日志，确认环境可用后重跑。",
    ),
}


def register_reason_code(
    reason_code: str,
    *,
    attribution: str,
    recoverability: str = "UNKNOWN",
    is_blocking: bool = True,
    must_remain_blocked: bool = False,
    meaning: str = "",
    likely_root_cause: str = "",
    suggested_action: str = "",
    source_module: str = "",
) -> None:
    """Register (or update) one reason code in the unified catalog.

    Open registration for emitters outside the compile/ledger path (reasoner
    failures, contract derivation, LLM transport).  Guidance is optional at
    registration time; ``build_reason_code_catalog`` marks entries without
    guidance as ``guidance_pending`` so the directory stays visibly complete
    instead of silently carrying codes nobody can interpret.
    """
    code = _text(reason_code)
    if not code:
        raise ValueError("register_reason_code requires a non-empty reason code")
    family = _text(attribution) or "UNKNOWN"
    REASON_CODE_REGISTRY[code] = _reason_definition(
        family,
        recoverability=recoverability,
        is_blocking=is_blocking,
        must_remain_blocked=must_remain_blocked,
    )
    if meaning or likely_root_cause or suggested_action:
        _CODE_GUIDANCE[code] = _guidance(meaning, likely_root_cause, suggested_action)
        _CODE_GUIDANCE[code]["source_module"] = source_module


# ─── Reasoner / derivation / LLM transport codes (hypothesis-stage positioning) ──

_REASONER_FAILURE_CODES: dict[str, dict[str, Any]] = {
    "http_401": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型服务返回 401（未认证）。",
        "likely_root_cause": "LLM_API_KEY 无效或已轮换。",
        "suggested_action": "在前端「模型配置」页面更新 API Key 后重试（QB-L006 同类）。",
    },
    "http_403": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型服务返回 403（无权限）。",
        "likely_root_cause": "密钥缺少该模型/端点的调用权限。",
        "suggested_action": "检查密钥权限范围或联系模型服务商。",
    },
    "http_429": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型服务限流（429）。",
        "likely_root_cause": "并发超过 provider 速率限制（默认 4 并发 worker）。",
        "suggested_action": "降低 reasoner max_workers 或增大调用间隔；系统会自动重试一次。",
    },
    "timeout": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型调用超时。",
        "likely_root_cause": "网络延迟或模型响应慢；timeout 下限 300s（产品护栏）。",
        "suggested_action": "检查网络连通性；确认 timeout_seconds >= 300 未被下调。",
    },
    "provider_unconfigured": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "SOURCE_DEPENDENT",
        "meaning": "模型未配置（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 缺一）。",
        "likely_root_cause": "部署未配置模型凭据。",
        "suggested_action": "配置模型端点/密钥/模型名；未配置时引擎降级为本地启发式。",
    },
    "parse_error": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型输出 JSON 无法解析。",
        "likely_root_cause": "模型返回非 JSON/截断/围栏格式；max_tokens 不足（下限 32768）。",
        "suggested_action": "确认 max_tokens >= 32768；查看引擎报告的 raw_chars/content_chars 判断截断。",
    },
    "empty_raw_response": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型返回空响应。",
        "likely_root_cause": "provider 故障或响应被代理吞掉。",
        "suggested_action": "检查 provider 健康状态与网络代理。",
    },
    "outer_json_corrupted": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型外层 JSON 损坏（截断/围栏包裹）。",
        "likely_root_cause": "输出被截断（max_tokens 不足，下限 32768）或包裹 markdown 围栏。",
        "suggested_action": "确认 max_tokens >= 32768；查看 raw_chars 判断截断。",
    },
    "outer_response_not_object": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型外层响应根节点不是对象。",
        "likely_root_cause": "模型输出游离文本或数组。",
        "suggested_action": "检查 response_format=json_object 是否生效。",
    },
    "invalid_choices_shape": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型响应 choices 结构不符合 OpenAI 兼容契约。",
        "likely_root_cause": "provider 返回形状异常。",
        "suggested_action": "检查 provider 兼容性（thinking/response_format 需显式支持）。",
    },
    "invalid_choice_item": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "choices 条目缺少 message 或 message 非对象。",
        "likely_root_cause": "provider 返回形状异常。",
        "suggested_action": "检查 provider 兼容性。",
    },
    "invalid_message_shape": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "message 缺少 content 或 content 非字符串。",
        "likely_root_cause": "provider 返回形状异常。",
        "suggested_action": "检查 provider 兼容性。",
    },
    "empty_hypotheses": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型 JSON 中 hypotheses 数组为空。",
        "likely_root_cause": "模型认为证据不足（合法降级）或输出契约未遵守。",
        "suggested_action": "结合其他引擎输出判断是模型判断还是契约问题。",
    },
    "empty_choices": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型响应缺少 choices 数组。",
        "likely_root_cause": "provider 返回形状异常（与 OpenAI 兼容契约不符）。",
        "suggested_action": "检查 provider 兼容性（thinking/response_format 选项需显式支持）。",
    },
    "empty_content": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型响应 content 为空。",
        "likely_root_cause": "模型未产出内容或 reasoning-only 输出。",
        "suggested_action": "检查 thinking_mode 配置与模型输出策略。",
    },
    "missing_hypotheses_array": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型 JSON 缺少 hypotheses 数组（输出契约违反）。",
        "likely_root_cause": "模型未遵守输出硬限制模板。",
        "suggested_action": "检查 prompt 输出契约与模型指令遵循能力。",
    },
    "not_list_or_dict": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "模型输出根节点不是对象/列表。",
        "likely_root_cause": "模型输出游离文本。",
        "suggested_action": "检查 response_format=json_object 是否生效。",
    },
    "unknown_failure": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "UNKNOWN",
        "meaning": "引擎失败原因无法归类。",
        "likely_root_cause": "未预期的异常类型。",
        "suggested_action": "查看引擎报告 last_exception_type 定位具体异常。",
    },
}

_LLM_TRANSPORT_CODES: dict[str, dict[str, Any]] = {
    "QB-L001": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "AI 模型响应超时。",
        "likely_root_cause": "网络连通性或模型响应慢；timeout 下限 300s。",
        "suggested_action": "检查网络连接；确认 timeout_seconds >= 300（护栏）。",
    },
    "QB-L002": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "AI 模型调用频率超限。",
        "likely_root_cause": "并发超过 provider 速率限制。",
        "suggested_action": "降低并发数（max_workers）或增加调用间隔。",
    },
    "QB-L003": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "AI 模型返回了无法解析的响应。",
        "likely_root_cause": "模型版本不兼容或输出截断。",
        "suggested_action": "检查模型版本兼容性与原始响应内容。",
    },
    "QB-L004": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "无法连接 AI 模型服务。",
        "likely_root_cause": "模型 API 地址/端口错误或网络不可达。",
        "suggested_action": "确认 API 地址配置正确，检查防火墙规则。",
    },
    "QB-L005": {
        "attribution": "HYPOTHESIS_GENERATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "AI 模型输出被截断（超过 max_tokens）。",
        "likely_root_cause": "max_tokens 不足（下限 32768）。",
        "suggested_action": "确认 max_tokens >= 32768（护栏）。",
    },
    "QB-L006": {
        "attribution": "LLM_PROVIDER_GAP", "recoverability": "RECOVERABLE",
        "meaning": "AI 模型 API 密钥无效。",
        "likely_root_cause": "API Key 错误或已轮换。",
        "suggested_action": "在前端「模型配置」页面检查/更新 API Key。",
    },
}

_CONTRACT_DERIVATION_CODES: dict[str, dict[str, Any]] = {
    "already_declared_contract": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "契约自动推导跳过：同一操作已有显式声明契约，推导不覆盖。",
        "likely_root_cause": "声明契约与自动推导覆盖同一操作（声明优先，正确行为）。",
        "suggested_action": "无需处理；如声明契约过期，更新声明。",
    },
    "conflicting_derived_claims": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "同一操作存在冲突的推导值（如操作描述 200ms vs PRD 300ms），冲突值被跳过并可见。",
        "likely_root_cause": "来源文档对同一契约给出不同数值。",
        "suggested_action": "修订来源文档消除矛盾；产品保留更精确的操作作用域值。",
    },
    "operation_not_found_or_ambiguous": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "SOURCE_DEPENDENT",
        "meaning": "推导语句中的路径未匹配到唯一操作。",
        "likely_root_cause": "语句路径与来源 API 规范不一致，或匹配到多个操作。",
        "suggested_action": "核对语句中的路径/方法与 API 规范一致。",
    },
    "actor_unresolved": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "SOURCE_DEPENDENT",
        "meaning": "推导无法解析唯一可执行 actor。",
        "likely_root_cause": "测试账号清单缺少该角色或角色歧义。",
        "suggested_action": "在测试账号清单中声明角色（保持精确绑定）。",
    },
    "non_get_head_operation": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "SOURCE_DEPENDENT",
        "meaning": "推导跳过：延迟/稳定性契约只接受 GET/HEAD 操作。",
        "likely_root_cause": "语句挂在写操作上（产品首增量只测读操作）。",
        "suggested_action": "将契约语句声明在对应的 GET/HEAD 操作上。",
    },
    "event_fields_incomplete": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "SOURCE_DEPENDENT",
        "meaning": "事件语句缺少事件契约必填字段（路径/事件类型/字段/关联参数），推导跳过。",
        "likely_root_cause": "来源语句未完整声明事件轮询所需字段；推导从不产生半成品契约。",
        "suggested_action": "补全语句中的路径、事件类型、字段与关联参数，或使用显式事件契约 JSON。",
    },
    "quote_not_anchored": {
        "attribution": "CONTRACT_DERIVATION_GAP", "recoverability": "RECOVERABLE",
        "meaning": "推导引文无法逐字锚定到来源文本。",
        "likely_root_cause": "内部提取与文本切片不一致（产品侧缺陷）。",
        "suggested_action": "报告该问题；推导失败安全跳过并记录。",
    },
    "embeddings_unavailable": {
        "attribution": "EMBEDDING_CAPABILITY_GAP", "recoverability": "RECOVERABLE",
        "meaning": "语义去重跳过：嵌入能力不可用（未配置或 provider 失败）。",
        "likely_root_cause": "LLM_EMBEDDING_MODEL 未配置或 /embeddings 调用失败。",
        "suggested_action": "配置 LLM_EMBEDDING_MODEL；可选增强，精确去重不受影响。",
    },
}

for _code, _spec in {
    **_REASONER_FAILURE_CODES,
    **_LLM_TRANSPORT_CODES,
    **_CONTRACT_DERIVATION_CODES,
}.items():
    register_reason_code(_code, **_spec)


def build_reason_code_catalog() -> dict[str, Any]:
    """Emit the complete, documented reason-code directory (v1).

    One machine-readable catalog for the chain-positioning report, CLI and
    frontend: every registered code carries attribution / recoverability /
    blocking semantics plus diagnostic guidance (explicitly marked synthetic).
    Codes without guidance are listed with ``guidance_pending`` so the
    directory stays visibly complete instead of silently carrying codes
    nobody can interpret.
    """
    catalog: dict[str, Any] = {}
    for code in sorted(REASON_CODE_REGISTRY):
        profile = profile_reason_code(code)
        guidance_present = bool(profile.get("meaning"))
        catalog[code] = {
            "reason_code": code,
            "reason_family": profile.get("reason_family"),
            "recoverability": profile.get("recoverability"),
            "is_blocking": profile.get("is_blocking", True),
            "must_remain_blocked": profile.get("must_remain_blocked", False),
            "meaning": profile.get("meaning", ""),
            "likely_root_cause": profile.get("likely_root_cause", ""),
            "suggested_action": profile.get("suggested_action", ""),
            "guidance_kind": profile.get("guidance_kind", GUIDANCE_KIND),
            "guidance_pending": not guidance_present,
            "source_module": profile.get("source_module", ""),
        }
    return {
        "schema_version": REASON_CODE_REGISTRY_SCHEMA,
        "guidance_kind": GUIDANCE_KIND,
        "code_count": len(catalog),
        "codes": catalog,
    }


def profile_reason_code(reason_code: str) -> dict[str, Any]:
    """Return the explicit registry row for a terminal reason code.

    Unknown codes are not guessed from their detail.  They are returned as an
    unregistered reason so the funnel can fail safe and operators can extend
    this single registry with the real emitter's contract.
    """

    normalized = _text(reason_code)
    definition = REASON_CODE_REGISTRY.get(normalized)
    if definition is None:
        return {
            "registry_status": "UNREGISTERED",
            "reason_code": normalized,
            **_reason_definition("UNREGISTERED", is_blocking=True),
            **(
                _CODE_GUIDANCE.get(normalized)
                or _FAMILY_GUIDANCE.get("UNREGISTERED")
                or {}
            ),
        }
    guidance = dict(_CODE_GUIDANCE.get(normalized) or {})
    if not guidance:
        family = str(definition.get("reason_family") or "")
        guidance = dict(_FAMILY_GUIDANCE.get(family) or {})
    return {
        "registry_status": "REGISTERED",
        "reason_code": normalized,
        **dict(definition),
        **guidance,
    }


# ─── SPEC v1.2.1 §11.2: Phase B Evidence Refinement ─────────────────────────


def _phase_b_evidence_refinement(
    *,
    attribution: str,
    reason_code: str,
    obligation: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Phase B: Verify Phase A attribution against real evidence in IR/experiment.

    Returns refinement result with confirmed/adjusted attribution and evidence.
    """
    ir = _dict(behavior_ir)
    exp = _dict(experiment)
    obl = _dict(obligation)
    ops = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    actors = _list(ir.get("actors"))

    evidence: list[dict[str, Any]] = []
    secondary_contributors: list[str] = []
    adjusted_attribution = attribution
    adjusted_reason = reason_code
    confidence_boost = 0.0

    # Check 1: Does IR contain candidate operations for the obligation?
    entity_ref = _text(obl.get("entity_ref") or obl.get("target_entity"))
    has_candidate_op = any(
        _text(op.get("entity_ref") or op.get("entity")) == entity_ref
        for op in ops if isinstance(op, dict)
    ) if entity_ref else bool(ops)
    evidence.append({
        "check": "ir_candidate_operation_exists",
        "passed": has_candidate_op,
        "detail": f"entity_ref={entity_ref}, ops_count={len(ops)}",
    })

    # Check 2: Source refs availability
    source_refs = _list(obl.get("source_refs"))
    has_source_refs = len(source_refs) > 0
    evidence.append({
        "check": "source_refs_available",
        "passed": has_source_refs,
        "detail": f"source_refs_count={len(source_refs)}",
    })

    # Check 3: Binding satisfiability
    binding_graph = _dict(exp.get("binding_coverage_graph"))
    binding_status = _text(binding_graph.get("graph_status"))
    bindings_satisfiable = binding_status != "BLOCKED"
    evidence.append({
        "check": "binding_satisfiability",
        "passed": bindings_satisfiable,
        "detail": f"binding_graph_status={binding_status or 'not_available'}",
    })
    if not bindings_satisfiable and attribution != "BINDING_GRAPH_GAP":
        secondary_contributors.append("BINDING_GRAPH_GAP")

    # Check 4: Adapter availability
    adapter_ref = _text(exp.get("adapter_ref") or exp.get("transport_adapter"))
    has_adapter = bool(adapter_ref)
    evidence.append({
        "check": "adapter_available",
        "passed": has_adapter,
        "detail": f"adapter_ref={adapter_ref or 'none'}",
    })
    if not has_adapter and attribution != "ADAPTER_CAPABILITY_GAP":
        secondary_contributors.append("ADAPTER_CAPABILITY_GAP")

    # Check 5: Actor configuration
    actor_contract = _dict(exp.get("actor_selection_contract"))
    has_actors = bool(actor_contract.get("treatment_actor_ref") or actor_contract.get("control_actor_ref"))
    ir_has_actors = len(actors) > 0
    evidence.append({
        "check": "actor_configured",
        "passed": has_actors or ir_has_actors,
        "detail": f"exp_actors={has_actors}, ir_actors={len(actors)}",
    })
    if not (has_actors or ir_has_actors) and attribution != "SOURCE_GAP":
        secondary_contributors.append("SOURCE_GAP")

    # Check 6: Conflicting source materials
    has_conflict = any(
        _text(r.get("relation_type")) == "conflicts_with"
        for r in relations if isinstance(r, dict)
    )
    evidence.append({
        "check": "no_conflicting_sources",
        "passed": not has_conflict,
        "detail": f"conflict_relations={has_conflict}",
    })
    if has_conflict and attribution != "SOURCE_GAP":
        secondary_contributors.append("SOURCE_GAP")

    # Check 7: Environment policy
    env_policy = _text(exp.get("environment_policy") or obl.get("environment_policy"))
    requires_non_prod = "non_production" in env_policy.lower() or "sandbox" in env_policy.lower()
    evidence.append({
        "check": "environment_policy_compatible",
        "passed": not requires_non_prod,
        "detail": f"environment_policy={env_policy or 'default'}",
    })
    if requires_non_prod and attribution != "ENVIRONMENT_GAP":
        secondary_contributors.append("ENVIRONMENT_GAP")

    # Check 8: Permanent irreversible write
    cleanup_plan = _list(exp.get("cleanup_plan"))
    has_cleanup = len(cleanup_plan) > 0
    compensation = _dict(exp.get("compensation_relation_plan"))
    has_compensation = _text(compensation.get("status")) in ("RESOLVED", "COMPLETE")
    irreversible = not has_cleanup and not has_compensation
    evidence.append({
        "check": "reversible_write",
        "passed": not irreversible,
        "detail": f"cleanup_steps={len(cleanup_plan)}, compensation={has_compensation}",
    })
    if irreversible and attribution != "CLEANUP_CAPABILITY_GAP":
        secondary_contributors.append("CLEANUP_CAPABILITY_GAP")

    # ── Attribution adjustment based on evidence ──
    passed_count = sum(1 for e in evidence if e["passed"])
    total_checks = len(evidence)

    # If original attribution evidence check fails, adjust
    if attribution == "OBSERVER_CAPABILITY_GAP" and has_candidate_op:
        # IR has operations but observer still blocked → refine to binding
        if not bindings_satisfiable:
            adjusted_attribution = "BINDING_GRAPH_GAP"
            adjusted_reason = "BLOCKED_MISSING_BINDING"
            confidence_boost = 0.05
    elif attribution == "BEHAVIOR_MODEL_GAP" and has_candidate_op:
        # IR has candidate ops → not a behavior model gap
        if not has_source_refs:
            adjusted_attribution = "SOURCE_GAP"
            adjusted_reason = "BLOCKED_MISSING_ACTOR"
            confidence_boost = 0.05

    # Confidence based on evidence ratio
    evidence_ratio = passed_count / total_checks if total_checks > 0 else 0.5
    refined_confidence = min(0.95, 0.6 + evidence_ratio * 0.3 + confidence_boost)

    return {
        "primary_attribution": adjusted_attribution,
        "primary_reason": adjusted_reason,
        "secondary_contributors": list(dict.fromkeys(secondary_contributors)),  # dedupe preserve order
        "evidence_checks": evidence,
        "evidence_passed": passed_count,
        "evidence_total": total_checks,
        "refined_confidence": refined_confidence,
        "adjusted": adjusted_attribution != attribution,
    }


# ─── Main Attribution Function ────────────────────────────────────────────────


def attribute_blocker(
    *,
    obligation: dict[str, Any],
    experiment: dict[str, Any] | None,
    execution_result: dict[str, Any] | None,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attribute the primary blocker for a single obligation.

    SPEC v1.2.1 §11: Two-phase attribution.
    Phase A: Reason Candidate from reason_code mapping.
    Phase B: Evidence Refinement against IR/experiment evidence.

    Args:
        obligation: The test obligation.
        experiment: The compiled experiment (may be BLOCKED).
        execution_result: The execution result (may be None if not executed).
        behavior_ir: The Behavior IR graph.

    Returns:
        qualibug.blocker-attribution.v1 receipt.
    """
    obl = _dict(obligation)
    exp = _dict(experiment)
    result = _dict(execution_result)
    ir = _dict(behavior_ir)

    oid = _text(obl.get("obligation_id"))
    eid = _text(exp.get("experiment_id"))

    # Determine terminal stage and reason
    terminal_stage = ""
    reason_code = ""
    reason_detail = ""

    compile_receipt = _dict(exp.get("compile_receipt"))
    compile_status = _text(compile_receipt.get("status")).upper()

    if compile_status == "BLOCKED":
        terminal_stage = "COMPILER_ENTERED"
        reason_code = _text(compile_receipt.get("reason_code"))
        reason_detail = _text(compile_receipt.get("detail"))
    elif compile_status == "DEFERRED":
        terminal_stage = "COMPILER_ENTERED"
        reason_code = _text(compile_receipt.get("reason_code")) or "DEFERRED"
        reason_detail = _text(compile_receipt.get("detail"))
    elif result:
        exec_status = _text(result.get("status")).upper()
        if exec_status == "BLOCKED":
            terminal_stage = "RUNTIME_PROOF_VALID"
            reason_code = _text(result.get("reason_code"))
            reason_detail = _text(result.get("detail"))
        elif exec_status == "HARNESS_FAILURE":
            terminal_stage = "TARGET_TRANSPORT_REACHED"
            reason_code = "HARNESS_FAILURE"
            reason_detail = _text(result.get("reason_code"))

    # If no blocker found, obligation is not blocked
    if not reason_code:
        return {
            "schema_version": "qualibug.blocker-attribution.v1",
            "obligation_id": oid,
            "experiment_id": eid,
            "terminal_stage": "",
            "reason_code": "",
            "reason_detail": "",
            "attribution": "",
            "primary_attribution": "",
            "primary_reason": "",
            "secondary_contributors": [],
            "recoverability": "",
            "confidence": 1.0,
            "missing_capabilities": [],
            "available_evidence": [],
            "evidence_refinement": None,
            "source_refs": list(obl.get("source_refs") or [])[:5],
            "recommended_fix_class": "",
            "must_remain_blocked": False,
            "fingerprint": "",
        }

    # ── Phase A: Reason Candidate ──
    attribution = "UNKNOWN"
    recoverability = "UNKNOWN"
    must_remain_blocked = False
    confidence = 0.5

    registry_profile = profile_reason_code(reason_code)
    if registry_profile["registry_status"] == "REGISTERED":
        attribution = str(registry_profile["reason_family"])
        recoverability = str(registry_profile["recoverability"])
        must_remain_blocked = bool(registry_profile["must_remain_blocked"])
        confidence = 0.9
    else:
        # An unregistered code is a visible contract defect.  Never infer its
        # family from free-form detail because that can turn an unknown failure
        # into a misleading customer capability claim.
        attribution = "UNKNOWN"
        recoverability = "UNKNOWN"
        confidence = 0.0

    # ── Phase B: Evidence Refinement (SPEC v1.2.1 §11.2) ──
    refinement = _phase_b_evidence_refinement(
        attribution=attribution,
        reason_code=reason_code,
        obligation=obl,
        experiment=exp,
        behavior_ir=ir,
    )
    primary_attribution = refinement["primary_attribution"]
    primary_reason = refinement["primary_reason"]
    secondary_contributors = refinement["secondary_contributors"]
    confidence = refinement["refined_confidence"]

    # Determine missing capabilities
    missing_capabilities: list[str] = []
    if primary_attribution == "OBSERVER_CAPABILITY_GAP":
        missing_capabilities.append("read_operation_for_observer")
    elif primary_attribution == "BINDING_GRAPH_GAP":
        missing_capabilities.append("binding_source_resolution")
    elif primary_attribution == "FIXTURE_CAPABILITY_GAP":
        missing_capabilities.append("fixture_materialization")
    elif primary_attribution == "CLEANUP_CAPABILITY_GAP":
        missing_capabilities.append("compensation_relation")
    elif primary_attribution == "ORACLE_INPUT_GAP":
        missing_capabilities.append("oracle_input_coverage")
    elif primary_attribution == "ADAPTER_CAPABILITY_GAP":
        missing_capabilities.append("adapter_registration")
    elif primary_attribution == "SOURCE_GAP":
        missing_capabilities.append("source_material_acquisition")

    # Recommended fix class
    fix_class_map = {
        "OBSERVER_CAPABILITY_GAP": "observer_resolution_enhancement",
        "BINDING_GRAPH_GAP": "binding_propagation_fix",
        "FIXTURE_CAPABILITY_GAP": "fixture_dag_enhancement",
        "COMPILER_GAP": "compiler_logic_fix",
        "CLEANUP_CAPABILITY_GAP": "compensation_relation_recovery",
        "ORACLE_INPUT_GAP": "oracle_input_contract_fix",
        "ADAPTER_CAPABILITY_GAP": "adapter_registration",
        "SOURCE_GAP": "source_material_acquisition",
        "BEHAVIOR_MODEL_GAP": "behavior_ir_enhancement",
        "ENVIRONMENT_GAP": "environment_configuration",
        "POLICY_SAFETY_BLOCK": "none_permanent",
        "TARGET_SYSTEM_RESPONSE": "target_investigation",
    }
    recommended_fix_class = fix_class_map.get(primary_attribution, "unknown")

    # Fingerprint
    fp_content = {
        "obligation_id": oid,
        "reason_code": primary_reason,
        "reason_registry_status": registry_profile["registry_status"],
        "attribution": primary_attribution,
        "recoverability": recoverability,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.blocker-attribution.v1",
        "obligation_id": oid,
        "experiment_id": eid,
        "terminal_stage": terminal_stage,
        "reason_code": primary_reason,
        "reason_detail": reason_detail,
        "attribution": primary_attribution,
        "primary_attribution": primary_attribution,
        "primary_reason": primary_reason,
        "secondary_contributors": secondary_contributors,
        "recoverability": recoverability,
        "confidence": confidence,
        "missing_capabilities": missing_capabilities,
        "available_evidence": refinement["evidence_checks"],
        "evidence_refinement": {
            "evidence_passed": refinement["evidence_passed"],
            "evidence_total": refinement["evidence_total"],
            "adjusted": refinement["adjusted"],
        },
        "source_refs": list(obl.get("source_refs") or [])[:5],
        "recommended_fix_class": recommended_fix_class,
        "must_remain_blocked": must_remain_blocked,
        "fingerprint": fingerprint,
    }


# ─── Batch Attribution ────────────────────────────────────────────────────────


def attribute_all_blockers(
    *,
    obligations: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attribute blockers for all obligations in a campaign.

    Returns summary with per-attribution counts and recoverability breakdown.
    """
    exp_by_oid: dict[str, dict[str, Any]] = {}
    for exp in _list(experiments):
        if isinstance(exp, dict):
            oid = _text(exp.get("obligation_id"))
            if oid:
                exp_by_oid[oid] = exp

    result_by_oid: dict[str, dict[str, Any]] = {}
    for res in _list(execution_results):
        if isinstance(res, dict):
            oid = _text(res.get("obligation_id"))
            if oid:
                result_by_oid[oid] = res

    attributions: list[dict[str, Any]] = []
    attribution_counts: dict[str, int] = {}
    recoverability_counts: dict[str, int] = {}
    recoverable_count = 0
    permanent_count = 0

    seen_oids: set[str] = set()
    for obl in _list(obligations):
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        if not oid or oid in seen_oids:
            continue
        seen_oids.add(oid)

        attr = attribute_blocker(
            obligation=obl,
            experiment=exp_by_oid.get(oid),
            execution_result=result_by_oid.get(oid),
            behavior_ir=behavior_ir,
        )
        # Only include blocked obligations
        if _text(attr.get("reason_code")):
            attributions.append(attr)
            cat = _text(attr.get("attribution"))
            attribution_counts[cat] = attribution_counts.get(cat, 0) + 1
            rec = _text(attr.get("recoverability"))
            recoverability_counts[rec] = recoverability_counts.get(rec, 0) + 1
            if rec == "RECOVERABLE":
                recoverable_count += 1
            elif rec in ("PERMANENTLY_BLOCKED",) or attr.get("must_remain_blocked"):
                permanent_count += 1

    return {
        "schema_version": "qualibug.blocker-attribution-batch.v1",
        "total_blocked": len(attributions),
        "recoverable_count": recoverable_count,
        "permanent_count": permanent_count,
        "attribution_counts": attribution_counts,
        "recoverability_counts": recoverability_counts,
        "attributions": attributions,
    }
