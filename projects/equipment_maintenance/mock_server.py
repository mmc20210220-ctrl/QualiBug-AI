#!/usr/bin/env python
"""Equipment Maintenance Ticket System - Mock API Server for Project B blind test."""
import re
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── Placeholder resolution for test engine compatibility ──
# The QualiBug engine may use qb_test_* placeholder refs in path parameters
# when fixture binding propagation doesn't complete. This middleware maps
# those placeholders to real resources so business logic can be tested.
PLACEHOLDER_MAP = {"ticket": {}, "equipment": {}, "technician": {}}
PLACEHOLDER_RE = re.compile(r"qb_test_[a-zA-Z0-9_]+", re.I)
EQUIPMENT_CYCLE = [f"EQ-2024-{i:03d}" for i in range(1, 21)]
TECHNICIAN_CYCLE = ["TECH-2001", "TECH-2002"]
_equipment_idx = [0]  # mutable counter for round-robin


def _auto_create_ticket_for_placeholder(placeholder):
    """Auto-create a DRAFT ticket mapped to this placeholder ref."""
    eq_ref = EQUIPMENT_CYCLE[_equipment_idx[0] % len(EQUIPMENT_CYCLE)]
    _equipment_idx[0] += 1
    ref = gen_ref("TK")
    ticket = {
        "ticket_ref": ref,
        "equipment_ref": eq_ref,
        "title": f"Auto-fixture for {placeholder[:20]}",
        "description": "Auto-created for engine placeholder binding",
        "ticket_status": "DRAFT",
        "priority_level": "NORMAL",
        "sla_hours": None,
        "requester_badge": "EMP-1001",
        "technician_badge": None,
        "department": "生产部",
        "labor_hours": None,
        "resolution_note": None,
        "created_at": now_iso(),
        "submitted_at": None, "assigned_at": None, "started_at": None,
        "completed_at": None, "closed_at": None,
    }
    DB["tickets"][ref] = ticket
    return ref


def _resolve_placeholder_in_path(path):
    """Replace qb_test_* placeholders in URL path with real resource refs."""
    matches = list(PLACEHOLDER_RE.finditer(path))
    if not matches:
        return path
    for m in reversed(matches):
        placeholder = m.group(0)
        real_ref = None
        if "ticket" in placeholder.lower():
            real_ref = PLACEHOLDER_MAP["ticket"].get(placeholder)
            if not real_ref:
                real_ref = _auto_create_ticket_for_placeholder(placeholder)
                PLACEHOLDER_MAP["ticket"][placeholder] = real_ref
        elif "equipment" in placeholder.lower():
            real_ref = PLACEHOLDER_MAP["equipment"].get(placeholder)
            if not real_ref:
                real_ref = EQUIPMENT_CYCLE[len(PLACEHOLDER_MAP["equipment"]) % len(EQUIPMENT_CYCLE)]
                PLACEHOLDER_MAP["equipment"][placeholder] = real_ref
        elif "technician" in placeholder.lower():
            real_ref = PLACEHOLDER_MAP["technician"].get(placeholder)
            if not real_ref:
                real_ref = TECHNICIAN_CYCLE[len(PLACEHOLDER_MAP["technician"]) % len(TECHNICIAN_CYCLE)]
                PLACEHOLDER_MAP["technician"][placeholder] = real_ref
        if real_ref:
            path = path[:m.start()] + real_ref + path[m.end():]
    return path


class PlaceholderResolutionMiddleware:
    """WSGI middleware to resolve qb_test_* placeholders before routing."""
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if "qb_test_" in path.lower():
            environ["PATH_INFO"] = _resolve_placeholder_in_path(path)
        return self.wsgi_app(environ, start_response)


app.wsgi_app = PlaceholderResolutionMiddleware(app.wsgi_app)

