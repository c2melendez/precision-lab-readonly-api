-- Apply explicitly to the dedicated PostgreSQL database before enabling ops dispatch.
-- Do not point this at the suspended Axis Suite database.
CREATE TABLE IF NOT EXISTS ops_idempotency (
    operation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_sha256 CHAR(64) NOT NULL UNIQUE,
    request_sha256 CHAR(64) NOT NULL,
    repo TEXT NOT NULL CHECK (repo IN ('lite', 'plus')),
    operation TEXT NOT NULL CHECK (operation IN ('gate', 'docs')),
    dry_run BOOLEAN NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('dispatching', 'accepted', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ops_idempotency_created_at_idx
    ON ops_idempotency (created_at);
