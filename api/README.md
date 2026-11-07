# HAN management API

The application retrieves HAN employee profiles through the Microsoft Graph Python SDK and stores source versions in PostgreSQL.
`GET /users` reads the current effective values, including manual overrides, using source-independent snake-case fields.
Each saved Graph version keeps its raw profile and request URL; the start of `valid_at` records when that version was stored.

After starting the API, open `http://127.0.0.1:8001/docs` for interactive OpenAPI documentation or `http://127.0.0.1:8001/openapi.json` for the schema. The Teams listing operations use HTTP `GET` with JSON request bodies. Downloads return file bytes with a content type based on the file.

Start PostgreSQL from the repository root:

```bash
docker compose up -d postgres
```

Then run the API from this directory:

```bash
uv sync
uv run poe start
```

At startup, Alembic applies pending revisions in `migrations/versions/` before the API accepts requests.
Each revision defines `upgrade()` and `downgrade()` steps, and Alembic tracks the current revision in `alembic_version`.
The temporal PostgreSQL schema remains in SQL because it uses PostgreSQL-specific constraints.
The `source_fetched_at` downgrade restores that column from the start of `valid_at`.
Run `uv run poe migrate-up` or `uv run poe migrate-down` from this directory to apply one manually; startup always upgrades to the latest revision.

The application validates the root `.env` at startup and stops before serving requests if a required value is missing or invalid.
Required keys are `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT`, `GRAPH_CLIENT_ID`, `GRAPH_TENANT_ID`, `GRAPH_SCOPES`, `GROUP_ID`, and `GRAPH_REDIRECT_URI`.
Environment variables can override values from the file.

`POST /users/refresh?limit=10` retrieves ten Graph profiles and stores them, one completed profile per transaction.
Omit `limit` to retrieve every member returned by Graph.
`GET /users?limit=10` reads the first ten effective users from PostgreSQL.
The refresh preserves prior source versions and existing manual overrides; it does not yet mark users absent when they disappear from Graph.
The Graph employee group ID is read from `GROUP_ID` in the root `.env` file when the application starts.
The authentication strategy uses interactive browser sign-in with the configured redirect URI and delegated scopes.
Set `GRAPH_BROWSER_AUTO_SIGN_IN=true` to reuse an existing Microsoft sign-in in the browser when possible. This opens the browser without an account-selection prompt; Microsoft may still request sign-in, consent, or MFA. The option is off by default. For this option, `GRAPH_REDIRECT_URI` must use `http://localhost` with a port and match the app registration.
`openid`, `profile`, and `offline_access` are omitted from configured scopes because MSAL adds them automatically.
Set `POSTGRES_HOST` if PostgreSQL is not reachable at `127.0.0.1`.

At startup, the application creates the authentication strategy, Graph client, user repository, and user service once. FastAPI dependencies give each route the shared repository or service. The Graph credential is closed when the application shuts down.

Run the local checks without Graph credentials:

```bash
uv run poe test
```

With the PostgreSQL container running, check temporal storage and override preservation:

```bash
uv run poe test-db
```

## Teams document libraries

The Teams routes use the application's shared interactive browser credential. No bearer token is needed on HTTP requests. All requests act as the account signed in through that credential; direct Team membership and file access checks apply to that account. Configure `GRAPH_SCOPES` with delegated Microsoft Graph `GroupMember.Read.All`, `Group.Read.All`, and `Files.Read.All` permissions and grant consent in the public client app registration. When `GRAPH_SCOPES` is empty, token acquisition uses `https://graph.microsoft.com/.default`. The signed-in account also needs SharePoint access.

```bash
curl -X GET -H 'Content-Type: application/json' -d '{"name":"research"}' http://127.0.0.1:8001/teams
curl -X GET -H 'Content-Type: application/json' -d '{"path":"General/Reports","name":"budget","recursive":true,"limit":100}' http://127.0.0.1:8001/teams/TEAM_ID/files
curl -OJ http://127.0.0.1:8001/teams/TEAM_ID/files/DRIVE_ID/ITEM_ID/download
```

`GET /teams` takes a JSON body with optional `name` and returns directly joined Teams as objects with `id` and `display_name`. The name filter matches any case-insensitive substring. `GET /teams/{team_id}/files` takes a JSON body with optional `path` and `name`, plus `recursive` (default `false`) and `limit` (default 100, maximum 500). Filters combine with AND and apply before the limit. The path filter matches the start of the library-relative item path, ignoring case; partial folder or file names are allowed. Use `recursive: true` to search nested folders. The name filter selects files whose names contain the case-insensitive substring. Send `{}` to use defaults.

File results contain `library`, `drive_id`, `item_id`, `name`, `path` (relative to the library root), `type` (`file` or `folder`), and nullable `size` and `web_url`. The service follows Graph pages. The GET download route returns file bytes with a safe attachment filename. The SDK buffers each download in memory. Both Team routes require direct membership; downloads also check drive and item ownership.

Standard channel files generally live in these group libraries. Private and shared channel files may be on separate SharePoint sites and are outside this API's scope.
