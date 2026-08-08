"""Family-specific executable experiment protocol compiler.

This module owns step semantics. The generic experiment compiler owns contract
assembly and blockers; it must never replace a missing family protocol with a
single status-code probe.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .oracle_expression_resolver import resolve_expression_from_invariant

# Read-side owned-scope protocol (additive): registers the owned_read_scope
# assertion kind once per process. Idempotent; the compile chain always passes
# through this module, so the evaluator exists before any experiment executes.
from .validation_read_side_protocol import (
    install_owned_read_scope_protocol,
    is_ownership_key as _is_ownership_key_read_side,
)

install_owned_read_scope_protocol()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _minimal_body_from_schema(operation: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal default request body from the operation's schema properties."""
    schema = _dict(operation.get("request_schema") or operation.get("requestBody") or {})
    props = _dict(schema.get("properties"))
    if not props:
        props = _dict(_dict(schema.get("content", {})).get("application/json", {}).get("schema", {}).get("properties"))
    if not props:
        return {}
    body: dict[str, Any] = {}
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        prop_type = _text(prop.get("type") or "string")
        example = prop.get("example")
        if example is not None:
            body[name] = example
        elif prop_type == "string":
            body[name] = "test_value"
        elif prop_type in ("integer", "number"):
            body[name] = 1
        elif prop_type == "boolean":
            body[name] = True
        elif prop_type == "array":
            body[name] = []
        else:
            body[name] = {}
    return body


def source_request_example(
    operation: dict[str, Any],
    *,
    sibling_operations: list[Any] | None = None,
) -> dict[str, Any]:
    """Return an explicitly documented request example, never synthesized data."""

    from .experiment_compiler_support import _source_request_example

    example = _source_request_example(
        operation,
        sibling_operations=sibling_operations,
    )
    return deepcopy(example) if example else {}


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    request_schema = _dict(_dict(operation).get("request_schema"))
    # Prefer the schema that actually declares fields. A top-level
    # ``{"type": "object", "properties": {}}`` shell with the real field
    # declarations under ``content.<media>.schema`` must resolve to the
    # content schema, or every field-based protocol sees an empty field set
    # and blocks on a source schema that does declare fields.
    if _dict(request_schema.get("properties")):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema and _dict(schema.get("properties")):
            return schema
    if _text(request_schema.get("type")):
        return request_schema
    for media in _dict(request_schema.get("content")).values():
        schema = _dict(_dict(media).get("schema"))
        if schema:
            return schema
    return {}


