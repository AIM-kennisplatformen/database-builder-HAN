# API Parity: Error Handling

## Client contract

Employee sync, UBWFRIS ingest, and metadata ingest send terminal failures as an SSE `error` event. Its data has one envelope:

```json
{
  "status": "error",
  "error": {"code": "...", "http_status": 500, "detail": "..."},
  "context": {"operation": "..."},
  "progress": {}
}
```

`error.code` is stable and operation-specific for recognized failures; `internal_error` covers unexpected failures. `http_status` is part of the event data: after SSE response headers are sent, it does not change the HTTP response status. `context` identifies the operation and may include route identifiers. `progress` preserves route-specific partial results, such as employee counts or UBWFRIS row counts. The transport-neutral [api/errors.py](../../../server/src/api/errors.py) defines `PublicError`, `internal_error`, and shared logging helpers. The [employee sync](../../../server/src/api/routes/employees/sync.py) and [UBWFRIS ingest](../../../server/src/api/routes/ubwfris/snapshots/ingest.py) routes expose route-local mapper classes with static `describe` methods and call them directly. Metadata ingest and drive discovery share the documents-local [error mapper](../../../server/src/api/routes/documents/error_mapping.py), used by [metadata ingest](../../../server/src/api/routes/documents/metadata.py) and [drive discovery](../../../server/src/api/routes/documents/drives.py).

Known validation errors retain their validated domain detail. Upstream Graph and operational failures use fixed safe detail; unexpected failures use a generic detail. SSE payloads never contain tracebacks. Drive discovery returns the same error object in an HTTP body shaped as `{"error": {"code": "...", "http_status": 502, "detail": "..."}}`, with the HTTP status matching `error.http_status`, in [drive discovery](../../../server/src/api/routes/documents/drives.py).

## Error mapping

- Employee sync maps invalid limits to 422, no employees to 404, unavailable sources to 501, and unavailable stores to 503.
- UBWFRIS maps invalid workbooks to 422, duplicate uploads to 409, parser failures to 500, and unavailable stores to 503.
- Metadata maps an active run to 409, inaccessible groups to 404, invalid selections to 422, and unavailable sources or stores to 503. A `MetadataSourceError` preserves Graph 403 and 404, maps 429 to 503, and maps other source statuses to 502. Its public detail is fixed so the upstream message is not exposed.
- Unexpected failures use `internal_error` with status 500 and generic detail.

Metadata selection validation uses the specific `InvalidMetadataSelection` exception, which remains a `ValueError` for domain callers. Other `ValueError` instances, including malformed upstream responses, do not automatically become client validation errors.

## Logging and response timing

Unexpected failures are logged once with their traceback and operation identifiers. Known server failures are logged, including their cause traceback when available; expected 4xx failures are not traceback-logged. A stream that ends without a terminal outcome logs an error with operation context and emits the safe `internal_error` event without inventing a traceback. Clients must inspect the terminal SSE `error` event because the HTTP status is already set when streaming begins.

There are no existing clients, so this contract does not need to preserve the previous endpoint-specific error payloads.
