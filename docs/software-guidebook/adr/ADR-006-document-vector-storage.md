# ADR-006: Store document vectors in Qdrant

## Status

Accepted

## Date

2026-11-13

## Decision Drivers

- The new document-discovery capability needs similarity search over extracted passages.
- File metadata, source links, processing state, and vector state must remain traceable.
- Document discovery is separate from the TypeDB-backed expertise and project journeys.

## Context

[ADR-004](ADR-004-data-storage.md) assigns PostgreSQL retained source records and ingestion state and TypeDB the knowledge graph.
The document-discovery capability adds vectorized SharePoint content and access-controlled passage retrieval.
PostgreSQL and Qdrant cannot share one transaction, so partial rebuilds and stale vectors need an explicit publication rule.
The design details are in the [SharePoint document integration design](../../data-sources/sharepoint-document-integration.md).

## Options Considered

**Option 1: Store vectors and file metadata in PostgreSQL**

- Pros: One store could hold the metadata and embeddings under one transaction boundary.
- Cons: The agreed document-search goal calls for Qdrant, and this option would change that selection.

**Option 2: Store vectors in TypeDB**

- Pros: No additional database beyond those already planned in ADR-004.
- Cons: It would mix passage similarity search with the separate knowledge-graph model and would not meet the Qdrant requirement.

**Option 3: Store file metadata in PostgreSQL and vectors in Qdrant**

- Pros: PostgreSQL retains source identity and ingestion state; Qdrant supports similarity search and file-ID filtering.
- Cons: The stores need coordinated publication, cleanup, and recovery.

## Decision

Use PostgreSQL for SharePoint file metadata, Graph addresses and links, operator observations, delta cursors, job state, and the active vector-generation reference.
Use Qdrant for passage vectors and passage text, with each point carrying its PostgreSQL metadata ID, Graph drive and item IDs, chunk identity, and vector generation.
Keep TypeDB for the existing knowledge graph; document discovery remains a separate capability.

For a changed file, write a complete new Qdrant generation before switching the active generation in PostgreSQL.
Keep the previous generation active when a rebuild fails or times out, then remove old points after a successful switch.
Only active generations may appear in similarity-search results.

## Consequences

**Positive:**

- A file can be traced from a search passage to its PostgreSQL metadata and Graph source.
- Rebuilding one file does not expose a mixture of old and new passages.
- TypeDB remains focused on sourced relationships rather than document passage storage.

**Negative:**

- Qdrant adds another store to deploy, protect, back up, and reconcile with PostgreSQL.
- Failed writes can leave inactive Qdrant generations that require cleanup.
- Passage text is retained in Qdrant in addition to its SharePoint source.

**Neutral:**

- Content extraction, chunking, embedding model, supported formats, and numeric limits remain open.

## Related Decisions

- [ADR-004: Data Storage](ADR-004-data-storage.md)
- [ADR-007: Access-First Document Search](ADR-007-access-first-document-search.md)
