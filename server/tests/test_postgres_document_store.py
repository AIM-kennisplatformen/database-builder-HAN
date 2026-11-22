import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    AuditActor,
    AuditEntry,
    Document,
    DocumentId,
    DocumentMetadata,
    FileHashes,
    FileMetadata,
)
from domain.models.documents.document_ingestion import (
    DocumentIngestFileSelection,
    ProcessingStatus,
)
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
)
from domain.ports.documents.document_store import MetadataRunPublication
from infrastructure.postgres.document_store import PostgresDocumentStore


def test_document_store_upserts_full_source_metadata() -> None:
    document = Document(
        _id=DocumentId("drive", "item"),
        file_metadata=FileMetadata(
            name="Report.pdf",
            mime_type="application/pdf",
            size_bytes=42,
            content_version=ContentVersion("v2"),
            source_url="https://example.test/report",
            hashes=FileHashes(sha1="sha1", sha256="sha256", quick_xor="xor"),
        ),
        document_metadata=DocumentMetadata(
            created=AuditEntry(
                timestamp=datetime(2026, 1, 1, 12, tzinfo=UTC),
                actor=AuditActor("Ada"),
            ),
            updated=AuditEntry(
                timestamp=datetime(2026, 3, 3, 12, tzinfo=UTC),
                actor=AuditActor("Bob"),
            ),
        ),
    )
    connection = MagicMock()
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).save_document(document)
    )

    sql, values = connection.execute.call_args.args
    assert "ON CONFLICT (drive_id, item_id) DO UPDATE" in sql
    assert values[:10] == (
        "drive",
        "item",
        "Report.pdf",
        "application/pdf",
        42,
        "v2",
        "https://example.test/report",
        "sha1",
        "sha256",
        "xor",
    )
    assert isinstance(values[10], Jsonb)
    assert values[10].obj["created"]["actor"]["display_name"] == "Ada"
    assert values[10].obj["created"]["timestamp"] == "2026-01-01T12:00:00Z"
    assert values[10].obj["updated"]["actor"]["display_name"] == "Bob"
    assert values[10].obj["updated"]["timestamp"] == "2026-03-03T12:00:00Z"
    assert "authors" not in values[10].obj
    assert "update_history" not in values[10].obj


def test_startup_recovery_discards_only_staged_rows_and_interrupts_running_runs() -> (
    None
):
    connection = MagicMock()
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).interrupt_metadata_runs()
    )

    assert connection.execute.await_count == 3
    (delete_sql,) = connection.execute.await_args_list[0].args
    (update_sql,) = connection.execute.await_args_list[1].args
    (operation_update_sql,) = connection.execute.await_args_list[2].args
    assert "DELETE FROM document_metadata_staging" in delete_sql
    assert "run.status = 'running'" in delete_sql
    assert "UPDATE document_metadata_runs SET status = 'interrupted'" in update_sql
    assert "WHERE status = 'running'" in update_sql
    assert "'refreshing'" in operation_update_sql
    assert "'interrupted'" in operation_update_sql
    assert "jsonb_object_agg" in operation_update_sql
    assert "ELSE outcome.value" in operation_update_sql
    assert "UPDATE operation_runs SET status = 'interrupted'" in operation_update_sql
    assert "kind = 'metadata.ingest'" in operation_update_sql
    assert "document_ms_graph_sources" not in delete_sql + update_sql
    assert "document_metadata_observations" not in delete_sql + update_sql


def test_candidate_query_uses_current_published_run_and_visible_operator_observations() -> None:
    published_run_id = uuid4()
    result_cursor = MagicMock()
    result_cursor.fetchall = AsyncMock(
        return_value=[
            (
                "drive",
                "item",
                "Report.pdf",
                "reports/Report.pdf",
                "v2",
                "v1",
                "policy-v1",
                "v1",
                "policy-v1",
                None,
                "drive",
                False,
            )
        ]
    )
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=result_cursor)
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    candidates = asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).query_visible_document_candidates(
            "operator", "drive", published_run_id
        )
    )

    assert len(candidates) == 1
    assert candidates[0].item_id == "item"
    assert candidates[0].relative_path == "reports/Report.pdf"
    assert candidates[0].content_version == "v2"
    assert candidates[0].state is not None
    assert candidates[0].state.active_content_version == "v1"
    sql, parameters = connection.execute.await_args.args
    assert "cursor.published_run_id = %s" in sql
    assert "cursor.principal_id = %s AND cursor.drive_id = %s" in sql
    assert "run.selection = cursor.selection" in sql
    assert "observation.principal_id = cursor.principal_id" in sql
    assert "observation.selection = cursor.selection" in sql
    assert "observation.visible" in sql
    assert "observation.run_id" not in sql
    assert "cursor.delta_link <> ''" in sql
    assert parameters == ("operator", "drive", published_run_id)


