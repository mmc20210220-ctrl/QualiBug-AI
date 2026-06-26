
from fastapi import FastAPI, Header
from core.engine import Engine

app = FastAPI(title="QualiBug V11 Production Final")

engine=Engine()

@app.get("/run")
def run(q:str, authorization:str=Header(None)):
    return engine.run(q,authorization)

@app.get("/replay")
def replay(q:str, authorization:str=Header(None)):
    return engine.replay(q,authorization)

@app.get("/metrics")
def metrics():
    return engine.metrics

@app.get("/graph")
def graph(authorization:str=Header(None)):
    tenant=engine.auth.verify(authorization)
    return engine.graph.get(tenant,{})

@app.get("/logs")
def logs():
    return engine.logs

@app.get("/health")
def health():
    return {"status":"ok","version":"v11"}
