"""Persist operator-visible employee and metadata operation status."""

from pathlib import Path

from alembic import op

revision = "007_operation_runs"
down_revision = "006_ubwfris_snapshot_completion"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "DROP INDEX document_metadata_runs_operation_id"
    )
    connection.exec_driver_sql(
        "ALTER TABLE document_metadata_runs DROP COLUMN operation_id"
    )
    connection.exec_driver_sql("DROP TABLE operation_runs")
