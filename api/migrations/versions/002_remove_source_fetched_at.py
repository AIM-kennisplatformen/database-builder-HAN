"""Remove redundant fetch time and retain a reversible data backfill.

Revision ID: 002_remove_source_fetched_at
Revises: 001_initial
"""

from alembic import op

revision = "002_remove_source_fetched_at"
down_revision = "001_initial"
branch_labels = None
depends_on = None

SQL = """
ALTER TABLE user_ms_graph_sources
    DROP COLUMN IF EXISTS source_fetched_at;

ALTER TABLE user_ms_graph_sources
    DROP CONSTRAINT IF EXISTS user_ms_graph_sources_check,
    DROP CONSTRAINT IF EXISTS user_ms_graph_sources_presence_check;

ALTER TABLE user_ms_graph_sources
    ADD CONSTRAINT user_ms_graph_sources_presence_check CHECK (
        (is_present AND response_body IS NOT NULL
            AND jsonb_typeof(response_body) = 'object'
            AND response_body ? 'id'
            AND response_body ->> 'id' = id
            AND request_url IS NOT NULL)
        OR (NOT is_present AND response_body IS NULL
            AND request_url IS NULL
            AND display_name IS NULL AND given_name IS NULL
            AND surname IS NULL AND mail IS NULL AND company_name IS NULL
            AND department IS NULL AND job_title IS NULL
            AND skills IS NULL AND responsibilities IS NULL
            AND interests IS NULL)
    );
"""


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL)


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        "ALTER TABLE user_ms_graph_sources ADD COLUMN IF NOT EXISTS source_fetched_at TIMESTAMPTZ"
    )
    connection.exec_driver_sql(
        """UPDATE user_ms_graph_sources
           SET source_fetched_at = lower(valid_at)
           WHERE is_present AND source_fetched_at IS NULL"""
    )
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
           DROP CONSTRAINT IF EXISTS user_ms_graph_sources_presence_check"""
    )
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
           ADD CONSTRAINT user_ms_graph_sources_check CHECK (
               (is_present AND response_body IS NOT NULL
                   AND jsonb_typeof(response_body) = 'object'
                   AND response_body ? 'id'
                   AND response_body ->> 'id' = id
                   AND request_url IS NOT NULL AND source_fetched_at IS NOT NULL)
               OR (NOT is_present AND response_body IS NULL
                   AND request_url IS NULL AND source_fetched_at IS NULL
                   AND display_name IS NULL AND given_name IS NULL
                   AND surname IS NULL AND mail IS NULL AND company_name IS NULL
                   AND department IS NULL AND job_title IS NULL
                   AND skills IS NULL AND responsibilities IS NULL
                   AND interests IS NULL)
           )"""
    )
