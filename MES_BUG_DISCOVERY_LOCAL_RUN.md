# QualiBug Phase91 · Local MES Bug Discovery Run Pack

This package is preconfigured for the bundled **MES BugLab** local test target. It is for local evaluation only. The MES database is synthetic training data, not customer data.

## First-time setup

Open PowerShell in this extracted folder and run:

```powershell
.\BOOTSTRAP_LOCAL.ps1
```

This creates `.env.local` from `.env.local.example`. Fill only your local LLM settings in `.env.local`; never upload that file.

Then verify the model connection:

```powershell
.\TEST_DEEPSEEK_CONNECTION.ps1
```

## Run one controlled MES discovery round

```powershell
.\RUN_MES_BUG_DISCOVERY_ONCE.ps1
```

The script will:

1. ensure the bundled MES BugLab is healthy on `127.0.0.1:8000`;
2. start exactly one lease-protected QualiBug discovery worker;
3. print heartbeat progress while the worker runs;
4. show durable result, candidate findings and next actions.

Candidate findings are **not automatically confirmed**. They still pass QualiBug's evidence, adversarial, schema and human-review gates.

## Review the latest result

```powershell
.\SHOW_MES_DISCOVERY_RESULT.ps1
.\CHECK_LOCAL_QUALIBUG.ps1
```

Main artifacts are under:

```text
platform_outputs\real_project_demo\
```

## Stop background processes

```powershell
.\STOP_LOCAL_QUALIBUG.ps1
```

## Reset the bundled MES test data

Only after stopping the target and worker:

```powershell
.\RESET_MES_BUGLAB.ps1 -Force
```

This resets the synthetic **bundled MES BugLab** database only. It must never be adapted to reset a customer database.

## Safety boundary

- The package is configured for the local MES BugLab test environment.
- Do not point it at production.
- Do not run a second worker or daemon while `RUN_MES_BUG_DISCOVERY_ONCE.ps1` is active.
- `GRAPH_CONTEXT_MODE=shadow` remains the default for Phase91 local validation.
