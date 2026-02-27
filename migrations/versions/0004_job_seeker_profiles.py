from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004_job_seeker_profiles'
down_revision = '0003_file_objects_constraints_and_tokens_fk'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Choose JSONB on PostgreSQL, fallback to generic JSON for SQLite/dev
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        # import dialect type only when running against Postgres
        from sqlalchemy.dialects.postgresql import JSONB

        json_type = JSONB
        json_server_default = sa.text("'[]'::jsonb")
    else:
        json_type = sa.JSON
        json_server_default = sa.text("'[]'")

    # Create job_seeker_profiles table storing profile snapshot and structured JSON fields
    op.create_table(
        'job_seeker_profiles',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('full_name', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('phone', sa.Text(), nullable=True),
        sa.Column('location', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('experiences', json_type(), nullable=False, server_default=json_server_default),
        sa.Column('education', json_type(), nullable=False, server_default=json_server_default),
        sa.Column('skills', json_type(), nullable=False, server_default=json_server_default),
        sa.Column('languages', json_type(), nullable=False, server_default=json_server_default),
        sa.Column('certifications', json_type(), nullable=False, server_default=json_server_default),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', name='uq_job_seeker_profiles_user_id'),
    )


def downgrade() -> None:
    # Drop table. Let failures surface to ensure migration correctness.
    op.drop_table('job_seeker_profiles')
