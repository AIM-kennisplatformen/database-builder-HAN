# ADR-005: Ingestion Processing Model

## Status

Accepted; revised 2026-11-08

## Date

2026-11-02

## Decision Drivers

- Operators need to request source refreshes, and some refreshes must run automatically ([ADR-002](ADR-002-application-structure.md)).
- The employee-sync API must await the complete Microsoft Graph refresh and may stream progress with SSE.
- A failed refresh must preserve previously valid data and be visible to an operator ([Quality Attributes](../03-quality-attributes.md)).
- Ingestion needs retained source records and refresh state in PostgreSQL ([ADR-004](ADR-004-data-storage.md)).
- The initial demonstration runs locally on an existing virtual machine with limited implementation and operational capacity ([ADR-001](ADR-001-runtime-and-deployment-target.md)).

## Context

The planned management application contains the management API and ingestion pipeline.
It must refresh Microsoft Graph data, load new UBW FRIS demo data, and load or update project information when those sources are selected and validated.
Source access methods, update frequency, and acceptable data age are not yet established.
No ingestion flow, scheduler, worker, or event interface has been implemented ([Data](../09-data.md), [External Interfaces](../07-external-interfaces.md)).

The [pipeline design issue](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16) still needs to define partial-data handling, retryable failures, and the TypeDB write boundary.
This decision addresses where refresh work runs and how it starts, without specifying source-specific extraction or graph update rules.

## Options Considered

### All Refreshes in the API Process

The management API runs every operator-requested refresh to completion; automatic refreshes require a scheduler to invoke the same logic.

Pros:

- The employee-sync request can await its result and optionally stream progress directly.
- Few moving parts for operator-requested work.

Cons:

- Long refreshes consume API process resources and can affect responsiveness.
- Work in a disconnected request needs an explicit cancellation or continuation policy.

### All Refreshes in a Worker

The API records requested jobs in PostgreSQL; a separate worker processes both requested and scheduled jobs.

Pros:

- Long-running extraction stays outside the API process.
- Retained job state can support failure inspection and later retry design.

Cons:

- An awaited HTTP request needs to observe a separate job until completion, adding coordination and progress delivery.

### API-Run Employee Sync with Shared Use Cases

The employee-sync HTTP route awaits an asynchronous use case in the API process. Scheduled refreshes run in a separate worker process, which invokes the same use cases through their ports.

Pros:

- The route can return a final result or stream progress while the sync runs.
- HTTP and scheduled entry points reuse ingestion rules and port implementations.

Cons:

- API responsiveness and request lifetime need attention during long employee syncs.
- Different entry points require coordination if they can refresh the same source concurrently.

## Decision

Use the API-run employee-sync path with shared use cases. The asynchronous `sync_employees` route awaits the complete refresh in the management API process. It may translate use-case progress into SSE messages. Domain use cases and ports remain independent of HTTP and SSE. Automatic refreshes run in a scheduled worker within the same management and ingestion application boundary; the worker invokes those use cases without going through the API route.

PostgreSQL retains source records and refresh state as specified by [ADR-004](ADR-004-data-storage.md); modeled entities, relations, and provenance are written to TypeDB. Progress and outcome recording must remain available for operator inspection, including when the HTTP connection closes. Define the schedule, concurrency controls, retry and cancellation policies, and recovery behavior after source access and failure handling are validated under [issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).

## Consequences

**Positive:**

- An operator can await the employee refresh outcome from one HTTP request, with optional live progress.
- Scheduled and requested refreshes can reuse the same core workflow without coupling it to HTTP.
- Source records and refresh outcomes can be inspected after the request.

**Negative:**

- A slow employee refresh occupies an API request and may affect API responsiveness.
- Request timeouts, disconnects, and overlapping scheduled refreshes require explicit handling.
- PostgreSQL and TypeDB updates require explicit failure and recovery handling.

**Open:**

- Confirm which sources support automatic refresh and define their acceptable age and schedule.
- Define how jobs resume or retry after failure without replacing previously valid graph data.
- Define how operator corrections remain effective during later refreshes ([Principles](../05-principles.md)).
- Decide whether a disconnected employee-sync request continues or cancels, and how its final outcome is retrieved.

## Related Decisions

- [ADR-001: Runtime and Deployment Target](ADR-001-runtime-and-deployment-target.md)
- [ADR-002: Application Structure](ADR-002-application-structure.md)
- [ADR-004: Data Storage](ADR-004-data-storage.md)
