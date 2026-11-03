"""Create temporal Microsoft Graph source and override tables.

Revision ID: 001_initial
Revises:
"""

from pathlib import Path

from alembic import op

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL_FILE.read_text())


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql("DROP VIEW user_ms_graph_current")
    connection.exec_driver_sql("DROP FUNCTION user_ms_graph_at(timestamptz)")
    connection.exec_driver_sql("DROP TABLE user_overrides")
    connection.exec_driver_sql("DROP TABLE user_ms_graph_sources")
