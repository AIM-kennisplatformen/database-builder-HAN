# Context

## System Purpose and Boundary

The HAN knowledge platform is a planned back end that brings selected source data into a TypeDB knowledge graph and makes graph context available to the existing Studio chat application through a Model Context Protocol (MCP) server.
The data pipeline, TypeDB graph, MCP server, management API, and developer-only management UI proof of concept are inside this project's boundary.
Studio, source systems, and Langfuse are outside it.
This repository currently contains architecture documentation but no implementation of those services.
[Issue #14](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/14) defines the context and functional-scope work.

The primary user goal is to find HAN colleagues relevant to a topic.
An expertise connection can be a recorded skill, project involvement, or another sourced association; the kind of connection must be clear in the result.
The [Functional Overview](02-functional-overview.md) defines the journey and its validation status.

## People and External Systems

- **Authenticated HAN staff** use Studio for expert discovery.
- **Developers or designated operators** run the pipeline and resolve ambiguous or unmatched links through the management API.
  A developer-only UI demonstrates this work.
- **Studio** provides the existing chat application and MCP client.
  A local demo will connect it to the planned HAN MCP server.
- **Microsoft Graph** provides production-access colleague data.
  Useful fields and coverage remain under [research issue #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6).
- **UBW FRIS** supplies demo data only; real data access is unavailable for this project.
  Its usable fields remain under [research issue #9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9).
- **An enrichment source** remains to be selected under [research issue #7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7).
  A trusted API, dataset, or approved manual import could be suitable.
- **Langfuse** is a planned destination for MCP traces.
  Its logging boundary is addressed by [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

The future end user of a management platform is unknown.
This project includes a functional management API and a developer-only UI proof of concept, not an end-user management application.

## System Context Diagram

This diagram shows planned interactions and data movement, not implemented integrations.
It shows the HAN knowledge platform as one system and Studio as a separate internal system.
The pipeline, graph, MCP server, management API, and developer UI belong to a later container view.
The exact pipeline and MCP component designs belong to [issues #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16) and [#17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

```plantuml
{{#include diagrams/system-context.puml}}
```

<details>
<summary>View PlantUML source</summary>

```text
{{#include diagrams/system-context.puml}}
```

</details>

Studio already provides a chat application and configurable MCP client, as shown by its [README](https://github.com/AIM-kennisplatformen/studio/blob/main/README.md) and [agent configuration](https://github.com/AIM-kennisplatformen/studio/blob/main/src/backend/utility/chat_util.py).
Studio authenticates users through [Authentik](https://github.com/AIM-kennisplatformen/studio/blob/main/src/backend/endpoints/auth.py).
The local demo must convey the authenticated user's identity to the HAN MCP request.
The format and trust mechanism for that identity, and the resulting authorization rules, remain design questions for [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).
Studio does not access TypeDB or call the management API.

## Initial Scope and Exclusions

The main deliverable is a locally run Studio demonstration in which authenticated HAN staff ask for expertise and receive a list of relevant colleagues with names, work email addresses, source-backed match reasons, and the kind of connection behind each match.
Department and function add substantial value where available.
A colleague without enough information to contact them is excluded from staff-facing results.
A ranked list is not required.
Richer supporting evidence is desirable, but each match must remain traceable to a source and explainable.

The operator path includes a functional API for resolving ambiguous or unmatched source links and correcting accepted mappings.
A developer-only UI proof of concept must demonstrate a correction that is reflected in a later expert-discovery query.
The first supported link types and precise mapping operations belong to [issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).

Contacting or booking colleagues, extracting expertise from document text, a staff-facing correction or feedback feature, and a production management UI are outside this initial scope.
A staff member would need a separate way to report a bad result; no reporting path is planned yet.
Project hours, capacity, and billability are uncommitted candidate journeys pending discussion with Marijn Siebel and Sander Maijers.
Simon has proposed demonstrating hours spent, but no HR-data proof of concept has been agreed.

## Stakeholder Feedback and Open Decisions

Simon reports that he and Marijn Siebel prioritized expert discovery at the start of this project because access to real UBW FRIS data was unavailable.
This design interview established the local-demo boundary, minimum colleague result, management API and developer UI scope, and the separation between Studio, MCP access, and manual correction.
These are scope decisions for the initial architecture, not claims that integrations have been built.

The following remain open:

- Marijn Siebel and Sander Maijers must decide whether any project-hours or billability journey belongs in the proof of concept, and whether the proposed hours-spent example is useful.
- [Source research #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6), [#7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7), and [#9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9) must establish usable fields, trusted enrichment, and demo-data limits.
- [MCP architecture issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17) must define identity propagation and authorization boundaries; detailed HR access rules cannot be validated with the available data.
- The future users and interface of a management platform beyond the developer-only proof of concept are unknown.
