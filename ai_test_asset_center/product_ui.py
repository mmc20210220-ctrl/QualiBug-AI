from __future__ import annotations

"""Phase61 Product UI — Enterprise Dashboard & Design System.

Pure HTML/CSS/JS, zero external dependencies. Dark-themed enterprise dashboard
with real-time status, animated metrics, responsive layout, and full CRUD workflows.
"""

import html as _html
import json
from typing import Any
from urllib.parse import quote

PRODUCT_UI_VERSION = "phase61-product-ui"


def h(value: Any) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


def _icon(name: str) -> str:
    paths = {
        "overview": "M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z",
        "runtime": "M12 3v9l6 3M5 4h14v16H5z",
        "knowledge": "M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z",
        "assets": "M6 3h8l4 4v14H6z M14 3v5h5 M9 13h6 M9 17h6",
        "environment": "M12 3c4.97 0 9 4.03 9 9s-4.03 9-9 9-9-4.03-9-9 4.03-9 9-9Zm0 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10Z",
        "risk": "M12 3 2.8 20h18.4L12 3Zm0 5v5m0 3.5v.01",
        "release": "M6 4h12v16H6z M9 8h6M9 12h6M9 16h3",
        "benchmark": "M4 20V10m5 10V4m5 16v-7m5 7V7",
        "security": "M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Z",
        "settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8.2 4a7.6 7.6 0 0 0-.13-1.4l2.04-1.58-2-3.46-2.4.97a7.4 7.4 0 0 0-2.42-1.4L14.93 2h-4l-.37 3.13a7.4 7.4 0 0 0-2.42 1.4l-2.4-.97-2 3.46 2.04 1.58A7.6 7.6 0 0 0 5.8 12c0 .48.05.95.13 1.4l-2.04 1.58 2 3.46 2.4-.97a7.4 7.4 0 0 0 2.42 1.4l.37 3.13h4l.37-3.13a7.4 7.4 0 0 0 2.42-1.4l2.4.97 2-3.46-2.04-1.58c.08-.45.13-.92.13-1.4Z",
        "refresh": "M20 11a8 8 0 1 0 2 5.3M20 4v7h-7",
        "download": "M12 3v11m0 0 4-4m-4 4-4-4M4 20h16",
        "shield": "M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Z",
        "spark": "m12 2 1.7 6.3L20 10l-6.3 1.7L12 18l-1.7-6.3L4 10l6.3-1.7L12 2Z",
        "check": "M20 6 9 17l-5-5",
        "clock": "M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
        "bug": "M8 2v3m8-3v3M3 8h18M5.5 5.5l1.5 1.5m10 0 1.5-1.5M10 14l-2 3m6-3 2 3M12 12v3",
        "pipeline": "M4 4h4v4H4Zm8 0h4v4h-4ZM4 12h4v4H4Zm8 2h4v2h-4ZM4 20h4v4H4Zm8-4h4v4h-4Z",
        "engine": "M12 2a4 4 0 0 1 4 4v2h2a4 4 0 0 1 0 8h-2v2a4 4 0 0 1-8 0v-2H6a4 4 0 0 1 0-8h2V6a4 4 0 0 1 4-4Zm0 6v8m4-4H8",
        "play": "M7 4v16l13-8L7 4Z",
        "eye": "M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
        "plug": "M12 2a4 4 0 0 1 4 4v4h2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-2v4a4 4 0 0 1-8 0v-4H6a2 2 0 0 1-2-2v-2a2 2 0 0 1 2-2h2V6a4 4 0 0 1 4-4Z",
        "database": "M4 6c0 1.5 3.6 3 8 3s8-1.5 8-3M4 6v6c0 1.5 3.6 3 8 3s8-1.5 8-3V6M4 12v6c0 1.5 3.6 3 8 3s8-1.5 8-3v-6",
        "ai": "M12 2a2 2 0 0 1 2 2v2h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2v2a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2v-2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2V4a2 2 0 0 1 2-2h2Zm-2 8a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z",
        "chevron_down": "M6 9l6 6 6-6",
    }
    path = paths.get(name, paths["overview"])
    return f"<svg viewBox='0 0 24 24' aria-hidden='true' focusable='false'><path d='{path}'/></svg>"


def status_badge(value: Any) -> str:
    raw = str(value or "unknown")
    n = raw.strip().lower()
    tone = "success" if n in {"succeeded","success","ready","healthy","valid","green","ok","passed"} else \
           "danger" if n in {"failed","blocked","error","not_testable","red","unsafe"} else \
           "warning" if n in {"waiting_approval","queued","running","pending","warning","degraded","yellow"} else \
           "info" if n in {"sandbox","safe_read_only","readonly"} else "neutral"
    return f"<span class='status status-{tone}'>{h(raw)}</span>"


def metric_card(label: str, value: Any, hint: str = "", tone: str = "default", icon: str = "overview") -> str:
    tones = {"success":"metric-success","warning":"metric-warning","danger":"metric-danger"}
    return (
        f"<article class='metric-card {tones.get(tone,'')}'>"
        f"<div class='metric-head'><span>{h(label)}</span><i class='metric-icon'>{_icon(icon)}</i></div>"
        f"<strong>{h(value)}</strong>"
        f"<small>{h(hint)}</small></article>"
    )


def empty_state(title: str, description: str) -> str:
    return f"<div class='empty-state'><i>{_icon('spark')}</i><div><strong>{h(title)}</strong><p>{h(description)}</p></div></div>"


def table(headers: list[str], rows: list[list[str]], empty_text: str = "No data") -> str:
    hdr = "".join(f"<th>{h(c)}</th>" for c in headers)
    b = "".join("<tr>"+"".join(f"<td>{cell}</td>" for cell in row)+"</tr>" for row in rows) if rows else \
        f"<tr><td class='table-empty' colspan='{max(1,len(headers))}'>{h(empty_text)}</td></tr>"
    return f"<div class='table-wrap'><table><thead><tr>{hdr}</tr></thead><tbody>{b}</tbody></table></div>"


def section(title: str, description: str = "", body: str = "", action: str = "", section_id: str = "") -> str:
    hd = f"<div class='section-title'><div><h2>{h(title)}</h2>{f'<p>{h(description)}</p>' if description else ''}</div>{action}</div>"
    ident = f" id='{h(section_id)}'" if section_id else ""
    return f"<section class='panel'{ident}>{hd}{body}</section>"


