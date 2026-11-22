ALTER TABLE document_metadata_staging ADD COLUMN relative_path TEXT;
ALTER TABLE document_metadata_observations ADD COLUMN relative_path TEXT;
ALTER TABLE document_metadata_observations ADD COLUMN name TEXT;
ALTER TABLE document_metadata_observations ADD COLUMN content_version TEXT;

UPDATE document_metadata_observations AS observation
SET name = source.name, content_version = source.content_version
FROM document_ms_graph_sources AS source
WHERE source.drive_id = observation.drive_id
  AND source.item_id = observation.item_id;

UPDATE document_metadata_cursors SET delta_link = '';

CREATE TABLE document_processing_state (
    drive_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    active_content_version TEXT,
    active_policy_version TEXT,
    last_attempt_content_version TEXT,
    last_attempt_policy_version TEXT,
    last_outcome TEXT CHECK (last_outcome IN ('succeeded', 'failed', 'no_usable_chunks')),
    deferred BOOLEAN NOT NULL DEFAULT FALSE,
    failure_stage TEXT,
    failure_detail TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (drive_id, item_id)
);

CREATE TABLE document_ingest_file_runs (
    operation_id UUID NOT NULL REFERENCES operation_runs(id) ON DELETE CASCADE,
    drive_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    source_content_version TEXT,
    processing_policy_version TEXT NOT NULL,
    statuses JSONB NOT NULL,
    selection_reasons JSONB NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('selected', 'deferred', 'failed', 'succeeded', 'no_usable_chunks')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (operation_id, drive_id, item_id)
);
CREATE INDEX document_ingest_file_runs_item
    ON document_ingest_file_runs (drive_id, item_id, created_at DESC);
