CREATE TABLE tenants (id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE users (id text PRIMARY KEY, display_name text NOT NULL, enabled boolean NOT NULL DEFAULT true);
CREATE TABLE memberships (
    tenant_id text REFERENCES tenants, user_id text REFERENCES users,
    role text CHECK (role IN ('support','supervisor','auditor')),
    PRIMARY KEY (tenant_id,user_id,role)
);
CREATE TABLE orders (
    tenant_id text REFERENCES tenants, order_no text NOT NULL, amount_minor bigint NOT NULL CHECK(amount_minor>=0),
    currency text NOT NULL CHECK(currency='CNY'), paid_at timestamptz, shipped_at timestamptz,
    refund_state text NOT NULL DEFAULT 'none' CHECK(refund_state IN ('none','reserved','refunded')),
    PRIMARY KEY(tenant_id,order_no),
    CHECK(shipped_at IS NULL OR (paid_at IS NOT NULL AND shipped_at>=paid_at AND refund_state='none')),
    CHECK(refund_state='none' OR paid_at IS NOT NULL)
);
CREATE TABLE refund_policies (
    tenant_id text REFERENCES tenants, version text NOT NULL, effective_at timestamptz NOT NULL,
    rules jsonb NOT NULL, PRIMARY KEY(tenant_id,version)
);
CREATE TABLE refund_requests (
    tenant_id text, id text, order_no text NOT NULL, applicant_id text NOT NULL,
    applicant_role text NOT NULL DEFAULT 'support' CHECK(applicant_role='support'),
    reason text NOT NULL, amount_minor bigint NOT NULL CHECK(amount_minor>0),
    currency text NOT NULL CHECK(currency='CNY'), policy_version text NOT NULL,
    state text NOT NULL CHECK(state IN ('pending','approved','executing','reconciling','manual','succeeded','failed','closed')),
    close_reason text, created_at timestamptz NOT NULL, approve_by timestamptz NOT NULL,
    start_by timestamptz, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,id,applicant_id),
    FOREIGN KEY(tenant_id,order_no) REFERENCES orders,
    FOREIGN KEY(tenant_id,policy_version) REFERENCES refund_policies,
    FOREIGN KEY(tenant_id,applicant_id,applicant_role) REFERENCES memberships,
    CHECK(approve_by>created_at), CHECK ((state='closed')=(close_reason IS NOT NULL))
);
CREATE UNIQUE INDEX one_active_or_success ON refund_requests(tenant_id,order_no)
    WHERE state IN ('pending','approved','executing','reconciling','manual','succeeded');
CREATE TABLE refund_decisions (
    tenant_id text, request_id text, applicant_id text NOT NULL, reviewer_id text NOT NULL,
    reviewer_role text NOT NULL DEFAULT 'supervisor' CHECK(reviewer_role='supervisor'),
    decision text NOT NULL CHECK(decision IN ('approve','reject')), note text NOT NULL, decided_at timestamptz NOT NULL,
    PRIMARY KEY(tenant_id,request_id), CHECK(reviewer_id<>applicant_id),
    FOREIGN KEY(tenant_id,request_id,applicant_id) REFERENCES refund_requests(tenant_id,id,applicant_id),
    FOREIGN KEY(tenant_id,reviewer_id,reviewer_role) REFERENCES memberships
);
CREATE TABLE refund_operations (
    tenant_id text, id text, request_id text NOT NULL, payment_key text NOT NULL UNIQUE,
    payload_digest text NOT NULL, state text NOT NULL CHECK(state IN ('prepared','unknown','succeeded','failed')),
    channel_ref text, created_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,id),
    UNIQUE(tenant_id,request_id), FOREIGN KEY(tenant_id,request_id) REFERENCES refund_requests
);
CREATE TABLE jobs (
    tenant_id text, id text, request_id text NOT NULL, kind text NOT NULL CHECK(kind IN ('execute','reconcile')),
    state text NOT NULL CHECK(state IN ('ready','leased','done','manual')),
    run_at timestamptz NOT NULL, lease_until timestamptz, lease_owner text, attempts integer NOT NULL CHECK(attempts>=0),
    last_error text, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,request_id,kind),
    FOREIGN KEY(tenant_id,request_id) REFERENCES refund_requests,
    CHECK((state='leased')=(lease_until IS NOT NULL)), CHECK((state='leased')=(lease_owner IS NOT NULL))
);
CREATE INDEX jobs_due ON jobs(run_at) WHERE state='ready';
CREATE TABLE idempotency_records (
    tenant_id text, user_id text NOT NULL, action text, key text, payload_digest text NOT NULL,
    request_id text NOT NULL, PRIMARY KEY(tenant_id,user_id,action,key),
    FOREIGN KEY(tenant_id,request_id) REFERENCES refund_requests,
    actor_role text NOT NULL DEFAULT 'support' CHECK(actor_role='support'),
    FOREIGN KEY(tenant_id,user_id,actor_role) REFERENCES memberships
);
CREATE TABLE action_confirmations (
    tenant_id text, id text, user_id text NOT NULL, action text NOT NULL,
    payload_digest text NOT NULL, expires_at timestamptz NOT NULL, consumed_at timestamptz,
    PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id) REFERENCES tenants, CHECK(consumed_at<=expires_at),
    actor_role text NOT NULL DEFAULT 'support' CHECK(actor_role='support'),
    FOREIGN KEY(tenant_id,user_id,actor_role) REFERENCES memberships
);
CREATE TABLE agent_runs (
    tenant_id text REFERENCES tenants, id text, user_id text NOT NULL, trace_id text NOT NULL,
    started_at timestamptz NOT NULL, state text NOT NULL, PRIMARY KEY(tenant_id,id),
    actor_role text NOT NULL DEFAULT 'support' CHECK(actor_role='support'),
    FOREIGN KEY(tenant_id,user_id,actor_role) REFERENCES memberships
);
CREATE TABLE agent_steps (
    tenant_id text, run_id text, seq integer CHECK(seq>0), tool text NOT NULL, result_summary jsonb NOT NULL,
    PRIMARY KEY(tenant_id,run_id,seq), FOREIGN KEY(tenant_id,run_id) REFERENCES agent_runs
);
CREATE TABLE audit_events (
    tenant_id text REFERENCES tenants, id text, occurred_at timestamptz NOT NULL, actor text NOT NULL,
    request_id text NOT NULL, action text NOT NULL, details jsonb NOT NULL, trace_id text NOT NULL,
    PRIMARY KEY(tenant_id,id), FOREIGN KEY(tenant_id,request_id) REFERENCES refund_requests
);
CREATE INDEX audit_request_time ON audit_events(tenant_id,request_id,occurred_at);
