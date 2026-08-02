from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center import (
    _authoritative_rule_to_interface_edges,
    _classify_source,
    _declared_project_source_files,
    _links_by_exact_source_section,
    _links_by_exclusive_contract_fields,
    _links_by_overlap,
    _links_by_same_source_exclusive_module_neighbors,
    _parse_source,
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_documents,
)


MARKDOWN_API_DOC = """# API Docs

### POST `/orders`
Create an order.
The amount must be positive.

| Field | Type | Description |
| --- | --- | --- |
| order_id | string | order identifier |
| amount | number | payable amount |

```json
{"order_id": "ORD-1", "amount": 99.5}
```

### GET `/orders/{order_id}`
Query order detail.

| Field | Type | Description |
| --- | --- | --- |
| order_id | string | order identifier |
```
"""


SOURCE_CODE_API_DOC = """
const express = require('express');
const app = express();
app.get('/api/orders', listOrders);
app.post('/api/orders', createOrder);
router.patch('/api/orders/:id', patchOrder);
// app.delete('/api/orders/:id', deleteOrder);

@app.route('/api/users/addresses', methods=['GET', 'POST'])
def addresses():
    pass

@GetMapping('/api/orders/{id}')
public Order getOrder() { return null; }
"""


FIELD_DICTIONARY_DOC = """# Table: orders

| Field | Type | Description | Required |
| --- | --- | --- | --- |
| order_id | string | order identifier | yes |
| amount | decimal | payable amount | yes |
| tenant_id | string | tenant scope | no |
"""


SVG_DOC = """<svg xmlns="http://www.w3.org/2000/svg">
  <title>Orders Checkout</title>
  <desc>Checkout page with loading state and submit action</desc>
  <g id="order-form"></g>
  <g data-name="summary-card"></g>
  <text>Submit Order</text>
  <text>Loading</text>
</svg>
"""


def test_classify_source_distinguishes_new_document_types() -> None:
    prd_text = "# PRD\nThis product requirement references an API appendix."

    assert _classify_source("PRD.md", prd_text) == "prd"
    assert _classify_source(
        "BUSINESS_RULES.md",
        "# Business rules\n- A disabled account cannot sign in.",
    ) == "business_rules"
    assert _classify_source("API_DOCS.md", MARKDOWN_API_DOC) == "markdown_api"
    assert _classify_source("db_field_dictionary.csv", "table,field,type,description\norders,amount,decimal,payable amount\n") == "db_field_dictionary"
    assert _classify_source("checkout_flow.svg", SVG_DOC) == "uiux_svg"


def test_classify_and_parse_source_code_preserves_declared_http_routes() -> None:
    source_type = _classify_source("order_controller.js", SOURCE_CODE_API_DOC)
    parsed = _parse_source(
        SOURCE_CODE_API_DOC.encode("utf-8"),
        "order_controller.js",
        source_type,
        "src_order_controller",
    )

    assert source_type == "source_code"
    routes = {(row["method"], row["path"]) for row in parsed["operations"]}
    assert routes == {
        ("GET", "/api/orders"),
        ("POST", "/api/orders"),
        ("PATCH", "/api/orders/:id"),
        ("GET", "/api/users/addresses"),
        ("POST", "/api/users/addresses"),
        ("GET", "/api/orders/{id}"),
    }
    assert all(row["source_kind"] == "source_code" for row in parsed["operations"])
    assert all(":line:" in row["source_locator"] for row in parsed["operations"])
    assert all("request_body" not in row for row in parsed["operations"])


def test_parse_source_extracts_markdown_api_field_dictionary_and_svg() -> None:
    api = _parse_source(MARKDOWN_API_DOC.encode("utf-8"), "API_DOCS.md", "markdown_api", "src_api")
    field_dict = _parse_source(FIELD_DICTIONARY_DOC.encode("utf-8"), "field_dictionary.md", "db_field_dictionary", "src_dict")
    svg = _parse_source(SVG_DOC.encode("utf-8"), "checkout_flow.svg", "uiux_svg", "src_ui")

    assert len(api["operations"]) == 2
    assert {row["path"] for row in api["operations"]} == {"/orders", "/orders/{order_id}"}
    assert "order_id" in api["operations"][0]["parameters"]
    assert "amount" in api["operations"][0]["parameters"]

    assert len(field_dict["tables"]) == 1
    assert field_dict["tables"][0]["name"] == "orders"
    assert {row["field"] for row in field_dict["field_dictionary"]} >= {"order_id", "amount", "tenant_id"}
    tenant_row = next(row for row in field_dict["field_dictionary"] if row["field"] == "tenant_id")
    assert tenant_row["required"] is False

    assert len(svg["ui_specs"]) == 1
    assert svg["ui_specs"][0]["name"] == "Orders Checkout"
    assert "Submit Order" in svg["ui_specs"][0]["text_labels"]
    assert "Loading" in svg["ui_specs"][0]["states"]


