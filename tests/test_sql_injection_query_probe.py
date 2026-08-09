# -*- coding: utf-8 -*-
"""Query-safety SQL-injection probe — read-side query parameter channel.

Root cause under test: the query-safety mutation heuristic
(experiment_protocols_base._semantic_invalid_value) only fires for request
BODY string fields. A rule declaring query-safety vocabulary
(关键词必须参数化查询 / 表名拼接存在注入风险) on a GET/HEAD operation governs
the query parameters, which were never probed — so a target that
concatenates the raw value into SQL was unreachable on every read surface.

The tests pin the generic behavior: the probe compiles only for query-safety
rules on documented query parameters, the treatment request carries the
OWASP-style quote-escape payload, and the tri-state evaluator reports
VIOLATION only on SQL-interpretation evidence (database error markers, or
the payload echoed inside an SQL-named field), PASS on literal treatment,
and INDETERMINATE on missing/ambiguous evidence.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.validation_read_side_protocol import (
    SQL_INJECTION_PAYLOAD,
    _declares_query_safety,
    _evaluate_sql_injection_probe,
    _target_query_parameter,
    compile_query_safety_injection_probe,
)

_SEARCH_OP = {
    "method": "GET",
    "path": "/api/products/search-raw",
    "parameters": [
        {"in": "query", "name": "keyword", "schema": {"type": "string", "example": "phone"}},
        {"in": "query", "name": "category", "schema": {"type": "string", "example": "PHONE"}},
    ],
}

_TABLE_OP = {
    "method": "GET",
    "path": "/api/reports/debug/table",
    "parameters": [
        {"in": "query", "name": "table", "schema": {"type": "string"}},
    ],
}


class TestProbeCompilation:
    def test_keyword_parameterized_rule_compiles_probe(self):
        exp = compile_query_safety_injection_probe(
            operation=_SEARCH_OP,
            operation_ref="op-search",
            property_spec={"source_intent": "原始搜索接口拼接 SQL 存在注入风险，关键词必须参数化查询"},
            actor_ref="actor_buyer01",
        )
        assert exp is not None
        assert exp["status"] == "COMPILED"
        assert exp["assertion"]["kind"] == "sql_injection_probe"
        assert exp["assertion"]["query_parameter"] == "keyword"
        assert exp["treatment_plan"][0]["query"] == {
            "keyword": SQL_INJECTION_PAYLOAD,
            "category": "PHONE",
        }

    def test_table_name_rule_compiles_probe(self):
        """REPORT-007 shape: a table-name concatenation rule governs `table`."""
        exp = compile_query_safety_injection_probe(
            operation=_TABLE_OP,
            operation_ref="op-debug",
            property_spec={"source_intent": "调试表查询接口拼接表名存在 SQL 注入和越权读表"},
            actor_ref="actor_buyer01",
        )
        assert exp is not None
        assert exp["treatment_plan"][0]["query"] == {"table": SQL_INJECTION_PAYLOAD}

    def test_ordinary_rule_never_compiles_probe(self):
        assert compile_query_safety_injection_probe(
            operation=_SEARCH_OP,
            operation_ref="op-search",
            property_spec={"source_intent": "用户端不展示下架商品"},
            actor_ref="actor_buyer01",
        ) is None

    def test_write_operation_never_compiles_probe(self):
        write_op = {**_SEARCH_OP, "method": "POST"}
        assert compile_query_safety_injection_probe(
            operation=write_op,
            operation_ref="op-search",
            property_spec={"source_intent": "关键词必须参数化查询"},
            actor_ref="actor_buyer01",
        ) is None

    def test_undocumented_parameter_stays_uncompiled(self):
        assert compile_query_safety_injection_probe(
            operation={**_SEARCH_OP, "parameters": []},
            operation_ref="op-search",
            property_spec={"source_intent": "关键词必须参数化查询"},
            actor_ref="actor_buyer01",
        ) is None

    def test_vocabulary_gate(self):
        assert _declares_query_safety("关键词必须参数化查询")
        assert _declares_query_safety("拼接表名存在注入风险")
        assert _declares_query_safety("keyword must be parameterized, no injection")
        assert not _declares_query_safety("用户端不展示下架商品")

    def test_target_parameter_selection(self):
        assert _target_query_parameter(
            {"source_intent": "关键词必须参数化查询"},
            _SEARCH_OP,
            "关键词必须参数化查询",
        ) == "keyword"


class TestProbeEvaluator:
    def _eval(self, status, body):
        return _evaluate_sql_injection_probe({
            "spec": {"query_parameter": "keyword", "payload": SQL_INJECTION_PAYLOAD},
            "observations": {"status_code": status, "body": body},
        })

    def test_sql_error_marker_is_violation(self):
        result = self._eval(500, {"error": "syntax error at or near '1'"})
        assert result["passed"] is False
        assert result["reason_code"] == "SQL_INJECTION_SQL_ERROR_SURFACED"

    def test_payload_echo_in_sql_field_is_violation(self):
        result = self._eval(200, {
            "data": [{"sql": "SELECT * FROM products WHERE keyword LIKE '%' OR '1'='1"}],
        })
        assert result["passed"] is False
        assert result["reason_code"] == "SQL_PAYLOAD_ECHOED_IN_SQL_FIELD"

    def test_clean_literal_treatment_is_pass(self):
        assert self._eval(200, {"data": []})["passed"] is True

    def test_rejection_is_pass(self):
        assert self._eval(400, {"error": "invalid keyword"})["passed"] is True

    def test_request_echo_of_payload_is_not_violation(self):
        """A search endpoint echoing the query term back is NOT injection evidence."""
        result = self._eval(200, {"keyword": SQL_INJECTION_PAYLOAD, "data": []})
        assert result["passed"] is True

    def test_bare_5xx_without_marker_is_indeterminate(self):
        result = self._eval(500, {"error": "boom"})
        assert result["passed"] is None
        assert result["reason_code"] == "SQL_PROBE_TARGET_ERROR"

    def test_missing_evidence_is_indeterminate(self):
        result = _evaluate_sql_injection_probe({
            "spec": {"query_parameter": "keyword"},
            "observations": {"status_code": 0},
        })
        assert result["passed"] is None
        assert result["reason_code"] == "SQL_PROBE_EVIDENCE_MISSING"
