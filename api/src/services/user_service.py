"""Retrieve Graph users and store their source snapshots."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar, cast
from urllib.parse import quote, urlencode, urlparse

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, ConfigDict, Field

from domain.models.user import User
from domain.repositories.user_repository import UserRepository


class GraphMember(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)

    id: str = Field(min_length=1)


class GraphMemberPage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)

    value: list[GraphMember]
    next_link: str | None = Field(default=None, alias="@odata.nextLink")


class GraphUserProfile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)

    id: str = Field(min_length=1)
    display_name: str | None = Field(default=None, alias="displayName")
    given_name: str | None = Field(default=None, alias="givenName")
    surname: str | None = None
    mail: str | None = None
    company_name: str | None = Field(default=None, alias="companyName")
    department: str | None = None
    job_title: str | None = Field(default=None, alias="jobTitle")
    skills: list[str] | None = None
    responsibilities: list[str] | None = None
    interests: list[str] | None = None


def user_from_graph_snapshot(profile: GraphUserProfile) -> User:
    return User(
        id=profile.id,
        display_name=profile.display_name,
        given_name=profile.given_name,
        surname=profile.surname,
        mail=profile.mail,
        company_name=profile.company_name,
        department=profile.department,
        job_title=profile.job_title,
        skills=profile.skills,
        responsibilities=profile.responsibilities,
        interests=profile.interests,
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


def _graph_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.microsoft.com"
        or not parsed.path.startswith("/v1.0/")
    ):
        raise ValueError("Graph returned a nextLink outside the v1.0 API")
    return url


class MsGraphApiUserService:
    def __init__(
        self, client: GraphServiceClient, group_id: str, repository: UserRepository
    ) -> None:
        self.client: GraphServiceClient = client
        self.group_id: str = group_id
        self.repository: UserRepository = repository

    async def _retrieve_detailed_user_snapshot(
        self, member: GraphMember
    ) -> GraphUserProfile:
        user_request = self.client.users.by_user_id(member.id)
        query = user_request.UserItemRequestBuilderGetQueryParameters(
            select=list(FIELDS)
        )
        response = cast(
            Response | None,
            await user_request.get(
                request_configuration=RequestConfiguration(
                    query_parameters=query,
                    options=[ResponseHandlerOption(NativeResponseHandler())],
                )
            ),
        )
        if response is None:
            raise ValueError("Graph returned no user profile")
        profile = GraphUserProfile.model_validate_json(response.content)
        if profile.id != member.id:
            raise ValueError("Graph profile id differs from group member id")
        return profile

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
    ) -> AsyncIterator[GraphUserProfile]:
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
            config = RequestConfiguration(
                query_parameters=members.GraphUserRequestBuilderGetQueryParameters(),
                options=[ResponseHandlerOption(NativeResponseHandler())],
            )
            config.headers.add("ConsistencyLevel", "eventual")
            page = cast(
                Response | None,
                await members.with_url(page_url).get(request_configuration=config),
            )
            if page is None:
                raise ValueError("Graph returned no member page")
            body = GraphMemberPage.model_validate_json(page.content)
            for member in body.value:
                yield await self._retrieve_detailed_user_snapshot(member)
                count += 1
                if limit is not None and count >= limit:
                    return

            if body.next_link:
                next_page_url = _graph_url(body.next_link)

    async def retrieve_han_employees_from_ms_graph(
        self, limit: int | None = None
    ) -> int:
        count = 0
        async for snapshot in self._retrieve_user_snapshots(limit):
            await self.repository.save_ms_graph_user(user_from_graph_snapshot(snapshot))
            count += 1
        return count
