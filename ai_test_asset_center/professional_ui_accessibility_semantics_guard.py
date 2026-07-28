"""Accuracy guards for deterministic accessibility rules.

The first engine draft intentionally exposed its rule algorithms for hardening.
This installer corrects three places where a permissive approximation could
produce a misleading formal result:

* target-size spacing uses the WCAG 24 CSS-pixel centre-circle distance;
* focus visibility compares focused and unfocused computed styles instead of
  treating an ordinary static border as a focus indicator;
* translucent foreground/background layers remain untestable rather than being
  approximately composited into a potentially false contrast verdict.
"""
from __future__ import annotations

import copy
from typing import Any

from . import professional_ui_accessibility_engine as _engine

_INSTALL_MARKER = "_qualibug_accessibility_semantics_guard_installed"
_ORIGINAL_FOCUS_AUDIT = "_qualibug_accessibility_focus_audit_before_semantics_guard"

_BASELINE_SCRIPT = r"""
config => {
  const excluded = (config.exclude_selectors || []).flatMap(selector => {
    try { return Array.from(document.querySelectorAll(selector)); } catch (_) { return []; }
  });
  const isExcluded = el => excluded.some(root => root === el || root.contains(el));
  const visible = el => {
    if (!el || !(el instanceof Element) || isExcluded(el)) return false;
    const style = getComputedStyle(el), rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
  };
  const pathOf = el => {
    const parts=[]; let current=el;
    while (current && current !== document.documentElement && parts.length < 8) {
      const parent=current.parentElement;
      const index=parent ? Array.from(parent.children).indexOf(current)+1 : 1;
      parts.unshift(`${current.tagName.toLowerCase()}:nth-child(${index})`);
      current=parent;
    }
    return parts.join('>');
  };
  const styleOf = el => {
    const style=getComputedStyle(el);
    return {
      outlineStyle:style.outlineStyle,
      outlineWidth:style.outlineWidth,
      outlineColor:style.outlineColor,
      outlineOffset:style.outlineOffset,
      boxShadow:style.boxShadow,
      borderTop:style.borderTop,
      borderRight:style.borderRight,
      borderBottom:style.borderBottom,
      borderLeft:style.borderLeft,
      backgroundColor:style.backgroundColor
    };
  };
  const candidates=Array.from(document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[contenteditable="true"],[tabindex]'))
    .filter(el => visible(el) && !el.matches('[disabled],[inert],[aria-disabled="true"]') && Number(el.getAttribute('tabindex') || 0) >= 0);
  const styles={};
  candidates.forEach(el => { styles[pathOf(el)] = styleOf(el); });
  return {count:candidates.length, styles, scrollX:window.scrollX, scrollY:window.scrollY};
}
"""

_FOCUSED_SCRIPT = r"""
() => {
  const el=document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return {active:false};
  const style=getComputedStyle(el), rect=el.getBoundingClientRect();
  const path=(() => {
    const parts=[]; let current=el;
    while (current && current !== document.documentElement && parts.length < 8) {
      const parent=current.parentElement;
      const index=parent ? Array.from(parent.children).indexOf(current)+1 : 1;
      parts.unshift(`${current.tagName.toLowerCase()}:nth-child(${index})`);
      current=parent;
    }
    return parts.join('>');
  })();
  const points=[
    [rect.left+rect.width/2,rect.top+rect.height/2],
    [rect.left+1,rect.top+1],
    [rect.right-1,rect.top+1],
    [rect.left+1,rect.bottom-1],
    [rect.right-1,rect.bottom-1]
  ].filter(([x,y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight);
  const exposed=points.some(([x,y]) => {
    const top=document.elementFromPoint(x,y);
    return top === el || !!(top && el.contains(top));
  });
  let pseudo=false;
  try { pseudo=el.matches(':focus-visible'); } catch (_) { pseudo=false; }
  return {
    active:true,
    path,
    node:{tag:el.tagName.toLowerCase(),id:el.id||'',role:el.getAttribute('role')||'',type:el.getAttribute('type')||'',path},
    style:{
      outlineStyle:style.outlineStyle,
      outlineWidth:style.outlineWidth,
      outlineColor:style.outlineColor,
      outlineOffset:style.outlineOffset,
      boxShadow:style.boxShadow,
      borderTop:style.borderTop,
      borderRight:style.borderRight,
      borderBottom:style.borderBottom,
      borderLeft:style.borderLeft,
      backgroundColor:style.backgroundColor
    },
    focusVisiblePseudo:pseudo,
    exposed
  };
}
"""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _style_indicator_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    outline_width = str(after.get("outlineWidth") or "0").strip()
    outline_style = str(after.get("outlineStyle") or "none").strip().lower()
    outline_active = outline_style not in {"", "none", "hidden"} and outline_width not in {
        "",
        "0",
        "0px",
    }
    if outline_active and (
        after.get("outlineStyle") != before.get("outlineStyle")
        or after.get("outlineWidth") != before.get("outlineWidth")
        or after.get("outlineColor") != before.get("outlineColor")
        or after.get("outlineOffset") != before.get("outlineOffset")
    ):
        return True
    if after.get("boxShadow") not in {"", "none", None} and (
        after.get("boxShadow") != before.get("boxShadow")
    ):
        return True
    for key in ("borderTop", "borderRight", "borderBottom", "borderLeft", "backgroundColor"):
        if after.get(key) != before.get(key):
            return True
    return False


