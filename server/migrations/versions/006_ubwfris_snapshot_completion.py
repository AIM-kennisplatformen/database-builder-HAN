"""Track incomplete and completed UBW FRIS snapshots.

Revision ID: 006_ubwfris_snapshot_completion
Revises: 005_employee_sync_staging
"""

from pathlib import Path

from alembic import op

revision = "006_ubwfris_snapshot_completion"
down_revision = "005_employee_sync_staging"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE ubwfris_rha_rows_staging")
    connection.exec_driver_sql("DROP TABLE ubwfris_ib_rows_staging")
    connection.exec_driver_sql("ALTER TABLE ubwfris_snapshots DROP COLUMN status")
