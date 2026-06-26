from __future__ import annotations

import base64
import json
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.request import Request, urlopen

from aitestops.yaml_writer import dump_yaml


PLACEHOLDER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@dataclass
class UIJourneyConfig:
    project: str = "enterprise_shop"
    base_url: str = "http://127.0.0.1:8000"
    username: str = "alice"
    password: str = "Alice123!"
    admin_username: str = "admin"
    admin_password: str = "Admin123!"
    execute_browser: bool = False
    browser: str = "chromium"
    headless: bool = True
    max_pages: int = 8
    mode: str = "auto"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def ensure_placeholder_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(PLACEHOLDER_PNG)


def is_reachable(url: str, timeout: int = 3) -> tuple[bool, str | None]:
    if not url:
        return False, "base_url is empty"
    try:
        req = Request(url, headers={"User-Agent": "AI-UI-Journey-Tester/12.0"})
        with urlopen(req, timeout=timeout) as resp:  # nosec - user-configured SUT URL
            return 200 <= getattr(resp, "status", 200) < 500, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


class UIJourneyGenerator:
    """Generate UI Journey DSL from product intent, not Playwright code."""

    def generate(self, prd_text: str, openapi: Dict[str, Any], config: UIJourneyConfig) -> Dict[str, Any]:
        text = (prd_text or "").lower()
        paths = " ".join((openapi.get("paths") or {}).keys()).lower() if isinstance(openapi, dict) else ""
        ecommerce = any(k in text + paths for k in ["checkout", "cart", "coupon", "order", "product", "购物车", "优惠券", "下单"])
        if ecommerce:
            steps = [
                {"intent": "open_home_page"},
                {"intent": "login", "actor": "normal_user"},
                {"intent": "search_product", "keyword": "headphones"},
                {"intent": "open_first_product"},
                {"intent": "add_to_cart"},
                {"intent": "open_cart"},
                {"intent": "apply_coupon", "coupon": "WELCOME10"},
                {"intent": "checkout"},
                {"intent": "verify_order_created"},
            ]
            title = "Normal user checkout smoke journey"
        else:
            steps = [
                {"intent": "open_home_page"},
                {"intent": "login", "actor": "normal_user"},
                {"intent": "verify_order_created"},
            ]
            title = "Core user smoke journey"
        return {
            "journey_id": "ecommerce_checkout_smoke",
            "title": title,
            "priority": "P0",
            "actor": "normal_user",
            "mode": config.mode,
            "base_url": config.base_url,
            "steps": steps,
        }


