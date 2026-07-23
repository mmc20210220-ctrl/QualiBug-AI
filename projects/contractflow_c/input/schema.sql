CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tenants (
  id UUID PRIMARY KEY,
  code VARCHAR(32) NOT NULL UNIQUE,
  name VARCHAR(128) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE departments (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  code VARCHAR(32) NOT NULL,
  name VARCHAR(128) NOT NULL,
  UNIQUE (tenant_id, code)
);

CREATE TABLE users (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  department_id UUID NULL REFERENCES departments(id),
  email VARCHAR(160) NOT NULL UNIQUE,
  password VARCHAR(160) NOT NULL,
  full_name VARCHAR(120) NOT NULL,
  role VARCHAR(40) NOT NULL,
  api_token VARCHAR(160) NOT NULL UNIQUE,
  active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE vendors (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  code VARCHAR(40) NOT NULL,
  name VARCHAR(160) NOT NULL,
  contact_email VARCHAR(160) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (tenant_id, code)
);

CREATE TABLE budgets (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  department_id UUID NOT NULL REFERENCES departments(id),
  fiscal_year INTEGER NOT NULL,
  total_amount NUMERIC(18,2) NOT NULL,
  available_amount NUMERIC(18,2) NOT NULL,
  reserved_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  spent_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (tenant_id, department_id, fiscal_year)
);

CREATE TABLE contracts (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contract_no VARCHAR(64) NOT NULL,
  title VARCHAR(200) NOT NULL,
  owner_id UUID NOT NULL REFERENCES users(id),
  department_id UUID NOT NULL REFERENCES departments(id),
  vendor_id UUID NOT NULL REFERENCES vendors(id),
  budget_id UUID NOT NULL REFERENCES budgets(id),
  total_amount NUMERIC(18,2) NOT NULL,
  paid_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  currency VARCHAR(3) NOT NULL DEFAULT 'CNY',
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
  internal_notes TEXT NULL,
  rejection_reason TEXT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, contract_no)
);

CREATE INDEX idx_contracts_tenant_status ON contracts(tenant_id, status);
CREATE INDEX idx_contracts_owner ON contracts(owner_id);

CREATE TABLE milestones (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  name VARCHAR(160) NOT NULL,
  amount NUMERIC(18,2) NOT NULL,
  accepted_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  due_date DATE NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
  submission_version INTEGER NOT NULL DEFAULT 0,
  evidence_url VARCHAR(500) NULL,
  version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_milestones_contract ON milestones(contract_id);

CREATE TABLE acceptance_records (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  milestone_id UUID NOT NULL REFERENCES milestones(id),
  accepted_by UUID NOT NULL REFERENCES users(id),
  accepted_amount NUMERIC(18,2) NOT NULL,
  notes TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE invoices (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  vendor_id UUID NOT NULL REFERENCES vendors(id),
  invoice_no VARCHAR(80) NOT NULL,
  subtotal NUMERIC(18,2) NOT NULL,
  tax_amount NUMERIC(18,2) NOT NULL,
  total_amount NUMERIC(18,2) NOT NULL,
  issue_date DATE NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'VALID',
  created_by UUID NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_invoices_contract ON invoices(contract_id);

CREATE TABLE payment_requests (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  contract_id UUID NOT NULL REFERENCES contracts(id),
  milestone_id UUID NOT NULL REFERENCES milestones(id),
  invoice_id UUID NOT NULL REFERENCES invoices(id),
  requested_by UUID NOT NULL REFERENCES users(id),
  amount NUMERIC(18,2) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
  idempotency_key VARCHAR(160) NULL,
  manager_approved_by UUID NULL REFERENCES users(id),
  finance_approved_by UUID NULL REFERENCES users(id),
  paid_at TIMESTAMPTZ NULL,
  rejection_reason TEXT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_payments_contract_status ON payment_requests(contract_id, status);
CREATE INDEX idx_payments_milestone ON payment_requests(milestone_id);

CREATE TABLE audit_logs (
  id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  actor_id UUID NOT NULL REFERENCES users(id),
  entity_type VARCHAR(64) NOT NULL,
  entity_id UUID NOT NULL,
  action VARCHAR(80) NOT NULL,
  before_data JSONB NULL,
  after_data JSONB NULL,
  correlation_id VARCHAR(120) NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_tenant_created ON audit_logs(tenant_id, created_at DESC);
