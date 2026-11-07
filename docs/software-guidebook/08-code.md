# Code

## Target project structure

The following layout is the intended structure for the management and ingestion application under `api/src/`. It is not yet the current file layout.

```text
api/src/
├── main.py
├── dependencies.py
├── api/
│   ├── routes/
│   └── schemas/
├── core/
│   ├── config.py
│   └── logger.py
├── db/
│   ├── postgres/
│   ├── typedb/
│   └── qdrant/
├── domain/
│   ├── models/
│   └── repositories/
└── services/
```

`main.py` and `dependencies.py` assemble the application and may import across module boundaries. The `api` module contains routes and HTTP request and response schemas. Routes convert requests into service inputs and service results into response schemas. A service input or result does not need its own class; services may work with raw data or domain models, but do not depend on this API's HTTP schemas.

The dependency rules are:

| Module | May depend on project modules |
| ------ | ----------------------------- |
| `api` | `core`, `services`, `domain` |
| `core` | None |
| `db` | `core`, `domain` |
| `domain` | None |
| `services` | `core`, `db`, `domain` |

Services use a `domain/repositories` interface where one exists, with `dependencies.py` supplying its `db` implementation. Direct service use of `db` is allowed when an interface adds no value. Both `domain` and `db` are internal to this application. Microsoft Graph is an external API, so a service may call it directly without treating it as a repository.

The required database adapters have different roles: `db/postgres/` handles PostgreSQL persistence, `db/typedb/` handles the knowledge graph, and `db/qdrant/` handles vector storage and similarity queries for document RAG. Services coordinate those operations. Document RAG is planned for general information finding in the demonstration, separate from expertise discovery.

## Microsoft Graph integration

The management API uses `msgraph-sdk` to make Microsoft Graph API calls and `azure-identity` to obtain delegated credentials. In [application setup](../../api/src/api/main.py), an Azure Identity `InteractiveBrowserCredential` is passed to the SDK's `GraphServiceClient`. This keeps credential acquisition in Azure Identity while the [Graph user service](../../api/src/services/user_service.py) uses the SDK for requests and pagination.
