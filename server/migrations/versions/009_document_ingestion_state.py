"""Persist document ingestion selections and processing state.

Revision ID: 009_document_ingestion_state
Revises: 008_ubwfris_operation_runs
"""

from pathlib import Path

from alembic import op

revision = "009_document_ingestion_state"
down_revision = "008_ubwfris_operation_runs"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE document_ingest_file_runs")
    connection.exec_driver_sql("DROP TABLE document_processing_state")
    connection.exec_driver_sql(
        "ALTER TABLE document_metadata_observations DROP COLUMN content_version"
    )
    connection.exec_driver_sql(
        "ALTER TABLE document_metadata_observations DROP COLUMN name"
    )
    connection.exec_driver_sql(
        "ALTER TABLE document_metadata_observations DROP COLUMN relative_path"
    )
    connection.exec_driver_sql(
        "ALTER TABLE document_metadata_staging DROP COLUMN relative_path"
    )
