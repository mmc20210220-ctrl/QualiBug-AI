from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.__main__ import scan
from ai_test_asset_center.private_pilot_scan_context_contract import build_campaign_context_from_scan_body
from ai_test_asset_center.private_pilot_service import _prepare_v12_scan_body


def main() -> None:
    root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")
    project = "benchmark_mall_v05_p0probe"
    actor = {"name": "local_dev", "role": "project_owner"}
    body = {
        "base_url": "http://127.0.0.1:8080",
        "ui_base_url": "http://127.0.0.1:5174",
        "scope_id": "benchmark-mall-checkout",
        "environment_ref": "local-benchmark",
        "page_agent_bridge": {
            "url": "http://127.0.0.1:8797/execute",
            "auto_start_local": True,
            "mode": "page_agent_browser_plan",
        },
    }
    prepared = _prepare_v12_scan_body(project, root, actor, body, local_dev_mode=True)
    context = build_campaign_context_from_scan_body(prepared)
    result = scan(
        project=project,
        root=root,
        prd_text=str(prepared.get("prd") or ""),
        api_doc_text=str(prepared.get("api_doc") or prepared.get("api_doc_text") or ""),
        base_url=str(prepared.get("base_url") or ""),
        multi_layer=bool(str(prepared.get("base_url") or "")),
        campaign_context=context,
    )
    first_ui_request = (prepared.get("ui_execution_requests") or [{}])[0]
    summary = {
        "prepared_base_url": prepared.get("base_url"),
        "prepared_ui_base_url": prepared.get("ui_base_url"),
        "prepared_ui_base_url_source": prepared.get("ui_base_url_source"),
        "prepared_ui_execution_requests": len(prepared.get("ui_execution_requests") or []),
        "prepared_ui_request_ids": [item.get("request_id") for item in (prepared.get("ui_execution_requests") or [])[:10]],
        "context_ui_execution_requests": len(context.get("ui_execution_requests") or []),
        "first_prepared_ui_request": {
            "request_id": first_ui_request.get("request_id"),
            "start_url": first_ui_request.get("start_url"),
            "browser_plan": first_ui_request.get("browser_plan"),
            "metadata": first_ui_request.get("metadata"),
        },
        "scan_id": result.get("scan_id"),
        "campaign_id": (result.get("campaign") or {}).get("campaign_id"),
        "campaign_status": (result.get("campaign") or {}).get("campaign_status"),
        "execution_status": result.get("execution_status"),
        "runtime_contract_status": (result.get("runtime_contract") or {}).get("status"),
        "runtime_contract_reason": (result.get("runtime_contract") or {}).get("reason"),
        "total_findings": result.get("total_findings"),
        "ui_execution_layer": ((result.get("layers") or {}).get("ui_execution") or {}),
        "ui_execution": result.get("ui_execution"),
        "test_data_bootstrap": result.get("test_data_bootstrap"),
        "ui_test_data_bootstrap": result.get("ui_test_data_bootstrap"),
        "test_data_plan": result.get("test_data_plan"),
    }
    (root / ".tmp_benchmark_page_agent_rerun_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