def _generate_minimal_body_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Generate a minimal valid request body from a JSON Schema definition.

    Used as a fallback when no documented request example exists. The generated
    body uses type-appropriate default values for required fields.
    """
    if not isinstance(schema, dict):
        return {}
    properties = _dict(schema.get("properties"))
    if not properties:
        return {}
    required = [_text(v) for v in (schema.get("required") or []) if _text(v)]
    body: dict[str, Any] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        field_type = _text(field_schema.get("type")).lower()
        # Only populate required fields or fields needed for meaningful requests
        if field_name not in required and field_name not in properties:
            continue
        if field_type == "string":
            example = field_schema.get("example") or field_schema.get("default")
            body[field_name] = str(example) if example else "test_value"
        elif field_type == "integer":
            body[field_name] = int(field_schema.get("example") or field_schema.get("default") or 1)
        elif field_type == "number":
            body[field_name] = float(field_schema.get("example") or field_schema.get("default") or 1.0)
        elif field_type == "boolean":
            body[field_name] = True
        elif field_type == "array":
            body[field_name] = []
        elif field_type == "object":
            body[field_name] = {}
        else:
            body[field_name] = "test_value"
    return body


# ── Semantic invalid value heuristics (industry-neutral, field-name driven) ──
_NUMERIC_NEGATIVE_FIELDS = re.compile(
    r"(price|amount|total|balance|stock|quantity|qty|count|num|limit|quota|"
    r"weight|volume|rate|fee|cost|salary|wage|budget|credit|debit|payment|"
    r"refund|discount|tax|margin|profit|revenue|income|expense|"
    r"价格|金额|余额|库存|数量|限额|配额|费用|单价|总价|退款|优惠)",
    re.IGNORECASE,
)
_PASSWORD_FIELDS = re.compile(
    r"(pass(word|wd|phrase)?|pwd|secret|credential|密码|口令)", re.IGNORECASE
)
_EMAIL_FIELDS = re.compile(r"(e-?mail|邮箱|邮件地址)", re.IGNORECASE)
_PHONE_FIELDS = re.compile(r"(phone|mobile|tel|cell|手机|电话|联系方式)", re.IGNORECASE)
_DATE_FIELDS = re.compile(r"(date|time|_at$|_on$|日期|时间)", re.IGNORECASE)


def _semantic_invalid_value(
    field_name: str,
    declared_type: str,
    property_schema: dict[str, Any],
    semantic_text: str = "",
) -> tuple[Any, str] | None:
    """Generate a semantically invalid value based on field name/type heuristics.

    Returns (invalid_value, constraint_description) or None if no heuristic applies.
    Industry-neutral: uses common field-name patterns, never benchmark-specific values.

    The rule's ``semantic_text`` describes the whole contract (必须校验状态、
    生效时间、分类范围…) and may mention any dimension word, so field-CLASS
    heuristics (password/email/phone/date) key off the field name only — a
    coupon ``code`` must never mutate into an invalid date just because the
    contract says 校验时间. Numeric/amount heuristics keep the statement
    context: a neutral-named amount field (value/adjustment) governed by a
    rule naming 金额 is genuinely the amount the rule governs.
    """
    combined = f"{field_name} {semantic_text}".lower()

    # Numeric fields that should reject negative values
    if declared_type in ("integer", "number"):
        if _NUMERIC_NEGATIVE_FIELDS.search(combined):
            return -1, "semantic:negative_value"
        # Check for maximum constraint in schema
        maximum = property_schema.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            return maximum + 1, "semantic:exceeds_maximum"
        # Generic numeric boundary: zero for quantities
        if re.search(r"(quantity|qty|count|num|stock|数量|库存)", combined, re.IGNORECASE):
            return 0, "semantic:zero_quantity"

    # String fields with semantic constraints
    if declared_type == "string":
        # A restrictive rule naming an enum value as the ONLY allowed value
        # (对外注册只能创建 buyer) forbids every other declared enum value.
        # The treatment is another enum value — the target must reject it or
        # ignore the client input. Only applies when the rule's allowed value
        # IS in the enum; a rule that names no enum value (只能查看自己的订单)
        # never mutates an unrelated enum field.
        enum_values = [
            _text(value)
            for value in _list(property_schema.get("enum"))
            if _text(value)
        ]
        if enum_values:
            _restricted_match = re.search(
                r"(?:只能|仅能|仅|必须|只)(?:创建|为|是|使用|设置|取)?"
                r"\s*([A-Za-z_][A-Za-z0-9_-]*)",
                combined,
            )
            _allowed_value = (
                _text(_restricted_match.group(1)).lower()
                if _restricted_match
                else ""
            )
            if _allowed_value and any(
                _text(value).lower() == _allowed_value
                for value in enum_values
            ):
                for candidate in enum_values:
                    if _text(candidate).lower() != _allowed_value:
                        return candidate, f"semantic:enum_value_not_allowed:{candidate}"
        if _PASSWORD_FIELDS.search(field_name):
            return "1", "semantic:weak_password"
        if _EMAIL_FIELDS.search(field_name):
            return "not-an-email", "semantic:invalid_email_format"
        if _PHONE_FIELDS.search(field_name):
            return "0", "semantic:invalid_phone_format"
        if _DATE_FIELDS.search(field_name):
            return "1900-13-99", "semantic:invalid_date"
        # Check for minLength constraint
        min_length = property_schema.get("minLength")
        if isinstance(min_length, int) and min_length > 1:
            return "x", "semantic:below_min_length"
        # Check for pattern constraint
        if property_schema.get("pattern"):
            return "!!!invalid!!!", "semantic:pattern_violation"

    return None


# Response-side constraint signals: the rule constrains what a response may
# carry (导出结果禁止包含 password) rather than a request body mutation.
# Generic Chinese business syntax — not industry-specific vocabulary.
_RESPONSE_SIDE_SIGNALS = ("导出", "结果", "响应", "返回", "输出")
_RESPONSE_FORBID_FIELD_RE = re.compile(
    r"(?:禁止|不得|不能|不允许|不可|不应)[^，,。；;\n]{0,20}?"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)

# Generic secret-material concepts a response-side rule may forbid by its
# common name (响应不得返回支付密钥、签名密钥或完整敏感配置) instead of an
# ASCII identifier. Each maps to canonical ASCII matchers that the evaluator
# matches as substrings of response field names. This is generic credential
# vocabulary (密钥/密码/凭据/secret/password/credential/…), never an
# industry term, so the protocol stays language- and industry-neutral.
_SECRET_FAMILY_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("敏感配置", ("secret", "credential", "password")),
    ("敏感信息", ("secret", "credential", "password")),
    ("密钥", ("secret", "key")),
    ("密码", ("password", "pwd")),
    ("口令", ("password", "pwd")),
    ("凭据", ("credential",)),
    ("token", ("token",)),
    ("secret", ("secret",)),
    ("password", ("password",)),
    ("credential", ("credential",)),
)
# Generic account/entity field concepts a response-side rule may forbid
# (响应不得泄露完整手机号、用户状态或角色). These are universal account
# record fields — phone/status/role/email — never industry terms; the
# matchers scan response field names the same way the secret family does.
_ACCOUNT_FIELD_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("手机号", ("phone", "mobile")),
    ("手机", ("phone", "mobile")),
    ("电话", ("phone", "mobile")),
    ("mobile", ("mobile",)),
    ("phone", ("phone",)),
    ("状态", ("status",)),
    ("status", ("status",)),
    ("角色", ("role",)),
    ("role", ("role",)),
    ("邮箱", ("email",)),
    ("邮件", ("email",)),
    ("email", ("email",)),
)
# Identifier compounds that legitimately embed a key matcher without being
# credential material (idempotency_key, correlation_key, …). Generic
# enterprise-technical vocabulary; keeps the family matcher honest on
# responses that carry tracking identifiers next to real secrets.
_KEY_MATCHER_IDENTIFIER_COMPOUNDS = (
    "idempotency", "correlation", "primary", "foreign", "reference",
    "unique", "dedupe", "dedup", "event", "transaction_ref", "request_ref",
)


def _extract_forbidden_response_fields(
    property_spec: dict[str, Any],
) -> tuple[list[str], bool]:
    """Extract fields a response-side rule forbids in its output.

    A rule like 导出结果禁止包含 password 或其他认证凭据 names the forbidden
    field after a prohibition word; the identifier is source material, never
    a hardcoded name. A rule like 响应不得返回支付密钥、签名密钥或完整敏感
    配置 names generic secret concepts (密钥/密码/凭据/…) rather than ASCII
    identifiers — those map to the canonical secret-family matchers with
    ``family_match=True`` so the evaluator scans response field names for
    credential vocabulary. Returns ([], False) when the rule is not
    response-side (no export/result/response/return/output signal), so
    write-side validation keeps its existing body-mutation protocol.
    """
    expression = _dict(property_spec.get("expression"))
    raw = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    if not raw or not any(signal in raw for signal in _RESPONSE_SIDE_SIGNALS):
        return [], False
    fields = [
        match.group(1)
        for match in _RESPONSE_FORBID_FIELD_RE.finditer(raw)
    ]
    family: list[str] = []
    for concept, matchers in [*_SECRET_FAMILY_CONCEPTS, *_ACCOUNT_FIELD_CONCEPTS]:
        if concept in raw:
            for matcher in matchers:
                if matcher not in family:
                    family.append(matcher)
    return list(dict.fromkeys([*fields, *family])), bool(family)


# Generic ownership-modal vocabulary for read-side caller-scope declarations
# (只能…自己的/仅限本人/own/self). Ownership modality, not an industry term.
_OWNERSHIP_MODAL_TERMS = ("自己的", "本人", "own", "self")
_OWNERSHIP_RESTRICTIVE_MODALS = ("只能", "仅限", "仅允许", "only", "must")


def _concrete_ownership_body_values(
    *,
    operation: dict[str, Any],
    control_actor_ref: str,
    treatment_actor_ref: str,
    behavior_ir: dict[str, Any] | None,
    ownership_param: str,
    control_body: dict[str, Any],
    treatment_body: dict[str, Any],
) -> dict[str, Any] | None:
    """Concretize body ownership identity params from runtime-observed ids.

    Write-side ownership arms (merge fromUserId/toUserId, product sellerId…)
    test that the ownership binder cannot be pointed at someone else's
    identity. The arm values are the actors' login-observed identities
    (account_id) — the same material a /me read would return, so no list
    read is needed. Control arm: every ownership param is the owner's own
    identity. Treatment arm: the ownership binder carries the OWNER's
    identity (the viewer attempts to touch the owner's resource), while the
    other ownership params stay the viewer's own identity.

    Returns concrete bodies, or None when no ownership param is declared or
    an arm identity is missing (callers keep the placeholder template path).
    """
    schema = _dict(operation.get("request_schema"))
    media = _dict(_dict(schema.get("content")).get("application/json"))
    props = _dict(_dict(media.get("schema")).get("properties"))
    if not isinstance(props, dict) or not props:
        return None
    ownership_params = [
        str(name)
        for name in props
        if _is_ownership_key_read_side(str(name))
    ]
    if not ownership_params:
        return None
    actors = {
        _text(actor.get("id")): actor
        for actor in _list(_dict(behavior_ir).get("actors"))
        if isinstance(actor, dict)
    }
    control_actor = actors.get(control_actor_ref) or {}
    treatment_actor = actors.get(treatment_actor_ref) or {}
    control_id = _text(control_actor.get("account_id"))
    treatment_id = _text(treatment_actor.get("account_id"))
    if not control_id or not treatment_id:
        return None

    def _param_key(name: str) -> str:
        return re.sub(r"[^a-z0-9_]+", "", _text(name).lower())

    binder_key = _param_key(ownership_param)
    control = deepcopy(control_body)
    for name in ownership_params:
        if name in control and isinstance(control[name], str):
            control[name] = control_id
    treatment = deepcopy(treatment_body)
    for name in ownership_params:
        if name not in treatment or not isinstance(treatment[name], str):
            continue
        treatment[name] = (
            control_id if _param_key(name) == binder_key else treatment_id
        )
    return {"control": control, "treatment": treatment}


def _read_side_owned_scope_projection(
    *,
    operation: dict[str, Any],
    operation_ref: str,
    property_spec: dict[str, Any],
    control_actor_ref: str,
    treatment_actor_ref: str,
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compile the two-arm owned-scope read when structured material allows.

    Returns a COMPILED protocol dict, or None when the rule is not a
    caller-scoped ownership read (the caller then falls through to the
    existing validation chain). Every material input is source-declared:

    * the operation's ownership query parameter (normalized structural name
      match, e.g. ``userId``) whose description states the caller-scoped
      constraint (只能…自己的/仅限本人);
    * the bound actor's runtime-observed identity id (``account_id`` from its
      bearer token) for the control arm;
    * a second account-bound actor of the same role with an observed identity
      id for the treatment arm — the peer whose data must not leak.

    The verdict is sealed by the ``owned_read_scope`` evaluator: an explicit
    rejection (4xx) passes, a body whose ownership fields all name the caller
    passes, any row carrying a foreign identity is a VIOLATION, and missing
    evidence stays INDETERMINATE.
    """
    method = _text(operation.get("method")).upper()
    if method not in {"GET", "HEAD"}:
        return None
    expression = _dict(property_spec.get("expression"))
    raw = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    # The rule must be an ownership-read constraint: the parameter itself is
    # the source declaration, and the rule text must not be a response-side
    # rule (those already took the forbidden-field branch above).
    if any(signal in raw for signal in _RESPONSE_SIDE_SIGNALS):
        return None
    # The rule must itself be an ownership-read constraint. The parameter's
    # description declares the caller-scoped contract, but the RULE TEXT must
    # state the same modality — otherwise an unrelated rule bound to the same
    # operation (管理员代查必须记录审计) would be silently recompiled into an
    # ownership test it never declared. Rules whose text lacks the modality
    # stay visible gaps.
    if not (
        any(term in raw for term in _OWNERSHIP_MODAL_TERMS)
        and any(modal in raw for modal in _OWNERSHIP_RESTRICTIVE_MODALS)
    ):
        return None
    owner_param: dict[str, Any] | None = None
    for _param in _list(operation.get("parameters")):
        if not isinstance(_param, dict):
            continue
        if _text(_param.get("in")).lower() != "query":
            continue
        if not _is_ownership_key_read_side(_text(_param.get("name"))):
            continue
        _declaration = _text(_param.get("description"))
        if not any(term in _declaration for term in _OWNERSHIP_MODAL_TERMS):
            continue
        owner_param = _param
        break
    if owner_param is None:
        return None
    _param_name = _text(owner_param.get("name"))
    actor_ref = _text(
        treatment_actor_ref or control_actor_ref or property_spec.get("actor_ref")
    )
    actor_catalog = [
        item
        for item in _list(_dict(behavior_ir).get("actors"))
        if isinstance(item, dict)
    ]
    actors_by_ref = {_text(item.get("id")): item for item in actor_catalog}
    actor = actors_by_ref.get(actor_ref)
    if not actor:
        return None
    own_identity = _text(actor.get("account_id"))
    if not own_identity:
        return None
    role = _text(actor.get("role") or actor.get("role_key"))
    peer = next(
        (
            item
            for item in actor_catalog
            if _text(item.get("id")) != actor_ref
            and _text(item.get("role") or item.get("role_key")) == role
            and _text(item.get("account_id"))
            and _text(item.get("account_id")) != own_identity
        ),
        None,
    )
    if peer is None:
        # No same-role peer with an observed identity: the two-arm read has
        # no "someone else's identity" to aim at. Stay a visible gap.
        return None
    peer_identity = _text(peer.get("account_id"))
    return {
        "status": "COMPILED",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "owned_scope_own_identity_read",
            "protocol_step": "positive_control",
            "query": {_param_name: own_identity},
            "property_template": _text(property_spec.get("template")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "owned_scope_peer_identity_read",
            "protocol_step": "single_observation",
            "query": {_param_name: peer_identity},
            "property_template": _text(property_spec.get("template")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }],
        "observers": [{"observer_id": "http_response"}],
        "assertion": {
            "kind": "owned_read_scope",
            "owner_identity": own_identity,
            "query_parameter": _param_name,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        },
    }


# Generic account-state precondition vocabulary: a rule like 仅 ACTIVE 用户
# 可登录 / 禁用用户不得登录 states an ACCOUNT-STATE precondition on an
# identity operation. The experiment must then exercise a NON-ACTIVE account
# from the runtime catalog — the target must reject its login. The vocabulary
# is generic system/account terminology, never an industry term.
_ACCOUNT_STATE_PRECONDITION_TERMS = (
    "ACTIVE", "active", "禁用", "锁定", "停用", "封禁", "冻结",
    "inactive", "disabled", "locked", "suspended", "status", "状态",
)
_ACCOUNT_STATE_RESTRICTIVE_MODALS = ("仅", "只能", "必须", "不得", "不能", "只有")
_IDENTITY_LOCATOR_KEYS = ("email", "phone", "mobile", "username", "login", "account", "user_id", "userid")

# Entity identity fields a business-object reference may carry in a request
# body (SKU / code / key / no / ref — generic relational identifiers, never
# industry vocabulary).
_ENTITY_IDENTITY_FIELD_RE = re.compile(r"(?:sku|code|key|no|ref)$", re.I)

# Public/sellable state names: a state outside this set is non-public by its
# own English meaning (DRAFT = unpublished, OFF_SALE = delisted,
# DISABLED = deactivated, EXPIRED = past validity, DELETED = removed). The
# set is generic status vocabulary, not industry terms.
_PUBLIC_ENTITY_STATUSES = frozenset({
    "on_sale", "active", "enabled", "published", "available",
    "open", "normal", "listed", "in_stock", "activated",
})

# Entity-state isolation signals: rules that forbid user-facing surfaces
# from exposing entities in non-public states (用户端不展示下架商品、草稿商品、
# 内部商品). 展示/提供/可见/开放 are generic exposure verbs; the state words
# (下架/草稿/停用/过期/禁用/删除/内部) name the non-public states.
_ENTITY_STATE_EXPOSURE_VERBS = ("不展示", "不提供", "不可见", "不开放", "不得展示", "禁止展示", "不显示")
_ENTITY_STATE_VIOLATION_WORDS = ("下架", "草稿", "停用", "过期", "禁用", "删除", "内部", "归档")

# Decision-endpoint vocabulary: an operation that decides an entity's
# eligibility and echoes the decision in its response (校验优惠券并试算优惠 /
# 使用优惠券 / 领取优惠券 / 模拟折扣计算 / validate/check/verify/use/claim/
# simulate/estimate). For such operations the response body IS the effect —
# there is no entity mutation to observe.
_DECISION_ENDPOINT_TOKENS = (
    "validate", "check", "verify", "eligible", "usable", "consume",
    "apply", "simulate", "quote", "estimate", "calculate", "use",
    "claim", "校验", "验证", "使用", "领取", "可用", "模拟", "计算",
    "预估", "报价", "试算",
)

# Consumption-state markers: rules that constrain an object's ELIGIBILITY at
# a decision operation (优惠券必须在有效期内 / 优惠券状态必须为 ACTIVE / 用户
# 使用次数不能超过限制). The violating input is an entity row the environment
# really has in the forbidden state — the treatment resolves it at runtime the
# same way the exposure arm does. The markers are positive state constraints
# (必须为/必须在/在有效期/状态/ACTIVE/只能用于/不能超过/不能使用/次数) and
# their violation words — generic business language, never industry terms.
_ENTITY_STATE_CONSUMPTION_MARKERS = (
    "必须为", "必须在", "在有效期", "有效期内", "只能用于", "不能使用",
    "不可用", "不能超过", "次数", "状态", "ACTIVE", "ENABLED",
)
# Which violation dimension the rule constrains: date-expiry rules (有效期/
# 过期/失效/生效) need a row whose validity DATE has passed even when its
# status is still public; status rules need a row whose status is non-public.
_ENTITY_STATE_EXPIRY_MARKERS = ("有效期", "过期", "失效", "生效", "到期", "有效期内")
_ENTITY_STATE_STATUS_MARKERS = ("状态", "ACTIVE", "ENABLED", "停用", "禁用")


def _entity_state_violation_mode(semantic_text: str) -> str:
    """Derive the runtime violation dimension from the rule's own vocabulary."""
    if any(marker in semantic_text for marker in _ENTITY_STATE_EXPIRY_MARKERS):
        return "expiry"
    if any(marker in semantic_text for marker in _ENTITY_STATE_STATUS_MARKERS):
        return "status"
    return "any"


def _non_public_entity_treatment(
    control: dict[str, Any],
    semantic_text: str,
    behavior_ir: dict[str, Any],
    property_spec: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Build the rejection-arm body for an entity-state isolation rule.

    A rule like 用户端不展示下架商品、草稿商品、内部商品 forbids user-facing
    surfaces from exposing entities in non-public states. The treatment must
    reference an entity that actually IS in such a state — resolved at
    runtime from the environment's own collection read (the entity's list
    GET, identified by the subject-entity binding recorded at IR build time),
    never guessed. Returns (treatment, mutation evidence) with a
    runtime-resolved mutation descriptor, or None when the rule is not an
    entity-state isolation rule or no entity list read exists.
    """
    if not any(v in semantic_text for v in _ENTITY_STATE_EXPOSURE_VERBS):
        if not any(m in semantic_text for m in _ENTITY_STATE_CONSUMPTION_MARKERS):
            return None
        if not any(w in semantic_text for w in _ENTITY_STATE_VIOLATION_WORDS):
            # Positive state constraints (必须在有效期内 / 必须为 ACTIVE /
            # 不能超过限制) name the REQUIRED state, not a violation word —
            # the treatment is an entity that does NOT satisfy the required
            # state. The consumption markers above already bound the rule to
            # the eligibility surface; a state-constraint rule without any
            # state vocabulary at all (must satisfy 最低金额) belongs to the
            # amount-boundary arm, not here.
            if not any(t in semantic_text for t in ("有效", "状态", "ACTIVE", "次数", "停用", "禁用", "过期")):
                return None
    elif not any(w in semantic_text for w in _ENTITY_STATE_VIOLATION_WORDS):
        return None
    if not isinstance(control, dict) or not isinstance(behavior_ir, dict):
        return None
    identity_field = ""
    identity_path = ""
    # The governed entity's identity slot. For the exposure arm (用户端不展示
    # 下架商品) the business line carries the entity identity inside a detail
    # array (items[].sku) and a top-level couponCode names a REFERENCED
    # entity — detail arrays take priority. For the consumption arm (优惠券
    # 必须在有效期内 / 状态必须为 ACTIVE) the rule constrains the REFERENCED
    # entity itself — its identity is the top-level slot (code), and replacing
    # items[].sku with a coupon code would break the request. The two arms
    # are distinguished by their trigger: exposure verbs vs consumption
    # markers.
    _consumption_triggered = not any(
        v in semantic_text for v in _ENTITY_STATE_EXPOSURE_VERBS
    )
    if _consumption_triggered:
        identity_field = next(
            (
                key
                for key in control
                if _ENTITY_IDENTITY_FIELD_RE.search(str(key))
            ),
            "",
        )
        if identity_field:
            identity_path = f"$.{identity_field}"
    if not identity_field:
        for _body_key, _body_value in control.items():
            if (
                isinstance(_body_value, list)
                and _body_value
                and isinstance(_body_value[0], dict)
            ):
                _inner_field = next(
                    (
                        key
                        for key in _body_value[0]
                        if _ENTITY_IDENTITY_FIELD_RE.search(str(key))
                    ),
                    "",
                )
                if _inner_field:
                    identity_field = _inner_field
                    identity_path = f"$.{_body_key}[0].{_inner_field}"
                    break
    if not identity_field:
        identity_field = next(
            (
                key
                for key in control
                if _ENTITY_IDENTITY_FIELD_RE.search(str(key))
            ),
            "",
        )
    if not identity_field:
        return None
    subject_entities = [
        _text(value).lower()
        for value in _list(_dict(property_spec).get("subject_entity_refs"))
        if _text(value)
    ]
    # The entity list read: a GET whose path names the subject entity
    # (products ↔ product from the IR subject-entity binding). Only such a
    # read lists the entity's rows with their status field — an identity-only
    # read (cart lines) cannot name a non-public entity.
    resolver = None
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        if re.search(r"(?:^|/)(?:health)(?:/|$)", _text(op.get("path")).lower()):
            continue
        _path_segments = [
            seg
            for seg in _text(op.get("path") or op.get("raw_path"))
            .lower()
            .strip("/")
            .split("/")
            if seg and seg not in {"api", "health", "v1"}
        ]
        if subject_entities and not any(
            _seg.startswith(_obj) or _obj.startswith(_seg)
            for _seg in _path_segments
            for _obj in subject_entities
        ):
            continue
        resolver = {
            "operation_ref": _text(op.get("id")),
            "method": "GET",
            "path": _text(op.get("path") or op.get("raw_path")),
        }
        break
    if not resolver:
        return None
    treatment = deepcopy(control)
    mutation = {
        "class": "runtime_entity_state_violation",
        "json_path": identity_path or f"$.{identity_field}",
        "resolver_operations": [resolver],
        "identity_field": identity_field,
        "status_field": "status",
        # The runtime resolver picks the violating row by the rule's own
        # dimension: date-expiry rules need a row whose validity date has
        # passed (status may still be public); status rules need a row whose
        # status is non-public; anything else takes either.
        "violation_mode": _entity_state_violation_mode(semantic_text),
    }
    return treatment, mutation


def _amount_boundary_treatment(
    control: dict[str, Any],
    semantic_text: str,
    behavior_ir: dict[str, Any],
    property_spec: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Build the rejection-arm body for an input amount-boundary rule.

    A rule like 必须满足最低订单金额 / 折扣券必须遵守封顶金额 constrains the
    ORDER amount a decision endpoint accepts for an entity (a coupon): the
    treatment uses an entity row whose DECLARED boundary (min_order_amount /
    max_discount) the runtime reads from the environment's own collection,
    then computes the violating input amount (boundary - 1 for a minimum;
    boundary * 100 / rate + 1 for a percent cap). The mutation descriptor is
    runtime-resolved — the body value cannot be known at compile time — and
    the executor replaces the entity identity field with the boundary-
    carrying row, exactly like the entity-state arm.
    """
    _MIN_MARKERS = ("最低金额", "最低订单金额", "门槛", "最小金额", "最低消费")
    _CAP_MARKERS = ("封顶", "上限", "最大优惠", "最大金额", "封顶金额")
    boundary_kind = ""
    if any(m in semantic_text for m in _MIN_MARKERS):
        boundary_kind = "min_amount"
    elif any(m in semantic_text for m in _CAP_MARKERS):
        boundary_kind = "max_cap"
    if not boundary_kind:
        return None
    if not isinstance(control, dict) or not isinstance(behavior_ir, dict):
        return None
    identity_field = next(
        (
            key
            for key in control
            if _ENTITY_IDENTITY_FIELD_RE.search(str(key))
        ),
        "",
    )
    if not identity_field:
        return None
    # The body field carrying the order amount: the numeric field whose name
    # names the order total (totalAmount/orderAmount/subtotal/amount) — the
    # generic amount vocabulary, not an industry term.
    amount_field = next(
        (
            key
            for key in control
            if re.search(
                r"(?:total|order|subtotal|amount|orderamount|totalamount)",
                str(key),
                re.IGNORECASE,
            )
        ),
        "",
    )
    if not amount_field:
        return None
    subject_entities = [
        _text(value).lower()
        for value in _list(_dict(property_spec).get("subject_entity_refs"))
        if _text(value)
    ]
    resolver = None
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        if re.search(r"(?:^|/)(?:health)(?:/|$)", _text(op.get("path")).lower()):
            continue
        _path_segments = [
            seg
            for seg in _text(op.get("path") or op.get("raw_path"))
            .lower()
            .strip("/")
            .split("/")
            if seg and seg not in {"api", "health", "v1"}
        ]
        if subject_entities and not any(
            _seg.startswith(_obj) or _obj.startswith(_seg)
            for _seg in _path_segments
            for _obj in subject_entities
        ):
            continue
        resolver = {
            "operation_ref": _text(op.get("id")),
            "method": "GET",
            "path": _text(op.get("path") or op.get("raw_path")),
        }
        break
    if not resolver:
        return None
    treatment = deepcopy(control)
    mutation = {
        "class": "runtime_amount_boundary_violation",
        "json_path": f"$.{amount_field}",
        "resolver_operations": [resolver],
        "identity_field": identity_field,
        "amount_field": amount_field,
        "boundary_kind": boundary_kind,
        "status_field": "status",
    }
    return treatment, mutation


def _non_active_account_treatment(
    control: dict[str, Any],
    semantic_text: str,
    actor_catalog: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str]] | None:
    """Build the rejection-arm body for an account-state precondition rule.

    A rule naming an account state (仅 ACTIVE 用户可登录) forbids non-ACTIVE
    identities from the operation. The treatment replaces the identity
    locator with a runtime account whose status is not ACTIVE and marks its
    password with the product's own secret reference (secret_ref:test_accounts:
    <email>); the governed executor resolves the credential before transport.
    Returns (treatment, mutation evidence) or None when the rule is not a
    state precondition or no non-ACTIVE account is declared.
    """
    if not any(term in semantic_text for term in _ACCOUNT_STATE_PRECONDITION_TERMS):
        return None
    if not any(modal in semantic_text for modal in _ACCOUNT_STATE_RESTRICTIVE_MODALS):
        return None
    if not isinstance(control, dict) or not isinstance(actor_catalog, list):
        return None
    identity_key = next(
        (
            key
            for key in control
            if any(token in str(key).lower() for token in _IDENTITY_LOCATOR_KEYS)
        ),
        "",
    )
    if not identity_key:
        return None
    non_active = next(
        (
            actor
            for actor in actor_catalog
            if isinstance(actor, dict)
            and _text(actor.get("account_ref") or actor.get("account"))
            and _text(actor.get("account_status") or actor.get("status")).upper()
            not in {"ACTIVE", "ACTIVATED", "ENABLED", ""}
        ),
        None,
    )
    if not non_active:
        return None
    account_email = _text(
        non_active.get("account_ref") or non_active.get("account")
    )
    password_key = next(
        (
            key
            for key in control
            if "password" in str(key).lower() or "passwd" in str(key).lower()
        ),
        "",
    )
    treatment = deepcopy(control)
    treatment[identity_key] = account_email
    if password_key:
        treatment[password_key] = (
            f"secret_ref:test_accounts:{account_email}"
        )
    return treatment, {
        "json_path": f"$.{identity_key}",
        "constraint": f"account_state_not_active:{account_email}",
        "source": "account_state_precondition",
    }


