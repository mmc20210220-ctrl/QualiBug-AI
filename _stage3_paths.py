#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyze Stage-3 miss related_paths vs known behavior_ir operations."""
import json, re
from pathlib import Path

rep = json.loads(Path("_miss_diagnosis.json").read_text(encoding="utf-8"))
scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))

# known operation paths (normalized) from behavior_ir
bir = scan["v12"].get("behavior_ir") or {}
ops = bir.get("operations") or []
def norm(p):
    p = str(p).split("?",1)[0].rstrip("/").lower()
    p = re.sub(r"/qb_test_[0-9a-z]+", "/*", p)
    p = re.sub(r"/QB-TEST-[0-9A-F]+", "/*", p)
    p = re.sub(r"\{[^}]+\}", "/*", p)
    p = re.sub(r"/[0-9a-f]{8,}", "/*", p)
    return p
known_paths = set()
for o in ops:
    if isinstance(o, dict) and o.get("path"):
        known_paths.add(norm(o["path"]))
print("known normalized op paths:", len(known_paths))

# missed bugs by stage
misses = rep.get("miss_reports") or rep.get("missed_bugs") or []
print("total misses:", len(misses))
# find the key holding per-bug records
if not misses:
    print("report keys:", sorted(rep.keys()))

stage3 = [m for m in misses if m.get("failure_stage") == 3]
print("stage3 misses:", len(stage3))

in_known = 0
out_known = 0
all_miss_paths = {}
for m in stage3:
    rps = (m.get("detail") or {}).get("related_paths") or []
    if not rps:
        out_known += 1
        continue
    hit = any(norm(p) in known_paths for p in rps)
    if hit:
        in_known += 1
    else:
        out_known += 1
    for p in rps:
        np = norm(p)
        all_miss_paths[np] = all_miss_paths.get(np, 0) + 1

print(f"stage3 with path in known ops: {in_known}; outside known ops: {out_known}")
print("\ntop missed paths (normalized):")
for p, c in sorted(all_miss_paths.items(), key=lambda x: -x[1])[:30]:
    marker = "KNOWN" if p in known_paths else "new"
    print(f"  {c:3d} [{marker:5s}] {p}")

out = {
    "known_paths": sorted(known_paths),
    "stage3_count": len(stage3),
    "stage3_in_known": in_known,
    "stage3_out_known": out_known,
    "missed_path_freq": dict(sorted(all_miss_paths.items(), key=lambda x: -x[1])),
}
Path("_stage3_paths.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
