
from fastapi import FastAPI, Header, HTTPException
from core.engine import Engine

app = FastAPI(title="QualiBug V11 Production Final")

engine = Engine()


def require_demo_token(authorization: str | None = Header(None)) -> str:
    token = (authorization or "").strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    if token != "demo-token":
        raise HTTPException(status_code=401, detail="authentication required")
    return token

@app.get("/run")
def run(q: str, authorization: str = Header(None)):
    token = require_demo_token(authorization)
    return engine.run(q, token)

@app.get("/replay")
def replay(q: str, authorization: str = Header(None)):
    token = require_demo_token(authorization)
    return engine.replay(q, token)

@app.get("/metrics")
def metrics(authorization: str = Header(None)):
    require_demo_token(authorization)
    return engine.metrics

@app.get("/graph")
def graph(authorization: str = Header(None)):
    token = require_demo_token(authorization)
    tenant = engine.auth.verify(token)
    return engine.graph.get(tenant, {})

@app.get("/logs")
def logs(authorization: str = Header(None)):
    require_demo_token(authorization)
    return engine.logs

@app.get("/health")
def health():
    return {"status": "ok", "version": "v11"}
