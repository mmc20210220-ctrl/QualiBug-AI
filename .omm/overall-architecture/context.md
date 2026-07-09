# Context

QualiBug AI is deployed as a **private pilot** — a self-contained server that customers run on their own infrastructure (or via Docker). It connects to the customer's software system as a "black box" via HTTP APIs, browser automation, and database connections.

## Deployment Model
- **Docker**: Python 3.12-slim image, port 8088, CMD runs `private_pilot_entrypoint`
- **Kubernetes**: Minimal k8s.yaml manifest provided
- **Local**: `qualibug-server` console script for direct execution

## Security Model
- JWT auth with HMAC-SHA256 (no external deps)
- Credential encryption via `credential_crypto.py`
- Target URL allowlisting via `QUALIBUG_ALLOWED_TARGET_ORIGINS`
- Public binding blocked by default (`QUALIBUG_ALLOW_PUBLIC_BIND=1` to override)
- Customer delivery gate separates internal defects from customer-facing findings
