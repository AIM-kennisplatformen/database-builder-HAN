# Code

## Target project structure

The following layout is planned for the management and ingestion application under `api/src/`. It is not yet the current file layout. The separately deployed MCP application has its own boundary ([ADR-002](adr/ADR-002-application-structure.md)).

```text
api/src/
├── main.py                         # Application setup and dependency wiring
├── config.py                       # Application configuration
├── logger.py                       # Logging setup
├── api/
│   ├── app.py                      # HTTP application and route registration
│   └── employees/
│       ├── get_employees.py        # Route, request, and response models
│       └── sync_employees.py       # Async route and optional SSE response
├── domain/
│   ├── models/                     # Pure Python dataclasses
│   ├── exceptions/
│   ├── ports/
│   │   ├── persistence/            # Storage capabilities needed by use cases
│   │   └── services/               # External-source capabilities needed by use cases
│   └── use_cases/
│       ├── employees/              # Employee queries and refresh workflow
│       └── documents/              # Document workflows when needed
└── infrastructure/
    ├── persistence/                # PostgreSQL, TypeDB, and Qdrant adapters
    └── services/                   # Microsoft Graph and other source adapters
```

The `domain` package contains both domain rules and application use cases. Its models are dataclasses and do not depend on Pydantic, HTTP, database clients, or the Microsoft Graph SDK. Use cases may be top-level functions rather than classes. They express workflows using capabilities defined by `domain/ports/`; port names describe what the use case needs, not which technology supplies it.

The `api` package owns HTTP routes and Pydantic request and response models. Routes translate between HTTP data and domain values. `sync_employees.py` awaits a complete asynchronous refresh. If progress is streamed with server-sent events (SSE), the route translates progress reported by the use case into SSE messages; the use case does not know about SSE. The request remains open until the refresh finishes. See the revised [processing decision](adr/ADR-005-processing-model.md) for how this coexists with scheduled work.

`infrastructure` implements ports for storage and external services. `config.py` and `logger.py` hold application-wide setup at the source root. `main.py` is the composition root: it constructs adapters, supplies them to the use cases used by the API, and assembles the application. Scheduled execution can call the same use cases with the same port implementations without going through HTTP.

The dependency rules are:

| Module | May depend on project modules |
| ------ | ----------------------------- |
| `api` | `domain` |
| `domain` | None |
| `config.py`, `logger.py` | None |
| `infrastructure` | `domain`, `config.py`, `logger.py` |
| `main.py` | `api`, `domain`, `infrastructure`, `config.py`, `logger.py` |

PostgreSQL retains source records and refresh state, while TypeDB stores modeled graph data ([ADR-004](adr/ADR-004-data-storage.md)). The target also reserves a Qdrant adapter for document RAG. These technologies belong behind persistence ports. Microsoft Graph access belongs behind a service port. The first port and adapter contracts will be defined as those workflows are implemented.

## Conventions

Prefer guard clauses when they reduce nesting. Leave a blank line after each guard clause before the main flow continues.

Use the [naming conventions](naming-conventions.md) for classes, functions, variables, and modules. They define the target naming style for future work and the later naming refactor.

## Current Microsoft Graph integration

The current prototype has not yet been rearranged into this target structure. In [application setup](../../api/src/main.py), Azure Identity's `InteractiveBrowserCredential` is passed to the `msgraph-sdk` `GraphServiceClient`. The [Graph user service](../../api/src/services/user_service.py) currently makes requests and handles pagination. Moving that interaction into an infrastructure adapter is part of the planned restructure.
