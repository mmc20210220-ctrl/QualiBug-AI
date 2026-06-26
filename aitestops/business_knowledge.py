from __future__ import annotations

import json
import re
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree as ET


DOMAIN_KEYWORDS = {
    "authentication": ["auth", "login", "token", "password", "account", "lock"],
    "catalog": ["product", "catalog", "category", "search", "price"],
    "cart": ["cart", "quantity", "subtotal"],
    "coupon": ["coupon", "discount", "promotion"],
    "checkout": ["checkout", "payment", "shipping", "tax", "total"],
    "order": ["order", "refund", "fulfill", "cancel"],
    "inventory": ["inventory", "stock", "oversell"],
    "permission": ["admin", "permission", "role", "access", "ownership", "unauthorized", "forbidden"],
}


ENTITY_KEYWORDS = {
    "user": ["user", "account", "guest", "admin", "vip"],
    "product": ["product", "sku", "catalog", "stock", "inventory"],
    "cart": ["cart", "subtotal", "quantity"],
    "coupon": ["coupon", "discount", "promotion"],
    "order": ["order", "checkout", "payment", "refund"],
}


RISK_PATTERNS = {
    "permission_bypass": ["permission", "admin", "unauthorized", "forbidden", "ownership", "access control"],
    "data_consistency": ["consistency", "subtotal", "total", "inventory", "stock", "order", "cart"],
    "state_transition": ["status", "state", "created", "paid", "cancelled", "refunded", "locked"],
    "boundary_validation": ["quantity", "range", "missing", "invalid", "empty", "price"],
    "financial_rule": ["coupon", "discount", "payment", "tax", "amount", "total"],
}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


class TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def read_html_text(path: Path) -> str:
    parser = TextHTMLParser()
    parser.feed(read_text(path))
    return parser.text()


def read_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except Exception:
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts: List[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        line = "".join(texts).strip()
        if line:
            parts.append(line)
    return "\n".join(parts)


def safe_id(value: str) -> str:
    clean = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    return re.sub(r"_+", "_", clean)[:80] or "item"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_module_manifest(input_dir: Path) -> Dict[str, Any]:
    manifest = input_dir / "business_knowledge_uploads" / "module_manifest.json"
    data = load_json(manifest)
    if isinstance(data, dict) and isinstance(data.get("modules"), list):
        return data
    return {"project": input_dir.name, "modules": []}


def module_for_path(input_dir: Path, path: Path, manifest: Dict[str, Any]) -> str:
    rel_name = str(path.relative_to(input_dir)).replace("\\", "/")
    for module in manifest.get("modules", []):
        module_path = str(module.get("path") or "")
        marker = "/business_knowledge_uploads/"
        if marker in module_path:
            module_rel = module_path.split(marker, 1)[1].strip("/")
            if rel_name.startswith(f"business_knowledge_uploads/{module_rel}/"):
                return str(module.get("name") or module_rel)
    parts = rel_name.split("/")
    if len(parts) >= 3 and parts[0] == "business_knowledge_uploads":
        return parts[1]
    return "通用资料"


def collect_input_documents(input_dir: Path) -> Dict[str, Any]:
    docs: List[Dict[str, Any]] = []
    openapi_specs: List[Dict[str, Any]] = []
    module_manifest = load_module_manifest(input_dir)
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"upload_manifest.json", "module_manifest.json"}:
            continue
        suffix = path.suffix.lower()
        rel_name = str(path.relative_to(input_dir)).replace("\\", "/")
        module_name = module_for_path(input_dir, path, module_manifest)
        if suffix in {".md", ".txt", ".diff", ".log", ".csv", ".yaml", ".yml", ".prd", ".feature"}:
            docs.append({"name": rel_name, "module": module_name, "type": suffix.lstrip("."), "text": read_text(path)})
        elif suffix in {".html", ".htm"}:
            docs.append({"name": rel_name, "module": module_name, "type": suffix.lstrip("."), "text": read_html_text(path)})
        elif suffix == ".docx":
            text = read_docx_text(path)
            if text:
                docs.append({"name": rel_name, "module": module_name, "type": "docx", "text": text})
        elif suffix == ".json":
            data = load_json(path)
            if isinstance(data, dict) and isinstance(data.get("paths"), dict):
                openapi_specs.append({"name": rel_name, "module": module_name, "spec": data})
            elif isinstance(data, dict):
                docs.append({"name": rel_name, "module": module_name, "type": "json", "text": json.dumps(data, ensure_ascii=False, indent=2)})
    return {"documents": docs, "openapi_specs": openapi_specs, "module_manifest": module_manifest}


