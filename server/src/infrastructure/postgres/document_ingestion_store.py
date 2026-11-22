"""Persist candidate selection and per-document processing outcomes."""

from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from domain.models.documents.document_ingestion import (
    DocumentFileIngestSelection,
    DocumentProcessingCandidate,
    DocumentProcessingState,
    ProcessingOutcome,
)
from infrastructure.postgres.pool_store import PoolStorePostgres
from infrastructure.postgres.transactions import staging_transaction


class DocumentIngestionStoreMethods(PoolStorePostgres):
    async def query_visible_document_candidates(
        self, principal_id: str, drive_id: str, published_run_id: UUID
    ) -> tuple[DocumentProcessingCandidate, ...]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT observation.drive_id, observation.item_id, observation.name,
                          observation.relative_path, observation.content_version,
                          state.active_content_version, state.active_policy_version,
                          state.last_attempt_content_version,
                          state.last_attempt_policy_version, state.last_outcome,
                          state.drive_id,
                          COALESCE(state.deferred, FALSE)
                   FROM document_metadata_cursors AS cursor
                   JOIN document_metadata_runs AS run
                     ON run.id = cursor.published_run_id
                    AND run.principal_id = cursor.principal_id
                    AND run.drive_id = cursor.drive_id
                    AND run.selection = cursor.selection
                   JOIN document_metadata_observations AS observation
                     ON observation.principal_id = cursor.principal_id
                    AND observation.drive_id = cursor.drive_id
                    AND observation.selection = cursor.selection
                    AND observation.visible
                   JOIN document_ms_graph_sources AS source
                     ON source.drive_id = observation.drive_id
                    AND source.item_id = observation.item_id
                   LEFT JOIN document_processing_state AS state
                     ON state.drive_id = source.drive_id
                    AND state.item_id = source.item_id
                   WHERE cursor.principal_id = %s AND cursor.drive_id = %s
                     AND cursor.published_run_id = %s AND cursor.delta_link <> ''
                     AND run.status = 'complete'
                   ORDER BY observation.item_id""",
                (principal_id, drive_id, published_run_id),
            )
            rows = cast(list[tuple[object, ...]], await cursor.fetchall())
            if not rows:
                cursor = await connection.execute(
                    """SELECT EXISTS (
                           SELECT 1 FROM document_metadata_cursors AS cursor
                           JOIN document_metadata_runs AS run
                             ON run.id = cursor.published_run_id
                            AND run.principal_id = cursor.principal_id
                            AND run.drive_id = cursor.drive_id
                            AND run.selection = cursor.selection
                           WHERE cursor.principal_id = %s AND cursor.drive_id = %s
                             AND cursor.published_run_id = %s
                             AND cursor.delta_link <> '' AND run.status = 'complete'
                       )""",
                    (principal_id, drive_id, published_run_id),
                )
                validity_row = cast(tuple[bool] | None, await cursor.fetchone())
                if validity_row is None or not validity_row[0]:
                    raise ValueError("Published metadata snapshot is no longer current")
        candidates: list[DocumentProcessingCandidate] = []
        for row in rows:
            (
                row_drive,
                item_id,
                name,
                relative_path,
                content_version,
                active_content,
                active_policy,
                attempt_content,
                attempt_policy,
                outcome,
                state_drive_id,
                is_deferred,
            ) = row
            state = None
            if state_drive_id is not None:
                state = DocumentProcessingState(
                    cast(str | None, active_content),
                    cast(str | None, active_policy),
                    cast(str | None, attempt_content),
                    cast(str | None, attempt_policy),
                    ProcessingOutcome(cast(str, outcome)) if outcome else None,
                    bool(is_deferred),
                )
            candidates.append(
                DocumentProcessingCandidate(
                    cast(str, row_drive),
                    cast(str, item_id),
                    cast(str, name),
                    cast(str | None, relative_path),
                    cast(str | None, content_version),
                    state,
                )
            )
        return tuple(candidates)

    async def record_document_ingest_selections(
        self,
        operation_id: UUID,
        selections: tuple[DocumentFileIngestSelection, ...],
    ) -> None:
        async with staging_transaction(self._pool) as connection:
            for selection in selections:
                _ = await connection.execute(
                    """INSERT INTO document_ingest_file_runs (
                         operation_id, drive_id, item_id, relative_path,
                         source_content_version, processing_policy_version,
                         statuses, selection_reasons, outcome
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (operation_id, drive_id, item_id) DO UPDATE SET
                         relative_path = EXCLUDED.relative_path,
                         source_content_version = EXCLUDED.source_content_version,
                         processing_policy_version = EXCLUDED.processing_policy_version,
                         statuses = EXCLUDED.statuses,
                         selection_reasons = EXCLUDED.selection_reasons,
                         outcome = EXCLUDED.outcome, updated_at = now()""",
                    (
                        operation_id,
                        selection.drive_id,
                        selection.item_id,
                        selection.relative_path,
                        selection.source_content_version,
                        selection.processing_policy_version,
                        Jsonb([status.value for status in selection.statuses]),
                        Jsonb(list(selection.selection_reasons)),
                        selection.outcome,
                    ),
                )

    async def record_document_processing_outcome(
        self,
        drive_id: str,
        item_id: str,
        source_content_version: str | None,
        processing_policy_version: str,
        outcome: ProcessingOutcome | str,
        *,
        is_deferred: bool = False,
        failure_stage: str | None = None,
        failure_detail: str | None = None,
    ) -> None:
        outcome_value = (
            outcome.value if isinstance(outcome, ProcessingOutcome) else outcome
        )
        if outcome_value == "deferred":
            async with self._pool.connection() as connection:
                _ = await connection.execute(
                    """INSERT INTO document_processing_state
                         (drive_id, item_id, deferred, updated_at)
                       VALUES (%s, %s, TRUE, now())
                       ON CONFLICT (drive_id, item_id) DO UPDATE SET
                         deferred = TRUE, updated_at = now()""",
                    (drive_id, item_id),
                )
            return
        if outcome_value not in {item.value for item in ProcessingOutcome}:
            raise ValueError("Unsupported document processing outcome")
        active_update = outcome_value == ProcessingOutcome.SUCCEEDED.value
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                """INSERT INTO document_processing_state (
                     drive_id, item_id, active_content_version, active_policy_version,
                     last_attempt_content_version, last_attempt_policy_version,
                     last_outcome, deferred, failure_stage, failure_detail, updated_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (drive_id, item_id) DO UPDATE SET
                     active_content_version = CASE WHEN %s THEN EXCLUDED.active_content_version
                       ELSE document_processing_state.active_content_version END,
                     active_policy_version = CASE WHEN %s THEN EXCLUDED.active_policy_version
                       ELSE document_processing_state.active_policy_version END,
                     last_attempt_content_version = EXCLUDED.last_attempt_content_version,
                     last_attempt_policy_version = EXCLUDED.last_attempt_policy_version,
                     last_outcome = EXCLUDED.last_outcome,
                     deferred = %s,
                     failure_stage = EXCLUDED.failure_stage,
                     failure_detail = EXCLUDED.failure_detail,
                     updated_at = now()""",
                (
                    drive_id,
                    item_id,
                    source_content_version if active_update else None,
                    processing_policy_version if active_update else None,
                    source_content_version,
                    processing_policy_version,
                    outcome_value,
                    is_deferred,
                    failure_stage,
                    failure_detail,
                    active_update,
                    active_update,
                    is_deferred,
                ),
            )
