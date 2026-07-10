from __future__ import annotations

import json
import re
from types import SimpleNamespace

from ai_test_asset_center.business_state_graph import _api_facts
from benchmark_runtime.runtime_target import BenchmarkRuntime, RuntimeBug


def test_openapi_server_base_path_is_preserved_in_executable_endpoint() -> None:
    api = json.dumps({
        "openapi": "3.0.3",
        "servers": [{"url": "https://target.example/api/v1/commerce"}],
        "paths": {
            "/actions/submit": {
                "post": {"operationId": "submitAction", "responses": {"200": {"description": "ok"}}},
            },
        },
    })

    _, _, endpoints = _api_facts(api, re.compile(r"status|state", re.I))

    assert endpoints[0]["path"] == "/api/v1/commerce/actions/submit"
    assert endpoints[0]["operation_id"] == "submitAction"


def test_runtime_response_never_exposes_hidden_oracle_identifiers(tmp_path) -> None:
    runtime = BenchmarkRuntime(tmp_path)
    bug = RuntimeBug(
        project_name="hidden-project",
        project_slug="hidden-slug",
        bug_id="SECRET-BUG-1",
        severity="P0",
        category="C01",
        method="POST",
        path="/api/action",
        pattern=re.compile(r"^/api/action$"),
        title="hidden title",
        expected_behavior="hidden expected",
        actual_bug_behavior="hidden actual",
    )

    response = runtime.response_for(
        bug,
        SimpleNamespace(headers={"X-Tenant-Id": "t-a"}),
        {"object_id": "x"},
    )
    serialized = json.dumps(response, ensure_ascii=False)

    for secret in ("SECRET-BUG-1", "hidden-project", "hidden-slug", "hidden expected"):
        assert secret not in serialized
    assert "observed_bug_id" not in response
    assert response["resource"]["status"] == "accepted_despite_negative_probe"
