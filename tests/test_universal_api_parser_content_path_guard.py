from __future__ import annotations

import json

from ai_test_asset_center.universal_api_parser import parse_to_openapi


def test_compact_large_openapi_json_is_content_not_a_filesystem_path() -> None:
    """A one-line OpenAPI payload may be much longer than PATH_MAX.

    This is the shape produced by an internal normalize/render hop for large
    customer contracts.  Parsing it must never attempt ``stat(payload)``.
    """

    paths = {
        f"/api/resources/{index}": {
            "get": {
                "operationId": f"getResource{index}",
                "responses": {"200": {"description": "ok"}},
            }
        }
        for index in range(180)
    }
    compact = json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "large contract", "version": "1.0.0"},
            "paths": paths,
        },
        separators=(",", ":"),
    )

    assert "\n" not in compact
    assert len(compact) > 4096

    parsed = parse_to_openapi(compact)

    assert parsed["openapi"] == "3.0.3"
    assert len(parsed["paths"]) == 180
    assert parsed["paths"]["/api/resources/179"]["get"]["operationId"] == "getResource179"
