# Page Agent Bridge Server

## Overview

This bridge server provides a minimal HTTP endpoint for QualiBug's
`page_agent_bridge.py` consumer. It supports three modes:

- `stub_page_agent`: deterministic stub execution for protocol bring-up
- `playwright_browser_plan`: proxy execution through the existing Playwright runner
- `page_agent_browser_plan`: executes an explicit browser plan, or derives a safe
  read-only observation plan from the request payload and then runs it through
  the existing Playwright runner

Server module:

- `ai_test_asset_center/page_agent_bridge_server.py`

## Start The Server

```bash
python -m ai_test_asset_center.page_agent_bridge_server --host 127.0.0.1 --port 8797 --mode stub_page_agent
```

Or run the Playwright proxy mode:

```bash
python -m ai_test_asset_center.page_agent_bridge_server --host 127.0.0.1 --port 8797 --mode playwright_browser_plan
```

Or run the task-aware page-agent mode:

```bash
python -m ai_test_asset_center.page_agent_bridge_server --host 127.0.0.1 --port 8797 --mode page_agent_browser_plan
```

## Health Check

```bash
curl http://127.0.0.1:8797/health
```

## Execute Request

```bash
curl -X POST http://127.0.0.1:8797/execute \
  -H "Content-Type: application/json" \
  -d "{\"project_id\":\"demo\",\"request_id\":\"ui_repro_1\",\"task\":\"Open dashboard\",\"start_url\":\"http://127.0.0.1:3000/\",\"page_hints\":[\"wait for dashboard tiles\"],\"execution_mode\":\"safe_read_only\",\"metadata\":{\"stub_created_data\":{\"object_type\":\"order\",\"object_id\":\"demo-order-1\",\"data_scope_ref\":\"order:demo-order-1\",\"operation_ref\":\"ui_create_order\",\"cleanup_operation_ref\":\"ui_cancel_order\"}}}"
```

## Wire Into QualiBug

Set environment variables:

```bash
set QUALIBUG_PAGE_AGENT_BRIDGE_URL=http://127.0.0.1:8797/execute
set QUALIBUG_PAGE_AGENT_BRIDGE_MODE=stub_page_agent
```

Or pass bridge config in request payload:

```json
{
  "page_agent_bridge": {
    "url": "http://127.0.0.1:8797/execute",
    "timeout_ms": 120000
  }
}
```

## Request Payload Shape

```json
{
  "project_id": "demo",
  "request_id": "ui_repro_1",
  "title": "UI repro",
  "task": "Open dashboard and inspect counters",
  "start_url": "http://127.0.0.1:3000/",
  "execution_mode": "safe_read_only",
  "browser_plan": {},
  "page_hints": ["wait for dashboard tiles"],
  "success_criteria": {},
  "metadata": {},
  "runtime_contract": {
    "status": "approved",
    "approved_base_url": "http://127.0.0.1:3000",
    "execution_mode": "safe_read_only"
  }
}
```

## Response Payload Shape

```json
{
  "status": "executed",
  "execution_status": "executed",
  "confirmation_status": "candidate",
  "current_url": "http://127.0.0.1:3000/",
  "history": [],
  "console": [],
  "network": [],
  "artifacts": [],
  "findings": [],
  "created_data": {},
  "duration_ms": 10
}
```

## Notes

- `created_data` is required if the request is used by `ui_test_data_bootstrap`.
- `stub_page_agent` is only for protocol testing and controlled integration bring-up.
- `playwright_browser_plan` currently opens the target URL, waits for load, and captures a screenshot through the existing browser execution stack.
- `page_agent_browser_plan` uses `browser_plan` when explicitly provided; for
  `safe_read_only` requests without a plan, it derives a generic observation
  flow from `start_url` and `success_criteria`.
- `approved_sandbox_write` requests still require an explicit `browser_plan`;
  the bridge will block rather than infer write actions.
