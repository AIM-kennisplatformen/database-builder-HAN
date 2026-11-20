CREATE TABLE operation_runs (
    id UUID PRIMARY KEY,
    principal_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('employee.sync', 'metadata.ingest')),
    status TEXT NOT NULL CHECK (
        status IN ('running', 'complete', 'failed', 'interrupted')
    ),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    failure_code TEXT,
    domain_ids JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX operation_runs_principal_started
    ON operation_runs (principal_id, started_at DESC);

INSERT INTO operation_runs (id, principal_id, kind, status, started_at, finished_at)
SELECT id, '', 'employee.sync', status, started_at, finished_at
FROM employee_sync_runs;

ALTER TABLE document_metadata_runs
    ADD COLUMN operation_id UUID REFERENCES operation_runs(id);
CREATE INDEX document_metadata_runs_operation_id
    ON document_metadata_runs (operation_id) WHERE operation_id IS NOT NULL;
