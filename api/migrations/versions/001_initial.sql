-- PostgreSQL 19 temporal keys need GiST equality support for text columns.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Application time is measured in instants. Close the previous range and
-- insert the next source version in one transaction. Keep an absent user as a
-- source row with is_present = false so existing overrides remain referenced.
CREATE TABLE IF NOT EXISTS user_ms_graph_sources (
    id TEXT NOT NULL,
    valid_at TSTZRANGE NOT NULL,
    is_present BOOLEAN NOT NULL DEFAULT true,
    display_name TEXT,
    given_name TEXT,
    surname TEXT,
    mail TEXT,
    company_name TEXT,
    department TEXT,
    job_title TEXT,
    skills TEXT[],
    responsibilities TEXT[],
    interests TEXT[],
    response_body JSONB,
    request_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, valid_at WITHOUT OVERLAPS),
    UNIQUE (mail, valid_at WITHOUT OVERLAPS),
    CONSTRAINT user_ms_graph_sources_presence_check CHECK (
        (is_present AND response_body IS NOT NULL
            AND jsonb_typeof(response_body) = 'object'
            AND response_body ? 'id'
            AND response_body ->> 'id' = id
            AND request_url IS NOT NULL)
        OR (NOT is_present AND response_body IS NULL
            AND request_url IS NULL
            AND display_name IS NULL AND given_name IS NULL
            AND surname IS NULL AND mail IS NULL AND company_name IS NULL
            AND department IS NULL AND job_title IS NULL
            AND skills IS NULL AND responsibilities IS NULL
            AND interests IS NULL)
    )
);

-- One replacement value per source field and date. JSON null intentionally
-- overrides a source value with SQL NULL; no row means use the source value.
-- The temporal FK allows an override period to span several source versions.
CREATE TABLE IF NOT EXISTS user_overrides (
    source_id TEXT NOT NULL,
    valid_at TSTZRANGE NOT NULL,
    field TEXT NOT NULL CHECK (field IN (
        'display_name', 'given_name', 'surname', 'mail', 'company_name',
        'department', 'job_title', 'skills', 'responsibilities', 'interests'
    )),
    value JSONB NOT NULL,
    performed_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, field, valid_at WITHOUT OVERLAPS),
    FOREIGN KEY (source_id, PERIOD valid_at)
        REFERENCES user_ms_graph_sources (id, PERIOD valid_at)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        (field IN ('skills', 'responsibilities', 'interests')
            AND jsonb_typeof(value) IN ('array', 'null'))
        OR (field NOT IN ('skills', 'responsibilities', 'interests')
            AND jsonb_typeof(value) IN ('string', 'null'))
    )
);

-- A view has no timestamp parameter, so the function provides the as-of query.
-- JSONB null remains distinct from a missing override for COALESCE, then
-- jsonb_to_record converts the selected values back to their source types.
CREATE OR REPLACE FUNCTION user_ms_graph_at(p_as_of TIMESTAMPTZ)
RETURNS TABLE (
    id TEXT,
    source_valid_at TSTZRANGE,
    display_name TEXT,
    given_name TEXT,
    surname TEXT,
    mail TEXT,
    company_name TEXT,
    department TEXT,
    job_title TEXT,
    skills TEXT[],
    responsibilities TEXT[],
    interests TEXT[]
)
LANGUAGE sql STABLE
AS $$
    SELECT
        s.id,
        s.valid_at,
        merged.display_name,
        merged.given_name,
        merged.surname,
        merged.mail,
        merged.company_name,
        merged.department,
        merged.job_title,
        merged.skills,
        merged.responsibilities,
        merged.interests
    FROM user_ms_graph_sources AS s
    LEFT JOIN LATERAL (
        SELECT jsonb_object_agg(o.field, o.value) AS values_by_field
        FROM user_overrides AS o
        WHERE o.source_id = s.id AND o.valid_at @> p_as_of
    ) AS overrides ON true
    CROSS JOIN LATERAL jsonb_to_record(jsonb_build_object(
        'display_name', COALESCE(overrides.values_by_field -> 'display_name', to_jsonb(s.display_name)),
        'given_name', COALESCE(overrides.values_by_field -> 'given_name', to_jsonb(s.given_name)),
        'surname', COALESCE(overrides.values_by_field -> 'surname', to_jsonb(s.surname)),
        'mail', COALESCE(overrides.values_by_field -> 'mail', to_jsonb(s.mail)),
        'company_name', COALESCE(overrides.values_by_field -> 'company_name', to_jsonb(s.company_name)),
        'department', COALESCE(overrides.values_by_field -> 'department', to_jsonb(s.department)),
        'job_title', COALESCE(overrides.values_by_field -> 'job_title', to_jsonb(s.job_title)),
        'skills', COALESCE(overrides.values_by_field -> 'skills', to_jsonb(s.skills)),
        'responsibilities', COALESCE(overrides.values_by_field -> 'responsibilities', to_jsonb(s.responsibilities)),
        'interests', COALESCE(overrides.values_by_field -> 'interests', to_jsonb(s.interests))
    )) AS merged (
        display_name TEXT,
        given_name TEXT,
        surname TEXT,
        mail TEXT,
        company_name TEXT,
        department TEXT,
        job_title TEXT,
        skills TEXT[],
        responsibilities TEXT[],
        interests TEXT[]
    )
    WHERE s.valid_at @> p_as_of AND s.is_present;
$$;

CREATE OR REPLACE VIEW user_ms_graph_current AS
SELECT * FROM user_ms_graph_at(CURRENT_TIMESTAMP);