class BrowserExplorer:
    """Explore pages and produce semantic page and element maps."""

    def explore(self, config: UIJourneyConfig, workspace: Path, output: Path) -> Dict[str, Any]:
        explorer_dir = workspace / "ui_explorer"
        screenshot = output / "ui" / "screenshots" / "explorer_home.png"
        explorer_dir.mkdir(parents=True, exist_ok=True)
        screenshot.parent.mkdir(parents=True, exist_ok=True)

        reachable, reach_error = is_reachable(config.base_url)
        if not config.execute_browser or not reachable:
            ensure_placeholder_png(screenshot)
            page_map = {
                "status": "skipped",
                "reason": "browser execution disabled" if not config.execute_browser else f"SUT not reachable: {reach_error}",
                "base_url": config.base_url,
                "pages": [
                    {
                        "url": config.base_url,
                        "title": "Unexplored SUT",
                        "links": [],
                        "text_fragments": ["Start SUT and enable browser execution to explore real pages."],
                        "screenshot": str(screenshot),
                    }
                ],
                "edges": [],
                "max_pages": config.max_pages,
            }
            element_map = {"status": "synthetic", "elements": self.synthetic_elements(config.base_url)}
            write_json(explorer_dir / "page_map.json", page_map)
            write_json(explorer_dir / "element_map.json", element_map)
            return {"page_map": page_map, "element_map": element_map}

        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:
            ensure_placeholder_png(screenshot)
            page_map = {
                "status": "skipped",
                "reason": f"Playwright is not available: {exc}",
                "base_url": config.base_url,
                "pages": [{"url": config.base_url, "title": "Playwright unavailable", "links": [], "text_fragments": [], "screenshot": str(screenshot)}],
                "edges": [],
                "max_pages": config.max_pages,
            }
            element_map = {"status": "synthetic", "elements": self.synthetic_elements(config.base_url)}
            write_json(explorer_dir / "page_map.json", page_map)
            write_json(explorer_dir / "element_map.json", element_map)
            return {"page_map": page_map, "element_map": element_map}

        pages: List[Dict[str, Any]] = []
        elements: List[Dict[str, Any]] = []
        edges: List[Dict[str, str]] = []
        with sync_playwright() as p:
            browser = getattr(p, config.browser).launch(headless=config.headless)
            page = browser.new_page()
            page.goto(config.base_url, wait_until="domcontentloaded", timeout=8000)
            page.screenshot(path=str(screenshot), full_page=True)
            pages.append(self.page_snapshot(page, screenshot))
            elements.extend(self.element_snapshot(page, config.base_url))
            links = page.locator("a[href]").evaluate_all("(els) => els.slice(0, 20).map(e => e.href)")
            for href in links[: max(0, config.max_pages - 1)]:
                edges.append({"from": config.base_url, "to": href})
            browser.close()

        page_map = {"status": "explored", "base_url": config.base_url, "pages": pages, "edges": edges, "max_pages": config.max_pages}
        element_map = {"status": "explored", "elements": elements}
        write_json(explorer_dir / "page_map.json", page_map)
        write_json(explorer_dir / "element_map.json", element_map)
        return {"page_map": page_map, "element_map": element_map}

    def page_snapshot(self, page: Any, screenshot: Path) -> Dict[str, Any]:
        text_fragments = page.locator("body").inner_text(timeout=2000).splitlines()[:30]
        links = page.locator("a[href]").evaluate_all("(els) => els.slice(0, 30).map(e => ({text: e.innerText, href: e.href}))")
        return {"url": page.url, "title": page.title(), "links": links, "text_fragments": text_fragments, "screenshot": str(screenshot)}

    def element_snapshot(self, page: Any, page_url: str) -> List[Dict[str, Any]]:
        js = """
        (els) => els.slice(0, 200).map((e, i) => ({
          tag: e.tagName.toLowerCase(),
          text: (e.innerText || e.value || '').trim().slice(0, 80),
          testid: e.getAttribute('data-testid'),
          placeholder: e.getAttribute('placeholder'),
          aria_label: e.getAttribute('aria-label'),
          role: e.getAttribute('role') || (e.tagName.toLowerCase() === 'button' ? 'button' : null),
          href: e.getAttribute('href'),
          type: e.getAttribute('type'),
          name: e.getAttribute('name'),
          id: e.id,
          css: e.id ? `#${e.id}` : e.getAttribute('data-testid') ? `[data-testid="${e.getAttribute('data-testid')}"]` : e.tagName.toLowerCase()
        }))
        """
        raw = page.locator("button,a,[role=button],input,textarea,select,[data-testid]").evaluate_all(js)
        return [self.enrich_element(item, page_url, i) for i, item in enumerate(raw)]

    def synthetic_elements(self, page_url: str) -> List[Dict[str, Any]]:
        seeds = [
            ("login_button", "login button", "button", "Login"),
            ("username_input", "username field", "textbox", ""),
            ("password_input", "password field", "textbox", ""),
            ("search_input", "product search field", "textbox", ""),
            ("add_to_cart_button", "add to cart button", "button", "Add to Cart"),
            ("checkout_button", "checkout button", "button", "Checkout"),
        ]
        return [
            {
                "element_id": eid,
                "semantic_name": name,
                "page_url": page_url,
                "role": role,
                "text": text,
                "testid": eid.replace("_", "-"),
                "placeholder": "Username" if "username" in eid else "Password" if "password" in eid else None,
                "aria_label": name,
                "css_candidates": [f"[data-testid='{eid.replace('_', '-')}']", "button" if role == "button" else "input"],
                "xpath_candidate": f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{text.lower()}')]",
                "nearby_text": ["username", "password"] if "login" in eid else [],
                "confidence": 0.72,
            }
            for eid, name, role, text in seeds
        ]

    def enrich_element(self, item: Dict[str, Any], page_url: str, index: int) -> Dict[str, Any]:
        text = item.get("text") or item.get("placeholder") or item.get("aria_label") or item.get("name") or item.get("id") or item.get("tag")
        element_id = self.semantic_id(str(text), item.get("tag"), index)
        css_candidates = []
        if item.get("testid"):
            css_candidates.append(f"[data-testid='{item['testid']}']")
        if item.get("id"):
            css_candidates.append(f"#{item['id']}")
        if item.get("css"):
            css_candidates.append(item["css"])
        return {
            "element_id": element_id,
            "semantic_name": str(text),
            "page_url": page_url,
            "role": item.get("role") or ("link" if item.get("tag") == "a" else "textbox" if item.get("tag") in {"input", "textarea", "select"} else item.get("tag")),
            "text": item.get("text"),
            "testid": item.get("testid"),
            "placeholder": item.get("placeholder"),
            "aria_label": item.get("aria_label"),
            "href": item.get("href"),
            "css_candidates": list(dict.fromkeys(css_candidates)),
            "xpath_candidate": f"//*[contains(normalize-space(), {json.dumps(str(text))})]",
            "nearby_text": [],
            "confidence": 0.92 if item.get("testid") or item.get("aria_label") or item.get("placeholder") else 0.68,
        }

    @staticmethod
    def semantic_id(text: str, tag: str | None, index: int) -> str:
        clean = "".join(ch.lower() if ch.isalnum() else "_" for ch in text).strip("_")[:32]
        return clean or f"{tag or 'element'}_{index}"