def _mutation_required_field_removals(mutation: dict[str, Any]) -> list[str]:
    """Fields a single-constraint mutation deliberately removes from the body.

    A ``constraint: required`` mutation omits a declared required field to
    test whether the target rejects the malformed write. Compile time reads
    the descriptor and stamps the removed fields on the step as an explicit
    runtime contract — the descriptor itself stays inert at runtime.
    """
    if _text(mutation.get("constraint")).lower() != "required":
        return []
    path = _text(mutation.get("json_path"))
    if not path:
        return []
    leaf = path.rsplit(".", 1)[-1].strip("[]")
    return [leaf] if leaf else []


def _normalize_identity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _strip_ownership_identity_fields(
    value: Any,
    keep: set[str] | None = None,
) -> Any:
    """Validation arms isolate the field under test.

    Ownership identity fields (userId/ownerId/accountId/…) carried by the
    source example point the write at a different account. In a validation
    experiment that confounds the effect observation: a 2xx write lands in an
    invisible cart and reads as zero effect (VALIDATION_EFFECT_AMBIGUOUS)
    instead of proving the malformed input was accepted. Dropping them lets
    the target derive the identity from the authenticated actor, so the
    treatment's effect is observable on the acting account. The field under
    mutation (if it is an ownership field itself) is preserved.
    """
    if not isinstance(value, dict):
        if isinstance(value, list):
            return [_strip_ownership_identity_fields(item, keep) for item in value]
        return value
    keep_norm = {_normalize_identity_key(k) for k in (keep or set())}
    return {
        key: _strip_ownership_identity_fields(child, keep)
        for key, child in value.items()
        if _normalize_identity_key(key) in keep_norm
        or not _is_ownership_key_read_side(str(key))
    }


