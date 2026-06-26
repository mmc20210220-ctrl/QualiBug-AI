# V11 Remote SUT Platform I/O

V11 fixes the enterprise boundary problem.

The testing platform should not assume it can write into or even access the SUT repository/server filesystem. In most companies, the testing platform only receives environment endpoints and testing artifacts.

## Platform Input

- SUT base URL
- OpenAPI URL
- Health check URL
- PRD / Jira / Confluence export
- Git Diff / Merge Request diff
- CI failure logs and test artifacts
- Role/account references

## Platform Output

- Generated test assets
- API test DSL
- Precise regression plan
- Failure evidence bundle
- AI triage report
- Bug draft
- Executive report

## Boundary

SUT is read-only or HTTP-only. All outputs are written to AI Test Asset Center workspace and output directories.
