# SharePoint document integration design

## Status and purpose

This records the design decisions agreed for a local demonstration of document discovery.
Document discovery is separate from the [expertise and project journeys](../software-guidebook/02-functional-overview.md).
Its purpose is to consolidate access to information that the requesting user can already access in Microsoft Graph.
This document describes the intended scaffolding and behavior; document extractors, embedding models, chunking, and supported formats have not been selected.
The [Graph file retrieval guide](ms-graph-files.md) describes the source endpoints and their order.

The design extends [ADR-004](../software-guidebook/adr/ADR-004-data-storage.md): PostgreSQL stores document metadata and processing state, Qdrant stores document vectors and passages, and TypeDB remains the knowledge graph for the existing discovery journeys.
[ADR-006](../software-guidebook/adr/ADR-006-document-vector-storage.md) records the storage choice.
[ADR-007](../software-guidebook/adr/ADR-007-access-first-document-search.md) records the access-first search trade-off.

## Terms and identities

| Term                  | Meaning in this design                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------------------------ |
| Document              | A Graph drive item with a `file` facet that belongs to an approved, inventoried drive.                 |
| Document identity     | The pair `(drive_id, item_id)`, which remains stable across a rename or move within one drive.         |
| Metadata run          | One operator's inventory or delta refresh of a drive for selected file extensions.                     |
| Published catalog     | Metadata from completed runs that search may use.                                                      |
| Vector generation     | A complete set of Qdrant points for one document content version and processing-policy version.        |
| Accessible candidates | Graph Search hits for one user's question that can be joined exactly to published document identities. |

The Graph item address, drive and item IDs, SharePoint `webUrl`, name, parent, type, size, source version, and retrieval time belong in PostgreSQL.
The URL and path are mutable display and navigation data; the drive and item IDs identify a file within a drive.
A move or copy to a different drive is treated as a separate document identity and access path.
Identical content may reuse embedding computation, but each file retains separate metadata, provenance, and Qdrant points.

## Application boundaries

The management API and ingestion code remain in one application, while MCP is separately wired under `server/src/mcp/`, consistent with [ADR-002](../software-guidebook/adr/ADR-002-application-structure.md).
Both use shared domain use cases and policies, with their own infrastructure adapters and no API-to-MCP network call.
Domain policies decide access eligibility and vectorization priority from supplied facts.
Adapters make Microsoft Graph, PostgreSQL, Qdrant, credential, and signing calls.
An access-control use case returns accessible candidate document IDs and a `partial` indicator; API and MCP search use cases take that result as an argument and produce different responses.

An eventual operation that runs metadata followed by vectors may compose the two use cases, but that operation is outside the current design.

## Metadata ingestion

`POST /metadata` accepts an approved Graph drive ID and a list of normalized file extensions.
It does not accept a path filter; it inventories the whole drive for those selected types.
A changed selection does not erase records for types omitted from a later run.
Selecting a new type requires a full baseline before delta updates can cover that type.
The initiating operator's delegated Graph token defines what this run can see, and the operator identity is recorded with its observations and delta cursor.
The current local setup requires the operator to sign in interactively.
The drive allowlist is an enforced domain policy; a caller-supplied drive ID does not grant approval.

The first run walks the [drive delta feed](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0) to completion, follows every `@odata.nextLink`, and saves its `@odata.deltaLink` only after a complete successful scan.
Later runs use that cursor; an expired cursor starts a fresh baseline.
Delta links are tied to the drive and delegated principal and are never reused for another principal.
Repeated delta entries are handled idempotently.
Records are staged under the run ID and become part of the published catalog only when the run and its cursor complete.
On failure or cancellation, the previously published catalog stays available.

There is no whole-run metadata deadline.
Individual Graph requests have timeouts and bounded retry behavior.
A job may still be cancelled after the caller-disconnect grace period or interrupted by process failure.
Only one metadata run for a drive and principal may mutate that principal's cursor at a time.

PostgreSQL has one document record per `(drive_id, item_id)` and separate per-principal observations.
Under delegated access, a delta `deleted` marker or missing item can mean lost visibility rather than global deletion.
Such an observation marks the item no longer visible to that operator; it does not automatically erase the shared document record or its vectors.
A separate operator purge use case is required; its behavior is not designed yet.

### Creation and latest update

For each file, retain two audit entries from the [drive item](https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0): `createdDateTime` with `createdBy`, and `lastModifiedDateTime` with `lastModifiedBy`. A missing identity leaves the actor unknown without losing the timestamp. These entries describe SharePoint activity, not authorship of the file's content. Metadata ingestion does not retrieve file versions or maintain a full author or update history.
In a read-only delegated Graph sample of 30 inventoried files on 17 November 2026, selected without a file-type filter, `createdBy` was available for all 30.
Previously ingested rows retain their old JSON metadata until the drive is inventoried again; an inventory run replaces it with the two audit entries.

