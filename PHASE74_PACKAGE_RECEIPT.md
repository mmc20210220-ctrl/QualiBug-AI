# Phase74 Package Receipt

## Controlled archive scope

The Phase74 source archive contains source, tests, configuration examples, documentation, Phase74 release evidence and the CI workflow.

Excluded from the archive:

- runtime workspaces and canonical discovery ledgers;
- generated platform outputs and MES target data;
- benchmark truth/oracle files;
- `.env.local`, credentials, tokens, logs, caches and bytecode.

## Verification before final archive

- Archive integrity check: `unzip -t` passed.
- Full product release manifest: `passed`, `release_ready=true`.
- Full regression suite: 99/99 passed.
- Agent Loop targeted tests: 4/4 passed.
- Required product regressions: 14/14 passed.

## Credential scan note

The archive contains one fixed self-test sentinel in `aitestops/self_dogfood_audit.py` to prove that UI/report redaction never renders secret-shaped strings. It is not a credential, is not used for network access, and is the only allowlisted secret-shaped literal outside test fixtures.

The final SHA-256 is distributed beside the archive. The SHA file must be checked before deployment.
