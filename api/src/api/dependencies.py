"""Resolve application services for HTTP routes."""

from typing import Protocol, cast

from fastapi import FastAPI, Request

from domain.repositories.user_repository import UserRepository
from services.ms_graph_api_user_service import MsGraphApiUserService


class ApplicationState(Protocol):
    user_repository: UserRepository
    user_service: MsGraphApiUserService


def _state(request: Request) -> ApplicationState:
    return cast(ApplicationState, cast(object, cast(FastAPI, request.app).state))


def get_user_repository(request: Request) -> UserRepository:
    return _state(request).user_repository


def get_user_service(request: Request) -> MsGraphApiUserService:
    return _state(request).user_service
