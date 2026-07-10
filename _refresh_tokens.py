import json
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://localhost:8080"
LOGIN = "/api/auth/login"
ACC = Path(r"D:\QualiBug-AI\QualiBug-AI-main\platform_inputs\benchmark_mall\test_accounts.json")
accounts = json.loads(ACC.read_text(encoding="utf-8"))


def login(email, password):
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(BASE + LOGIN, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            return data.get("token", ""), int(r.status)
    except urllib.error.HTTPError as e:
        return "", int(e.code)
    except Exception as e:
        return "", 0


refreshed = 0
for name, acc in accounts.items():
    tok, status = login(acc["email"], acc["password"])
    if tok:
        acc["token"] = tok
        refreshed += 1
        print(f"{name:16s} refreshed  (HTTP {status}, len={len(tok)})")
    else:
        print(f"{name:16s} login rejected HTTP {status} (kept old token; status={acc.get('status','')})")

ACC.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"REFRESHED {refreshed}/{len(accounts)} tokens -> {ACC}")
