from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from ai_test_asset_center import blind_project_runner
from ai_test_asset_center.blind_project_runner import _compile_project_context
from ai_test_asset_center.input_grounded_candidate_compiler import (
    ApiEndpoint,
    _candidate_dedupe_key,
    _resolve_candidate_limit,
    compile_grounded_candidates,
    merge_endpoints,
)


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
            {
                "method": "GET",
                "path": "/orders/{order_id}",
                "summary": "Get order detail with tenant authorization",
                "parameters": ["order_id", "tenant_id"],
                "tokens": ["auth", "tenant"],
            },
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
    order_detail = next(api for api in payload["apis"] if api["path"] == "/orders/{order_id}" and api["method"] == "GET")
    assert order_detail["summary"] == "Get order detail with tenant authorization"
    assert order_detail["has_tenant_id"] is True
    assert order_detail["security"] == [{"bearerAuth": []}]


def test_compile_grounded_candidates_uses_knowledge_asset_when_documents_are_sparse(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Minimal input\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/transactions",
                "summary": "支付订单。支付回调必须幂等，重复支付不能产生额外副作用。",
                "parameters": ["tenant_id"],
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
            {
                "rule_id": "rule:ownership",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "ownership_scope",
                "statement": "普通用户只能看到自己的订单，管理员可查看全部。",
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
    assert any(candidate["risk_type"] == "ownership_scope_probe" for candidate in payload["candidates"])
    assert any(
        ref["file"].startswith("knowledge_asset:")
        for candidate in payload["candidates"]
        for ref in candidate["source_refs"]
    )


def test_compile_grounded_candidates_uses_knowledge_rules_to_enrich_sparse_endpoint_checks(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Minimal input\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/orders/refund",
                "summary": "提交退款请求",
                "tags": ["orders"],
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:refund-owner",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "ownership_scope",
                "statement": "只有订单所有者或管理员可以退款。",
            },
            {
                "rule_id": "rule:refund-idempotent",
                "source_id": "src_rules",
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "statement": "支付回调必须幂等，重复退款不能产生额外副作用。",
            },
        ],
        "business_objects": [{"object": "orders"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_rules", knowledge_asset=knowledge_asset)

    risk_types = {candidate["risk_type"] for candidate in payload["candidates"]}
    assert "ownership_scope_probe" in risk_types
    assert "idempotency_replay_probe" in risk_types


def test_compile_grounded_candidates_sanitizes_role_catalog_and_candidate_actors(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text(
        """# Demo PRD

## 认证
客户端登录后将 `token` 放入 Header：

```http
Authorization: Bearer user-1
```
""",
        encoding="utf-8",
    )

    knowledge_asset = {
        "interfaces": [
            {
                "method": "GET",
                "path": "/api/admin/audit-logs",
                "summary": "仅管理员可访问。返回审计日志，不包含环境变量和密钥信息。",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:admin-only",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "permission_boundary",
                "statement": "仅管理员可访问审计日志，普通用户和客服不能访问。",
            }
        ],
        "roles": [{"role": "admin"}, {"role": "buyer"}, {"role": "customer_service"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_roles", knowledge_asset=knowledge_asset)

    assert set(payload["domain_model"]["roles"]) == {"admin", "buyer", "customer_service"}
    auth_candidates = [candidate for candidate in payload["candidates"] if candidate["risk_type"] == "auth_boundary_probe"]
    assert any(candidate["actors"] == ["anonymous"] for candidate in auth_candidates)
    assert any("已登录访问" in candidate["title"] for candidate in auth_candidates)
    assert all("##" not in actor for candidate in payload["candidates"] for actor in candidate["actors"])


def test_compile_grounded_candidates_expands_multiple_grounded_variants_per_endpoint(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Refund flow\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/payments/refund",
                "summary": "提交退款。退款金额不能超过订单实付金额，重复退款不能产生额外副作用，退款后订单进入 REFUNDED。",
                "tags": ["payments"],
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:refund-owner",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "ownership_scope",
                "statement": "只有订单所有者或管理员可以退款。",
            },
            {
                "rule_id": "rule:refund-idempotent",
                "source_id": "src_rules",
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "statement": "重复退款不能产生额外副作用。",
            },
            {
                "rule_id": "rule:refund-conservation",
                "source_id": "src_rules",
                "rule_type": "conservation",
                "risk_type": "conservation",
                "statement": "退款金额不能超过订单实付金额，主表、流水和汇总必须一致。",
            },
            {
                "rule_id": "rule:refund-state",
                "source_id": "src_rules",
                "rule_type": "workflow",
                "risk_type": "state_transition",
                "statement": "订单退款后进入 REFUNDED 终态，终态不得再次退款。",
            },
        ],
        "roles": [{"role": "admin"}, {"role": "buyer"}],
        "business_objects": [{"object": "refunds"}],
        "state_machines": [{"states": ["PAID", "REFUNDING", "REFUNDED"], "terminal_states": ["REFUNDED"]}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_variants", knowledge_asset=knowledge_asset)

    endpoint_candidates = [candidate for candidate in payload["candidates"] if candidate["endpoint"]["path"] == "/payments/refund"]
    by_risk = Counter(candidate["risk_type"] for candidate in endpoint_candidates)

    assert by_risk["ownership_scope_probe"] >= 2
    assert by_risk["idempotency_replay_probe"] >= 2
    assert by_risk["state_transition_probe"] >= 2
    assert by_risk["conservation_probe"] >= 2
    assert any("负库存/负金额/额度下溢" in candidate["title"] for candidate in endpoint_candidates)
    assert any("主表/流水/汇总不一致" in candidate["title"] for candidate in endpoint_candidates)


def test_compile_grounded_candidates_filters_noisy_state_machine_tokens(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Refund flow\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/payments/refund",
                "summary": "提交退款。退款后进入 REFUNDED。",
                "tags": ["payments"],
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:refund-state",
                "source_id": "src_rules",
                "rule_type": "workflow",
                "risk_type": "state_transition",
                "statement": "退款完成后进入 REFUNDED 终态，终态不得再次退款。",
            }
        ],
        "roles": [{"role": "buyer"}],
        "state_machines": [
            {
                "states": ["360px", "430px", "用户购物车", "CREATED", "PAID", "REFUNDING", "REFUNDED"],
                "terminal_states": ["用户购物车", "REFUNDED"],
                "transitions": [
                    {"from": "360px", "to": "430px"},
                    {"from": "CREATED", "to": "PAID"},
                    {"from": "PAID", "to": "REFUNDING"},
                    {"from": "REFUNDING", "to": "REFUNDED"},
                ],
            }
        ],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_state_cleanup", knowledge_asset=knowledge_asset)

    state_candidates = [candidate for candidate in payload["candidates"] if candidate["risk_type"] == "state_transition_probe"]

    assert state_candidates
    assert all(candidate["probe_plan"]["state_machine"] == ["CREATED", "PAID", "REFUNDING", "REFUNDED"] for candidate in state_candidates)
    assert all(
        candidate["probe_plan"].get("terminal_states", ["REFUNDED"]) == ["REFUNDED"]
        for candidate in state_candidates
    )


def test_compile_grounded_candidates_uses_summary_state_signal_for_write_endpoint(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Order flow\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/api/orders",
                "summary": "创建订单。订单初始状态为 CREATED。",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:order-state",
                "source_id": "src_rules",
                "rule_type": "workflow",
                "risk_type": "state_transition",
                "statement": "订单创建后进入 CREATED，终态后不得非法重入。",
            }
        ],
        "roles": [{"role": "buyer"}],
        "state_machines": [
            {
                "states": ["CREATED", "PAID", "SHIPPED", "FINISHED", "CANCELLED"],
                "terminal_states": ["FINISHED", "CANCELLED"],
            }
        ],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_order_state_signal", knowledge_asset=knowledge_asset)

    order_state_candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "state_transition_probe" and candidate["endpoint"]["path"] == "/api/orders"
    ]

    assert order_state_candidates


def test_compile_grounded_candidates_uses_payment_path_as_state_signal(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Payment flow\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/api/payments",
                "summary": "支付订单。金额必须等于订单应付金额。",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:payment-state",
                "source_id": "src_rules",
                "rule_type": "workflow",
                "risk_type": "state_transition",
                "statement": "支付成功后订单进入 PAID，终态后不得非法重入。",
            }
        ],
        "roles": [{"role": "buyer"}],
        "state_machines": [
            {
                "states": ["CREATED", "PAID", "SHIPPED", "FINISHED", "CANCELLED"],
                "terminal_states": ["FINISHED", "CANCELLED"],
            }
        ],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_payment_state_signal", knowledge_asset=knowledge_asset)

    payment_state_candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "state_transition_probe" and candidate["endpoint"]["path"] == "/api/payments"
    ]

    assert payment_state_candidates


def test_compile_grounded_candidates_does_not_treat_status_field_constraint_as_state_transition(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Legacy field constraint\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/api/legacy/v1/resource/1",
                "summary": "请求字段：userId 为字符串，amount 单位为分，status 长度不超过 5。",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:legacy-status-field",
                "source_id": "src_rules",
                "rule_type": "workflow",
                "risk_type": "state_transition",
                "statement": "仅当接口声明真实状态流转时才生成状态机候选，字段长度约束本身不构成状态机。",
            }
        ],
        "roles": [{"role": "buyer"}],
        "state_machines": [
            {
                "states": ["CREATED", "PAID", "REFUNDED"],
                "terminal_states": ["REFUNDED"],
            }
        ],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_legacy_status_field", knowledge_asset=knowledge_asset)

    assert not any(
        candidate["risk_type"] == "state_transition_probe" and candidate["endpoint"]["path"] == "/api/legacy/v1/resource/1"
        for candidate in payload["candidates"]
    )


def test_merge_endpoints_preserves_same_path_different_methods() -> None:
    api_md = [
        ApiEndpoint(path="/api/orders", method="GET", summary="查询订单"),
    ]
    knowledge_endpoints = [
        ApiEndpoint(path="/api/orders", method="POST", summary="创建订单。订单初始状态为 CREATED。"),
    ]

    merged = merge_endpoints(api_md, knowledge_endpoints)

    assert sorted((endpoint.method, endpoint.path) for endpoint in merged) == [
        ("GET", "/api/orders"),
        ("POST", "/api/orders"),
    ]


def test_compile_grounded_candidates_instantiates_multiple_ownership_rules_per_endpoint(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Ownership\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "GET", "path": "/orders", "summary": "List orders."},
        ],
        "rule_library": [
            {
                "rule_id": "rule:orders-owner-only",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "ownership_scope",
                "statement": "Orders list must only return the caller's own orders and must enforce tenant ownership.",
            },
            {
                "rule_id": "rule:orders-admin-scope",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "permission_boundary",
                "statement": "Orders list must block cross-tenant access even for admin.",
            },
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_ownership_instantiation", knowledge_asset=knowledge_asset)

    ownership = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "ownership_scope_probe" and candidate["endpoint"]["path"] == "/orders"
    ]

    assert len(ownership) >= 2


