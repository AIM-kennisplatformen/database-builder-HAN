"""Shared Microsoft Graph response and pagination handling."""

from urllib.parse import urlparse

from httpx import Response
from pydantic import BaseModel, ValidationError

from domain.exceptions.metadata_ingest_errors import MetadataSourceError


class GraphError(BaseModel):
    code: str | None = None
    message: str | None = None


class GraphErrorResponse(BaseModel):
    error: GraphError


def validate_graph_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.microsoft.com"
        or not parsed.path.startswith("/v1.0/")
    ):
        raise ValueError("Graph returned a link outside the v1.0 API")


def raise_for_graph_error(response: Response, default_detail: str) -> None:
    if not response.is_error:
        return
    try:
        error = GraphErrorResponse.model_validate_json(response.content).error
    except ValidationError:
        detail = default_detail
    else:
        detail = error.message or error.code or "Graph error"
    raise MetadataSourceError(response.status_code, detail)
