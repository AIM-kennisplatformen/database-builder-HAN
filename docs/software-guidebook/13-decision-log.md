# Decision Log

Architecture decisions are recorded in [ADRs](adr/).
This chapter indexes the decisions that shape the HAN knowledge platform.

## Decisions Index

| ID                                                      | Decision                      | Status   | Date       | Architectural effect                                                |
| ------------------------------------------------------- | ----------------------------- | -------- | ---------- | ------------------------------------------------------------------- |
| [ADR-001](adr/ADR-001-runtime-and-deployment-target.md) | Runtime and deployment target | Accepted | 2026-11-02 | HAN services run in containers on the existing Linux AMD64 VM.      |
| [ADR-002](adr/ADR-002-application-structure.md)         | Application structure         | Accepted | 2026-11-02 | MCP runs separately from the management and ingestion application.  |