class SelfHealingLocator:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.notes: List[Dict[str, Any]] = []

    def record(self, failed_intent: str, original_locator: str, strategy: str, new_locator: str, confidence: float, result: str, before: str = "", after: str = "") -> None:
        self.notes.append(
            {
                "failed_intent": failed_intent,
                "original_locator": original_locator,
                "healing_strategy": strategy,
                "new_locator": new_locator,
                "confidence": confidence,
                "result": result,
                "screenshot_before": before,
                "screenshot_after": after,
            }
        )
        write_json(self.workspace / "ui_execution" / "self_healing_notes.json", self.notes)


class EvidenceCollector:
    def __init__(self, workspace: Path, output: Path):
        self.workspace = workspace
        self.output = output
        for sub in ["screenshots", "traces", "dom_snapshots", "network_logs", "console_logs"]:
            (output / "ui" / sub).mkdir(parents=True, exist_ok=True)

    def step_evidence(self, journey_id: str, index: int, intent: str, page: Any | None, status: str, error: str = "") -> Dict[str, Any]:
        safe_intent = "".join(ch if ch.isalnum() else "_" for ch in intent)
        screenshot = self.output / "ui" / "screenshots" / f"{index:02d}_{safe_intent}.png"
        dom_path = self.output / "ui" / "dom_snapshots" / f"{index:02d}_{safe_intent}.html"
        console_path = self.output / "ui" / "console_logs" / f"{index:02d}_{safe_intent}.json"
        network_path = self.output / "ui" / "network_logs" / f"{index:02d}_{safe_intent}.json"
        current_url = ""
        if page is not None:
            try:
                current_url = page.url
                page.screenshot(path=str(screenshot), full_page=True)
                dom_path.write_text(page.content(), encoding="utf-8", errors="replace")
            except Exception:
                ensure_placeholder_png(screenshot)
                dom_path.write_text("", encoding="utf-8")
        else:
            ensure_placeholder_png(screenshot)
            dom_path.write_text("", encoding="utf-8")
        write_json(console_path, [])
        write_json(network_path, [])
        return {
            "journey_id": journey_id,
            "step_index": index,
            "intent": intent,
            "status": status,
            "current_url": current_url,
            "screenshot": str(screenshot),
            "dom_snapshot": str(dom_path),
            "console_log": str(console_path),
            "network_log": str(network_path),
            "error": error,
        }

    def bundle(self, execution_result: Dict[str, Any], page_map: Dict[str, Any], element_map: Dict[str, Any]) -> Dict[str, Any]:
        bundle = {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "execution_result": execution_result,
            "page_map_path": str(self.workspace / "ui_explorer" / "page_map.json"),
            "element_map_path": str(self.workspace / "ui_explorer" / "element_map.json"),
            "page_count": len(page_map.get("pages", [])),
            "element_count": len(element_map.get("elements", [])),
            "evidence_root": str(self.output / "ui"),
        }
        write_json(self.workspace / "ui_execution" / "evidence_bundle.json", bundle)
        return bundle


