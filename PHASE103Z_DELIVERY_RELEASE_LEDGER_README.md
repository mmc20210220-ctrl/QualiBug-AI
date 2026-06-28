# Phase103Z Delivery Release Ledger

Phase103Z adds the final handoff layer after the Phase103X delivery bundle and
Phase103Y acceptance gate. It creates a customer-safe release ledger with SHA256
checksums, customer release notes, a release receipt, and a verification command
that can detect post-release tampering.

## Build a release ledger

```powershell
python -m ai_test_asset_center.phase103_delivery_release --bundle-dir .\outputs\phase103_delivery_bundle --output-dir .\outputs\phase103_delivery_release
```

## Build bundle first, then create release ledger

```powershell
python -m ai_test_asset_center.phase103_delivery_release --build-first --scenario manufacturing --bundle-dir .\outputs\phase103_delivery_bundle_manufacturing --output-dir .\outputs\phase103_delivery_release_manufacturing
```

## Verify an existing release ledger

```powershell
python -m ai_test_asset_center.phase103_delivery_release --verify --bundle-dir .\outputs\phase103_delivery_bundle --output-dir .\outputs\phase103_delivery_release
```

## Outputs

- `release_manifest.json`
- `release_manifest.md`
- `CHECKSUMS.sha256`
- `CUSTOMER_RELEASE_NOTES.md`
- `RELEASE_RECEIPT.md`
- `release_verification_report.json` when verifying
- `release_verification_report.md` when verifying

The release ledger is offline-safe and does not include raw token, cookie,
password, session, or client secret values.
