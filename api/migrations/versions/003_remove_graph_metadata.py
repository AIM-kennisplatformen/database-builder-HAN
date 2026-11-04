"""Keep only modeled fields in Microsoft Graph source versions.

Revision ID: 003_remove_graph_metadata
Revises: 002_remove_source_fetched_at
"""

from alembic import op

revision = "003_remove_graph_metadata"
down_revision = "002_remove_source_fetched_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
        "DROP CONSTRAINT user_ms_graph_sources_presence_check"""
    )
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
           DROP COLUMN response_body,
           DROP COLUMN request_url,
           ADD CONSTRAINT user_ms_graph_sources_presence_check CHECK (
               is_present OR (
                   display_name IS NULL AND given_name IS NULL
                   AND surname IS NULL AND mail IS NULL
                   AND company_name IS NULL AND department IS NULL
                   AND job_title IS NULL AND skills IS NULL
                   AND responsibilities IS NULL AND interests IS NULL
               )
           )"""
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
        "DROP CONSTRAINT user_ms_graph_sources_presence_check"""
    )
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
           ADD COLUMN response_body JSONB,
           ADD COLUMN request_url TEXT"""
    )
    # Original response data cannot be recovered; rebuild from retained fields.
    connection.exec_driver_sql(
        """UPDATE user_ms_graph_sources
           SET response_body = jsonb_build_object(
                   'id', id, 'displayName', display_name,
                   'givenName', given_name, 'surname', surname,
                   'mail', mail, 'companyName', company_name,
                   'department', department, 'jobTitle', job_title,
                   'skills', skills, 'responsibilities', responsibilities,
                   'interests', interests
               ),
               request_url = 'https://graph.microsoft.com/v1.0/users/' || id
           WHERE is_present"""
    )
    connection.exec_driver_sql(
        """ALTER TABLE user_ms_graph_sources
           ADD CONSTRAINT user_ms_graph_sources_presence_check CHECK (
               (is_present AND response_body IS NOT NULL
                   AND jsonb_typeof(response_body) = 'object'
                   AND response_body ? 'id'
                   AND response_body ->> 'id' = id
                   AND request_url IS NOT NULL)
               OR (NOT is_present AND response_body IS NULL
                   AND request_url IS NULL
                   AND display_name IS NULL AND given_name IS NULL
                   AND surname IS NULL AND mail IS NULL
                   AND company_name IS NULL AND department IS NULL
                   AND job_title IS NULL AND skills IS NULL
                   AND responsibilities IS NULL AND interests IS NULL)
           )"""
    )
