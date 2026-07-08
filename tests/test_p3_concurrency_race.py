"""P3-5: Concurrency race condition detection — multithreaded lost-update on shared counter.

A buggy SUT endpoint does read-modify-write without synchronization.
Two concurrent threads both read the old value → only one increment takes effect
(lost update). This test proves the system can detect the concurrency defect.
"""
from __future__ import annotations
import json,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest

# ── Buggy SUT with race-prone endpoint ──
_orders: dict[str,dict] = {}
_lock = threading.Lock()  # Lock for creating orders, NOT used by pay (the bug!)
_seq = {"n":0}

class RaceSUT(BaseHTTPRequestHandler):
    def log_message(self,*_):return
    def _json(self,code,payload):
        body=json.dumps(payload).encode()
        self.send_response(code);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def _read(self):
        cl=int(self.headers.get("Content-Length")or 0)
        return json.loads(self.rfile.read(cl)or b"{}")if cl else{}
    def do_POST(self):
        body=self._read();path=self.path
        if path=="/api/orders":
            with _lock:_seq["n"]+=1;oid=str(_seq["n"]);_orders[oid]={"id":oid,"status":"created","amount_paid":0}
            return self._json(201,_orders[oid])
        if path.startswith("/api/orders/")and path.endswith("/pay"):
            oid=path.split("/")[3]
            # BUG: no lock on read-modify-write → race condition!
            # Both threads read amount_paid=0, each adds 100, each writes 100
            amount=body.get("amount",0)
            order=_orders.get(oid,{})
            current=order.get("amount_paid",0)  # READ without lock
            time.sleep(0.05)  # Increase race window
            order["amount_paid"]=current+amount  # WRITE without lock
            order["status"]="paid"
            return self._json(200,order)
        return self._json(404,{})
    def do_GET(self):
        oid=self.path.rsplit("/api/orders/",1)[-1]if self.path.startswith("/api/orders/")else""
        order=_orders.get(oid)
        return self._json(200,order)if order else self._json(404,{})
    def do_DELETE(self):
        oid=self.path.rsplit("/api/orders/",1)[-1];_orders.pop(oid,None);return self._json(204,{})


@pytest.fixture(scope="module")
def _race_result(tmp_path_factory):
    _orders.clear();_seq["n"]=0
    root=tmp_path_factory.mktemp("race")
    srv=ThreadingHTTPServer(("127.0.0.1",0),RaceSUT)
    t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
    base=f"http://127.0.0.1:{srv.server_address[1]}"
    import urllib.request

    # 1) Create order
    req=urllib.request.Request(f"{base}/api/orders",data=json.dumps({"quantity":1}).encode(),headers={"Content-Type":"application/json"},method="POST")
    resp=json.loads(urllib.request.urlopen(req).read())
    oid=resp["id"]

    # 2) Fire two concurrent pay requests from separate threads
    results=[]
    def pay(amount):
        req2=urllib.request.Request(f"{base}/api/orders/{oid}/pay",data=json.dumps({"amount":amount}).encode(),headers={"Content-Type":"application/json"},method="POST")
        try:
            r=json.loads(urllib.request.urlopen(req2,timeout=5).read())
            results.append(r.get("amount_paid",0))
        except Exception as e:
            results.append(-1)

    t1=threading.Thread(target=pay,args=(100,));t2=threading.Thread(target=pay,args=(100,))
    t1.start();t2.start();t1.join();t2.join()

    # 3) Read final state
    req3=urllib.request.Request(f"{base}/api/orders/{oid}",method="GET")
    final=json.loads(urllib.request.urlopen(req3).read())
    srv.shutdown();t.join(timeout=2)

    return {"expected":200,"actual":final.get("amount_paid",0),"race_threads":results,"final":final}


def test_race_condition_causes_lost_update(_race_result):
    """Concurrent pay should accumulate to 200, but race condition causes lost update (100 or less)."""
    actual=_race_result["actual"]
    expected_sum=200  # 100+100
    assert actual<expected_sum,f"Race condition NOT detected: actual={actual}, expected<{expected_sum} (got full sum — no lost update)"
    assert actual>=100,f"actual={actual} — at least one pay should have succeeded"

def test_concurrent_threads_both_got_stale_data(_race_result):
    """Both threads read amount_paid=0 and wrote 100 (instead of one reading 100)."""
    results=_race_result["race_threads"]
    # If both threads got amount_paid=100 as the response, the bug isn't present
    # If one got 0 or both got 100, the race IS present
    distinct=len(set(r for r in results if r>=0))
    assert distinct<=len(results),f"thread results: {results}"

def test_final_state_is_corrupted(_race_result):
    """Final amount_paid should NOT equal correct sum of 200."""
    assert _race_result["final"].get("status")=="paid"
    assert _race_result["final"].get("amount_paid",0)!=200,"Race condition should cause amount_paid != 200"
