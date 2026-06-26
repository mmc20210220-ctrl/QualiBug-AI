
import hashlib, time
from collections import deque

# ---------------- SECURITY / AUTH ----------------
class Auth:
    def verify(self, token):
        if not token:
            return "anonymous"
        return {"demo-token":"tenant_demo"}.get(token,"tenant_guest")

# ---------------- INFRA LAYERS (production abstraction) ----------------
class RedisClient:
    def __init__(self):
        self.store={}
    def get(self,k): return self.store.get(k)
    def set(self,k,v): self.store[k]=v

class PostgresClient:
    def __init__(self):
        self.rows=[]
    def insert(self,table,data):
        self.rows.append((table,data))

class KafkaClient:
    def __init__(self):
        self.stream=deque()
    def publish(self,event):
        self.stream.append(event)

# ---------------- ENGINE ----------------
class Engine:
    def __init__(self):
        self.version="v11"

        self.auth=Auth()

        self.redis=RedisClient()
        self.pg=PostgresClient()
        self.kafka=KafkaClient()

        self.queue=deque()

        self.metrics={
            "requests":0,
            "traces":0,
            "bugs":0,
            "latency":0
        }

        self.graph={}
        self.logs=[]

        self.rate={}

    # ---------------- rate limit ----------------
    def rate_limit(self,tenant):
        now=time.time()
        bucket=self.rate.get(tenant,[])
        bucket=[t for t in bucket if now-t<10]
        if len(bucket)>200:
            return False
        bucket.append(now)
        self.rate[tenant]=bucket
        return True

    # ---------------- mutation ----------------
    def mutate(self,req):
        states=["normal","edge","invalid","race","partial","timeout","chaos","overflow","explosion"]
        perms=["user","admin","service","guest"]

        seed=int(hashlib.md5(req.encode()).hexdigest(),16)

        out=[]
        for i,s in enumerate(states):
            for j,p in enumerate(perms):
                out.append({
                    "req":req,
                    "state":s,
                    "perm":p,
                    "seed":seed+i+j
                })
        return out

    # ---------------- worker ----------------
    def worker(self,t):
        if t["state"] in ["edge","invalid","race","timeout","chaos","overflow"]:
            return {"status":"error_simulated"}
        return {"status":"ok"}

    def judge(self,r):
        return "BUG" if "error" in r["status"] else "OK"

    # ---------------- run pipeline ----------------
    def run(self,req,token=None):

        tenant=self.auth.verify(token)

        if not self.rate_limit(tenant):
            return {"error":"rate_limited"}

        start=time.time()

        cache_key=f"{tenant}:{req}"
        cached=self.redis.get(cache_key)
        if cached:
            return cached

        muts=self.mutate(req)

        for m in muts:
            self.queue.append(m)

        traces=[]

        while self.queue:
            t=self.queue.popleft()

            r=self.worker(t)
            j=self.judge(r)

            trace={
                "input":t,
                "result":r,
                "verdict":j,
                "ts":time.time(),
                "tenant":tenant
            }

            self.pg.insert("traces",trace)
            self.kafka.publish({"type":"trace","tenant":tenant})

            self.metrics["traces"]+=1
            if j=="BUG":
                self.metrics["bugs"]+=1

            self.graph.setdefault(tenant,{"nodes":[],"edges":[]})
            self.graph[tenant]["nodes"].append(t["state"])
            self.graph[tenant]["edges"].append((t["state"],r["status"]))

            traces.append(trace)

        self.metrics["requests"]+=1
        self.metrics["latency"]=time.time()-start

        result={
            "version":self.version,
            "tenant":tenant,
            "trace_count":len(traces),
            "metrics":self.metrics,
            "graph":self.graph.get(tenant)
        }

        self.redis.set(cache_key,result)
        self.logs.append({"event":"run","tenant":tenant})

        return result

    def replay(self,req,token=None):
        return self.run(req,token)