class IntentExecutor:
    """Execute business intents. Playwright is only the browser engine."""

    def execute(self, journey: Dict[str, Any], config: UIJourneyConfig, workspace: Path, output: Path, element_map: Dict[str, Any]) -> Dict[str, Any]:
        collector = EvidenceCollector(workspace, output)
        healer = SelfHealingLocator(workspace)
        started = time.time()
        steps = journey.get("steps", [])
        reachable, reach_error = is_reachable(config.base_url)
        if not config.execute_browser or not reachable:
            step_results = []
            for i, step in enumerate(steps, start=1):
                intent = step.get("intent", "unknown")
                status = "skipped"
                error = "browser execution disabled" if not config.execute_browser else f"SUT not reachable: {reach_error}"
                if i == 1:
                    healer.record(intent, "base_url", "graceful_degrade", config.base_url, 1.0, "skipped")
                step_results.append(collector.step_evidence(journey["journey_id"], i, intent, None, status, error))
            result = self.result(journey, "skipped", step_results, started, graceful_degrade_reason=step_results[0]["error"] if step_results else "")
            write_json(workspace / "ui_execution" / "ui_execution_result.json", result)
            return result

        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:
            step_results = [collector.step_evidence(journey["journey_id"], 1, "bootstrap_browser", None, "failed", f"Playwright unavailable: {exc}")]
            result = self.result(journey, "failed", step_results, started)
            write_json(workspace / "ui_execution" / "ui_execution_result.json", result)
            return result

        step_results: List[Dict[str, Any]] = []
        with sync_playwright() as p:
            browser = getattr(p, config.browser).launch(headless=config.headless)
            page = browser.new_page()
            for i, step in enumerate(steps, start=1):
                intent = step.get("intent", "unknown")
                try:
                    self.perform_intent(page, intent, step, config, element_map, healer)
                    step_results.append(collector.step_evidence(journey["journey_id"], i, intent, page, "passed"))
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    healer.record(intent, "semantic resolver", "record_failure_for_triage", "no viable locator", 0.0, "failed")
                    step_results.append(collector.step_evidence(journey["journey_id"], i, intent, page, "failed", err))
                    break
            browser.close()
        status = "passed" if step_results and all(s["status"] == "passed" for s in step_results) and len(step_results) == len(steps) else "failed"
        result = self.result(journey, status, step_results, started)
        write_json(workspace / "ui_execution" / "ui_execution_result.json", result)
        return result

    def perform_intent(self, page: Any, intent: str, step: Dict[str, Any], config: UIJourneyConfig, element_map: Dict[str, Any], healer: SelfHealingLocator) -> None:
        if intent == "open_home_page":
            page.goto(config.base_url, wait_until="domcontentloaded", timeout=10000)
            return
        if intent == "login":
            self.fill_first(page, ["username", "user", "email", "账号", "用户名"], config.username, healer, intent)
            self.fill_first(page, ["password", "密码"], config.password, healer, intent)
            self.click_first(page, ["login", "sign in", "submit", "登录", "提交"], healer, intent)
            return
        if intent == "search_product":
            self.fill_first(page, ["search", "keyword", "product", "搜索", "商品"], step.get("keyword", "headphones"), healer, intent)
            page.keyboard.press("Enter")
            return
        if intent in {"open_first_product", "add_to_cart", "open_cart", "checkout", "verify_order_created"}:
            words = {
                "open_first_product": ["product", "detail", "headphones", "商品"],
                "add_to_cart": ["add to cart", "cart", "加入购物车"],
                "open_cart": ["cart", "购物车"],
                "checkout": ["checkout", "place order", "submit order", "结算", "下单"],
                "verify_order_created": ["order", "created", "订单"],
            }[intent]
            if intent == "verify_order_created":
                page.get_by_text(words[0], exact=False).first.wait_for(timeout=3000)
            else:
                self.click_first(page, words, healer, intent)
            return
        if intent == "apply_coupon":
            self.fill_first(page, ["coupon", "优惠券"], step.get("coupon", "WELCOME10"), healer, intent)
            self.click_first(page, ["apply", "use", "使用", "应用"], healer, intent)

    def fill_first(self, page: Any, words: List[str], value: str, healer: SelfHealingLocator, intent: str) -> None:
        for word in words:
            candidates = [
                lambda w=word: page.get_by_label(w, exact=False),
                lambda w=word: page.get_by_placeholder(w, exact=False),
                lambda w=word: page.locator(f"input[name*='{w}' i], textarea[name*='{w}' i]"),
            ]
            for build in candidates:
                try:
                    locator = build().first
                    locator.fill(value, timeout=1200)
                    return
                except Exception:
                    continue
        healer.record(intent, ",".join(words), "placeholder_label_name_similarity", "no textbox found", 0.0, "failed")
        raise RuntimeError(f"Cannot find input for {words}")

    def click_first(self, page: Any, words: List[str], healer: SelfHealingLocator, intent: str) -> None:
        for word in words:
            candidates = [
                lambda w=word: page.get_by_role("button", name=w, exact=False),
                lambda w=word: page.get_by_role("link", name=w, exact=False),
                lambda w=word: page.get_by_text(w, exact=False),
                lambda w=word: page.locator(f"[data-testid*='{w}' i]"),
            ]
            for build in candidates:
                try:
                    build().first.click(timeout=1200)
                    return
                except Exception:
                    continue
        healer.record(intent, ",".join(words), "role_text_testid_similarity", "no clickable found", 0.0, "failed")
        raise RuntimeError(f"Cannot find clickable for {words}")

    @staticmethod
    def result(journey: Dict[str, Any], status: str, step_results: List[Dict[str, Any]], started: float, graceful_degrade_reason: str = "") -> Dict[str, Any]:
        passed = sum(1 for s in step_results if s["status"] == "passed")
        failed = sum(1 for s in step_results if s["status"] == "failed")
        skipped = sum(1 for s in step_results if s["status"] == "skipped")
        return {
            "journey_id": journey.get("journey_id"),
            "title": journey.get("title"),
            "priority": journey.get("priority"),
            "status": status,
            "total_steps": len(journey.get("steps", [])),
            "passed_steps": passed,
            "failed_steps": failed,
            "skipped_steps": skipped,
            "duration_sec": round(time.time() - started, 2),
            "graceful_degrade_reason": graceful_degrade_reason,
            "steps": step_results,
        }


