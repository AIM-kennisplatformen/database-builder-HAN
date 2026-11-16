"""Fetch active HAN group members and their employee profiles from Graph."""

from collections.abc import AsyncIterator
from typing import ClassVar, cast, override
from urllib.parse import quote, urlencode

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, ConfigDict, Field

from domain.models.employee import (
    Employee,
    EmployeeSelfReportedDetails,
    EmploymentDetails,
)
from domain.ports.employees.employee_source import EmployeeSource, EmployeeStreamItem
from infrastructure.msgraph.base import validate_graph_url


class GraphGroupMember(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)

    id: str = Field(min_length=1)


class GraphGroupMemberPage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True)

    value: list[GraphGroupMember]
    count: int | None = Field(default=None, alias="@odata.count", ge=0)
    next_link: str | None = Field(default=None, alias="@odata.nextLink")


class GraphEmployeeProfile(BaseModel):
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


GRAPH_EMPLOYEE_PROFILE_FIELDS = (
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


class MsGraphEmployeeSource(EmployeeSource):
    def __init__(self, client: GraphServiceClient, group_id: str) -> None:
        self.client: GraphServiceClient = client
        self.group_id: str = group_id

    def _first_page_url(self, limit: int | None) -> str:
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

    async def _fetch_profile(self, member_id: str) -> Employee:
        request = self.client.users.by_user_id(member_id)
        query = request.UserItemRequestBuilderGetQueryParameters(
            select=list(GRAPH_EMPLOYEE_PROFILE_FIELDS)
        )
        response = cast(
            Response | None,
            await request.get(
                request_configuration=RequestConfiguration(
                    query_parameters=query,
                    options=[ResponseHandlerOption(NativeResponseHandler())],
                )
            ),
        )
        if response is None:
            raise ValueError("Graph returned no employee profile")

        profile = GraphEmployeeProfile.model_validate_json(response.content)
        if profile.id != member_id:
            raise ValueError("Graph profile id differs from group member id")
        return Employee(
            id=profile.id,
            display_name=profile.display_name,
            given_name=profile.given_name,
            surname=profile.surname,
            mail=profile.mail,
            employment_details=EmploymentDetails(
                company_name=profile.company_name,
                department=profile.department,
                job_title=profile.job_title,
            ),
            self_reported_details=EmployeeSelfReportedDetails(
                skills=profile.skills,
                responsibilities=profile.responsibilities,
                interests=profile.interests,
            ),
        )

    @override
    async def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[EmployeeStreamItem]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")

        members = self.client.groups.by_group_id(
            self.group_id
        ).transitive_members.graph_user
        next_page_url: str | None = self._first_page_url(limit)
        seen_urls: set[str] = set()
        processed = 0
        total: int | None = None
        available: int = 0

        while next_page_url is not None:
            page_url = next_page_url
            validate_graph_url(page_url)
            if page_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(page_url)

            config = RequestConfiguration(
                query_parameters=members.GraphUserRequestBuilderGetQueryParameters(),
                options=[ResponseHandlerOption(NativeResponseHandler())],
            )
            config.headers.add("ConsistencyLevel", "eventual")
            response = cast(
                Response | None,
                await members.with_url(page_url).get(request_configuration=config),
            )
            if response is None:
                raise ValueError("Graph returned no member page")

            page = GraphGroupMemberPage.model_validate_json(response.content)
            if total is None:
                if page.count is None:
                    raise ValueError("Graph did not return a group member count")
                available = page.count
                total = min(available, limit) if limit is not None else available
            for member in page.value:
                employee = await self._fetch_profile(member.id)
                processed += 1
                yield EmployeeStreamItem(employee, total, available)
                if limit is not None and processed >= limit:
                    return

            next_page_url = page.next_link
