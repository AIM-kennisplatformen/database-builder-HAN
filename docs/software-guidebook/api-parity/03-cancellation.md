# API Parity: Cancellation

## Policy

A client disconnect cancels the request and stops progress delivery and ingestion. A cancelled or failed request must not leave a partial published result. Inaccessible drives in a broad metadata selection are the explicit exception: the request skips them, continues with accessible drives, and reports the skipped drive IDs so a later request can retry. An explicitly included drive that is inaccessible fails the request and rolls back all drives in that request.

The database commit is the completion boundary. Employee sync and group metadata publish staged data in one transaction after a cancellation checkpoint. UBWFRIS keeps the staged-row move and completion update cancellable, checks for pending cancellation before transaction exit, and shields commit or rollback and connection release. If cancellation arrives before a completion boundary, the transaction rolls back and staged run data is deleted. If disconnect races with a protected commit, it may complete; it completes the operation atomically rather than publishing a partial result. Future operations that allow partial completion need an explicit policy, such as the planned vectorization exception.

## Current implementation

All three SSE route generators close their use-case generators in shielded finally blocks. The employee, metadata, and UBWFRIS workflows also shield cleanup at the resource owner. UBWFRIS shields snapshot discard in the use case, and its PostgreSQL writers protect COPY, transaction, and pool teardown while leaving row writes cancellable.

Employee sync stages each employee under a run ID. On success, one transaction publishes the staged employees and marks the run complete. Cancellation or failure discards the staging run. Startup recovery removes staging from runs left in the running state after a process crash; it assumes the current single-instance deployment.

Metadata stages each drive separately, then publishes all prepared drives in one PostgreSQL transaction. A broad selection skips a drive that returns 403 and continues; the final result includes skipped drive IDs. An explicit include that is already known to be inaccessible fails during selection validation. A later 403 during an explicitly included drive fails the group request and clears staging from the earlier prepared drives. Startup recovery clears staging for runs orphaned by a crash or failed cleanup.

UBWFRIS uploads copy rows into separate IB/RHA staging tables. A transaction moves the staged rows into the published table and marks the snapshot complete together. Cancellation discards staging; startup recovery removes staging and snapshot records still marked running. The migration marks pre-existing snapshots complete because their state cannot be inferred reliably, preserving legacy published rows but also any legacy incomplete snapshot.

Employee publication, metadata group publication, and UBWFRIS snapshot completion keep normal row and SQL work cancellable, check for pending cancellation before commit, then shield transaction and connection teardown. A rollback and connection release therefore finish despite repeated cancellation. Startup recovery for metadata, employee, and UBWFRIS runs also clears leftover staging after cleanup failures.

ASGI tests exercise a client disconnect through all three routes and verify that no ordinary SSE error event is delivered after disconnect. The metadata and UBWFRIS route tests assert staged cleanup; the employee route test asserts no committed result. Separate use-case cancellation tests verify employee staging cleanup and source closure, metadata group staging cleanup and rollback after a later included-drive failure, and shielded UBWFRIS snapshot discard.

See [employee route](../../../server/src/api/routes/employees/sync.py), [employee workflow](../../../server/src/domain/use_cases/employee_sync.py), [employee writer](../../../server/src/infrastructure/postgres/employee_writer.py), and [startup recovery](../../../server/src/main.py); [UBWFRIS route](../../../server/src/api/routes/ubwfris/snapshots/ingest.py), [UBWFRIS workflow](../../../server/src/domain/use_cases/ubwfris_ingest.py), [snapshot writers](../../../server/src/infrastructure/postgres/ubwfris_writer.py), and [completion migration](../../../server/migrations/versions/006_ubwfris_snapshot_completion.sql); and [metadata route](../../../server/src/api/routes/documents/metadata.py), [group workflow](../../../server/src/domain/use_cases/metadata_ingest/group.py), [drive workflow](../../../server/src/domain/use_cases/metadata_ingest/drive.py), and [document store](../../../server/src/infrastructure/postgres/document_store.py).

## Remaining verification

- Cover cleanup failure and startup recovery while preserving completed employee, metadata, and UBWFRIS data.
- Verify that cancellation at the commit boundary either rolls back all staged work or completes the full operation.
- Legacy UBWFRIS snapshots are treated as complete because their pre-migration state is unknowable; their old completion status cannot be reconstructed.