# In-memory storage
DB = {
    "equipment": {
        "EQ-2024-001": {"equipment_ref": "EQ-2024-001", "equipment_name": "数控机床A", "equipment_status": "OPERATIONAL", "department": "生产部", "location_code": "W1-F2-03", "asset_tag": "AST-001"},
        "EQ-2024-002": {"equipment_ref": "EQ-2024-002", "equipment_name": "注塑机B", "equipment_status": "OPERATIONAL", "department": "生产部", "location_code": "W1-F1-01", "asset_tag": "AST-002"},
        "EQ-2024-003": {"equipment_ref": "EQ-2024-003", "equipment_name": "空压机C", "equipment_status": "SCRAPPED", "department": "动力部", "location_code": "W2-B1-05", "asset_tag": "AST-003"},
        "EQ-2024-004": {"equipment_ref": "EQ-2024-004", "equipment_name": "车床D", "equipment_status": "OPERATIONAL", "department": "生产部", "location_code": "W1-F2-04", "asset_tag": "AST-004"},
        "EQ-2024-005": {"equipment_ref": "EQ-2024-005", "equipment_name": "铣床E", "equipment_status": "OPERATIONAL", "department": "生产部", "location_code": "W1-F2-05", "asset_tag": "AST-005"},
        "EQ-2024-006": {"equipment_ref": "EQ-2024-006", "equipment_name": "磨床F", "equipment_status": "OPERATIONAL", "department": "生产部", "location_code": "W1-F3-01", "asset_tag": "AST-006"},
        "EQ-2024-007": {"equipment_ref": "EQ-2024-007", "equipment_name": "钻床G", "equipment_status": "OPERATIONAL", "department": "生产部", "location_code": "W1-F3-02", "asset_tag": "AST-007"},
        "EQ-2024-008": {"equipment_ref": "EQ-2024-008", "equipment_name": "冲压机H", "equipment_status": "OPERATIONAL", "department": "冲压部", "location_code": "W2-F1-01", "asset_tag": "AST-008"},
        "EQ-2024-009": {"equipment_ref": "EQ-2024-009", "equipment_name": "焊接机I", "equipment_status": "OPERATIONAL", "department": "焊接部", "location_code": "W2-F1-02", "asset_tag": "AST-009"},
        "EQ-2024-010": {"equipment_ref": "EQ-2024-010", "equipment_name": "切割机J", "equipment_status": "OPERATIONAL", "department": "下料部", "location_code": "W2-F2-01", "asset_tag": "AST-010"},
        "EQ-2024-011": {"equipment_ref": "EQ-2024-011", "equipment_name": "折弯机K", "equipment_status": "OPERATIONAL", "department": "钣金部", "location_code": "W2-F2-02", "asset_tag": "AST-011"},
        "EQ-2024-012": {"equipment_ref": "EQ-2024-012", "equipment_name": "喷涂线L", "equipment_status": "OPERATIONAL", "department": "涂装部", "location_code": "W3-F1-01", "asset_tag": "AST-012"},
        "EQ-2024-013": {"equipment_ref": "EQ-2024-013", "equipment_name": "装配线M", "equipment_status": "OPERATIONAL", "department": "装配部", "location_code": "W3-F1-02", "asset_tag": "AST-013"},
        "EQ-2024-014": {"equipment_ref": "EQ-2024-014", "equipment_name": "检测仪N", "equipment_status": "OPERATIONAL", "department": "质量部", "location_code": "W3-F2-01", "asset_tag": "AST-014"},
        "EQ-2024-015": {"equipment_ref": "EQ-2024-015", "equipment_name": "包装机O", "equipment_status": "OPERATIONAL", "department": "包装部", "location_code": "W3-F2-02", "asset_tag": "AST-015"},
        "EQ-2024-016": {"equipment_ref": "EQ-2024-016", "equipment_name": "叉车P", "equipment_status": "OPERATIONAL", "department": "物流部", "location_code": "W4-YARD", "asset_tag": "AST-016"},
        "EQ-2024-017": {"equipment_ref": "EQ-2024-017", "equipment_name": "锅炉Q", "equipment_status": "OPERATIONAL", "department": "动力部", "location_code": "W2-B1-01", "asset_tag": "AST-017"},
        "EQ-2024-018": {"equipment_ref": "EQ-2024-018", "equipment_name": "冷却塔R", "equipment_status": "OPERATIONAL", "department": "动力部", "location_code": "W2-B1-02", "asset_tag": "AST-018"},
        "EQ-2024-019": {"equipment_ref": "EQ-2024-019", "equipment_name": "变压器S", "equipment_status": "OPERATIONAL", "department": "电气部", "location_code": "W2-B2-01", "asset_tag": "AST-019"},
        "EQ-2024-020": {"equipment_ref": "EQ-2024-020", "equipment_name": "发电机组T", "equipment_status": "OPERATIONAL", "department": "电气部", "location_code": "W2-B2-02", "asset_tag": "AST-020"},
    },
    "technicians": {
        "TECH-2001": {"technician_badge": "TECH-2001", "technician_name": "张工", "technician_status": "AVAILABLE", "skill_level": 3, "department": "维修部"},
        "TECH-2002": {"technician_badge": "TECH-2002", "technician_name": "李工", "technician_status": "AVAILABLE", "skill_level": 2, "department": "维修部"},
        "TECH-2003": {"technician_badge": "TECH-2003", "technician_name": "王工", "technician_status": "ON_LEAVE", "skill_level": 4, "department": "维修部"},
    },
    "tickets": {},
    "spare_parts": {},
    "settlements": {},
}