@pytest.mark.parametrize("current_snapshot", [False, True])
def test_empty_candidate_result_requires_the_published_snapshot_to_remain_current(
    current_snapshot: bool,
) -> None:
    published_run_id = uuid4()
    candidates_cursor = MagicMock()
    candidates_cursor.fetchall = AsyncMock(return_value=[])
    validity_cursor = MagicMock()
    validity_cursor.fetchone = AsyncMock(
        return_value=(current_snapshot,) if current_snapshot else (False,)
    )
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[candidates_cursor, validity_cursor])
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    async def query() -> tuple[object, ...]:
        return await PostgresDocumentStore(
            cast(AsyncConnectionPool, pool)
        ).query_visible_document_candidates("operator", "drive", published_run_id)

    if current_snapshot:
        assert asyncio.run(query()) == ()
    else:
        with pytest.raises(ValueError, match="Published metadata snapshot is no longer current"):
            asyncio.run(query())

    assert connection.execute.await_count == 2
    validation_sql, validation_parameters = connection.execute.await_args_list[1].args
    assert "SELECT EXISTS" in validation_sql
    assert "cursor.published_run_id = %s" in validation_sql
    assert "run.status = 'complete'" in validation_sql
    assert validation_parameters == ("operator", "drive", published_run_id)


def test_selected_file_run_records_are_persisted_with_statuses_and_reasons() -> None:
    operation_id = uuid4()
    connection = MagicMock()
    connection.execute = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction.return_value = transaction
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = connection_context
    selection = DocumentIngestFileSelection(
        drive_id="drive",
        item_id="item",
        relative_path="reports/Report.pdf",
        source_content_version="v2",
        processing_policy_version="policy-v1",
        statuses=(ProcessingStatus.NEW, ProcessingStatus.DEFERRED),
        selection_reasons=("reports;extensions=pdf;statuses=deferred,new",),
    )

    asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).record_document_ingest_selections(
            operation_id, (selection,)
        )
    )

    sql, parameters = connection.execute.await_args.args
    assert "INSERT INTO document_ingest_file_runs" in sql
    assert "ON CONFLICT (operation_id, drive_id, item_id) DO UPDATE" in sql
    assert parameters[:6] == (
        operation_id,
        "drive",
        "item",
        "reports/Report.pdf",
        "v2",
        "policy-v1",
    )
    assert parameters[6].obj == ["new", "deferred"]
    assert parameters[7].obj == ["reports;extensions=pdf;statuses=deferred,new"]
    assert parameters[8] == "selected"


def test_metadata_selection_serialization_matches_persisted_cursor_key() -> None:
    expected = (
        '{"exclude_rules": [], "include_rules": [{"extensions": ["pdf"], '
        '"path": "reports"}]}'
    )
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=(expected, "saved-cursor"))
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[cursor, None])
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    run = asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).begin_metadata_run(
            "operator",
            "drive",
            MetadataSelection(
                include_rules=(MetadataPathRule("reports", ("pdf",)),),
                exclude_rules=(),
            ),
        )
    )

    assert run.same_selection
    assert run.cursor == "saved-cursor"
    _, insert_values = connection.execute.await_args_list[1].args
    assert insert_values[3] == expected


