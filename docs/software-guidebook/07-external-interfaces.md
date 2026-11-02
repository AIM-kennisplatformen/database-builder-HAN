# External Interfaces

The initial local demonstration plans to consume the Microsoft Graph API and a UBW FRIS data dump, and to provide an MCP interface to Studio.
These interfaces have not been implemented in this repository.

## APIs Provided

### HAN MCP server

- **Type**: Model Context Protocol (MCP) server for graph context requested by Studio.
- **Base URL**: Not selected; a local URL is possible when Studio and the MCP server run on the same machine.
- **Versioning**: Not defined.
- **Authentication**: Studio must convey the authenticated user's identity. The identity format, trust mechanism, and authorization rules remain open under [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).
- **Rate limits**: Not defined.
- **Documentation**: The planned user interaction is in the [Functional Overview](02-functional-overview.md); the MCP contract and tool definitions remain open under [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17).

The MCP server will supply source-backed colleague and project context for Studio's discovery requests.
Its request and response contracts, transport, failure responses, and service guarantees have not been specified.

## APIs Consumed

| Service             | Purpose                                     | Auth method                                                            | Rate limits | Failure handling |
| ------------------- | ------------------------------------------- | ---------------------------------------------------------------------- | ----------- | ---------------- |
| Microsoft Graph API | Colleague identity and contact information. | Delegated OAuth for current access; pipeline credential handling open. | Throttling. | Policies open.   |

### Microsoft Graph API

- **Purpose**: The planned pipeline needs colleague identity and work contact data for discovery results. The [Graph source inventory](../data-sources/ms-graph-api.md) records the endpoints, delegated scope status, and field validation gaps.
- **Fallback behavior**: Not defined for unavailable Graph data. Pipeline and graph refresh behavior remain under [issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16).
- **Timeout policy**: Not defined.
- **Retry policy**: Not defined. A future extractor must handle HTTP `429` and honor `Retry-After`, as described in the [Graph source inventory](../data-sources/ms-graph-api.md).
- **Circuit breaker**: Not defined.

The [Graph source inventory](../data-sources/ms-graph-api.md) records these `v1.0` endpoints, the earlier inspected token, and the newly reported delegated `User.Read.All` grant.

| Endpoint                                                                                                                                                  | Version | Delegated scopes needed for the project                                                             |
| --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------- |
| [`GET /groups/{id}`](https://learn.microsoft.com/en-us/graph/api/group-get?view=graph-rest-1.0)                                                           | `v1.0`  | `Group.Read.All` to read the configured employee group.                                             |
| [`GET /groups/{id}/transitiveMembers/microsoft.graph.user`](https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers?view=graph-rest-1.0) | `v1.0`  | `Group.Read.All` to list members; `User.ReadBasic.All` to read basic user properties.               |
| [`GET /users/{id}`](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0)                                                             | `v1.0`  | `User.ReadBasic.All` for other colleagues' basic profiles; `User.Read.All` for their full profiles. |

The earlier inspected token had `Group.Read.All` and `User.ReadBasic.All`, but lacked `User.Read.All`. The project now reports access to delegated `User.Read.All`; a new token and its returned profile fields have not yet been verified.
Extraction must follow `@odata.nextLink` to cover all pages; Graph may throttle requests, and the new pipeline's retry policy is still open.

## Events/Messages

### Published Events

No published event interface has been defined for this project.

### Consumed Events

No consumed event interface has been defined for this project.

## File Interfaces

| Interface          | Format          | Location        | Frequency       | Direction |
| ------------------ | --------------- | --------------- | --------------- | --------- |
| UBW FRIS demo dump | Not established | Not established | Not established | Import    |

UBW FRIS has an API, but this project has no access to it and plans to use a data dump instead.
Real UBW FRIS data is unavailable; the dump supports demo project-data mapping only.
Its usable fields, file contract, and import failure handling remain unresolved under [issue #9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9).