class UIFailureTriage:
    def triage(self, execution_result: Dict[str, Any], workspace: Path, output: Path) -> Dict[str, Any]:
        failed = next((s for s in execution_result.get("steps", []) if s.get("status") == "failed"), None)
        skipped_reason = execution_result.get("graceful_degrade_reason")
        if skipped_reason:
            result = {
                "failure_type": "环境问题",
                "suspected_owner": "测试环境",
                "confidence": 0.95,
                "evidence": skipped_reason,
                "suggested_fix": "启动被测系统，确认基础地址正确，启用浏览器执行后重新运行 UI 旅程。",
                "bug_draft": "被测系统不可达，UI 执行已降级跳过。",
            }
        elif failed:
            error = failed.get("error", "")
            is_business_verification = str(failed.get("intent", "")).startswith("verify_")
            failure_type = "assertion_failed" if is_business_verification else "element_not_found" if "Cannot find" in error or "locator" in error.lower() else "assertion_failed"
            result = {
                "failure_type": "元素未找到" if failure_type == "element_not_found" else "断言失败",
                "suspected_owner": "测试平台" if failure_type == "element_not_found" else "应用系统",
                "confidence": 0.78,
                "evidence": failed,
                "suggested_fix": "复核 DOM 快照和元素地图。对于容易混淆的页面控件，补充稳定的可访问标签或 data-testid。",
                "bug_draft": f"UI 旅程在意图 {failed.get('intent')} 失败：{error}",
            }
        else:
            result = {
                "failure_type": "无失败",
                "suspected_owner": "无",
                "confidence": 1.0,
                "evidence": "旅程已通过或按预期跳过",
                "suggested_fix": "无需修复。",
                "bug_draft": "",
            }
        write_json(workspace / "ui_execution" / "ui_failure_triage.json", result)
        (output / "ui" / "ui_failure_triage.md").write_text(self.render_md(result), encoding="utf-8")
        return result

    @staticmethod
    def render_md(result: Dict[str, Any]) -> str:
        return f"""# UI 失败归因

- 失败类型：{result['failure_type']}
- 疑似归属：{result['suspected_owner']}
- 置信度：{result['confidence']}
- 证据：{result['evidence']}
- 建议修复：{result['suggested_fix']}

## 缺陷草稿

{result['bug_draft']}
"""


