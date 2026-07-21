#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Check keyword hits of each duplicate variant against GT bugs."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
bugs = json.loads(Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json").read_text(encoding="utf-8"))
findings = [f for f in (scan.get("findings") or []) if isinstance(f, dict)]

def _text_blob(f):
    parts = [
        str(f.get("title") or ""),
        str(f.get("expected") or ""),
        str(f.get("actual") or ""),
        str(f.get("category") or ""),
        json.dumps(f.get("oracle") or {}, ensure_ascii=False, default=str),
        json.dumps((f.get("evidence") or {}).get("reproduction_steps") or [], ensure_ascii=False),
    ]
    return " ".join(parts).lower()

# Focus on the 4 "warehouse GET /api/products" variants
variants = [f for f in findings if "warehouse GET /api/products" in str(f.get("title"))]
out = {"num_variants": len(variants), "variants": []}

# Which GT bugs reference products?
product_bugs = [b for b in bugs if "product" in str(b.get("module","")).lower()]
out["product_bug_ids"] = [b["bug_id"] for b in product_bugs]

for i, f in enumerate(variants):
    blob = _text_blob(f)
    hits_per_bug = {}
    for b in product_bugs:
        kws = [str(k).lower() for k in (b.get("match_keywords") or []) if str(k).strip()]
        module = str(b.get("module") or "").lower()
        hits = sum(1 for k in kws if k and k in blob)
        module_signal = bool(module) and (module.split("-")[0] in blob)
        matched = (hits >= 2) or (hits >= 1 and module_signal)
        if matched:
            hits_per_bug[b["bug_id"]] = hits
    out["variants"].append({
        "index": i,
        "cdef": f.get("canonical_defect_id"),
        "matched_product_bugs": hits_per_bug,
        "blob_sample": blob[:400],
    })

Path("_variant_match.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print("saved")