def _validation_protocol_material(
    operation: dict[str, Any],
    property_spec: dict[str, Any],
    actor_catalog: list[dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    control = source_request_example(operation)
    schema = _request_body_schema(operation)
    if not control and schema:
        control = _generate_minimal_body_from_schema(schema)
    # ── Account-state precondition arm ──
    # A rule naming an account state (仅 ACTIVE 用户可登录) is not tested by
    # an invalid-format mutation: the property under test is that a non-ACTIVE
    # account's credential is REJECTED. Build that treatment before the
    # generic mutation strategies when the runtime catalog declares such an
    # account.
    if control and isinstance(control, dict):
        _semantic_text_pre = "\n".join(
            _text(value)
            for value in (
                _dict(property_spec.get("expression")).get("raw"),
                property_spec.get("source_intent"),
                property_spec.get("description"),
            )
            if _text(value)
        )
        _state_treatment = _non_active_account_treatment(
            control, _semantic_text_pre, list(actor_catalog or [])
        )
        if _state_treatment:
            treatment, mutation = _state_treatment
            return control, treatment, mutation
        # ── Entity-state isolation arm ──
        # 用户端不展示下架商品、草稿商品、内部商品 forbids user-facing
        # surfaces from exposing non-public-state entities. The treatment
        # references an entity the environment actually has in such a state,
        # resolved at runtime through the status-carrying list read (the
        # response schema names both the identity field and status). The
        # mutation descriptor is runtime-resolved — the body value cannot be
        # known at compile time.
        _entity_state_treatment = _non_public_entity_treatment(
            control, _semantic_text_pre, _dict(behavior_ir), property_spec,
        )
        if _entity_state_treatment:
            treatment, mutation = _entity_state_treatment
            return control, treatment, mutation
        # ── Input amount-boundary arm ──
        # 必须满足最低订单金额 / 折扣券必须遵守封顶金额 constrain the order
        # amount a decision endpoint accepts for an entity. The treatment's
        # violating amount is computed at runtime from the boundary the
        # environment's own entity row declares (min_order_amount - 1, or the
        # percent cap * 100 / rate + 1) — the mutation descriptor is
        # runtime-resolved, never a compile-time guess.
        _amount_treatment = _amount_boundary_treatment(
            control, _semantic_text_pre, _dict(behavior_ir), property_spec,
        )
        if _amount_treatment:
            treatment, mutation = _amount_treatment
            return control, treatment, mutation
    if not schema:
        # No request body schema — but if we have a control body from
        # request_example, apply semantic invalid values using inferred types.
        if control and isinstance(control, dict):
            semantic_text_no_schema = "\n".join(
                _text(v)
                for v in (
                    _dict(property_spec.get("expression")).get("raw"),
                    property_spec.get("source_intent"),
                    property_spec.get("description"),
                )
                if _text(v)
            )
            # Try explicit target fields first, then all fields
            explicit_no_schema = [
                _text(v).removeprefix("$.")
                for v in (
                    property_spec.get("field"),
                    property_spec.get("field_name"),
                    property_spec.get("field_ref"),
                    property_spec.get("json_path"),
                    _dict(property_spec.get("expression")).get("field"),
                    _dict(property_spec.get("expression")).get("field_name"),
                )
                if _text(v)
            ]
            field_order_no_schema = [
                *[f for f in explicit_no_schema if f in control],
                *[f for f in control if f not in explicit_no_schema],
            ]
            for field in field_order_no_schema:
                val = control[field]
                if isinstance(val, bool):
                    inferred_type = "boolean"
                elif isinstance(val, int):
                    inferred_type = "integer"
                elif isinstance(val, float):
                    inferred_type = "number"
                elif isinstance(val, str):
                    inferred_type = "string"
                else:
                    continue
                result = _semantic_invalid_value(field, inferred_type, {}, semantic_text_no_schema)
                if result is not None:
                    invalid_value, constraint = result
                    treatment = deepcopy(control)
                    treatment[field] = invalid_value
                    return control, treatment, {
                        "json_path": f"$.{field}",
                        "constraint": constraint,
                        "source": "inferred_from_example",
                    }
            # No semantic match — fall back to removing first field
            if field_order_no_schema:
                field = field_order_no_schema[0]
                treatment = deepcopy(control)
                treatment.pop(field, None)
                return control, treatment, {
                    "json_path": f"$.{field}",
                    "constraint": "required_inferred",
                    "source": "inferred_from_example",
                }
        # No control body or no fields — use synthetic fallback
        method = _text(operation.get("method", "")).upper()
        if method in ("PATCH", "PUT"):
            control = {"status": "active"}
            return control, {}, {"json_path": "$.status", "constraint": "synthetic", "source": "synthetic_fallback"}
        if method == "POST":
            control = {}
            return control, {}, {"json_path": "$", "constraint": "synthetic", "source": "synthetic_fallback"}
        if method == "DELETE":
            # DELETE has no body — the validation test checks the HTTP response
            control = {}
            return control, {}, {"json_path": "$", "constraint": "synthetic", "source": "synthetic_fallback_delete"}
        return {}, {}, {}
    if not control:
        # No documented example — generate a minimal valid body from the schema.
        # This is a best-effort fallback: the generated body may not exercise all
        # business rules, but it allows the obligation to compile and execute,
        # which is better than blocking the entire discovery pipeline.
        control = _generate_minimal_body_from_schema(schema)
        if not control:
            return {}, {}, {}
    properties = _dict(schema.get("properties"))

    explicit_targets: list[str] = []
    expression = _dict(property_spec.get("expression"))
    direct_values = [
        property_spec.get("field"),
        property_spec.get("field_name"),
        property_spec.get("field_ref"),
        property_spec.get("json_path"),
        expression.get("field"),
        expression.get("field_name"),
        expression.get("field_ref"),
        expression.get("json_path"),
    ]
    semantic_text = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    for field in properties:
        normalized_direct = {
            _text(value).removeprefix("$.")
            for value in direct_values
            if _text(value)
        }
        if field in normalized_direct or re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(str(field))}(?![A-Za-z0-9_])",
            semantic_text,
        ):
            explicit_targets.append(str(field))

    # ── Strategy 1: semantic invalid value (catches negative price, weak password, etc.) ──
    semantic_field_order = [
        *explicit_targets,
        *[str(f) for f in properties if str(f) not in explicit_targets],
    ]
    # Strategy 0 pre-pass: an enum-restrictive rule names the ONLY allowed
    # value (对外注册只能创建 buyer) — the field whose declared enum contains
    # that value is the governed field, and any OTHER declared enum value is
    # the mutation. This must run before the generic per-field heuristics:
    # the rule's statement may omit the field name entirely (the field list
    # lives in a following clause), so only the enum membership identifies
    # the target field.
    _restricted_value_match = re.search(
        r"(?:只能|仅能|仅|必须|只)(?:创建|为|是|使用|设置|取)?"
        r"\s*([A-Za-z_][A-Za-z0-9_-]*)",
        semantic_text,
    )
    if _restricted_value_match:
        _allowed_value = _text(_restricted_value_match.group(1)).lower()
        for field in semantic_field_order:
            if field not in control:
                continue
            raw_property = properties.get(field)
            enum_values = [
                _text(value)
                for value in _list(_dict(raw_property).get("enum"))
                if _text(value)
            ]
            if not any(
                _text(value).lower() == _allowed_value
                for value in enum_values
            ):
                continue
            for candidate in enum_values:
                if _text(candidate).lower() != _allowed_value:
                    treatment = deepcopy(control)
                    treatment[field] = candidate
                    return control, treatment, {
                        "json_path": f"$.{field}",
                        "constraint": f"enum_value_not_allowed:{candidate}",
                        "source": "request_schema",
                    }
            break
    for field in semantic_field_order:
        if field not in control:
            continue
        raw_property = properties.get(field)
        property_schema = _dict(raw_property)
        declared_type = _text(property_schema.get("type")).lower()
        if not declared_type:
            # Infer type from control value
            val = control[field]
            if isinstance(val, bool):
                declared_type = "boolean"
            elif isinstance(val, int):
                declared_type = "integer"
            elif isinstance(val, float):
                declared_type = "number"
            elif isinstance(val, str):
                declared_type = "string"
            else:
                continue
        result = _semantic_invalid_value(field, declared_type, property_schema, semantic_text)
        if result is not None:
            invalid_value, constraint = result
            treatment = deepcopy(control)
            treatment[field] = invalid_value
            return control, treatment, {
                "json_path": f"$.{field}",
                "constraint": constraint,
                "source": "request_schema",
            }

    # ── Strategy 2: remove required field ──
    required = [
        _text(value)
        for value in (schema.get("required") or [])
        if _text(value)
    ]
    required_order = [
        *[field for field in explicit_targets if field in required],
        *[field for field in required if field not in explicit_targets],
    ]
    for field in required_order:
        if field not in control:
            continue
        treatment = deepcopy(control)
        treatment.pop(field, None)
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": "required",
            "source": "request_schema",
        }

    # ── Strategy 3: type mismatch ──
    def matches_declared_type(value: Any, declared_type: str) -> bool:
        if declared_type == "string":
            return isinstance(value, str)
        if declared_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if declared_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if declared_type == "boolean":
            return isinstance(value, bool)
        if declared_type == "array":
            return isinstance(value, list)
        if declared_type == "object":
            return isinstance(value, dict)
        if declared_type == "null":
            return value is None
        return False

    field_order = [
        *explicit_targets,
        *[str(field) for field in properties if str(field) not in explicit_targets],
    ]
    for field in field_order:
        raw_property = properties.get(field)
        property_schema = _dict(raw_property)
        declared_type = _text(property_schema.get("type")).lower()
        if field not in control or not matches_declared_type(control[field], declared_type):
            continue
        invalid_value: Any = [] if declared_type == "object" else {} if declared_type != "null" else True
        treatment = deepcopy(control)
        treatment[field] = invalid_value
        return control, treatment, {
            "json_path": f"$.{field}",
            "constraint": f"type:{declared_type}",
            "source": "request_schema",
        }
    return {}, {}, {}