class BusinessKnowledgeBuilder:
    """Build an enterprise business testing asset model from project knowledge."""

    def build(self, project: str, input_dir: Path, out_dir: Path) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle = collect_input_documents(input_dir)
        all_text = "\n\n".join(doc["text"] for doc in bundle["documents"])
        endpoints = self.extract_endpoints(bundle["openapi_specs"])
        model = {
            "project": project,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "input_inventory": self.input_inventory(bundle),
            "module_knowledge_map": self.build_module_knowledge_map(bundle),
            "business_domains": self.extract_domains(all_text, endpoints),
            "roles": self.extract_roles(all_text),
            "entities": self.extract_entities(all_text, endpoints),
            "business_rules": self.extract_business_rules(all_text),
            "state_models": self.extract_state_models(all_text),
            "api_catalog": endpoints,
            "business_scenarios": self.build_scenarios(all_text, endpoints),
            "data_dependency_model": self.build_data_dependencies(all_text, endpoints),
            "risk_matrix": self.build_risk_matrix(all_text, endpoints),
        }
        model["coverage_map"] = self.build_coverage_map(model)
        model["test_asset_backlog"] = self.build_asset_backlog(model)
        write_json(out_dir / "business_knowledge_model.json", model)
        (out_dir / "business_knowledge_report.md").write_text(self.render_markdown(model), encoding="utf-8")
        (out_dir / "business_knowledge_report.html").write_text(self.render_html(model), encoding="utf-8")
        return model

    @staticmethod
    def input_inventory(bundle: Dict[str, Any]) -> Dict[str, Any]:
        docs = bundle["documents"]
        specs = bundle["openapi_specs"]
        return {
            "document_count": len(docs),
            "openapi_count": len(specs),
            "module_count": len({*(d.get("module") for d in docs), *(s.get("module") for s in specs)} - {None}),
            "documents": [{"name": d["name"], "module": d.get("module", "通用资料"), "type": d["type"], "chars": len(d["text"])} for d in docs],
            "openapi_specs": [{"name": s["name"], "module": s.get("module", "通用资料"), "path_count": len((s["spec"].get("paths") or {}))} for s in specs],
        }

    def build_module_knowledge_map(self, bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
        modules: Dict[str, Dict[str, Any]] = {}
        for doc in bundle["documents"]:
            module = doc.get("module") or "通用资料"
            item = modules.setdefault(module, {"module": module, "document_count": 0, "openapi_count": 0, "char_count": 0, "domains": set(), "risks": set(), "sample_files": []})
            item["document_count"] += 1
            item["char_count"] += len(doc.get("text") or "")
            item["sample_files"].append(doc["name"])
            lower = (doc.get("text") or "").lower()
            item["domains"].update(name for name, keywords in DOMAIN_KEYWORDS.items() if any(keyword in lower for keyword in keywords))
            item["risks"].update(risk for risk, keywords in RISK_PATTERNS.items() if any(keyword in lower for keyword in keywords))
        for spec in bundle["openapi_specs"]:
            module = spec.get("module") or "通用资料"
            item = modules.setdefault(module, {"module": module, "document_count": 0, "openapi_count": 0, "char_count": 0, "domains": set(), "risks": set(), "sample_files": []})
            item["openapi_count"] += 1
            item["sample_files"].append(spec["name"])
            for path, path_item in (spec.get("spec", {}).get("paths") or {}).items():
                text = f"{path} {json.dumps(path_item, ensure_ascii=False)}".lower()
                item["domains"].update(name for name, keywords in DOMAIN_KEYWORDS.items() if any(keyword in text for keyword in keywords))
                item["risks"].update(risk for risk, keywords in RISK_PATTERNS.items() if any(keyword in text for keyword in keywords))
        result = []
        for item in modules.values():
            result.append(
                {
                    "module": item["module"],
                    "document_count": item["document_count"],
                    "openapi_count": item["openapi_count"],
                    "char_count": item["char_count"],
                    "domains": sorted(item["domains"]),
                    "risks": sorted(item["risks"]),
                    "sample_files": item["sample_files"][:8],
                }
            )
        return sorted(result, key=lambda item: item["module"])

    @staticmethod
    def extract_endpoints(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        endpoints: List[Dict[str, Any]] = []
        for item in specs:
            spec = item["spec"]
            for path, path_item in (spec.get("paths") or {}).items():
                for method, operation in path_item.items():
                    if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                        continue
                    text = f"{path} {operation.get('operationId', '')} {operation.get('summary', '')}".lower()
                    endpoints.append(
                        {
                            "source": item["name"],
                            "method": method.upper(),
                            "path": path,
                            "operation_id": operation.get("operationId") or safe_id(f"{method}_{path}"),
                            "summary": operation.get("summary", ""),
                            "domain": detect_domain(text),
                            "entity": detect_entity(text),
                            "has_body": bool(operation.get("requestBody")),
                            "has_path_params": "{" in path and "}" in path,
                            "requires_auth": bool(operation.get("security")) or "admin" in path.lower(),
                        }
                    )
        return endpoints

    @staticmethod
    def extract_domains(text: str, endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lower = text.lower()
        domains = []
        for name, keywords in DOMAIN_KEYWORDS.items():
            evidence = [kw for kw in keywords if kw in lower or any(kw in f"{ep['path']} {ep['summary']}".lower() for ep in endpoints)]
            if evidence:
                domains.append(
                    {
                        "domain": name,
                        "evidence": evidence[:8],
                        "endpoint_count": sum(1 for ep in endpoints if ep["domain"] == name),
                    }
                )
        return domains

    @staticmethod
    def extract_roles(text: str) -> List[Dict[str, Any]]:
        roles = []
        seen = set()
        for line in text.splitlines():
            if re.search(r"\b(guest|user|admin|vip|operator|approver|auditor)\b", line, re.I):
                name_match = re.match(r"\s*[-*]?\s*([A-Za-z][A-Za-z _-]{1,30})\s*:", line)
                name = safe_id(name_match.group(1)) if name_match else safe_id(line.split()[0])
                if name in seen:
                    continue
                seen.add(name)
                roles.append({"role": name, "source_line": line.strip()[:240]})
        if not roles:
            for role in ["guest", "user", "admin"]:
                if re.search(rf"\b{role}\b", text, re.I):
                    roles.append({"role": role, "source_line": "inferred from business text"})
        return roles

    @staticmethod
    def extract_entities(text: str, endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lower = text.lower()
        entities = []
        for name, keywords in ENTITY_KEYWORDS.items():
            evidence = [kw for kw in keywords if kw in lower or any(kw in ep["path"].lower() for ep in endpoints)]
            if evidence:
                entities.append(
                    {
                        "entity": name,
                        "evidence": evidence[:8],
                        "api_paths": sorted({ep["path"] for ep in endpoints if ep["entity"] == name})[:20],
                    }
                )
        return entities

    @staticmethod
    def extract_business_rules(text: str) -> List[Dict[str, Any]]:
        rules = []
        current_section = "general"
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                current_section = stripped.strip("# ").lower() or "general"
                continue
            if re.match(r"^(\d+\.|-|\*)\s+", stripped):
                rule_text = re.sub(r"^(\d+\.|-|\*)\s+", "", stripped)
            elif re.search(r"\b(must|cannot|should|required|validate|prevent|allow|deny)\b|\u5fc5\u987b|\u4e0d\u5f97|\u4e0d\u80fd|\u9700\u8981|\u6821\u9a8c|\u7981\u6b62|\u5141\u8bb8", stripped, re.I):
                rule_text = stripped
            else:
                continue
            if rule_text:
                rules.append(
                    {
                        "rule_id": f"BR_{len(rules)+1:03d}",
                        "section": current_section,
                        "text": rule_text,
                        "domain": detect_domain(rule_text.lower()),
                        "risk_tags": detect_risk_tags(rule_text),
                    }
                )
        return rules

    @staticmethod
    def extract_state_models(text: str) -> List[Dict[str, Any]]:
        state_words = sorted(set(re.findall(r"\b(created|paid|cancelled|canceled|refunded|fulfilled|locked|active|inactive|expired|used|available|unavailable)\b", text, re.I)))
        models = []
        if state_words:
            models.append(
                {
                    "model": "business_state_candidates",
                    "states": [s.lower() for s in state_words],
                    "note": "Candidate states inferred from documents. Review and approve for strict state-machine testing.",
                }
            )
        return models

    @staticmethod
    def build_scenarios(text: str, endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        domains = sorted({detect_domain(text.lower()), *[ep["domain"] for ep in endpoints if ep["domain"] != "general"]})
        scenarios = []
        for domain in domains:
            if domain == "general":
                continue
            domain_eps = [ep for ep in endpoints if ep["domain"] == domain]
            scenarios.append(
                {
                    "scenario_id": f"SCN_{len(scenarios)+1:03d}_{domain}",
                    "name": scenario_name(domain),
                    "domain": domain,
                    "priority": "P0" if domain in {"permission", "checkout", "order", "inventory"} else "P1",
                    "business_value": scenario_value(domain),
                    "api_chain": [{"method": ep["method"], "path": ep["path"]} for ep in domain_eps[:8]],
                    "test_focus": test_focus(domain),
                }
            )
        return scenarios

    @staticmethod
    def build_data_dependencies(text: str, endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deps = []
        for entity in ["user", "product", "cart", "coupon", "order"]:
            if entity in text.lower() or any(entity in ep["path"].lower() for ep in endpoints):
                deps.append(
                    {
                        "entity": entity,
                        "required_profiles": data_profiles_for(entity),
                        "setup_strategy": setup_strategy_for(entity),
                        "cleanup_strategy": "auto_cleanup_or_test_tenant_isolation",
                    }
                )
        return deps

    @staticmethod
    def build_risk_matrix(text: str, endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        risks = []
        combined = text.lower() + "\n" + "\n".join(ep["path"].lower() for ep in endpoints)
        for risk, keywords in RISK_PATTERNS.items():
            evidence = [kw for kw in keywords if kw in combined]
            if evidence:
                risks.append(
                    {
                        "risk_id": f"RISK_{safe_id(risk).upper()}",
                        "risk": risk,
                        "priority": "P0" if risk in {"permission_bypass", "financial_rule", "data_consistency"} else "P1",
                        "evidence": evidence,
                        "recommended_tests": recommended_tests_for_risk(risk),
                    }
                )
        return risks

    @staticmethod
    def build_coverage_map(model: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "scenario_count": len(model["business_scenarios"]),
            "rule_count": len(model["business_rules"]),
            "endpoint_count": len(model["api_catalog"]),
            "risk_count": len(model["risk_matrix"]),
            "module_count": len(model.get("module_knowledge_map", [])),
            "module_coverage": [
                {
                    "module": item["module"],
                    "document_count": item["document_count"],
                    "openapi_count": item["openapi_count"],
                    "domain_count": len(item.get("domains", [])),
                    "risk_count": len(item.get("risks", [])),
                    "covered": bool(item.get("domains") or item.get("risks") or item.get("openapi_count")),
                }
                for item in model.get("module_knowledge_map", [])
            ],
            "scenario_to_api": [
                {
                    "scenario_id": s["scenario_id"],
                    "api_count": len(s["api_chain"]),
                    "covered": bool(s["api_chain"]),
                }
                for s in model["business_scenarios"]
            ],
        }

    @staticmethod
    def build_asset_backlog(model: Dict[str, Any]) -> List[Dict[str, Any]]:
        backlog = []
        for scenario in model["business_scenarios"]:
            backlog.append(
                {
                    "asset_id": f"ASSET_{len(backlog)+1:03d}",
                    "type": "business_scenario_test",
                    "source_scenario": scenario["scenario_id"],
                    "priority": scenario["priority"],
                    "next_action": "generate_api_chain_and_ui_journey",
                }
            )
        for risk in model["risk_matrix"]:
            backlog.append(
                {
                    "asset_id": f"ASSET_{len(backlog)+1:03d}",
                    "type": "risk_regression_pack",
                    "source_risk": risk["risk_id"],
                    "priority": risk["priority"],
                    "next_action": "create_boundary_negative_permission_tests",
                }
            )
        return backlog

    @staticmethod
    def render_markdown(model: Dict[str, Any]) -> str:
        scenarios = "\n".join(f"- {s['scenario_id']} [{s['priority']}] {s['name']}: {s['business_value']}" for s in model["business_scenarios"])
        risks = "\n".join(f"- {r['risk_id']} [{r['priority']}] {r['risk']}: {', '.join(r['recommended_tests'])}" for r in model["risk_matrix"])
        deps = "\n".join(f"- {d['entity']}: profiles={', '.join(d['required_profiles'])}; setup={d['setup_strategy']}" for d in model["data_dependency_model"])
        modules = "\n".join(
            f"- {m['module']}：文档 {m['document_count']}，接口文档 {m['openapi_count']}，领域 {', '.join(m.get('domains', [])) or '-'}，风险 {', '.join(m.get('risks', [])) or '-'}"
            for m in model.get("module_knowledge_map", [])
        )
        return f"""# 企业业务知识模型

项目：{model['project']}

生成时间：{model['generated_at']}

## 业务场景

{scenarios or '- 未识别到业务场景。'}

## 业务模块

{modules or '- 未识别到业务模块。'}

## 风险矩阵

{risks or '- 未识别到风险。'}

## 数据依赖模型

{deps or '- 未识别到数据依赖。'}

## 覆盖情况

- 业务场景：{model['coverage_map']['scenario_count']}
- 业务规则：{model['coverage_map']['rule_count']}
- API 接口：{model['coverage_map']['endpoint_count']}
- 风险：{model['coverage_map']['risk_count']}
- 业务模块：{model['coverage_map'].get('module_count', 0)}
"""

    @staticmethod
    def render_html(model: Dict[str, Any]) -> str:
        scenario_rows = "".join(
            f"<tr><td>{esc(s['scenario_id'])}</td><td>{esc(s['priority'])}</td><td>{esc(s['name'])}</td><td>{esc(s['domain'])}</td><td>{esc(s['business_value'])}</td><td>{len(s['api_chain'])}</td></tr>"
            for s in model["business_scenarios"]
        )
        risk_rows = "".join(
            f"<tr><td>{esc(r['risk_id'])}</td><td>{esc(r['priority'])}</td><td>{esc(r['risk'])}</td><td>{esc(', '.join(r['evidence']))}</td><td>{esc(', '.join(r['recommended_tests']))}</td></tr>"
            for r in model["risk_matrix"]
        )
        dep_rows = "".join(
            f"<tr><td>{esc(d['entity'])}</td><td>{esc(', '.join(d['required_profiles']))}</td><td>{esc(d['setup_strategy'])}</td><td>{esc(d['cleanup_strategy'])}</td></tr>"
            for d in model["data_dependency_model"]
        )
        module_rows = "".join(
            f"<tr><td>{esc(m['module'])}</td><td>{esc(m['document_count'])}</td><td>{esc(m['openapi_count'])}</td><td>{esc(', '.join(m.get('domains', [])) or '-')}</td><td>{esc(', '.join(m.get('risks', [])) or '-')}</td><td>{esc(', '.join(m.get('sample_files', [])))}</td></tr>"
            for m in model.get("module_knowledge_map", [])
        )
        cards = [
            ("文档数", model["input_inventory"]["document_count"]),
            ("OpenAPI", model["input_inventory"]["openapi_count"]),
            ("业务模块", model["coverage_map"].get("module_count", 0)),
            ("业务场景", model["coverage_map"]["scenario_count"]),
            ("业务规则", model["coverage_map"]["rule_count"]),
            ("接口数", model["coverage_map"]["endpoint_count"]),
            ("风险数", model["coverage_map"]["risk_count"]),
        ]
        card_html = "".join(f"<div class='metric'><b>{esc(v)}</b><span>{esc(k)}</span></div>" for k, v in cards)
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>企业业务知识模型</title>
  <style>
    body{{margin:0;font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;background:#eef3f8;color:#142033}}
    header{{background:#10213f;color:white;padding:26px 34px}} main{{padding:22px 34px;display:grid;gap:18px}}
    .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}} .metric,section{{background:white;border:1px solid #d8e1ee;border-radius:8px;padding:16px;box-shadow:0 8px 24px rgba(15,23,42,.07)}}
    .metric b{{display:block;color:#2563eb;font-size:26px}} .metric span{{color:#607089;font-size:13px}}
    table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{border-bottom:1px solid #d8e1ee;padding:9px;text-align:left;vertical-align:top}} th{{background:#f8fafc}}
    @media(max-width:1000px){{.metrics{{grid-template-columns:1fr 1fr}}}}
  </style>
</head>
<body>
<header><h1>企业业务知识模型</h1><p>项目：{esc(model['project'])}。生成时间：{esc(model['generated_at'])}。</p></header>
<main>
  <div class="metrics">{card_html}</div>
  <section><h2>业务模块</h2><table><thead><tr><th>模块</th><th>文档数</th><th>接口文档数</th><th>识别领域</th><th>风险类型</th><th>样例文件</th></tr></thead><tbody>{module_rows}</tbody></table></section>
  <section><h2>业务场景</h2><table><thead><tr><th>ID</th><th>优先级</th><th>名称</th><th>领域</th><th>业务价值</th><th>接口数</th></tr></thead><tbody>{scenario_rows}</tbody></table></section>
  <section><h2>风险矩阵</h2><table><thead><tr><th>ID</th><th>优先级</th><th>风险</th><th>证据</th><th>推荐测试</th></tr></thead><tbody>{risk_rows}</tbody></table></section>
  <section><h2>数据依赖模型</h2><table><thead><tr><th>实体</th><th>所需数据画像</th><th>准备策略</th><th>清理策略</th></tr></thead><tbody>{dep_rows}</tbody></table></section>
</main>
</body>
</html>"""


def detect_domain(text: str) -> str:
    scores = {name: sum(1 for kw in keywords if kw in text) for name, keywords in DOMAIN_KEYWORDS.items()}
    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] else "general"


def detect_entity(text: str) -> str:
    scores = {name: sum(1 for kw in keywords if kw in text) for name, keywords in ENTITY_KEYWORDS.items()}
    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] else "general"


def detect_risk_tags(text: str) -> List[str]:
    lower = text.lower()
    return [risk for risk, keywords in RISK_PATTERNS.items() if any(kw in lower for kw in keywords)]


def scenario_name(domain: str) -> str:
    return {
        "authentication": "Authentication and account safety",
        "catalog": "Product discovery and detail validation",
        "cart": "Cart management and quantity validation",
        "coupon": "Coupon eligibility and discount control",
        "checkout": "Checkout amount and order creation",
        "order": "Order lifecycle and ownership",
        "inventory": "Inventory update and oversell prevention",
        "permission": "Role permission and access boundary",
    }.get(domain, f"{domain.title()} scenario")


def scenario_value(domain: str) -> str:
    return {
        "authentication": "Protect login and account lockout rules.",
        "catalog": "Ensure users can find valid sellable products.",
        "cart": "Protect cart correctness before checkout.",
        "coupon": "Prevent discount abuse and amount errors.",
        "checkout": "Protect payment/order conversion and financial correctness.",
        "order": "Protect order ownership, status and history.",
        "inventory": "Prevent stock mismatch and overselling.",
        "permission": "Prevent unauthorized business operations.",
    }.get(domain, "Protect critical business behavior.")


def test_focus(domain: str) -> List[str]:
    return {
        "permission": ["anonymous access", "normal user forbidden", "admin allowed", "ownership boundary"],
        "checkout": ["empty cart", "amount calculation", "coupon eligibility", "order created", "inventory reduced"],
        "inventory": ["stock boundary", "oversell", "admin update", "consistency after order"],
        "coupon": ["valid coupon", "invalid coupon", "threshold", "role eligibility"],
        "cart": ["add item", "invalid quantity", "stock limit", "subtotal"],
    }.get(domain, ["happy path", "negative path", "boundary path"])


def data_profiles_for(entity: str) -> List[str]:
    return {
        "user": ["guest", "normal_user", "admin_user", "locked_user"],
        "product": ["active_in_stock_product", "inactive_product", "out_of_stock_product", "missing_product"],
        "cart": ["empty_cart", "cart_with_one_item", "cart_over_stock"],
        "coupon": ["valid_coupon", "invalid_coupon", "expired_coupon", "used_coupon"],
        "order": ["created_order", "paid_order", "cancelled_order", "missing_order"],
    }.get(entity, ["default_profile"])


def setup_strategy_for(entity: str) -> str:
    return {
        "user": "synthetic_or_masked_user_pool",
        "product": "catalog_fixture_or_api_seed",
        "cart": "api_setup_add_cart_items",
        "coupon": "coupon_fixture_or_rule_seed",
        "order": "api_chain_create_order_or_order_fixture",
    }.get(entity, "infer_from_api_chain")


def recommended_tests_for_risk(risk: str) -> List[str]:
    return {
        "permission_bypass": ["role boundary API tests", "UI hidden action tests", "cross-user ownership tests"],
        "data_consistency": ["API chain consistency tests", "database/evidence reconciliation", "rollback tests"],
        "state_transition": ["state machine transition tests", "invalid transition tests"],
        "boundary_validation": ["boundary value tests", "invalid payload tests"],
        "financial_rule": ["amount calculation tests", "coupon abuse tests", "rounding tests"],
    }.get(risk, ["happy path", "negative path"])


def esc(value: Any) -> str:
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
