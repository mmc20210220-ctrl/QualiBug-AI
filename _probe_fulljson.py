"""Probe full.json structure: locate where per-obligation oracle/contract receipts live."""
import json, sys
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
print("loading", path.name, path.stat().st_size/1024/1024, "MB ...")
d = json.load(open(path, encoding="utf-8"))
print("top-level type:", type(d).__name__)
if isinstance(d, dict):
    print("top-level keys:", list(d.keys()))

# Collect summary of where activation_receipt / contract_evidence_receipts appear
found = {
    "activation_receipt": 0,
    "contract_evidence_receipts": 0,
    "observer_receipts": 0,
    "obligation": 0,
    "oracle": 0,
}
sample_paths = []

def walk(obj, prefix="", depth=0):
    if depth > 12 or not isinstance(obj, (dict, list)):
        return
    items = obj.items() if isinstance(obj, dict) else enumerate(obj)
    for k, v in items:
        key = k if isinstance(obj, dict) else f"[{k}]"
        p = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(v, dict):
            if "activation_receipt" in v and found["activation_receipt"] < 2:
                found["activation_receipt"] += 1
                sample_paths.append(("activation_receipt", p))
            if "contract_evidence_receipts" in v:
                found["contract_evidence_receipts"] += 1
                if len(sample_paths) < 20:
                    sample_paths.append(("contract_evidence_receipts", p, len(v.get("contract_evidence_receipts", []))))
            if "observer_receipts" in v:
                found["observer_receipts"] += 1
            if key in ("obligation", "obligations") and found["obligation"] < 2:
                found["obligation"] += 1
                sample_paths.append(("obligation", p))
            if key in ("oracle", "oracles") and found["oracle"] < 2:
                found["oracle"] += 1
                sample_paths.append(("oracle", p))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            if key in ("obligations", "oracles", "delivery_results", "obligation_results"):
                found["obligation"] += 1
                if len(sample_paths) < 30:
                    sample_paths.append((f"LIST:{key}", p, len(v)))
        walk(v, p, depth+1)

walk(d)
print("\ncounts:", found)
print("\nsample paths:")
for sp in sample_paths[:40]:
    print("  ", sp)
