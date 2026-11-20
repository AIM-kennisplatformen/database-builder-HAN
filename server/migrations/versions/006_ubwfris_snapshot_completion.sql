-- Existing snapshots may already be valid imports; retain them as complete.
ALTER TABLE ubwfris_snapshots
    ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'
    CHECK (status IN ('running', 'complete'));

ALTER TABLE ubwfris_snapshots
    ALTER COLUMN status SET DEFAULT 'running';

CREATE TABLE ubwfris_ib_rows_staging (
    LIKE ubwfris_ib_rows
        INCLUDING DEFAULTS
        INCLUDING CONSTRAINTS
        INCLUDING INDEXES
);

ALTER TABLE ubwfris_ib_rows_staging
    ADD CONSTRAINT ubwfris_ib_rows_staging_snapshot_fk
    FOREIGN KEY (snapshot_id, row_type)
    REFERENCES ubwfris_snapshots (id, row_type);

CREATE TABLE ubwfris_rha_rows_staging (
    LIKE ubwfris_rha_rows
        INCLUDING DEFAULTS
        INCLUDING CONSTRAINTS
        INCLUDING INDEXES
);

ALTER TABLE ubwfris_rha_rows_staging
    ADD CONSTRAINT ubwfris_rha_rows_staging_snapshot_fk
    FOREIGN KEY (snapshot_id, row_type)
    REFERENCES ubwfris_snapshots (id, row_type);
