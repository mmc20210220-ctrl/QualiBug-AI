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
    install_query_safety_injection_probe,
    compile_query_safety_injection_probe as _compile_query_safety_injection_probe,
    is_ownership_key as _is_ownership_key_read_side,
)

install_owned_read_scope_protocol()
install_query_safety_injection_probe()

# Read-only audit protocol (additive): registers the readonly_numeric_audit /
# readonly_uniqueness_audit assertion kinds and the (validation,
# readonly_audit_validation) protocol once per process. The validation
# branch below reuses its numeric projection for GET/HEAD rules that carry a
# source-declared numeric boundary but no write-side mutation material —
# the same observer/assertion chain, never a duplicated implementation.
from .readonly_audit_protocol import (
    install_readonly_audit_protocol,
    _numeric_boundary_from_expression as _audit_numeric_boundary_from_expression,
    _evaluate_readonly_numeric_audit as _readonly_numeric_audit_evaluator,
)

install_readonly_audit_protocol()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# Read-only UI plan actions the ui_state_consistency protocol may compile from
# surface-declared contracts. Interactive actions (click/fill/select/press) are
# excluded: the UI read-only guard blocks any interaction step without cleanup
# equivalence, and the governed interactive UI adapter owns that path.
_READ_ONLY_UI_ACTIONS = frozenset({
    "goto",
    "wait_for_load",
    "screenshot",
    "expect_text",
    "expect_url",
    "expect_visible",
    "expect_hidden",
    "expect_enabled",
    "expect_disabled",
    "expect_value",
    "expect_checked",
    "expect_unchecked",
    "expect_count",
    "expect_attribute",
    "expect_css",
    "expect_role",
    "expect_accessible_name",
    "expect_dimensions",
    "expect_in_viewport",
    "expect_not_obscured",
    "expect_no_horizontal_overflow",
    "expect_no_console_errors",
    "expect_no_failed_requests",
})


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
            # A required array detail field (batch interfaces: items/products)
            # must carry at least one row built from the declared item schema.
            # An empty array trips the required-field gate
            # (``missing_required_body_fields:<field>``) and blocks the whole
            # experiment before the batch rule under test is observed.
            body[name] = _minimal_array_rows(_dict(prop.get("items")))
        else:
            body[name] = {}
    return body


def _operation_has_required_body(operation: dict[str, Any]) -> bool:
    """True when the operation's contract declares a mandatory JSON body.

    ``requestBody.required`` may be declared at the top level, at the media
    level, or as a boolean ``schema.required``. Presence-validated payloads
    (``{"type": "object"}`` with no ``properties``) are exactly the case that
    must be carried as an explicit body — a bodyless POST is rejected with
    422 ``Field required`` and gets misread as an authorization defect.
    """
    request_schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    if not request_schema:
        return False
    if request_schema.get("required") is True:
        return True
    content = request_schema.get("content")
    if isinstance(content, dict):
        for media in content.values():
            if not isinstance(media, dict):
                continue
            if media.get("required") is True:
                return True
            if _dict(media.get("schema")).get("required") is True:
                return True
    return False


def _minimal_array_rows(items_schema: dict[str, Any], *, depth: int = 0) -> list[dict[str, Any]]:
    """Build one structurally-valid detail row from an array item schema.

    Recursively populates the item's declared properties with type-appropriate
    defaults (or declared examples) so a batch request body (items/products
    arrays) carries a real detail row instead of an empty array. Schema-driven
    and industry-neutral: no field name is hardcoded — only the item schema's
    own properties are walked. An item schema without any property declaration
    yields one empty object row, which still satisfies the required-array gate
    and lets the target's own validation produce the observation.
    """
    if depth > 6 or not isinstance(items_schema, dict):
        return [{}]
    props = _dict(items_schema.get("properties"))
    if not props:
        return [{}]
    row: dict[str, Any] = {}
    for field_name, field_schema in props.items():
        if not isinstance(field_schema, dict):
            continue
        field_type = _text(field_schema.get("type") or "string")
        example = field_schema.get("example")
        if example is not None:
            row[field_name] = example
        elif field_type == "string":
            row[field_name] = "test_value"
        elif field_type in ("integer", "number"):
            row[field_name] = 1
        elif field_type == "boolean":
            row[field_name] = True
        elif field_type == "array":
            row[field_name] = _minimal_array_rows(
                _dict(field_schema.get("items")), depth=depth + 1
            )
        else:
            row[field_name] = {}
    return [row]


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
            # Batch detail arrays (items/products) must carry one structurally
            # valid row or the required-field gate blocks the whole experiment.
            body[field_name] = _minimal_array_rows(_dict(field_schema.get("items")))
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
    r"percent|pct|ratio|markup|"
    r"价格|金额|余额|库存|数量|限额|配额|费用|单价|总价|退款|优惠|"
    r"百分比|比率|调价|折扣率|上浮|下调)",
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
        # ── Numeric verification-code field (integer codes) ──
        # Same verification-must-verify semantics as the string branch below:
        # a numeric verification-value field (code/otp) on a verification
        # surface must reject a code that was never issued.
        if (
            _verification_value_field_hit(field_name, combined)
            and _verification_login_context_hit(combined)
        ):
            return 999999, "semantic:verification_code_mismatch"
        # Check for maximum constraint in schema
        maximum = property_schema.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            return maximum + 1, "semantic:exceeds_maximum"
        # Generic numeric boundary: zero for quantities
        if re.search(r"(quantity|qty|count|num|stock|数量|库存)", combined, re.IGNORECASE):
            return 0, "semantic:zero_quantity"

    # String fields with semantic constraints
    if declared_type == "string":
        # ── Query-safety injection probe ──
        # A rule declaring query-safety vocabulary (关键词必须参数化查询 /
        # 不得拼接 SQL / parameterized / injection) demands a probe that
        # escapes the string literal when the backend concatenates raw input
        # into a query — a generic OWASP-style probe, industry-neutral, never
        # benchmark data. Gated on the rule's own vocabulary so ordinary
        # string fields are never mutated into probes.
        if any(
            token in combined
            for token in (
                "参数化", "拼接", "注入", "sql", "parameterized",
                "injection", "concatenat",
            )
        ):
            return "' OR '1'='1", "semantic:sql_injection_probe"
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
        # ── Verification-code login must actually verify the code ──
        # A verification-value field (code/otp/验证码…) on a surface whose own
        # contract names a verification mechanism (验证码登录 / otp login) is
        # the credential the login claims to check. The property under test is
        # that the code is REALLY verified server-side: a deterministic wrong
        # code that was never issued must be rejected. Accepting it is the
        # any-code-login weakness — industry-universal verification vocabulary,
        # never an industry term. Runs before generic minLength/pattern
        # checks: a length-gated code field still has to reject a wrong code,
        # which is exactly the defect class (length-only verification).
        if (
            _verification_value_field_hit(field_name, combined)
            and _verification_login_context_hit(combined)
        ):
            return "000000", "semantic:verification_code_mismatch"
        # Check for minLength constraint
        min_length = property_schema.get("minLength")
        if isinstance(min_length, int) and min_length > 1:
            return "x", "semantic:below_min_length"
        # Check for pattern constraint
        if property_schema.get("pattern"):
            return "!!!invalid!!!", "semantic:pattern_violation"

    return None


