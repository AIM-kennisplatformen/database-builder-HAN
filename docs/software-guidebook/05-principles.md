# Principles

## Overview

These principles guide future choices for the planned HAN knowledge platform.
They express preferences that apply across features and data sources, rather than selecting a particular database, schema, or workflow.
The services described here have not yet been implemented ([Context](01-context.md)).

## Architecture Principles

### Secure every access path by default

- **Statement**: Grant access explicitly at every interface.
- **Rationale**: Both graph queries and data mapping can reveal or change information about colleagues. Future staff access must depend on a verified identity and permissions ([Context](01-context.md), [issue #17](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/17)).
- **Implications**:
  - We WILL: protect the MCP and data-mapping interfaces, and build each response from information the requester is allowed to access.
  - We WON'T: expose sensitive information by default or build an unrestricted response and remove forbidden fields afterward.
- **Example**: The local demo should prove that Studio can pass its current user's identity to MCP. Before staff-facing deployment, MCP must validate HAN identity and apply the requester's access rules.

## Design Principles

| Principle                                      | Implication                                                                                                                                                                                                                                                                           |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Keep data sources independent                  | Let each source extract and retain its data without requiring other sources to succeed. Handle cross-source matching separately where possible ([Quality Attributes](03-quality-attributes.md), [issue #16](https://github.com/AIM-kennisplatformen/database-builder-HAN/issues/16)). |
| Keep knowledge traceable                       | Retain the source of each claim and when its data was obtained; distinguish that time from any source modification time. Make transformations and manual input inspectable ([Data](09-data.md), [Quality Attributes](03-quality-attributes.md)).                                      |
| Protect human decisions from silent automation | Flag new data that conflicts with manual input for review. Do not silently replace a human decision during extraction or refresh ([Functional Overview](02-functional-overview.md)).                                                                                                  |
| Make only claims the evidence supports         | Keep the type and strength of a connection clear in models and results. A project association may support a skill claim, but does not itself establish a personal skill ([Glossary](02-functional-overview.md#domain-glossary), [Functional Overview](02-functional-overview.md)).    |

## Coding Standards

| Area          | Standard                                                                                           | Enforced By                                                                                    |
| ------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Documentation | [Repository instructions](../../AGENTS.md) refer to a Markdown standard that is currently missing. | No standard is available to enforce.                                                           |
| Source code   | No implementation language or source-code style standard has been selected.                        | Not applicable; this repository contains no service implementation ([Context](01-context.md)). |

## Principles Summary

Quick reference:

1. Secure every access path by default.
2. Keep data sources independent.
3. Keep knowledge traceable.
4. Protect human decisions from silent automation.
5. Make only claims the evidence supports.