def test_legacy_metadata_cursor_forces_baseline_before_candidate_query() -> None:
    expected = (
        '{"exclude_rules": [], "include_rules": [{"extensions": ["pdf"], '
        '"path": "reports"}]}'
    )
    cursor = MagicMock()
    cursor.fetchone = AsyncMock(return_value=(expected, ""))
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=[cursor, None])
    pool = MagicMock()
    pool.connection.return_value.__aenter__.return_value = connection

    run = asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).begin_metadata_run(
            "operator",
            "drive",
            MetadataSelection(
                include_rules=(MetadataPathRule("reports", ("pdf",)),),
                exclude_rules=(),
            ),
        )
    )

    assert run.same_selection
    assert run.cursor is None
    migration = Path("migrations/versions/009_document_ingestion_state.sql").read_text()
    assert "SET name = source.name, content_version = source.content_version" in migration
    assert "UPDATE document_metadata_cursors SET delta_link = '';" in migration


@pytest.mark.parametrize("fail_outcome_update", [False, True])
def test_single_drive_publication_and_run_outcome_share_one_transaction(
    fail_outcome_update: bool,
) -> None:
    operation_id = uuid4()
    ownership_cursor = MagicMock()
    ownership_cursor.fetchone = AsyncMock(
        return_value=("drive", operation_id)
    )
    run_cursor = MagicMock()
    run_cursor.fetchone = AsyncMock(return_value=("operator", "drive", "selection"))
    outcome_cursor = MagicMock()
    outcome_cursor.fetchone = AsyncMock(
        return_value=None if fail_outcome_update else ("operation",)
    )
    execute_results: list[object] = [
        ownership_cursor,
        run_cursor,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    execute_results.append(outcome_cursor)
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=execute_results)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction.return_value = transaction
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = connection_context
    publication = MetadataRunPublication(uuid4(), "cursor", True, True)

    async def publish() -> None:
        await PostgresDocumentStore(cast(AsyncConnectionPool, pool)).publish_metadata_drive(
            operation_id,
            "drive",
            publication,
            {"status": "published", "metadata_run_id": str(publication.run_id)},
        )

    if fail_outcome_update:
        with pytest.raises(ValueError, match="Combined metadata operation is not active"):
            asyncio.run(publish())
        transaction.__aexit__.assert_awaited_once()
        assert transaction.__aexit__.await_args.args[0] is ValueError
    else:
        asyncio.run(publish())
        transaction.__aexit__.assert_awaited_once_with(None, None, None)

    sql_statements = [call.args[0] for call in connection.execute.await_args_list]
    assert any("INSERT INTO document_ms_graph_sources" in statement for statement in sql_statements)
    assert any("INSERT INTO document_metadata_cursors" in statement for statement in sql_statements)
    outcome_sql = sql_statements[-1]
    assert "UPDATE operation_runs" in outcome_sql
    assert "drive_outcomes" in outcome_sql
    assert "status = 'complete'" not in outcome_sql
    assert connection.execute.await_count == 9


def test_failing_parent_run_merges_summary_and_preserves_drive_outcomes() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = connection_context
    operation_id = uuid4()

    asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).fail_group_operation(
            operation_id,
            "request_interrupted",
            interrupted=True,
            summary={"processed": 1},
        )
    )

    sql, parameters = connection.execute.await_args.args
    assert "summary = summary || COALESCE(%s::jsonb, '{}'::jsonb)" in sql
    assert parameters[0] == "interrupted"
    assert parameters[1] == "request_interrupted"
    assert parameters[2] == '{"processed": 1}'
    assert parameters[3] == operation_id


def test_drive_outcome_fallback_does_not_replace_terminal_outcome() -> None:
    outcome_cursor = MagicMock()
    outcome_cursor.fetchone = AsyncMock(return_value=("operation",))
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=outcome_cursor)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    connection.transaction.return_value = transaction
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = connection_context
    operation_id = uuid4()

    asyncio.run(
        PostgresDocumentStore(cast(AsyncConnectionPool, pool)).record_metadata_drive_outcome(
            operation_id,
            "drive",
            {"status": "failed", "failure_code": "metadata.publish_failed"},
        )
    )

    sql, parameters = connection.execute.await_args.args
    assert "CASE WHEN summary->'drive_outcomes'->%s->>'status' IN" in sql
    assert "('published', 'failed', 'skipped', 'interrupted')" in sql
    assert "THEN summary->'drive_outcomes'->%s" in sql
    assert parameters[0:3] == ("drive", "drive", "drive")
    assert parameters[3] == '{"status": "failed", "failure_code": "metadata.publish_failed"}'
    assert parameters[4] == operation_id
