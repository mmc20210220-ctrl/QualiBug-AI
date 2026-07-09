from ai_test_asset_center.system_behavior_space import build_system_behavior_space


API_SPEC = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
    post:
      summary: create order
  /api/orders/{id}/pay:
    post:
      summary: pay order
"""

DB_SCHEMA = """
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  amount DECIMAL(10,2) NOT NULL,
  stock INTEGER,
  deleted_at TIMESTAMP,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  UNIQUE(tenant_id, id)
);
"""


def test_system_behavior_space_builds_cross_surface_promises() -> None:
    space = build_system_behavior_space(
        prd_text="管理员可以管理订单，普通用户只能查看自己的订单。订单状态 CREATED -> PAID。",
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        ui_materials="/orders /admin/orders /orders/{id}",
        accounts=[{"role": "admin", "email": "a@example.com"}, {"role": "user", "email": "u@example.com"}],
    ).to_dict()

    assert space["version"] == "system_behavior_space.v1"
    assert space["summary"]["object_count"] >= 1
    assert space["summary"]["promise_count"] >= 5
    assert space["summary"]["probe_candidate_count"] == space["summary"]["promise_count"]
    surfaces = space["summary"]["promise_by_surface"]
    assert surfaces["api"] >= 1
    assert surfaces["db"] >= 1
    assert surfaces["ui"] >= 1
    assert surfaces["auth"] >= 1
    dimensions = space["summary"]["promise_by_dimension"]
    assert dimensions["tenant"] >= 1
    assert dimensions["cross_surface_consistency"] >= 1
    assert dimensions["authorization"] >= 1
    assert dimensions["conservation"] >= 1


def test_system_behavior_space_does_not_limit_itself_to_bug_families() -> None:
    space = build_system_behavior_space(
        prd_text="订单金额必须一致，软删除后不可见，操作必须有审计追踪。",
        api_spec_text=API_SPEC,
        db_schema_text=DB_SCHEMA,
        ui_materials="/orders",
        accounts=[{"role": "user", "email": "u@example.com"}],
    ).to_dict()

    assert "bug" not in space["model_goal"].lower()
    objectives = "\n".join(item["objective"] for item in space["probe_candidates"])
    assert "system promise" in objectives
    assert any(set(item["surface_plan"]) >= {"api", "db"} for item in space["probe_candidates"])
    assert any(set(item["surface_plan"]) >= {"api", "ui"} for item in space["probe_candidates"])


def test_system_behavior_space_surfaces_missing_assets_as_gaps() -> None:
    space = build_system_behavior_space(prd_text="用户只能看自己的数据").to_dict()
    gaps = {item["kind"] for item in space["coverage_gaps"]}

    assert "API_SURFACE_MISSING" in gaps
    assert "DB_SURFACE_MISSING" in gaps
    assert "UI_SURFACE_MISSING" in gaps
    assert "ROLE_SURFACE_MISSING" in gaps
