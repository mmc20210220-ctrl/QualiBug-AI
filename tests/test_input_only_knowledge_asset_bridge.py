from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center import blind_project_runner
from ai_test_asset_center.blind_project_runner import _compile_project_context
from ai_test_asset_center.input_grounded_candidate_compiler import compile_grounded_candidates


def test_copy_input_only_allows_public_schema_sql_but_blocks_seed_sql(tmp_path: Path) -> None:
    source_input_dir = tmp_path / "suite" / "demo_project" / "input"
    source_input_dir.mkdir(parents=True)
    (source_input_dir / "PRD.md").write_text("# PRD\n", encoding="utf-8")
    (source_input_dir / "schema.sql").write_text("CREATE TABLE orders(id int);", encoding="utf-8")
    (source_input_dir / "seed.sql").write_text("INSERT INTO orders VALUES (1);", encoding="utf-8")

    result = blind_project_runner._copy_input_only(source_input_dir, tmp_path / "normalized" / "input")

    assert sorted(item["file"] for item in result["allowed_input_files"]) == ["PRD.md", "schema.sql"]
    assert result["blocked_files"] == ["seed.sql"]


def test_copy_input_only_does_not_block_project_name_with_seed_substring(tmp_path: Path) -> None:
    source_input_dir = tmp_path / "suite" / "customer_seedbank_portal" / "input"
    source_input_dir.mkdir(parents=True)
    (source_input_dir / "PRD.md").write_text("# PRD\n", encoding="utf-8")

    result = blind_project_runner._copy_input_only(source_input_dir, tmp_path / "normalized" / "input")

    assert [item["file"] for item in result["allowed_input_files"]] == ["PRD.md"]
    assert result["blocked_files"] == []


def test_sync_input_only_knowledge_asset_ingests_platform_inputs(monkeypatch, tmp_path: Path) -> None:
    project_id = "demo"
    input_dir = tmp_path / "platform_inputs" / project_id
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# PRD\nTenant order flow", encoding="utf-8")
    (input_dir / "API_DOCS.md").write_text("GET /orders", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_ingest(project: str, file_paths, root: Path, actor):
        captured["project"] = project
        captured["files"] = sorted(str(Path(path).relative_to(root)).replace("\\", "/") for path in file_paths)
        captured["actor"] = actor
        return {"created": [{"source_id": "src_1"}], "duplicates": [], "errors": [], "rebuild_recommended": True}

    def fake_load(*args, **kwargs):
        return None

    def fake_build(project: str, root: Path):
        return {"project_id": project, "summary": {"active_source_count": 2}, "interfaces": []}

    monkeypatch.setattr(blind_project_runner, "ingest_enterprise_knowledge_files", fake_ingest)
    monkeypatch.setattr(blind_project_runner, "load_enterprise_business_knowledge_asset", fake_load)
    monkeypatch.setattr(blind_project_runner, "build_enterprise_business_knowledge_asset", fake_build)

    result = blind_project_runner._sync_input_only_knowledge_asset(project_id, tmp_path)

    assert result["enabled"] is True
    assert result["summary"]["active_source_count"] == 2
    assert captured["project"] == project_id
    assert captured["files"] == [
        "platform_inputs/demo/API_DOCS.md",
        "platform_inputs/demo/PRD.md",
    ]
    assert captured["actor"] == {"name": "blind_project_runner", "role": "project_owner"}


def test_compile_project_context_uses_knowledge_asset_when_input_is_sparse(tmp_path: Path) -> None:
    project_id = "demo"
    input_dir = tmp_path / "platform_inputs" / project_id
    input_dir.mkdir(parents=True)
    (input_dir / "prd.md").write_text("# Empty PRD\n", encoding="utf-8")
    (input_dir / "api.md").write_text("", encoding="utf-8")
    (input_dir / "openapi.json").write_text("{}", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "GET", "path": "/orders/{order_id}", "summary": "Get order detail", "parameters": ["order_id", "tenant_id"]},
            {"method": "POST", "path": "/orders", "summary": "Create order", "parameters": ["tenant_id"]},
        ],
        "data_tables": [
            {"name": "orders", "columns": ["order_id", "tenant_id", "status", "amount"]},
        ],
        "field_dictionary": [
            {"table": "order_items", "field": "quantity"},
        ],
    }

    summary = _compile_project_context(project_id, tmp_path, knowledge_asset=knowledge_asset)

    assert summary["api_count"] >= 2
    assert summary["entity_count"] >= 2
    assert summary["knowledge_data_dictionary_count"] == 2
    assert summary["knowledge_interface_count"] == 2
    payload = json.loads((tmp_path / "platform_outputs" / project_id / "input_only_project_context.json").read_text(encoding="utf-8"))
    assert any(api["path"] == "/orders/{order_id}" for api in payload["apis"])


def test_compile_grounded_candidates_uses_knowledge_asset_when_documents_are_sparse(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Minimal input\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/transactions",
                "summary": "Create payment transaction with tenant ownership and idempotency key",
                "parameters": ["tenant_id", "idempotency_key"],
                "tags": ["payments"],
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:tenant",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "permission_boundary",
                "statement": "Transactions must enforce tenant ownership and reject cross-tenant access.",
            },
            {
                "rule_id": "rule:idempotency",
                "source_id": "src_rules",
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "statement": "Each payment submission must be idempotent and duplicate retries cannot create extra side effects.",
            },
        ],
        "business_objects": [{"object": "transactions"}],
        "roles": [{"role": "finance_manager"}],
        "state_machines": [{"states": ["draft", "submitted", "completed"]}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo", knowledge_asset=knowledge_asset)

    assert payload["domain_model"]["knowledge_asset_attached"] is True
    assert payload["summary"]["knowledge_asset_interface_count"] == 1
    assert payload["summary"]["candidate_count"] >= 1
    assert any(candidate["risk_type"] == "idempotency_replay_probe" for candidate in payload["candidates"])
    assert any(
        ref["file"].startswith("knowledge_asset:")
        for candidate in payload["candidates"]
        for ref in candidate["source_refs"]
    )
