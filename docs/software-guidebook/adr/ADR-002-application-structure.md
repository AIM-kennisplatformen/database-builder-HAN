# ADR-002: Application Structure

## Status

Accepted

## Date

2026-11-02

## Decision Drivers

- One developer must implement and support the HAN back end within the fixed project window.
- The initial delivery is a controlled local demonstration on an existing virtual machine, with no budget for new infrastructure.
- Another developer must be able to reproduce and operate the containerized setup after handover, as described in [ADR-001](ADR-001-runtime-and-deployment-target.md).

## Context

The planned HAN back end has three responsibilities: ingest source data into TypeDB, provide graph context to Studio through MCP, and offer a management API for data and mapping changes. None of these responsibilities has been implemented in this repository. Studio is an existing, separately deployed application and accesses HAN graph context through MCP rather than TypeDB or the management API ([Context](../01-context.md)).

The management API allows operators to update data and mappings manually and to request source refreshes. Refreshes include detecting changes in Microsoft Graph data, loading new UBW FRIS data, and loading or updating project information. Some refreshes can also be started automatically. The ingestion pipeline performs the requested or scheduled refresh work.

The frontend will run in its own container under every option, so hosting frontend files does not affect the backend application boundary. [ADR-001](ADR-001-runtime-and-deployment-target.md) establishes containerized deployment on the existing Linux AMD64 virtual machine. This ADR considers how to group the three backend responsibilities into applications.

All HAN backend code will remain in one repository under every option. MCP and management have largely independent responsibilities, and little configuration is expected to be shared between them.

## Options Considered

### One Backend Application

One deployable application contains the MCP interface, management API, and ingestion pipeline. The management API starts operator-requested refreshes; automated refreshes use the same ingestion logic.

Pros:

- One backend build and deployment needs to be maintained within the fixed project window.
- One application version and configuration need to be reproduced after handover.
- Operator-requested and automated refreshes can use the same ingestion logic without a service call.

Cons:

- A management or ingestion change requires redeploying the Studio-facing MCP interface, even when that interface has not changed.

### Two Backend Applications

One deployable application contains the management API and ingestion pipeline. A second deployable application provides the MCP interface to Studio.

Pros:

- The management API can request refreshes without a service call to the ingestion pipeline.
- Management and ingestion changes can be deployed without redeploying the Studio-facing MCP application.

Cons:

- A second backend build and deployment must be maintained, although both applications live in one repository and little configuration is expected to be shared.
- Separately deployed applications must remain compatible with the graph data that ingestion writes and MCP reads.

### Separate Backend Services

The MCP interface, management API, and ingestion pipeline are three independently deployable services. The management service requests refresh work from the ingestion service; automated refreshes also run through the ingestion service.

Pros:

- None identified for the current decision drivers or local demonstration.

Cons:

- The management API needs a service interaction to request refreshes and receive their outcomes from the ingestion service.
- Three backend builds, deployments, and configurations increase implementation and handover work for one developer.

## Decision

Build two backend applications in the same repository. One application provides the MCP interface to Studio. The other contains the management API and ingestion pipeline, including operator-requested and automated refreshes. Deploy each backend application in its own container; the frontend remains in a separate container.

MCP and management have largely independent responsibilities, so separating them allows management and ingestion work to be deployed without redeploying the Studio-facing interface. Management and ingestion stay together because both read and change graph data and mappings as part of corrections and refreshes, and they share refresh work. Keeping both applications in one repository, with little shared configuration expected, makes the additional build and deployment acceptable for this project.

## Consequences

**Positive:**

- Management and ingestion changes can be deployed without redeploying the Studio-facing MCP application when the shared graph data remains compatible.
- Operator-requested and automated refreshes can reuse ingestion logic within the management application without a service API between them.
- Separate containers allow different network exposure and resource limits for MCP and management, if the deployment configuration applies them.

**Negative:**

- Two backend images and runtime configurations must be built, updated, diagnosed, and handed over.
- Separate releases must remain compatible with the TypeDB data that management and ingestion write and MCP reads.
- If ingestion runs in the management API's server process, a long or failed refresh may affect API responsiveness. Running ingestion in a separate worker process within the same application would limit this effect.
