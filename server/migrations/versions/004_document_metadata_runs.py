"""Stage and publish per-principal Graph metadata runs.

Revision ID: 004_document_metadata_runs
Revises: 003_document_metadata
"""

from pathlib import Path

from alembic import op

revision = "004_document_metadata_runs"
down_revision = "003_document_metadata"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP VIEW published_document_catalog")
    connection.exec_driver_sql("DROP TABLE document_metadata_observations")
    connection.exec_driver_sql("DROP TABLE document_metadata_staging")
    connection.exec_driver_sql("DROP TABLE document_metadata_cursors")
    connection.exec_driver_sql("DROP TABLE document_metadata_runs")
