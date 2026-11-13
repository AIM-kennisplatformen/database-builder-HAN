# database-builder-HAN

Database builder Application for the HAN app

The [Software Guidebook](docs/software-guidebook/00-index.md) explains the system's goals and provides a chapter-by-chapter guide for developers.

## Streamed progress

Set `STREAM_PROGRESS_INTERVAL_SECONDS` in the server environment to control the minimum time between progress SSE events for employee sync and UBWFRIS snapshot uploads. The default is `0.25` seconds; set it to `0` to send every progress event. The first progress event and the final complete or error event are sent immediately. This setting does not slow ingestion or sync processing.
