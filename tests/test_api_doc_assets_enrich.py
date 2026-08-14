"""Regression for api_doc_assets.enrich_api_spec_text schema preservation.

The enrichment must return a machine-parseable primary (OpenAPI) verbatim when
the merged endpoint catalog is a subset of it — even when merged rows outnumber
the primary's own markdown-derived rows because several sources describe the
same endpoints. Downgrading to the markdown render drops request schemas and
role constraints, which later blocks every body-bound obligation with
``BODY_PARAMETER_NOT_SOURCE_BOUND``.
"""
from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.api_doc_assets import enrich_api_spec_text


_OPENAPI_PRIMARY = """openapi: 3.0.3
info:
  title: Test API
  version: 1.0.0
paths:
  /api/users/addresses:
    post:
      operationId: user_service_post__addresses
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                receiver:
                  type: string
              required:
                - receiver
      x-required-roles:
        - buyer
      responses:
        '201':
          description: created
"""


def _input_dir(tmp_path: Path) -> Path:
    d = tmp_path / "platform_inputs" / "project"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_enrich_preserves_openapi_primary_when_merged_is_subset(tmp_path):
    # The project dir adds markdown sources that describe the SAME endpoint with
    # no new paths. Merged rows (markdown + primary) outnumber the primary's own
    # markdown-derived rows, which used to force a lossy markdown downgrade.
    (_input_dir(tmp_path) / "API_SPEC.md").write_text(
        "### POST /api/users/addresses\n\n创建地址。\n",
        encoding="utf-8",
    )

    result = enrich_api_spec_text(tmp_path, "project", _OPENAPI_PRIMARY)

    # The OpenAPI primary is returned verbatim (schema + roles survive).
    assert "requestBody" in result
    assert "x-required-roles" in result
    assert result.strip().startswith("openapi: 3.0.3")
    assert "receiver" in result


def test_enrich_still_merges_new_paths_from_openapi(tmp_path):
    # When a project OpenAPI file declares an endpoint the primary lacks, the
    # merged markdown render must include it (coverage never silently drops).
    (_input_dir(tmp_path) / "openapi.yaml").write_text(
        """openapi: 3.0.3
info:
  title: Extra
  version: 1.0.0
paths:
  /api/extra/endpoint:
    get:
      operationId: extra_get
      responses:
        '200':
          description: ok
""",
        encoding="utf-8",
    )
    primary = "### POST /api/users/addresses\n\n创建地址。\n"

    result = enrich_api_spec_text(tmp_path, "project", primary)

    assert "/api/extra/endpoint" in result
    assert "/api/users/addresses" in result
