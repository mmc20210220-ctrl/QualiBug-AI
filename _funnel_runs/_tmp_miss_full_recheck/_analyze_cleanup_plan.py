import json
import re

p = r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/optimized.json"
d = json.load(open(p, encoding="utf-8"))
fr = d["full_result"]


def walk(obj, depth=0):
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (
                isinstance(v, list)
                and v
                and isinstance(v[0], dict)
                and ("cleanup_plan" in v[0] or "treatment_plan" in v[0])
            ):
                return k, v
            r = walk(v, depth + 1)
            if r:
                return r
    return None


found = walk(fr)
print("walk", found[0] if isinstance(found, tuple) else found, "len", len(found[1]) if isinstance(found, tuple) else 0)

exps = found[1] if isinstance(found, tuple) else []
cartish = []
for exp in exps:
    if not isinstance(exp, dict):
        continue
    blob = json.dumps(exp, ensure_ascii=False)
    if "cart/items" in blob or "bir_dff5e016338935e6" in blob:
        cartish.append(exp)

print("cartish experiments", len(cartish))
for exp in cartish[:3]:
    print(
        "exp",
        exp.get("experiment_id"),
        "obl",
        exp.get("obligation_id"),
        "cleanup",
        json.dumps(exp.get("cleanup_plan"), ensure_ascii=False)[:600],
    )
    # show treatment/control methods
    for plan_name in ("control_plan", "treatment_plan"):
        for step in exp.get(plan_name) or []:
            if isinstance(step, dict):
                print(
                    " ",
                    plan_name,
                    step.get("method"),
                    step.get("path"),
                    step.get("operation_ref"),
                )

# Fallback: scan raw blob for cleanup_plan containing cart delete
blob = json.dumps(fr, ensure_ascii=False)
count = 0
for m in re.finditer(r'"cleanup_plan": \[(?:[^\[\]]|\[[^\]]*\]){0,1200}\]', blob):
    s = m.group(0)
    if "cart" in s or "210216" in s or "items/:id" in s or "items/{id}" in s:
        print("SNIP", s[:900])
        print("---")
        count += 1
        if count >= 5:
            break
print("snip count", count)
