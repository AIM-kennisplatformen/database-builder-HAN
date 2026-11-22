import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import Response
from msgraph.graph_service_client import GraphServiceClient

from domain.ports.employees.employee_source import EmployeeStreamItem
from infrastructure.msgraph.employee_source import EmployeeSourceMsGraph


def _profile_response(member_id: str) -> Response:
    return Response(
        200,
        json={
            "id": member_id,
            "displayName": f"Employee {member_id}",
            "givenName": "Ada",
            "surname": "Lovelace",
            "mail": f"{member_id}@example.test",
            "companyName": "HAN",
            "department": "Research",
            "jobTitle": "Engineer",
            "skills": ["Python"],
            "responsibilities": ["Review"],
            "interests": ["Data"],
        },
    )


def _client(member_pages: list[Response]) -> MagicMock:
    client = MagicMock()
    members = client.groups.by_group_id.return_value.transitive_members.graph_user
    members.with_url.return_value.get = AsyncMock(side_effect=member_pages)

    def profile_request(member_id: str) -> MagicMock:
        request = MagicMock()
        request.get = AsyncMock(return_value=_profile_response(member_id))
        return request

    client.users.by_user_id.side_effect = profile_request
    return client


def test_employee_source_pages_members_and_maps_each_profile() -> None:
    second_url = "https://graph.microsoft.com/v1.0/groups/group/members?page=2"
    client = _client(
        [
            Response(
                200,
                json={
                    "@odata.count": 3,
                    "value": [{"id": "a"}, {"id": "b"}],
                    "@odata.nextLink": second_url,
                },
            ),
            Response(200, json={"value": [{"id": "c"}]}),
        ]
    )
    source = EmployeeSourceMsGraph(cast(GraphServiceClient, client), "group")

    async def collect() -> list[EmployeeStreamItem]:
        return [item async for item in source.stream_employees(None)]

    items = asyncio.run(collect())

    assert [item.employee.id for item in items] == ["a", "b", "c"]
    assert {(item.total, item.available) for item in items} == {(3, 3)}
    employee = items[0].employee
    assert employee.display_name == "Employee a"
    assert employee.employment_details is not None
    assert employee.employment_details.company_name == "HAN"
    assert employee.employment_details.department == "Research"
    assert employee.self_reported_details is not None
    assert employee.self_reported_details.skills == ["Python"]
    assert employee.self_reported_details.responsibilities == ["Review"]
    assert employee.self_reported_details.interests == ["Data"]
    members = client.groups.by_group_id.return_value.transitive_members.graph_user
    member_urls: list[str] = [
        cast(str, call.args[0]) for call in members.with_url.call_args_list
    ]
    assert urlparse(member_urls[0]).path.endswith(
        "/groups/group/transitiveMembers/microsoft.graph.user"
    )
    assert parse_qs(urlparse(member_urls[0]).query) == {
        "$count": ["true"],
        "$select": ["id,accountEnabled,userType"],
        "$filter": ["accountEnabled eq true and userType eq 'Member'"],
        "$top": ["999"],
    }
    assert member_urls[1] == second_url
    assert [call.args[0] for call in client.users.by_user_id.call_args_list] == [
        "a",
        "b",
        "c",
    ]


def test_employee_source_limit_stops_before_fetching_later_pages() -> None:
    second_url = "https://graph.microsoft.com/v1.0/groups/group/members?page=2"
    client = _client(
        [
            Response(
                200,
                json={
                    "@odata.count": 4,
                    "value": [{"id": "a"}, {"id": "b"}],
                    "@odata.nextLink": second_url,
                },
            ),
            Response(200, json={"value": [{"id": "c"}]}),
        ]
    )
    source = EmployeeSourceMsGraph(cast(GraphServiceClient, client), "group")

    async def collect() -> list[EmployeeStreamItem]:
        return [item async for item in source.stream_employees(1)]

    items = asyncio.run(collect())

    assert len(items) == 1
    assert (items[0].total, items[0].available) == (1, 4)
    members = client.groups.by_group_id.return_value.transitive_members.graph_user
    assert members.with_url.call_count == 1
    assert client.users.by_user_id.call_args.args == ("a",)


def test_employee_source_rejects_invalid_limit_without_graph_calls() -> None:
    client = _client([])
    source = EmployeeSourceMsGraph(cast(GraphServiceClient, client), "group")

    async def collect() -> None:
        async for _item in source.stream_employees(0):
            pass

    with pytest.raises(ValueError, match="limit must be positive"):
        asyncio.run(collect())
    client.groups.by_group_id.assert_not_called()


def test_employee_source_requires_count_on_first_page() -> None:
    client = _client([Response(200, json={"value": []})])
    source = EmployeeSourceMsGraph(cast(GraphServiceClient, client), "group")

    async def collect() -> None:
        async for _item in source.stream_employees(None):
            pass

    with pytest.raises(ValueError, match="did not return a group member count"):
        asyncio.run(collect())
