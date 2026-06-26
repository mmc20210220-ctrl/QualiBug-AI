from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from aitestops.api_dsl_to_pytest import ApiDslToPytestGenerator
from aitestops.openapi_parser import ApiEndpoint, OpenApiParser
from aitestops.yaml_writer import dump_yaml


class ApiAssetGenerator:
    """Generate API testing assets from OpenAPI/Swagger JSON."""

    def __init__(self):
        self.parser = OpenApiParser()
        self.pytest_generator = ApiDslToPytestGenerator()

    def generate_from_openapi(self, spec_path: Path, out_dir: Path) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        endpoints = self.parser.parse_file(spec_path)
        risks = self._analyze(endpoints)
        test_cases = self._generate_test_cases(endpoints)
        dsl_cases = self._generate_dsl(endpoints)
        pytest_code = self.pytest_generator.render(dsl_cases)

        self._write_json(out_dir / "openapi_endpoints.json", [self._endpoint_to_dict(e) for e in endpoints])
        self._write_json(out_dir / "api_risks.json", risks)
        self._write_json(out_dir / "api_test_cases.json", test_cases)
        (out_dir / "api_dsl.yaml").write_text(dump_yaml(dsl_cases), encoding="utf-8")
        self._write_json(out_dir / "api_dsl.json", dsl_cases)
        (out_dir / "generated_api_pytest_test.py").write_text(pytest_code, encoding="utf-8")
        (out_dir / "generation_summary.md").write_text(
            self._summary(spec_path.name, endpoints, risks, test_cases, dsl_cases),
            encoding="utf-8",
        )
        meta = {
            "source": spec_path.name,
            "engine_used": "openapi_static_analyzer",
            "version": "v11_product_api_assets",
            "out_dir": str(out_dir),
            "endpoint_count": len(endpoints),
            "risk_count": len(risks),
            "test_case_count": len(test_cases),
            "dsl_case_count": len(dsl_cases),
            "files": [
                "openapi_endpoints.json",
                "api_risks.json",
                "api_test_cases.json",
                "api_dsl.yaml",
                "api_dsl.json",
                "generated_api_pytest_test.py",
                "generation_summary.md",
            ],
        }
        self._write_json(out_dir / "generation_meta.json", meta)
        return meta

    def _analyze(self, endpoints: List[ApiEndpoint]) -> List[Dict[str, Any]]:
        risks: List[Dict[str, Any]] = []
        for ep in endpoints:
            domain = self._domain_flags(ep)
            if ep.has_path_params:
                risks.append(
                    {
                        "endpoint": f"{ep.method} {ep.path}",
                        "risk": "Path parameter boundary and missing resource risk",
                        "priority": "P1",
                        "category": "boundary",
                        "reason": "Path parameters need valid, missing, illegal and cross-tenant coverage.",
                        **domain,
                    }
                )
            if ep.has_body:
                risks.append(
                    {
                        "endpoint": f"{ep.method} {ep.path}",
                        "risk": "Request body validation risk",
                        "priority": "P1",
                        "category": "data_validation",
                        "reason": "Required fields, invalid values and business constraints need negative tests.",
                        **domain,
                    }
                )
            if ep.requires_role or ep.security or "admin" in ep.path.lower():
                risks.append(
                    {
                        "endpoint": f"{ep.method} {ep.path}",
                        "risk": "Authentication and authorization risk",
                        "priority": "P0",
                        "category": "permission",
                        "reason": "Anonymous, normal user and admin boundaries must be verified.",
                        **domain,
                    }
                )
            if ep.method in {"POST", "PUT", "PATCH", "DELETE"}:
                risks.append(
                    {
                        "endpoint": f"{ep.method} {ep.path}",
                        "risk": "State change and data consistency risk",
                        "priority": "P1",
                        "category": "consistency",
                        "reason": "Write APIs need duplicate request, rollback and state consistency coverage.",
                        **domain,
                    }
                )

        seen = set()
        unique = []
        for item in risks:
            key = (item["endpoint"], item["risk"])
            if key not in seen:
                unique.append(item)
                seen.add(key)
        return unique

    def _generate_test_cases(self, endpoints: List[ApiEndpoint]) -> List[Dict[str, Any]]:
        cases: List[Dict[str, Any]] = []
        for index, ep in enumerate(endpoints, start=1):
            base = f"API_{index:03d}"
            flags = self._domain_flags(ep)
            cases.append(
                {
                    "case_id": f"{base}_HAPPY",
                    "title": f"{ep.method} {ep.path} happy-path contract test",
                    "type": "api_contract",
                    "priority": "P0" if self._is_money_or_order(ep) else "P1",
                    "method": ep.method,
                    "path": ep.path,
                    "expected_status": self._happy_status(ep),
                    "automation_candidate": True,
                    **flags,
                }
            )
            if ep.has_path_params:
                cases.append(
                    {
                        "case_id": f"{base}_NOT_FOUND",
                        "title": f"{ep.method} {ep.path} missing resource boundary test",
                        "type": "api_negative",
                        "priority": "P1",
                        "method": ep.method,
                        "path": ep.path,
                        "expected_status": 404,
                        "automation_candidate": True,
                        **flags,
                    }
                )
            if ep.has_body:
                cases.append(
                    {
                        "case_id": f"{base}_INVALID_BODY",
                        "title": f"{ep.method} {ep.path} invalid body validation test",
                        "type": "api_boundary",
                        "priority": "P1",
                        "method": ep.method,
                        "path": ep.path,
                        "expected_status": 400,
                        "automation_candidate": True,
                        **flags,
                    }
                )
            if ep.requires_role or ep.security or "admin" in ep.path.lower():
                cases.append(
                    {
                        "case_id": f"{base}_FORBIDDEN",
                        "title": f"{ep.method} {ep.path} forbidden access test",
                        "type": "api_permission",
                        "priority": "P0",
                        "method": ep.method,
                        "path": ep.path,
                        "expected_status": 403,
                        "automation_candidate": True,
                        **flags,
                    }
                )
        return cases

    def _generate_dsl(self, endpoints: List[ApiEndpoint]) -> List[Dict[str, Any]]:
        dsl: List[Dict[str, Any]] = []
        for index, ep in enumerate(endpoints, start=1):
            base = f"API_{index:03d}"
            happy_path = self._example_path(ep, valid=True)
            dsl.append(
                {
                    "case_id": f"{base}_HAPPY",
                    "title": f"{ep.method} {ep.path} happy path",
                    "method": ep.method,
                    "path": happy_path,
                    "headers": self._headers(ep, happy=True),
                    "body": self._body(ep, valid=True),
                    "expected_status": self._happy_status(ep),
                    "assertions": self._happy_assertions(ep),
                }
            )
            if ep.has_path_params:
                dsl.append(
                    {
                        "case_id": f"{base}_NOT_FOUND",
                        "title": f"{ep.method} {ep.path} not found resource",
                        "method": ep.method,
                        "path": self._example_path(ep, valid=False),
                        "headers": self._headers(ep, happy=True),
                        "body": self._body(ep, valid=True),
                        "expected_status": 404,
                        "assertions": [{"target": "body.error_code", "operator": "exists"}],
                    }
                )
            if ep.has_body:
                dsl.append(
                    {
                        "case_id": f"{base}_INVALID_BODY",
                        "title": f"{ep.method} {ep.path} invalid body",
                        "method": ep.method,
                        "path": happy_path,
                        "headers": self._headers(ep, happy=True),
                        "body": self._body(ep, valid=False),
                        "expected_status": 400,
                        "assertions": [{"target": "body.error_code", "operator": "exists"}],
                    }
                )
            if ep.requires_role or ep.security or "admin" in ep.path.lower():
                dsl.append(
                    {
                        "case_id": f"{base}_FORBIDDEN",
                        "title": f"{ep.method} {ep.path} forbidden for normal user",
                        "method": ep.method,
                        "path": happy_path,
                        "headers": {"X-Role": "user"},
                        "body": self._body(ep, valid=True),
                        "expected_status": 403,
                        "assertions": [{"target": "body.error_code", "operator": "equals", "value": "FORBIDDEN"}],
                    }
                )
        return dsl

    @staticmethod
    def _domain_flags(ep: ApiEndpoint) -> Dict[str, bool]:
        text = f"{ep.path} {ep.operation_id} {ep.summary}".lower()
        return {
            "requires_auth": bool(ep.security or ep.requires_role or "admin" in text),
            "involves_amount": any(k in text for k in ["pay", "payment", "coupon", "discount", "amount", "checkout", "order"]),
            "involves_inventory": any(k in text for k in ["inventory", "stock", "product", "cart"]),
            "involves_order": "order" in text or "checkout" in text,
            "involves_user_permission": bool(ep.security or ep.requires_role or "admin" in text or "user" in text),
        }

    @staticmethod
    def _is_money_or_order(ep: ApiEndpoint) -> bool:
        flags = ApiAssetGenerator._domain_flags(ep)
        return flags["involves_amount"] or flags["involves_order"]

    @staticmethod
    def _happy_status(ep: ApiEndpoint) -> int:
        if "201" in ep.responses:
            return 201
        if "204" in ep.responses:
            return 204
        return 200

    @staticmethod
    def _headers(ep: ApiEndpoint, happy: bool = True) -> Dict[str, str]:
        if ep.requires_role == "admin" or "admin" in ep.path.lower():
            return {"X-Role": "admin" if happy else "user"}
        if ep.security:
            return {"X-Role": "user"}
        return {}

    @staticmethod
    def _example_path(ep: ApiEndpoint, valid: bool) -> str:
        path = ep.path
        replacements = {
            "product_id": "p-1001" if valid else "missing-product",
            "order_id": "o-1001" if valid else "missing-order",
            "user_id": "u-1001" if valid else "missing-user",
            "id": "1" if valid else "9999",
        }
        for param in ep.parameters:
            if param.get("in") == "path":
                name = param["name"]
                path = path.replace("{" + name + "}", replacements.get(name, "1" if valid else "9999"))
        return path

    @staticmethod
    def _body(ep: ApiEndpoint, valid: bool) -> Any:
        if not ep.has_body:
            return None
        text = f"{ep.path} {ep.operation_id}".lower()
        if "cart" in text:
            return {"product_id": "p-1001", "quantity": 1} if valid else {"product_id": "", "quantity": 0}
        if "checkout" in text or "order" in text:
            return {"coupon_code": "VIP20"} if valid else {"coupon_code": 123}
        return {"name": "demo"} if valid else {}

    @staticmethod
    def _happy_assertions(ep: ApiEndpoint) -> List[Dict[str, Any]]:
        if ep.path == "/health":
            return [{"target": "body.status", "operator": "equals", "value": "ok"}]
        if "products" in ep.path and ep.method == "GET":
            return [{"target": "body.items.length", "operator": "gte", "value": 1}]
        if "orders" in ep.path and ep.method == "POST":
            return [{"target": "body.order_id", "operator": "exists"}]
        return []

    @staticmethod
    def _endpoint_to_dict(ep: ApiEndpoint) -> Dict[str, Any]:
        return {
            "method": ep.method,
            "path": ep.path,
            "operation_id": ep.operation_id,
            "summary": ep.summary,
            "parameters": ep.parameters,
            "has_body": ep.has_body,
            "responses": list(ep.responses.keys()),
            "security": ep.security,
            "requires_role": ep.requires_role,
        }

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _summary(source_name: str, endpoints: List[ApiEndpoint], risks: List[Dict[str, Any]], test_cases: List[Dict[str, Any]], dsl_cases: List[Dict[str, Any]]) -> str:
        endpoint_text = "\n".join(f"- {e.method} {e.path} ({e.operation_id})" for e in endpoints)
        risk_text = "\n".join(f"- [{r['priority']}] {r['endpoint']} - {r['risk']}: {r['reason']}" for r in risks)
        case_text = "\n".join(f"- {c['case_id']} {c['title']} ({c['priority']}, {c['type']})" for c in test_cases)
        return f"""# OpenAPI API Test Asset Summary

## Source

{source_name}

## Endpoints

{endpoint_text}

## Risks

{risk_text}

## Generated Cases

{case_text}

## API DSL

{len(dsl_cases)} DSL cases generated. Python tests are rendered by the template engine, not by the LLM.
"""
