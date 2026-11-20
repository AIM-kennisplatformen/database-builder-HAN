"""Stage and publish employee sync runs atomically.

Revision ID: 005_employee_sync_staging
Revises: 004_document_metadata_runs
"""

from pathlib import Path

from alembic import op

revision = "005_employee_sync_staging"
down_revision = "004_document_metadata_runs"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE employee_sync_staging")
    connection.exec_driver_sql("DROP TABLE employee_sync_runs")
