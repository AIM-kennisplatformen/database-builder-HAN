# Context

## System Overview

The HAN knowledge-platform back end is a planned system for bringing selected structured information into a TypeDB knowledge graph and making graph context available to the existing Studio chat application through a Model Context Protocol (MCP) server.
The pipeline, graph, and MCP server are within this project's boundary; Studio is outside it.
This repository currently contains guidebook and architecture work, but no implementation of those services.
[Initial architecture issue #13](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/13) defines the planned work.

## Why This System Exists

HAN staff need to find colleagues with relevant expertise and discover related people and projects.
Those goals are described in the [Functional Overview](02-functional-overview.md); their priority and supporting data still need validation.
[Data exploration issue #4](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/4) explains why the older pipeline and theoretical graph schema need rebuilding around data that can actually be obtained.

## System Context Diagram

The relationships below are planned for this project.
Studio already has a chat application and a configurable MCP client, but this repository does not yet provide the HAN MCP server.

```plantuml
@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Context.puml

title HAN Knowledge-Platform Management App

Boundary(studio_app, "Studio App Context", "") {
  Person_Ext(staff, "HAN staff", "Finds expertise")
  System_Ext(studio, "Studio", "Chat and MCP client")
  System_Ext(langfuse, "Langfuse", "")
}

Boundary(management_app, "Management App Context", "") {
  Person(unknown_manager, "?", "At this time it is unknown who will be the user of the Management App.")
  System(han, "HAN Knowledge Platform", "MCP server, Management App")
}

Boundary(data_sources, "External data sources", "") {
  System_Ext(msgraph, "MS Graph", "Planned source")
  System_Ext(ubwfris, "UBW FRIS", "Planned source")
  System_Ext(other, "Other sources", "Planned sources")
}

Rel(staff, studio, "Asks about expertise")
Rel(studio, han, "Gets knowledgegraph context")
Rel(studio, langfuse, "Logs LLM calls")

Rel(unknown_manager, han, "Builds knowledgegraph and maps data sources")
Rel(han, msgraph, "")
Rel(han, ubwfris, "")
Rel(han, other, "")
Rel(han, langfuse, "Logs MCP tool internal calls")
@enduml
```

<details>
<summary>View PlantUML source</summary>

````text
@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Context.puml

title HAN Knowledge-Platform Management App

Boundary(studio_app, "Studio App Context", "") {
  Person_Ext(staff, "HAN staff", "Finds expertise")
  System_Ext(studio, "Studio", "Chat and MCP client")
  System_Ext(langfuse, "Langfuse", "")
}

Boundary(management_app, "Management App Context", "") {
  Person(unknown_manager, "?", "At this time it is unknown who will be the user of the Management App.")
  System(han, "HAN Knowledge Platform", "MCP server, Management App")
}

Boundary(data_sources, "External data sources", "") {
  System_Ext(msgraph, "MS Graph", "Planned source")
  System_Ext(ubwfris, "UBW FRIS", "Planned source")
  System_Ext(other, "Other sources", "Planned sources")
}

Rel(staff, studio, "Asks about expertise")
Rel(studio, han, "Gets knowledgegraph context")
Rel(studio, langfuse, "Logs LLM calls")

Rel(unknown_manager, han, "Builds knowledgegraph and maps data sources")
Rel(han, msgraph, "")
Rel(han, ubwfris, "")
Rel(han, other, "")
Rel(han, langfuse, "Logs MCP tool internal calls")
@enduml
````

</details>

The source relationships are candidates rather than completed integrations, as described by [pipeline issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).
The planned MCP and Langfuse boundary comes from [MCP issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).
Studio's [README](https://github.com/AIM-kennisplatformen/studio/blob/main/README.md) and [agent configuration code](https://github.com/AIM-kennisplatformen/studio/blob/main/src/backend/utility/chat_util.py) show its existing chat and MCP-client roles.

## People and Interests

| Role | Interest or interaction |
| --- | --- |
| HAN staff | Use Studio to seek expertise, colleagues, and related projects. |
| Developer | Builds and operates the planned pipeline, graph, and MCP server. |
| Knowledge-platform researchers | Need to evaluate whether graph-backed answers rest on suitable source data. |
| Project lead and architecture reviewers | Need a reviewable system boundary and evidence for source and design choices. |

The staff information needs are described in the [Functional Overview](02-functional-overview.md).
[Issues #13](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/13) and [#14](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/14) call for stakeholder review of the architecture and scope.

## External Systems and Scope

Studio is the user-facing chat application.
Its agent can use a configured MCP client; connecting it to this project's MCP server remains planned.
Microsoft Graph, UBW FRIS, and other external data sources are candidates whose usable fields, access, and quality remain under research in [issues #4](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/4), [#6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6), and [#9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9).
Langfuse is a planned destination for MCP-related traces under [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

The proposed initial proof of concept focuses on finding colleagues with relevant expertise and discovering related people and projects.
Availability, project hours, and billability remain broader user goals in the [Functional Overview](02-functional-overview.md), with their priority and data needs still to be validated.
This project uses Studio's existing chat interface rather than building another staff-facing chat application.

Studio already authenticates its users through [Authentik](https://github.com/AIM-kennisplatformen/studio/blob/main/src/backend/endpoints/auth.py).
How that identity could support role-based access to HAN graph information remains an open design question in [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).
A design-only proof of concept extending Studio's login to role-based information access is proposed; no role-filtering implementation is claimed here.
