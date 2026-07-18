
import logging
from fastapi import FastAPI
from core.engine import MockEngine

logger = logging.getLogger(__name__)
logger.warning(
    "enterprise_bug_factory is a development/mock service.  "
    "For production use qualibug-server (port 8088)."
)

app = FastAPI(title="Enterprise Bug Factory (dev only)")
engine = MockEngine()

@app.get("/run")
def run(q: str):
    return engine.run(q)

@app.get("/version")
def version():
    return {"version":"v2"}
