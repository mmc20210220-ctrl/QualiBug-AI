# V9 Blackbox Enterprise Replace Patch

This patch is intended to be extracted into an existing AI Test Asset Center project directory.
It upgrades the project to V9 Blackbox Enterprise mode.

## What it adds

- Blackbox enterprise workspace model
- SUT input import instead of writing into business system directory
- `RUN_BLACKBOX_SHOP.cmd`
- `scripts/blackbox_import_and_run.py`
- V9 documentation

## How to use on Windows PowerShell

Run these commands from your existing AI Test Asset Center project root:

```powershell
Expand-Archive "$env:USERPROFILE\Downloads\ai_test_asset_center_v9_replace_patch.zip" -DestinationPath . -Force
.\RUN_BLACKBOX_SHOP.cmd
```

If your enterprise shop demo is not next to this project, pass the input directory explicitly:

```powershell
.\RUN_BLACKBOX_SHOP.cmd C:\Users\Test\Desktop\enterprise_shop_demo\ai_test_inputs
```

All generated files stay inside the AI Test Asset Center project:

- `workspaces\enterprise_shop`
- `outputs\blackbox_enterprise_shop`

The tested business system directory is treated as read-only input.