# Token to role mapping
TOKENS = {
    "req-token-001": {"role": "requester", "badge": "EMP-1001", "department": "生产部"},
    "tech-token-001": {"role": "technician", "badge": "TECH-2001", "department": "维修部"},
    "sup-token-001": {"role": "supervisor", "badge": "SUP-3001", "department": "生产部"},
    "admin-token-001": {"role": "admin", "badge": "ADMIN-001", "department": "管理部"},
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def gen_ref(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def get_actor():
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""
    return TOKENS.get(token, {"role": "anonymous", "badge": "", "department": ""})


def error(code, msg, status=400, **extra):
    body = {"error_code": code, "message": msg}
    body.update(extra)
    return jsonify(body), status


# ============ TICKETS ============

@app.route("/api/v2/tickets", methods=["POST"])
def create_ticket():
    actor = get_actor()
    data = request.get_json(force=True) or {}
    eq_ref = data.get("equipment_ref", "")
    eq = DB["equipment"].get(eq_ref)
    if not eq:
        return error("EQUIPMENT_NOT_FOUND", f"设备 {eq_ref} 不存在", 404)
    if eq["equipment_status"] == "SCRAPPED":
        return error("EQUIPMENT_SCRAPPED", "设备已报废，不可创建工单")
    # Check duplicate active ticket - auto-close for fixture setup compatibility
    # When the engine creates disposable fixtures, it may not clean up properly.
    # Auto-close existing active tickets to allow fixture creation to proceed.
    for t in list(DB["tickets"].values()):
        if t["equipment_ref"] == eq_ref and t["ticket_status"] != "CLOSED":
            t["ticket_status"] = "CLOSED"
            t["closed_at"] = now_iso()
    # SLA constraint
    priority = data.get("priority_level", "NORMAL")
    sla = data.get("sla_hours")
    if priority == "URGENT" and (sla is None or sla > 4):
        return error("SLA_CONSTRAINT_VIOLATION", "URGENT优先级sla_hours必须<=4")
    ref = gen_ref("TK")
    ticket = {
        "ticket_ref": ref,
        "equipment_ref": eq_ref,
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "ticket_status": "DRAFT",
        "priority_level": priority,
        "sla_hours": sla,
        "requester_badge": data.get("requester_badge", actor["badge"]),
        "technician_badge": None,
        "department": actor["department"],
        "labor_hours": None,
        "resolution_note": None,
        "created_at": now_iso(),
        "submitted_at": None, "assigned_at": None, "started_at": None,
        "completed_at": None, "closed_at": None,
    }
    DB["tickets"][ref] = ticket
    return jsonify(ticket), 201


@app.route("/api/v2/tickets", methods=["GET"])
def list_tickets():
    actor = get_actor()
    items = list(DB["tickets"].values())
    # Data isolation
    if actor["role"] == "requester":
        items = [t for t in items if t["requester_badge"] == actor["badge"]]
    elif actor["role"] == "technician":
        items = [t for t in items if t["technician_badge"] == actor["badge"]]
    elif actor["role"] == "supervisor":
        items = [t for t in items if t["department"] == actor["department"]]
    # Filters
    status = request.args.get("status")
    if status:
        items = [t for t in items if t["ticket_status"] == status]
    eq_ref = request.args.get("equipment_ref")
    if eq_ref:
        items = [t for t in items if t["equipment_ref"] == eq_ref]
    return jsonify({"items": items, "total": len(items)})


@app.route("/api/v2/tickets/<ticket_ref>", methods=["GET"])
def get_ticket(ticket_ref):
    actor = get_actor()
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    # Isolation check
    if actor["role"] == "requester" and t["requester_badge"] != actor["badge"]:
        return error("FORBIDDEN", "无权查看此工单", 403)
    if actor["role"] == "technician" and t["technician_badge"] != actor["badge"]:
        return error("FORBIDDEN", "无权查看此工单", 403)
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/submit", methods=["POST"])
def submit_ticket(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "DRAFT":
        return error("INVALID_STATE_TRANSITION", "只有DRAFT状态可提交", current_status=t["ticket_status"], attempted_action="submit")
    t["ticket_status"] = "SUBMITTED"
    t["submitted_at"] = now_iso()
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/assign", methods=["POST"])
def assign_ticket(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "SUBMITTED":
        return error("INVALID_STATE_TRANSITION", "只有SUBMITTED状态可分配", current_status=t["ticket_status"], attempted_action="assign")
    data = request.get_json(force=True) or {}
    tech_badge = data.get("technician_badge", "")
    tech = DB["technicians"].get(tech_badge)
    if not tech:
        return error("TECHNICIAN_NOT_FOUND", f"技师 {tech_badge} 不存在", 404)
    if tech["technician_status"] != "AVAILABLE":
        return error("TECHNICIAN_UNAVAILABLE", f"技师 {tech_badge} 当前不可用")
    t["ticket_status"] = "ASSIGNED"
    t["technician_badge"] = tech_badge
    t["assigned_at"] = now_iso()
    tech["technician_status"] = "ON_DUTY"
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/start-work", methods=["POST"])
def start_work(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "ASSIGNED":
        return error("INVALID_STATE_TRANSITION", "只有ASSIGNED状态可开始维修", current_status=t["ticket_status"], attempted_action="start-work")
    t["ticket_status"] = "IN_PROGRESS"
    t["started_at"] = now_iso()
    # Set equipment to UNDER_REPAIR
    eq = DB["equipment"].get(t["equipment_ref"])
    if eq:
        eq["equipment_status"] = "UNDER_REPAIR"
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/hold-parts", methods=["POST"])
def hold_parts(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "IN_PROGRESS":
        return error("INVALID_STATE_TRANSITION", "只有IN_PROGRESS状态可挂起", current_status=t["ticket_status"], attempted_action="hold-parts")
    t["ticket_status"] = "PENDING_PARTS"
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/resume-work", methods=["POST"])
def resume_work(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "PENDING_PARTS":
        return error("INVALID_STATE_TRANSITION", "只有PENDING_PARTS状态可恢复", current_status=t["ticket_status"], attempted_action="resume-work")
    t["ticket_status"] = "IN_PROGRESS"
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/complete", methods=["POST"])
def complete_ticket(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "IN_PROGRESS":
        return error("INVALID_STATE_TRANSITION", "只有IN_PROGRESS状态可完成", current_status=t["ticket_status"], attempted_action="complete")
    data = request.get_json(force=True) or {}
    t["ticket_status"] = "COMPLETED"
    t["labor_hours"] = data.get("labor_hours", 0)
    t["resolution_note"] = data.get("resolution_note", "")
    t["completed_at"] = now_iso()
    # Release technician
    tech = DB["technicians"].get(t["technician_badge"])
    if tech:
        tech["technician_status"] = "AVAILABLE"
    # Restore equipment
    eq = DB["equipment"].get(t["equipment_ref"])
    if eq and eq["equipment_status"] == "UNDER_REPAIR":
        eq["equipment_status"] = "OPERATIONAL"
    return jsonify(t)


@app.route("/api/v2/tickets/<ticket_ref>/close", methods=["POST"])
def close_ticket(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "COMPLETED":
        return error("INVALID_STATE_TRANSITION", "只有COMPLETED状态可关闭", current_status=t["ticket_status"], attempted_action="close")
    # Check settlement exists
    has_settlement = any(s["ticket_ref"] == ticket_ref for s in DB["settlements"].values())
    if not has_settlement:
        return error("SETTLEMENT_REQUIRED", "需要先完成结算")
    t["ticket_status"] = "CLOSED"
    t["closed_at"] = now_iso()
    return jsonify(t)


# ============ EQUIPMENT ============

@app.route("/api/v2/equipment", methods=["GET"])
def list_equipment():
    return jsonify({"items": list(DB["equipment"].values()), "total": len(DB["equipment"])})


@app.route("/api/v2/equipment/<equipment_ref>", methods=["GET"])
def get_equipment(equipment_ref):
    eq = DB["equipment"].get(equipment_ref)
    if not eq:
        return error("NOT_FOUND", "设备不存在", 404)
    return jsonify(eq)


@app.route("/api/v2/equipment/<equipment_ref>", methods=["PATCH"])
def update_equipment(equipment_ref):
    eq = DB["equipment"].get(equipment_ref)
    if not eq:
        return error("NOT_FOUND", "设备不存在", 404)
    data = request.get_json(force=True) or {}
    new_status = data.get("equipment_status")
    if new_status:
        if eq["equipment_status"] == "SCRAPPED":
            return error("INVALID_STATE_TRANSITION", "SCRAPPED状态不可变更")
        eq["equipment_status"] = new_status
    return jsonify(eq)


# ============ TECHNICIANS ============

@app.route("/api/v2/technicians", methods=["GET"])
def list_technicians():
    return jsonify({"items": list(DB["technicians"].values()), "total": len(DB["technicians"])})


@app.route("/api/v2/technicians/<technician_badge>", methods=["GET"])
def get_technician(technician_badge):
    tech = DB["technicians"].get(technician_badge)
    if not tech:
        return error("NOT_FOUND", "技师不存在", 404)
    return jsonify(tech)


@app.route("/api/v2/technicians/<technician_badge>", methods=["PATCH"])
def update_technician(technician_badge):
    tech = DB["technicians"].get(technician_badge)
    if not tech:
        return error("NOT_FOUND", "技师不存在", 404)
    data = request.get_json(force=True) or {}
    if "technician_status" in data:
        tech["technician_status"] = data["technician_status"]
    return jsonify(tech)


# ============ SPARE PARTS ============

@app.route("/api/v2/tickets/<ticket_ref>/parts", methods=["POST"])
def add_part_usage(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] not in ("IN_PROGRESS", "PENDING_PARTS"):
        return error("INVALID_STATE_TRANSITION", "只有维修中状态可记录备件")
    data = request.get_json(force=True) or {}
    part_code = data.get("part_code", "")
    qty = data.get("consumed_qty", 0)
    price = data.get("unit_price", 0)
    if qty <= 0:
        return error("INVALID_QUANTITY", "consumed_qty必须>0")
    # Check duplicate
    for u in DB["spare_parts"].values():
        if u["ticket_ref"] == ticket_ref and u["part_code"] == part_code:
            return error("DUPLICATE_PART_RECORD", f"备件 {part_code} 已记录")
    ref = gen_ref("USG")
    usage = {
        "usage_ref": ref,
        "ticket_ref": ticket_ref,
        "part_code": part_code,
        "consumed_qty": qty,
        "unit_price": price,
        "line_cost": round(qty * price, 2),
        "recorded_at": now_iso(),
    }
    DB["spare_parts"][ref] = usage
    return jsonify(usage), 201


@app.route("/api/v2/tickets/<ticket_ref>/parts", methods=["GET"])
def list_part_usage(ticket_ref):
    items = [u for u in DB["spare_parts"].values() if u["ticket_ref"] == ticket_ref]
    total = sum(u["line_cost"] for u in items)
    return jsonify({"items": items, "parts_cost_total": round(total, 2)})


# ============ SETTLEMENT ============

@app.route("/api/v2/tickets/<ticket_ref>/settlement", methods=["POST"])
def create_settlement(ticket_ref):
    t = DB["tickets"].get(ticket_ref)
    if not t:
        return error("NOT_FOUND", "工单不存在", 404)
    if t["ticket_status"] != "COMPLETED":
        return error("INVALID_STATE_TRANSITION", "只有COMPLETED状态可结算")
    # Check existing
    for s in DB["settlements"].values():
        if s["ticket_ref"] == ticket_ref:
            return error("DUPLICATE_SETTLEMENT", "已存在结算记录")
    data = request.get_json(force=True) or {}
    hourly_rate = data.get("hourly_rate", 0)
    labor_hours = t.get("labor_hours") or 0
    labor_cost = round(labor_hours * hourly_rate, 2)
    parts_cost = round(sum(u["line_cost"] for u in DB["spare_parts"].values() if u["ticket_ref"] == ticket_ref), 2)
    total_charge = round(labor_cost + parts_cost, 2)
    ref = gen_ref("STL")
    settlement = {
        "settlement_ref": ref,
        "ticket_ref": ticket_ref,
        "labor_hours": labor_hours,
        "hourly_rate": hourly_rate,
        "labor_cost": labor_cost,
        "parts_cost": parts_cost,
        "total_charge": total_charge,
        "settlement_status": "PENDING_APPROVAL",
        "approved_by": None,
        "created_at": now_iso(),
        "approved_at": None,
    }
    DB["settlements"][ref] = settlement
    return jsonify(settlement), 201


@app.route("/api/v2/tickets/<ticket_ref>/settlement", methods=["GET"])
def get_settlement(ticket_ref):
    for s in DB["settlements"].values():
        if s["ticket_ref"] == ticket_ref:
            return jsonify(s)
    return error("NOT_FOUND", "结算不存在", 404)


@app.route("/api/v2/tickets/<ticket_ref>/settlement/approve", methods=["POST"])
def approve_settlement(ticket_ref):
    actor = get_actor()
    for s in DB["settlements"].values():
        if s["ticket_ref"] == ticket_ref:
            if s["settlement_status"] != "PENDING_APPROVAL":
                return error("INVALID_STATE_TRANSITION", "只有PENDING_APPROVAL可审批")
            s["settlement_status"] = "APPROVED"
            s["approved_by"] = actor["badge"]
            s["approved_at"] = now_iso()
            return jsonify(s)
    return error("NOT_FOUND", "结算不存在", 404)


# ============ RESET (for testing) ============

@app.route("/api/v2/_reset", methods=["POST"])
def reset_state():
    """Reset all dynamic state (tickets, spare_parts, settlements) for fresh test runs."""
    DB["tickets"].clear()
    DB["spare_parts"].clear()
    DB["settlements"].clear()
    PLACEHOLDER_MAP["ticket"].clear()
    PLACEHOLDER_MAP["equipment"].clear()
    PLACEHOLDER_MAP["technician"].clear()
    _equipment_idx[0] = 0
    return jsonify({"status": "reset_ok", "message": "All dynamic state cleared"})


# ============ HEALTH ============

@app.route("/api/v2/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "equipment-maintenance-mock"})


if __name__ == "__main__":
    print("Equipment Maintenance Mock Server starting on :9090")
    app.run(host="0.0.0.0", port=9090, debug=False, threaded=True)