def _focus_audit(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    selected = set(step["rules"]) & _engine._FOCUS_RULES
    if not selected:
        return {"findings": [], "untestable": [], "checked": 0, "truncated": False}
    page.evaluate(
        "() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); }"
    )
    baseline = _dict(page.evaluate(
        _BASELINE_SCRIPT,
        {"exclude_selectors": list(step["exclude_selectors"])},
    ))
    styles = {
        str(key): _dict(value)
        for key, value in _dict(baseline.get("styles")).items()
    }
    candidate_count = int(baseline.get("count") or 0)
    limit = min(candidate_count, int(step["max_focus_checks"]))
    findings: list[dict[str, Any]] = []
    untestable: list[dict[str, Any]] = []
    seen: set[str] = set()
    checked = 0
    try:
        for _index in range(limit):
            page.keyboard.press("Tab")
            observation = _dict(page.evaluate(_FOCUSED_SCRIPT))
            if observation.get("active") is not True:
                break
            path = str(observation.get("path") or "")
            if not path or path in seen:
                break
            seen.add(path)
            checked += 1
            node = copy.deepcopy(_dict(observation.get("node")))
            before = _dict(styles.get(path))
            after = _dict(observation.get("style"))
            if "focus_visible" in selected:
                visible = observation.get("focusVisiblePseudo") is True or _style_indicator_changed(before, after)
                if not visible:
                    findings.append({"rule": "focus_visible", "node": node, "detail": "indicator_missing"})
            if "focus_not_obscured" in selected and observation.get("exposed") is not True:
                findings.append({"rule": "focus_not_obscured", "node": node, "detail": "focused_element_obscured"})
    finally:
        page.evaluate(
            "point => { window.scrollTo(point.x || 0, point.y || 0); if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); }",
            {"x": float(baseline.get("scrollX") or 0), "y": float(baseline.get("scrollY") or 0)},
        )
    truncated = candidate_count > limit
    if candidate_count == 0:
        untestable.extend(
            {"rule": rule, "reason": "no_keyboard_focus_candidates", "count": 1}
            for rule in sorted(selected)
        )
    elif checked < min(candidate_count, limit):
        untestable.extend(
            {"rule": rule, "reason": "keyboard_focus_cycle_incomplete", "count": candidate_count - checked}
            for rule in sorted(selected)
        )
    elif truncated:
        untestable.extend(
            {"rule": rule, "reason": "focus_candidate_limit_exceeded", "count": candidate_count - limit}
            for rule in sorted(selected)
        )
    return {
        "findings": findings,
        "untestable": untestable,
        "checked": checked,
        "candidate_count": candidate_count,
        "truncated": truncated,
    }


def install_professional_ui_accessibility_semantics_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original_focus = getattr(
        _engine,
        _ORIGINAL_FOCUS_AUDIT,
        _engine._focus_audit,
    )
    setattr(_engine, _ORIGINAL_FOCUS_AUDIT, original_focus)
    script = _engine._DOM_AUDIT_SCRIPT
    script = script.replace(
        "return Math.abs(cx - ocx) < 24 && Math.abs(cy - ocy) < 24;",
        "return Math.hypot(cx - ocx, cy - ocy) < 24;",
    )
    script = script.replace(
        "let found = false;",
        "let found = true;",
    )
    script = script.replace(
        "background = candidate.a < 1 ? blend(candidate, background) : candidate;\n            found = true;\n            if (candidate.a >= 1) break;",
        "if (candidate.a < 1) { complex += 1; return; }\n            background = candidate;\n            found = true;\n            break;",
    )
    script = script.replace(
        "const fg = foreground.a < 1 ? blend(foreground, background) : foreground;",
        "if (foreground.a < 1) { complex += 1; return; }\n        const fg = foreground;",
    )
    _engine._DOM_AUDIT_SCRIPT = script
    _engine._focus_audit = _focus_audit
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_accessibility_semantics_guard"]
