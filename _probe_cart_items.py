"""Read-only probe of the live benchmark target: what does GET /api/cart/items
actually return for a buyer? This distinguishes 'empty list (no setup)' vs
'response shape mismatch with the extraction path'. Non-production target, GET only."""
import json, urllib.request

BASE = "http://localhost:8080"
tokens = json.load(open(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\benchmark_mall_test_accounts_tokens.json", encoding="utf-8"))

def get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"

for email in ("buyer01@example.com", "buyer02@example.com"):
    tok = tokens[email]["token"]
    st, body = get("/api/cart/items", tok)
    print(f"\n=== {email}  GET /api/cart/items  -> HTTP {st} ===")
    print(body[:1200])
    # also try the raw list shape parse
    try:
        parsed = json.loads(body)
        print("  parsed type:", type(parsed).__name__)
        if isinstance(parsed, list):
            print(f"  list length: {len(parsed)}")
            if parsed:
                print("  first item keys:", list(parsed[0].keys()))
        elif isinstance(parsed, dict):
            print("  dict keys:", list(parsed.keys()))
            for k in ("data", "items", "results", "list"):
                if k in parsed and isinstance(parsed[k], list):
                    print(f"  .{k} length: {len(parsed[k])}")
                    if parsed[k]:
                        print(f"  .{k}[0] keys:", list(parsed[k][0].keys()))
    except Exception as e:
        print("  parse failed:", e)
