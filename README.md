# database-builder-HAN

Database builder Application for the HAN app

The [Software Guidebook](docs/software-guidebook/00-index.md) explains the system's goals and provides a chapter-by-chapter guide for developers.

## Streamed progress

Set `STREAM_PROGRESS_INTERVAL_SECONDS` in the server environment to control the minimum time between progress SSE events for employee sync and UBWFRIS snapshot uploads. The default is `0.25` seconds; set it to `0` to send every progress event. The first progress event and the final complete or error event are sent immediately. This setting does not slow ingestion or sync processing.

## Delete endpoints

Delete endpoints are disabled by default and hidden from OpenAPI unless enabled. Set the matching server environment variable to `true` and restart the server:

- `DELETE_ENDPOINTS__EMPLOYEE=true` enables `DELETE /api/v1/employees/sync`.
- `DELETE_ENDPOINTS__METADATA=true` enables `DELETE /api/v1/documents/metadata`.
- `DELETE_ENDPOINTS__UBWFRIS=true` enables both UBWFRIS snapshot delete endpoints.

Disabled endpoints remain mounted but are hidden from OpenAPI and return 404. Enabled endpoints also require an authenticated Microsoft Graph user.

These endpoints clear global stored data for their use case and remove the matching operation history:

- `DELETE /api/v1/employees/sync` clears employee source rows and sync history. It returns 409 while a sync is running or whenever user overrides exist; overrides are preserved.
- `DELETE /api/v1/documents/metadata` clears all stored document metadata, observations, cursors, and metadata ingestion history. This affects every user's metadata inventory.
- `DELETE /api/v1/ubwfris/snapshots/ib` clears all IB snapshots and import history.
- `DELETE /api/v1/ubwfris/snapshots/rha` clears all RHA snapshots and import history.

The snapshot and metadata endpoints return 409 while an ingestion for the affected data is running.
