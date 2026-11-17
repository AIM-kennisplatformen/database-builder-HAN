CREATE TABLE document_ms_graph_sources (
    drive_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT,
    size_bytes BIGINT CHECK (size_bytes >= 0),
    content_version TEXT,
    source_url TEXT,
    sha1 TEXT,
    sha256 TEXT,
    quick_xor TEXT,
    document_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (drive_id, item_id)
);