def test_field_dictionary_json_preserves_required_false_in_normalized_evidence() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _field_dictionary_entries,
    )

    rows = _field_dictionary_entries(
        '{"fields":[{"table":"orders","field":"warehouse_id","required":false}]}',
        {
            "fields": [
                {"table": "orders", "field": "warehouse_id", "required": False},
                {"table": "orders", "field": "sku", "required": True},
            ]
        },
        "src_fields",
    )
    by_field = {row["field"]: row for row in rows}
    warehouse = by_field["warehouse_id"]
    assert warehouse["required"] is False
    assert warehouse["normalized_evidence"] == (
        "table=orders; field=warehouse_id; required=false"
    )
    assert warehouse["evidence_kind"] == "NORMALIZED_STRUCTURED_DECLARATION"
    assert warehouse["evidence_derivation"] == (
        "normalized_field_dictionary_projection"
    )
    assert "quote" not in warehouse
    assert "source_excerpt" not in warehouse

    sku = by_field["sku"]
    assert sku["required"] is True
    assert sku["normalized_evidence"] == "table=orders; field=sku; required=true"
    assert sku["evidence_kind"] == "NORMALIZED_STRUCTURED_DECLARATION"
    assert sku["evidence_derivation"] == "normalized_field_dictionary_projection"
    assert "quote" not in sku
    assert "source_excerpt" not in sku


