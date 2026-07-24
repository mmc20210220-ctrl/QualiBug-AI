#!/usr/bin/env python3
"""
TicketSLA Management System - Mock Server
Project D Blind Generalization Benchmark Target

Domain: 工单与SLA管理
Entities: Ticket, SLA, Assignment, Escalation, Comment, Attachment, Customer, Agent, Team, Notification
State Machines: Ticket lifecycle, SLA status, Escalation status
Roles: Customer, Agent, Supervisor, Admin

This is the System Under Test (SUT) for Project D blind testing.
Contains hidden bugs for benchmark evaluation.
"""

import json
import uuid
import time
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ============================================================
# Authentication & Accounts
# ============================================================

ACCOUNTS = {
    "customer-alice-token": {"id": "cust-001", "name": "Alice Wang", "role": "CUSTOMER", "tenant": "acme"},
    "customer-bob-token": {"id": "cust-002", "name": "Bob Li", "role": "CUSTOMER", "tenant": "acme"},
    "customer-carol-token": {"id": "cust-003", "name": "Carol Zhang", "role": "CUSTOMER", "tenant": "globex"},
    "agent-dave-token": {"id": "agent-001", "name": "Dave Chen", "role": "AGENT", "tenant": "acme", "team": "team-001"},
    "agent-eve-token": {"id": "agent-002", "name": "Eve Liu", "role": "AGENT", "tenant": "acme", "team": "team-001"},
    "agent-frank-token": {"id": "agent-003", "name": "Frank Wu", "role": "AGENT", "tenant": "globex", "team": "team-002"},
    "supervisor-grace-token": {"id": "sup-001", "name": "Grace Zhao", "role": "SUPERVISOR", "tenant": "acme"},
    "supervisor-henry-token": {"id": "sup-002", "name": "Henry Sun", "role": "SUPERVISOR", "tenant": "globex"},
    "admin-ivan-token": {"id": "admin-001", "name": "Ivan Zhou", "role": "ADMIN", "tenant": "acme"},
    "admin-judy-token": {"id": "admin-002", "name": "Judy Xu", "role": "ADMIN", "tenant": "globex"},
}

# ============================================================
# Data Store
# ============================================================

class DataStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()
    
    def reset(self):
        """Reset to initial state"""
        self.tickets = {}
        self.slas = {}
        self.assignments = {}
        self.escalations = {}
        self.comments = {}
        self.attachments = {}
        self.customers = {}
        self.agents = {}
        self.teams = {}
        self.notifications = {}
        self._init_data()
    
    def _init_data(self):
        """Initialize base data"""
        # Teams
        self.teams["team-001"] = {
            "id": "team-001", "name": "ACME Support Team", "tenant": "acme",
            "members": ["agent-001", "agent-002"], "created_at": self._now()
        }
        self.teams["team-002"] = {
            "id": "team-002", "name": "Globex Support Team", "tenant": "globex",
            "members": ["agent-003"], "created_at": self._now()
        }
        
        # Customers
        self.customers["cust-001"] = {
            "id": "cust-001", "name": "Alice Wang", "email": "alice@acme.com",
            "tenant": "acme", "tier": "GOLD", "created_at": self._now()
        }
        self.customers["cust-002"] = {
            "id": "cust-002", "name": "Bob Li", "email": "bob@acme.com",
            "tenant": "acme", "tier": "SILVER", "created_at": self._now()
        }
        self.customers["cust-003"] = {
            "id": "cust-003", "name": "Carol Zhang", "email": "carol@globex.com",
            "tenant": "globex", "tier": "GOLD", "created_at": self._now()
        }
        
        # Agents
        self.agents["agent-001"] = {
            "id": "agent-001", "name": "Dave Chen", "tenant": "acme",
            "team_id": "team-001", "status": "AVAILABLE", "max_tickets": 5,
            "current_tickets": 0, "created_at": self._now()
        }
        self.agents["agent-002"] = {
            "id": "agent-002", "name": "Eve Liu", "tenant": "acme",
            "team_id": "team-001", "status": "AVAILABLE", "max_tickets": 5,
            "current_tickets": 0, "created_at": self._now()
        }
        self.agents["agent-003"] = {
            "id": "agent-003", "name": "Frank Wu", "tenant": "globex",
            "team_id": "team-002", "status": "AVAILABLE", "max_tickets": 5,
            "current_tickets": 0, "created_at": self._now()
        }
        
        # SLAs
        self.slas["sla-001"] = {
            "id": "sla-001", "name": "Gold Tier SLA", "tenant": "acme",
            "priority": "HIGH", "response_time_hours": 2, "resolution_time_hours": 24,
            "status": "ACTIVE", "created_at": self._now()
        }
        self.slas["sla-002"] = {
            "id": "sla-002", "name": "Silver Tier SLA", "tenant": "acme",
            "priority": "MEDIUM", "response_time_hours": 4, "resolution_time_hours": 48,
            "status": "ACTIVE", "created_at": self._now()
        }
        self.slas["sla-003"] = {
            "id": "sla-003", "name": "Globex Gold SLA", "tenant": "globex",
            "priority": "HIGH", "response_time_hours": 2, "resolution_time_hours": 24,
            "status": "ACTIVE", "created_at": self._now()
        }
        
        # Initial tickets
        self.tickets["ticket-001"] = {
            "id": "ticket-001", "title": "Login page not loading", "description": "Cannot access login page",
            "customer_id": "cust-001", "tenant": "acme", "priority": "HIGH",
            "status": "OPEN", "category": "TECHNICAL", "sla_id": "sla-001",
            "assigned_agent": None, "created_at": self._now(),
            "updated_at": self._now(), "resolved_at": None, "closed_at": None,
            "version": 1
        }
        self.tickets["ticket-002"] = {
            "id": "ticket-002", "title": "Payment failed", "description": "Payment processing error",
            "customer_id": "cust-002", "tenant": "acme", "priority": "MEDIUM",
            "status": "ASSIGNED", "category": "BILLING", "sla_id": "sla-002",
            "assigned_agent": "agent-001", "created_at": self._now(),
            "updated_at": self._now(), "resolved_at": None, "closed_at": None,
            "version": 1
        }
        self.tickets["ticket-003"] = {
            "id": "ticket-003", "title": "Feature request", "description": "Add dark mode",
            "customer_id": "cust-003", "tenant": "globex", "priority": "LOW",
            "status": "IN_PROGRESS", "category": "FEATURE", "sla_id": "sla-003",
            "assigned_agent": "agent-003", "created_at": self._now(),
            "updated_at": self._now(), "resolved_at": None, "closed_at": None,
            "version": 1
        }
        
        # Assignments
        self.assignments["assign-001"] = {
            "id": "assign-001", "ticket_id": "ticket-002", "agent_id": "agent-001",
            "tenant": "acme", "assigned_at": self._now(), "status": "ACTIVE"
        }
        self.assignments["assign-002"] = {
            "id": "assign-002", "ticket_id": "ticket-003", "agent_id": "agent-003",
            "tenant": "globex", "assigned_at": self._now(), "status": "ACTIVE"
        }
    
    def _now(self):
        return datetime.utcnow().isoformat() + "Z"

