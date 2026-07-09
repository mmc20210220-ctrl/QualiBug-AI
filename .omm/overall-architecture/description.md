# Overall Architecture

QualiBug AI is an **autonomous bug discovery platform** (v95.0.0, Phase106) that analyzes software systems to find real defects. It ingests project knowledge (PRD, OpenAPI, DB schemas, business rules), generates intelligent probes, executes them against live targets, and collects evidence to confirm or falsify hypotheses. The platform is designed for **cross-industry adaptability** — no hardcoded business logic.

## Key Components

- **Frontend**: React 19 + TypeScript + Vite 8 SPA on port 5174 (dev)
- **Backend (Primary)**: Python `ThreadingHTTPServer` on port 8088 (`private_pilot_service.py`)
- **Backend (Legacy)**: FastAPI gateway (`backend/main.py`) — retained for compatibility
- **Discovery Engine**: Reader → Reasoner → Executor → Verifier loop
- **Reasoning Engines**: 17+ business reasoning engines (causality, reconciliation, saga, lifecycle, etc.)
- **Evidence Pipeline**: 4-layer evidence state preservation with double-gate verification
- **Patch Architecture**: ~15+ runtime patches that extend core service modularly

## Tech Stack

- Python 3.12+ with FastAPI, httpx, pytest
- React 19 with Vite 8, React Router 7, Three.js, @xyflow/react
- LLM: OpenAI / Anthropic via `llm_reasoning.py`
- Playwright for browser automation
- File-based persistence (JSON under `platform_workspace/`)
- No external database required for core operation
