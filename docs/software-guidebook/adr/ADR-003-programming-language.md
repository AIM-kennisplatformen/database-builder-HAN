# ADR-003: Programming Language

## Status

Accepted

## Date

2026-11-02

## Decision Drivers

- The language and libraries must support reading from and writing to TypeDB, including the TypeQL operations needed by ingestion, management, and MCP requests.
- The MCP application must expose a protocol-compatible interface that Studio can use and must be able to handle the authenticated user identity conveyed with requests.
- The management and ingestion application must consume the selected source interfaces, starting with the Microsoft Graph API and a UBW FRIS data dump. Other enrichment sources remain to be selected.
- One language must support both backend applications and the operator-requested and automated refresh work within the application boundary established by [ADR-002](ADR-002-application-structure.md).
- The implementation must run in containers on Linux AMD64, as established by [ADR-001](ADR-001-runtime-and-deployment-target.md).
- One developer must implement and support the back end within the fixed project window. The developer knows Python and Rust, has limited Java experience, and has no project time to learn another language ([Constraints](../04-constraints.md)).
- Another developer must be able to reproduce the build and maintain both backend applications after handover.

## Context

The planned HAN back end ingests selected source data into TypeDB, provides graph context to Studio through MCP, and exposes a management API for correcting data and mappings and requesting refreshes. This repository does not yet contain implementations of these services. [ADR-002](ADR-002-application-structure.md) groups management and ingestion in one backend application and MCP in another; both remain in one repository.

The language choice must cover both applications and their integrations. The pipeline starts with Microsoft Graph and a UBW FRIS data dump, while the enrichment source and the precise source fields remain open. Current Microsoft Graph access uses delegated authentication, and organizational restrictions make interactive sign-in relevant. The MCP transport, request contract, and identity verification mechanism also remain open. Support for the required TypeDB operations, MCP integration, source access, and authentication must be checked against the versions and contracts selected during implementation.

This ADR concerns the backend implementation language. The developer-only management UI has its own frontend technology choice. Runtime packaging and application boundaries are already recorded in ADR-001 and ADR-002.

## Options Considered

### Python for Both Backend Applications

Implement the MCP application and the management and ingestion application in Python. Use Python integrations for TypeDB, MCP, and the selected sources, including `ms-graph-sdk` for Microsoft Graph.

Pros:

- The [Microsoft Graph Python SDK](https://github.com/microsoftgraph/msgraph-sdk-python) can use interactive browser credentials through Azure Identity. The developer expects this to make delegated Graph access easier to manage under the organization's restrictions. This advantage disappears if the project receives an application credential and uses application access instead of delegated access.
- Official [TypeDB](https://typedb.com/docs/home/install/drivers/) and [MCP](https://github.com/modelcontextprotocol/python-sdk) Python SDKs cover the two central integrations.
- The sole developer already knows Python, so the ingestion and service code can be developed without a language learning period during the fixed project window.

Cons:

- Python's runtime type behavior makes tests and explicit validation important at source-record, graph-result, and API boundaries.

### Rust for Both Backend Applications

Implement both backend applications in Rust. Use Rust libraries or direct protocol clients for TypeDB, MCP, and the selected sources, including Microsoft Graph.

Pros:

- Official [TypeDB](https://typedb.com/docs/home/install/drivers/) and [MCP](https://github.com/modelcontextprotocol/rust-sdk) Rust SDKs cover the two central integrations.
- Rust's compile-time type checking can catch mistakes in source transformations and service interfaces before deployment.

Cons:

- A Rust approach to delegated Microsoft Graph sign-in that works under the organization's restrictions has not been established for this project. Selecting and validating one adds work before ingestion can use Graph.
- Rust requires the developer to handle ownership, lifetimes, and compile-time type constraints while implementing the ingestion and service code within the fixed project window.

## Decision

Use Python for both backend applications. This keeps the implementation in a language the developer already knows and supports the current delegated Microsoft Graph access approach. The interactive sign-in advantage is conditional on delegated access; the language decision remains Python if application credentials become available.

## Consequences

**Positive:**

- Both backend applications use one implementation language.
- The current delegated Microsoft Graph access can use the interactive sign-in options available through Python's `ms-graph-sdk`.

**Neutral:**

- If Microsoft Graph access changes to application credentials, the interactive sign-in advantage no longer applies.

## Related Decisions

- [ADR-001: Runtime and Deployment Target](ADR-001-runtime-and-deployment-target.md)
- [ADR-002: Application Structure](ADR-002-application-structure.md)