def _verification_value_field_hit(field_name: str, combined: str) -> bool:
    """Whether the FIELD itself is a verification-value field.

    The field name is the primary signal (code/otp/验证码/…). The combined
    corpus (field name + contract text) is only consulted for CJK-named
    fields, where the field name appears verbatim in the corpus — a
    ``phone`` field on a verification surface must never be treated as the
    verification value just because the contract text mentions 验证码.
    """
    name_lower = _text(field_name).lower()
    if re.search(
        r"(?:^|[^a-z0-9])"
        r"(?:code|otp|captcha|verifycode|verificationcode|smscode)"
        r"(?:[^a-z0-9]|$)",
        name_lower,
    ):
        return True
    return any(
        token in name_lower
        for token in ("验证码", "校验码", "短信码")
    )


def _verification_login_context_hit(combined: str) -> bool:
    """Whether the combined corpus names a verification-code login context.

    The mechanism vocabulary (验证码/otp/verification code/sms code) is the
    precise gate: a coupon redemption-code operation or an HTTP status-code
    rule never declares a verification-code login. The identity-exchange
    context (登录/login/登入) is required for English-only contracts where the
    mechanism is implied by the login surface itself.
    """
    if any(
        token in combined
        for token in (
            "验证码", "校验码", "短信码", "otp",
            "verification code", "sms code",
        )
    ):
        return True
    return any(
        token in combined
        for token in ("登录", "登入", "login", "sign in", "signin")
    )


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


# Generic public-state literals: a state literal is public by its OWN English
# meaning (ON_SALE/ACTIVE/ENABLED/PUBLISHED/…), matching the runtime
# entity-state classification in the step executor. Literal semantics, never
# a translation table — no industry vocabulary enters the product.
_READ_SIDE_PUBLIC_STATE_LITERALS = frozenset({
    "on_sale", "active", "enabled", "published", "available", "open",
    "normal", "listed", "in_stock", "activated",
})


def _read_side_allowed_states(
    property_spec: dict[str, Any],
    operation: dict[str, Any],
    behavior_ir: dict[str, Any] | None = None,
) -> set[str]:
    """Extract the ONLY row states a read surface may return.

    Primary source: the operation's own declaration naming the allowed state
    set (业务约束：用户端默认仅返回 ON_SALE 商品). The allowed states are
    generic enum literals in that declaration — never inferred from field
    names or response samples.

    Fallback (no declaration): when the rule is an entity-state exposure rule
    (用户端不展示下架商品、草稿商品、内部商品) on a PUBLIC surface (no declared
    roles) and the rule's subject entity carries a state enum in the IR model
    (schema CHECK / OpenAPI enum), the allowed rows are the enum literals
    whose own meaning is public. Restricted surfaces (卖家目录 — 商家本人或
    管理员) stay excluded: their legitimate rows include non-public states the
    owner may see, and a state filter would fabricate false positives.
    Rules without the declaration keep their visible BLOCKED below (no
    vacuous observation).
    """
    material = " ".join(
        [
            _text(_dict(property_spec.get("expression")).get("raw")),
            _text(operation.get("description")),
        ]
    )
    allowed: set[str] = set()
    for _match in re.finditer(
        r"(?:仅|只|默认|只会)[^。；，\n]{0,30}?"
        r"(?:返回|展示|显示|呈现)[^。；，\n]{0,20}?"
        r"\b([A-Z][A-Z0-9_]{2,})\b",
        material,
    ):
        allowed.add(_match.group(1))
    if allowed:
        return allowed
    # ── Fallback: exposure rule + declared entity state enum ──
    if not any(w in material for w in _ENTITY_STATE_VIOLATION_WORDS):
        return allowed
    if not (
        any(v in material for v in _ENTITY_STATE_EXPOSURE_VERBS)
        or "出现" in material
    ):
        return allowed
    if _list(operation.get("required_roles")):
        # Restricted surface — its rows legitimately include non-public
        # states (owner-facing catalogs, admin lists). No state filter.
        return allowed
    if not isinstance(behavior_ir, dict):
        return allowed
    subject_names = {
        _text(value).lower()
        for value in _list(_dict(property_spec).get("subject_entity_refs"))
        if _text(value)
    }
    if not subject_names:
        return allowed
    enum_values: set[str] = set()
    for entity in _list(behavior_ir.get("entities")):
        if not isinstance(entity, dict):
            continue
        if _text(entity.get("name")).lower() not in subject_names:
            continue
        for field in _list(entity.get("fields")):
            if not isinstance(field, dict):
                continue
            if _text(field.get("semantic_type")).upper() != "STATE":
                continue
            for value in _list(field.get("enum_values")):
                if _text(value):
                    enum_values.add(_text(value))
    if not enum_values:
        return allowed
    return {
        value
        for value in enum_values
        if value.lower() in _READ_SIDE_PUBLIC_STATE_LITERALS
    }


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


# Path-parameter name vocabulary: an identity-scoped read (GET a detail
# resource, e.g. a product by sku or a coupon by code) addresses its row
# through a path placeholder. Generic relational identity names — never
# industry terms.
_PATH_IDENTITY_PARAM_RE = re.compile(r"(?:sku|code|key|no|ref|id)$", re.I)


