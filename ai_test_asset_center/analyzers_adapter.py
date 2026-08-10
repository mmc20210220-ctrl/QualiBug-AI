"""
Phase92A: Analyzers Adapter — 整合分析器到证据管道

将 8 个分析器（business_rules, state_machine, multi_tenant, conservation,
concurrency, async_task, cache_consistency, authorization）整合为标准
Reasoner 引擎，输出标准假设格式，接入 Phase92A 证据管道。
"""

from __future__ import annotations

import hashlib
import re
import json
from typing import Any, Dict, List

from .openapi_spec_utils import parse_openapi_spec

class AnalyzersAdapter:
    """
    分析器适配器：将分析器输出转换为 Phase92A 标准假设格式
    """

    def __init__(self):
        self.analyzers = {}
        self.retirement_reason = ""
        self._init_analyzers()

    def _init_analyzers(self):
        """初始化所有 8 个分析器。

        The analyzer package was retired per the architecture-inventory
        strangler process (``ai_test_asset_center/analyzers/__init__.py`` is an
        empty package). The adapter keeps its interface so stage wiring stays
        stable, but reports the retirement explicitly instead of pretending 8
        engines exist and then degrading every one of them to 0 hypotheses.
        """
        try:
            from .analyzers import (
                BusinessRulesAnalyzer,
                StateMachineAnalyzer,
                MultiTenantAnalyzer,
                ConservationAnalyzer,
                ConcurrencyAnalyzer,
                AsyncTaskAnalyzer,
                CacheConsistencyAnalyzer,
                AuthorizationAnalyzer,
            )
            self.analyzers = {
                "business_rules": BusinessRulesAnalyzer(),
                "state_machine": StateMachineAnalyzer(),
                "multi_tenant": MultiTenantAnalyzer(),
                "conservation": ConservationAnalyzer(),
                "concurrency": ConcurrencyAnalyzer(),
                "async_task": AsyncTaskAnalyzer(),
                "cache_consistency": CacheConsistencyAnalyzer(),
                "authorization": AuthorizationAnalyzer(),
            }
        except Exception as e:
            # Retirement is a declared architecture decision, not a broken
            # import: surface it as one informational line instead of a per-run
            # WARN stack and 8 fake degraded engines.
            self.retirement_reason = f"{type(e).__name__}: {str(e)[:200]}"
            self.analyzers = {}

    def run_analyzer(
        self,
        engine_name: str,
        prd_text: str,
        api_spec: str,
        max_hypotheses: int = 15
    ) -> List[Dict[str, Any]]:
        """
        运行单个分析器并转换为标准假设格式
        """
        hypotheses: List[Dict[str, Any]] = []

        try:
            analyzer = self.analyzers.get(engine_name)
            if not analyzer:
                return []
            api_spec_parsed = self._parse_api_spec(api_spec)

            # 调用分析器
            violations = self._execute_analyzer(analyzer, engine_name, prd_text, api_spec_parsed)

            # 转换为标准假设格式
            for idx, violation in enumerate(violations[:max_hypotheses]):
                hypothesis = self._convert_to_hypothesis(
                    violation,
                    engine_name,
                    idx,
                    api_spec_parsed,
                )
                if hypothesis:
                    hypotheses.append(hypothesis)

        except Exception as e:
            print(f"[WARN] [{engine_name}] 分析失败: {e}", flush=True)

        return hypotheses

    def _execute_analyzer(
        self,
        analyzer: Any,
        engine_name: str,
        prd_text: str,
        api_spec_parsed: Dict[str, Any],
    ) -> List[Any]:
        """执行具体的分析器"""
        violations = []

        try:
            if engine_name == "business_rules":
                rules = analyzer.extract_rules_from_prd(prd_text)
                violations = analyzer.validate_rule_implementation(rules, api_spec_parsed)

            elif engine_name == "state_machine":
                sm = analyzer.extract_state_machine(prd_text, api_spec_parsed)
                violations = analyzer.validate_state_transitions(sm, [])

            elif engine_name == "multi_tenant":
                violations = analyzer.analyze_api_endpoints(api_spec_parsed)

            elif engine_name == "conservation":
                violations = analyzer.analyze_conservation(prd_text, api_spec_parsed)

            elif engine_name == "concurrency":
                violations = analyzer.analyze_concurrency(api_spec_parsed, prd_text)

            elif engine_name == "async_task":
                violations = analyzer.analyze_async_tasks(api_spec_parsed, prd_text)

            elif engine_name == "cache_consistency":
                violations = analyzer.analyze_cache_consistency(api_spec_parsed, prd_text)

            elif engine_name == "authorization":
                violations = analyzer.analyze_authorization(api_spec_parsed, prd_text)

        except Exception as e:
            pass

        return violations

    def _parse_api_spec(self, api_spec: str) -> Dict[str, Any]:
        """解析 API 规格，避免退化到固定 demo 路径。"""
        return parse_openapi_spec(api_spec)

    def _build_route_catalog(self, api_spec_parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
        """构建轻量路由目录，供无 endpoint 场景做保守绑定。"""
        catalog: List[Dict[str, Any]] = []
        for path, methods in (api_spec_parsed.get("paths", {}) or {}).items():
            if not isinstance(methods, dict):
                continue
            for method, config in methods.items():
                config = config if isinstance(config, dict) else {}
                summary = str(config.get("summary", "") or "")
                description = str(config.get("description", "") or "")
                catalog.append(
                    {
                        "method": str(method).upper(),
                        "path": str(path),
                        "summary": summary,
                        "description": description,
                        "keywords": self._tokenize_match_text(" ".join([str(path), summary, description])),
                    }
                )
        return catalog

    def _tokenize_match_text(self, text: str) -> List[str]:
        """抽取文本关键词用于 route 匹配。"""
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]{2,}", str(text or ""))
        stopwords = {
            "api", "http", "json", "data", "info", "list", "detail",
            "must", "should", "rule", "check", "system",
        }
        normalized: List[str] = []
        for token in tokens:
            lower = token.lower()
            if lower in stopwords:
                continue
            normalized.append(lower)
        return normalized[:30]

    def _match_catalog_paths(self, violation: Any, api_spec_parsed: Dict[str, Any]) -> List[str]:
        """根据标题/描述/规则文本与 OpenAPI 目录做保守路径绑定。"""
        catalog = self._build_route_catalog(api_spec_parsed)
        if not catalog:
            return []

        evidence = getattr(violation, "evidence", {}) or {}
        reproduction_steps = getattr(violation, "reproduction_steps", []) or []
        text = " ".join(
            [
                str(getattr(violation, "title", "") or ""),
                str(getattr(violation, "description", "") or ""),
                str(getattr(violation, "expected_behavior", "") or ""),
                str(getattr(violation, "actual_behavior", "") or ""),
                json.dumps(evidence, ensure_ascii=False, default=str),
                " ".join(str(step) for step in reproduction_steps[:5]),
            ]
        )
        query_tokens = set(self._tokenize_match_text(text))
        if not query_tokens:
            return []

        inferred_method = self._infer_method(violation)
        ranked: List[tuple[int, str]] = []
        for route in catalog:
            route_tokens = set(route.get("keywords", []))
            overlap = len(query_tokens.intersection(route_tokens))
            if overlap <= 0:
                continue
            score = overlap * 10
            if route["method"] == inferred_method:
                score += 3
            if any(token in route["path"].lower() for token in list(query_tokens)[:8]):
                score += 2
            ranked.append((score, f"{route['method']} {route['path']}"))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        seen: set[str] = set()
        bound: List[str] = []
        for _, route_ref in ranked:
            if route_ref in seen:
                continue
            bound.append(route_ref)
            seen.add(route_ref)
            if len(bound) >= 2:
                break
        return bound

    def _extract_candidate_paths(self, violation: Any) -> List[str]:
        """从分析器输出中提取可绑定的 API 路径。"""
        candidates: List[str] = []
        evidence = getattr(violation, "evidence", {}) or {}
        related_endpoints = getattr(violation, "related_endpoints", []) or []
        affected_endpoints = getattr(violation, "affected_endpoints", []) or []
        location = str(getattr(violation, "location", "") or "")
        reproduction_steps = getattr(violation, "reproduction_steps", []) or []

        def _collect(value: Any) -> None:
            if isinstance(value, str) and "/" in value:
                candidates.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    _collect(item)
            elif isinstance(value, dict):
                for item in value.values():
                    _collect(item)

        _collect(related_endpoints)
        _collect(affected_endpoints)
        _collect(evidence)
        _collect(location)
        _collect(reproduction_steps)

        normalized: List[str] = []
        seen: set[str] = set()
        for raw in candidates:
            match = re.search(
                r"(?:(GET|POST|PUT|DELETE|PATCH)\s+)?(\/(?:api|master|production|inventory|quality|planning|sales|purchase|warehouse|report|system|admin)\/[\w\-\/{}]+)",
                raw,
                re.IGNORECASE,
            )
            if not match:
                continue
            method = (match.group(1) or "").upper()
            path = match.group(2)
            text = f"{method} {path}".strip()
            if text not in seen:
                normalized.append(text)
                seen.add(text)
        return normalized

    def _infer_method(self, violation: Any, path_hint: str = "") -> str:
        """根据标题、描述和证据推断 HTTP 方法。"""
        title = str(getattr(violation, "title", "") or "").lower()
        description = str(getattr(violation, "description", "") or "").lower()
        expected = str(getattr(violation, "expected_behavior", "") or "").lower()
        actual = str(getattr(violation, "actual_behavior", "") or "").lower()
        text = " ".join(part for part in (title, description, expected, actual, path_hint.lower()) if part)

        delete_kw = ("删除", "移除", "delete", "remove")
        update_kw = ("更新", "修改", "变更", "update", "modify", "patch", "edit")
        create_kw = ("创建", "新增", "添加", "create", "add", "insert")

        if any(keyword in text for keyword in delete_kw):
            return "DELETE"
        if any(keyword in text for keyword in update_kw):
            return "PUT"
        if any(keyword in text for keyword in create_kw):
            return "POST"
        return "GET"

    def _build_verification_method(self, violation: Any, api_spec_parsed: Dict[str, Any]) -> Dict[str, Any]:
        """为执行器构建结构化 verification_method。"""
        candidates = self._extract_candidate_paths(violation)
        if not candidates:
            candidates = self._match_catalog_paths(violation, api_spec_parsed)
        if not candidates:
            return {}

        evidence = getattr(violation, "evidence", {}) or {}
        step_map: Dict[str, Any] = {}

        write_endpoint = str(
            evidence.get("write_endpoint")
            or evidence.get("source_endpoint")
            or ""
        ).strip()
        read_endpoint = str(
            evidence.get("read_endpoint")
            or evidence.get("target_endpoint")
            or evidence.get("endpoint")
            or evidence.get("path")
            or ""
        ).strip()

        # 缓存一致性、守恒等场景优先保留写后读序列，交给执行器自动加 observer。
        if write_endpoint and read_endpoint:
            write_method = self._infer_method(violation, write_endpoint)
            read_match = re.search(r"(\/[\w\-\/{}]+)", read_endpoint)
            write_match = re.search(r"(\/[\w\-\/{}]+)", write_endpoint)
            if write_match and read_match:
                step_map["step1"] = f"{write_method} {write_match.group(1)}"
                step_map["step2"] = f"GET {read_match.group(1)}"
                return step_map

        first = candidates[0]
        if " " in first:
            method, path = first.split(" ", 1)
            step_map["step1"] = f"{method.upper()} {path}"
        else:
            step_map["step1"] = f"{self._infer_method(violation, first)} {first}"

        # 保留第二个不同路径，方便异步/缓存类问题做交叉观察。
        if len(candidates) > 1:
            second = candidates[1]
            if " " in second:
                method, path = second.split(" ", 1)
                step_map["step2"] = f"{method.upper()} {path}"
            else:
                step_map["step2"] = f"GET {second}"
        return step_map

    def _extract_entity_name(self, verification_method: Dict[str, Any]) -> str:
        """从 verification_method 中提取实体名，帮助执行器做 observer 绑定。"""
        for key in ("path", "step1", "step2", "step3"):
            value = str(verification_method.get(key, "") or "")
            match = re.search(
                r"/(?:api|master|production|inventory|quality|planning|sales|purchase|warehouse|report|system|admin)/([\w\-]+)",
                value,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).rstrip("s")
        return ""

    def _convert_to_hypothesis(
        self,
        violation: Any,
        engine_name: str,
        idx: int,
        api_spec_parsed: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """将分析器输出转换为标准假设格式"""
        try:
            # 提取字段
            title = str(getattr(violation, "title", "未命名发现"))
            description = str(getattr(violation, "description", ""))
            severity = str(getattr(violation, "severity", "P2"))
            category = str(getattr(violation, "category", engine_name))

            expected = str(
                getattr(violation, "expected_behavior", "")
                or getattr(violation, "expected", "")
                or "系统应该按照业务规则正确工作"
            )
            actual = str(
                getattr(violation, "actual_behavior", "")
                or getattr(violation, "actual", "")
                or description
                or "可能存在问题"
            )

            evidence = getattr(violation, "evidence", {}) or {}
            reproduction_steps = getattr(violation, "reproduction_steps", []) or []
            related_endpoints = getattr(violation, "related_endpoints", []) or []
            affected_endpoints = getattr(violation, "affected_endpoints", []) or []

            verification_method = self._build_verification_method(violation, api_spec_parsed)
            entity_name = self._extract_entity_name(verification_method)

            # 生成唯一的 hypothesis_id
            title_hash = hashlib.md5(title.encode()).hexdigest()[:8]
            hypothesis_id = f"{engine_name}_{idx}_{title_hash}"

            # 构建标准假设
            hypothesis = {
                "hypothesis_id": hypothesis_id,
                "title": title,
                "severity": severity,
                "category": category,
                "risk_type": category,
                "description": description,
                "expected_behavior": expected,
                "actual_behavior": actual,
                "verification_method": verification_method,
                "entity": entity_name,
                "source_entity": entity_name,
                "reproduction_steps": reproduction_steps[:5],
                "evidence": evidence,
                "related_endpoints": related_endpoints or affected_endpoints,
                "why_this_matters": "这个问题可能会影响业务功能、数据一致性或安全性",
                "symptoms_if_broken": "用户可能会遇到功能失败、数据错误或安全问题",
                "_reasoner_engine": engine_name,
                "_hypothesis_source": "local_analyzer",
            }

            return hypothesis

        except Exception:
            return None


def build_analyzer_hypotheses(
    prd_text: str,
    api_spec: str,
    max_hypotheses_per_analyzer: int = 15
) -> Dict[str, List[Dict[str, Any]]]:
    """
    运行所有分析器并返回假设
    """
    adapter = AnalyzersAdapter()
    results = {}

    for engine_name in [
        "business_rules",
        "state_machine",
        "multi_tenant",
        "conservation",
        "concurrency",
        "async_task",
        "cache_consistency",
        "authorization",
    ]:
        try:
            hypotheses = adapter.run_analyzer(
                engine_name,
                prd_text,
                api_spec,
                max_hypotheses_per_analyzer
            )
            if hypotheses:
                results[engine_name] = hypotheses
        except Exception as e:
            print(f"[WARN] 分析器 {engine_name} 运行失败: {e}", flush=True)

    return results


def get_analyzer_engine_names() -> List[str]:
    """Return the engines that actually initialized.

    The analyzer package is retired, so this is empty unless a future
    successor re-populates ``ai_test_asset_center/analyzers``. The scan stage
    must never report fake per-engine degradations for engines that do not
    exist.
    """
    adapter = AnalyzersAdapter()
    if not adapter.analyzers and adapter.retirement_reason:
        print(
            f"[INFO] 分析器包已退役（architecture strangler）：{adapter.retirement_reason}；"
            "跳过分析器阶段（不产生假设，不伪造 degraded）",
            flush=True,
        )
    return sorted(adapter.analyzers.keys())
