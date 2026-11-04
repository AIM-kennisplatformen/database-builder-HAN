"""Authenticate to Graph, retrieve users, and store their source snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TypedDict, cast
from urllib.parse import quote, urlencode, urlparse

from azure.identity import InteractiveBrowserCredential
from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient

from domain.models.user import User
from domain.repositories.user_repository import UserRepository


class GraphUserSnapshot(TypedDict):
    id: str
    response_body: dict[str, object]


def _optional_string(profile: dict[str, object], field: str) -> str | None:
    value = profile.get(field)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"Graph profile field {field} must be a string")
    return value


def _optional_strings(profile: dict[str, object], field: str) -> list[str] | None:
    value = profile.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in cast(list[object], value)
    ):
        raise TypeError(f"Graph profile field {field} must be a string list")
    return cast(list[str], value)


def user_from_graph_snapshot(snapshot: GraphUserSnapshot) -> User:
    profile = snapshot["response_body"]
    user = User(
        id=snapshot["id"],
        display_name=_optional_string(profile, "displayName"),
        given_name=_optional_string(profile, "givenName"),
        surname=_optional_string(profile, "surname"),
        mail=_optional_string(profile, "mail"),
        company_name=_optional_string(profile, "companyName"),
        department=_optional_string(profile, "department"),
        job_title=_optional_string(profile, "jobTitle"),
        skills=_optional_strings(profile, "skills"),
        responsibilities=_optional_strings(profile, "responsibilities"),
        interests=_optional_strings(profile, "interests"),
    )
    return user


class InteractiveBrowserAuth:
    def __init__(self, tenant_id: str, client_id: str, redirect_uri: str) -> None:
        self.tenant_id: str = tenant_id
        self.client_id: str = client_id
        self.redirect_uri: str = redirect_uri

    def create_credential(self) -> InteractiveBrowserCredential:
        return InteractiveBrowserCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            prompt="select_account",
        )


FIELDS = (
    "id",
    "displayName",
    "givenName",
    "surname",
    "mail",
    "companyName",
    "department",
    "jobTitle",
    "skills",
    "responsibilities",
    "interests",
)


def _raw_config[QueryParameters](
    query_parameters: QueryParameters, *, advanced: bool = False
) -> RequestConfiguration[QueryParameters]:
    config = RequestConfiguration(
        query_parameters=query_parameters,
        options=[ResponseHandlerOption(NativeResponseHandler())],
    )
    if advanced:
        config.headers.add("ConsistencyLevel", "eventual")
    return config


def _graph_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.microsoft.com"
        or not parsed.path.startswith("/v1.0/")
    ):
        raise ValueError("Graph returned a nextLink outside the v1.0 API")
    return url


def _raw_response(value: object) -> Response:
    """Validate the HTTP response returned by the native response handler."""
    if not isinstance(value, Response):
        raise TypeError("Graph did not return a native HTTP response")
    return value


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Graph did not return a JSON object")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise TypeError("Graph did not return a JSON object")
    return cast(dict[str, object], cast(object, mapping))


class MsGraphApiUserService:
    def __init__(
        self, client: GraphServiceClient, group_id: str, repository: UserRepository
    ) -> None:
        self.client: GraphServiceClient = client
        self.group_id: str = group_id
        self.repository: UserRepository = repository

    async def _retrieve_detailed_user_snapshot(
        self, member: dict[str, object]
    ) -> GraphUserSnapshot:
        user_id = member.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Graph member has no id")
        user_request = self.client.users.by_user_id(user_id)
        query = user_request.UserItemRequestBuilderGetQueryParameters(
            select=list(FIELDS)
        )
        response = _raw_response(
            await user_request.get(request_configuration=_raw_config(query))
        )
        profile = _json_object(cast(object, response.json()))
        if profile.get("id") != user_id:
            raise ValueError("Graph profile id differs from group member id")
        return {
            "id": user_id,
            "response_body": profile,
        }

    def _first_member_page_url(self, limit: int | None) -> str:
        query = urlencode(
            {
                "$count": "true",
                "$select": "id,accountEnabled,userType",
                "$filter": "accountEnabled eq true and userType eq 'Member'",
                "$top": min(limit or 999, 999),
            }
        )
        return (
            "https://graph.microsoft.com/v1.0/groups/"
            f"{quote(self.group_id, safe='')}/transitiveMembers/microsoft.graph.user?{query}"
        )

    async def _retrieve_user_snapshots(
        self, limit: int | None = None
    ) -> AsyncIterator[GraphUserSnapshot]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")

        members = self.client.groups.by_group_id(
            self.group_id
        ).transitive_members.graph_user
        next_page_url: str | None = self._first_member_page_url(limit)
        seen_urls: set[str] = set()
        count = 0
        while next_page_url:
            page_url = next_page_url
            next_page_url = None
            if page_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(page_url)
            page = _raw_response(
                await members.with_url(page_url).get(
                    request_configuration=_raw_config(
                        members.GraphUserRequestBuilderGetQueryParameters(),
                        advanced=True,
                    )
                )
            )
            body = _json_object(cast(object, page.json()))
            values = body.get("value")
            if not isinstance(values, list):
                raise TypeError("Graph member page has no value list")
            for member in cast(list[object], values):
                yield await self._retrieve_detailed_user_snapshot(_json_object(member))
                count += 1
                if limit is not None and count >= limit:
                    return

            candidate_url = body.get("@odata.nextLink")
            if candidate_url is not None and not isinstance(candidate_url, str):
                raise TypeError("Graph nextLink must be a string")
            if candidate_url:
                next_page_url = _graph_url(candidate_url)

    async def retrieve_users_from_external_source(
        self, limit: int | None = None
    ) -> int:
        count = 0
        async for snapshot in self._retrieve_user_snapshots(limit):
            await self.repository.save_graph_snapshot(user_from_graph_snapshot(snapshot))
            count += 1
        return count
