"""Delegated Graph Team file behavior without live credentials."""

import asyncio
from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport
from kiota_abstractions.authentication.anonymous_authentication_provider import (
    AnonymousAuthenticationProvider,
)
from msgraph.graph_request_adapter import GraphRequestAdapter
from msgraph.graph_service_client import GraphServiceClient

from api.routes.teams import (
    download_team_file,
    handle_team_service_error,
    list_joined_teams,
    list_team_files,
    router,
)
from api.schemas.team import TeamFileResponse, TeamFilesQuery, TeamResponse, TeamsQuery
from dependencies import get_team_service
from services.team_service import (
    TeamFile,
    TeamService,
    TeamServiceError,
    TeamServiceErrorCode,
    TeamSummary,
)

TEAM = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


def create_team_service(
    handle_graph_request: Callable[[httpx.Request], httpx.Response],
) -> TeamService:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle_graph_request), follow_redirects=True
    )
    adapter = GraphRequestAdapter(AnonymousAuthenticationProvider(), client=client)
    return TeamService(GraphServiceClient(request_adapter=adapter))


def test_teams_filter_groups_and_follow_pages() -> None:
    calls: list[str] = []

    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path == "/v1.0/me/memberOf" and "page" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"@odata.type": "#microsoft.graph.user", "id": "person"},
                        {"@odata.type": "#microsoft.graph.group", "id": OTHER},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/memberOf?page=2",
                },
            )
        if path == "/v1.0/me/memberOf":
            return httpx.Response(
                200,
                json={"value": [{"@odata.type": "#microsoft.graph.group", "id": TEAM}]},
            )
        return httpx.Response(
            200,
            json={
                "displayName": "Actual Team" if TEAM in path else "Ordinary group",
                "resourceProvisioningOptions": ["Team"] if TEAM in path else [],
            },
        )

    result = asyncio.run(create_team_service(handle_graph_request).list_joined_teams())
    assert [(team.id, team.display_name) for team in result] == [(TEAM, "Actual Team")]
    assert len(calls) == 4


def test_membership_denied_before_drive_lookup() -> None:
    calls: list[str] = []

    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"value": []})

    with pytest.raises(TeamServiceError) as error:
        asyncio.run(
            create_team_service(handle_graph_request).list_team_files(TEAM, 100, False)
        )
    assert error.value.code == TeamServiceErrorCode.NOT_TEAM_MEMBER
    assert calls == ["/v1.0/me/memberOf"]


def test_file_listing_pages_and_limit() -> None:
    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1.0/me/memberOf":
            return httpx.Response(
                200,
                json={"value": [{"@odata.type": "#microsoft.graph.group", "id": TEAM}]},
            )
        if path == f"/v1.0/groups/{TEAM}" and "drives" not in path:
            return httpx.Response(200, json={"resourceProvisioningOptions": ["Team"]})
        if path.endswith("/drives"):
            return httpx.Response(
                200, json={"value": [{"id": "drive", "name": "Documents"}]}
            )
        if "page" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "a", "name": "a.txt", "file": {}, "size": 2}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/drive/root/children?page=2",
                },
            )
        return httpx.Response(
            200, json={"value": [{"id": "b", "name": "Folder", "folder": {}}]}
        )

    files = asyncio.run(
        create_team_service(handle_graph_request).list_team_files(TEAM, 2, False)
    )
    assert [(file.item_id, file.path, file.type) for file in files] == [
        ("a", "a.txt", "file"),
        ("b", "Folder", "folder"),
    ]


