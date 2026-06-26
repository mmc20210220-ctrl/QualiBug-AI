# V11.1 Platform Input Form Patch

This patch adds a real platform input form to the web dashboard.

After applying it, open:

```powershell
.\RUN_OPEN_PLATFORM.cmd
```

Then fill in:

- SUT Base URL
- Health check URL
- OpenAPI URL
- PRD text
- Git Diff text
- Failure logs
- OpenAPI fallback JSON

All inputs are saved under:

```text
platform_inputs\enterprise_shop
```

All outputs are generated under:

```text
platform_outputs\enterprise_shop
platform_workspace\enterprise_shop
```
