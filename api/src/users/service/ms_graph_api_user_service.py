"""Authenticate to Graph, retrieve users, and store their source snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, TypedDict
from urllib.parse import quote, urlencode, urlparse

from azure.core.credentials import TokenCredential
from azure.identity import InteractiveBrowserCredential
from fastapi import Request
from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.generated.groups.item.group_item_request_builder import (
    GroupItemRequestBuilder,
)
from msgraph.generated.users.item.user_item_request_builder import (
    UserItemRequestBuilder,
)
from msgraph.graph_service_client import GraphServiceClient

from users.repository.user_repository import UserRepository
from users.user import User


class GraphAuthStrategy(Protocol):
    """Create a credential for one Graph client lifetime."""

    def create_credential(self) -> ClosableTokenCredential: ...


class ClosableTokenCredential(TokenCredential, Protocol):
    """A Graph credential whose resources can be released at shutdown."""

    def close(self) -> None: ...


class InteractiveBrowserAuth:
    def __init__(self, tenant_id: str, client_id: str, redirect_uri: str) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.redirect_uri = redirect_uri

    def create_credential(self) -> InteractiveBrowserCredential:
        return InteractiveBrowserCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            prompt="select_account",
        )


def get_auth_strategy(request: Request) -> GraphAuthStrategy:
    return request.app.state.auth_strategy


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


class GraphUserSnapshot(TypedDict):
    id: str
    response_body: dict[str, Any]
    request_url: str


def user_from_snapshot(snapshot: GraphUserSnapshot) -> User:
    profile = snapshot["response_body"]
    return User(
        id=snapshot["id"],
        display_name=profile.get("displayName"),
        given_name=profile.get("givenName"),
        surname=profile.get("surname"),
        mail=profile.get("mail"),
        company_name=profile.get("companyName"),
        department=profile.get("department"),
        job_title=profile.get("jobTitle"),
        skills=profile.get("skills"),
        responsibilities=profile.get("responsibilities"),
        interests=profile.get("interests"),
    )


def _raw_config(
    query_parameters: Any = None, *, advanced: bool = False
) -> RequestConfiguration:
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


class MsGraphUserSource:
    def __init__(self, client: GraphServiceClient, group_id: str) -> None:
        self.client = client
        self.group_id = group_id

    async def users(self, limit: int | None = None) -> AsyncIterator[User]:
        async for snapshot in self.snapshots(limit):
            yield user_from_snapshot(snapshot)

    async def snapshots(
        self, limit: int | None = None
    ) -> AsyncIterator[GraphUserSnapshot]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")

        group_query = GroupItemRequestBuilder.GroupItemRequestBuilderGetQueryParameters(
            select=["id"]
        )
        group_response = _raw_response(
            await self.client.groups.by_group_id(self.group_id).get(
                request_configuration=_raw_config(group_query)
            )
        )
        canonical_id = group_response.json().get("id")
        if not canonical_id:
            raise ValueError("Graph group has no id")

        members = self.client.groups.by_group_id(
            canonical_id
        ).transitive_members.graph_user
        query = urlencode(
            {
                "$count": "true",
                "$select": "id,accountEnabled,userType",
                "$filter": "accountEnabled eq true and userType eq 'Member'",
                "$top": min(limit or 999, 999),
            }
        )
        first_url = (
            "https://graph.microsoft.com/v1.0/groups/"
            f"{quote(canonical_id, safe='')}/transitiveMembers/microsoft.graph.user?{query}"
        )
        page = _raw_response(
            await members.with_url(first_url).get(
                request_configuration=_raw_config(advanced=True)
            )
        )
        seen_urls: set[str] = set()
        count = 0
        while True:
            body = page.json()
            values = body.get("value")
            if not isinstance(values, list):
                raise TypeError("Graph member page has no value list")
            for member in values:
                user_id = member.get("id")
                if not user_id:
                    raise ValueError("Graph member has no id")
                query = UserItemRequestBuilder.UserItemRequestBuilderGetQueryParameters(
                    select=list(FIELDS)
                )
                profile_response = _raw_response(
                    await self.client.users.by_user_id(user_id).get(
                        request_configuration=_raw_config(query)
                    )
                )
                profile = profile_response.json()
                if profile.get("id") != user_id:
                    raise ValueError("Graph profile id differs from group member id")
                yield {
                    "id": user_id,
                    "response_body": profile,
                    "request_url": str(profile_response.request.url),
                }
                count += 1
                if limit is not None and count >= limit:
                    return

            next_url = body.get("@odata.nextLink")
            if not next_url:
                return
            next_url = _graph_url(next_url)
            if next_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(next_url)
            page = _raw_response(
                await members.with_url(next_url).get(
                    request_configuration=_raw_config(advanced=True)
                )
            )


class MsGraphApiUserService:
    def __init__(self, graph: MsGraphUserSource, repository: UserRepository) -> None:
        self.graph = graph
        self.repository = repository

    async def retrieve_users_from_external_source(
        self, limit: int | None = None
    ) -> int:
        count = 0
        async for snapshot in self.graph.snapshots(limit):
            await self.repository.save_graph_snapshot(snapshot)
            count += 1
        return count
