CREATE TABLE employee_sync_runs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'complete', 'failed', 'interrupted')
    ),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE employee_sync_staging (
    run_id UUID NOT NULL REFERENCES employee_sync_runs(id) ON DELETE CASCADE,
    employee_id TEXT NOT NULL,
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
    PRIMARY KEY (run_id, employee_id)
);
