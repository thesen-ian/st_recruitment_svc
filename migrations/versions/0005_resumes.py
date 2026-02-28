from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0005_resumes'
down_revision = '0004_job_seeker_profiles'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    # Create resumes table linking users and private file_objects
    op.create_table(
        'resumes',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('file_object_id', sa.String(length=36), sa.ForeignKey('file_objects.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Index to speed up per-user counts and lookups
    try:
        op.create_index('idx_resumes_user_id', 'resumes', ['user_id'])
    except Exception as e:
        logger.error("Failed to create idx_resumes_user_id: %s", e, exc_info=True)

    # Ensure one-to-one mapping between resume row and file_object reuse is prevented
    try:
        # SQLite does not support ALTER TABLE ADD CONSTRAINT; create a unique index instead
        bind = op.get_bind()
        dialect = getattr(bind, 'dialect', None)
        name = getattr(dialect, 'name', None)
        if name == 'sqlite':
            # unique index enforces uniqueness on sqlite without table rewrite
            op.create_index('uq_resumes_file_object_id', 'resumes', ['file_object_id'], unique=True)
        else:
            op.create_unique_constraint('uq_resumes_file_object_id', 'resumes', ['file_object_id'])
    except Exception as e:
        logger.error("Failed to create uq_resumes_file_object_id: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        bind = op.get_bind()
        dialect = getattr(bind, 'dialect', None)
        name = getattr(dialect, 'name', None)
        if name == 'sqlite':
            try:
                op.drop_index('uq_resumes_file_object_id', table_name='resumes')
            except Exception as e:
                logger.error("Failed to drop uq_resumes_file_object_id index: %s", e, exc_info=True)
        else:
            try:
                op.drop_constraint('uq_resumes_file_object_id', 'resumes', type_='unique')
            except Exception as e:
                logger.error("Failed to drop uq_resumes_file_object_id constraint: %s", e, exc_info=True)
    except Exception as e:
        logger.error("Failed to determine dialect while dropping uq_resumes_file_object_id: %s", e, exc_info=True)

    try:
        op.drop_index('idx_resumes_user_id', table_name='resumes')
    except Exception as e:
        logger.error("Failed to drop idx_resumes_user_id: %s", e, exc_info=True)
    op.drop_table('resumes')
