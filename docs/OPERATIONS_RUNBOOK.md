# Operations Runbook

This runbook describes the minimum operating procedures for a controlled
enterprise private pilot. It is not a substitute for the customer's production
SRE policy, but it defines the expected health checks, logging, backup, restore
and incident actions for QualiBug.

## Health Checks

The service exposes:

```text
GET /health
```

Expected response:

```json
{
  "ok": true,
  "service": "qualibug_private_pilot",
  "private_root": "...",
  "public_bind_allowed": false
}
```

Operational expectations:

- The endpoint must return HTTP 200 before the service is added to a load
  balancer target group.
- The `private_root` value must point to the mounted project-scoped runtime
  storage.
- `public_bind_allowed` should remain false unless the service is explicitly
  deployed behind a trusted reverse proxy, SSO/OIDC and network ACLs.

## Logs

The default Python HTTP server access log is intentionally suppressed to avoid
accidental request/credential leakage. Production deployments should collect:

- Reverse proxy access logs with request ID, actor, role, path, status and
  latency.
- Application process stdout/stderr.
- Audit artifacts generated under the configured `QUALIBUG_PRIVATE_ROOT`.

Do not log plaintext credentials, tokens, raw customer documents or unredacted
evidence payloads.

## Backup

Back up the configured `QUALIBUG_PRIVATE_ROOT` directory. This directory holds
project-scoped runtime state such as generated knowledge assets, control-plane
outputs, task queues, audit artifacts and release evidence.

Recommended minimum policy for private pilots:

- Daily snapshot while pilots are active.
- Retain at least seven daily snapshots.
- Encrypt backups at rest.
- Store backup access under the customer's normal privileged-access process.

## Restore

1. Stop the QualiBug private pilot service.
2. Restore the selected `QUALIBUG_PRIVATE_ROOT` snapshot to a clean directory.
3. Start the service with `QUALIBUG_PRIVATE_ROOT` pointing to the restored path.
4. Run `GET /health`.
5. Open `/dashboard?project=<project>` and `/release?project=<project>`.
6. Run `python -m aitestops.cli verify-release` from the release package.

Do not restore over an active directory without first taking a forensic copy.

## Incident Response

For suspected credential exposure, data leakage or production-protection bypass:

1. Remove the service from the reverse proxy target group.
2. Preserve `QUALIBUG_PRIVATE_ROOT` and process logs.
3. Rotate affected external credential references in the enterprise vault.
4. Review audit artifacts for the affected project.
5. Re-enable service only after `verify-release` passes and the customer
   incident owner approves the restart.

## Release Candidate Gate

Before promoting a release candidate:

```bash
python -m aitestops.cli verify-release
```

The generated `PHASE69_RELEASE_MANIFEST.json` must report
`overall_status: passed`.