STORE = DataStore()

# ============================================================
# Helper Functions
# ============================================================

def gen_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def authenticate(headers):
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        return ACCOUNTS.get(token)
    return None

def check_role(user, allowed_roles):
    return user and user.get("role") in allowed_roles

def check_tenant(user, resource_tenant):
    return user and user.get("tenant") == resource_tenant

# ============================================================
# Request Handler
# ============================================================

class TicketSLAHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging
    
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        return {}
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        
        # Health check (no auth required)
        if path == "/health":
            self._send_json({"status": "healthy", "service": "ticketsla"})
            return
        
        user = authenticate(self.headers)
        
        if not user:
            self._send_json({"error": "Unauthorized"}, 401)
            return
        
        # List tickets
        if path == "/tickets":
            self._handle_list_tickets(user, parse_qs(parsed.query))
            return
        
        # Get ticket
        if path.startswith("/tickets/") and path.count("/") == 2:
            ticket_id = path.split("/")[2]
            self._handle_get_ticket(user, ticket_id)
            return
        
        # Get ticket comments
        if path.startswith("/tickets/") and path.endswith("/comments"):
            ticket_id = path.split("/")[2]
            self._handle_get_comments(user, ticket_id)
            return
        
        # Get ticket attachments
        if path.startswith("/tickets/") and path.endswith("/attachments"):
            ticket_id = path.split("/")[2]
            self._handle_get_attachments(user, ticket_id)
            return
        
        # List SLAs
        if path == "/slas":
            self._handle_list_slas(user)
            return
        
        # Get SLA
        if path.startswith("/slas/") and path.count("/") == 2:
            sla_id = path.split("/")[2]
            self._handle_get_sla(user, sla_id)
            return
        
        # List teams
        if path == "/teams":
            self._handle_list_teams(user)
            return
        
        # Get team
        if path.startswith("/teams/") and path.count("/") == 2:
            team_id = path.split("/")[2]
            self._handle_get_team(user, team_id)
            return
        
        # List agents
        if path == "/agents":
            self._handle_list_agents(user)
            return
        
        # Get agent
        if path.startswith("/agents/") and path.count("/") == 2:
            agent_id = path.split("/")[2]
            self._handle_get_agent(user, agent_id)
            return
        
        # List customers
        if path == "/customers":
            self._handle_list_customers(user)
            return
        
        # Get customer
        if path.startswith("/customers/") and path.count("/") == 2:
            customer_id = path.split("/")[2]
            self._handle_get_customer(user, customer_id)
            return
        
        # List escalations
        if path == "/escalations":
            self._handle_list_escalations(user)
            return
        
        # List notifications
        if path == "/notifications":
            self._handle_list_notifications(user)
            return
        
        self._send_json({"error": "Not found"}, 404)
    
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        
        if not user:
            self._send_json({"error": "Unauthorized"}, 401)
            return
        
        body = self._read_body()
        
        # Create ticket
        if path == "/tickets":
            self._handle_create_ticket(user, body)
            return
        
        # Assign ticket
        if path.startswith("/tickets/") and path.endswith("/assign"):
            ticket_id = path.split("/")[2]
            self._handle_assign_ticket(user, ticket_id, body)
            return
        
        # Start work on ticket
        if path.startswith("/tickets/") and path.endswith("/start"):
            ticket_id = path.split("/")[2]
            self._handle_start_ticket(user, ticket_id, body)
            return
        
        # Add comment
        if path.startswith("/tickets/") and path.endswith("/comments"):
            ticket_id = path.split("/")[2]
            self._handle_add_comment(user, ticket_id, body)
            return
        
        # Add attachment
        if path.startswith("/tickets/") and path.endswith("/attachments"):
            ticket_id = path.split("/")[2]
            self._handle_add_attachment(user, ticket_id, body)
            return
        
        # Escalate ticket
        if path.startswith("/tickets/") and path.endswith("/escalate"):
            ticket_id = path.split("/")[2]
            self._handle_escalate_ticket(user, ticket_id, body)
            return
        
        # Resolve ticket
        if path.startswith("/tickets/") and path.endswith("/resolve"):
            ticket_id = path.split("/")[2]
            self._handle_resolve_ticket(user, ticket_id, body)
            return
        
        # Close ticket
        if path.startswith("/tickets/") and path.endswith("/close"):
            ticket_id = path.split("/")[2]
            self._handle_close_ticket(user, ticket_id, body)
            return
        
        # Reopen ticket
        if path.startswith("/tickets/") and path.endswith("/reopen"):
            ticket_id = path.split("/")[2]
            self._handle_reopen_ticket(user, ticket_id, body)
            return
        
        # Transfer ticket
        if path.startswith("/tickets/") and path.endswith("/transfer"):
            ticket_id = path.split("/")[2]
            self._handle_transfer_ticket(user, ticket_id, body)
            return
        
        # Merge tickets
        if path == "/tickets/merge":
            self._handle_merge_tickets(user, body)
            return
        
        # Create SLA
        if path == "/slas":
            self._handle_create_sla(user, body)
            return
        
        # Create team
        if path == "/teams":
            self._handle_create_team(user, body)
            return
        
        # Add member to team
        if path.startswith("/teams/") and path.endswith("/members"):
            team_id = path.split("/")[2]
            self._handle_add_team_member(user, team_id, body)
            return
        
        # Create customer
        if path == "/customers":
            self._handle_create_customer(user, body)
            return
        
        # Bulk assign
        if path == "/tickets/bulk-assign":
            self._handle_bulk_assign(user, body)
            return
        
        self._send_json({"error": "Not found"}, 404)
    
    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        
        if not user:
            self._send_json({"error": "Unauthorized"}, 401)
            return
        
        body = self._read_body()
        
        # Update ticket
        if path.startswith("/tickets/") and path.count("/") == 2:
            ticket_id = path.split("/")[2]
            self._handle_update_ticket(user, ticket_id, body)
            return
        
        # Update SLA
        if path.startswith("/slas/") and path.count("/") == 2:
            sla_id = path.split("/")[2]
            self._handle_update_sla(user, sla_id, body)
            return
        
        # Update customer
        if path.startswith("/customers/") and path.count("/") == 2:
            customer_id = path.split("/")[2]
            self._handle_update_customer(user, customer_id, body)
            return
        
        self._send_json({"error": "Not found"}, 404)
    
    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        user = authenticate(self.headers)
        
        if not user:
            self._send_json({"error": "Unauthorized"}, 401)
            return
        
        # Remove team member
        if path.startswith("/teams/") and "/members/" in path:
            parts = path.split("/")
            team_id = parts[2]
            member_id = parts[4]
            self._handle_remove_team_member(user, team_id, member_id)
            return
        
        # Delete attachment
        if path.startswith("/tickets/") and "/attachments/" in path:
            parts = path.split("/")
            ticket_id = parts[2]
            attachment_id = parts[4]
            self._handle_delete_attachment(user, ticket_id, attachment_id)
            return
        
        self._send_json({"error": "Not found"}, 404)
    
    # ============================================================
    # Ticket Handlers
    # ============================================================
    
    def _handle_list_tickets(self, user, params):
        with STORE.lock:
            tickets = list(STORE.tickets.values())
            
            # BUG-TSLA-001: Tenant isolation - Customer can see all tickets
            # Should filter by tenant for customers
            if user["role"] == "CUSTOMER":
                # Missing: tickets = [t for t in tickets if t["tenant"] == user["tenant"]]
                # Missing: tickets = [t for t in tickets if t["customer_id"] == user["id"]]
                pass  # BUG: No filtering
            
            # Filter by status if provided
            status = params.get("status", [None])[0]
            if status:
                tickets = [t for t in tickets if t["status"] == status]
            
            self._send_json({"tickets": tickets, "total": len(tickets)})
    
    def _handle_get_ticket(self, user, ticket_id):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # BUG-TSLA-002: Tenant isolation - Any user can view any ticket
            # Should check: if ticket["tenant"] != user["tenant"]: 403
            # Missing tenant check
            
            self._send_json(ticket)
    
    def _handle_create_ticket(self, user, body):
        with STORE.lock:
            # Only customers can create tickets
            if user["role"] != "CUSTOMER":
                self._send_json({"error": "Only customers can create tickets"}, 403)
                return
            
            title = body.get("title")
            description = body.get("description")
            priority = body.get("priority", "MEDIUM")
            category = body.get("category", "GENERAL")
            
            if not title:
                self._send_json({"error": "Title is required"}, 400)
                return
            
            # BUG-TSLA-003: Priority validation missing
            # Should validate priority is one of: LOW, MEDIUM, HIGH, CRITICAL
            # Missing: if priority not in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]: 400
            
            # Get customer's SLA based on tier
            customer = STORE.customers.get(user["id"])
            sla_id = None
            if customer:
                if customer["tier"] == "GOLD":
                    sla_id = "sla-001" if user["tenant"] == "acme" else "sla-003"
                else:
                    sla_id = "sla-002"
            
            ticket_id = gen_id("ticket")
            ticket = {
                "id": ticket_id,
                "title": title,
                "description": description,
                "customer_id": user["id"],
                "tenant": user["tenant"],
                "priority": priority,
                "status": "OPEN",
                "category": category,
                "sla_id": sla_id,
                "assigned_agent": None,
                "created_at": STORE._now(),
                "updated_at": STORE._now(),
                "resolved_at": None,
                "closed_at": None,
                "version": 1
            }
            STORE.tickets[ticket_id] = ticket
            
            # Create notification
            self._create_notification(user["tenant"], "TICKET_CREATED", ticket_id, f"New ticket: {title}")
            
            self._send_json(ticket, 201)
    
    def _handle_assign_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # BUG-TSLA-004: State transition - Can assign non-OPEN tickets
            # Should check: if ticket["status"] != "OPEN": 409
            # Missing state check - allows assigning ASSIGNED/IN_PROGRESS tickets
            
            # Only Supervisor or Admin can assign
            if not check_role(user, ["SUPERVISOR", "ADMIN"]):
                self._send_json({"error": "Only supervisors can assign tickets"}, 403)
                return
            
            agent_id = body.get("agent_id")
            if not agent_id:
                self._send_json({"error": "agent_id is required"}, 400)
                return
            
            agent = STORE.agents.get(agent_id)
            if not agent:
                self._send_json({"error": "Agent not found"}, 404)
                return
            
            # BUG-TSLA-005: Tenant isolation - Can assign cross-tenant agent
            # Should check: if agent["tenant"] != ticket["tenant"]: 403
            # Missing tenant check
            
            # BUG-TSLA-006: Capacity check missing
            # Should check: if agent["current_tickets"] >= agent["max_tickets"]: 409
            # Missing capacity validation
            
            # Update ticket
            ticket["status"] = "ASSIGNED"
            ticket["assigned_agent"] = agent_id
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            # Create assignment record
            assign_id = gen_id("assign")
            STORE.assignments[assign_id] = {
                "id": assign_id,
                "ticket_id": ticket_id,
                "agent_id": agent_id,
                "tenant": ticket["tenant"],
                "assigned_at": STORE._now(),
                "status": "ACTIVE"
            }
            
            # BUG-TSLA-007: Agent workload not updated
            # Should: agent["current_tickets"] += 1
            # Missing workload update
            
            self._send_json(ticket)
    
    def _handle_start_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # Only assigned agent can start
            if ticket["assigned_agent"] != user["id"]:
                self._send_json({"error": "Only assigned agent can start ticket"}, 403)
                return
            
            # BUG-TSLA-008: State transition - Can start non-ASSIGNED tickets
            # Should check: if ticket["status"] != "ASSIGNED": 409
            # Missing state check
            
            ticket["status"] = "IN_PROGRESS"
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            self._send_json(ticket)
    
    def _handle_add_comment(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # BUG-TSLA-009: Can comment on CLOSED tickets
            # Should check: if ticket["status"] == "CLOSED": 409
            # Missing state check
            
            content = body.get("content")
            if not content:
                self._send_json({"error": "Content is required"}, 400)
                return
            
            # BUG-TSLA-010: Comment length validation missing
            # Should check: if len(content) > 5000: 400
            # Missing length validation
            
            comment_id = gen_id("comment")
            comment = {
                "id": comment_id,
                "ticket_id": ticket_id,
                "author_id": user["id"],
                "author_name": user["name"],
                "content": content,
                "tenant": ticket["tenant"],
                "created_at": STORE._now()
            }
            STORE.comments[comment_id] = comment
            
            ticket["updated_at"] = STORE._now()
            
            self._send_json(comment, 201)
    
    def _handle_add_attachment(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            filename = body.get("filename")
            size = body.get("size", 0)
            
            if not filename:
                self._send_json({"error": "Filename is required"}, 400)
                return
            
            # BUG-TSLA-011: Attachment size limit not enforced
            # Should check: if size > 10 * 1024 * 1024: 400 (10MB limit)
            # Missing size validation
            
            attachment_id = gen_id("attach")
            attachment = {
                "id": attachment_id,
                "ticket_id": ticket_id,
                "filename": filename,
                "size": size,
                "uploaded_by": user["id"],
                "tenant": ticket["tenant"],
                "created_at": STORE._now()
            }
            STORE.attachments[attachment_id] = attachment
            
            self._send_json(attachment, 201)
    
    def _handle_escalate_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # Only Supervisor can escalate
            if user["role"] != "SUPERVISOR":
                self._send_json({"error": "Only supervisors can escalate"}, 403)
                return
            
            # BUG-TSLA-012: Can escalate CLOSED tickets
            # Should check: if ticket["status"] in ["CLOSED", "RESOLVED"]: 409
            # Missing state check
            
            reason = body.get("reason")
            if not reason:
                self._send_json({"error": "Reason is required"}, 400)
                return
            
            escalation_id = gen_id("esc")
            escalation = {
                "id": escalation_id,
                "ticket_id": ticket_id,
                "escalated_by": user["id"],
                "reason": reason,
                "status": "PENDING",
                "tenant": ticket["tenant"],
                "created_at": STORE._now()
            }
            STORE.escalations[escalation_id] = escalation
            
            # BUG-TSLA-013: Priority not upgraded on escalation
            # Should: ticket["priority"] = "CRITICAL"
            # Missing priority upgrade
            
            ticket["updated_at"] = STORE._now()
            
            self._send_json(escalation, 201)
    
    def _handle_resolve_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # Only assigned agent can resolve
            if ticket["assigned_agent"] != user["id"]:
                self._send_json({"error": "Only assigned agent can resolve"}, 403)
                return
            
            # BUG-TSLA-014: State transition - Can resolve non-IN_PROGRESS tickets
            # Should check: if ticket["status"] != "IN_PROGRESS": 409
            # Missing state check - allows resolving OPEN/ASSIGNED tickets
            
            resolution = body.get("resolution")
            if not resolution:
                self._send_json({"error": "Resolution is required"}, 400)
                return
            
            ticket["status"] = "RESOLVED"
            ticket["resolved_at"] = STORE._now()
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            # BUG-TSLA-015: SLA compliance not checked on resolve
            # Should calculate if resolution time exceeded SLA
            # Missing SLA check
            
            self._send_json(ticket)
    
    def _handle_close_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # Only customer or supervisor can close
            if not check_role(user, ["CUSTOMER", "SUPERVISOR", "ADMIN"]):
                self._send_json({"error": "Only customer or supervisor can close"}, 403)
                return
            
            # BUG-TSLA-016: State transition - Can close non-RESOLVED tickets
            # Should check: if ticket["status"] != "RESOLVED": 409
            # Missing state check - allows closing OPEN/IN_PROGRESS tickets
            
            # BUG-TSLA-017: Customer authorization - Any customer can close any ticket
            # Should check: if user["role"] == "CUSTOMER" and ticket["customer_id"] != user["id"]: 403
            # Missing ownership check
            
            ticket["status"] = "CLOSED"
            ticket["closed_at"] = STORE._now()
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            # BUG-TSLA-018: Agent workload not decremented on close
            # Should: agent["current_tickets"] -= 1
            # Missing workload update
            
            self._send_json(ticket)
    
    def _handle_reopen_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # Only customer can reopen
            if user["role"] != "CUSTOMER":
                self._send_json({"error": "Only customer can reopen"}, 403)
                return
            
            # BUG-TSLA-019: State transition - Can reopen OPEN/IN_PROGRESS tickets
            # Should check: if ticket["status"] not in ["RESOLVED", "CLOSED"]: 409
            # Missing state check
            
            # BUG-TSLA-020: Customer authorization - Any customer can reopen any ticket
            # Should check: if ticket["customer_id"] != user["id"]: 403
            # Missing ownership check
            
            reason = body.get("reason")
            if not reason:
                self._send_json({"error": "Reason is required"}, 400)
                return
            
            ticket["status"] = "OPEN"
            ticket["assigned_agent"] = None
            ticket["resolved_at"] = None
            ticket["closed_at"] = None
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            # BUG-TSLA-021: SLA not reset on reopen
            # Should create new SLA deadline
            # Missing SLA reset
            
            self._send_json(ticket)
    
    def _handle_transfer_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # Only Supervisor can transfer
            if user["role"] != "SUPERVISOR":
                self._send_json({"error": "Only supervisors can transfer"}, 403)
                return
            
            new_agent_id = body.get("agent_id")
            if not new_agent_id:
                self._send_json({"error": "agent_id is required"}, 400)
                return
            
            new_agent = STORE.agents.get(new_agent_id)
            if not new_agent:
                self._send_json({"error": "Agent not found"}, 404)
                return
            
            # BUG-TSLA-022: Old agent workload not decremented
            # Should decrement old agent's current_tickets
            # Missing workload adjustment
            
            old_agent_id = ticket["assigned_agent"]
            ticket["assigned_agent"] = new_agent_id
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            # BUG-TSLA-023: New agent workload not incremented
            # Should: new_agent["current_tickets"] += 1
            # Missing workload update
            
            # Create new assignment
            assign_id = gen_id("assign")
            STORE.assignments[assign_id] = {
                "id": assign_id,
                "ticket_id": ticket_id,
                "agent_id": new_agent_id,
                "tenant": ticket["tenant"],
                "assigned_at": STORE._now(),
                "status": "ACTIVE"
            }
            
            # BUG-TSLA-024: Old assignment not deactivated
            # Should set old assignment status to "TRANSFERRED"
            # Missing old assignment update
            
            self._send_json(ticket)
    
    def _handle_merge_tickets(self, user, body):
        with STORE.lock:
            # Only Supervisor or Admin can merge
            if not check_role(user, ["SUPERVISOR", "ADMIN"]):
                self._send_json({"error": "Only supervisors can merge tickets"}, 403)
                return
            
            source_id = body.get("source_ticket_id")
            target_id = body.get("target_ticket_id")
            
            if not source_id or not target_id:
                self._send_json({"error": "Both ticket IDs are required"}, 400)
                return
            
            source = STORE.tickets.get(source_id)
            target = STORE.tickets.get(target_id)
            
            if not source or not target:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # BUG-TSLA-025: Tenant isolation - Can merge cross-tenant tickets
            # Should check: if source["tenant"] != target["tenant"]: 403
            # Missing tenant check
            
            # BUG-TSLA-026: State validation - Can merge CLOSED tickets
            # Should check: if source["status"] == "CLOSED": 409
            # Missing state check
            
            # Merge: close source, add reference to target
            source["status"] = "CLOSED"
            source["closed_at"] = STORE._now()
            source["merged_into"] = target_id
            
            # BUG-TSLA-027: Priority not inherited (should take higher priority)
            # Should: target["priority"] = max(source["priority"], target["priority"])
            # Missing priority merge
            
            target["updated_at"] = STORE._now()
            
            self._send_json({"merged": True, "source": source_id, "target": target_id})
    
    def _handle_bulk_assign(self, user, body):
        with STORE.lock:
            # Only Supervisor or Admin
            if not check_role(user, ["SUPERVISOR", "ADMIN"]):
                self._send_json({"error": "Only supervisors can bulk assign"}, 403)
                return
            
            ticket_ids = body.get("ticket_ids", [])
            agent_id = body.get("agent_id")
            
            if not ticket_ids or not agent_id:
                self._send_json({"error": "ticket_ids and agent_id are required"}, 400)
                return
            
            agent = STORE.agents.get(agent_id)
            if not agent:
                self._send_json({"error": "Agent not found"}, 404)
                return
            
            results = []
            for tid in ticket_ids:
                ticket = STORE.tickets.get(tid)
                if ticket:
                    # BUG-TSLA-028: No state validation in bulk assign
                    # Should check each ticket is OPEN
                    ticket["status"] = "ASSIGNED"
                    ticket["assigned_agent"] = agent_id
                    ticket["updated_at"] = STORE._now()
                    results.append({"ticket_id": tid, "status": "assigned"})
                else:
                    results.append({"ticket_id": tid, "status": "not_found"})
            
            self._send_json({"results": results})
    
    def _handle_update_ticket(self, user, ticket_id, body):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            # BUG-TSLA-029: Concurrency - No version check (optimistic locking)
            # Should check: if body.get("version") != ticket["version"]: 409
            # Missing optimistic locking
            
            # BUG-TSLA-030: Authorization - Any user can update any ticket
            # Should check ownership or role
            # Missing authorization
            
            if "title" in body:
                ticket["title"] = body["title"]
            if "description" in body:
                ticket["description"] = body["description"]
            if "priority" in body:
                # BUG-TSLA-031: Priority change validation missing
                # Should validate priority value and restrict who can change
                ticket["priority"] = body["priority"]
            
            ticket["updated_at"] = STORE._now()
            ticket["version"] += 1
            
            self._send_json(ticket)
    
    # ============================================================
    # SLA Handlers
    # ============================================================
    
    def _handle_list_slas(self, user):
        with STORE.lock:
            slas = [s for s in STORE.slas.values() if s["tenant"] == user["tenant"]]
            self._send_json({"slas": slas})
    
    def _handle_get_sla(self, user, sla_id):
        with STORE.lock:
            sla = STORE.slas.get(sla_id)
            if not sla:
                self._send_json({"error": "SLA not found"}, 404)
                return
            self._send_json(sla)
    
    def _handle_create_sla(self, user, body):
        with STORE.lock:
            # Only Admin can create SLA
            if user["role"] != "ADMIN":
                self._send_json({"error": "Only admins can create SLAs"}, 403)
                return
            
            name = body.get("name")
            priority = body.get("priority")
            response_hours = body.get("response_time_hours")
            resolution_hours = body.get("resolution_time_hours")
            
            if not name or not priority:
                self._send_json({"error": "Name and priority are required"}, 400)
                return
            
            # BUG-TSLA-032: SLA time validation missing
            # Should check: response_hours > 0 and resolution_hours > 0
            # Should check: resolution_hours >= response_hours
            # Missing validation
            
            sla_id = gen_id("sla")
            sla = {
                "id": sla_id,
                "name": name,
                "tenant": user["tenant"],
                "priority": priority,
                "response_time_hours": response_hours,
                "resolution_time_hours": resolution_hours,
                "status": "ACTIVE",
                "created_at": STORE._now()
            }
            STORE.slas[sla_id] = sla
            
            self._send_json(sla, 201)
    
    def _handle_update_sla(self, user, sla_id, body):
        with STORE.lock:
            sla = STORE.slas.get(sla_id)
            if not sla:
                self._send_json({"error": "SLA not found"}, 404)
                return
            
            # Only Admin can update SLA
            if user["role"] != "ADMIN":
                self._send_json({"error": "Only admins can update SLAs"}, 403)
                return
            
            # BUG-TSLA-033: Can update ACTIVE SLA affecting existing tickets
            # Should check: if sla["status"] == "ACTIVE" and has_active_tickets: 409
            # Missing impact check
            
            if "response_time_hours" in body:
                sla["response_time_hours"] = body["response_time_hours"]
            if "resolution_time_hours" in body:
                sla["resolution_time_hours"] = body["resolution_time_hours"]
            
            self._send_json(sla)
    
    # ============================================================
    # Team Handlers
    # ============================================================
    
    def _handle_list_teams(self, user):
        with STORE.lock:
            teams = [t for t in STORE.teams.values() if t["tenant"] == user["tenant"]]
            self._send_json({"teams": teams})
    
    def _handle_get_team(self, user, team_id):
        with STORE.lock:
            team = STORE.teams.get(team_id)
            if not team:
                self._send_json({"error": "Team not found"}, 404)
                return
            self._send_json(team)
    
    def _handle_create_team(self, user, body):
        with STORE.lock:
            # Only Admin can create team
            if user["role"] != "ADMIN":
                self._send_json({"error": "Only admins can create teams"}, 403)
                return
            
            name = body.get("name")
            if not name:
                self._send_json({"error": "Name is required"}, 400)
                return
            
            team_id = gen_id("team")
            team = {
                "id": team_id,
                "name": name,
                "tenant": user["tenant"],
                "members": [],
                "created_at": STORE._now()
            }
            STORE.teams[team_id] = team
            
            self._send_json(team, 201)
    
    def _handle_add_team_member(self, user, team_id, body):
        with STORE.lock:
            team = STORE.teams.get(team_id)
            if not team:
                self._send_json({"error": "Team not found"}, 404)
                return
            
            # Only Supervisor or Admin
            if not check_role(user, ["SUPERVISOR", "ADMIN"]):
                self._send_json({"error": "Only supervisors can add members"}, 403)
                return
            
            agent_id = body.get("agent_id")
            if not agent_id:
                self._send_json({"error": "agent_id is required"}, 400)
                return
            
            agent = STORE.agents.get(agent_id)
            if not agent:
                self._send_json({"error": "Agent not found"}, 404)
                return
            
            # BUG-TSLA-034: Tenant isolation - Can add cross-tenant agent
            # Should check: if agent["tenant"] != team["tenant"]: 403
            # Missing tenant check
            
            # BUG-TSLA-035: Duplicate member check missing
            # Should check: if agent_id in team["members"]: 409
            # Missing duplicate check
            
            team["members"].append(agent_id)
            agent["team_id"] = team_id
            
            self._send_json(team)
    
    def _handle_remove_team_member(self, user, team_id, member_id):
        with STORE.lock:
            team = STORE.teams.get(team_id)
            if not team:
                self._send_json({"error": "Team not found"}, 404)
                return
            
            # Only Supervisor or Admin
            if not check_role(user, ["SUPERVISOR", "ADMIN"]):
                self._send_json({"error": "Only supervisors can remove members"}, 403)
                return
            
            if member_id not in team["members"]:
                self._send_json({"error": "Member not in team"}, 404)
                return
            
            # BUG-TSLA-036: Can remove agent with active tickets
            # Should check: if agent has active assigned tickets: 409
            # Missing active ticket check
            
            team["members"].remove(member_id)
            
            self._send_json(team)
    
    # ============================================================
    # Agent Handlers
    # ============================================================
    
    def _handle_list_agents(self, user):
        with STORE.lock:
            agents = [a for a in STORE.agents.values() if a["tenant"] == user["tenant"]]
            self._send_json({"agents": agents})
    
    def _handle_get_agent(self, user, agent_id):
        with STORE.lock:
            agent = STORE.agents.get(agent_id)
            if not agent:
                self._send_json({"error": "Agent not found"}, 404)
                return
            self._send_json(agent)
    
    # ============================================================
    # Customer Handlers
    # ============================================================
    
    def _handle_list_customers(self, user):
        with STORE.lock:
            # BUG-TSLA-037: Tenant isolation - Can list all customers
            # Should filter by tenant
            customers = list(STORE.customers.values())
            self._send_json({"customers": customers})
    
    def _handle_get_customer(self, user, customer_id):
        with STORE.lock:
            customer = STORE.customers.get(customer_id)
            if not customer:
                self._send_json({"error": "Customer not found"}, 404)
                return
            
            # BUG-TSLA-038: Tenant isolation - Can view cross-tenant customer
            # Should check: if customer["tenant"] != user["tenant"]: 403
            # Missing tenant check
            
            self._send_json(customer)
    
    def _handle_create_customer(self, user, body):
        with STORE.lock:
            # Only Admin can create customer
            if user["role"] != "ADMIN":
                self._send_json({"error": "Only admins can create customers"}, 403)
                return
            
            name = body.get("name")
            email = body.get("email")
            tier = body.get("tier", "SILVER")
            
            if not name or not email:
                self._send_json({"error": "Name and email are required"}, 400)
                return
            
            # BUG-TSLA-039: Email uniqueness validation missing
            # Should check: if email already exists: 409
            # Missing uniqueness check
            
            customer_id = gen_id("cust")
            customer = {
                "id": customer_id,
                "name": name,
                "email": email,
                "tenant": user["tenant"],
                "tier": tier,
                "created_at": STORE._now()
            }
            STORE.customers[customer_id] = customer
            
            self._send_json(customer, 201)
    
    def _handle_update_customer(self, user, customer_id, body):
        with STORE.lock:
            customer = STORE.customers.get(customer_id)
            if not customer:
                self._send_json({"error": "Customer not found"}, 404)
                return
            
            # BUG-TSLA-040: Authorization - Any user can update any customer
            # Should check: if user["role"] != "ADMIN" and user["id"] != customer_id: 403
            # Missing authorization
            
            if "name" in body:
                customer["name"] = body["name"]
            if "email" in body:
                customer["email"] = body["email"]
            if "tier" in body:
                # BUG-TSLA-041: Tier change validation missing
                # Should restrict tier changes to Admin only
                customer["tier"] = body["tier"]
            
            self._send_json(customer)
    
    # ============================================================
    # Comment/Attachment/Notification Handlers
    # ============================================================
    
    def _handle_get_comments(self, user, ticket_id):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            comments = [c for c in STORE.comments.values() if c["ticket_id"] == ticket_id]
            self._send_json({"comments": comments})
    
    def _handle_get_attachments(self, user, ticket_id):
        with STORE.lock:
            ticket = STORE.tickets.get(ticket_id)
            if not ticket:
                self._send_json({"error": "Ticket not found"}, 404)
                return
            
            attachments = [a for a in STORE.attachments.values() if a["ticket_id"] == ticket_id]
            self._send_json({"attachments": attachments})
    
    def _handle_delete_attachment(self, user, ticket_id, attachment_id):
        with STORE.lock:
            attachment = STORE.attachments.get(attachment_id)
            if not attachment:
                self._send_json({"error": "Attachment not found"}, 404)
                return
            
            # BUG-TSLA-042: Authorization - Any user can delete any attachment
            # Should check: if attachment["uploaded_by"] != user["id"] and user["role"] not in ["SUPERVISOR", "ADMIN"]: 403
            # Missing authorization
            
            del STORE.attachments[attachment_id]
            self._send_json({"deleted": True})
    
    def _handle_list_escalations(self, user):
        with STORE.lock:
            escalations = [e for e in STORE.escalations.values() if e["tenant"] == user["tenant"]]
            self._send_json({"escalations": escalations})
    
    def _handle_list_notifications(self, user):
        with STORE.lock:
            notifications = [n for n in STORE.notifications.values() 
                           if n["tenant"] == user["tenant"] and n.get("user_id") == user["id"]]
            self._send_json({"notifications": notifications})
    
    def _create_notification(self, tenant, event_type, resource_id, message):
        notif_id = gen_id("notif")
        STORE.notifications[notif_id] = {
            "id": notif_id,
            "tenant": tenant,
            "event_type": event_type,
            "resource_id": resource_id,
            "message": message,
            "created_at": STORE._now()
        }


# ============================================================
# Server Entry Point
# ============================================================

def run_server(port=8002):
    server = HTTPServer(("0.0.0.0", port), TicketSLAHandler)
    print(f"TicketSLA Mock Server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    run_server(port)
