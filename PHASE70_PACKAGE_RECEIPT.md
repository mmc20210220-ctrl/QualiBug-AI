# Phase70 Package Receipt

## Archive

- Asset: `QualiBug_AI_Enterprise_Edition_Phase70_Complete.zip`
- Integrity sidecar: `QualiBug_AI_Enterprise_Edition_Phase70_Complete.sha256`
- Archive integrity: `unzip -t` passed.
- Hash verification: `sha256sum -c` passed.

## Delivery exclusion boundary

The archive excludes real `.env` / `.env.local` files, runtime outputs, project
workspaces, caches, bytecode, logs, databases and private benchmark/customer
artifacts. `.env.local.example` is retained as a credential-free configuration
example.

The source contains a named dogfood redaction sentinel used by the self-audit
code. It is not a real credential, is not loaded as configuration, and does not
match any deployment secret reference.

## Unpacked verification

From a clean extracted copy:

```text
compileall = passed
core regression = 28/28 passed
PHASE70_RELEASE_MANIFEST.json overall_status = passed
PHASE70_RELEASE_MANIFEST.json release_ready = true
```
