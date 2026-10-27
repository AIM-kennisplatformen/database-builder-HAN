# Constraints

## Summary

The initial architecture must fit a one-developer project with a fixed end date and no usable budget for new paid services.
It must use TypeDB on Linux AMD64 and connect to the existing Studio application.
Access to HAN identity infrastructure and real UBW FRIS data is unavailable, so the result is a controlled local demonstration.
These are constraints on a planned system; the integrations have not been implemented in this repository.

## Technology Constraints

| Constraint                          | Reason                                                                                                                                                 | Impact on Architecture                                                                                      | Negotiable?                             |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| Use TypeDB for the knowledge graph. | The project assignment requires a TypeQL model and TypeDB store ([issue #14](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/14)). | The pipeline must write selected source data to TypeDB, and the MCP server must read graph context from it. | No, within this project.                |
| Develop for Linux AMD64.            | This is the required target platform for the project.                                                                                                  | Runtime and dependency choices must work on Linux AMD64; support for other platforms is not required.       | Yes, if the project assignment changes. |

## Organizational Constraints

| Constraint                                               | Reason                                                                  | Impact on Architecture                                                                                                                                            |
| -------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One developer.                                           | Simon is the only developer assigned to this back end.                  | The pipeline, MCP server, tests, and developer-only management UI must fit one person's implementation and support capacity.                                      |
| Fixed project window: 31 August 2026 to 15 January 2027. | This is the agreed duration of the project.                             | The architecture and proof of concept must be achievable within that window; production rollout is not part of this delivery.                                     |
| No usable budget for new paid services.                  | No funding is currently available for new API or service subscriptions. | Source selection must work with existing access or free options. A uniquely suitable paid source would need separate funding approval before it could be adopted. |

## People Constraints

| Constraint                                                                                                                                                                                     | Impact                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| The sole developer knows Python and Rust, has limited Java experience, and can build a proof-of-concept UI with JavaScript, React, or Vue. There is no project time to learn another language. | Service and UI technology choices must stay within those skills. This does not select an implementation language in advance. |

## First-Party Dependencies

| Dependency | Type     | Version constraint                                                                                         | Impact                                                                                                                                           | Risk                                                                                                                                                                      |
| ---------- | -------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Studio     | Internal | Latest commit on `main`; the team can update Studio because no version has the required HAN OAuth integration ([Studio issue #50](https://github.com/AIM-kennisplatformen/studio/issues/50)). Record the commit used for each demonstration. | Hosted Studio needs a public URL for the MCP server; running both on the same machine allows a local URL. Studio must pass its user's identity to MCP. | Without HAN OAuth, no hosted Studio version can be used for staff access. A public MCP endpoint requires authentication. For now, MCP could verify Authentik identity and permissions, but the link to HAN OAuth remains open. |

The local demonstration may use live Microsoft Graph colleague data under controlled project access.

## Third-Party Dependencies

**Internal** means a system within HAN; **external** means a service or source outside HAN; **managed** means external software whose environment and hosting the team controls.

| Dependency                 | Type                | Version constraint                                                                                                                | Impact                                                                                                                                               | Risk                                                                                                                                                                |
| -------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hosted Langfuse            | Managed             | Version 3.                                                                                                                        | Requires OpenTelemetry logging ([issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17)) and OpenAI-compatible models to log AI-call token usage. | Studio automatically logs the input and output of every MCP tool call. Preventing that logging requires a Studio update.                                           |
| Microsoft Graph API        | External            | Endpoints and API versions remain under [issue #6](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/6).      | Required colleague identity and contact source.                                                                                                     | [Paging](https://learn.microsoft.com/en-us/graph/paging), [throttling](https://learn.microsoft.com/en-us/graph/throttling), or unavailable permissions may limit extraction. |
| UBW FRIS                   | Internal            | Version not established; usable fields remain under [issue #9](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/9). | Project-data mapping can use demo or mock data only.                                                                                                 | Real access is unavailable; production coverage, joins, and HR access rules remain unvalidated.                                                                    |
| External enrichment source | External            | Provider and version remain undecided under [issue #7](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/7). | May fill an evidenced data need.                                                                                                                     | Permissions, quotas, cost, licence, and storage rights may rule out a candidate.                                                                                    |
| OpenAI-compatible AI model | External or managed | Provider and version are not fixed.                                                                                                | Studio needs a compatible model API for chat and Langfuse token-usage logging.                                                                       | Compatibility and access need verification; Nebius is not required.                                                                                                 |

The project seeks an external enrichment source to fill an evidenced need.
Omitting it requires a documented finding that no suitable source is available; record the selected source's limits here when known.
The model API compatibility requirement follows the team's choice to use Langfuse for token-usage logging.
