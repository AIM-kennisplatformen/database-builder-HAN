CREATE TABLE document_metadata_runs (
    id UUID PRIMARY KEY,
    principal_id TEXT NOT NULL,
    drive_id TEXT NOT NULL,
    selection TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed', 'interrupted')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX document_metadata_one_active_run
    ON document_metadata_runs (principal_id, drive_id) WHERE status = 'running';

CREATE TABLE document_metadata_cursors (
    principal_id TEXT NOT NULL,
    drive_id TEXT NOT NULL,
    selection TEXT NOT NULL,
    delta_link TEXT NOT NULL,
    published_run_id UUID NOT NULL REFERENCES document_metadata_runs(id),
    PRIMARY KEY (principal_id, drive_id)
);

CREATE TABLE document_metadata_staging (
    run_id UUID NOT NULL REFERENCES document_metadata_runs(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    visible BOOLEAN NOT NULL,
    name TEXT,
    mime_type TEXT,
    size_bytes BIGINT,
    content_version TEXT,
    source_url TEXT,
    sha1 TEXT,
    sha256 TEXT,
    quick_xor TEXT,
    document_metadata JSONB,
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE document_metadata_observations (
    principal_id TEXT NOT NULL,
    drive_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    selection TEXT NOT NULL,
    visible BOOLEAN NOT NULL,
    observed_run_id UUID NOT NULL REFERENCES document_metadata_runs(id),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, drive_id, item_id)
);

CREATE VIEW published_document_catalog AS
SELECT source.* FROM document_ms_graph_sources AS source
WHERE EXISTS (
    SELECT 1 FROM document_metadata_observations AS observation
    WHERE observation.drive_id = source.drive_id
      AND observation.item_id = source.item_id
      AND observation.visible
);