def callout(title: str, text: str, tone: str = "info", icon: str = "shield") -> str:
    return f"<div class='callout callout-{h(tone)}'><i>{_icon(icon)}</i><div><strong>{h(title)}</strong><p>{h(text)}</p></div></div>"


def detail_list(items: list[tuple[str, Any]], empty_text: str = "No details") -> str:
    if not items:
        return empty_state("No information", empty_text)
    rows = "".join(f"<div class='detail-row'><span>{h(label)}</span><b>{h(value)}</b></div>" for label, value in items)
    return f"<div class='detail-list'>{rows}</div>"


def progress_bar(value: float, label: str = "") -> str:
    pct = min(100, max(0, int(value * 100)))
    tone = "green" if pct >= 80 else "amber" if pct >= 50 else "red"
    return f"<div class='progress-wrap'><div class='progress-bar progress-{tone}' style='width:{pct}%'></div>{f'<span>{h(label)} {pct}%</span>' if label else ''}</div>"


def pipeline_stage(num: int, name: str, status: str, duration: str = "") -> str:
    tones = {"done":"stage-done","running":"stage-running","pending":"stage-pending","failed":"stage-failed"}
    icon_map = {"done":"check","running":"refresh","pending":"clock","failed":"risk"}
    return (
        f"<div class='pipeline-step {tones.get(status,'stage-pending')}'>"
        f"<div class='step-num'>{_icon(icon_map.get(status,'clock')) if status in ('done','failed') else str(num)}</div>"
        f"<div class='step-info'><strong>{h(name)}</strong><span>{h(status.title())}{' · '+h(duration) if duration else ''}</span></div>"
        f"</div>"
    )


def engine_status_card(name: str, status: str, findings: int = 0, icon: str = "engine") -> str:
    tones = {"active":"engine-active","idle":"engine-idle","error":"engine-error"}
    return (
        f"<div class='engine-card {tones.get(status,'engine-idle')}'>"
        f"<i>{_icon(icon)}</i>"
        f"<div><strong>{h(name)}</strong><span>{h(status.title())}{' · '+str(findings)+' findings' if findings else ''}</span></div>"
        f"</div>"
    )


def _nav(project_id: str, active: str) -> str:
    project = quote(str(project_id or "real_project_demo"))
    items = [
        ("overview", "总览", "dashboard", "overview"),
        ("findings", "Bug发现", "findings", "bug"),
        ("knowledge", "知识中心", "knowledge", "knowledge"),
        ("assets", "TestOps", "control-plane", "assets"),
        ("release", "发布门禁", "release", "release"),
        ("benchmark", "评测", "benchmark", "benchmark"),
    ]
    return "".join(
        f"<a class='nav-item{' is-active' if k==active else ''}' href='/{p}?project={project}'><i>{_icon(icon)}</i><span>{h(label)}</span></a>"
        for k, label, p, icon in items
    )


