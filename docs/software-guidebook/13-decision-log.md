# Decision Log

Architecture decisions are recorded in [ADRs](adr/).
This chapter indexes the decisions that shape the HAN knowledge platform.

## Decisions Index

| ID                                                      | Decision                      | Status   | Date       | Architectural effect                                               |
| ------------------------------------------------------- | ----------------------------- | -------- | ---------- | ------------------------------------------------------------------ |
| [ADR-001](adr/ADR-001-runtime-and-deployment-target.md) | Runtime and deployment target | Accepted | 2026-11-02 | HAN services run in containers on the existing Linux AMD64 VM.     |
| [ADR-002](adr/ADR-002-application-structure.md)         | Application structure         | Accepted | 2026-11-02 | MCP runs separately from the management and ingestion application. |
| [ADR-003](adr/ADR-003-programming-language.md)          | Programming language          | Accepted | 2026-11-02 | Both backend applications use Python.                              |
| [ADR-004](adr/ADR-004-data-storage.md)                  | Data storage                  | Accepted | 2026-11-02 | TypeDB stores the graph; PostgreSQL supports ingestion.            |
| [ADR-005](adr/ADR-005-processing-model.md)              | Ingestion processing model    | Accepted | 2026-11-02 | The API runs employee sync; a worker runs scheduled refreshes.     |
| [ADR-006](adr/ADR-006-document-vector-storage.md)       | Document vector storage       | Accepted | 2026-11-13 | PostgreSQL tracks documents; Qdrant stores passages and vectors.   |
| [ADR-007](adr/ADR-007-access-first-document-search.md)  | Access-first document search  | Accepted | 2026-11-13 | Delegated Graph Search limits candidates before local search.      |
