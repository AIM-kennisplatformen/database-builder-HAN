"""Store UBW FRIS IB and RHA rows by hash-identified workbook snapshot.

Revision ID: 002_ubwfris_snapshots
Revises: 001_initial
"""

from pathlib import Path

from alembic import op

revision = "002_ubwfris_snapshots"
down_revision = "001_initial"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP TABLE ubwfris_rha_rows")
    connection.exec_driver_sql("DROP TABLE ubwfris_ib_rows")
    connection.exec_driver_sql("DROP TABLE ubwfris_snapshots")
