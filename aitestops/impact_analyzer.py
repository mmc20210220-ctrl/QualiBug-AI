from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from aitestops.api_dsl_to_pytest import ApiDslToPytestGenerator
from aitestops.git_diff_parser import GitDiffParser, ChangedFile, changed_files_to_dict


class ImpactAnalyzer:
    """Analyze code diff and recommend a minimal regression test set.

    V4 goal:
    Git Diff + existing test assets -> impacted endpoints -> selected test cases -> executable regression test.
    """

    def __init__(self):
        self.diff_parser = GitDiffParser()
        self.pytest_generator = ApiDslToPytestGenerator()

    def analyze(self, diff_path: Path, assets_dir: Path, out_dir: Path) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        changed_files = self.diff_parser.parse_file(diff_path)
        endpoints = self._load_json(assets_dir / "openapi_endpoints.json")
        test_cases = self._load_json(assets_dir / "api_test_cases.json")
        dsl_cases = self._load_json(assets_dir / "api_dsl.json")

        impacted = self._match_impacted_endpoints(changed_files, endpoints)
        selected_case_ids = self._select_case_ids(impacted, test_cases)
        selected_dsl = [case for case in dsl_cases if case.get("case_id") in selected_case_ids]

        # Guardrail: always keep health smoke if it exists. It detects broken app/service startup quickly.
        for case in dsl_cases:
            if case.get("case_id", "").endswith("_HAPPY") and case.get("path") == "/health":
                if case["case_id"] not in selected_case_ids:
                    selected_case_ids.insert(0, case["case_id"])
                    selected_dsl.insert(0, case)
                break

        total_cases = len(test_cases)
        selected_count = len(selected_case_ids)
        saving_ratio = round(1 - selected_count / total_cases, 4) if total_cases else 0

        plan = {
            "version": "v4_impact_regression",
            "source_diff": str(diff_path),
            "assets_dir": str(assets_dir),
            "changed_files": changed_files_to_dict(changed_files),
            "impacted_endpoints": impacted,
            "selected_case_ids": selected_case_ids,
            "total_case_count": total_cases,
            "selected_case_count": selected_count,
            "estimated_regression_saving_ratio": saving_ratio,
            "strategy": "Run health smoke + all cases related to impacted endpoints. Do not run full regression unless impact confidence is low.",
        }

        (out_dir / "impact_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "selected_api_dsl.json").write_text(json.dumps(selected_dsl, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "generated_regression_pytest_test.py").write_text(self.pytest_generator.render(selected_dsl), encoding="utf-8")
        (out_dir / "impact_report.md").write_text(self._render_report(plan), encoding="utf-8")
        return plan

    def _match_impacted_endpoints(self, changed_files: List[ChangedFile], endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        evidence_text = "\n".join(
            [f.path + " " + " ".join(f.touched_keywords) + " " + " ".join(f.raw_snippets) for f in changed_files]
        ).lower()

        impacted: List[Dict[str, Any]] = []
        for endpoint in endpoints:
            path = endpoint["path"]
            method = endpoint["method"]
            score = 0
            reasons: List[str] = []

            tokens = self._endpoint_tokens(path, endpoint)
            for token in tokens:
                if token and token in evidence_text:
                    score += 2
                    reasons.append(f"matched token: {token}")

            # Direct path mention such as /orders or /admin/users in diff.
            if path.lower() in evidence_text:
                score += 5
                reasons.append(f"direct endpoint path mentioned: {path}")

            # Service-level broad fallback.
            if "api_service" in evidence_text and path != "/health":
                score += 1
                reasons.append("shared API service file changed")

            if score > 0:
                impacted.append({
                    "endpoint": f"{method} {path}",
                    "method": method,
                    "path": path,
                    "impact_score": score,
                    "reasons": sorted(set(reasons)),
                    "confidence": self._confidence(score),
                })

        # If nothing matched, choose health and mark low confidence.
        if not impacted:
            impacted.append({
                "endpoint": "UNKNOWN",
                "method": "UNKNOWN",
                "path": "UNKNOWN",
                "impact_score": 0,
                "reasons": ["No endpoint keyword matched. Manual review or full regression recommended."],
                "confidence": "low",
            })

        return sorted(impacted, key=lambda x: x["impact_score"], reverse=True)

    def _select_case_ids(self, impacted: List[Dict[str, Any]], test_cases: List[Dict[str, Any]]) -> List[str]:
        # Select only medium/high confidence endpoint matches.
        # Low-confidence broad matches, such as a shared api_service.py change, stay in the report
        # but do not expand the regression set unless nothing better matched.
        reliable_impacted = [
            item for item in impacted
            if item["endpoint"] != "UNKNOWN" and item.get("confidence") in {"medium", "high"}
        ]
        impacted_endpoints = {item["endpoint"] for item in reliable_impacted}
        selected: List[str] = []
        for case in test_cases:
            if case.get("endpoint") in impacted_endpoints:
                selected.append(case["case_id"])

        # If only low confidence matches exist, select all P0 to avoid unsafe under-testing.
        if not selected:
            selected = [case["case_id"] for case in test_cases if case.get("priority") == "P0"]

        return selected

    @staticmethod
    def _endpoint_tokens(path: str, endpoint: Dict[str, Any]) -> Set[str]:
        tokens: Set[str] = set()
        for part in path.replace("{", "").replace("}", "").split("/"):
            if part:
                tokens.add(part.lower())
                if part.endswith("s"):
                    tokens.add(part[:-1].lower())
        op = str(endpoint.get("operation_id", "")).lower()
        summary = str(endpoint.get("summary", "")).lower()
        for raw in [op, summary]:
            for token in ["health", "product", "products", "order", "orders", "admin", "user", "users", "stock", "permission", "role"]:
                if token in raw:
                    tokens.add(token)
        return tokens

    @staticmethod
    def _confidence(score: int) -> str:
        if score >= 5:
            return "high"
        if score >= 2:
            return "medium"
        return "low"

    @staticmethod
    def _load_json(path: Path) -> Any:
        if not path.exists():
            raise FileNotFoundError(f"Required asset file not found: {path}. Run generate-openapi first.")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _render_report(plan: Dict[str, Any]) -> str:
        files = "\n".join(
            f"- {f['path']} (+{f['added_lines']}/-{f['removed_lines']}), keywords: {', '.join(f['touched_keywords']) or 'none'}"
            for f in plan["changed_files"]
        )
        impacted = "\n".join(
            f"- {e['endpoint']} | score={e['impact_score']} | confidence={e['confidence']} | reasons={'; '.join(e['reasons'])}"
            for e in plan["impacted_endpoints"]
        )
        selected = "\n".join(f"- {cid}" for cid in plan["selected_case_ids"])
        saving_pct = round(plan["estimated_regression_saving_ratio"] * 100, 2)
        return f"""# V4 Git Diff 影响面分析与精准回归报告

## 变更文件

{files}

## 影响接口

{impacted}

## 推荐执行用例

{selected}

## 回归节省估算

- 全量用例数：{plan['total_case_count']}
- 推荐执行：{plan['selected_case_count']}
- 估算节省：{saving_pct}%

## 企业落地价值

V4 不再默认全量回归，而是根据 Git Diff、接口测试资产和风险规则自动推荐最小回归集。这样可以减少无效执行、缩短 CI 时间，并把 QA 从人工判断回归范围中解放出来。
"""
