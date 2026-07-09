"""Oracle DSL Parser — Parse WHEN-THEN business rules from natural language.

Supports both English and Chinese natural language patterns.
Parses PRD text (semicolon-delimited) into structured ParsedRule objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ═════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ParsedRule:
    """A single parsed business rule ready for compilation."""
    rule_id: str
    raw_text: str
    actor: str = ""
    action: str = ""
    entity: str = ""
    assertion: str = ""
    timeout_minutes: int = 0
    severity: str = "P1"
    rule_type: str = ""  # Classified after parsing: state_change, conservation, audit, idempotency, permission
    metadata: dict[str, str] = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════════
# Action → Rule Type Mapping
# ═════════════════════════════════════════════════════════════════════════════

ACTION_TYPE_MAP: dict[str, str] = {
    # State change actions
    "cancel": "state_change", "取消": "state_change",
    "close": "state_change", "关闭": "state_change",
    "approve": "state_change", "审批": "state_change",
    "reject": "state_change", "驳回": "state_change",
    "refund": "state_change", "退款": "state_change",
    "complete": "state_change", "完成": "state_change",
    "transition": "state_change", "流转": "state_change",
    # Conservation actions
    "pay": "conservation", "支付": "conservation",
    "deduct": "conservation", "扣减": "conservation",
    "reduce": "conservation", "减少": "conservation",
    "add": "conservation", "增加": "conservation",
    "transfer": "conservation", "转账": "conservation",
    # Audit actions
    "access": "audit", "访问": "audit",
    "view": "audit", "查看": "audit",
    "read": "audit", "读取": "audit",
    "modify": "audit", "修改": "audit",
    "delete": "audit", "删除": "audit",
    # Idempotency actions
    "submit": "idempotency", "提交": "idempotency",
    "callback": "idempotency", "回调": "idempotency",
    "receive": "idempotency", "接收": "idempotency",
    "retry": "idempotency", "重试": "idempotency",
    # General actions
    "create": "state_change", "创建": "state_change",
    "update": "state_change", "更新": "state_change",
    "activate": "state_change", "激活": "state_change",
    "deactivate": "state_change", "停用": "state_change",
    "freeze": "state_change", "冻结": "state_change",
    "unfreeze": "state_change", "解冻": "state_change",
    "import": "state_change", "导入": "state_change",
    "export": "audit", "导出": "audit",
    "sync": "state_change", "同步": "state_change",
    "assign": "state_change", "分配": "state_change",
    "release": "state_change", "发布": "state_change",
    # Permission actions
    "login": "permission", "登录": "permission",
    "impersonate": "permission", "冒充": "permission",
    "grant": "permission", "授权": "permission",
    "revoke": "permission", "撤销": "permission",
}


ASSERTION_TYPE_MAP: dict[str, str] = {
    # Conservation assertions
    "must not change": "conservation", "不能变": "conservation",
    "must be conserved": "conservation", "守恒": "conservation",
    "must balance": "conservation", "必须平衡": "conservation",
    "must match": "conservation", "必须一致": "conservation",
    "must equal": "conservation", "必须等于": "conservation",
    "不能超过": "conservation", "不能大于": "conservation",
    "must be restored": "conservation", "必须恢复": "conservation",
    # State assertions
    "must be updated": "state_change", "必须更新": "state_change",
    "must transition": "state_change", "必须流转": "state_change",
    "must be cancelled": "state_change", "必须取消": "state_change",
    "must not be modified": "state_change", "不能修改": "state_change",
    "cannot be changed": "state_change", "不可变更": "state_change",
    # Audit assertions
    "must be logged": "audit", "必须记录": "audit",
    "must contain entry": "audit", "必须留痕": "audit",
    "must be audited": "audit", "必须审计": "audit",
    "must be traceable": "audit", "可追踪": "audit",
    # Idempotency assertions
    "must be idempotent": "idempotency", "幂等": "idempotency",
    "must not duplicate": "idempotency", "不能重复": "idempotency",
    "must not create duplicate": "idempotency",
    # Permission assertions
    "must not be accessible": "permission", "不能访问": "permission",
    "must be rejected": "permission", "必须拒绝": "permission",
    "must be blocked": "permission", "必须阻止": "permission",
    "must require authorization": "permission", "必须授权": "permission",
    "only authorized": "permission", "仅授权": "permission",
}


# ═════════════════════════════════════════════════════════════════════════════
# DSL Parser
# ═════════════════════════════════════════════════════════════════════════════

class DSLParser:
    """Parse WHEN-THEN business rules from natural language.

    Usage::

        parser = DSLParser()
        rules = parser.parse("WHEN customer cancels order THEN inventory must be restored")
        rules = parser.parse_prd("订单取消后库存必须恢复；支付回调必须幂等")
    """

    # English WHEN-THEN pattern — relaxed for cross-industry generality
    EN_PATTERN = re.compile(
        r"(?:WHEN|IF|AFTER|ON)\s+"
        r"(?P<actor>[\w-]+(?:\s+[\w-]+)*)\s+"
        r"(?P<action>[\w-]+(?:\s+[\w-]+)?)\s+"
        r"(?:ON\s+)?"
        r"(?P<entity>[\w/-]+(?:\s+[\w/-]+)*)\s*"
        r"(?:THEN|THAT|ENSURE|VERIFY)\s+"
        r"(?P<assertion>.+?)\s*"
        r"(?:WITHIN\s+(?P<timeout>\d+)\s*(?:minutes?|mins?|seconds?|secs?|hours?|hrs?))?\s*"
        r"(?:SEVERITY\s+(?P<severity>P[0-4]))?\s*$",
        re.IGNORECASE,
    )

    # Chinese patterns (semicolon-delimited in PRD)
    CN_PATTERNS = [
        # "当X时，Y必须Z" or "X时，Y必须Z"
        re.compile(r"(?:当)?(?P<actor>\S{1,4})?(?P<action>取消|关闭|审批|驳回|退款|完成|支付|扣减|减少|增加|转账|访问|查看|读取|修改|删除|提交|回调|接收|登录|下单|报名)(?P<entity>\S{1,6})?(?:时|后)，?(?P<assertion>.+?)(?:；|。|$)"),
        # "X后Y" (simple sequence)
        re.compile(r"(?P<entity>\S{1,6})(?P<action>取消|关闭|审批|驳回|退款|完成|支付|扣减|下单|报名)(?:后|时)(?P<assertion>.+?)(?:；|。|$)"),
        # "X必须Y" / "X不能Y" / "X不得超过Y" / "X不允许Y"
        re.compile(r"(?P<entity>\S{1,8})(?P<assertion>必须|不能|不可|不允许|仅允许|不得超过|不能超过|不能大于)(?P<rest>.+?)(?:；|。|$)"),
        # "X幂等" / "X回调幂等" (single-word assertions)
        re.compile(r"(?P<entity>\S{1,6})(?P<action>回调|提交|支付)?(?P<assertion>幂等|可重入)(?:；|。|$)"),
    ]

    def __init__(self):
        self._rule_counter = 0

    def parse(self, text: str) -> ParsedRule | None:
        """Parse a single WHEN-THEN rule from text.

        Returns None if the text doesn't match the expected pattern.
        """
        text = text.strip()
        if not text:
            return None

        # Try English pattern first
        m = self.EN_PATTERN.match(text)
        if m:
            return self._from_match(m, text)

        # Try Chinese patterns
        for pattern in self.CN_PATTERNS:
            m = pattern.search(text)
            if m:
                return self._from_cn_match(m, text, pattern)

        # Fallback: try to extract at minimum entity + assertion
        return self._fallback_parse(text)

    def parse_prd(self, prd_text: str) -> list[ParsedRule]:
        """Parse a PRD text (semicolon-delimited rules) into a list of ParsedRules.

        Handles both Chinese semicolons (；) and English semicolons (;).
        """
        rules: list[ParsedRule] = []
        # Split on Chinese or English semicolons, also handle newlines
        segments = re.split(r"[；;]\s*|\n+", prd_text)
        for seg in segments:
            seg = seg.strip()
            if not seg or len(seg) < 3:
                continue
            rule = self.parse(seg)
            if rule:
                rules.append(rule)
        return rules

    def _from_match(self, m: re.Match, raw_text: str) -> ParsedRule:
        self._rule_counter += 1
        actor = (m.group("actor") or "").strip()
        action = (m.group("action") or "").strip()
        entity = (m.group("entity") or "").strip()
        assertion = (m.group("assertion") or "").strip()
        timeout_str = m.group("timeout")
        severity = (m.group("severity") or "P1").strip().upper()

        timeout = 0
        if timeout_str:
            timeout = int(timeout_str)
            # Normalize to minutes
            raw_lower = raw_text.lower()
            if "second" in raw_lower or "sec" in raw_lower:
                timeout = max(1, timeout // 60)
            elif "hour" in raw_lower or "hr" in raw_lower:
                timeout = timeout * 60

        rule_type = self._classify_rule_type(action, assertion)

        return ParsedRule(
            rule_id=f"DSL-{self._rule_counter:04d}",
            raw_text=raw_text,
            actor=actor,
            action=action.lower(),
            entity=entity.lower(),
            assertion=assertion,
            timeout_minutes=timeout,
            severity=severity,
            rule_type=rule_type,
        )

    def _from_cn_match(self, m: re.Match, raw_text: str, pattern: re.Pattern) -> ParsedRule:
        self._rule_counter += 1
        groups = m.groupdict()

        actor = (groups.get("actor") or "").strip()
        action = (groups.get("action") or "").strip()
        entity = (groups.get("entity") or "").strip()
        assertion = (groups.get("assertion") or "").strip()

        # If entity is empty but actor is present, actor IS the entity
        # (e.g., "订单取消后库存必须恢复" → entity="订单", action="取消")
        if not entity and actor:
            entity = actor
            actor = ""

        # If pattern has a "rest" group, append it to assertion
        rest = groups.get("rest", "")
        if rest:
            assertion = f"{assertion}{rest}".strip()

        # Extract severity if present in text
        severity = "P1"
        sev_match = re.search(r"(P[0-3])", raw_text, re.IGNORECASE)
        if sev_match:
            severity = sev_match.group(1).upper()

        rule_type = self._classify_rule_type(action, assertion)

        return ParsedRule(
            rule_id=f"DSL-{self._rule_counter:04d}",
            raw_text=raw_text,
            actor=actor,
            action=action.lower() if action else "",
            entity=entity.lower() if entity else "",
            assertion=assertion,
            timeout_minutes=0,
            severity=severity,
            rule_type=rule_type,
        )

    def _fallback_parse(self, text: str) -> ParsedRule | None:
        """Minimal parse when structured patterns don't match."""
        # Try to extract entity and assertion from the text
        # Pattern: "Entity必须/不能assertion" or "Entity不得assertion"
        m = re.search(r"(\S{2,10}?)(必须|不能|不可|不允许|不得|不得超过|不能超过)(.+)", text)
        if m:
            self._rule_counter += 1
            entity = m.group(1).strip()
            assertion = f"{m.group(2)}{m.group(3)}".strip()
            rule_type = self._classify_rule_type("", assertion)
            return ParsedRule(
                rule_id=f"DSL-{self._rule_counter:04d}",
                raw_text=text,
                entity=entity.lower(),
                assertion=assertion,
                severity="P1",
                rule_type=rule_type,
            )

        # Pattern: "EntityAction" (two-character entity + verb)
        m = re.search(r"(\S{2,6})(.{1,4})", text)
        if m and len(text) >= 3:
            self._rule_counter += 1
            entity = m.group(1).strip()
            rest = m.group(2).strip()
            rule_type = self._classify_rule_type(rest, "")
            return ParsedRule(
                rule_id=f"DSL-{self._rule_counter:04d}",
                raw_text=text,
                entity=entity.lower(),
                assertion=text,
                severity="P1",
                rule_type=rule_type,
            )

        return None

    @staticmethod
    def _classify_rule_type(action: str, assertion: str) -> str:
        """Classify the rule type based on action and assertion keywords."""
        # Check action first
        for keyword, rtype in ACTION_TYPE_MAP.items():
            if keyword in action:
                return rtype

        # Check assertion
        assertion_lower = assertion.lower()
        for keyword, rtype in ASSERTION_TYPE_MAP.items():
            if keyword in assertion_lower:
                return rtype

        # Fallback classification by assertion keywords
        if any(w in assertion_lower for w in ("restore", "recover", "rollback", "恢复", "回滚")):
            return "state_change"
        if any(w in assertion_lower for w in ("log", "audit", "trace", "record", "记录", "审计", "日志")):
            return "audit"
        if any(w in assertion_lower for w in ("duplicate", "repeat", "idempotent", "重复", "幂等")):
            return "idempotency"
        if any(w in assertion_lower for w in ("authorize", "permission", "role", "access", "权限", "授权", "访问")):
            return "permission"
        if any(w in assertion_lower for w in ("amount", "balance", "quantity", "stock", "金额", "余额", "数量", "库存")):
            return "conservation"

        return "state_change"  # Default
