# Document ingestion plan

## Purpose

This plan describes the next document-ingestion capability for SharePoint files. One operation refreshes metadata for the selected drives, then ingests eligible documents from the refreshed catalog. Ingestion extracts document structure, produces chunks, embeds them, writes complete Qdrant generations, and stores content-derived metadata suggestions in PostgreSQL.

The plan builds on the implemented group-drive metadata flow and the storage and search decisions in [ADR-006](../../software-guidebook/adr/ADR-006-document-vector-storage.md) and [ADR-007](../../software-guidebook/adr/ADR-007-access-first-document-search.md). It records agreed behavior; it does not select specific processors, models, prompts, or numeric limits.

## Agreed flow

1. Validate the drive, path, extension, and ingestion-status selection before writing metadata.
2. Run the existing metadata refresh for every selected drive, even when published metadata already exists.
3. Use each drive's newly published metadata snapshot to select documents. A drive whose refresh fails has no documents ingested in this run; other successfully refreshed drives can continue.
4. Process eligible files independently. Only publish a new file result after all required processing stages succeed. A file with no usable chunks is a reported terminal outcome, not a processing failure.
5. Return a durable run ID, progress, per-file outcomes, source and processing versions, usage measurements, and per-drive outcome counts.

## Decisions at a glance

| Concern | Agreed behavior |
| --- | --- |
| Scope | Same group, drive, path, and extension selection as metadata ingestion; every selected drive is refreshed first. |
| Path rules | A more-specific path overrides only extension or status settings it explicitly names. Conflicting include and exclude settings at equal specificity fail validation when they overlap. |
| Default ingestion statuses | `new`, `deferred`, and `outdated`. `failed`, `unchanged`, and `no_usable_chunks` require explicit selection. |
| Metadata endpoint | Uses the same path and extension precedence; it has no ingestion-status filter. |
| Processing | Format processors produce structured content and locations. Separate configured stages extract metadata suggestions, form chunks, and embed chunks. |
| Metadata suggestions | Values are nullable and may be empty. Non-empty values need supporting source locations. Invalid individual suggestions are dropped; an uninterpretable model response is a failure. |
| Publication | Per-file atomic publication. Failed work leaves the last successful version intact. Suggestions are versioned with source and processing versions. |
| No usable chunks | Report `no_usable_chunks`; save valid metadata suggestions, create no new Qdrant generation, and leave any prior generation marked outdated. |
| Disconnect | Metadata refresh cancels on disconnect. During document processing, active files finish and no additional files start. The run can be inspected after disconnect. |
| Model change | Embedding model changes require an explicit corpus cutover. Search uses vectors from only one compatible embedding space at a time; incomplete coverage at cutover is allowed and reported. |
| Deletion | A delegated missing or deleted observation changes that operator's visibility only. Removing shared content requires a separate operator purge after deletion is verified. |

## Document map

- [Selection and API behavior](selection-and-api.md) defines request selectors, conflict validation, state tags, and run events.
- [Processing and storage](processing-and-storage.md) defines processor and model stages, metadata provenance, Qdrant and Postgres publication, and model cutover.
- [Delivery plan](delivery-plan.md) sequences implementation work and gives observable acceptance scenarios.

## Existing decisions and validation gate

PostgreSQL remains the source of truth for document identity, source metadata, processing state, content-derived metadata suggestions, and the active vector generation. Qdrant stores passage text and vectors with enough identity and generation data to join each point back to Postgres. A complete new Qdrant generation is written before Postgres switches the active reference; the previous generation remains active after a failed rebuild.

Search remains access-first under ADR-007. The project has not yet validated the exact Microsoft Search hit-to-`(drive_id, item_id)` mapping in the HAN tenant. The existing validator received HTTP 403 because the configured application lacks the required file-search permission. This is a release gate for document search, separate from the ingestion design.

## Deferred implementation choices

Choose concrete processors and supported-format tests, OCR/vision tooling, model names and prompts, chunk limits, retry counts, concurrency, file and run budgets, durable run persistence details, and API event field names during implementation. Configure service endpoints and model choices through environment configuration; do not accept arbitrary model endpoints in an ingestion request. Do not add numeric limits to this plan until they are measured and agreed.
