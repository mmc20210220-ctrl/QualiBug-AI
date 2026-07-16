# Phase61 Product UI Specification

## Goal

Phase61 turns the separate pilot-operations, TestOps control, enterprise
knowledge, release-gate and industry-benchmark reports into a consistent product
experience.

This is not a frontend rewrite. The project already has server-rendered pages
and a private service entrypoint, so Phase61 uses
`ai_test_asset_center/product_ui.py` as the shared rendering shell.

## Reuse Boundary

- Reuses `enterprise_pilot_runtime`, `enterprise_testops_control_plane`,
  `enterprise_knowledge_center`, `release_risk_dashboard` and benchmark outputs.
- Does not add a second permission model, task model, data model or risk model.
- Does not add a frontend framework, bundler, component library, CDN dependency
  or external font dependency.

## Page Structure

```text
Private service
├─ /dashboard       Enterprise pilot operations center
├─ /control-plane   Enterprise TestOps control center
├─ /knowledge       Enterprise business knowledge center
├─ /release         Release risk dashboard
└─ /benchmark       Multi-industry benchmark

Shared UI shell
├─ Enterprise project context
├─ Controlled private runtime status
├─ Unified navigation, cards, tables, badges and callouts
├─ Current-page JSON snapshot export
└─ Responsive layout
```

## Operating Boundary

- Refresh only reloads controlled project assets.
- Snapshot export only exports the current page payload and must not include
  plaintext credentials.
- Configuration, connector sync, task enqueue, approval and execution still use
  the existing audited APIs.
- Existing project isolation, role checks, audit hash chain and production
  environment protections remain authoritative.

## Local Preview

```bash
set QUALIBUG_PRIVATE_ROOT=C:\path\to\qualibug
python -m ai_test_asset_center.private_pilot_entrypoint
```

Open:

```text
http://127.0.0.1:5174/dashboard?project=real_project_demo
http://127.0.0.1:5174/control-plane?project=real_project_demo
http://127.0.0.1:5174/knowledge?project=real_project_demo
http://127.0.0.1:5174/release?project=real_project_demo
http://127.0.0.1:5174/benchmark?project=real_project_demo
```

Production or long-running pilots must be deployed behind a trusted reverse
proxy with HTTPS, SSO/OIDC, network ACLs, project isolation, centralized logs
and backup policy.
