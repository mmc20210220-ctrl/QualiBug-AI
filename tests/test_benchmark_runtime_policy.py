from __future__ import annotations

import json
from pathlib import Path

from benchmark_runtime.runtime_target import BenchmarkRuntime


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_project(root: Path, name: str, slug: str, bugs: list[dict[str, object]], samples: list[dict[str, object]]) -> None:
    project = root / "projects" / name
    _write_json(project / "fixtures" / "sample_requests.json", samples)
    _write_json(
        project / "oracle" / "BUG_GROUND_TRUTH.json",
        {
            "project_slug": slug,
            "project_name": name,
            "bugs": bugs,
        },
    )


def test_list_and_search_surfaces_follow_project_contract_method(tmp_path: Path) -> None:
    _make_project(
        tmp_path,
        "03_mes_work_order_quality_trace",
        "mes",
        [
            {
                "bug_id": "MES-SEARCH",
                "endpoint_hint": "/api/v1/mes/search?keyword=",
                "primary_category": {"id": "C21"},
            },
            {
                "bug_id": "MES-LIST",
                "endpoint_hint": "/api/v1/mes/list?page_size=50000",
                "primary_category": {"id": "C28"},
            },
            {
                "bug_id": "MES-CREATE",
                "endpoint_hint": "/api/v1/mes/work-orders",
                "primary_category": {"id": "C09"},
            },
        ],
        [
            {"method": "POST", "path": "/api/v1/mes/search?keyword="},
            {"method": "POST", "path": "/api/v1/mes/list?page_size=50000"},
            {"method": "POST", "path": "/api/v1/mes/work-orders"},
        ],
    )

    runtime = BenchmarkRuntime(tmp_path)

    assert runtime.find("POST", "/api/v1/mes/search?keyword=work_order").bug_id == "MES-SEARCH"
    assert runtime.find("POST", "/api/v1/mes/list?page_size=50000").bug_id == "MES-LIST"
    assert runtime.find("GET", "/api/v1/mes/search?keyword=work_order") is None
    assert runtime.find("GET", "/api/v1/mes/list?page_size=50000") is None
    assert runtime.find("POST", "/api/v1/mes/work-orders").bug_id == "MES-CREATE"


def test_query_surface_fallback_keeps_business_domain_isolation(tmp_path: Path) -> None:
    _make_project(
        tmp_path,
        "03_mes_work_order_quality_trace",
        "mes",
        [
            {
                "bug_id": "MES-SEARCH",
                "endpoint_hint": "/api/v1/mes/search?keyword=",
                "primary_category": {"id": "C21"},
            }
        ],
        [],
    )
    _make_project(
        tmp_path,
        "02_saas_multitenant_crm",
        "crm",
        [
            {
                "bug_id": "CRM-SEARCH",
                "endpoint_hint": "/api/v1/crm/search?keyword=",
                "primary_category": {"id": "C21"},
            }
        ],
        [],
    )

    runtime = BenchmarkRuntime(tmp_path)

    assert runtime.find("GET", "/api/v1/mes/search?q=work_order").bug_id == "MES-SEARCH"
    assert runtime.find("GET", "/api/v1/crm/search?q=customer").bug_id == "CRM-SEARCH"
    assert runtime.find("POST", "/api/v1/mes/search?q=work_order") is None
    assert runtime.find("GET", "/search?q=customer") is None


def test_report_style_query_surface_can_be_post_when_contract_says_post(tmp_path: Path) -> None:
    _make_project(
        tmp_path,
        "04_expense_approval_reimbursement",
        "expense",
        [
            {
                "bug_id": "EXPENSE-REPORT",
                "endpoint_hint": "/api/v1/expense/report?month=2026-06",
                "primary_category": {"id": "C28"},
            }
        ],
        [{"method": "POST", "path": "/api/v1/expense/report?month=2026-06"}],
    )

    runtime = BenchmarkRuntime(tmp_path)

    assert runtime.find("POST", "/api/v1/expense/report?month=2026-06").bug_id == "EXPENSE-REPORT"
    assert runtime.find("GET", "/api/v1/expense/report?month=2026-06") is None


def test_benchmark_runtime_auth_login_and_identity_resolution(tmp_path: Path) -> None:
    _make_project(
        tmp_path,
        "01_ecommerce_order_payment_inventory",
        "shop",
        [
            {
                "bug_id": "SHOP-ORDER",
                "endpoint_hint": "/api/v1/shop/orders/1",
                "primary_category": {"id": "C08"},
            }
        ],
        [{"method": "GET", "path": "/api/v1/shop/orders/1"}],
    )

    runtime = BenchmarkRuntime(tmp_path)
    identity = runtime.login("qb_normal_user", "benchmark-demo-password", "t-a")

    assert identity is not None
    assert identity.role == "normal_user"
    assert runtime.identify({"Authorization": f"Bearer {identity.token}"}) == identity
    assert runtime.identify({"Cookie": f"sid={identity.session_cookie}"}) == identity
    assert runtime.login("qb_normal_user", "wrong-password", "t-a") is None
