"""Resolve application services for HTTP routes."""

from fastapi import Request

from domain.repositories.user_repository import UserRepository
from services.ms_graph_api_user_service import MsGraphApiUserService


def get_user_repository(request: Request) -> UserRepository:
    return request.app.state.user_repository


def get_user_service(request: Request) -> MsGraphApiUserService:
    return request.app.state.user_service
