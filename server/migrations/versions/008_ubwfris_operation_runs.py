"""Allow UBWFRIS upload attempts in operator-visible operation history."""

from pathlib import Path

from alembic import op

revision = "008_ubwfris_operation_runs"
down_revision = "007_operation_runs"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    has_ubwfris_operations = connection.exec_driver_sql(
        """SELECT EXISTS (
               SELECT 1 FROM operation_runs
               WHERE kind IN ('ubwfris.ib', 'ubwfris.rha')
           )"""
    ).scalar_one()
    if has_ubwfris_operations:
        raise RuntimeError(
            "Cannot downgrade while UBWFRIS operation history exists; "
            "preserve the audit records or archive them first"
        )
    connection.exec_driver_sql(
        "ALTER TABLE operation_runs DROP CONSTRAINT operation_runs_kind_check"
    )
    connection.exec_driver_sql(
        """ALTER TABLE operation_runs ADD CONSTRAINT operation_runs_kind_check
           CHECK (kind IN ('employee.sync', 'metadata.ingest'))"""
    )
