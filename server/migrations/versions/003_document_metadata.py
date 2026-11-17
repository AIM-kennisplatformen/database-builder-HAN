"""Persist metadata for Microsoft Graph documents.

Revision ID: 003_document_metadata
Revises: 002_ubwfris_snapshots
"""

from pathlib import Path

from alembic import op

revision = "003_document_metadata"
down_revision = "002_ubwfris_snapshots"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE document_ms_graph_sources")
