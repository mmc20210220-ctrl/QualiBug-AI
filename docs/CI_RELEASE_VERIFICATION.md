# CI Release Verification

The release candidate must pass the same measured verifier locally and in CI:

```bash
python -m aitestops.cli verify-release
```

The GitHub Actions workflow lives at:

```text
.github/workflows/release-verify.yml
```

It runs on Ubuntu with Python 3.12. The CI job runs the
release verifier, which performs:

- Python source compilation.
- Full pytest regression suite.
- Product UI tests.
- Customer-visible text quality checks.
- Private service page/API smoke checks.

The workflow uploads the generated `PHASE69_RELEASE_MANIFEST.json` as an
artifact for the verification job. A release candidate is not
eligible for GA promotion unless the job passes with a complete measured manifest.
