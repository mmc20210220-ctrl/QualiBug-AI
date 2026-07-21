#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Deep-dump assertion + reproduction_steps for one TP and one FP owner_tenant_visibility."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
findings = [f for f in (scan.get("findings") or []) if isinstance(f, dict)]

want = {
    "FP_buyer_pay": "[ContractOracle] owner_tenant_visibility: buyer POST /api/payments/pay",
    "TP_warehouse_pay": "[ContractOracle] owner_tenant_visibility: warehouse POST /api/payments/pay",
    "TP_warehouse_products": "[ContractOracle] owner_tenant_visibility: warehouse GET /api/products",
    "FP_buyer_cart": "[ContractOracle] owner_tenant_visibility: buyer POST /api/cart/items",
}
out = {}
for f in findings:
    title = str(f.get("title") or "")
    for label, want_title in want.items():
        if title == want_title:
            ev = f.get("evidence") or {}
            out[label] = {
                "title": title,
                "assertion": ev.get("assertion"),
                "reproduction_steps": ev.get("reproduction_steps"),
                "request": ev.get("request"),
                "response": ev.get("response"),
                "oracle": f.get("oracle"),
            }
Path("_otv_deep.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("dumped:", list(out.keys()))
