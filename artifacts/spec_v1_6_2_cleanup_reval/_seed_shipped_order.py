import json
import urllib.request

base = "http://127.0.0.1:8080"


def req(method, path, token=None, body=None):
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw or "null")
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "null")
        except Exception:
            return e.code, raw


buyer = req(
    "POST",
    "/api/auth/login",
    body={"email": "buyer01@example.com", "password": "Test@123456"},
)[1]["token"]
wh = req(
    "POST",
    "/api/auth/login",
    body={"email": "warehouse01@example.com", "password": "Test@123456"},
)
print("warehouse", wh[0], str(wh[1])[:120])
wh_tok = wh[1]["token"] if wh[0] == 200 else None
addr = req("GET", "/api/users/addresses", token=buyer)
print("addr", addr[0], addr[1])
addr_id = addr[1][0]["id"] if isinstance(addr[1], list) and addr[1] else None
created = req(
    "POST",
    "/api/orders",
    token=buyer,
    body={
        "items": [{"sku": "SKU-PHONE-001", "qty": 1}],
        "couponCode": "NEW100",
        "addressId": addr_id,
    },
)
print("create", created[0], str(created[1])[:300])
oid = (created[1] or {}).get("id") if isinstance(created[1], dict) else None
if oid:
    pay = req(
        "POST",
        "/api/payments/pay",
        token=buyer,
        body={
            "orderId": oid,
            "amount": 6899,
            "channel": "BALANCE",
            "idempotencyKey": "reval-v6-" + oid[:8],
        },
    )
    print("pay", pay[0], str(pay[1])[:200])
    if wh_tok:
        ship = req("POST", f"/api/orders/{oid}/ship", token=wh_tok, body={})
        print("ship", ship[0], str(ship[1])[:200])
    print("get", req("GET", f"/api/orders/{oid}", token=buyer))