def _read_side_path_identity_exposure(
    *,
    operation: dict[str, Any],
    operation_ref: str,
    property_spec: dict[str, Any],
    control_actor_ref: str,
    treatment_actor_ref: str,
    behavior_ir: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compile a two-arm read for an entity-state exposure rule on a detail read.

    A rule like 用户端不展示下架商品、草稿商品、内部商品 constrains which entity
    rows a read surface may return. On a LIST read the existing row-state filter
    arm suffices; on an IDENTITY-SCOPED read (GET /api/products/{sku}) the
    rule must be proven on the DETAIL response. Historically every such
    obligation died below as read_side_rule_lacks_decidable_assertion because
    the validation protocol only mutates request bodies — a GET has none.

    The treatment references an entity the environment really has in a
    non-public state, resolved at runtime through the entity's collection read
    (the same resolver the write-side arm uses); the mutation descriptor names
    the PATH parameter the executor substitutes the violating identity into.
    The assertion expects the rejection (4xx): a detail surface that answers a
    non-public entity is the violation. Returns a COMPILED protocol dict or
    None when the rule is not an entity-state exposure rule on an identity-
    scoped read or the structured material (subject entity, collection read,
    path identity param) is missing.
    """
    method = _text(operation.get("method")).upper()
    if method not in {"GET", "HEAD"}:
        return None
    raw_path = _text(operation.get("path") or operation.get("raw_path"))
    if "{" not in raw_path and ":" not in raw_path:
        return None
    expression = _dict(property_spec.get("expression"))
    semantic_text = "\n".join(
        _text(value)
        for value in (
            expression.get("raw"),
            property_spec.get("source_intent"),
            property_spec.get("description"),
        )
        if _text(value)
    )
    # Same vocabulary gate as the write-side exposure arm: exposure verbs
    # (不展示/不提供/不可见/不开放/不得展示/禁止展示/不显示) or consumption
    # markers (必须为/必须在/在有效期/有效期内/只能用于/不能使用/不可用/
    # 不能超过/次数/状态/ACTIVE/ENABLED) combined with a violation word
    # (下架/草稿/停用/过期/禁用/删除/内部/归档).
    _exposure_triggered = any(
        v in semantic_text for v in _ENTITY_STATE_EXPOSURE_VERBS
    )
    if not _exposure_triggered:
        if not any(m in semantic_text for m in _ENTITY_STATE_CONSUMPTION_MARKERS):
            return None
        if not any(w in semantic_text for w in _ENTITY_STATE_VIOLATION_WORDS):
            if not any(t in semantic_text for t in ("有效", "状态", "ACTIVE", "次数", "停用", "禁用", "过期")):
                return None
    elif not any(w in semantic_text for w in _ENTITY_STATE_VIOLATION_WORDS):
        return None
    if not isinstance(behavior_ir, dict):
        return None
    # The path identity parameter: the placeholder whose name is the entity
    # identity slot (sku/code/key/no/ref/id).
    path_param = ""
    for _param in _list(operation.get("parameters")):
        if not isinstance(_param, dict):
            continue
        if _text(_param.get("in") or _param.get("location")).lower() != "path":
            continue
        _name = _text(_param.get("name"))
        if _PATH_IDENTITY_PARAM_RE.search(_name):
            path_param = _name
            break
    if not path_param:
        for _segment in re.findall(r"\{([A-Za-z_]\w*)\}", raw_path):
            if _PATH_IDENTITY_PARAM_RE.search(_segment):
                path_param = _segment
                break
    if not path_param:
        return None
    subject_entities = [
        _text(value).lower()
        for value in _list(property_spec.get("subject_entity_refs"))
        if _text(value)
    ]
    if not subject_entities:
        _identity_tokens = [
            _tok.lower()
            for _tok in re.findall(r"[A-Za-z0-9]+", path_param)
            if _tok.lower() not in {"id", "ids", "no", "key", "code", "ref", "sku"}
        ]
        subject_entities = _identity_tokens
    # The entity collection read: a placeholder-free GET whose path names the
    # subject entity — the same resolver the write-side exposure arm uses.
    resolver = None
    for op in _list(behavior_ir.get("operations")):
        if not isinstance(op, dict):
            continue
        if _text(op.get("method")).upper() not in {"GET", "HEAD"}:
            continue
        _rp = _text(op.get("path") or op.get("raw_path"))
        if re.search(r"(?:^|/)(?:health)(?:/|$)", _rp.lower()):
            continue
        if "{" in _rp:
            continue
        _segments = [
            seg
            for seg in _rp.lower().strip("/").split("/")
            if seg and seg not in {"api", "health", "v1"}
        ]
        if subject_entities and not any(
            _seg.startswith(_obj) or _obj.startswith(_seg)
            for _seg in _segments
            for _obj in subject_entities
        ):
            continue
        resolver = {
            "operation_ref": _text(op.get("id")),
            "method": "GET",
            "path": _rp,
        }
        break
    if not resolver:
        return None
    mutation = {
        "class": "runtime_entity_state_violation",
        "path_param": path_param,
        "json_path": f"path:{path_param}",
        "resolver_operations": [resolver],
        "identity_field": path_param,
        "status_field": "status",
        "violation_mode": _entity_state_violation_mode(semantic_text),
    }
    return {
        "status": "COMPILED",
        "control_plan": [{
            "step_id": "control_1",
            "actor_ref": control_actor_ref,
            "operation_ref": operation_ref,
            "intent": "read_side_entity_state_control",
            "protocol_step": "positive_control",
            "property_template": _text(property_spec.get("template")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": treatment_actor_ref,
            "operation_ref": operation_ref,
            "intent": "read_side_entity_state_exposure",
            "protocol_step": "single_observation",
            "mutation": mutation,
            "property_template": _text(property_spec.get("template")),
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }],
        "observers": [{"observer_id": "http_response"}],
        "assertion": {
            "kind": "http_status_class",
            "expected_class": 4,
            "compare_field": "status_code",
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
# body (SKU / code / key / no / ref / id — generic relational identifiers,
# never industry vocabulary).
_ENTITY_IDENTITY_FIELD_RE = re.compile(r"(?:sku|code|key|no|ref|id)$", re.I)

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
_ENTITY_STATE_EXPOSURE_VERBS = (
    "不展示", "不提供", "不可见", "不开放", "不得展示", "禁止展示", "不显示",
    "不得出现", "不出现", "不能出现", "禁止出现", "不允许出现",
)
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
# status is still public; status rules need a row whose status is non-public;
# usage rules (次数/限额/超限) need a row whose declared usage has reached
# its limit.
_ENTITY_STATE_EXPIRY_MARKERS = ("有效期", "过期", "失效", "生效", "到期", "有效期内")
_ENTITY_STATE_STATUS_MARKERS = ("状态", "ACTIVE", "ENABLED", "停用", "禁用")
_ENTITY_STATE_USAGE_MARKERS = ("次数", "限额", "超限", "限用", "只能使用", "使用一次")

# Entity-state precondition vocabulary: a rule like 已取消订单不能支付 /
# 已支付订单不能直接取消 names a subject in a non-public state (取消/退款/
# 完成/关闭/…) and forbids an operation on it (不能/不得/禁止). The treatment
# references an entity the environment really has in such a state (resolved
# from its list read, same as the exposure arm) and the write protocol
# asserts the 4xx rejection. State words are generic system lifecycle
# vocabulary, never industry terms.
_ENTITY_STATE_PRECONDITION_STATE_WORDS = (
    "取消", "退款", "完成", "关闭", "发货", "收货", "支付", "过期",
    "删除", "下架", "停用", "禁用", "草稿", "归档", "冻结",
)
_ENTITY_STATE_PRECONDITION_BANS = ("不能", "不得", "禁止", "不可", "不允许")


def _read_side_numeric_boundary_projection(
    *,
    property_spec: dict[str, Any],
    operation_ref: str,
    actor_ref: str,
) -> dict[str, Any] | None:
    """Compile a read-only numeric boundary audit for a GET/HEAD validation rule.

    A source rule carrying a numeric boundary (inventory available_qty must
    not go below zero, cart qty must stay positive) on a read-side binding
    has no request body to mutate; historically it died as
    ``read_side_rule_lacks_decidable_assertion`` — a structural break: the
    boundary is decidable on the response the bound read itself returns. This
    projection reuses the read-only numeric audit chain (assertion kind and
    ``http_response`` observer, both registered by
    ``readonly_audit_protocol.install_readonly_audit_protocol``): one
    observed row below the boundary is violation evidence.

    Returns a COMPILED protocol dict, or None when the rule is not a
    numeric-boundary rule or the declared field/boundary operator is missing
    (the rule then stays a visible BLOCKED — no vacuous observation).
    """
    if not _text(operation_ref) or not _text(actor_ref):
        return None
    expression = _dict(property_spec.get("expression"))
    qualifier, column, boundary_operator = _audit_numeric_boundary_from_expression(
        expression
    )
    if not column or boundary_operator not in {"non_negative", "positive"}:
        return None
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "readonly_numeric_audit",
            "protocol_step": "readonly_audit_read",
            "property_template": _text(property_spec.get("template")),
        }],
        "observers": [
            {"observer_id": "http_response"},
        ],
        "assertion": {
            "kind": "readonly_numeric_audit",
            "field": column,
            "field_qualifier": qualifier,
            "operator": boundary_operator,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        },
    }


def _entity_state_precondition_triggered(semantic_text: str) -> bool:
    """True when the rule names a subject state and forbids an operation.

    已取消订单不能支付、发货、确认收货 → subject state word (取消) + ban
    (不能) + subject marker (已). A ban alone on an amount/limit constraint
    (折扣金额不能超过 X) carries no subject state — it stays in the
    amount-boundary arm, not here.
    """
    if not any(ban in semantic_text for ban in _ENTITY_STATE_PRECONDITION_BANS):
        return False
    if not any(
        word in semantic_text for word in _ENTITY_STATE_PRECONDITION_STATE_WORDS
    ):
        return False
    return any(marker in semantic_text for marker in ("已", "后", "状态"))


def _state_prepare_operation(
    semantic_text: str,
    behavior_ir: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve the state-change operation that moves an entity INTO the
    rule's forbidden state (已取消订单 → the cancel operation).

    The state word (取消) matches the operation's own declared vocabulary —
    its summary/description (取消订单) — never an inferred English enum.
    The runtime then executes the state change on a real entity, reads the
    entity back, and anchors the forbidden state value from the target's own
    response. Returns None when no operation declares the state word.
    """
    if not isinstance(behavior_ir, dict):
        return None
    _state_word = next(
        (word for word in _ENTITY_STATE_PRECONDITION_STATE_WORDS if word in semantic_text),
        "",
    )
    if not _state_word:
        return None
    for _op in _list(behavior_ir.get("operations")):
        if not isinstance(_op, dict):
            continue
        if _text(_op.get("method")).upper() not in {"POST", "PUT", "PATCH"}:
            continue
        _surface = " ".join(
            [
                _text(_op.get("path") or _op.get("raw_path")),
                _text(_op.get("summary") or _op.get("title")),
                _text(_op.get("description")),
            ]
        )
        if _state_word not in _surface:
            continue
        _identity_param = next(
            (
                _text(_param.get("name"))
                for _param in _list(_op.get("parameters"))
                if isinstance(_param, dict)
                and _text(_param.get("in") or _param.get("location")).lower()
                in {"path", "query"}
                and _text(_param.get("name"))
            ),
            "",
        )
        return {
            "operation_ref": _text(_op.get("id")),
            "method": "POST",
            "path": _text(_op.get("path") or _op.get("raw_path")),
            "identity_param": _identity_param,
        }
    return None


def _entity_state_violation_mode(semantic_text: str) -> str:
    """Derive the runtime violation dimension from the rule's own vocabulary."""
    if any(marker in semantic_text for marker in _ENTITY_STATE_EXPIRY_MARKERS):
        return "expiry"
    if any(marker in semantic_text for marker in _ENTITY_STATE_STATUS_MARKERS):
        return "status"
    if any(marker in semantic_text for marker in _ENTITY_STATE_USAGE_MARKERS):
        return "usage"
    return "any"


def _non_public_entity_treatment(
    control: dict[str, Any],
    semantic_text: str,
    behavior_ir: dict[str, Any],
    property_spec: dict[str, Any] | None = None,
    *,
    force: bool = False,
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

    ``force=True`` skips the exposure/consumption vocabulary gate: a
    state-precondition rule (已取消订单不能支付) constrains a WRITE on an
    entity the environment has in a non-public state — the same runtime
    resolver selects that entity, and the write protocol asserts the 4xx
    rejection instead of a response-content filter.
    """
    if not force:
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
    if not subject_entities:
        # Fall back to the identity field's own entity name (orderId → order):
        # a list GET whose path names that entity still carries the status
        # field the runtime resolver needs. Generic identity naming (entity
        # name + id suffix), never an industry term; ownership prefixes
        # (user/owner) are excluded so an owner-key never aims the resolver
        # at the account collection.
        _identity_tokens = [
            _tok.lower()
            for _tok in re.findall(r"[A-Z]?[a-z0-9]+", identity_field)
            if _tok
            and _tok.lower()
            not in {
                "id", "ids", "no", "key", "code", "ref", "sku",
                "user", "owner", "account", "actor",
            }
        ]
        subject_entities = _identity_tokens
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
        # The entity list read carries no path parameter (collection shape);
        # an identity-scoped read (…/order/{orderId}) cannot enumerate rows.
        if "{" in _text(op.get("path") or op.get("raw_path")):
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
    _violation_mode = _entity_state_violation_mode(semantic_text)
    mutation = {
        "class": "runtime_entity_state_violation",
        "json_path": identity_path or f"$.{identity_field}",
        "resolver_operations": [resolver],
        "identity_field": identity_field,
        "status_field": "status",
        # The runtime resolver picks the violating row by the rule's own
        # dimension: date-expiry rules need a row whose validity date has
        # passed (status may still be public); status rules need a row whose
        # status is non-public; usage rules need a row whose declared usage
        # reached its limit; anything else takes either.
        "violation_mode": _violation_mode,
    }
    if _violation_mode == "usage":
        # The quota fields the rule itself constrains (user_limit/
        # global_limit — operand fields carrying limit/usage vocabulary).
        # The runtime reads the row's limit from exactly these declared
        # fields and its usage from the remaining numeric fields, so a
        # user_limit value can never be mistaken for a used count.
        _usage_limit_fields: list[str] = []
        for _operand in _list(_dict(property_spec).get("expression", {}).get("operands")):
            _ofield = _text(_dict(_operand).get("field"))
            if _ofield and re.search(
                r"(?:limit|count|quota|usage|uses|次数|限额|上限)",
                _ofield,
                re.IGNORECASE,
            ):
                _usage_limit_fields.append(_ofield)
        if _usage_limit_fields:
            mutation["usage_limit_fields"] = _usage_limit_fields
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
        # The entity list read carries no path parameter (collection shape);
        # an identity-scoped read (…/order/{orderId}) cannot enumerate rows.
        if "{" in _text(op.get("path") or op.get("raw_path")):
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


def _scope_violation_treatment(
    control: dict[str, Any],
    semantic_text: str,
    behavior_ir: dict[str, Any],
    property_spec: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Build the rejection-arm body for an object-scope rule.

    类目券只能用于指定类目 constrains which category an entity may be applied
    to. The treatment uses an entity row the environment ACTUALLY has with a
    declared scope (category_scope non-null) and sets the request's line-item
    category to a DIFFERENT scope value observed in the same collection —
    all values are the environment's own observed data, never synthesized.
    The runtime resolver picks the scoped row and the distinct scope from the
    entity's own list read.
    """
    _SCOPE_MARKERS = ("类目", "分类", "范围", "只能用于", "仅限")
    if not any(m in semantic_text for m in _SCOPE_MARKERS):
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
    # The line-item array carrying the category input: items[].category /
    # lines[].type — a generic detail-array field, never an industry term.
    items_path = ""
    category_field = ""
    for _body_key, _body_value in control.items():
        if not (
            isinstance(_body_value, list)
            and _body_value
            and isinstance(_body_value[0], dict)
        ):
            continue
        _cat_field = next(
            (
                key
                for key in _body_value[0]
                if re.search(
                    r"(?:categor|class|type|scope|分类|类目)",
                    str(key),
                    re.IGNORECASE,
                )
            ),
            "",
        )
        if _cat_field:
            items_path = f"$.{_body_key}[0].{_cat_field}"
            category_field = _cat_field
            break
    if not items_path:
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
        if "{" in _text(op.get("path") or op.get("raw_path")):
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
        "class": "runtime_scope_violation",
        "json_path": items_path,
        "resolver_operations": [resolver],
        "identity_field": identity_field,
        "category_field": category_field,
        "scope_field": "category_scope",
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
        # ── Entity-state precondition arm ──
        # 已取消订单不能支付、发货、确认收货 names a subject in a non-public
        # state and forbids an operation on it: the write must be REJECTED
        # (4xx). The treatment references an entity the environment really
        # has in such a state (resolved at runtime from its list read, the
        # same resolver as the isolation arm); the validation protocol's
        # default rejection assertion judges the write.
        if _entity_state_precondition_triggered(_semantic_text_pre):
            _precondition_treatment = _non_public_entity_treatment(
                control, _semantic_text_pre, _dict(behavior_ir), property_spec,
                force=True,
            )
            if _precondition_treatment:
                treatment, mutation = _precondition_treatment
                # Anchor the forbidden state at runtime: the state-change
                # operation (取消订单) moves a real entity INTO the state the
                # rule forbids operations on (已取消订单不能支付). The executor
                # executes it, reads the entity back, and uses that entity as
                # the treatment input — never an arbitrary non-public row.
                _state_prepare = _state_prepare_operation(
                    _semantic_text_pre, _dict(behavior_ir)
                )
                if _state_prepare:
                    mutation = dict(mutation)
                    mutation["state_prepare"] = _state_prepare
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
        # ── Object-scope arm ──
        # 类目券只能用于指定类目 constrains the category an entity may be
        # applied to. The treatment's scoped entity + a distinct observed
        # scope value are resolved at runtime from the environment's own
        # collection read — never a compile-time guess.
        _scope_treatment = _scope_violation_treatment(
            control, _semantic_text_pre, _dict(behavior_ir), property_spec,
        )
        if _scope_treatment:
            treatment, mutation = _scope_treatment
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
        # No documented request material means there is no decidable mutation.
        # Fabricating a generic body or status value changes the source
        # contract and can manufacture validation findings, so fail closed at
        # the caller's existing missing-binding gate.
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
        if declared_type == "array":
            # ── Nested array-item descent ──
            # Batch-create / detail bodies (products: [{...}], items: [{...}])
            # carry the governed fields inside the array element schema; the
            # top-level heuristics cannot see them, so a negative stock/price
            # inside products[0] stayed untested and the strategy fell through
            # to "remove the required array" (a missing-array test, not an
            # abnormal-value test). Descend into the first element: the same
            # semantic invalid-value heuristics over the element's own
            # declared properties, honoring explicit rule targets first; the
            # mutation path addresses the first element ($.products[0].stock).
            _items_schema = _dict(property_schema.get("items"))
            _item_properties = _dict(_items_schema.get("properties"))
            _item_control = (
                control[field][0]
                if isinstance(control.get(field), list)
                and control[field]
                and isinstance(control[field][0], dict)
                else None
            )
            if _item_properties and _item_control:
                _item_explicit = [
                    str(f)
                    for f in _item_properties
                    if str(f) in normalized_direct
                    or re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(str(f))}(?![A-Za-z0-9_])",
                        semantic_text,
                    )
                ]
                _item_field_order = [
                    *_item_explicit,
                    *[
                        str(f)
                        for f in _item_properties
                        if str(f) not in _item_explicit
                    ],
                ]
                for _item_field in _item_field_order:
                    if _item_field not in _item_control:
                        continue
                    _item_raw = _dict(_item_properties.get(_item_field))
                    _item_type = _text(_item_raw.get("type")).lower()
                    if not _item_type:
                        _item_val = _item_control[_item_field]
                        if isinstance(_item_val, bool):
                            _item_type = "boolean"
                        elif isinstance(_item_val, int):
                            _item_type = "integer"
                        elif isinstance(_item_val, float):
                            _item_type = "number"
                        elif isinstance(_item_val, str):
                            _item_type = "string"
                        else:
                            continue
                    _item_result = _semantic_invalid_value(
                        _item_field, _item_type, _item_raw, semantic_text
                    )
                    if _item_result is not None:
                        _invalid_value, _constraint = _item_result
                        treatment = deepcopy(control)
                        treatment[field] = [
                            {**_item_control, _item_field: _invalid_value},
                            *list(control[field])[1:],
                        ]
                        return control, treatment, {
                            "json_path": f"$.{field}[0].{_item_field}",
                            "constraint": _constraint,
                            "source": "request_schema",
                        }
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
        body: dict[str, Any] | None = source_request_example(
            operation, sibling_operations=sibling_operations
        )
        if not body and method in {"POST", "PUT", "PATCH"}:
            body = _minimal_body_from_schema(operation)
        if not body:
            if _operation_has_required_body(operation):
                # Source-declared required body with no field-level schema: a
                # bodyless POST is rejected 422 Field required and misread as
                # a defect; {} exercises the real permitted invocation.
                body = {}
            else:
                body = None
        step: dict[str, Any] = {
            "step_id": "treatment_1",
            "actor_ref": actor,
            "operation_ref": operation_ref,
            "intent": "permitted_operation_invocation",
            "protocol_step": "permitted_invocation",
            "property_template": template,
        }
        if body is not None:
            step["body"] = deepcopy(body)
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [step],
            "assertion": {
                "kind": "http_status_class",
                "expected_class": 2,
                "compare_field": "status_code",
                "authorization_semantics": "permitted_invocation",
            },
        }
    # ── Credential-gated write guard ──
    # A write operation whose own contract demands verification-based
    # authentication (回调必须验签 / 必须完成验证码或等价身份校验) but is
    # reachable without any declared credential is tested by an ANONYMOUS
    # (no-credential) write: the target must reject the unverified request.
    # The treatment body carries the documented example; the identity-locator
    # field (email/username/…) is aimed at a real account from the runtime
    # catalogue (the "any account" shape of a password-reset surface), and a
    # callback surface's status field carries the success literal the state
    # machine accepts. Single-arm by construction — there is no authorized
    # baseline to compare; the rejection itself is the property.
    if template == "credential_gated_write":
        actor = treatment_actor_ref or control_actor_ref or _text(
            property_spec.get("actor_ref")
        )
        if not actor:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_ACTOR",
                "detail": "credential_gated_write_actor",
            }
        body = source_request_example(
            operation, sibling_operations=sibling_operations
        )
        if not body and method in {"POST", "PUT", "PATCH"}:
            body = _minimal_body_from_schema(operation)
        if not isinstance(body, dict):
            body = {}
        _locator_field = _text(property_spec.get("identity_locator_field"))
        if _locator_field and _locator_field not in body:
            # The schema may declare the locator while the example omits it;
            # keep the probe aimed at a real account.
            body[_locator_field] = "test_value"
        if _locator_field:
            # Aim the probe at a real account from the runtime catalogue — an
            # anonymous caller must not be able to touch ANY declared account,
            # so any account-bound actor is a valid target (never synthesized).
            for _actor in _list(_dict(behavior_ir).get("actors")):
                _ref = _text(_actor.get("account_ref") or _actor.get("account"))
                if (
                    _ref
                    and _text(_actor.get("role")).lower()
                    not in {"anonymous", "public"}
                ):
                    body[_locator_field] = _ref
                    break
        # Callback/webhook surfaces: the body's status/state field carries the
        # success literal the channel would accept — the forged-success shape.
        _surface = (
            f"{_text(operation.get('path') or operation.get('raw_path'))} "
            f"{_text(operation.get('summary'))}"
        ).casefold()
        if any(token in _surface for token in ("callback", "回调", "notify", "通知")):
            _status_field = next(
                (
                    key
                    for key in body
                    if re.search(r"(?:^|_)(?:status|state)$", str(key), re.I)
                ),
                "",
            )
            if _status_field:
                body[_status_field] = "SUCCESS"
        return {
            "status": "COMPILED",
            "control_plan": [],
            "treatment_plan": [{
                "step_id": "treatment_1",
                "actor_ref": actor,
                "operation_ref": operation_ref,
                "intent": "credential_gated_anonymous_write",
                "protocol_step": "treatment",
                "body": deepcopy(body),
                "property_template": template,
            }],
            "assertion": {
                "kind": "http_status_class",
                "expected_class": 4,
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
    if not treatment_actor_ref and family != "ui_state_consistency":
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
            "observers": [
                {"observer_id": "http_response"},
                {"observer_id": "business_effect"},
            ],
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
        # ── Same-experiment concurrent double-write ──
        # Control and treatment are the SAME write (same operation, same body)
        # released at the same moment by the barrier executor against the SAME
        # resource. The assertion boundary comes only from the rule's own
        # declaration:
        #   * a structured comparison the source declared (available_qty >= 0),
        #   * the IR-built non-negative field equation (库存不能为负 → non_negative
        #     + terms),
        #   * oversell-prohibition vocabulary (超卖/超额/oversell) with no declared
        #     field → the runtime readback projection of the resource.
        # Without any of these the pair still executes (dual 2xx alone is never a
        # verdict — ``insufficient_signal: dual_2xx_alone``) and the evaluator
        # stays INDETERMINATE with a named reason; a boundary is never invented.
        _expression = _dict(property_spec.get("expression"))
        _semantic_text = " ".join(
            _text(value)
            for value in (
                _expression.get("raw"),
                property_spec.get("source_intent"),
                property_spec.get("description"),
            )
            if _text(value)
        )
        _assertion_payload: dict[str, Any] = {
            "kind": "concurrent_double_write",
            "invariant_ref": _text(property_spec.get("invariant_ref")),
        }
        if _dict(_expression.get("structured_expression")):
            _assertion_payload["structured_expression"] = _dict(
                _expression.get("structured_expression")
            )
        if _dict(_expression.get("equation")):
            _assertion_payload["equation"] = _dict(_expression.get("equation"))
        if any(
            token in _semantic_text
            for token in (
                "超卖", "超额", "oversell", "over-sell", "over_sell", "overconsum",
            )
        ):
            _assertion_payload["oversell_projection"] = True
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
            "observers": [
                {"observer_id": "final_state"},
                {"observer_id": "barrier_timeline"},
            ],
            "assertion": _assertion_payload,
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
                # The boundary arm's resolver IS the entity status read
                # (GET /api/inventory/{sku}) — use it as the governance
                # before/after observation path so after_values materializes
                # and the non_negative assertion can evaluate. The identity
                # value comes from the compiled body (the example SKU).
                _obs_path = _text(
                    _dict(_list(_bound_mutation.get("resolver_operations"))[0]).get(
                        "path"
                    )
                )
                _body_identity = _text(_dict(body).get(_identity_field) or "")
                if _obs_path and _body_identity:
                    _treatment_step["observation_path"] = _obs_path.replace(
                        "{" + _identity_field + "}", _body_identity
                    )
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
        expression = _dict(property_spec.get("expression"))
        window_ms = expression.get("window_ms") or property_spec.get("window_ms")
        if _text(expression.get("temporal_semantics")) == "action_deadline":
            binding_fields = (
                "anchor_operation_ref",
                "completion_operation_ref",
                "completion_observer",
                "process_graph_ref",
                "wait_contract_ref",
            )
            if (
                any(not _text(expression.get(field)) for field in binding_fields)
                or _text(expression.get("anchor_grounding_status")) != "BOUND"
                or _text(expression.get("completion_grounding_status")) != "BOUND"
            ):
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": (
                        "temporal_action_deadline_requires_anchor_and_completion_binding"
                    ),
                }
            if operation_ref != _text(expression.get("completion_operation_ref")):
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": "temporal_completion_operation_identity_mismatch",
                }
            if (
                isinstance(window_ms, bool)
                or not isinstance(window_ms, (int, float))
                or int(window_ms) != window_ms
                or int(window_ms) <= 0
            ):
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_ASSERTION",
                    "detail": "temporal_window_ms_missing_or_invalid",
                }
            graph_ref = _text(expression.get("process_graph_ref"))
            graph_candidates = [
                row
                for row in _list(_dict(behavior_ir).get("process_graphs"))
                if isinstance(row, dict)
                and _text(row.get("status")) == "COMPILED"
                and _text(
                    row.get("execution_graph_id") or row.get("process_id")
                ) == graph_ref
            ]
            if len(graph_candidates) != 1:
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": "temporal_process_graph_not_uniquely_resolved",
                }
            raw_graph = _dict(graph_candidates[0])
            wait_ref = _text(expression.get("wait_contract_ref"))
            wait_candidates = [
                row
                for row in _list(raw_graph.get("wait_contracts"))
                if isinstance(row, dict)
                and _text(row.get("wait_id") or row.get("contract_id")) == wait_ref
            ]
            if len(wait_candidates) != 1:
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": "temporal_wait_contract_not_uniquely_resolved",
                }
            raw_wait = _dict(wait_candidates[0])
            nodes = {
                _text(row.get("node_id")): row
                for row in _list(raw_graph.get("nodes"))
                if isinstance(row, dict) and _text(row.get("node_id"))
            }
            source_node = _dict(nodes.get(_text(raw_wait.get("source_node_id"))))
            target_node = _dict(nodes.get(_text(raw_wait.get("target_node_id"))))
            policy = _dict(
                raw_wait.get("async_policy") or raw_wait.get("poll_policy")
            )
            wait_windows = [
                row
                for row in _list(raw_wait.get("time_window_constraints"))
                if isinstance(row, dict)
                and row.get("source_backed") is True
                and row.get("window_ms") == int(window_ms)
            ]
            if not (
                raw_wait.get("source_backed") is True
                and _text(raw_wait.get("wait_kind")) == "TIMED_WAIT"
                and _text(raw_wait.get("status")) == "BOUND"
                and _text(raw_wait.get("source_node_id"))
                != _text(raw_wait.get("target_node_id"))
                and _text(source_node.get("operation_ref"))
                == _text(expression.get("anchor_operation_ref"))
                and _text(source_node.get("operation_ref")) != operation_ref
                and _text(target_node.get("operation_ref")) == operation_ref
                and _text(
                    raw_wait.get("observer_operation_ref")
                    or raw_wait.get("read_operation_ref")
                ) == _text(expression.get("completion_observer"))
                and len(wait_windows) == 1
                and policy.get("enabled") is True
                and policy.get("expected_max_delay_ms") == int(window_ms)
            ):
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_BINDING",
                    "detail": "temporal_process_wait_binding_contract_mismatch",
                }
            from .multi_step_protocol import compile_multi_step_process_protocol

            process_property = {
                **property_spec,
                "process_graph_ref": graph_ref,
            }
            process_result = compile_multi_step_process_protocol(
                {
                    "risk_family": family,
                    "operation_ref": operation_ref,
                    "control_actor_ref": control_actor_ref,
                    "treatment_actor_ref": treatment_actor_ref,
                    "property_spec": process_property,
                    "behavior_ir": _dict(behavior_ir),
                }
            )
            if _text(process_result.get("status")) != "COMPILED":
                return process_result
            observers = [
                dict(row)
                for row in _list(process_result.get("observers"))
                if isinstance(row, dict)
            ]
            if not any(
                _text(row.get("observer_id")) == "temporal_window"
                for row in observers
            ):
                observers.append({"observer_id": "temporal_window"})
            return {
                **process_result,
                "observers": observers,
                "assertion": {
                    "kind": "eventual_consistency",
                    "temporal_semantics": "action_deadline",
                    "window_ms": int(window_ms),
                    "wait_contract_ref": wait_ref,
                    "process_graph_ref": graph_ref,
                    "anchor_operation_ref": _text(
                        expression.get("anchor_operation_ref")
                    ),
                    "completion_operation_ref": operation_ref,
                },
            }
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

    if family == "ui_state_consistency":
        # ── UI/UX browser plan protocol ──
        # A UI rule (CUST-PROD-01 / ORACLE-UI-002 …) constrains a browser
        # page: the experiment opens the declared page URL, optionally
        # performs the oracle's action, and observes the rendered DOM. The
        # ui_browser observer captures the page evidence; the
        # ui_state_consistency assertion judges it against the rule.
        _ui_url = _text(property_spec.get("ui_url"))
        if not _ui_url:
            return {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_OPERATION",
                "detail": "ui_rule_requires_declared_page_url",
            }
        # State vocabulary comes from the rule's own text: the status enum
        # literals (ON_SALE/OFF_SALE/DRAFT/…) the UI rule names are the
        # observable tokens the rendered page must honour. Never inferred.
        # Protocol/technology vocabulary (API/HTTP/URL/DOM/SKU/…) is product
        # domain language, not a business state — a rule mentioning "the API
        # returns them" must not turn "API" into a page state.
        _TECH_VOCAB = {
            "API", "HTTP", "HTTPS", "URL", "URI", "JSON", "XML", "DOM",
            "SKU", "A11Y", "ID", "SQL", "HTML", "CSS", "JS", "CLI", "UI",
            "UX", "PDF", "CSV", "XLSX",
        }
        _ui_raw = _text(_dict(property_spec.get("expression")).get("raw"))
        # Rule-id tokens (CUST-PROD-01 → CUST/PROD) are not page states:
        # exclude the id prefix before the rule text.
        _ui_id_part = _ui_raw.split("：", 1)[0]
        _ui_id_tokens = {
            _it.group(0)
            for _it in re.finditer(r"\b[A-Z]{2,}\b", _ui_id_part)
        }
        _ui_states = sorted({
            _match.group(0)
            for _match in re.finditer(r"\b[A-Z][A-Z0-9_]{2,}\b", _ui_raw)
            if _match.group(0) not in _ui_id_tokens
            and _match.group(0) not in _TECH_VOCAB
        })
        # The allowed state set from the rule's own declaration
        # (Only products with status ON_SALE may be rendered / 仅返回
        # ON_SALE / Filter non-ON_SALE items): the enum literal after
        # only/仅/只 or after non- is the ONLY state the page may render.
        # Without the declaration the assertion stays INDETERMINATE on every
        # page (no fabricated verdicts).
        _ui_allowed = sorted({
            _am.group(1)
            for _am in re.finditer(
                r"(?:only|Only|ONLY|only may|Only may|仅|只|只允许|non-|Non-)"
                r"\b[^.]{0,60}?\b([A-Z][A-Z0-9_]{2,})\b",
                _ui_raw,
            )
        })
        # Forbidden states: the rule's own prohibition words. Two source
        # shapes are consumed — the document's structured negative_examples
        # (["OFF_SALE", "DRAFT", …]) and prohibition sentences in the rule /
        # oracle text (OFF_SALE and DRAFT titles are absent from DOM, 页面
        # 不得渲染已删除商品). Prohibition is judged sentence-wise: a
        # sentence carrying a prohibition word makes every enum literal in
        # that sentence forbidden; a plain statement (order state becomes
        # CANCELLED) forbids nothing. Never inferred.
        _ui_forbidden_raw: list[str] = []
        _ui_forbidden_set: set[str] = set()
        # The document's structured negative_examples are forbidden by
        # declaration — no prohibition sentence is needed for them.
        _ui_forbidden_set.update(
            _text(item)
            for item in _list(property_spec.get("negative_examples"))
            if _text(item)
            and re.search(r"\b[A-Z][A-Z0-9_]{2,}\b", _text(item))
            and _text(item) not in _TECH_VOCAB
        )
        _ui_oracle = _dict(property_spec.get("ui_oracle"))
        for _then_row in _list(_ui_oracle.get("then")):
            _ui_forbidden_raw.append(_text(_then_row))
        # Oracle expectation sentences are judged sentence-wise: a sentence
        # carrying a prohibition word makes every enum literal in that
        # sentence forbidden; a plain statement (state text is CANCELLED)
        # forbids nothing. Never inferred.
        for _sentence in re.split(r"[.;。\n]+", " ".join(_ui_forbidden_raw)):
            if re.search(
                r"(?:absent|not present|must not|must never|should not|"
                r"is not|are not|cannot|can not|\bno\b|without|"
                r"禁止|不得|不可|不应|不能|不包含|不存在|隐藏)",
                _sentence,
            ):
                _ui_forbidden_set.update(
                    _fm.group(0)
                    for _fm in re.finditer(r"\b[A-Z][A-Z0-9_]{2,}\b", _sentence)
                    if _fm.group(0) not in _TECH_VOCAB
                )
        _ui_forbidden = sorted(_ui_forbidden_set)
        # ── Surface-declared DOM assertions ──
        # The UI surface declaration chain compiles visible UI material into
        # governed read-only Playwright plans (surface_contracts) riding on
        # the obligation property. When present, the protocol carries their
        # declared expectations into the assertion so the rendered page is
        # judged against the document's own control vocabulary (button
        # visible/hidden, menu isolation, display-state absence) instead of
        # token guessing. Only read-only expectations are accepted; an
        # interactive surface plan is refused here — it must declare cleanup
        # equivalence through the governed interactive UI adapter, never
        # through this read-only protocol.
        _surface_checks: list[dict[str, Any]] = []
        for _surface_contract in _list(property_spec.get("surface_contracts")):
            _steps = [
                dict(row)
                for row in _list(
                    _dict(_dict(_surface_contract.get("ui_request")).get("browser_plan")).get("steps")
                )
                if isinstance(row, dict)
            ]
            _surface_actions = [
                _text(row.get("action")).lower()
                for row in _steps
                if _text(row.get("action"))
            ]
            if any(
                _text(action) not in _READ_ONLY_UI_ACTIONS
                for action in _surface_actions
                if _text(action)
            ):
                return {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_TARGET_POLICY",
                    "detail": (
                        "ui_surface_interaction_requires_cleanup_equivalence:"
                        + ",".join(
                            action
                            for action in _surface_actions
                            if _text(action) not in _READ_ONLY_UI_ACTIONS
                        )
                    ),
                }
            for _step in _steps:
                _action = _text(_step.get("action")).lower()
                if _action not in _READ_ONLY_UI_ACTIONS:
                    continue
                _check: dict[str, Any] = {
                    "action": _action,
                    "locator_intent": dict(_step.get("locator_intent") or {}),
                }
                for _field in ("text", "pattern", "expected", "selector"):
                    if _text(_step.get(_field)):
                        _check[_field] = _text(_step.get(_field))
                _surface_checks.append(_check)
        return {
            "status": "COMPILED",
            "control_plan": [{
                "step_id": "control_1",
                "actor_ref": control_actor_ref,
                "operation_ref": operation_ref,
                "intent": "ui_page_observation",
                "protocol_step": "ui_open",
                "ui_url": _ui_url,
                # The page observation is executed by the ui_browser observer
                # (real browser navigation of the declared URL) — declare the
                # observer requirement on the step itself so the flow freezer
                # binds it to this exact step; the step carries no HTTP
                # operation identity for scope-based observer resolution.
                "surface": "ui_browser",
                "observer_requirements": [{"observer_id": "ui_browser"}],
            }],
            "treatment_plan": [],
            "observers": [{"observer_id": "ui_browser"}],
            "assertion": {
                "kind": "ui_state_consistency",
                "ui_url": _ui_url,
                "states": _ui_states,
                "allowed_states": _ui_allowed,
                "forbidden_states": _ui_forbidden,
                "surface_checks": _surface_checks,
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
            # ── Query-safety SQL-injection probe ──
            # A rule declaring query-safety vocabulary (关键词必须参数化查询 /
            # 表名拼接存在注入风险) on a read operation governs the query
            # parameters, not a request body. Compile the injection probe
            # first so the read surface is actually probed; without it the
            # rule dies below as read_side_rule_lacks_decidable_assertion
            # and a concatenating target is never exercised.
            _injection_probe = _compile_query_safety_injection_probe(
                operation=operation,
                operation_ref=operation_ref,
                property_spec=property_spec,
                actor_ref=treatment_actor_ref or control_actor_ref,
            )
            if _injection_probe is not None:
                return _injection_probe
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
            # ── Identity-scoped read entity-state exposure ──
            # A rule constraining which entity rows a surface may return
            # (用户端不展示下架商品、草稿商品、内部商品) is decidable on a
            # DETAIL read (GET /api/products/{sku}) only when the treatment
            # can aim the path at an entity the environment really has in a
            # non-public state. The write-side mutation machinery is body-only;
            # this projection builds the path-identity variant so the exposure
            # rule reaches the detail surface instead of dying as
            # read_side_rule_lacks_decidable_assertion.
            _read_path_exposure = _read_side_path_identity_exposure(
                operation=operation,
                operation_ref=operation_ref,
                property_spec=property_spec,
                control_actor_ref=control_actor_ref,
                treatment_actor_ref=treatment_actor_ref,
                behavior_ir=behavior_ir,
            )
            if _read_path_exposure is not None:
                return _read_path_exposure
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
        # ── Read-side row-state filter protocol ──
        # A rule constraining which entity rows a caller may see (用户端
        # 不展示下架商品、草稿商品、内部商品) becomes decidable when the
        # operation's own declaration states the ONLY row states the surface
        # may return (业务约束：用户端默认仅返回 ON_SALE 商品). The allowed
        # states come from that declaration — generic enum literals in the
        # source contract, never inferred. The single control arm observes
        # the real rows; the assertion fails on any row whose state field is
        # outside the declared set. Without the declaration the rule stays a
        # visible BLOCKED below (no vacuous observation).
        _allowed_states = _read_side_allowed_states(
            property_spec, operation, behavior_ir=behavior_ir
        )
        if method in {"GET", "HEAD"} and _allowed_states:
            _read_query: dict[str, Any] = {}
            for _param in _list(operation.get("parameters")):
                if not isinstance(_param, dict):
                    continue
                if _text(_param.get("in")).lower() != "query":
                    continue
                if not _text(_param.get("name")):
                    continue
                if not (_text(_param.get("required")).lower() == "true" or _param.get("required") is True):
                    continue
                _param_value = (
                    _param.get("example")
                    or _dict(_param.get("schema")).get("example")
                    or _param.get("default")
                )
                if _param_value is not None:
                    _read_query[_text(_param.get("name"))] = _param_value
            _read_step: dict[str, Any] = {
                "step_id": "control_1",
                "actor_ref": control_actor_ref,
                "operation_ref": operation_ref,
                "intent": "read_side_row_state_observation",
                "protocol_step": "positive_control",
            }
            if _read_query:
                _read_step["query"] = _read_query
            return {
                "status": "COMPILED",
                "control_plan": [_read_step],
                "treatment_plan": [],
                "observers": [
                    {"observer_id": "http_response"},
                    {"observer_id": "typed_assertion"},
                ],
                "assertion": {
                    "kind": "response_rows_state_filter",
                    "allowed_states": sorted(_allowed_states),
                },
            }
        if method not in {"POST", "PUT", "PATCH", "DELETE"} and not (
            allows_non_body and method in {"GET", "HEAD"}
        ):
            # ── Read-side numeric boundary projection ──
            # A GET/HEAD rule carrying a source-declared numeric boundary
            # (库存可用数量必须非负 / cart qty must stay positive) reached the
            # read-side fallback with no write-side mutation material. The
            # boundary is decidable on the response the read itself returns:
            # reuse the read-only numeric audit chain (assertion kind +
            # http_response observer, both already registered) on the bound
            # operation — a single observed row below the boundary is
            # violation evidence. Without the declared field or boundary
            # operator the rule stays a visible BLOCKED below (no vacuous
            # observation).
            _read_numeric = _read_side_numeric_boundary_projection(
                property_spec=property_spec,
                operation_ref=operation_ref,
                actor_ref=control_actor_ref or treatment_actor_ref,
            )
            if _read_numeric is not None:
                return _read_numeric
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
        # Schema-REQUIRED ownership fields must stay: the required-field gate
        # (``missing_required_body_fields:userId``) blocks pre-transport when
        # a field the target's contract declares mandatory is stripped, so the
        # experiment dies before the rule under test is observed. Keeping the
        # required identity field lets the runtime identity channel bind the
        # acting actor's own login-observed identity into it.
        _mutation_field = _text(mutation.get("json_path")).rsplit(".", 1)[-1].strip("[]")
        _keep_fields = {_mutation_field} if _mutation_field else set()
        _required_schema_fields = {
            str(field)
            for field in _list(_request_body_schema(operation).get("required"))
            if _text(field)
        }
        _keep_fields.update(_required_schema_fields)
        control_body = _strip_ownership_identity_fields(
            control_body, keep=_keep_fields or None
        )
        treatment_body = _strip_ownership_identity_fields(
            treatment_body, keep=_keep_fields or None
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

    write_body: dict[str, Any] | None = None
    if (
        family in {"authorization", "isolation", "visibility"}
        and method in {"POST", "PUT", "PATCH"}
    ):
        write_body = source_request_example(operation, sibling_operations=sibling_operations)
        if not write_body and not property_spec.get("defer_write_body_to_runtime"):
            write_body = _minimal_body_from_schema(operation)
        if not write_body:
            if (
                not property_spec.get("defer_write_body_to_runtime")
                and _operation_has_required_body(operation)
            ):
                # Source-declared required body with no field-level schema: the
                # target validates presence only, so attach an explicit empty
                # object. A bodyless POST is rejected 422 Field required and
                # misread as an authorization defect; {} exercises the real rule.
                write_body = {}
            else:
                # No example, no schema material, or the body is deferred to
                # the runtime observed-entity projection: no compile-time body.
                write_body = None

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
        if write_body is not None:
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
    if write_body is not None:
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
