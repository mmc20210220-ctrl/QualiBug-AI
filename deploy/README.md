# QualiBug Private Pilot Runtime Deployment

This deployment package is intended for controlled enterprise PoC and private
pilot environments. The runtime flow is:

```text
connector export
-> enterprise knowledge asset
-> controlled task queue
-> approval
-> risk/evidence/release gate
```

## Default Security Boundary

- The service binds to `127.0.0.1` by default.
- Docker Compose maps the service to host localhost by default.
- Public binding requires explicit `QUALIBUG_ALLOW_PUBLIC_BIND=1` and must be
  protected by enterprise reverse proxy controls.
- Production-like environments block automatic data creation, writes, replay,
  compensation and direct defect execution.
- Test data preparation produces isolated plans. Actual writes must be performed
  by enterprise-controlled executors or isolated environments.
- The service does not store tokens, passwords or API keys. Connectors only keep
  references such as `vault:`, `env:` or `secret_ref:`.
- Write APIs require trusted `X-QualiBug-Actor` and `X-QualiBug-Role` headers.

## Local Start

```bash
cp deploy/private.env.example deploy/private.env
# Set QUALIBUG_JWT_SECRET to a unique high-entropy value and edit
# QUALIBUG_PRIVATE_ROOT for the enterprise workspace path.
python -m ai_test_asset_center.private_pilot_entrypoint
```

Open:

```text
http://127.0.0.1:8088/
```

## Docker Compose

```bash
cp deploy/private.env.example deploy/private.env
# Replace the QUALIBUG_JWT_SECRET placeholder before startup. Compose fails
# closed when it is missing.
docker compose --env-file deploy/private.env -f deploy/docker-compose.private.yml up --build
```

## Production Checklist

Before production-like or long-running pilots, add:

- HTTPS termination.
- Enterprise SSO/OIDC or IAM integration.
- Network ACLs and private routing.
- Centralized structured logs.
- Backup and restore policy for project-scoped runtime state.
- Monitoring for health, latency and failed tasks.
- Explicit incident and rollback runbook.

The runtime trusts only identity headers injected by the trusted reverse proxy.
It does not provide public account management endpoints.
