# Debug Session: http-scan-campaign-drift [OPEN]

## Symptom
- Real HTTP `POST /api/v1/scan` still binds benchmark scans to stale campaign `CMP_43684e9f7c804dab33086629`.
- In-process direct `run_v12_pipeline()` on the same benchmark inputs produces a new active campaign `CMP_2337b64d024e3f47f7f73d7b`.

## Scope Guard
- Steps 1-4: no business-logic fix.
- First code change after this file: instrumentation only.

## Hypotheses
1. `_handle_v12_scan` passes a different `prd/api/schema/campaign_context` payload into `scan()` than the one used in direct reproduction.
2. Local readonly approval prediction still diverges from final scan inputs in one remaining field, forcing `scan()` onto an older campaign identity.
3. `scan()` loads different PRD or schema assets in the HTTP path than expected, causing a different `source_snapshot_hash`.
4. The HTTP path mutates `prepared_body` or `campaign_context` between preparation and `scan()` invocation, so runtime inputs differ from logged preconditions.
5. The service process is reusing stale persisted state from an unexpected project path or actor context during HTTP invocation.

## Plan
1. Add runtime instrumentation around `_prepare_v12_scan_body`, local approval prediction, and `_handle_v12_scan -> scan()`.
2. Reproduce via real HTTP and capture logs.
3. Compare runtime evidence with direct in-process run.
4. Apply minimal fix only after evidence confirms one hypothesis.
