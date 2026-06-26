# Phase58 Verification

## Automated regression

```text
python -m unittest discover -s tests -p 'test_*.py' -v
Ran 58 tests
OK
```

## Multi-source ingestion validation

The Phase58 test suite verifies that PRD, MRD, OpenAPI, Postman, SQL schema,
permission matrix, historical Bug, ticket, Feishu export and Confluence export
produce one traceable enterprise business knowledge asset.

Verified controls:

- Content-hash deduplication.
- Logical-document versioning with current version active and prior version `superseded`.
- Privileged role requirement for upload, edit and delete.
- PRD rule → interface → database table → Oracle/Probe relation edges.
- Integration with risk planning, assurance coverage and release risk dashboard.

## Local safe integration demo

A local HTTP demo imported five controlled materials (PRD, OpenAPI, SQL schema,
permission matrix and historical Bugs), then built an asset and ran discovery
and the release dashboard.

Observed result:

```text
sources=5
rules=5
oracles=8
knowledge probes selected=8
HTTP requests=13 GET / 0 write requests
release dashboard enterprise knowledge ready=true
```

This demonstrates planning and read-only validation only. It is not a claim
that every rule in an enterprise system has been completely verified.
