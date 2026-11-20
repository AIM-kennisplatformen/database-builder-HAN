ALTER TABLE operation_runs
    DROP CONSTRAINT operation_runs_kind_check;
ALTER TABLE operation_runs
    ADD CONSTRAINT operation_runs_kind_check CHECK (
        kind IN (
            'employee.sync',
            'metadata.ingest',
            'ubwfris.ib',
            'ubwfris.rha'
        )
    );
