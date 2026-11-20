import asyncio
from typing import final

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from api.routes.documents.metadata import router as metadata_router
from api.routes.documents.router import router as documents_router
from api.routes.employees.router import router as employees_router
from api.routes.employees.sync import router as employee_sync_router
from api.routes.ubwfris.snapshots.ingest import router as ubwfris_ingest_router
from api.routes.ubwfris.snapshots.router import router as ubwfris_router
from config import config
from dependencies import (
    get_current_graph_user_id,
    get_document_metadata_deleter,
    get_employee_data_deleter,
    get_ubwfris_ib_deleter,
    get_ubwfris_rha_deleter,
)

DELETE_PATHS = (
    "/api/v1/employees/sync",
    "/api/v1/documents/metadata",
    "/api/v1/ubwfris/snapshots/ib",
    "/api/v1/ubwfris/snapshots/rha",
)


@final
class DeleteStores:
    def __init__(self, *, result: bool = True) -> None:
        self.result: bool = result
        self.calls: list[str] = []

    async def delete_all_employee_data(self) -> bool:
        self.calls.append("employee")
        return self.result

    async def delete_all_metadata(self) -> bool:
        self.calls.append("metadata")
        return self.result

    async def delete_all_snapshots(self, row_type: str) -> bool:
        self.calls.append(f"ubwfris.{row_type}")
        return self.result


def _app(
    stores: DeleteStores,
    *,
    employee_enabled: bool,
    metadata_enabled: bool,
    ubwfris_enabled: bool,
) -> FastAPI:
    config.delete_endpoints.employee = employee_enabled
    config.delete_endpoints.metadata = metadata_enabled
    config.delete_endpoints.ubwfris = ubwfris_enabled
    _set_delete_schema(employee_sync_router, "/sync", employee_enabled)
    _set_delete_schema(metadata_router, "/metadata", metadata_enabled)
    _set_delete_schema(ubwfris_ingest_router, "/ib", ubwfris_enabled)
    _set_delete_schema(ubwfris_ingest_router, "/rha", ubwfris_enabled)

    app = FastAPI()
    app.include_router(employees_router)
    app.include_router(documents_router)
    app.include_router(ubwfris_router)
    app.dependency_overrides[get_current_graph_user_id] = lambda: "operator"
    app.dependency_overrides[get_employee_data_deleter] = lambda: stores
    app.dependency_overrides[get_document_metadata_deleter] = lambda: stores
    app.dependency_overrides[get_ubwfris_ib_deleter] = lambda: stores
    app.dependency_overrides[get_ubwfris_rha_deleter] = lambda: stores
    return app


def _set_delete_schema(router: APIRouter, path: str, enabled: bool) -> None:
    for route in router.routes:
        if (
            isinstance(route, APIRoute)
            and route.path == path
            and route.methods is not None
            and "DELETE" in route.methods
        ):
            route.include_in_schema = enabled
            return
    raise AssertionError(f"No DELETE route registered for {path}")


def _delete_all(app: FastAPI) -> list[httpx.Response]:
    async def send() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return [await client.delete(path) for path in DELETE_PATHS]

    return asyncio.run(send())


def test_disabled_delete_routes_are_absent_from_openapi_and_return_404() -> None:
    stores = DeleteStores()
    app = _app(
        stores,
        employee_enabled=False,
        metadata_enabled=False,
        ubwfris_enabled=False,
    )

    def unexpected_dependency() -> None:
        raise AssertionError("disabled endpoint resolved a protected dependency")

    app.dependency_overrides[get_current_graph_user_id] = unexpected_dependency
    app.dependency_overrides[get_employee_data_deleter] = unexpected_dependency
    app.dependency_overrides[get_document_metadata_deleter] = unexpected_dependency
    app.dependency_overrides[get_ubwfris_ib_deleter] = unexpected_dependency
    app.dependency_overrides[get_ubwfris_rha_deleter] = unexpected_dependency

    responses = _delete_all(app)

    assert [response.status_code for response in responses] == [404] * 4
    assert all(
        "delete" not in app.openapi()["paths"].get(path, {}) for path in DELETE_PATHS
    )
    assert stores.calls == []


def test_enabled_delete_endpoints_only_delete_their_own_data_type() -> None:
    stores = DeleteStores()

    app = _app(
        stores,
        employee_enabled=True,
        metadata_enabled=True,
        ubwfris_enabled=True,
    )
    responses = _delete_all(app)

    assert [response.status_code for response in responses] == [200] * 4
    assert [response.json() for response in responses] == [{"deleted": True}] * 4
    assert stores.calls == ["employee", "metadata", "ubwfris.ib", "ubwfris.rha"]
    assert all(
        "delete" in app.openapi()["paths"].get(path, {}) for path in DELETE_PATHS
    )


def test_each_configuration_flag_controls_only_its_delete_routes() -> None:
    cases = (
        (True, False, False, {DELETE_PATHS[0]}),
        (False, True, False, {DELETE_PATHS[1]}),
        (False, False, True, set(DELETE_PATHS[2:])),
    )
    for employee, metadata, ubwfris, expected in cases:
        app = _app(
            DeleteStores(),
            employee_enabled=employee,
            metadata_enabled=metadata,
            ubwfris_enabled=ubwfris,
        )
        enabled_paths = {
            path
            for path in DELETE_PATHS
            if "delete" in app.openapi()["paths"].get(path, {})
        }
        assert expected == enabled_paths


def test_enabled_delete_endpoints_return_conflict_when_store_refuses_reset() -> None:
    stores = DeleteStores(result=False)

    app = _app(
        stores,
        employee_enabled=True,
        metadata_enabled=True,
        ubwfris_enabled=True,
    )
    responses = _delete_all(app)

    assert [response.status_code for response in responses] == [409] * 4
    assert stores.calls == ["employee", "metadata", "ubwfris.ib", "ubwfris.rha"]
