# QualiBug AI · Phase60 Release Notes

## Enterprise Pilot Runtime & Private Deployment

Phase60 advances QualiBug from a reusable TestOps control plane to a private-cloud enterprise pilot runtime.

### Delivery

- Project/workspace/organization scoped runtime configuration.
- Connector registry for file, Confluence, Feishu, Jira, ZenTao, GitLab Diff and OpenAPI exports.
- Credentials stored only as `vault:` / `env:` / `secret_ref:` references.
- Existing Phase58 knowledge ingestion reused for connector exports; no parallel document store.
- Persistent local task queue with idempotency keys, statuses, attempts and result summaries.
- Independent approvals for sandbox data setup and protected-environment tasks.
- Production-like discovery, writes and data setup remain blocked by runtime policy.
- Hash-chained runtime audit log and Chinese pilot operations dashboard.
- Standard-library private HTTP service and Docker Compose private deployment pack.
- Pilot readiness scorecard for asset, environment, connector, task and audit readiness.

### Verification

- Phase60 dedicated tests: 5/5 passed.
- Full regression: 68/68 passed.
- Python compile check passed.
- Safe local pilot demo passed.
- Demo had 0 remote/network requests.
- Protected-environment discovery was approved by security role but still blocked by runtime policy.

### Claim boundary

The private pilot runtime makes existing quality intelligence operable in a controlled enterprise trial. It does not claim production readiness without customer-specific SSO, network controls, secret management, backup and operational validation.