class UIReportBuilder:
    def build(self, journey: Dict[str, Any], page_map: Dict[str, Any], element_map: Dict[str, Any], execution_result: Dict[str, Any], triage: Dict[str, Any], workspace: Path, output: Path) -> Dict[str, Any]:
        elements = element_map.get("elements", [])
        healing = read_json(workspace / "ui_execution" / "self_healing_notes.json", [])
        data = {
            "journey_overview": {
                "journey_name": journey.get("title"),
                "priority": journey.get("priority"),
                "status": execution_result.get("status"),
                "total_steps": execution_result.get("total_steps"),
                "passed_steps": execution_result.get("passed_steps"),
                "failed_steps": execution_result.get("failed_steps"),
                "skipped_steps": execution_result.get("skipped_steps"),
                "duration_sec": execution_result.get("duration_sec"),
            },
            "step_timeline": execution_result.get("steps", []),
            "page_map": {
                "status": page_map.get("status"),
                "page_count": len(page_map.get("pages", [])),
                "key_pages": page_map.get("pages", []),
                "edges": page_map.get("edges", []),
            },
            "element_map": {
                "total_elements": len(elements),
                "buttons": sum(1 for e in elements if e.get("role") == "button"),
                "inputs": sum(1 for e in elements if e.get("role") in {"textbox", "input", "textarea", "select"}),
                "links": sum(1 for e in elements if e.get("role") == "link"),
                "high_confidence": sum(1 for e in elements if float(e.get("confidence", 0)) >= 0.8),
                "low_confidence": sum(1 for e in elements if float(e.get("confidence", 0)) < 0.8),
                "elements": elements[:80],
            },
            "self_healing": {
                "attempts": len(healing),
                "successes": sum(1 for h in healing if h.get("result") == "success"),
                "failures": sum(1 for h in healing if h.get("result") == "failed"),
                "records": healing,
            },
            "failure_triage": triage,
            "ai_value_summary": {
                "generated_journeys": [journey.get("title")],
                "identified_elements": len(elements),
                "manual_selectors_avoided": len(elements),
                "prefer_api_testing": ["大批量数据准备", "支付网关边界场景", "深层库存一致性校验"],
            },
        }
        write_json(output / "ui" / "ui_visual_report_data.json", data)
        (output / "ui" / "ui_visual_report.html").write_text(self.render_html(data), encoding="utf-8")
        return data

    def render_html(self, data: Dict[str, Any]) -> str:
        overview = data["journey_overview"]
        status_text = {"passed": "通过", "failed": "失败", "skipped": "跳过", "running": "运行中"}.get(overview.get("status"), overview.get("status", "-"))
        timeline = "".join(
            f"<tr><td>{esc(s.get('step_index'))}</td><td>{esc(s.get('intent'))}</td><td>{esc(s.get('status'))}</td><td>{esc(s.get('current_url'))}</td><td><img src='{rel_asset(s.get('screenshot'))}' /></td><td>{esc(s.get('error'))}</td></tr>"
            for s in data["step_timeline"]
        )
        healing = "".join(f"<li>{esc(h.get('failed_intent'))}: {esc(h.get('healing_strategy'))} -> {esc(h.get('result'))}</li>" for h in data["self_healing"]["records"]) or "<li>无需自愈定位。</li>"
        triage = data["failure_triage"]
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI UI 旅程报告</title>
<style>
body{{margin:0;font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;background:#eef3f8;color:#142033}}header{{background:#10213f;color:white;padding:28px 34px}}main{{padding:22px 34px;display:grid;gap:18px}}section{{background:white;border:1px solid #d8e1ee;border-radius:8px;padding:18px;box-shadow:0 8px 24px rgba(15,23,42,.07)}}.metrics{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}}.metric{{background:#f8fafc;border:1px solid #d8e1ee;border-radius:8px;padding:14px}}.metric b{{display:block;color:#2563eb;font-size:26px}}table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{border-bottom:1px solid #d8e1ee;padding:9px;text-align:left;vertical-align:top}}img{{max-width:180px;border:1px solid #d8e1ee;border-radius:6px}}@media(max-width:1000px){{.metrics{{grid-template-columns:1fr 1fr}}}}
</style></head><body>
<header><h1>AI UI 旅程报告</h1><p>{esc(overview['journey_name'])} · {esc(status_text)}</p></header>
<main>
<div class="metrics">
<div class="metric"><b>{esc(overview['priority'])}</b><span>优先级</span></div>
<div class="metric"><b>{esc(status_text)}</b><span>状态</span></div>
<div class="metric"><b>{esc(overview['total_steps'])}</b><span>总步骤</span></div>
<div class="metric"><b>{esc(overview['passed_steps'])}</b><span>通过</span></div>
<div class="metric"><b>{esc(overview['failed_steps'])}</b><span>失败</span></div>
<div class="metric"><b>{esc(overview['duration_sec'])}s</b><span>耗时</span></div>
</div>
<section><h2>旅程时间线</h2><table><thead><tr><th>#</th><th>意图</th><th>状态</th><th>URL</th><th>截图</th><th>失败信息</th></tr></thead><tbody>{timeline}</tbody></table></section>
<section><h2>页面地图</h2><p>已探索页面：{esc(data['page_map']['page_count'])}。状态：{esc(data['page_map']['status'])}</p></section>
<section><h2>元素地图</h2><p>总数：{data['element_map']['total_elements']} · 按钮：{data['element_map']['buttons']} · 输入框：{data['element_map']['inputs']} · 链接：{data['element_map']['links']} · 高置信：{data['element_map']['high_confidence']} · 低置信：{data['element_map']['low_confidence']}</p></section>
<section><h2>自愈定位</h2><p>尝试：{data['self_healing']['attempts']} · 成功：{data['self_healing']['successes']} · 失败：{data['self_healing']['failures']}</p><ul>{healing}</ul></section>
<section><h2>失败归因</h2><p><b>{esc(triage['failure_type'])}</b> · 归属：{esc(triage['suspected_owner'])} · 置信度：{esc(triage['confidence'])}</p><p>{esc(triage['suggested_fix'])}</p><pre>{esc(triage['bug_draft'])}</pre></section>
<section><h2>AI 价值摘要</h2><p>生成旅程：{esc(', '.join(data['ai_value_summary']['generated_journeys']))}。识别元素：{data['ai_value_summary']['identified_elements']}。减少手写选择器：{data['ai_value_summary']['manual_selectors_avoided']}。</p></section>
</main></body></html>"""


def rel_asset(path: Any) -> str:
    if not path:
        return ""
    text = str(path).replace("\\", "/")
    marker = "/ui/"
    if marker in text:
        return text[text.index(marker) + len(marker) :]
    if "platform_outputs/" in text:
        return text.split("platform_outputs/", 1)[1]
    return text


def esc(value: Any) -> str:
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def run_ui_journey(project: str, inputs: Path, workspace_root: Path, output_root: Path, config: UIJourneyConfig) -> Dict[str, Any]:
    workspace = workspace_root / project
    output = output_root / project
    for path in [workspace / "ui_journeys", workspace / "ui_execution", workspace / "ui_explorer", output / "ui"]:
        path.mkdir(parents=True, exist_ok=True)

    prd = read_text(inputs / "prd.md") or read_text(inputs / "prd_ecommerce.md")
    openapi = read_json(inputs / "openapi.json", read_json(inputs / "shop_openapi.json", {}))
    journey = UIJourneyGenerator().generate(prd, openapi, config)
    write_json(workspace / "ui_journeys" / "ui_journey_dsl.json", journey)
    (workspace / "ui_journeys" / "ui_journey_dsl.yaml").write_text(dump_yaml(journey), encoding="utf-8")

    exploration = BrowserExplorer().explore(config, workspace, output)
    page_map = exploration["page_map"]
    element_map = exploration["element_map"]
    execution = IntentExecutor().execute(journey, config, workspace, output, element_map)
    evidence = EvidenceCollector(workspace, output).bundle(execution, page_map, element_map)
    triage = UIFailureTriage().triage(execution, workspace, output)
    report = UIReportBuilder().build(journey, page_map, element_map, execution, triage, workspace, output)
    return {
        "ok": execution.get("status") in {"passed", "skipped"},
        "project": project,
        "journey": journey,
        "execution": execution,
        "evidence": evidence,
        "triage": triage,
        "report_data": report,
        "ui_report": str(output / "ui" / "ui_visual_report.html"),
    }