def _identity_addressed_read_isolation_protocol(
    *,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Two-arm owned-resource read for identity-addressed path targets.

    A resource-targeted owned read (profile/{id} documented as 权限：本人或
    管理员) has no collection query param and usually no create fixture for
    the owned entity — the fixture-backed ``owned_resource`` proof cannot
    materialize, so the obligation would block without ever testing the
    boundary. When the runtime catalogue carries two account-bound actors
    with observed identities, compile the two-arm read directly:

    * control — the owner reads its own identity-addressed resource;
    * treatment — the viewer reads the owner's resource (the same concrete
      path, so same-resource identity is provable from the observed body).

    Both paths are resolved at compile time from runtime-observed account
    ids (the actor's own login identity) — no fixture, no fabricated data.
    Returns None when the shape does not apply (no identity path param, no
    two distinct actors with observed identities); the caller then falls
    through to the existing compile chain (which blocks visibly when the
    owned-resource fixture cannot be materialized).
    """
    path = _text(operation.get("path") or operation.get("raw_path"))
    if "{" not in path and "/:" not in path:
        return None
    owner_ref = _text(property_spec.get("owner_actor_ref") or control_actor_ref)
    viewer_ref = _text(property_spec.get("viewer_actor_ref") or treatment_actor_ref)
    if not owner_ref or not viewer_ref or owner_ref == viewer_ref:
        return None
    actor_catalog = [
        item
        for item in _list(_dict(behavior_ir).get("actors"))
        if isinstance(item, dict)
    ]
    actors_by_ref = {_text(item.get("id")): item for item in actor_catalog}
    owner = actors_by_ref.get(owner_ref)
    viewer = actors_by_ref.get(viewer_ref)
    if not owner or not viewer:
        return None
    owner_identity = _text(owner.get("account_id"))
    if not owner_identity:
        return None
    identity_param = ""
    for _param in _list(operation.get("parameters")):
        if not isinstance(_param, dict):
            continue
        if _text(_param.get("in") or _param.get("location")).lower() != "path":
            continue
        _name = _text(_param.get("name"))
        if not _name:
            continue
        if _is_ownership_key_read_side(_name) or re.sub(
            r"[^a-z0-9]+", "", _name.lower()
        ).endswith("id"):
            identity_param = _name
            break
    if not identity_param:
        return None
    owner_path = re.sub(
        r"\{" + re.escape(identity_param) + r"\}",
        owner_identity,
        path,
    )
    if "{" in owner_path or "/:" in owner_path:
        # Additional non-identity placeholders must go through the ordinary
        # binding machinery — this shape does not own them.
        return None
    return {
        "status": "COMPILED",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": owner_ref,
            "operation_ref": operation_ref,
            "intent": "owned_scope_own_identity_read",
            "protocol_step": "positive_control",
            "path": owner_path,
            "property_template": _text(property_spec.get("template")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": viewer_ref,
            "operation_ref": operation_ref,
            "intent": "owned_scope_peer_identity_read",
            "protocol_step": "treatment",
            "path": owner_path,
            "property_template": _text(property_spec.get("template")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "actor_identity"},
        ],
        "assertion": {
            "kind": "owner_tenant_visibility",
            "require_same_resource": True,
        },
        "_identity_addressed_read": True,
    }


def compile_family_protocol(
    *,
    risk_family: str,
    operation: dict[str, Any],
    operation_ref: str,
    control_actor_ref: str,
    treatment_actor_ref: str,
    property_spec: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile exact family steps or return one typed blocker."""

    family = _text(risk_family)
    method = _text(operation.get("method")).upper()
    template = _text(property_spec.get("template"))
    sibling_operations = (
        _list(_dict(behavior_ir).get("operations")) if behavior_ir else []
    )
    # Source-grounded permitted invocation: one actor, observe the documented
    # operation. Used when IR has permits but no executable deny pair — must
    # not invent a second actor or silently drop the module from scheduling.
    if template == "permitted_operation_invocation":
        actor = control_actor_ref or treatment_actor_ref or _text(
            property_spec.get("actor_ref")
        )
        if not actor:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": "permitted_actor",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": actor,
                "operation_ref": operation_ref,
                "intent": "permitted_operation_invocation",
                "protocol_step": "permitted_invocation",
                "property_template": template,
            }],
            "assertion": {
                "kind": "http_status_class",
                "expected_class": 2,
                "compare_field": "status_code",
            },
        }
    needs_control = family in {
        "authorization",
        "isolation",
        "validation",
        "privacy",
        "visibility",
    }
    if needs_control and not control_actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "control_actor",
        }
    if not treatment_actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "treatment_actor",
        }

    if family == "idempotency":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "idempotency_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "idempotency_initial_write",
                "protocol_step": "initial_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "idempotency_repeat_write",
                "protocol_step": "repeat_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
        }

    if family == "concurrency":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "concurrency_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        barrier_group = f"barrier:{operation_ref}"
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "concurrency_participant_control",
                "protocol_step": "concurrent_write",
                "barrier_group": barrier_group,
                "barrier_participant": "control",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "concurrency_participant_treatment",
                "protocol_step": "concurrent_write",
                "barrier_group": barrier_group,
                "barrier_participant": "treatment",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
            }],
        }

    if family == "conservation":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "conservation_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            body = _minimal_body_from_schema(operation)
        expression = _dict(property_spec.get("expression"))
        equation = _dict(property_spec.get("equation") or expression.get("equation"))
        # Prefer structured operands over NL guessing when present.
        if not equation:
            _op_terms: list[str] = []
            for _op in _list(expression.get("operands")):
                if not isinstance(_op, dict):
                    continue
                _f = _text(_op.get("field_id") or _op.get("field"))
                if _f:
                    _op_terms.append(_f)
            if _op_terms:
                equation = {
                    "operator": _text(expression.get("operator")) or "unchanged_sum",
                    "terms": list(dict.fromkeys(_op_terms)),
                }
        # V1.6.0: never invent conservation terms via NL guessing when structure
        # is empty. Empty terms must block before planner/executor/oracle.
        _term_rows = [
            t for t in _list(equation.get("terms") or equation.get("fields"))
            if _text(t) or isinstance(t, dict)
        ]
        if not equation or not _term_rows:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
                "detail": "conservation_requires_non_empty_equation_terms",
            }
        # Prefer JSON field names over cf_* for observer/assertion key alignment.
        _name_by_cf: dict[str, str] = {}
        for _op in _list(expression.get("operands")):
            if isinstance(_op, dict) and _text(_op.get("field_id")) and _text(_op.get("field")):
                _name_by_cf[_text(_op.get("field_id"))] = _text(_op.get("field"))
        _normalized_terms: list[str] = []
        for _t in _term_rows:
            if isinstance(_t, dict):
                _normalized_terms.append(
                    _text(_t.get("field") or _t.get("field_id"))
                )
            else:
                _tt = _text(_t)
                _normalized_terms.append(_name_by_cf.get(_tt, _tt))
        _normalized_terms = [t for t in _normalized_terms if t]
        if not _normalized_terms:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
                "detail": "conservation_requires_non_empty_equation_terms",
            }
        equation = {
            **equation,
            "terms": list(dict.fromkeys(_normalized_terms)),
            "operator": _text(equation.get("operator")) or "unchanged_sum",
        }
        # ── Non-negative boundary arm ──
        # 不能为负数 statements are FIELD BOUNDARY constraints: after the
        # write, the declared field must not go below zero. The treatment must
        # push the field past the boundary, which for a delta-style write
        # (adjust delta) depends on the current runtime value — the delta
        # needed is -(current + 1). The mutation descriptor is resolved at
        # runtime through the entity's status read (the single-entity GET
        # whose path binds the body's identity field).
        if _text(equation.get("operator")) == "non_negative":
            _bound_mutation = None
            _resolvers: list[dict[str, str]] = []
            _bound_field = _text(_normalized_terms[0]) if _normalized_terms else ""
            _delta_field = next(
                (
                    key
                    for key in _dict(body)
                    if re.search(r"(?:delta|change|adjust|incr|decr|diff)$", str(key), re.I)
                ),
                "",
            )
            _identity_field = next(
                (
                    key
                    for key in _dict(body)
                    if re.search(r"(?:sku|code|key|no|ref)$", str(key), re.I)
                ),
                "",
            )
            if _delta_field and _identity_field:
                for _op in _list(_dict(behavior_ir).get("operations")):
                    if not isinstance(_op, dict):
                        continue
                    if _text(_op.get("method")).upper() not in {"GET", "HEAD"}:
                        continue
                    _rp = _text(_op.get("path") or _op.get("raw_path"))
                    if re.search(r"(?:^|/)(?:health)(?:/|$)", _rp.lower()):
                        continue
                    if _identity_field.lower() not in _rp.lower():
                        continue
                    _resolvers.append({
                        "operation_ref": _text(_op.get("id")),
                        "method": "GET",
                        "path": _rp,
                    })
                if _resolvers:
                    _bound_mutation = {
                        "class": "runtime_boundary_break",
                        "json_path": f"$.{_delta_field}",
                        "resolver_operations": _resolvers,
                        "bound_field": _bound_field,
                        "delta_field": _delta_field,
                        "identity_field": _identity_field,
                    }
            _cons_assertion: dict[str, Any] = {
                "kind": "non_negative",
                "equation": equation,
                "operands": _list(expression.get("operands")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
                "rule_id": _text(property_spec.get("invariant_ref")),
            }
            _treatment_step: dict[str, Any] = {
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "conservation_mutation",
                "protocol_step": "conservation_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            }
            if _bound_mutation:
                _treatment_step["mutation"] = _bound_mutation
            _non_neg_observers: list[dict[str, Any]] = [
                {"observer_id": "business_effect"},
                {"observer_id": "entity_state"},
            ]
            if _resolvers:
                # The entity_state observer reads the entity back after the
                # write (GET /api/inventory/{sku}); without a resolver the
                # after-values evidence never materializes and the boundary
                # assertion stays BLOCKED_MISSING_OBSERVER. Reuse the boundary
                # arm's resolvers for the observer's identity readback.
                _non_neg_observers[1] = {
                    "observer_id": "entity_state",
                    "resolver_operations": [
                        dict(row) for row in _resolvers
                    ],
                }
            return {
                "status": "COMPILED",
                "control_plan": [],
                "treatment_plan": [_treatment_step],
                "observers": _non_neg_observers,
                "assertion": _cons_assertion,
            }
        _cons_assertion: dict[str, Any] = {
            "kind": "conservation",
            "equation": equation,
            "operands": _list(expression.get("operands")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
            "rule_id": _text(property_spec.get("invariant_ref")),
        }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "conservation_mutation",
                "protocol_step": "conservation_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            }],
            "observers": [
                {"observer_id": "business_effect"},
                {"observer_id": "entity_state"},
            ],
            "assertion": _cons_assertion,
        }

    if family == "temporal":
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "temporal_requires_write_operation",
            }
        body = source_request_example(operation, sibling_operations=sibling_operations)
        if method in {"POST", "PUT", "PATCH"} and not body:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "temporal_requires_source_request_example",
            }
        expression = _dict(property_spec.get("expression"))
        window_ms = expression.get("window_ms") or property_spec.get("window_ms")
        # Date-range temporal: expression has date_field/bounds but no window_ms
        date_field = _text(expression.get("date_field") or expression.get("field") or expression.get("start_date"))
        has_date_bounds = bool(
            expression.get("bounds")
            or expression.get("start")
            or expression.get("end")
            or expression.get("min")
            or expression.get("max")
            or expression.get("from")
            or expression.get("to")
        )
        if date_field and has_date_bounds:
            # Date-range temporal boundary experiment
            return {
                "status": "COMPILED",
                "control_plan": [{
                    "step_id": "control_1",
                    "actor_ref": control_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "valid_source_control",
                    "protocol_step": "positive_control",
                    "body": deepcopy(body),
                }],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "temporal_date_boundary_mutation",
                    "protocol_step": "temporal_date_write",
                    "body": deepcopy(body),
                    "date_field": date_field,
                    "property_template": _text(property_spec.get("template")),
                    "invariant_ref": _text(property_spec.get("invariant_ref")),
                }],
                "observers": [{"observer_id": "http_response"}, {"observer_id": "entity_state"}],
                "assertion": {
                    "kind": "temporal_date_boundary",
                    "date_field": date_field,
                    "bounds": expression.get("bounds") or {
                        k: expression[k]
                        for k in ("start", "end", "min", "max", "from", "to")
                        if k in expression
                    },
                },
            }
        if not isinstance(window_ms, (int, float)) or isinstance(window_ms, bool) or window_ms <= 0:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ASSERTION",
                "detail": "temporal_requires_positive_source_window_ms",
            }
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "temporal_mutation",
                "protocol_step": "temporal_write",
                "body": deepcopy(body),
                "property_template": _text(property_spec.get("template")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
            }],
            "observers": [{"observer_id": "temporal_window"}],
            "assertion": {
                "kind": "eventual_consistency",
                "window_ms": window_ms,
            },
        }

    if family == "validation":
        # ── Response-side constraint protocol ──
        # A source rule constraining RESPONSE content (导出结果禁止包含
        # password 或其他认证凭据 / 响应不得返回支付密钥、签名密钥或完整
        # 敏感配置) asserts the forbidden material is absent from the observed
        # body — a single-arm observation, never a write mutation. The
        # forbidden fields come from the rule's own text: ASCII identifiers
        # after a prohibition word, or generic secret-family concepts
        # (密钥/密码/凭据/…) mapped to canonical credential matchers. The
        # observation is allowed on any method the source binds (a write
        # operation whose own description documents the constraint); the
        # governed executor decides write safety and cleanup, the protocol
        # only observes the response content.
        _forbidden_fields, _family_match = _extract_forbidden_response_fields(property_spec)
        if _forbidden_fields:
            # A single-arm response observation on a GET/HEAD needs the
            # operation's declared query parameters to be a real request
            # (GET /api/auth/otp/send?email=…): without them the target
            # rejects the request before the response content exists.
            _query: dict[str, Any] = {}
            for _param in _list(operation.get("parameters")):
                if not isinstance(_param, dict):
                    continue
                if _text(_param.get("in")).lower() != "query":
                    continue
                _param_name = _text(_param.get("name"))
                if not _param_name:
                    continue
                _param_value = (
                    _param.get("example")
                    or _dict(_param.get("schema")).get("example")
                    or _param.get("default")
                )
                if _param_value is not None:
                    _query[_param_name] = _param_value
            _control_step: dict[str, Any] = {
                "step_id": "control_1",
                "actor_ref": control_actor_ref,
                "operation_ref": operation_ref,
                "intent": "response_side_constraint_observation",
                "protocol_step": "positive_control",
            }
            if _query:
                _control_step["query"] = _query
            return {
                "status": "COMPILED",
                "control_plan": [_control_step],
                "treatment_plan": [],
                "assertion": {
                    "kind": "response_field_absent",
                    "fields": _forbidden_fields,
                    "family_match": _family_match,
                },
            }
        # ── Read-side owned-scope projection ──
        # A source rule constraining a GET/HEAD ownership read (普通用户只能
        # 读取自己的地址) has no request body to mutate; historically every
        # such obligation died below as validation_body_protocol_requires_
        # write_operation — a structural break in the four-link reachability
        # chain, not a data problem. When the operation declares an ownership
        # query parameter whose own description states the caller-scoped
        # constraint, and the runtime catalogue holds two account-bound
        # actors of the same role with runtime-observed identity ids, the
        # protocol compiles a two-arm read (own identity vs peer identity)
        # sealed by the owned_read_scope evaluator. A rule without that
        # structured material stays a visible BLOCKED below — it must never
        # silently degrade into a vacuous 2xx observation.
        if method in {"GET", "HEAD"}:
            _read_projection = _read_side_owned_scope_projection(
                operation=operation,
                operation_ref=operation_ref,
                property_spec=property_spec,
                control_actor_ref=control_actor_ref,
                treatment_actor_ref=treatment_actor_ref,
                behavior_ir=behavior_ir,
            )
            if _read_projection is not None:
                return _read_projection
        parameter_location = _text(property_spec.get("parameter_location")).lower()
        tokens = property_spec.get("field_tokens")
        if (
            not parameter_location
            and isinstance(tokens, list)
            and tokens
            and isinstance(tokens[0], str)
            and str(tokens[0]).startswith("@")
        ):
            parameter_location = str(tokens[0])[1:].lower()
        allows_non_body = parameter_location in {"query", "path", "header"}
        if method not in {"POST", "PUT", "PATCH", "DELETE"} and not (
            allows_non_body and method in {"GET", "HEAD"}
        ):
            # A GET/HEAD rule that reached this point has no forbidden
            # response field, no ownership binding material and no declared
            # parameter location: there is no decidable assertion projection
            # for it, so it stays a visible BLOCKED. Silent degradation into
            # a bare status probe would fabricate verdicts from noise.
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": (
                    "validation_body_protocol_requires_write_operation"
                    if method in {"POST", "PUT", "PATCH", "DELETE"}
                    else "read_side_rule_lacks_decidable_assertion"
                ),
            }
        if allows_non_body and method in {"GET", "HEAD"}:
            # Parameter-only mutations compile through the privacy facade; emit a
            # placeholder COMPILED shell that the facade rewrites with query/path.
            return {
                "status": "COMPILED",
                "control_plan": [{
                    "step_id": "control_1",
                    "actor_ref": control_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "valid_source_control",
                    "protocol_step": "positive_control",
                }],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "single_constraint_mutation",
                    "protocol_step": "single_mutation",
                }],
                "assertion": {
                    "kind": "http_status_class",
                    "expected_class": 4,
                    "compare_field": "status_code",
                },
            }
        control_body, treatment_body, mutation = _validation_protocol_material(
            operation,
            property_spec,
            actor_catalog=_list(_dict(behavior_ir).get("actors")),
            behavior_ir=_dict(behavior_ir),
        )
        if not mutation:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "detail": "validation_requires_source_example_and_request_schema",
            }
        # Validation arms isolate the field under test: ownership identity
        # fields from the source example (userId/ownerId/…) would aim the
        # write at another account and make the effect invisible to the
        # acting account (VALIDATION_EFFECT_AMBIGUOUS). Drop them so the
        # target derives the identity from the authenticated actor.
        _mutation_field = _text(mutation.get("json_path")).rsplit(".", 1)[-1].strip("[]")
        _keep_fields = {_mutation_field} if _mutation_field else None
        control_body = _strip_ownership_identity_fields(
            control_body, keep=_keep_fields
        )
        treatment_body = _strip_ownership_identity_fields(
            treatment_body, keep=_keep_fields
        )
        # ── Cap-boundary assertion ──
        # A percent-cap rule (折扣券必须遵守封顶金额) does not reject the
        # input — a correct target accepts it and CLAMPS the discount to the
        # declared cap. The assertion is the value bound discount ≤ cap, both
        # read from the target's own response (discountAmount vs the echoed
        # coupon.max_discount), never a rejection expectation.
        if (
            _text(mutation.get("class")) == "runtime_amount_boundary_violation"
            and _text(mutation.get("boundary_kind")) == "max_cap"
        ):
            _cap_assertion: dict[str, Any] = {
                "kind": "json_path_compare",
                "path": "$.discountAmount",
                "expected_path": "$.coupon.max_discount",
                "operator": "lte",
                "compare_field": "json",
            }
        else:
            # Decision endpoints (validate/check/verify/use/claim/simulate:
            # the operation decides eligibility and echoes its decision in
            # the response body) are read-only for the entity — their
            # "effect" IS the response decision, and a zero-effect signal
            # must not mask an accepted-but-should-be-rejected treatment.
            # Marked on the assertion for the validation oracle.
            _op_surface = (
                f"{_text(operation.get('path') or operation.get('raw_path'))} "
                f"{_text(operation.get('summary'))}"
            ).casefold()
            _response_decision = any(
                token in _op_surface
                for token in _DECISION_ENDPOINT_TOKENS
            )
            _cap_assertion = {
                "kind": "http_status_class",
                "expected_class": 4,
                "compare_field": "status_code",
            }
            if _response_decision:
                _cap_assertion["response_decision"] = True
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref,
                "operation_ref": operation_ref,
                "intent": "valid_source_control",
                "protocol_step": "positive_control",
                "body": deepcopy(control_body),
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "single_constraint_mutation",
                "protocol_step": "single_mutation",
                "body": deepcopy(treatment_body),
                "mutation": mutation,
                # Explicit runtime contract for required-field-removal arms:
                # the pre-transport required-field gate must exempt exactly
                # these fields so the malformed write reaches the target.
                # The mutation descriptor itself stays compile-time inert.
                "required_field_removal": _mutation_required_field_removals(
                    mutation
                ),
            }],
            "assertion": _cap_assertion,
        }

    if family == "state":
        # ── Phase 2: postcondition-driven structured assertion ──
        # When the property_spec carries a postcondition expression, emit a
        # typed postcondition assertion (entity.field must_become expected_value)
        # instead of the generic state_transition assertion.
        _expr = _dict(property_spec.get("expression"))
        _expr_kind = _text(_expr.get("kind"))
        if _expr_kind == "postcondition":
            # ── P0-5: detect field_delta operands for causal verification ──
            _pc_operands = _list(_expr.get("operands"))
            _has_delta_fields = any(
                isinstance(op, dict)
                and (op.get("expected_delta") is not None or _text(op.get("expected_delta_direction")))
                for op in _pc_operands
            )
            _assertion_kind = "field_delta" if _has_delta_fields else "postcondition"
            return {
                "status": "COMPILED",
                "control_plan": [],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "state_transition_treatment",
                    "protocol_step": "treatment",
                }],
                "observers": [
                    {"observer_id": "before_state"},
                    {"observer_id": "after_state"},
                    {"observer_id": "entity_state"},
                ],
                "assertion": {
                    "kind": _assertion_kind,
                    "operator": _text(_expr.get("operator")),
                    "operands": _pc_operands,
                    "fields": _pc_operands if _has_delta_fields else [],
                },
            }
        # ── Cross-entity state consistency: resolve from raw + IR when no explicit states ──
        _state_from = _text(property_spec.get("from_state_ref") or property_spec.get("from_state"))
        _state_to = _text(property_spec.get("to_state_ref") or property_spec.get("to_state"))
        # V1.6.1: lift concrete from/to from expression operands (forbidden_state_transition).
        if not _state_from or not _state_to:
            for _op in _list(_expr.get("operands")):
                if not isinstance(_op, dict):
                    continue
                if not _state_from:
                    _state_from = _text(_op.get("from_state") or _op.get("from_state_ref"))
                if not _state_to:
                    _state_to = _text(_op.get("to_state") or _op.get("to_state_ref"))
        _state_resolved: dict[str, Any] = {}
        if not _state_from and not _state_to and behavior_ir:
            _state_inv = {
                "expression": _expr,
                "description": _text(
                    property_spec.get("template")
                    or _expr.get("raw")
                    or property_spec.get("invariant_ref")
                ),
            }
            _state_rr = resolve_expression_from_invariant(_state_inv, behavior_ir, operation=operation)
            if _state_rr.get("status") == "RESOLVED":
                _state_resolved = _state_rr
        if _state_resolved:
            _st_assertion: dict[str, Any] = {
                "kind": "cross_entity_consistency",
                "structured_expression": _state_resolved.get("expression", {}),
                "entity_bindings": _state_resolved.get("entity_bindings", {}),
                "join_plan": _state_resolved.get("join_plan", {}),
                "observer_requirements": _state_resolved.get("observer_requirements", []),
                "scope_fields": _state_resolved.get("scope_fields", []),
                "expression_type": _state_resolved.get("expression_type", ""),
                "root_entity": _state_resolved.get("root_entity", ""),
                "related_entities": _state_resolved.get("related_entities", []),
            }
            return {
                "status": "COMPILED",
                "control_plan": [],
                "treatment_plan": [{
                    "step_id": "treatment_1",
                    "actor_ref": treatment_actor_ref,
                    "operation_ref": operation_ref,
                    "intent": "cross_entity_state_treatment",
                    "protocol_step": "treatment",
                }],
                "observers": [
                    {"observer_id": "before_state"},
                    {"observer_id": "after_state"},
                    {"observer_id": "entity_state"},
                ],
                "assertion": _st_assertion,
            }
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref or treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "state_transition_control",
                "protocol_step": "positive_control",
            }],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": treatment_actor_ref,
                "operation_ref": operation_ref,
                "intent": "state_transition_treatment",
                "protocol_step": "treatment",
            }],
            "observers": [
                {"observer_id": "before_state"},
                {"observer_id": "after_state"},
            ],
            "assertion": {
                "kind": (
                    "forbidden_state_transition"
                    if _text(_expr.get("kind")) == "forbidden_state_transition"
                    or _text(_expr.get("operator")).lower() == "must_not_transition"
                    else "state_transition"
                ),
                "from_state": _state_from,
                "to_state": _state_to,
                "operator": _text(_expr.get("operator")) or "must_transition",
                "operands": _list(_expr.get("operands")),
                "invariant_ref": _text(property_spec.get("invariant_ref")),
                "rule_id": _text(property_spec.get("invariant_ref")),
            },
        }

    if family == "conservation":
        # Dead-path safeguard: the live conservation branch returns earlier.
        # Keep fail-closed here so reordering cannot reintroduce NL guessing.
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_EMPTY_CONSERVATION_TERMS",
            "detail": "conservation_requires_non_empty_equation_terms",
        }

    write_body: dict[str, Any] = {}
    if (
        family in {"authorization", "isolation", "visibility"}
        and method in {"POST", "PUT", "PATCH"}
    ):
        write_body = source_request_example(operation, sibling_operations=sibling_operations)
        if not write_body and not property_spec.get("defer_write_body_to_runtime"):
            write_body = _minimal_body_from_schema(operation)

    # ── Identity-addressed path reads (isolation/visibility) ──
    # A resource-targeted owned read (profile/{id} 权限：本人或管理员) has no
    # collection query param and usually no create fixture for the owned
    # entity, so the fixture-backed owned_resource proof cannot materialize
    # and the obligation would block without ever testing the boundary. When
    # the runtime catalogue carries two account-bound actors with observed
    # identities, compile the two-arm read directly (owner reads own
    # identity-addressed resource, viewer reads the owner's resource).
    if (
        family in {"isolation", "visibility"}
        and method in {"GET", "HEAD"}
        and _dict(property_spec).get("require_ownership_evidence") is True
    ):
        _identity_read = _identity_addressed_read_isolation_protocol(
            operation=operation,
            operation_ref=operation_ref,
            control_actor_ref=control_actor_ref,
            treatment_actor_ref=treatment_actor_ref,
            property_spec=property_spec,
            behavior_ir=behavior_ir,
        )
        if _identity_read is not None:
            return _identity_read

    control_plan: list[dict[str, Any]] = []
    if needs_control:
        control_step = {
            "step_id": "control_1",
            "actor_ref": control_actor_ref,
            "operation_ref": operation_ref,
            "intent": "authorized_control",
            "protocol_step": "positive_control",
        }
        if write_body:
            control_step["body"] = deepcopy(write_body)
        control_plan.append(control_step)
    treatment_step = {
        "step_id": "treatment_1",
        "actor_ref": treatment_actor_ref,
        "operation_ref": operation_ref,
        "intent": "treatment",
        "protocol_step": "treatment",
        "property_template": _text(property_spec.get("template")),
    }
    if write_body:
        treatment_step["body"] = deepcopy(write_body)
    ownership_param = _text(property_spec.get("ownership_param"))
    ownership_location = _text(property_spec.get("ownership_param_location")).lower()
    identity_target = _text(property_spec.get("identity_binding_target")) or "user_id"
    if family == "isolation" and ownership_param and identity_target:
        placeholder = "{" + identity_target + "}"
        if ownership_location == "query":
            treatment_step["query"] = {ownership_param: placeholder}
        elif ownership_location == "path":
            treatment_step["path_params"] = {ownership_param: placeholder}
        elif ownership_location == "header":
            treatment_step["headers"] = {ownership_param: placeholder}
        elif ownership_location == "body":
            body = dict(_dict(treatment_step.get("body")))
            # Nested ownership binders use dotted paths from schema walk.
            if "." in ownership_param:
                tokens = [part for part in ownership_param.split(".") if part]
                cursor: Any = body
                for token in tokens[:-1]:
                    nested = cursor.get(token)
                    if not isinstance(nested, dict):
                        nested = {}
                        cursor[token] = nested
                    cursor = nested
                if tokens:
                    cursor[tokens[-1]] = placeholder
            else:
                body[ownership_param] = placeholder
            treatment_step["body"] = body
        else:
            body = dict(_dict(treatment_step.get("body")))
            body[ownership_param] = placeholder
            treatment_step["body"] = body
        # ── Write-side ownership identity concretization ──
        # The arm bodies carry the runtime-observed actor identities when
        # available (account_id from login tokens): control = owner's own
        # identity on every ownership param, treatment = owner's identity on
        # the ownership binder (the viewer attempts to touch the owner's
        # resource) and the viewer's own identity on the remaining ownership
        # params. Without observed identities the placeholder template stays.
        _concrete = _concrete_ownership_body_values(
            operation=operation,
            control_actor_ref=control_actor_ref,
            treatment_actor_ref=treatment_actor_ref,
            behavior_ir=behavior_ir,
            ownership_param=ownership_param,
            control_body=(
                _dict(control_step.get("body")) if control_plan else {}
            ),
            treatment_body=_dict(treatment_step.get("body")),
        )
        if _concrete:
            if control_plan:
                control_step["body"] = _concrete["control"]
            treatment_step["body"] = _concrete["treatment"]
    return {
        "status": "COMPILED",
        "control_plan": control_plan,
        "treatment_plan": [treatment_step],
    }
