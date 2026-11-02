# ADR-004: Data Storage

## Status

Accepted

## Date

2026-11-02

## Decision Drivers

- The project requires a TypeDB knowledge graph for source-backed discovery.
- Ingestion needs to retain source records and track refresh progress independently of the graph it builds.
- One developer must operate the local demonstration and hand over a reproducible setup.

## Context

The planned ingestion pipeline combines selected source data into a graph that the MCP application reads. TypeDB holds the modeled entities, relations, and provenance needed for discovery. Ingestion also needs a place for source records and refresh state so that it can process changes and investigate failed imports. The services and storage have not yet been implemented.

## Options Considered

### TypeDB Only

Pros:

- One database to deploy and operate.

Cons:

- Source records and ingestion state would have to fit the graph model or remain outside persistent storage.

### TypeDB and PostgreSQL

Pros:

- PostgreSQL provides a separate store for source records and ingestion state, while TypeDB serves graph queries.

Cons:

- Two databases need deployment, backup, and consistency handling.

### TypeDB and a NoSQL Store

Pros:

- A document store could retain source records with varying shapes.

Cons:

- It adds another database without an identified need for its data model in this project.

## Decision

Use TypeDB for the knowledge graph and PostgreSQL for retained source records and ingestion state. Ingestion reads source data through PostgreSQL and writes the modeled entities, relations, and provenance to TypeDB. The MCP application reads graph context from TypeDB.

## Consequences

**Positive:**

- Source records and refresh state remain available for reprocessing and diagnosing imports.
- The graph can focus on relationships and provenance used by discovery.

**Negative:**

- The two stores require coordinated updates, operations, and recovery.

## Related Decisions

- [ADR-001: Runtime and Deployment Target](ADR-001-runtime-and-deployment-target.md)
- [ADR-002: Application Structure](ADR-002-application-structure.md)
