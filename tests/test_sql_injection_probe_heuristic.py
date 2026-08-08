"""SQL-injection probe heuristic for query-safety validation rules.

Root cause (session F, product module): the search-raw rule
(关键词必须参数化查询) — an injection-shaped input for the keyword query
parameter — had no mutation generator. The target really concatenates the
raw keyword into SQL (probed: `' OR '1'='1` escapes the ILIKE literal and is
echoed back in the response `sql` field; a UNION payload returns 500 with a
PostgreSQL error). The rule's binding stays the coordinator's item; the
mutation piece is generic: a rule declaring query-safety vocabulary
(参数化/拼接/注入/SQL/parameterized/injection/concatenat*) makes the
treated string field carry a generic OWASP-style probe — industry-neutral,
never benchmark data, gated on the rule's own vocabulary so ordinary string
fields are never mutated into probes.
"""
import pytest

from ai_test_asset_center.experiment_protocols_base import _semantic_invalid_value


class TestSqlInjectionProbe:
    def test_parameterized_rule_produces_probe(self):
        result = _semantic_invalid_value(
            "keyword", "string", {},
            "业务约束：关键词必须参数化查询",
        )
        assert result == ("' OR '1'='1", "semantic:sql_injection_probe")

    def test_english_query_safety_vocabulary(self):
        result = _semantic_invalid_value(
            "q", "string", {}, "keyword must be parameterized, no injection",
        )
        assert result is not None
        assert result[1] == "semantic:sql_injection_probe"

    def test_concat_vocabulary(self):
        result = _semantic_invalid_value(
            "keyword", "string", {}, "响应不得拼接 SQL",
        )
        assert result is not None
        assert result[1] == "semantic:sql_injection_probe"

    def test_non_query_rule_never_probes(self):
        """An ordinary string rule must not mutate the field into a probe."""
        assert _semantic_invalid_value(
            "keyword", "string", {}, "用户端不展示下架商品",
        ) is None
        assert _semantic_invalid_value(
            "title", "string", {}, "标题必须唯一",
        ) is None

    def test_numeric_fields_keep_negative_value(self):
        """Type gating still wins: a numeric field under a query rule keeps
        the negative-value heuristic, not the probe."""
        result = _semantic_invalid_value(
            "stock", "integer", {}, "关键词必须参数化查询",
        )
        assert result == (-1, "semantic:negative_value")

    def test_probe_is_industry_neutral(self):
        """The probe is a standard OWASP-style input, not benchmark data."""
        value, _ = _semantic_invalid_value(
            "keyword", "string", {}, "关键词必须参数化查询",
        )
        assert "'" in value  # quote-escape probe
        assert "OR" in value
