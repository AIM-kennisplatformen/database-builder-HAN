# ADR-005: Ingestion Processing Model

## Status

Accepted

## Date

2026-11-02

## Decision Drivers

- Operators need to request source refreshes, and some refreshes must run automatically ([ADR-002](ADR-002-application-structure.md)).
- A failed refresh must preserve previously valid data and be visible to an operator ([Quality Attributes](../03-quality-attributes.md)).
- Ingestion needs retained source records and refresh state in PostgreSQL ([ADR-004](ADR-004-data-storage.md)).
- The initial demonstration runs locally on an existing virtual machine with limited implementation and operational capacity ([ADR-001](ADR-001-runtime-and-deployment-target.md)).

## Context

The planned management application contains the management API and ingestion pipeline.
It must refresh Microsoft Graph data, load new UBW FRIS demo data, and load or update project information when those sources are selected and validated.
Source access methods, update frequency, and acceptable data age are not yet established.
No ingestion flow, scheduler, worker, or event interface has been implemented ([Data](../09-data.md), [External Interfaces](../07-external-interfaces.md)).

The [pipeline design issue](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16) still needs to define partial-data handling, retryable failures, and the TypeDB write boundary.
This decision therefore addresses how refresh work starts and runs, without specifying source-specific extraction or graph update rules.

## Options Considered

### On-Demand Batch Jobs Only

The management API starts a refresh when an operator requests it.

Pros:

- Few moving parts for the local demonstration.
- Suitable for a newly supplied data dump.

Cons:

- Does not provide the automatic refreshes planned in [ADR-002](ADR-002-application-structure.md).
- Long-running work in the API process could affect API responsiveness.

### Scheduled Worker with On-Demand Requests

A separate worker process belonging to the management and ingestion application runs refresh jobs requested by an operator or created by a schedule.
PostgreSQL retains job state and source records; the worker applies successful results to TypeDB.

Pros:

- One ingestion path handles both requested and automatic refreshes.
- A separate worker process can keep long-running refreshes away from the API server process.
- Retained job state can support failure inspection and later retry design.

Cons:

- Requires worker lifecycle, scheduling, and coordination with PostgreSQL and TypeDB.
- Source-specific intervals and failure behavior remain to be designed.

### Event-Driven Ingestion

Source events or a message broker trigger processing when data changes.

Pros:

- Could reduce delay between a source change and graph update if a selected source offers a suitable event interface.

Cons:

- No consumed event interface or broker has been defined for this project.
- Event delivery, duplicate handling, and recovery would add design and operational work before source capabilities are known.

## Decision

Run ingestion in a separate worker process within the management and ingestion application boundary established by [ADR-002](ADR-002-application-structure.md).
Use scheduled jobs for automatic refreshes and the same job path for on-demand refresh requests.
The management API records requested work in PostgreSQL, and the scheduler records automatic work there as well.
The worker processes those jobs using the same ingestion logic and records progress and outcomes in PostgreSQL.
Source records are retained there as specified by [ADR-004](ADR-004-data-storage.md); modeled entities, relations, and provenance are written to TypeDB.

Define the schedule, job concurrency, retry policy, and recovery behavior after source access and the pipeline failure design are validated under [issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).
Do not assume source events are available.

## Consequences

**Positive:**

- Requested and automatic refreshes share one processing path.
- The separate worker process keeps slow extraction out of the management API server process.
- PostgreSQL can retain job outcomes for operator inspection.

**Negative:**

- A worker and scheduler need to be operated alongside the management API.
- PostgreSQL and TypeDB updates require explicit failure and recovery handling.

**Open:**

- Confirm which sources support automatic refresh and define their acceptable age and schedule.
- Define how jobs resume or retry after failure without replacing previously valid graph data.
- Define how operator corrections remain effective during later refreshes ([Principles](../05-principles.md)).

## Related Decisions

- [ADR-001: Runtime and Deployment Target](ADR-001-runtime-and-deployment-target.md)
- [ADR-002: Application Structure](ADR-002-application-structure.md)
- [ADR-004: Data Storage](ADR-004-data-storage.md)
