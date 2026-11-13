CREATE TABLE ubwfris_snapshots (
    id TEXT PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
    row_type TEXT NOT NULL CHECK (row_type IN ('ib', 'rha')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, row_type)
);

CREATE TABLE ubwfris_ib_rows (
    snapshot_id TEXT NOT NULL,
    row_type TEXT NOT NULL DEFAULT 'ib' CHECK (row_type = 'ib'),
    row_number INTEGER NOT NULL CHECK (row_number > 0),
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    budgeted_hours NUMERIC NOT NULL,
    projected_hours NUMERIC NOT NULL,
    booked_hours NUMERIC NOT NULL,
    period INTEGER NOT NULL,
    remaining_hours NUMERIC NOT NULL,
    cost_center TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, row_number),
    FOREIGN KEY (snapshot_id, row_type)
        REFERENCES ubwfris_snapshots (id, row_type)
);

CREATE TABLE ubwfris_rha_rows (
    snapshot_id TEXT NOT NULL,
    row_type TEXT NOT NULL DEFAULT 'rha' CHECK (row_type = 'rha'),
    row_number INTEGER NOT NULL CHECK (row_number > 0),
    employee_number TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    project_manager TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_status TEXT NOT NULL,
    project_name TEXT NOT NULL,
    subproject_id TEXT NOT NULL,
    subproject_status TEXT NOT NULL,
    subproject_name TEXT NOT NULL,
    cost_center TEXT NOT NULL,
    registration_date DATE NOT NULL,
    hours NUMERIC NOT NULL,
    project_type TEXT NOT NULL,
    parttime_percentage INTEGER,
    main_department TEXT NOT NULL,
    academy TEXT NOT NULL,
    main_department_name TEXT NOT NULL,
    employment_cost_center TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, row_number),
    FOREIGN KEY (snapshot_id, row_type)
        REFERENCES ubwfris_snapshots (id, row_type)
);