def product_shell(
    *, title: str, project_id: str, active: str, eyebrow: str,
    headline: str, description: str, body: str,
    payload: dict[str, Any] | None = None, actions: str = "",
    environment_label: str = "Controlled private runtime",
    page_hint: str = "",
    llm_status: str = "unknown",
) -> str:
    project = h(project_id or "real_project_demo")
    payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str).replace("</", "<\\/")
    default_actions = (
        f"<a class='btn btn-secondary' href='/dashboard?project={project}'><i>{_icon('overview')}</i>回到总览</a>"
    )
    page_actions = actions or default_actions
    # Auto-detect LLM status if not explicitly passed
    if llm_status == "unknown":
        try:
            from .llm_reasoning import is_available
            llm_status = "configured" if is_available() else "offline"
        except Exception:
            llm_status = "offline"
    llm_labels = {
        "online": ("llm-online", "LLM Online"),
        "configured": ("llm-configured", "LLM Configured"),
        "failed": ("llm-failed", "LLM Failed"),
        "offline": ("llm-offline", "LLM Offline"),
    }
    llm_class, llm_label = llm_labels.get(llm_status, llm_labels["offline"])
    return f"""<!doctype html>
<html lang='en' data-product-ui='{PRODUCT_UI_VERSION}'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<meta name='color-scheme' content='light'>
<title>{h(title)} · QualiBug</title>
<style>
:root{{--ink:#0b1120;--muted:#5b6985;--bg:#f0f3f9;--surface:#fff;--line:#e3e8f2;--primary:#5865f2;--primary-hover:#4752d9;--success:#0ea571;--warning:#d97706;--danger:#e02449;--side:#0b1324;--side-text:#b0bedd;--radius:15px;--radius-sm:10px;--shadow-sm:0 1px 3px rgba(11,18,33,.04),0 1px 2px rgba(11,18,33,.06);--shadow:0 1px 3px rgba(11,18,33,.04),0 4px 16px rgba(11,18,33,.06);--shadow-lg:0 4px 6px rgba(11,18,33,.03),0 12px 36px rgba(11,18,33,.08);--transition:all .18s ease;--font:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}body{{background:var(--bg);color:var(--ink);font:14px/1.55 var(--font);-webkit-font-smoothing:antialiased}}a{{color:inherit;text-decoration:none}}button{{font:inherit;cursor:pointer;border:none}}svg{{fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}}
::selection{{background:rgba(88,101,242,.16)}}

.app-shell{{display:grid;grid-template-columns:256px 1fr;min-height:100vh}}

/* Sidebar */
.sidebar{{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;padding:18px 12px;background:var(--side);border-right:1px solid rgba(255,255,255,.04);overflow-y:auto}}
.brand{{display:flex;align-items:center;gap:10px;padding:6px 10px 20px;color:#fff}}
.brand-mark{{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#7b8cff,#22d3bb);transition:var(--transition)}}
.brand-mark:hover{{transform:scale(1.06)}}
.brand-mark svg{{width:19px;height:19px;stroke:#fff}}
.brand-copy strong{{display:block;line-height:1.15;font-size:15px;letter-spacing:-.01em}}
.brand-copy span{{color:#6d7fa8;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}}
.workspace-chip{{margin:0 6px 16px;padding:10px 12px;border:1px solid rgba(255,255,255,.06);border-radius:11px;background:rgba(255,255,255,.03);color:#d4dff7}}
.workspace-chip span{{display:block;font-size:9px;color:#6d7fa8;font-weight:700;letter-spacing:.09em;text-transform:uppercase}}
.workspace-chip b{{display:block;margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:600}}
.nav-label{{padding:0 10px 8px;color:#56658a;font-size:9px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}
.nav-item{{display:flex;align-items:center;gap:10px;margin:1px 0;padding:10px 10px;border:1px solid transparent;border-radius:9px;color:var(--side-text);font-size:13px;font-weight:500;transition:var(--transition)}}
.nav-item:hover{{background:rgba(255,255,255,.06);color:#fff}}
.nav-item.is-active{{border-color:rgba(123,140,255,.22);background:linear-gradient(90deg,rgba(88,101,242,.22),rgba(88,101,242,.04));color:#fff;font-weight:600}}
.nav-item i{{display:grid;place-items:center;width:18px;height:18px}}
.sidebar-actions{{margin-top:12px;padding:0 6px}}
.sidebar-action-btn{{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid rgba(255,255,255,.04);border-radius:9px;color:var(--side-text);font-size:12px;font-weight:500;transition:var(--transition)}}
.sidebar-action-btn:hover{{background:rgba(255,255,255,.06);color:#fff}}
.sidebar-action-btn i{{display:grid;place-items:center;width:18px;height:18px}}
.sidebar-foot{{margin-top:auto;padding:14px 10px 4px;border-top:1px solid rgba(255,255,255,.05);color:#56658a;font-size:10px;line-height:1.45}}
.sidebar-foot b{{display:block;margin-bottom:4px;color:#9eb1d9;font-size:11px;font-weight:600}}

/* Topbar */
.topbar{{position:sticky;z-index:10;top:0;display:flex;align-items:center;justify-content:space-between;height:64px;padding:0 28px;background:rgba(242,245,250,.88);border-bottom:1px solid rgba(226,232,242,.7);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}}
.crumb{{color:var(--muted);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.crumb b{{color:var(--ink)}}
.topbar-right{{display:flex;align-items:center;gap:10px}}
.runtime-pill{{display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid #b7efe4;border-radius:99px;background:#ecfdf7;color:#0d7d68;font-size:11px;font-weight:700;letter-spacing:-.01em}}
.runtime-pill::before{{content:"";width:7px;height:7px;border-radius:50%;background:var(--success);animation:pulse-dot 2s infinite}}
@keyframes pulse-dot{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.avatar{{display:grid;place-items:center;width:31px;height:31px;border-radius:50%;background:linear-gradient(135deg,#e8ebff,#d5dbff);color:#4455d8;font-size:11px;font-weight:800}}
.llm-pill{{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:99px;font-size:10px;font-weight:700;letter-spacing:-.01em}}
.llm-pill::before{{content:"";width:6px;height:6px;border-radius:50%}}
.llm-online{{border:1px solid #b9f0da;background:#ecfdf7;color:#0b8a5f}}.llm-online::before{{background:var(--success)}}
.llm-configured{{border:1px solid #fde68a;background:#fffbeb;color:#92400e}}.llm-configured::before{{background:var(--warning)}}
.llm-failed{{border:1px solid #fecaca;background:#fff1f2;color:#be123c}}.llm-failed::before{{background:var(--danger)}}
.llm-offline{{border:1px solid #fecaca;background:#fff5f5;color:#c11d3b}}.llm-offline::before{{background:var(--danger)}}
.scan-btn{{display:inline-flex;align-items:center;gap:7px;padding:7px 16px;border-radius:9px;background:var(--success);color:#fff;font-size:12px;font-weight:700;transition:var(--transition)}}.scan-btn:hover{{filter:brightness(1.1);transform:translateY(-1px)}}
.scan-btn:disabled{{opacity:.7;cursor:wait}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.scan-spin{{display:inline-block;animation:spin 1s linear infinite}}
.scan-overlay{{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.3);align-items:center;justify-content:center;flex-direction:column}}
.scan-overlay.show{{display:flex}}
.scan-toast{{background:var(--surface);border-radius:var(--radius);box-shadow:var(--shadow-lg);padding:32px 40px;text-align:center;max-width:360px}}
.scan-toast strong{{display:block;margin:16px 0 8px;font-size:16px;color:var(--ink)}}
.scan-toast p{{color:var(--muted);font-size:13px}}
.scan-spinner{{width:40px;height:40px;border:3px solid #e3e8f2;border-top-color:var(--primary);border-radius:50%;animation:spin .7s linear infinite;margin:0 auto}}
.bug-card{{border:1px solid var(--line);border-radius:var(--radius-sm);overflow:hidden;margin-bottom:10px;background:var(--surface)}}
.bug-card-header{{display:flex;align-items:center;gap:10px;padding:12px 14px;background:#f8fafc;border-bottom:1px solid var(--line)}}
.bug-card-header strong{{font-size:13px;font-weight:700;flex:1}}
.bug-card-body{{padding:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}}
.bug-field label{{display:block;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--muted);margin-bottom:3px}}
.bug-field span{{display:block;font-size:12px;color:var(--ink);line-height:1.5}}
.bug-card-danger{{border-left:3px solid var(--danger)}}
.bug-card-warning{{border-left:3px solid var(--warning)}}
.bug-card-info{{border-left:3px solid var(--primary)}}
.onboard-hero{{text-align:center;margin:40px 0 24px}}
.onboard-score{{margin:0 auto 16px;width:100px;height:100px;border-radius:50%;border:4px solid var(--line);display:flex;align-items:center;justify-content:center;flex-direction:column}}
.onboard-score strong{{font-size:36px;font-weight:900;display:block}}
.onboard-score span{{font-size:12px;color:var(--muted)}}
.onboard-hero h1{{font-size:22px;margin:8px 0}}
.onboard-hero p{{color:var(--muted);font-size:14px}}
.onboard-note{{font-size:12px;color:var(--muted)}}
.onboard-check{{display:flex;align-items:flex-start;gap:12px;padding:12px 14px;border:1px solid var(--line);border-radius:var(--radius-sm);margin-bottom:8px;background:var(--surface)}}
.onboard-check .status{{flex-shrink:0;margin-top:2px}}
.onboard-check strong{{display:block;font-size:13px;margin-bottom:2px}}
.onboard-check p{{font-size:11px;color:var(--muted);margin:0}}
.upload-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.upload-zone{{padding:32px 16px;border:2px dashed var(--line);border-radius:var(--radius);text-align:center;cursor:pointer}}
.upload-zone strong{{display:block;margin:8px 0 4px;font-size:14px}}
.upload-zone p{{color:var(--muted);font-size:11px}}
.env-form{{display:flex;flex-direction:column;gap:10px;max-width:500px}}
.env-field{{display:flex;flex-direction:column;gap:4px}}
.env-field label{{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase}}
.env-hint{{font-size:11px;color:var(--muted)}}
.onboard-launch{{text-align:center;margin:32px 0}}
.tone-success{{color:var(--success)}}
.tone-warning{{color:var(--warning)}}
.tone-danger{{color:var(--danger)}}
.bug-meta{{font-size:11px;color:var(--muted);white-space:nowrap}}
.bug-field-full{{grid-column:1 / -1}}
.bug-evidence{{background:#f0f4ff;padding:8px 10px;border-radius:4px;font-size:11px;line-height:1.5;max-height:120px;overflow-y:auto;font-family:monospace;white-space:pre-wrap;word-break:break-all;margin:0}}
.text-danger{{color:var(--danger);font-weight:600}}

/* Content */
.main{{min-width:0;display:flex;flex-direction:column}}
.content{{flex:1;max-width:1440px;width:100%;margin:0 auto;padding:24px 28px 48px}}

/* Hero */
.hero{{position:relative;overflow:hidden;display:grid;grid-template-columns:1fr minmax(200px,.4fr);gap:22px;margin-bottom:20px;padding:26px 28px;border:1px solid #e4e9ff;border-radius:var(--radius);background:linear-gradient(122deg,#fff 0%,#f9fbff 55%,#eff3ff 100%);box-shadow:var(--shadow)}}
.eyebrow{{display:inline-flex;align-items:center;gap:7px;color:var(--primary);font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}}
.hero h1{{max-width:800px;margin:6px 0 12px;font-size:30px;line-height:1.16;letter-spacing:-.02em}}
.hero p{{max-width:800px;margin:0;color:#536282;font-size:14px;line-height:1.55}}
.hero-actions{{display:flex;flex-wrap:wrap;gap:9px;margin-top:18px}}
.hero-art{{position:relative;min-height:120px;align-self:center;border:1px solid rgba(88,101,242,.1);border-radius:13px;background:rgba(255,255,255,.7)}}
.pulse-grid{{position:absolute;inset:16px;display:grid;grid-template-columns:repeat(8,1fr);gap:5px;align-items:end}}
.pulse-grid span{{display:block;border-radius:4px 4px 2px 2px;background:linear-gradient(180deg,#8d99fb,#5865f2);animation:pulse-bar 3s ease-in-out infinite}}
.pulse-grid span:nth-child(1){{height:35%;animation-delay:0s}}.pulse-grid span:nth-child(2){{height:60%;animation-delay:.15s}}.pulse-grid span:nth-child(3){{height:45%;animation-delay:.3s}}.pulse-grid span:nth-child(4){{height:82%;background:linear-gradient(180deg,#34d4bb,#14b8a2);animation-delay:.45s}}.pulse-grid span:nth-child(5){{height:55%;animation-delay:.6s}}.pulse-grid span:nth-child(6){{height:90%;animation-delay:.75s}}.pulse-grid span:nth-child(7){{height:50%;animation-delay:.9s}}.pulse-grid span:nth-child(8){{height:72%;animation-delay:1.05s}}
@keyframes pulse-bar{{0%,100%{{opacity:.8}}50%{{opacity:.45}}}}
.pulse-label{{position:absolute;right:15px;top:12px;color:#8896b8;font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}}

/* Buttons */
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:38px;padding:0 15px;border:1px solid transparent;border-radius:10px;font-size:13px;font-weight:700;transition:var(--transition)}}
.btn-primary{{background:var(--primary);color:#fff}}.btn-primary:hover{{background:var(--primary-hover);transform:translateY(-1px)}}
.btn-secondary{{border-color:#d9e0ef;background:#fff;color:#36445c}}.btn-secondary:hover{{border-color:#c4cee3;background:#f9fafc}}

/* Metrics */
.metric-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}}
.metric-card{{position:relative;overflow:hidden;padding:20px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);box-shadow:var(--shadow-sm);transition:var(--transition)}}
.metric-card:hover{{border-color:#ced6e8;transform:translateY(-2px);box-shadow:0 6px 24px rgba(11,18,33,.06)}}
.metric-head{{display:flex;align-items:center;justify-content:space-between;gap:8px;color:#6f7d98;font-size:11px;font-weight:800;letter-spacing:.01em;text-transform:uppercase}}
.metric-icon{{display:grid;place-items:center;width:32px;height:32px;border-radius:9px;background:#edf1ff;color:var(--primary)}}
.metric-card strong{{display:block;margin-top:12px;color:#0b1424;font-size:26px;font-weight:800;line-height:1;letter-spacing:-.02em}}
.metric-card small{{display:block;margin-top:7px;color:#95a3be;font-size:11px}}
.metric-success .metric-icon{{background:#e6f7f0;color:var(--success)}}
.metric-warning .metric-icon{{background:#fff8ed;color:var(--warning)}}
.metric-danger .metric-icon{{background:#fef1f2;color:var(--danger)}}

/* Panel */
.panel{{margin-bottom:16px;padding:20px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface);box-shadow:var(--shadow-sm)}}
.section-title{{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:15px}}
.section-title h2{{font-size:16px;font-weight:700;letter-spacing:-.01em}}
.section-title p{{margin-top:4px;color:var(--muted);font-size:12px}}

/* Engine cards */
.engine-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}}
.engine-card{{display:flex;align-items:center;gap:12px;padding:13px 14px;border:1px solid var(--line);border-radius:11px;background:var(--surface);transition:var(--transition)}}
.engine-card:hover{{border-color:#c5d1e8;box-shadow:0 2px 10px rgba(11,18,33,.04)}}
.engine-card i{{display:grid;place-items:center;width:34px;height:34px;border-radius:8px;background:#edf1ff;color:var(--primary)}}
.engine-card strong{{display:block;font-size:13px;font-weight:700}}
.engine-card span{{display:block;margin-top:2px;color:var(--muted);font-size:11px}}
.engine-active i{{background:#e6f7f0;color:var(--success)}}
.engine-active span{{color:var(--success)}}
.engine-error i{{background:#fef1f2;color:var(--danger)}}

/* Pipeline */
.pipeline-steps{{display:flex;gap:0;align-items:flex-start;flex-wrap:wrap}}
.pipeline-step{{display:flex;align-items:center;gap:10px;padding:10px 14px;flex:1;min-width:160px}}
.pipeline-step+.pipeline-step{{border-left:2px dashed #e2e8f2;padding-left:20px}}
.step-num{{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#eef1f8;color:#8896b8;font-size:13px;font-weight:800;flex-shrink:0}}
.step-info strong{{display:block;font-size:13px;font-weight:700}}
.step-info span{{display:block;margin-top:2px;color:var(--muted);font-size:11px}}
.stage-done .step-num{{background:#e6f7f0;color:var(--success)}}
.stage-running .step-num{{background:#f3f0ff;color:var(--primary);animation:pulse-dot 1.5s infinite}}
.stage-failed .step-num{{background:#fef1f2;color:var(--danger)}}

/* Table */
.table-wrap{{overflow:auto;border:1px solid #edf0f7;border-radius:12px}}table{{width:100%;border-collapse:collapse;min-width:600px}}th{{padding:10px 12px;background:#f7f9fd;color:#6b7a99;font-size:10px;font-weight:800;text-align:left;text-transform:uppercase;letter-spacing:.04em}}td{{padding:11px 12px;border-top:1px solid #f0f3f9;font-size:13px;vertical-align:middle}}.table-empty{{padding:24px;color:#a0aec0;text-align:center;font-size:13px}}

/* Status */
.status{{display:inline-flex;align-items:center;min-height:23px;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:700;white-space:nowrap}}
.status::before{{content:"";width:6px;height:6px;margin-right:6px;border-radius:50%;background:currentColor}}
.status-success{{background:#e6f7f0;color:#0b8a5f}}
.status-warning{{background:#fff8ed;color:#b35f09}}
.status-danger{{background:#fef1f2;color:#c11d3b}}
.status-info{{background:#eef3ff;color:#3b53db}}
.status-neutral{{background:#f1f4f9;color:#67758d}}

/* Callout */
.callout{{display:flex;gap:12px;padding:13px 15px;border:1px solid;border-radius:11px}}
.callout i{{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;flex-shrink:0}}
.callout strong{{display:block;font-size:12px;font-weight:800}}
.callout p{{margin-top:3px;opacity:.82;font-size:12px}}
.callout-info{{border-color:#d5dcff;background:#f5f7ff;color:var(--primary)}}
.callout-success{{border-color:#b9f0da;background:#f0fdf6;color:var(--success)}}
.callout-warning{{border-color:#fde2a3;background:#fffcf4;color:var(--warning)}}
.callout-danger{{border-color:#fecaca;background:#fff5f5;color:var(--danger)}}

/* Others */
.detail-list{{border:1px solid #edf0f7;border-radius:11px;overflow:hidden}}
.detail-row{{display:grid;grid-template-columns:1fr minmax(70px,.8fr);gap:10px;padding:9px 12px;border-top:1px solid #edf0f7;font-size:13px}}
.detail-row:first-child{{border-top:0}}
.detail-row span{{color:var(--muted);font-size:12px}}
.detail-row b{{color:var(--ink);font-weight:700;text-align:right;word-break:break-word}}
.empty-state{{display:flex;align-items:center;gap:12px;padding:18px;border:1px dashed #cfd9eb;border-radius:11px;background:#fbfcfe;color:#8896b8}}
.empty-state i{{display:grid;place-items:center;width:36px;height:36px;border-radius:9px;background:#edf1ff;color:var(--primary);flex-shrink:0}}
.empty-state strong{{display:block;color:#455166;font-size:13px}}
.empty-state p{{margin-top:3px;font-size:12px}}
.progress-bar{{height:100%;border-radius:99px;transition:width .5s ease}}
.progress-green{{background:linear-gradient(90deg,#22c55e,#16a34a)}}
.progress-amber{{background:linear-gradient(90deg,#f59e0b,#d97706)}}
.progress-red{{background:linear-gradient(90deg,#ef4444,#dc2626)}}
.split{{display:grid;grid-template-columns:1.15fr .85fr;gap:16px}}
.two-col{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}}
.toast{{position:fixed;right:20px;bottom:20px;z-index:30;display:none;padding:11px 16px;border-radius:10px;background:#152035;color:#fff;font-size:12px;font-weight:600;box-shadow:0 10px 30px rgba(0,0,0,.25)}}
.toast.show{{display:block;animation:slideUp .25s ease}}
@keyframes slideUp{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:translateY(0)}}}}

/* Upload */
.upload-zone{{border:2px dashed #c5d1e8;border-radius:13px;padding:28px;text-align:center;background:#fafbfe;transition:var(--transition);cursor:pointer;margin-bottom:12px}}
.upload-zone:hover,.upload-zone.drag-over{{border-color:var(--primary);background:#f3f5ff}}
.upload-zone i{{display:grid;place-items:center;width:44px;height:44px;margin:0 auto 12px;border-radius:11px;background:#edf1ff;color:var(--primary)}}
.upload-zone strong{{display:block;margin-bottom:6px;color:var(--ink);font-size:14px}}
.upload-zone p{{color:var(--muted);font-size:12px;margin:0 0 14px}}
.upload-zone .btn{{margin:0 auto}}
.upload-status{{margin-top:12px;text-align:left}}
.upload-item{{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid #e2e8f2;border-radius:8px;margin-bottom:6px;font-size:12px}}
.upload-item.success{{border-color:#b9f0da;background:#f0fdf6}}
.upload-item.error{{border-color:#fecaca;background:#fff5f5}}
.upload-item .file-icon{{display:grid;place-items:center;width:30px;height:30px;border-radius:6px;background:#edf1ff;color:var(--primary);font-size:10px;font-weight:800;flex-shrink:0}}
.upload-item .file-info{{flex:1;min-width:0}}
.upload-item .file-info span{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.upload-item .file-info small{{color:var(--muted);font-size:10px}}

/* Onboarding */
.onboarding-hero{{margin-bottom:20px;padding:32px;border:2px dashed #bcc7e0;border-radius:var(--radius);background:linear-gradient(135deg,#f8faff,#f0f4ff);text-align:center}}
.onboarding-inner i{{display:grid;place-items:center;width:52px;height:52px;margin:0 auto 16px;border-radius:14px;background:linear-gradient(135deg,#e8ecff,#d4dbff);color:var(--primary)}}
.onboarding-inner h2{{font-size:20px;margin:0 0 8px;font-weight:700}}
.onboarding-inner p{{max-width:580px;margin:0 auto 24px;color:var(--muted);font-size:13px;line-height:1.55}}
.onboarding-steps{{display:flex;align-items:center;justify-content:center;gap:12px;margin-bottom:24px;flex-wrap:wrap}}
.onboard-step{{text-align:center;min-width:100px}}
.onboard-step span{{display:grid;place-items:center;width:32px;height:32px;margin:0 auto 8px;border-radius:50%;background:var(--surface);border:2px solid var(--primary);color:var(--primary);font-size:13px;font-weight:800}}
.onboard-step strong{{display:block;font-size:12px;font-weight:700}}
.onboard-step small{{display:block;margin-top:2px;color:var(--muted);font-size:10px}}
.onboard-arrow{{color:#b0bdd4;font-size:18px;font-weight:700}}
.btn-lg{{min-height:46px;padding:0 24px;font-size:14px;border-radius:12px}}
.btn-delete{{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border:1px solid #fecaca;border-radius:6px;background:#fff5f5;color:#dc2626;font-size:14px;font-weight:700;cursor:pointer;transition:var(--transition)}}.btn-delete:hover{{background:#fef1f2;border-color:#f87171}}
.btn-preview{{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border:1px solid #d5dcff;border-radius:6px;background:#f5f7ff;font-size:12px;cursor:pointer;transition:var(--transition);margin-left:4px}}.btn-preview:hover{{background:#eef3ff}}
.filter-btn{{display:inline-flex;align-items:center;padding:3px 10px;border:1px solid var(--line);border-radius:99px;background:var(--surface);color:var(--muted);font-size:11px;font-weight:600;cursor:pointer;transition:var(--transition)}}.filter-btn:hover{{border-color:var(--primary);color:var(--primary)}}.filter-btn.active{{background:var(--primary);color:#fff;border-color:var(--primary)}}

/* Environment & settings form */
.env-form{{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap}}
.env-fields{{display:flex;gap:12px;flex-wrap:wrap;flex:1}}
.env-fields label{{display:flex;flex-direction:column;gap:4px;font-size:12px;font-weight:600;color:var(--muted);min-width:160px}}
.env-fields input,.env-fields select{{padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius-sm);font:13px var(--font);color:var(--ink);outline:none;transition:var(--transition);background:var(--surface)}}
.env-fields input:focus,.env-fields select:focus{{border-color:var(--primary);box-shadow:0 0 0 3px rgba(88,101,242,.12)}}
.env-msg{{font-size:12px;font-weight:600;padding:6px 0}}

/* Settings page */
.settings-grid{{display:grid;gap:16px}}
.settings-card{{padding:20px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}}
.settings-card h3{{font-size:14px;font-weight:700;margin-bottom:14px}}
.settings-field{{display:flex;align-items:center;gap:12px;padding:8px 0}}
.settings-field label{{font-size:12px;font-weight:600;color:var(--muted);min-width:120px}}
.settings-field input{{flex:1;padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius-sm);font:13px var(--font);color:var(--ink);outline:none}}
.settings-field input:focus{{border-color:var(--primary);box-shadow:0 0 0 3px rgba(88,101,242,.12)}}

/* File preview modal */
.modal-overlay{{display:none;position:fixed;inset:0;z-index:50;background:rgba(11,18,33,.4);align-items:center;justify-content:center}}
.modal-overlay.show{{display:flex}}
.modal{{background:var(--surface);border-radius:var(--radius);box-shadow:var(--shadow-lg);max-width:700px;max-height:80vh;width:90%;overflow:hidden;display:flex;flex-direction:column}}
.modal-header{{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;border-bottom:1px solid var(--line)}}
.modal-header h3{{font-size:15px;font-weight:700}}
.modal-close{{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;border:none;background:#f1f4f9;color:#67758d;cursor:pointer;font-size:16px;font-weight:700;transition:var(--transition)}}
.modal-close:hover{{background:#e2e8f2;color:var(--ink)}}
.modal-body{{flex:1;overflow:auto;padding:20px;font-size:13px;line-height:1.65;white-space:pre-wrap;font-family:var(--font)}}

/* Activity feed */
.activity-item{{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #f0f3f9;font-size:13px}}
.activity-item:last-child{{border-bottom:0}}
.activity-dot{{width:8px;height:8px;border-radius:50%;margin-top:6px;flex-shrink:0;background:var(--primary)}}
.activity-dot.success{{background:var(--success)}}
.activity-dot.warning{{background:var(--warning)}}
.activity-content{{flex:1}}
.activity-content strong{{font-weight:600}}
.activity-content time{{display:block;margin-top:2px;color:var(--muted);font-size:11px}}

@media(max-width:1120px){{.app-shell{{grid-template-columns:72px 1fr}}.brand-copy,.workspace-chip,.nav-label,.nav-item span,.sidebar-foot,.sidebar-action-btn span{{display:none}}.nav-item{{justify-content:center;padding:10px}}.sidebar-action-btn{{justify-content:center;padding:10px}}.hero{{grid-template-columns:1fr}}.hero-art{{display:none}}.metric-grid{{grid-template-columns:repeat(2,1fr)}}.engine-grid{{grid-template-columns:repeat(2,1fr)}}}}
@media(max-width:760px){{.app-shell{{display:block}}.sidebar{{position:relative;height:auto;padding:10px 12px}}.sidebar nav{{display:flex;gap:4px;overflow:auto}}.nav-item{{min-width:40px;margin:0;padding:8px}}.topbar{{position:relative;height:54px;padding:0 16px}}.content{{padding:14px 14px 32px}}h1{{font-size:24px}}.metric-grid{{grid-template-columns:1fr 1fr;gap:8px}}.split,.two-col{{grid-template-columns:1fr}}.pipeline-steps{{flex-direction:column}}.pipeline-step+.pipeline-step{{border-left:none;border-top:2px dashed #e2e8f2;padding-left:14px}}}}
</style>
</head>
<body>
<div class='app-shell'>
  <aside class='sidebar'>
    <div class='brand'><span class='brand-mark'>{_icon('shield')}</span><span class='brand-copy'><strong>QualiBug</strong><span>Quality OS</span></span></div>
    <div class='workspace-chip'><span>Project</span><b>{project}</b></div>
    <div class='nav-label'>Quality Control</div>
    <nav>{_nav(str(project_id or 'real_project_demo'), active)}</nav>
            <div class='sidebar-actions'>
              <a class='sidebar-action-btn' href='/settings?project={project}' title='系统设置'><i>{_icon('settings')}</i><span>设置</span></a>
            </div>
    <div class='sidebar-foot'><b>{h(environment_label)}</b>Credentials referenced, approval gated, audit chained, production protected.</div>
  </aside>
  <main class='main'>
    <header class='topbar'>
      <span class='crumb'><b>QualiBug</b> / {h(page_hint or title)}</span>
      <div class='topbar-right'><span class='llm-pill {llm_class}'>{llm_label}</span><button class='scan-btn' onclick='triggerScan()' title='Run bug scan'><i>{_icon('play')}</i>开始扫描</button><button class='btn btn-ghost' data-download title='Export snapshot'>Export snapshot</button><span class='runtime-pill'>{h(environment_label)}</span><span class='avatar'>QB</span></div>
    </header>
    <div class='content'>
      <section class='hero'>
        <div><div class='eyebrow'><i>{_icon('spark')}</i>{h(eyebrow)}</div><h1>{h(headline)}</h1><p>{h(description)}</p><div class='hero-actions'>{page_actions}</div></div>
        <div class='hero-art'><span class='pulse-label'>Signal</span><div class='pulse-grid'><span></span><span></span><span></span><span></span><span></span><span></span><span></span><span></span></div></div>
      </section>
      {body}
    </div>
  </main>
</div>
<div class='scan-overlay' id='scan-overlay'>
  <div class='scan-toast'>
    <div class='scan-spinner'></div>
    <strong id='scan-title'>正在运行 Bug 扫描...</strong>
    <p id='scan-sub'>正在分析业务模型、执行推理引擎</p>
  </div>
</div>
<div class='toast'></div>
<div class='modal-overlay' id='file-modal' onclick='if(event.target===this)closeModal()'>
  <div class='modal'>
    <div class='modal-header'><h3 id='modal-title'>文件预览</h3><button class='modal-close' onclick='closeModal()'>×</button></div>
    <div class='modal-body' id='modal-body'></div>
  </div>
</div>
<script id='qualibug-payload' type='application/json'>{payload_json}</script>
<script>
(function(){{
  const t=document.querySelector('.toast');
  function toast(m){{if(!t)return;t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200)}}
  document.querySelectorAll('[data-refresh]').forEach(b=>b.addEventListener('click',()=>location.reload()));
  document.querySelectorAll('[data-download]').forEach(b=>b.addEventListener('click',()=>{{
    try{{const p=JSON.parse(document.getElementById('qualibug-payload').textContent||'{{}}');const bl=new Blob([JSON.stringify(p,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(bl);a.download='qualibug-{project}-snapshot.json';a.click();URL.revokeObjectURL(a.href);toast('Snapshot exported.')}}
    catch(e){{toast('Export failed')}}
  }}));
  document.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',async()=>{{try{{await navigator.clipboard.writeText(b.dataset.copy||'');toast('Copied')}}catch{{toast('Clipboard unavailable')}}}}));

  // === File Preview Modal ===
  window.closeModal=function(){{document.getElementById('file-modal').classList.remove('show')}};
  window.previewFile=function(sid,name,type,url){{
    document.getElementById('modal-title').textContent=name||'文件预览';
    document.getElementById('modal-body').textContent='加载中...';
    document.getElementById('file-modal').classList.add('show');
    fetch(url)
      .then(r=>r.json())
      .then(data=>{{
        if(data.ok){{document.getElementById('modal-title').textContent=data.filename||name||'文件预览';document.getElementById('modal-body').textContent=(data.content||'').slice(0,80000)||'(空文件)'}}
        else{{document.getElementById('modal-body').textContent=data.message||data.error||'预览失败'}}
      }})
      .catch(e=>{{document.getElementById('modal-body').textContent='加载失败: '+e.message}});
  }};

  // === One-click Scan ===
  window.triggerScan=async function(){{
    var overlay=document.getElementById('scan-overlay');
    var title=document.getElementById('scan-title');
    var sub=document.getElementById('scan-sub');
    var btns=document.querySelectorAll('.scan-btn');
    btns.forEach(function(b){{b.disabled=true;b.innerHTML='<span class=scan-spin>↻</span> 扫描中...'}});
    if(overlay){{overlay.classList.add('show');if(title)title.textContent='正在运行 Bug 扫描...';if(sub)sub.textContent='阶段 1/5: 文档解析与业务提取';}}
    var stages=['文档解析与业务提取','探针生成与执行','LLM语义推理','影响分析','报告生成'];
    var stageIdx=0;
    var stageTimer=setInterval(function(){{
      stageIdx=(stageIdx+1)%stages.length;
      if(sub)sub.textContent='阶段 '+(stageIdx+1)+'/5: '+stages[stageIdx];
    }},3000);
    try{{
      var resp=await fetch('/api/scan/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project_id:'{project}'}})}});
      clearInterval(stageTimer);
      var r=await resp.json();
      if(r.ok){{
        if(title)title.textContent='\u2713 扫描完成';
        var n=(r.stage2_discovery||{{}}).total_findings||0;
        if(sub)sub.textContent='发现 '+n+' 个 Bug，页面即将刷新...';
        setTimeout(function(){{location.reload()}},1500);
      }}else{{
        if(title)title.textContent='\u2717 扫描失败';
        if(sub)sub.textContent=(r.message||'未知错误');
        setTimeout(function(){{if(overlay)overlay.classList.remove('show');btns.forEach(function(b){{b.disabled=false;b.innerHTML='\u25b6 开始扫描'}});}},2500);
      }}
    }}catch(e){{
      clearInterval(stageTimer);
      if(title)title.textContent='\u2717 网络错误';
      if(sub)sub.textContent=e.message;
      setTimeout(function(){{if(overlay)overlay.classList.remove('show');btns.forEach(function(b){{b.disabled=false;b.innerHTML='\u25b6 开始扫描'}});}},2500);
    }}
  }};

  // === Delete Source ===
  window.deleteSource=async function(sid,name){{
    if(!confirm('确认删除 '+(name||sid)+'？'))return;
    try{{
      var resp=await fetch('/api/knowledge/delete',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project_id:'{project}',source_id:sid}})}});
      var r=await resp.json();
      if(r.ok){{toast(r.message);setTimeout(function(){{location.reload()}},500)}}
      else{{toast('删除失败: '+(r.message||''))}}
    }}catch(e){{toast('删除失败: '+e.message)}}
  }};

  // === Environment Config ===
  window.saveEnvConfig=async function(e){{
    e.preventDefault();
    var f=new FormData(e.target);
    var data={{}};
    f.forEach(function(v,k){{data[k]=v}});
    data.payload={{target_environment:data.target_environment,base_url:data.base_url,request_timeout_seconds:parseInt(data.timeout)||10}};
    try{{
      var resp=await fetch('/api/environment/config',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
      var r=await resp.json();
      var m=document.getElementById('env-msg');
      if(r.ok||!r.error){{m.textContent='✓ 环境配置已保存';m.style.color='var(--success)';setTimeout(function(){{location.reload()}},800)}}
      else{{m.textContent='✗ '+(r.message||'保存失败');m.style.color='var(--danger)'}}
    }}catch(err){{var m=document.getElementById('env-msg');m.textContent='✗ '+err.message;m.style.color='var(--danger)'}}
  }};

  // === Save Settings ===
  window.saveSettings=async function(e){{
    e.preventDefault();
    var f=new FormData(e.target);
    var data={{}};
    f.forEach(function(v,k){{data[k]=v}});
    try{{
      var resp=await fetch('/api/settings/save',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
      var r=await resp.json();
      var m=document.getElementById('settings-msg');
      if(r.ok){{m.textContent='✓ 配置已保存。LLM '+(r.llm_status_label||'状态待验证')+(r.llm_error?'：'+r.llm_error:'')+'。';m.style.color=r.llm_status==='online'?'var(--success)':'var(--warning)'}}
      else{{m.textContent='✗ '+(r.message||'保存失败');m.style.color='var(--danger)'}}
    }}catch(err){{var m=document.getElementById('settings-msg');m.textContent='✗ '+err.message;m.style.color='var(--danger)'}}
  }};
  // === Save Connector ===
  window.saveConnector=async function(e){{
    e.preventDefault();
    var f=new FormData(e.target);
    var data={{}};
    f.forEach(function(v,k){{data[k]=v}});
    try{{
      var resp=await fetch('/api/connectors/register',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(data)}});
      var r=await resp.json();
      var m=document.getElementById('conn-msg');
      if(r.ok){{m.textContent='✓ 连接器已注册';m.style.color='var(--success)'}}
      else{{m.textContent='✗ '+(r.message||'注册失败');m.style.color='var(--danger)'}}
    }}catch(err){{var m=document.getElementById('conn-msg');m.textContent='✗ '+err.message;m.style.color='var(--danger)'}}
  }};

  // === Re-analyze Knowledge ===
  window.reanalyze=async function(){{
    toast('正在重新分析...');
    try{{
      var resp=await fetch('/api/knowledge/reanalyze',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project_id:'{project}'}})}});
      var r=await resp.json();
      if(r.ok){{toast('✓ 重新分析完成');setTimeout(function(){{location.reload()}},1000)}}
      else{{toast('✗ 分析失败: '+(r.message||''))}}
    }}catch(e){{toast('✗ 分析失败: '+e.message)}}
  }};

  // === Upload ===
  var zone=document.getElementById('upload-zone');
  var input=document.getElementById('file-input');
  var status=document.getElementById('upload-status');
  if(zone&&input&&status){{
    zone.addEventListener('click',function(e){{if(e.target.tagName!=='BUTTON')input.click()}});
    zone.addEventListener('dragover',function(e){{e.preventDefault();zone.classList.add('drag-over')}});
    zone.addEventListener('dragleave',function(){{zone.classList.remove('drag-over')}});
    zone.addEventListener('drop',function(e){{e.preventDefault();zone.classList.remove('drag-over');handleFiles(e.dataTransfer.files)}});
    input.addEventListener('change',function(){{handleFiles(input.files)}});
    function handleFiles(files){{
      if(!files.length)return;
      status.innerHTML='';
      for(var i=0;i<files.length;i++){{
        var file=files[i];
        var item=document.createElement('div');
        item.className='upload-item';
        item.innerHTML='<span class=file-icon>'+file.name.split('.').pop().toUpperCase().slice(0,4)+'</span><div class=file-info><span>'+file.name+'</span><small>上传中...</small></div>';
        status.appendChild(item);
        uploadFile(file,item);
      }}
    }}
    async function uploadFile(file,item){{
      try{{
        var b64=await new Promise(function(resolve,reject){{var r=new FileReader();r.onload=function(){{resolve(r.result.split(',')[1])}};r.onerror=reject;r.readAsDataURL(file)}});
        var resp=await fetch('/api/knowledge/ingest',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{project_id:'{project}',type:file.name.endsWith('.yaml')||file.name.endsWith('.json')?'openapi':'prd',filename:file.name,content:b64}})}});
        var r=await resp.json();
        if(r.ok){{item.className='upload-item success';item.querySelector('small').textContent='\\u2713 '+r.message}}
        else{{item.className='upload-item error';item.querySelector('small').textContent='\\u2717 '+(r.message||'Failed')}}
      }}catch(e){{item.className='upload-item error';item.querySelector('small').textContent='\\u2717 '+e.message}}
      setTimeout(function(){{location.reload()}},2500);
    }}
  }}
}})();
</script>
</body>
</html>"""
