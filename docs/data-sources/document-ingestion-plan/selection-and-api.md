# Selection and API behavior

## Combined operation

The operation accepts the same group and drive selection used by `POST /groups/{group_id}/metadata`. It also accepts ingestion statuses per include or exclude selector. Its sequence is:

1. Validate the full selection before metadata writes begin.
2. Refresh metadata for selected drives and paths, regardless of whether the catalog already has records.
3. After each drive's refresh completes and publishes, select eligible files from that refreshed snapshot using ingestion statuses.
4. Process those files under one durable run ID.

Metadata refresh is the prerequisite and freshness boundary. If a drive's refresh fails, that drive's ingestion does not start. The run reports the failure and can proceed with other drives whose refresh succeeded. A path-scoped refresh uses the same Graph ancestry and selection guarantees as the existing metadata flow.

## Selector shape

The exact wire representation will follow the existing API naming and validation conventions. Conceptually, each drive selection has include and exclude entries with a path, extensions, and (for ingestion only) statuses:

```json
{
  "drive_id": "drive-id",
  "include": [
    {"path": "/A", "extensions": [".pdf", ".md"]}
  ],
  "exclude": [
    {"path": "/A", "extensions": [".xlsx"]}
  ]
}
```

An ingestion selector may also have a `statuses` array whose strings are parsed as enum values. An omitted `extensions` list means all extensions for that directive. An omitted include `statuses` list selects `new`, `deferred`, and `outdated`. An omitted exclude `statuses` list matches all statuses. The metadata endpoint does not accept `statuses`.

An empty top-level include continues to mean all discovered drives. Exclude selectors narrow the matching set. Drive discovery and current delegated Graph access continue to determine which drives are eligible; a caller-supplied ID does not grant access.

## Path and extension precedence

Resolve each file against its matching path, extension, and status selectors. Statuses are independent tags, so a file may be both `outdated` and `failed`, or both `new` and `deferred`. A status filter matches when the file has any selected tag. A more-specific path changes only extension and status decisions it explicitly names; unrelated decisions inherit from the parent. The metadata endpoint applies the same path and extension rule without a status dimension.

For example:

| Path | Include extensions | Exclude extensions |
| --- | --- | --- |
| `/A` | `.pdf`, `.md` | `.xlsx` |
| `/A/B` | `.docx` | `.pdf` |

Under `/A/B`, `.md` remains included from `/A`, `.docx` is included by the child rule, and `.pdf` and `.xlsx` are excluded. Under other parts of `/A`, the parent decisions still apply.

Validation fails when equal-specificity include and exclude directives can select the same actual file. For ingestion, overlap is determined by extension filters and possible status-tag combinations, including coexisting tags. For example, an include for `outdated` conflicts with an exclude for `failed` when a file can have both tags. An omitted extension filter is a wildcard; an omitted include-status filter means `new`, `deferred`, and `outdated`, while an omitted exclude-status filter is a wildcard. Disjoint selections at the same path are allowed. Metadata applies the same path and extension overlap rule without status filters.

Normalize paths by treating `/` and `\\` as separators, trimming leading and trailing separators, and case-folding each path before matching. An omitted or empty path list means the drive root. Normalize extensions by trimming whitespace, removing leading dots, and lowercasing; an empty extension list is a wildcard. A filename with no dot has an empty extension and matches only wildcard rules. A leading dot counts as an extension suffix (for example, `.env` has extension `env`). Cover these rules with boundary tests.

## Ingestion statuses

Statuses describe independent facts about a file and may coexist where appropriate:

| Status | Meaning |
| --- | --- |
| `new` | No processing outcome exists yet. The file remains `new` while selected, queued, or in progress; it stops being `new` when processing has an outcome. |
| `deferred` | The file was not started because of priority, concurrency, cost, or time limits. Deferral is not a processing-attempt outcome; a file with no prior processing outcome remains `new` while deferred. |
| `outdated` | The current source content version or relevant processing policy differs from the active successful result. If a first attempt is discarded because the source changed during processing, the file is also outdated even though no successful result exists. A prior generation may remain usable until replaced, subject to embedding-model cutover rules. |
| `failed` | A real processing step failed. A previous successful result, if any, remains active. This status is opt-in by default. |
| `unchanged` | A successful result matches both current source content and processing policy. This status is opt-in by default. |
| `no_usable_chunks` | Processing completed without usable chunks. Valid metadata suggestions may still be stored. The outcome is opt-in by default. |

The default include status set is `new`, `deferred`, and `outdated`. `failed`, `unchanged`, and `no_usable_chunks` require explicit selection. Reattempting `no_usable_chunks` requires an explicit selection or a processing-policy change that makes it outdated.

## Run lifecycle and observable outcomes

Runs are durable and expose a stable run ID. The caller can inspect current progress and final per-drive and per-file outcomes after disconnect. Final records include selection reason, outcome, source `cTag` (or equivalent established content version), processor/model/prompt/policy versions, model usage measurements, and counts by outcome. Usage values are measurements, not billing claims.

Disconnect during metadata refresh cancels that refresh and prevents downstream document processing from starting with an incomplete catalog. Disconnect during document processing allows already-started files to finish and starts no additional files. The run remains inspectable. If another run is waiting on the same file, it shares the one active rebuild and records the shared result. Only one rebuild per document may run at once.

Transient download, processor, model, and Qdrant failures receive bounded retries within configured file/run budgets. A real failure is recorded as `failed`; files not started within a run's limit are `deferred`. A source content version change during processing discards that unpublished attempt and marks the file outdated for a later run.

The detailed event names and reconnect endpoint shape are implementation choices, but the API must make these distinctions observable: refresh failed, processing failed at a named stage, deferred, completed with chunks, completed with no usable chunks, and run ended with incomplete coverage.