## Vectorization

`POST /vectors` takes a completed metadata run ID and selects eligible documents from the published catalog.
Retrieving metadata is a separate, higher-priority step; vectorization never starts from an incomplete inventory.
The use case has no fixed list of document formats or extraction method yet.
An extractor port will accommodate additional formats, and a file with no usable text can be recorded as deferred or skipped with a reason.

The priority policy lives in `domain/policies` and is configurable without changing the vectorization use case.
It can rank by file type, size, measured extractable-text yield, waiting age, and a per-type total time budget.
The initial heuristic favors Word documents over PDFs and PowerPoint files; it is adjustable because PDFs may contain exported slides and presentations may rely on images or external context.
Per-file timeouts and per-type time budgets are distinct policy settings.
Unused type budget may be reassigned, and waiting age prevents permanent starvation.
The policy version and each skip or deferral reason are recorded.

A changed file `cTag` or changed processing-policy version makes its vectors eligible for rebuild.
Microsoft [describes `cTag` as a content tag](https://learn.microsoft.com/en-us/graph/api/resources/driveitem?view=graph-rest-1.0), while `eTag` also reflects metadata changes.
If `cTag` is unavailable, the source adapter must establish whether content changed before reusing vectors; metadata changes alone do not prove a content change.
The per-file timeout covers Graph download, extraction, embedding, and Qdrant write.
The vector-job deadline stops the job from starting new files; already started files may finish within their own timeouts.
On reaching the start cutoff, the job reports a `deadline_reached` error with completed and deferred counts, and a later run resumes eligible work.
Only one rebuild per document may be active at a time.

Each Qdrant point stores a passage, its vector, `(drive_id, item_id)`, PostgreSQL metadata ID, vector generation, and chunk identity.
The file's active vector generation is recorded in PostgreSQL.
A complete new generation is written before PostgreSQL marks it active; old points are removed afterward.
A failed or timed-out rebuild retains the previous active generation and reports its age.
Search uses only active generations.
Qdrant is not directly exposed to Studio or API callers.

## Access-first search

Every search starts with a fresh [Microsoft Search `driveItem` query](https://learn.microsoft.com/en-us/graph/api/resources/search-api-overview?view=graph-rest-1.0) using the requesting user's delegated Graph token.
Its permission-trimmed hits become candidate files for that one request.
The access use case accepts the user question and optional drive scope, joins hits exactly to published `(drive_id, item_id)` identities, and returns candidate IDs plus whether Graph paging, deadline, or candidate limits made the result partial.
It never caches accessible IDs across searches.
It returns an explicit access-check error if Graph or user-bound authentication is unavailable, rather than a successful empty result.
Unmatched Graph hits are omitted; titles, paths, and Graph Search `hitId` are not assumed to be stable file identities.

This access-first order is a business rule: Qdrant and PostgreSQL search are restricted to the Graph candidate set rather than used to discover files before access checking.
The API returns published file metadata and vectorization status for matching candidates.
The MCP tool takes a user question, runs Qdrant similarity search within those candidates, and returns relevant passages, metadata, provenance, and SharePoint links only for active vector generations.
Files without vectors may appear in API metadata results but cannot supply an MCP passage.
Both surfaces search all catalogued approved drives by default, with an optional drive filter.
Graph failures are explicit errors; completed searches with no matches return an empty result.

Graph Search is indexed rather than a complete inventory of accessible documents.
[SharePoint search indexing can omit accessible files](https://learn.microsoft.com/en-us/troubleshoot/sharepoint/search/search-results-missing).
Accessible files absent from its index or query results will be missed by this version.
The candidate count and search deadline are configurable; a bounded result reports `partial` rather than implying complete coverage.
Before implementation, a tenant-level test must prove that Graph Search hits provide an exact, reliable join to `(drive_id, item_id)`.
If that cannot be proved, the access-first search design must be revisited.

### Permission observations for future application ingestion

A future application-token ingestion path may retrieve [sharing permissions for each drive item](https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0) and retain them in PostgreSQL for access governance.
Store these as dated observations keyed by `(drive_id, item_id)` with the collector identity and token mode, permission ID, roles, grantee identifiers, grant or link type and scope, expiration, and a marker for incomplete retrieval.
Do not persist sharing URLs or `shareId` tokens.
Refresh observations when items or grants change, and retain enough history to explain a governance finding.

The permission endpoint returns sharing grants, not a complete user-by-file authorization table.
Under delegated access, a non-owner sees only grants that apply to that caller; an application token may expose broader grants, but the target tenant must be checked before claiming complete coverage.
SharePoint and OneDrive for Business do not return `inheritedFrom` on permission resources, so a permission observation alone does not explain its inheritance path.
Group membership, link conditions, expiration, and later revocation also affect whether a person can use a grant.
Stored observations support governance analysis; the access-first search decision continues to use the requesting user's live delegated Graph results for authorization.
Validate this path by sampling item permission responses under delegated access, then repeat the same sample with an application token if one is provisioned.
Compare grant counts and facets without assuming that either response is a complete effective-access list.

The same delegated Graph sample returned a permission response for all 30 files: 92 permission entries in total, with one link grant and grantee data on all 92 entries. No entry included `inheritedFrom`. These are caller-visible sharing grants; application-token coverage remains untested because no application credential was used.

## Local identity and token handling

For now, each local application uses its own interactively acquired delegated Graph credential and permits one user per instance.
The API authentication flow derives the operator's Graph ID through `GET /me`; file-retrieval middleware supplies that verified ID to the use case without requiring the project JWT.
MCP's authentication flow compares the signed project JWT's `user_id` with `GET /me` under MCP's current delegated Graph token.
Its retrieval middleware enforces that binding without treating a caller-supplied user ID as authority.
A mismatch or missing Graph credential is an authentication error.
The JWT contains only the user ID and minimal validation claims (`iss`, `aud`, `iat`, and `exp`), with an initially configurable 30-day lifetime; it never carries a Graph token.
Delegated Graph access and refresh tokens are not stored in PostgreSQL or sent between the applications.

Both MCP's local flow and an API token endpoint may invoke the shared token-issuance use case.
The use case derives identity from Graph `/me` and defines claims; infrastructure signs the token.
The API endpoint is intended only for Studio, which must authenticate the request with a separately configured signing key pair.
The JWT signing key pair is separate from Studio's request-authentication key pair, and Studio's private key remains outside browser code.
CORS restricts browser origins but does not authenticate Studio by itself.
`GET /me` belongs to authentication, while file discovery addresses the authenticated user through `GET /users/{user-id}/memberOf`.

A future application credential may be added behind ingestion ports only if the workflow still respects the intended user-visible drive scope.
An app-only token by itself does not establish an individual user's access and cannot replace the user-bound search source.

## Jobs, SSE, and failure recovery

The metadata and vector endpoints create durable jobs, stream `progress`, `complete`, and `error` SSE events, and include the job ID in the initial response.
`GET /jobs/{job-id}/events` lets a caller reconnect to the same event stream and recover its position.
The route follows the typed event and transport-mapping style of the existing [UBW FRIS ingestion route](../../server/src/api/routes/ubwfris/snapshots/ingest.py), while a separate runner owns the job lifetime.
If no caller remains connected for two minutes, the runner requests cancellation at a safe checkpoint.
Timeout and cancellation are `error` events with distinct machine-readable codes, job ID, and last progress counts.
Job outcomes persist in PostgreSQL.
If the local process restarts mid-run, the run becomes `interrupted` and an operator must sign in and restart it; published data remains usable.

## Studio data destinations

Studio may store document-derived conversation text in its local chat history and send passages to its configured model endpoint for the approved drives.
It should continue logging token usage, tool names and usage, and the actual LLM response to Langfuse while masking raw document-tool output and repeated passages in generation inputs and agent or chain spans.
[Langfuse span masking](https://langfuse.com/docs/observability/features/masking) is the proposed mechanism; the [Studio MCP callback](https://github.com/AIM-kennisplatformen/studio/blob/e26579b/backend/src/utility/chat_util.py#L75-L87) currently logs tool activity.
A test trace with a known passage must confirm the mask before relying on it.
An LLM response may quote a passage, and logging that response is an accepted trade-off.

## Deferred decisions and validation gates

- Validate the exact Graph Search hit-to-drive-item mapping in the HAN tenant before implementing the access-first join.
- Select extractors, supported document types, OCR behavior, chunking, embedding model, and numeric time or size limits later.
- Define the combined metadata-then-vectors operation later; it should compose the two existing use cases.
- Design the operator purge use case later; delegated delta observations alone cannot trigger global deletion.
- Confirm Studio's selective Langfuse masking with an exported trace while preserving token usage and LLM response logging.
- Choose whether an overlapping run is queued or rejected when implementing concurrency control.
- Update the relevant Software Guidebook feature, interface, and data chapters when this separate capability moves from design into implementation.