def test_compile_grounded_candidates_instantiates_multiple_idempotency_rules_per_endpoint(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Idempotency\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/payments", "summary": "Create payment. Payment callback must be idempotent."},
        ],
        "rule_library": [
            {
                "rule_id": "rule:payments-idempotency-1",
                "source_id": "src_rules",
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "statement": "POST /payments must enforce idempotency for retry and replay; duplicate requests cannot create extra side effects.",
            },
            {
                "rule_id": "rule:payments-idempotency-2",
                "source_id": "src_rules",
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "statement": "POST /payments callback handler must enforce idempotency by external_event_id and exactly-once processing.",
            },
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_idempotency_instantiation", knowledge_asset=knowledge_asset)

    idem = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "idempotency_replay_probe" and candidate["endpoint"]["path"] == "/payments"
    ]

    assert len(idem) >= 4


def test_compile_grounded_candidates_generates_business_rule_probe_from_knowledge_asset(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Business rule\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "GET", "path": "/orders", "summary": "List orders."},
        ],
        "rule_library": [
            {
                "rule_id": "rule:orders-visibility",
                "source_id": "src_rules",
                "rule_type": "business_rule",
                "risk_type": "business_rule",
                "statement": "Orders list must only return the caller's own orders and must enforce tenant ownership.",
            }
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_business_rule_probe", knowledge_asset=knowledge_asset)

    candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "business_rule_probe" and candidate["endpoint"]["path"] == "/orders"
    ]

    assert candidates
    assert any(ref["kind"] == "knowledge_rule" for ref in candidates[0]["source_refs"])


