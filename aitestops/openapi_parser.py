from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ApiEndpoint:
    method: str
    path: str
    operation_id: str
    summary: str = ""
    parameters: List[Dict[str, Any]] = field(default_factory=list)
    request_body: Dict[str, Any] = field(default_factory=dict)
    responses: Dict[str, Any] = field(default_factory=dict)
    security: List[Dict[str, Any]] = field(default_factory=list)
    requires_role: Optional[str] = None

    @property
    def has_path_params(self) -> bool:
        return any(p.get("in") == "path" for p in self.parameters)

    @property
    def has_body(self) -> bool:
        return bool(self.request_body)


class OpenApiParser:
    """Small OpenAPI 3.x parser for the V3 demo.

    It intentionally avoids heavy dependencies. JSON is supported out of the box.
    YAML can be added later with PyYAML if the enterprise environment needs it.
    """

    SUPPORTED_METHODS = {"get", "post", "put", "patch", "delete"}

    def parse_file(self, spec_path: Path) -> List[ApiEndpoint]:
        raw = spec_path.read_text(encoding="utf-8")
        if spec_path.suffix.lower() not in {".json"}:
            raise ValueError("V3 demo currently supports OpenAPI JSON. Please export swagger/openapi as .json")
        spec = json.loads(raw)
        return self.parse(spec)

    def parse(self, spec: Dict[str, Any]) -> List[ApiEndpoint]:
        paths = spec.get("paths", {})
        endpoints: List[ApiEndpoint] = []
        for path, path_item in paths.items():
            common_params = path_item.get("parameters", [])
            for method, operation in path_item.items():
                if method.lower() not in self.SUPPORTED_METHODS:
                    continue
                parameters = [*common_params, *operation.get("parameters", [])]
                endpoints.append(
                    ApiEndpoint(
                        method=method.upper(),
                        path=path,
                        operation_id=operation.get("operationId") or self._fallback_operation_id(method, path),
                        summary=operation.get("summary", ""),
                        parameters=parameters,
                        request_body=operation.get("requestBody", {}),
                        responses=operation.get("responses", {}),
                        security=operation.get("security", spec.get("security", [])),
                        requires_role=operation.get("x-requires-role"),
                    )
                )
        if not endpoints:
            raise ValueError("No API endpoints found in OpenAPI spec")
        return endpoints

    @staticmethod
    def _fallback_operation_id(method: str, path: str) -> str:
        clean = path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root"
        return f"{method.lower()}_{clean}"
