from __future__ import annotations

from typing import Any, Dict, List


class ApiDslToPytestGenerator:
    """Convert generated API DSL into executable Pytest.

    AI/analysis layer produces API intent; this generator owns executable code.
    """

    def render(self, dsl_cases: List[Dict[str, Any]]) -> str:
        blocks = [self._header()]
        for case in dsl_cases:
            blocks.append(self._render_case(case))
        return "\n\n".join(blocks) + "\n"

    def _header(self) -> str:
        return '''"""Generated API tests by AI Test Asset Center V3.

Do not hand-edit this file in enterprise mode.
Change OpenAPI spec, API DSL, or generator rules instead.
"""

from pathlib import Path
import sys


def _add_project_root_to_path():
    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "demo_system").exists():
            root = str(parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            return


_add_project_root_to_path()

from demo_system.api_service import ApiService


def read_path(data, dotted_path):
    value = data
    for part in dotted_path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            if part == "length":
                value = len(value)
            else:
                value = value[int(part)]
        else:
            value = getattr(value, part)
    return value
'''

    def _render_case(self, case: Dict[str, Any]) -> str:
        func_name = f"test_{case['case_id'].lower()}"
        method = case["method"]
        path = case["path"]
        headers = case.get("headers", {})
        body = case.get("body", None)
        expected_status = case["expected_status"]

        lines = [
            f"def {func_name}():",
            f"    \"\"\"{case['title']}\"\"\"",
            "    service = ApiService()",
            f"    response = service.request({method!r}, {path!r}, json={body!r}, headers={headers!r})",
            f"    assert response['status_code'] == {expected_status}",
        ]

        for assertion in case.get("assertions", []):
            target = assertion["target"]
            operator = assertion.get("operator", "equals")
            expected = assertion.get("value")
            expr = f"read_path(response, {target!r})"
            if operator == "equals":
                lines.append(f"    assert {expr} == {expected!r}")
            elif operator == "exists":
                lines.append(f"    assert {expr} is not None")
            elif operator == "gte":
                lines.append(f"    assert {expr} >= {expected!r}")
            else:
                raise ValueError(f"Unsupported assertion operator: {operator}")

        return "\n".join(lines)
