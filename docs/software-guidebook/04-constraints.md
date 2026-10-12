# Constraints

## Summary

The initial architecture must fit a one-developer project with a fixed end date and no usable budget for new paid services.
It must use TypeDB on Linux AMD64 and connect to the existing Studio application.
Access to HAN identity infrastructure and real UBW FRIS data is unavailable, so the result is a controlled local demonstration.
These are constraints on a planned system; the integrations have not been implemented in this repository.

## Technology Constraints

| Constraint | Reason | Impact on Architecture | Negotiable? |
| --- | --- | --- | --- |
| Use TypeDB for the knowledge graph. | The project assignment requires a TypeQL model and TypeDB store ([issue #14](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/14)). | The pipeline must write selected source data to TypeDB, and the MCP server must read graph context from it. | No, within this project. |
| Develop for Linux AMD64. | This is the required target platform for the project. | Runtime and dependency choices must work on Linux AMD64; support for other platforms is not required. | Yes, if the project assignment changes. |

## Organizational Constraints

| Constraint | Reason | Impact on Architecture |
| --- | --- | --- |
| One developer. | Simon is the only developer assigned to this back end. | The pipeline, MCP server, tests, and developer-only management UI must fit one person's implementation and support capacity. |
| Fixed project window: 31 August 2026 to 15 January 2027. | This is the agreed duration of the project. | The architecture and proof of concept must be achievable within that window; production rollout is not part of this delivery. |
| No usable budget for new paid services. | No funding is currently available for new API or service subscriptions. | Source selection must work with existing access or free options. A uniquely suitable paid source would need separate funding approval before it could be adopted. |

## People Constraints

| Constraint | Impact |
| --- | --- |
| The sole developer knows Python and Rust, has limited Java experience, and can build a proof-of-concept UI with JavaScript, React, or Vue. There is no project time to learn another language. | Service and UI technology choices must stay within those skills. This does not select an implementation language in advance. |

## Third-Party Dependencies

| Dependency | Version Constraint | Reason | Risk |
| --- | --- | --- | --- |
| Studio | Existing MCP client; its HAN identity integration is not available ([Studio issue #50](https://github.com/AIM-kennisplatformen/studio/issues/50)). | The HAN back end must supply graph context to the existing chat application through MCP. It does not store Studio user profiles. | A staff-facing deployment is blocked until trustworthy HAN identity and authorization can reach the MCP boundary. The local demonstration may use live Microsoft Graph colleague data under controlled project access. |
| Microsoft Graph | Endpoints, API versions, and permissions remain to be verified under [issue #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6). | Required starting source for colleague identity and work contact data. | [Paging](https://learn.microsoft.com/en-us/graph/paging), [throttling](https://learn.microsoft.com/en-us/graph/throttling), or unavailable permissions may limit extraction. |
| UBW FRIS | Real access is unavailable; usable fields remain to be investigated under [issue #9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9). | Any selected project-data mapping can use demo or mock data only. | A successful demonstration cannot validate production coverage, joins, or HR access rules. |
| External enrichment source | Provider and version are undecided under [issue #7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7). | The project seeks external data to fill an evidenced need; omitting it requires a documented finding that no suitable source is available. | Permissions, quotas, cost, licence, and storage rights may rule out a candidate. Record the selected source's actual limits here when known. |
| Hosted Langfuse | Instance and version are supplied by the wider project. | The MCP server is expected to send traces to the existing monitoring service ([issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17)). | Access or logging restrictions may limit what can be traced. |
| OpenAI-compatible model host | Provider and version are not fixed. | Studio needs access to a compatible model API for the chat demonstration. | The supplied host's API compatibility and access must be verified; Nebius is not required. |