def test_permission_entries_prefer_source_evidence_string() -> None:
    from ai_test_asset_center.enterprise_knowledge_center._parsing import (
        _permission_entries,
    )

    rows = _permission_entries(
        json.dumps(
            {
                "permissions": [
                    {
                        "permission_id": "perm:a",
                        "role": "operator",
                        "resource": "orders",
                        "decision": "allow",
                        "actions": ["write"],
                        "evidence": "operator / orders / allow",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        {
            "permissions": [
                {
                    "permission_id": "perm:a",
                    "role": "operator",
                    "resource": "orders",
                    "decision": "allow",
                    "actions": ["write"],
                    "evidence": "operator / orders / allow",
                }
            ]
        },
        "src_perm",
    )
    assert rows
    assert rows[0]["evidence"] == "operator / orders / allow"
    assert "permission_id" not in rows[0]["evidence"]


def test_parse_source_state_machine_ignores_sentence_and_layout_noise() -> None:
    workflow_doc = """# Workflow

CREATED -> PAID -> SHIPPED -> FINISHED
游客购物车应在登录后自动合并到用户购物车。
移动端 360px 到 430px 宽度必须可用。
普通用户只能看到自己的订单。
"""
    parsed = _parse_source(workflow_doc.encode("utf-8"), "PRD.md", "prd", "src_workflow")

    assert len(parsed["state_machines"]) == 1
    machine = parsed["state_machines"][0]
    assert machine["states"] == ["CREATED", "PAID", "SHIPPED", "FINISHED"]
    assert machine["transitions"] == [
        {"from": "CREATED", "to": "PAID"},
        {"from": "PAID", "to": "SHIPPED"},
        {"from": "SHIPPED", "to": "FINISHED"},
    ]


def test_parse_source_keeps_state_machines_bound_to_their_sections() -> None:
    workflow_doc = """# Workflow

## Purchase state machine
CREATED -> APPROVED -> CLOSED

## Refund state machine
REQUESTED -> APPROVED -> REFUNDED

## Notes
The two workflows are independent.
"""
    parsed = _parse_source(
        workflow_doc.encode("utf-8"),
        "PRD.md",
        "prd",
        "src_sectioned_workflows",
    )

    machines = {row["object"]: row for row in parsed["state_machines"]}
    assert set(machines) == {"purchase", "refund"}
    assert machines["purchase"]["transitions"] == [
        {"from": "CREATED", "to": "APPROVED"},
        {"from": "APPROVED", "to": "CLOSED"},
    ]
    assert machines["refund"]["transitions"] == [
        {"from": "REQUESTED", "to": "APPROVED"},
        {"from": "APPROVED", "to": "REFUNDED"},
    ]


def test_parse_source_resolves_state_machine_object_through_semantic_lexicon() -> None:
    workflow_doc = """# Product behavior

## 订单状态机
CREATED -> PAID
"""
    parsed = _parse_source(
        workflow_doc.encode("utf-8"),
        "PRD.md",
        "prd",
        "src_localized_workflow",
    )

    assert parsed["state_machines"][0]["object"] == "order"


def test_parse_source_separates_forbidden_from_allowed_state_transitions() -> None:
    workflow_doc = """# Product behavior

## Order state machine
CREATED -> PAID -> COMPLETED

Forbidden transitions:
- COMPLETED -> PAID
- COMPLETED -> CREATED
"""
    parsed = _parse_source(
        workflow_doc.encode("utf-8"),
        "PRD.md",
        "prd",
        "src_forbidden_transitions",
    )

    machine = parsed["state_machines"][0]
    assert machine["transitions"] == [
        {"from": "CREATED", "to": "PAID"},
        {"from": "PAID", "to": "COMPLETED"},
    ]
    assert machine["forbidden_transitions"] == [
        {"from": "COMPLETED", "to": "PAID"},
        {"from": "COMPLETED", "to": "CREATED"},
    ]


def test_parse_source_extracts_idempotency_rule_without_must_keywords() -> None:
    doc = """# Scenarios

- 订单提交可重试，重复提交不能产生额外副作用。
"""
    parsed = _parse_source(doc.encode("utf-8"), "acceptance_scenarios.md", "collaboration_document", "src_scenarios")

    assert any(row.get("rule_type") == "idempotency" for row in parsed.get("rules") or [])


def test_parse_source_extracts_async_event_rule_without_must_keywords() -> None:
    doc = """# Scenarios

- 订单创建后会发送通知消息，失败时进入重试队列并补发短信。
"""
    parsed = _parse_source(doc.encode("utf-8"), "acceptance_scenarios.md", "collaboration_document", "src_async")

    assert any(row.get("rule_type") == "async_event" for row in parsed.get("rules") or [])
    assert any(row.get("risk_type") == "async_event" for row in parsed.get("rules") or [])


def test_business_rule_document_extracts_declarative_negative_rule() -> None:
    doc = """# Business rules

- Customer UI does not display draft, archived, or internal records.
"""
    parsed = _parse_source(
        doc.encode("utf-8"),
        "BUSINESS_RULES.md",
        "business_rules",
        "src_rules",
    )

    rule = next(
        row
        for row in parsed["rules"]
        if "does not display" in row.get("statement", "")
    )
    assert rule["semantic_frame"]["modality"] == "PROHIBITED"
    assert rule["semantic_frame"]["polarity"] == "negative"
    assert rule["semantic_frame"]["subject"] == "Customer UI"
    assert rule["semantic_frame"]["behavior"] == (
        "display draft, archived, or internal records"
    )
    assert rule["source_locator"].startswith("line:")


def test_business_rule_semantics_distinguish_negation_from_different() -> None:
    doc = """# 业务规则

- 用户端不展示下架商品、草稿商品、内部商品。
- 管理端不同角色应展示不同菜单。
- 已支付订单不能直接取消，只能发起退款。
"""
    parsed = _parse_source(
        doc.encode("utf-8"),
        "BUSINESS_RULES.md",
        "business_rules",
        "src_rules",
    )
    rules = {row["statement"]: row["semantic_frame"] for row in parsed["rules"]}

    hidden = rules["用户端不展示下架商品、草稿商品、内部商品"]
    assert hidden["modality"] == "PROHIBITED"
    assert hidden["subject"] == "用户端"
    assert hidden["behavior"] == "展示下架商品、草稿商品、内部商品"

    role_menu = rules["管理端不同角色应展示不同菜单"]
    assert role_menu["modality"] == "REQUIRED"
    assert role_menu["subject"] == "管理端不同角色"
    assert role_menu["behavior"] == "不同菜单"

    cancellation = rules["已支付订单不能直接取消，只能发起退款"]
    assert cancellation["subject"] == "已支付订单"
    assert cancellation["behavior"] == "直接取消，只能发起退款"


def test_rule_extraction_keeps_semantics_and_drops_tabular_credentials() -> None:
    doc = """# Test accounts

| Role | Email | Password | Notes |
| --- | --- | --- | --- |
| Disabled buyer | buyer@example.com | Test@123456 | status is DISABLED, cannot sign in or submit |
"""
    parsed = _parse_source(
        doc.encode("utf-8"),
        "TEST_ACCOUNTS.md",
        "permission_matrix",
        "src_accounts",
    )

    rule = next(
        row for row in parsed["rules"] if "status is DISABLED" in row["statement"]
    )
    assert rule["statement"] == "status is DISABLED, cannot sign in or submit"
    assert "buyer@example.com" not in rule["statement"]
    assert "Test@123456" not in rule["statement"]
    assert rule["semantic_frame"]["condition"] == "status is DISABLED"
    assert rule["semantic_frame"]["subject"] == ""
    assert rule["semantic_frame"]["modality"] == "PROHIBITED"
    assert rule["semantic_frame"]["behavior"] == "sign in or submit"


def test_rule_semantic_frame_uses_the_same_redacted_source_as_statement() -> None:
    parsed = _parse_source(
        b"# Business rules\nThe user must use Bearer abcdefghijklmnop to call the endpoint.\n",
        "BUSINESS_RULES.md",
        "business_rules",
        "src_redacted_semantics",
    )

    rule = parsed["rules"][0]
    statement = rule["statement"]
    frame = rule["semantic_frame"]

    assert "abcdefghijklmnop" not in statement
    assert "abcdefghijklmnop" not in frame["behavior"]
    assert frame["behavior"].casefold() in statement.casefold()


def test_test_data_notes_are_not_permission_authority() -> None:
    doc = """# Test accounts

| Role | Email | Password | Notes |
| --- | --- | --- | --- |
| Disabled buyer | buyer@example.com | Test@123456 | status is DISABLED, cannot sign in or submit |
"""
    parsed = _parse_source(
        doc.encode("utf-8"),
        "TEST_ACCOUNTS.md",
        "test_data",
        "src_test_accounts",
    )

    assert parsed["permissions"] == []
    assert any(
        "status is DISABLED" in row["statement"] for row in parsed["rules"]
    )


def test_composed_test_data_block_cannot_create_permission_denial() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.source_ingestion import (
        parse_enterprise_source,
    )

    doc = """<!-- qualibug:source source_id=permissions source_type=permission_matrix -->
# Role permissions

| Role | Permissions |
| --- | --- |
| buyer | create orders |

<!-- qualibug:source source_id=accounts source_type=test_data -->
# Test accounts

| Role | Email | Password | Notes |
| --- | --- | --- | --- |
| Disabled buyer | buyer@example.com | Test@123456 | status is DISABLED, cannot sign in or submit |
"""
    parsed = parse_enterprise_source(
        doc.encode("utf-8"),
        "composed.md",
        "markdown_api",
        "src_composed",
    )

    assert any(
        row.get("role") == "buyer"
        and row.get("decision") == "allow"
        for row in parsed["permissions"]
    )
    assert not any(row.get("decision") == "deny" for row in parsed["permissions"])


def test_permission_table_rows_are_explicit_grants_and_keep_narrative_denials() -> None:
    doc = """# Role permissions

| Role | Permissions |
| --- | --- |
| buyer | view products, manage own cart |
| finance | view reports |

Permission constraints:
- finance 不能修改商品和库存。
- auditor cannot modify any business data.
"""
    parsed = _parse_source(
        doc.encode("utf-8"),
        "permissions.md",
        "permission_matrix",
        "src_permissions",
    )

    permissions = parsed["permissions"]
    assert any(
        row.get("role") == "buyer"
        and row.get("resource") == "product"
        and row.get("decision") == "allow"
        for row in permissions
    )
    assert any(
        row.get("role") == "finance"
        and row.get("resource") == "product"
        and row.get("decision") == "deny"
        and "update" in row.get("actions", [])
        for row in permissions
    )
    assert not any(
        row.get("decision") == "deny"
        and (
            (row.get("role") == "finance" and row.get("resource") == "finance")
            or (row.get("role") == "auditor" and row.get("resource") == "audit")
        )
        for row in permissions
    )
    assert any(
        row.get("role") == "finance"
        and row.get("resource") == "inventory"
        and row.get("decision") == "deny"
        for row in permissions
    )
    assert not any(
        row.get("role") == "finance"
        and row.get("resource") == "refund"
        and row.get("decision") == "deny"
        for row in permissions
    )


def test_permission_scope_extracts_own_and_other_owner_from_narrative() -> None:
    doc = """# Role permissions

| Role | Permissions |
| --- | --- |
| seller | create and modify own products |

- seller cannot modify other seller products.
"""
    parsed = _parse_source(
        doc.encode("utf-8"),
        "permissions.md",
        "permission_matrix",
        "src_scoped_permissions",
    )

    seller_rows = [row for row in parsed["permissions"] if row.get("role") == "seller"]
    assert any(row.get("scope") == "own" and row.get("decision") == "allow" for row in seller_rows)
    assert any(row.get("scope") == "other_owner" and row.get("decision") == "deny" for row in seller_rows)


def test_parse_source_extracts_back_in_stock_and_inventory_sync_as_async_event_rules() -> None:
    doc = """# Scenarios

- 当用户购买库存为 0 的商品时，按钮应置灰，但商品详情仍允许加入购物车用于到货提醒。
- 订单取消后库存应恢复，若商品已经下架则不恢复库存。
- 购物车记录需要保证库存同步。
"""
    parsed = _parse_source(doc.encode("utf-8"), "acceptance_scenarios.md", "collaboration_document", "src_inventory_async")

    rules = parsed.get("rules") or []
    assert any(row.get("rule_type") == "async_event" and "到货提醒" in str(row.get("statement") or "") for row in rules)
    assert any(row.get("rule_type") == "async_event" and "库存应恢复" in str(row.get("statement") or "") for row in rules)
    assert any(row.get("rule_type") == "async_event" and "库存同步" in str(row.get("statement") or "") for row in rules)


def test_build_asset_includes_new_extracted_structures(tmp_path: Path) -> None:
    project_id = "enterprise_knowledge_parse_case"
    actor = {"name": "tester", "role": "project_owner"}
    docs = [
        {"filename": "API_DOCS.md", "text": MARKDOWN_API_DOC},
        {"filename": "field_dictionary.md", "text": FIELD_DICTIONARY_DOC},
        {"filename": "checkout_flow.svg", "text": SVG_DOC},
    ]

    ingest = ingest_enterprise_knowledge_documents(project_id, docs, root=tmp_path, actor=actor)
    asset = build_enterprise_business_knowledge_asset(project_id, root=tmp_path)

    assert ingest["ok"] is True
    assert asset["summary"]["active_source_count"] == 3
    assert asset["summary"]["interface_count"] == 2
    assert asset["summary"]["data_table_count"] >= 1
    assert asset["summary"]["field_dictionary_count"] >= 3
    assert asset["summary"]["ui_design_spec_count"] == 1
    assert asset["summary"]["source_type_distribution"]["markdown_api"] == 1
    assert asset["summary"]["source_type_distribution"]["db_field_dictionary"] == 1
    assert asset["summary"]["source_type_distribution"]["uiux_svg"] == 1
    assert any(row["relation"] == "source_to_asset" for row in asset["relationships"])
    assert any(
        row.get("relation") == "rule_to_interface"
        and row.get("derivation") == "exact_source_section"
        and row.get("status") == "accepted"
        for row in asset["relationships"]
    )


def test_declared_source_sync_ignores_runtime_config_and_empty_legacy_placeholder(
    tmp_path: Path,
) -> None:
    project_id = "declared_source_boundary_case"
    input_dir = tmp_path / "platform_inputs" / project_id
    input_dir.mkdir(parents=True)
    (input_dir / "real_project_config.json").write_text(
        json.dumps({"project_id": project_id}),
        encoding="utf-8",
    )
    (input_dir / "multi_service_config.json").write_text(
        json.dumps({"services": []}),
        encoding="utf-8",
    )
    (input_dir / "prd.md").write_bytes(b"")
    (input_dir / "customer_notes.md").write_text(
        "# Customer notes\nThe order API is documented separately.\n",
        encoding="utf-8",
    )

    discovered = _declared_project_source_files(project_id, tmp_path)

    assert [path.name for path in discovered] == ["customer_notes.md"]


def test_declared_source_sync_includes_implementation_sources(tmp_path: Path) -> None:
    project_id = "declared_implementation_source_case"
    input_dir = tmp_path / "platform_inputs" / project_id
    input_dir.mkdir(parents=True)
    implementation = input_dir / "orders.js"
    implementation.write_text("app.get('/orders', handler);\n", encoding="utf-8")

    discovered = _declared_project_source_files(project_id, tmp_path)

    assert discovered == [implementation]


def test_declared_source_sync_ignores_nested_product_knowledge_assets(
    tmp_path: Path,
) -> None:
    # Keep paths short: nested platform_outputs trees easily exceed Windows MAX_PATH
    # under the repo-local pytest basetemp.
    project_id = "nested_asset"
    input_dir = tmp_path / "platform_inputs" / project_id
    input_dir.mkdir(parents=True, exist_ok=True)
    nested = input_dir / "platform_outputs" / "ekc"
    nested.mkdir(parents=True, exist_ok=True)
    (input_dir / "API_SPEC.md").write_text("# API\nGET /orders\n", encoding="utf-8")
    nested_asset = nested / "enterprise_business_knowledge_asset.json"
    nested_asset.write_text(
        json.dumps({"schema": "product-output-not-source"}),
        encoding="utf-8",
    )
    top_asset = input_dir / "enterprise_business_knowledge_asset.json"
    top_asset.write_text(
        json.dumps({"schema": "also-not-a-source"}),
        encoding="utf-8",
    )

    discovered = _declared_project_source_files(project_id, tmp_path)

    assert [path.name for path in discovered] == ["API_SPEC.md"]


def test_declared_source_sync_fails_closed_on_divergent_logical_key_copies(
    tmp_path: Path,
) -> None:
    """Same logical key under dual input roots with different bytes must fail closed."""
    from ai_test_asset_center.enterprise_knowledge_center import (
        _sync_declared_project_sources,
    )

    project_id = "dual_root_conflict"
    platform_dir = tmp_path / "platform_inputs" / project_id
    project_dir = tmp_path / "projects" / project_id / "input"
    platform_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    (platform_dir / "API_SPEC.md").write_text(
        "# API\n### GET /orders\nList orders.\n",
        encoding="utf-8",
    )
    (project_dir / "API_SPEC.md").write_text(
        "# API\n### GET /orders\nList orders.\n### DELETE /orders/:id\nRemove.\n",
        encoding="utf-8",
    )

    try:
        _sync_declared_project_sources(project_id, tmp_path, {"sources": []})
        raise AssertionError("expected DECLARED_SOURCE_LOGICAL_KEY_CONFLICT")
    except RuntimeError as exc:
        detail = str(exc)
        assert "DECLARED_SOURCE_LOGICAL_KEY_CONFLICT" in detail
        assert "markdown_api:api_spec" in detail
        assert "API_SPEC.md" in detail


def test_declared_source_sync_skips_identical_dual_root_copies(
    tmp_path: Path,
) -> None:
    """Identical content under dual roots must not collide; one ingest is enough."""
    from ai_test_asset_center.enterprise_knowledge_center import (
        _sync_declared_project_sources,
        _load_registry,
    )

    project_id = "dual_root_same"
    platform_dir = tmp_path / "platform_inputs" / project_id
    project_dir = tmp_path / "projects" / project_id / "input"
    platform_dir.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    body = "# API\n### GET /orders\nList orders.\n"
    (platform_dir / "API_SPEC.md").write_text(body, encoding="utf-8")
    (project_dir / "API_SPEC.md").write_text(body, encoding="utf-8")

    registry = _sync_declared_project_sources(project_id, tmp_path, {"sources": []})
    active = [
        row
        for row in registry.get("sources") or []
        if isinstance(row, dict) and row.get("status") == "active"
    ]
    api_rows = [row for row in active if row.get("logical_key") == "markdown_api:api_spec"]
    assert len(api_rows) == 1
    # Second sync is a no-op (hash already active).
    again = _sync_declared_project_sources(
        project_id, tmp_path, _load_registry(project_id, tmp_path)
    )
    again_api = [
        row
        for row in again.get("sources") or []
        if isinstance(row, dict)
        and row.get("status") == "active"
        and row.get("logical_key") == "markdown_api:api_spec"
    ]
    assert len(again_api) == 1


def test_token_overlap_relationships_are_candidate_only() -> None:
    edges = _links_by_overlap(
        [{
            "rule_id": "rule-cart-quantity",
            "statement": "Cart quantity must be conserved.",
            "tokens": ["cart", "quantity", "conserved"],
        }],
        [{
            "interface_id": "api:POST:/cart/items",
            "method": "POST",
            "path": "/cart/items",
            "summary": "Add cart quantity",
            "tokens": ["cart", "quantity", "add"],
        }],
        "rule_id",
        "interface_id",
        relation="rule_to_interface",
    )

    assert len(edges) == 1
    edge = edges[0]
    assert edge["relation"] == "rule_to_interface"
    assert edge["status"] == "candidate"
    assert edge["derivation"] == "token_overlap"
    assert edge["evidence_gate"] == "token_overlap_only_requires_explicit_source_relation"


def test_exact_markdown_endpoint_section_is_authoritative_rule_lineage() -> None:
    parsed = _parse_source(
        MARKDOWN_API_DOC.encode("utf-8"),
        "API_DOCS.md",
        "markdown_api",
        "src_api",
    )

    edges = _links_by_exact_source_section(parsed["rules"], parsed["operations"])

    assert len(edges) == 1
    edge = edges[0]
    assert edge["relation"] == "rule_to_interface"
    assert edge["status"] == "accepted"
    assert edge["derivation"] == "exact_source_section"
    assert edge["evidence"]["operation_locator"] == "POST /orders"


def test_exact_source_section_accepts_cross_document_verbatim_statement() -> None:
    edges = _links_by_exact_source_section(
        [{
            "rule_id": "rule:prd:1",
            "source_id": "src_prd",
            "statement": "The amount must be positive.",
        }],
        [{
            "interface_id": "markdown_api:POST:/orders",
            "source_id": "src_api",
            "method": "POST",
            "path": "/orders",
            "source_excerpt": "### POST `/orders`\nThe amount must be positive.\n",
        }],
    )

    assert len(edges) == 1
    assert edges[0]["status"] == "accepted"
    assert edges[0]["to"] == "markdown_api:POST:/orders"


def test_exclusive_contract_fields_bind_write_ops_in_same_module() -> None:
    edges = _links_by_exclusive_contract_fields(
        [{
            "rule_id": "rule-inventory-conserved",
            "statement": "available_qty and locked_qty must stay non-negative.",
            "risk_type": "data_conservation",
        }],
        [
            {
                "interface_id": "markdown_api:GET:/api/inventory/:sku",
                "method": "GET",
                "path": "/api/inventory/:sku",
                "summary": "Query SKU stock (available_qty / locked_qty).",
                "source_excerpt": "### GET /api/inventory/:sku\navailable_qty / locked_qty\n",
                "field_dictionary": ["available_qty", "locked_qty"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/reserve",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "source_excerpt": "### POST /api/inventory/reserve\n{\"sku\":\"X\",\"qty\":1}\n",
                "field_dictionary": ["sku", "qty"],
            },
            {
                "interface_id": "markdown_api:POST:/api/cart/items",
                "method": "POST",
                "path": "/api/cart/items",
                "source_excerpt": "### POST /api/cart/items\n{\"sku\":\"X\",\"qty\":1}\n",
                "field_dictionary": ["sku", "qty"],
            },
        ],
    )

    assert {edge["to"] for edge in edges} == {"markdown_api:POST:/api/inventory/reserve"}
    assert edges[0]["status"] == "accepted"
    assert edges[0]["derivation"] == "exclusive_contract_field_module"
    assert edges[0]["evidence"]["module_prefix"] == "/api/inventory"


def test_exclusive_contract_fields_prefer_field_bearing_write() -> None:
    edges = _links_by_exclusive_contract_fields(
        [{
            "rule_id": "rule-pay-idempotent",
            "statement": "idempotencyKey must prevent duplicate payment capture.",
            "risk_type": "idempotency",
        }],
        [
            {
                "interface_id": "markdown_api:POST:/api/payments/pay",
                "method": "POST",
                "path": "/api/payments/pay",
                "source_excerpt": '### POST /api/payments/pay\n{"orderId":"1","idempotencyKey":"abc"}\n',
                "field_dictionary": ["orderId", "idempotencyKey"],
            },
            {
                "interface_id": "markdown_api:POST:/api/payments/admin/manual-success",
                "method": "POST",
                "path": "/api/payments/admin/manual-success",
                "source_excerpt": "### POST /api/payments/admin/manual-success\n",
                "field_dictionary": ["orderId"],
            },
        ],
    )

    assert [edge["to"] for edge in edges] == ["markdown_api:POST:/api/payments/pay"]


def test_exclusive_contract_fields_prefer_reversible_module_writes() -> None:
    edges = _links_by_exclusive_contract_fields(
        [{
            "rule_id": "rule-inventory-conserved",
            "statement": "available_qty and locked_qty must stay non-negative.",
            "risk_type": "data_conservation",
        }],
        [
            {
                "interface_id": "markdown_api:GET:/api/inventory/:sku",
                "method": "GET",
                "path": "/api/inventory/:sku",
                "source_excerpt": "available_qty / locked_qty",
                "field_dictionary": ["available_qty", "locked_qty"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/reserve",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "summary": "预占库存",
                "source_excerpt": '{"sku":"X","qty":1,"orderId":"1"}',
                "field_dictionary": ["sku", "qty", "orderId"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/release",
                "method": "POST",
                "path": "/api/inventory/release",
                "summary": "释放预占库存",
                "source_excerpt": '{"sku":"X","qty":1,"orderId":"1"}',
                "field_dictionary": ["sku", "qty", "orderId"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/consume",
                "method": "POST",
                "path": "/api/inventory/consume",
                "summary": "支付后消耗锁定库存",
                "source_excerpt": '{"sku":"X","qty":1,"orderId":"1"}',
                "field_dictionary": ["sku", "qty", "orderId"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/admin/adjust",
                "method": "POST",
                "path": "/api/inventory/admin/adjust",
                "summary": "Admin inventory delta adjust",
                "source_excerpt": '{"sku":"X","delta":10,"reason":"count"}',
                "field_dictionary": ["sku", "delta", "reason"],
            },
        ],
    )

    targets = {edge["to"] for edge in edges}
    assert "markdown_api:POST:/api/inventory/reserve" in targets
    assert "markdown_api:POST:/api/inventory/admin/adjust" in targets
    assert "markdown_api:POST:/api/inventory/release" not in targets
    assert "markdown_api:POST:/api/inventory/consume" not in targets


def test_exclusive_contract_fields_fail_closed_across_modules() -> None:
    edges = _links_by_exclusive_contract_fields(
        [{
            "rule_id": "rule-ambiguous-qty",
            "statement": "qty must be conserved across cart and inventory.",
        }],
        [
            {
                "interface_id": "markdown_api:POST:/api/cart/items",
                "method": "POST",
                "path": "/api/cart/items",
                "source_excerpt": '{"qty":1}',
                "field_dictionary": ["qty"],
            },
            {
                "interface_id": "markdown_api:POST:/api/inventory/reserve",
                "method": "POST",
                "path": "/api/inventory/reserve",
                "source_excerpt": '{"qty":1}',
                "field_dictionary": ["qty"],
            },
        ],
    )

    assert edges == []


def test_same_source_neighbor_binds_concurrency_when_sku_spans_modules() -> None:
    rules = [
        {
            "rule_id": "rule-inventory-conserved",
            "source_id": "src-rules",
            "statement": "available_qty and locked_qty must stay non-negative.",
            "risk_type": "data_conservation",
        },
        {
            "rule_id": "rule-no-oversell",
            "source_id": "src-rules",
            "statement": "The same SKU must not oversell under concurrency.",
            "risk_type": "concurrency",
        },
    ]
    interfaces = [
        {
            "interface_id": "markdown_api:GET:/api/inventory/:sku",
            "method": "GET",
            "path": "/api/inventory/:sku",
            "field_dictionary": ["available_qty", "locked_qty"],
        },
        {
            "interface_id": "markdown_api:POST:/api/inventory/reserve",
            "method": "POST",
            "path": "/api/inventory/reserve",
            "summary": "预占库存",
            "source_excerpt": '{"sku":"X","qty":1,"orderId":"1"}',
            "field_dictionary": ["sku", "qty", "orderId"],
        },
        {
            "interface_id": "markdown_api:POST:/api/inventory/release",
            "method": "POST",
            "path": "/api/inventory/release",
            "summary": "释放预占库存",
            "source_excerpt": '{"sku":"X","qty":1,"orderId":"1"}',
            "field_dictionary": ["sku", "qty", "orderId"],
        },
        {
            "interface_id": "markdown_api:POST:/api/cart/items",
            "method": "POST",
            "path": "/api/cart/items",
            "source_excerpt": '{"sku":"X","qty":1}',
            "field_dictionary": ["sku", "qty"],
        },
    ]
    exclusive = _links_by_exclusive_contract_fields(rules, interfaces)
    neighbor = _links_by_same_source_exclusive_module_neighbors(
        rules,
        interfaces,
        seed_edges=exclusive,
    )
    authoritative = _authoritative_rule_to_interface_edges(rules, interfaces)

    assert any(edge["from"] == "rule-inventory-conserved" for edge in exclusive)
    assert any(
        edge["from"] == "rule-no-oversell"
        and edge["to"] == "markdown_api:POST:/api/inventory/reserve"
        and edge["derivation"] == "same_source_exclusive_module_neighbor"
        for edge in neighbor
    )
    assert any(
        edge["from"] == "rule-no-oversell"
        and edge["to"] == "markdown_api:POST:/api/inventory/reserve"
        for edge in authoritative
    )
    assert not any(
        edge["from"] == "rule-no-oversell"
        and edge["to"] == "markdown_api:POST:/api/cart/items"
        for edge in authoritative
    )


def test_explicit_positive_integer_rule_is_extracted_as_typed_constraint() -> None:
    parsed = _parse_source(
        b"# API\n\n### POST `/items`\n`quantity` must be a positive integer.\n",
        "API_DOCS.md",
        "markdown_api",
        "src_api",
    )

    rule = parsed["rules"][0]
    assert rule["operator"] == "field_constraint"
    assert rule["operands"] == [
        {
            "field_tokens": ["quantity"],
            "validation_constraint": "exclusiveMinimum",
            "validation_constraint_value": 0,
        }
    ]
