from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0007_jobs_table'
down_revision = '0006_company_profiles'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create job_status enum type and jobs table
    job_status = sa.Enum('draft', 'published', 'closed', name='job_status')
    job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'jobs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('company_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', job_status, nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # indexes
    op.create_index('idx_jobs_company_id', 'jobs', ['company_id'])
    op.create_index('idx_jobs_status', 'jobs', ['status'])


def downgrade() -> None:
    try:
        op.drop_index('idx_jobs_status', table_name='jobs')
    except Exception:
        pass
    try:
        op.drop_index('idx_jobs_company_id', table_name='jobs')
    except Exception:
        pass

    op.drop_table('jobs')
    try:
        op.execute('DROP TYPE IF EXISTS job_status')
    except Exception:
        # best effort for SQLite compatibility
        pass