def test_download_checks_ownership_and_follows_sdk_redirect() -> None:
    seen: list[tuple[str, str | None]] = []

    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("Authorization")))
        path = request.url.path
        if path == "/v1.0/me/memberOf":
            return httpx.Response(
                200,
                json={"value": [{"@odata.type": "#microsoft.graph.group", "id": TEAM}]},
            )
        if path == f"/v1.0/groups/{TEAM}":
            return httpx.Response(200, json={"resourceProvisioningOptions": ["Team"]})
        if path.endswith("/drives"):
            return httpx.Response(
                200, json={"value": [{"id": "drive", "name": "Documents"}]}
            )
        if path.endswith("/items/file"):
            return httpx.Response(
                200,
                json={
                    "id": "file",
                    "name": "report.txt",
                    "file": {"mimeType": "text/plain"},
                    "parentReference": {"driveId": "drive"},
                },
            )
        if path.endswith("/content"):
            return httpx.Response(
                302, headers={"Location": "https://tenant.sharepoint.com/report"}
            )
        assert request.url.host == "tenant.sharepoint.com"
        return httpx.Response(200, content=b"streamed")

    async def verify_download_response():
        team = create_team_service(handle_graph_request)
        with pytest.raises(TeamServiceError) as error:
            await team.download_team_file(TEAM, "other", "file")
        assert error.value.code == TeamServiceErrorCode.DRIVE_OUTSIDE_TEAM
        response = await download_team_file(TEAM, "drive", "file", team)
        data = response.body
        return data, response.headers["content-disposition"]

    assert asyncio.run(verify_download_response()) == (
        b"streamed",
        'attachment; filename="report.txt"',
    )
    assert seen[-1] == ("tenant.sharepoint.com", None)


def test_rejects_item_from_other_drive() -> None:
    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1.0/me/memberOf":
            return httpx.Response(
                200,
                json={"value": [{"@odata.type": "#microsoft.graph.group", "id": TEAM}]},
            )
        if path == f"/v1.0/groups/{TEAM}":
            return httpx.Response(200, json={"resourceProvisioningOptions": ["Team"]})
        if path.endswith("/drives"):
            return httpx.Response(
                200, json={"value": [{"id": "drive", "name": "Documents"}]}
            )
        return httpx.Response(
            200,
            json={"id": "file", "file": {}, "parentReference": {"driveId": "another"}},
        )

    with pytest.raises(TeamServiceError) as error:
        asyncio.run(
            create_team_service(handle_graph_request).download_team_file(
                TEAM, "drive", "file"
            )
        )
    assert error.value.code == TeamServiceErrorCode.ITEM_OUTSIDE_DRIVE


def test_dependency_returns_shared_sdk_service() -> None:
    app = FastAPI()
    app.state.team_service = create_team_service(
        lambda request: httpx.Response(200, json={"value": []})
    )
    request = Request({"type": "http", "app": app, "headers": []})
    assert get_team_service(request) is app.state.team_service


@pytest.mark.parametrize(
    ("status", "expected"), [(401, 401), (403, 403), (429, 429), (404, 404)]
)
def test_graph_errors_are_clear(status: int, expected: int) -> None:
    team = create_team_service(
        lambda request: httpx.Response(status, headers={"Retry-After": "7"})
    )
    with pytest.raises(TeamServiceError) as error:
        asyncio.run(team.list_joined_teams())
    request = Request({"type": "http", "app": FastAPI(), "headers": []})
    response = handle_team_service_error(request, error.value)
    assert response.status_code == expected
    if status == 429:
        assert response.headers["Retry-After"] == "7"


def test_rejects_external_pagination_url() -> None:
    team = create_team_service(
        lambda request: httpx.Response(
            200, json={"value": [], "@odata.nextLink": "https://example.com/leak"}
        )
    )
    with pytest.raises(TeamServiceError) as error:
        asyncio.run(team.list_joined_teams())
    assert error.value.code == TeamServiceErrorCode.INVALID_GRAPH_RESPONSE


