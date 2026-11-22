from main import app


def test_openapi_names_remain_stable_after_internal_renames() -> None:
    openapi = app.openapi()

    assert openapi["paths"]["/api/v1/employees/sync"]["post"]["operationId"] == (
        "sync_employees_endpoint_api_v1_employees_sync_post"
    )
    assert openapi["paths"]["/api/v1/documents/groups/drives"]["get"][
        "operationId"
    ] == "get_group_drives_endpoint_api_v1_documents_groups_drives_get"
    assert openapi["paths"]["/api/v1/documents/groups/{group_id}/metadata"]["post"][
        "operationId"
    ] == "ingest_group_metadata_endpoint_api_v1_documents_groups__group_id__metadata_post"

    schemas = openapi["components"]["schemas"]
    assert "EmployeesSyncRequestBody" in schemas
    assert "DriveMetadataSelectorBody" in schemas
    assert "MetadataIngestRequestBody" in schemas
    assert (
        openapi["paths"]["/api/v1/employees/sync"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/EmployeesSyncRequestBody"
    )
    assert (
        openapi["paths"]["/api/v1/documents/groups/{group_id}/metadata"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/MetadataIngestRequestBody"
    )
    assert schemas["MetadataIngestRequestBody"]["properties"]["include"]["items"][
        "$ref"
    ] == "#/components/schemas/DriveMetadataSelectorBody"
