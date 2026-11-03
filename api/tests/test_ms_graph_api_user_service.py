"""Pagination behavior for Microsoft Graph user retrieval."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Request, Response

from users.service.ms_graph_api_user_service import MsGraphApiUserService


def _response(url: str, body: dict) -> Response:
    return Response(200, json=body, request=Request("GET", url))


def _client(member_pages: list[Response], user_ids: list[str]) -> MagicMock:
    client = MagicMock()
    group = client.groups.by_group_id.return_value
    group.get = AsyncMock(
        return_value=_response(
            "https://graph.microsoft.com/v1.0/groups/group", {"id": "group"}
        )
    )
    members = group.transitive_members.graph_user
    members.with_url.return_value.get = AsyncMock(side_effect=member_pages)
    client.users.by_user_id.return_value.get = AsyncMock(
        side_effect=[
            _response(
                f"https://graph.microsoft.com/v1.0/users/{user_id}", {"id": user_id}
            )
            for user_id in user_ids
        ]
    )
    return client


def test_retrieves_users_across_pages() -> None:
    next_url = (
        "https://graph.microsoft.com/v1.0/groups/group/transitiveMembers?skiptoken=2"
    )
    client = _client(
        [
            _response(
                "https://graph.microsoft.com/v1.0/members",
                {"value": [{"id": "first"}], "@odata.nextLink": next_url},
            ),
            _response(next_url, {"value": [{"id": "second"}]}),
        ],
        ["first", "second"],
    )
    repository = MagicMock()
    repository.save_graph_snapshot = AsyncMock()
    service = MsGraphApiUserService(client, "group", repository)
    saved = asyncio.run(service.retrieve_users_from_external_source())

    assert saved == 2
    assert [
        call.args[0]["id"] for call in repository.save_graph_snapshot.call_args_list
    ] == [
        "first",
        "second",
    ]
    assert (
        client.groups.by_group_id.return_value.transitive_members.graph_user.with_url.call_count
        == 2
    )


def test_repeated_next_link_stops_pagination() -> None:
    client = _client([], [])
    members = client.groups.by_group_id.return_value.transitive_members.graph_user
    members.with_url.return_value.get = AsyncMock(
        side_effect=lambda **_: _response(
            "https://graph.microsoft.com/v1.0/members",
            {"value": [], "@odata.nextLink": members.with_url.call_args.args[0]},
        )
    )
    repository = MagicMock()
    repository.save_graph_snapshot = AsyncMock()
    service = MsGraphApiUserService(client, "group", repository)

    with pytest.raises(ValueError, match="repeated an @odata.nextLink"):
        asyncio.run(service.retrieve_users_from_external_source())

    assert members.with_url.call_count == 1


def test_limit_stops_before_fetching_another_profile() -> None:
    client = _client(
        [
            _response(
                "https://graph.microsoft.com/v1.0/members",
                {"value": [{"id": "first"}, {"id": "second"}]},
            )
        ],
        ["first", "second"],
    )
    repository = MagicMock()
    repository.save_graph_snapshot = AsyncMock()
    service = MsGraphApiUserService(client, "group", repository)
    saved = asyncio.run(service.retrieve_users_from_external_source(limit=1))

    assert saved == 1
    assert client.users.by_user_id.call_count == 1
    repository.save_graph_snapshot.assert_awaited_once()