def test_query_routes_accept_body_filters() -> None:
    methods = {
        route.path: route.methods
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    assert methods["/teams"] == {"QUERY"}
    assert methods["/teams/{team_id}/files"] == {"QUERY"}

    class StubService:
        team_name: str | None = None
        file_query: tuple[str, int, bool, str | None, str | None] | None = None

        async def list_joined_teams(self, name: str | None = None) -> list[TeamSummary]:
            self.team_name = name
            return [TeamSummary(id=TEAM, display_name="Research")]

        async def list_team_files(
            self,
            team_id: str,
            limit: int,
            recursive: bool,
            *,
            path: str | None = None,
            name: str | None = None,
        ) -> list[TeamFile]:
            self.file_query = team_id, limit, recursive, path, name
            return [
                TeamFile(
                    library="Documents",
                    drive_id="drive",
                    item_id="file",
                    name="budget.xlsx",
                    path="General/Reports/budget.xlsx",
                    type="file",
                    size=42,
                    web_url=None,
                )
            ]

    stub = StubService()
    teams = asyncio.run(
        list_joined_teams(
            TeamsQuery(name="research"), cast(TeamService, cast(object, stub))
        )
    )
    assert teams == [TeamResponse(id=TEAM, display_name="Research")]
    assert stub.team_name == "research"
    body = TeamFilesQuery(
        path="/General/Reports/", name="budget", recursive=True, limit=3
    )
    files = asyncio.run(
        list_team_files(TEAM, body, cast(TeamService, cast(object, stub)))
    )
    assert files == [
        TeamFileResponse(
            library="Documents",
            drive_id="drive",
            item_id="file",
            name="budget.xlsx",
            path="General/Reports/budget.xlsx",
            type="file",
            size=42,
            web_url=None,
        )
    ]
    assert stub.file_query == (
        TEAM,
        3,
        True,
        "General/Reports",
        "budget",
    )


def test_query_http_method_parses_json_body() -> None:
    app = FastAPI()
    app.include_router(router)
    stub = MagicMock()
    stub.list_joined_teams = AsyncMock(return_value=[])
    stub.list_team_files = AsyncMock(return_value=[])

    async def override_team_service() -> TeamService:
        return cast(TeamService, cast(object, stub))

    app.dependency_overrides[get_team_service] = override_team_service

    async def verify_query_responses() -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            teams = await client.request("QUERY", "/teams", json={"name": "research"})
            files = await client.request(
                "QUERY",
                f"/teams/{TEAM}/files",
                json={
                    "path": "Reports",
                    "name": "budget",
                    "limit": 2,
                    "recursive": True,
                },
            )
            wrong_method = await client.get("/teams")
        assert teams.status_code == 200
        assert files.status_code == 200
        assert wrong_method.status_code == 405

    asyncio.run(verify_query_responses())
    stub.list_joined_teams.assert_awaited_once_with(name="research")
    stub.list_team_files.assert_awaited_once_with(
        TEAM, 2, True, path="Reports", name="budget"
    )


def test_filters_files_before_limit_across_pages_and_folders() -> None:
    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1.0/me/memberOf":
            return httpx.Response(
                200,
                json={"value": [{"@odata.type": "#microsoft.graph.group", "id": TEAM}]},
            )
        if path == f"/v1.0/groups/{TEAM}":
            return httpx.Response(200, json={"resourceProvisioningOptions": ["Team"]})
        if path.endswith("/drives"):
            return httpx.Response(
                200, json={"value": [{"id": "drive", "name": "Documents"}]}
            )
        if path.endswith("/root/children") and "page" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "other", "name": "Other.txt", "file": {}}],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/drive/root/children?page=2",
                },
            )
        if path.endswith("/root/children"):
            return httpx.Response(
                200, json={"value": [{"id": "folder", "name": "Reports", "folder": {}}]}
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "first", "name": "Budget.xlsx", "file": {}},
                    {"id": "second", "name": "Budget-2026.xlsx", "file": {}},
                ]
            },
        )

    files = asyncio.run(
        create_team_service(handle_graph_request).list_team_files(
            TEAM, 1, True, path="REP", name="BUDGET"
        )
    )
    assert [(file.name, file.path) for file in files] == [
        ("Budget.xlsx", "Reports/Budget.xlsx")
    ]


def test_filters_team_names_case_insensitively() -> None:
    def handle_graph_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/me/memberOf":
            return httpx.Response(
                200,
                json={"value": [{"@odata.type": "#microsoft.graph.group", "id": TEAM}]},
            )
        return httpx.Response(
            200,
            json={
                "displayName": "Research Team",
                "resourceProvisioningOptions": ["Team"],
            },
        )

    assert [
        team.display_name
        for team in asyncio.run(
            create_team_service(handle_graph_request).list_joined_teams("SEARCH")
        )
    ] == ["Research Team"]
    assert (
        asyncio.run(
            create_team_service(handle_graph_request).list_joined_teams("finance")
        )
        == []
    )