def test_compile_grounded_candidates_generates_read_consistency_probe_from_business_rule(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Business rule\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "GET", "path": "/orders", "summary": "List orders with pagination and filters."},
        ],
        "rule_library": [
            {
                "rule_id": "rule:orders-visibility",
                "source_id": "src_rules",
                "rule_type": "business_rule",
                "risk_type": "business_rule",
                "statement": "Orders list must only return the caller's own orders and must enforce tenant ownership.",
            }
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_read_consistency", knowledge_asset=knowledge_asset)

    candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "read_consistency_probe" and candidate["endpoint"]["path"] == "/orders"
    ]

    assert candidates


def test_compile_grounded_candidates_bridges_ownership_rules_by_business_group(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Ownership\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "GET",
                "path": "/api/orders",
                "summary": "订单列表",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:orders-own",
                "source_id": "src_rules",
                "rule_type": "permission",
                "risk_type": "ownership_scope",
                "statement": "普通用户只能看到自己的订单，管理员可查看全部。",
            }
        ],
        "roles": [{"role": "buyer"}, {"role": "admin"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_ownership_group_bridge", knowledge_asset=knowledge_asset)

    candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "ownership_scope_probe" and candidate["endpoint"]["path"] == "/api/orders"
    ]

    assert candidates
    assert any(ref["kind"] == "knowledge_rule" for ref in candidates[0]["source_refs"])

def test_compile_grounded_candidates_generates_audit_probe_for_profile_endpoint(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text(
        """# Audit

- 后台操作必须记录审计日志
- 隐私字段必须脱敏
""",
        encoding="utf-8",
    )

    knowledge_asset = {
        "interfaces": [
            {"method": "GET", "path": "/api/users/me", "summary": "获取个人信息 email phone"},
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_audit_profile", knowledge_asset=knowledge_asset)

    candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "audit_privacy_probe" and candidate["endpoint"]["path"] == "/api/users/me"
    ]

    assert candidates


def test_compile_grounded_candidates_bridges_async_rules_by_business_group(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Async\n- 支付回调必须幂等并验签\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/api/payments/callback", "summary": "支付回调"},
        ],
        "rule_library": [
            {
                "rule_id": "rule:payment-callback-sign",
                "source_id": "src_rules",
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "statement": "支付回调必须验签，必须幂等处理并按 external_event_id 去重。",
                "tokens": ["支付", "回调", "验签", "幂等", "external_event_id"],
            }
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_async_group_bridge", knowledge_asset=knowledge_asset)

    async_candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "async_external_event_probe" and candidate["endpoint"]["path"] == "/api/payments/callback"
    ]

    assert async_candidates
    assert any(ref["kind"] == "knowledge_rule" for ref in async_candidates[0]["source_refs"])


def test_compile_grounded_candidates_generates_idempotency_and_async_for_payment_writes_when_prd_mentions_idempotency(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Idempotency\n- 支付回调必须幂等\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/api/payments", "summary": "支付订单"},
            {"method": "POST", "path": "/api/refunds", "summary": "提交退款"},
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_idem_async_payment", knowledge_asset=knowledge_asset)

    risk_types = {candidate["risk_type"] for candidate in payload["candidates"]}

    assert "idempotency_replay_probe" in risk_types
    assert "async_external_event_probe" in risk_types


def test_compile_grounded_candidates_generates_async_for_settlement_callback_outside_ecommerce(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Settlement\n- 结算回调必须幂等且验签，失败可重试\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/api/v1/settlements/callback", "summary": "结算回调"},
        ],
        "rule_library": [
            {
                "rule_id": "rule:settlement-callback-idempotent",
                "source_id": "src_rules",
                "rule_type": "async_event",
                "risk_type": "async_event",
                "statement": "结算回调必须幂等且验签，失败可重试。",
                "tokens": ["结算", "回调", "幂等", "验签", "重试"],
            }
        ],
        "roles": [{"role": "finance_manager"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_settlement_callback", knowledge_asset=knowledge_asset)
    risk_types = {candidate["risk_type"] for candidate in payload["candidates"]}
    paths = {candidate["endpoint"]["path"] for candidate in payload["candidates"]}

    assert "/api/v1/settlements/callback" in paths
    assert "idempotency_replay_probe" in risk_types or "async_external_event_probe" in risk_types


def test_compile_grounded_candidates_bridges_async_notify_and_message_rules(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Async\n- 订单创建后会发送通知消息，失败时进入重试队列并补发短信\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/api/orders", "summary": "创建订单后发送通知消息"},
        ],
        "rule_library": [
            {
                "rule_id": "rule:order-notify-async",
                "source_id": "src_rules",
                "rule_type": "async_event",
                "risk_type": "async_event",
                "statement": "订单创建后会发送通知消息，失败时进入重试队列并补发短信。",
                "tokens": ["订单", "通知", "消息", "重试队列", "短信"],
            }
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_async_notify_message", knowledge_asset=knowledge_asset)

    async_candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "async_external_event_probe" and candidate["endpoint"]["path"] == "/api/orders"
    ]

    assert async_candidates
    assert any(ref["kind"] == "knowledge_rule" for ref in async_candidates[0]["source_refs"])


def test_compile_grounded_candidates_bridges_inventory_restore_sync_and_back_in_stock_rules(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Async\n- 订单取消后库存应恢复\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/api/orders", "summary": "创建订单并处理库存恢复逻辑"},
            {"method": "POST", "path": "/api/cart/items", "summary": "库存不足时允许加入购物车用于到货提醒，并保持库存同步"},
        ],
        "rule_library": [
            {
                "rule_id": "rule:inventory-restore",
                "source_id": "src_rules",
                "rule_type": "async_event",
                "risk_type": "async_event",
                "statement": "订单取消后库存应恢复，若商品已经下架则不恢复库存。",
                "tokens": ["订单", "库存应恢复", "库存恢复"],
            },
            {
                "rule_id": "rule:back-in-stock",
                "source_id": "src_rules",
                "rule_type": "async_event",
                "risk_type": "async_event",
                "statement": "商品详情仍允许加入购物车用于到货提醒，并需要保证库存同步。",
                "tokens": ["商品", "购物车", "到货提醒", "库存同步"],
            },
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_inventory_async_bridge", knowledge_asset=knowledge_asset)

    async_candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "async_external_event_probe"
    ]

    assert any(candidate["endpoint"]["path"] == "/api/orders" for candidate in async_candidates)
    assert any(candidate["endpoint"]["path"] == "/api/cart/items" for candidate in async_candidates)


def test_compile_grounded_candidates_does_not_bridge_inventory_async_rules_to_user_only_legacy_endpoints(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Async\n- 订单取消后库存应恢复\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {"method": "POST", "path": "/api/cart/items", "summary": "库存不足时允许加入购物车用于到货提醒，并保持库存同步"},
            {"method": "POST", "path": "/api/legacy/v1/resource/1", "summary": "旧客户端继续传 userId 与 token"},
        ],
        "rule_library": [
            {
                "rule_id": "rule:inventory-sync",
                "source_id": "src_rules",
                "rule_type": "async_event",
                "risk_type": "async_event",
                "statement": "当用户购买库存为 0 的商品时，商品详情仍允许加入购物车用于到货提醒，并需要保证库存同步。",
                "tokens": ["用户", "商品", "购物车", "到货提醒", "库存同步"],
            },
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_inventory_async_no_legacy_bridge", knowledge_asset=knowledge_asset)

    async_candidates = [
        candidate
        for candidate in payload["candidates"]
        if candidate["risk_type"] == "async_external_event_probe"
    ]

    assert any(candidate["endpoint"]["path"] == "/api/cart/items" for candidate in async_candidates)
    assert not any(candidate["endpoint"]["path"] == "/api/legacy/v1/resource/1" for candidate in async_candidates)


def test_compile_grounded_candidates_bridges_historical_bug_rules_by_business_domain(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "PRD.md").write_text("# Historical bridge\n", encoding="utf-8")

    knowledge_asset = {
        "interfaces": [
            {
                "method": "POST",
                "path": "/api/cart/items",
                "summary": "添加购物车",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:hist:cart-repeat",
                "source_id": "src_hist",
                "source_type": "historical_bug",
                "rule_type": "reconciliation",
                "risk_type": "data_reconciliation",
                "statement": "QB-00011,2025-12-12,购物车,S4,购物车场景历史问题 11,重复问题,已修复,v3",
                "tokens": ["购物车", "重复问题"],
            }
        ],
        "risk_domains": [
            {
                "risk_id": "risk:hist:cart-repeat",
                "source_id": "src_hist",
                "source_type": "historical_bug",
                "risk_type": "historical_regression",
                "oracle_family": "historical_regression_oracle",
                "expected": "购物车历史缺陷提示重复添加可能导致重复副作用。",
                "tokens": ["购物车", "重复问题"],
            }
        ],
        "roles": [{"role": "buyer"}],
    }

    payload = compile_grounded_candidates(input_dir, project_id="demo_history_bridge", knowledge_asset=knowledge_asset)

    endpoint_candidates = [candidate for candidate in payload["candidates"] if candidate["endpoint"]["path"] == "/api/cart/items"]
    risk_types = {candidate["risk_type"] for candidate in endpoint_candidates}

    assert "idempotency_replay_probe" in risk_types
    assert any(
        any(ref["kind"] in {"knowledge_rule", "knowledge_risk"} for ref in candidate["source_refs"])
        for candidate in endpoint_candidates
    )


def test_candidate_dedupe_key_distinguishes_same_endpoint_same_risk_variants() -> None:
    endpoint = ApiEndpoint(
        path="/orders/refund",
        method="POST",
        summary="提交退款请求",
        actors=["buyer"],
    )

    buyer_key = _candidate_dedupe_key(
        endpoint,
        "ownership_scope_probe",
        actors=["buyer", "finance_manager"],
        rule_codes=["RULE_OWNER_ONLY", "C05"],
        probe={"mutations": ["tenant_id", "owner_user_id"], "expected_status": [403, 404]},
        title="买家跨归属退款候选",
        expected="普通买家只能操作自己的订单。",
        failure="非本人订单被允许退款。",
    )
    buyer_key_reordered = _candidate_dedupe_key(
        endpoint,
        "ownership_scope_probe",
        actors=["finance_manager", "BUYER"],
        rule_codes=["c05", "rule_owner_only"],
        probe={"expected_status": [404, 403], "mutations": ["owner_user_id", "tenant_id"]},
        title="  买家跨归属退款候选  ",
        expected="普通买家只能操作自己的订单。",
        failure="非本人订单被允许退款。",
    )
    admin_key = _candidate_dedupe_key(
        endpoint,
        "ownership_scope_probe",
        actors=["admin"],
        rule_codes=["RULE_ADMIN_SCOPE", "C05"],
        probe={"mutations": ["org_id", "owner_user_id"], "expected_status": [403, 404]},
        title="管理员跨组织退款候选",
        expected="管理员也必须受组织范围限制。",
        failure="管理员可越过组织边界退款。",
    )

    assert buyer_key == buyer_key_reordered
    assert buyer_key != admin_key


def test_resolve_candidate_limit_scales_with_endpoint_count_by_default(monkeypatch) -> None:
    monkeypatch.delenv("QUALIBUG_INPUT_ONLY_MAX_CANDIDATES", raising=False)

    assert _resolve_candidate_limit(None, endpoint_count=10, role_count=0) == 180
    assert _resolve_candidate_limit(None, endpoint_count=130, role_count=1) == 390
    assert _resolve_candidate_limit(None, endpoint_count=130, role_count=2) == 520
    assert _resolve_candidate_limit(None, endpoint_count=130, role_count=5) == 3900
    assert _resolve_candidate_limit(None, endpoint_count=400, role_count=5) == 5000


def test_resolve_candidate_limit_prefers_explicit_argument_then_env(monkeypatch) -> None:
    monkeypatch.setenv("QUALIBUG_INPUT_ONLY_MAX_CANDIDATES", "240")

    assert _resolve_candidate_limit(75, endpoint_count=130, role_count=5) == 75
    assert _resolve_candidate_limit(None, endpoint_count=130, role_count=5) == 240
