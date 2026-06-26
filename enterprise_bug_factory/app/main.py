
from fastapi import FastAPI
from core.engine import Engine

app = FastAPI()
engine = Engine()

@app.get("/run")
def run(q: str):
    return engine.run(q)

@app.get("/version")
def version():
    return {"version":"v2"}
