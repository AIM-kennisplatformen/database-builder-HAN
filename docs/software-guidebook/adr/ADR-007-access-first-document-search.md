# ADR-007: Access-First Document Search

## Status

Accepted, subject to the Graph Search identity-mapping validation gate

## Date

2026-11-13

## Decision Drivers

- A document may be returned only when the requesting user can access it in Microsoft Graph.
- The business rule requires access checking before searching retained metadata or vectors.
- Per-file Graph checks for every query would create too many requests.
- Current access is delegated; no application identity is available.

## Context

The API will return file metadata, and the MCP tool will return relevant passages with metadata and provenance.
Both must use a shared access-control use case while keeping separate result implementations.
Microsoft Search with delegated access [permission-trims file results](https://learn.microsoft.com/en-us/graph/api/resources/search-api-overview?view=graph-rest-1.0), but it is an indexed search rather than a complete inventory of accessible files.
The [integration design](../../data-sources/sharepoint-document-integration.md) records the local identity flow and request sequence.

## Options Considered

**Option 1: Search Qdrant first, then verify each result in Graph**

- Pros: Qdrant considers all indexed semantic matches before access filtering.
- Cons: It violates the access-first business rule and can require many per-file Graph requests to fill a result set.

**Option 2: Enumerate every file accessible to the user before each query**

- Pros: It could provide a broad candidate set without relying on query-specific Graph Search matches.
- Cons: No documented delegated Graph endpoint guarantees a complete accessible-file inventory, and a drive traversal can be expensive or omit isolated shares.

**Option 3: Use permission-trimmed Graph Search before local search**

- Pros: The access check is user-bound and precedes PostgreSQL and Qdrant search without one request per file.
- Cons: [Indexing can omit accessible documents](https://learn.microsoft.com/en-us/troubleshoot/sharepoint/search/search-results-missing), and Graph hits must be joined exactly to stored file identities.

## Decision

For every user question, call Microsoft Search for `driveItem` hits using that user's delegated Graph token.
Join hits exactly to published `(drive_id, item_id)` identities, and restrict subsequent PostgreSQL or Qdrant search to those candidates.
Do not cache authorized file IDs across requests.
Report candidate limits or incomplete paging as a partial result and Graph or identity failures as explicit errors.
API search returns metadata, while MCP similarity search returns passages and provenance from active vector generations.

Before implementing the join, test in the target tenant that a Graph Search hit supplies a reliable drive and item identity.
The documented `hitId` is an internal search identifier and must not be assumed to equal the drive item ID.
If an exact join cannot be established, reconsider this decision before enabling document search.

For the local demonstration, the API and MCP each acquire a delegated token interactively and use Graph `/me` during authentication to bind it to the local user.
File-retrieval middleware supplies the verified user ID, and group discovery uses `GET /users/{user-id}/memberOf`.
MCP also verifies a project JWT containing that user ID and minimal validation claims.
An app-only credential may later support ingestion only if its results still respect the intended user-visible drive scope; it does not establish staff access to a file.

## Consequences

**Positive:**

- Search cannot return a local document without an access-first Graph candidate match.
- API and MCP share one access decision while returning different data.
- The design avoids a Graph request for each Qdrant hit.

**Negative:**

- Accessible files absent from Graph Search may be missing from local results.
- Each query depends on Graph availability, and the first version has no cross-request candidate cache.
- The exact Graph hit-to-drive-item join needs tenant validation before implementation.

**Neutral:**

- The initial deployment permits one local signed-in user per application instance.

## Related Decisions

- [ADR-002: Application Structure](ADR-002-application-structure.md)
- [ADR-004: Data Storage](ADR-004-data-storage.md)
- [ADR-006: Document Vector Storage](ADR-006-document-vector-storage.md)
