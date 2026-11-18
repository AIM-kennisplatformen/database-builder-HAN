"""Read-only HAN tenant check for the Graph Search drive-item identity join.

Run from server/ with a search phrase known to match accessible files:
    PYTHONPATH=src uv run python scripts/validate_graph_search_identity.py "research"

Only aggregate counts or an error status are printed; file names, IDs, and tokens stay out of output.
"""

import argparse
import asyncio
from typing import cast

from azure.identity import InteractiveBrowserCredential
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.entity_type import EntityType
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.search_query import SearchQuery
from msgraph.generated.models.search_request import SearchRequest
from msgraph.generated.search.query.query_post_request_body import QueryPostRequestBody
from msgraph.graph_service_client import GraphServiceClient

from config import config


async def validate(query: str) -> int:
    if not config.graph_client_id or not config.graph_tenant_id:
        raise RuntimeError("GRAPH_CLIENT_ID and GRAPH_TENANT_ID must be configured")

    credential = InteractiveBrowserCredential(
        tenant_id=config.graph_tenant_id,
        client_id=config.graph_client_id,
        redirect_uri=config.graph_redirect_uri,
    )
    try:
        client = GraphServiceClient(credentials=credential, scopes=[])
        try:
            response = await client.search.query.post(
                QueryPostRequestBody(
                    requests=[
                        SearchRequest(
                            entity_types=[EntityType.DriveItem],
                            query=SearchQuery(query_string=query),
                            from_=0,
                            size=50,
                        )
                    ]
                )
            )
        except ODataError as exc:
            print(f"Graph Search failed with HTTP {exc.response_status_code}")
            return 2
        hits = [
            hit
            for result in (response.value if response else []) or []
            for container in result.hits_containers or []
            for hit in container.hits or []
        ]
        exact = 0
        direct_match = 0
        missing = 0
        for hit in hits:
            resource = hit.resource
            if not isinstance(resource, DriveItem):
                missing += 1
                continue
            drive_id = (
                resource.parent_reference.drive_id
                if resource.parent_reference
                else None
            )
            item_id = resource.id
            if not drive_id or not item_id:
                missing += 1
                continue
            exact += 1
            try:
                item = (
                    await client.drives.by_drive_id(drive_id)
                    .items.by_drive_item_id(item_id)
                    .get()
                )
            except ODataError:
                continue
            if (
                item
                and item.id == item_id
                and item.parent_reference
                and item.parent_reference.drive_id == drive_id
            ):
                direct_match += 1
        print(
            f"hits={len(hits)} identity_present={exact} "
            + f"direct_match={direct_match} missing_identity={missing}"
        )
        return 0 if hits and exact == direct_match == len(hits) else 1
    finally:
        credential.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "query", help="A phrase known to match accessible HAN files"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(validate(cast(str, args.query))))
