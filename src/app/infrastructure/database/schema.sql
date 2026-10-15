CREATE TABLE IF NOT EXISTS operations (
    id uuid PRIMARY KEY,
    cluster_key text NOT NULL,
    correlation_id text NOT NULL,
    source_node text NOT NULL,
    source_vmid integer NOT NULL,
    target_mode text NOT NULL CHECK (target_mode IN ('AUTO', 'FIXED')),
    requested_target text,
    selected_target text NOT NULL,
    newid integer NOT NULL,
    status text NOT NULL CHECK (status IN ('RESERVED','SUBMITTING','SUBMITTED','SUCCEEDED','FAILED','UNKNOWN')),
    upid text,
    task_node text,
    upstream_status integer,
    failure_origin text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resource_reservations (
    operation_id uuid PRIMARY KEY REFERENCES operations(id),
    cluster_key text NOT NULL,
    newid integer NOT NULL,
    target_node text NOT NULL,
    storage_id text NOT NULL,
    vcpu integer NOT NULL CHECK (vcpu > 0),
    memory_bytes bigint NOT NULL CHECK (memory_bytes > 0),
    storage_bytes bigint NOT NULL CHECK (storage_bytes >= 0),
    status text NOT NULL CHECK (status IN ('HELD','CONSUMED','RELEASED')),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS held_vmid_unique
    ON resource_reservations(cluster_key, newid) WHERE status = 'HELD';

CREATE TABLE IF NOT EXISTS round_robin_cursors (
    cluster_key text PRIMARY KEY,
    last_selected_node text NOT NULL
);
CREATE INDEX IF NOT EXISTS submitted_operations
    ON operations(cluster_key, created_at) WHERE status = 'SUBMITTED';
