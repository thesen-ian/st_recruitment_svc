from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0006_company_profiles'
down_revision = '0005_resumes'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    op.create_table(
        'company_profiles',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('company_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('website_url', sa.Text(), nullable=True),
        sa.Column('industry', sa.Text(), nullable=True),
        sa.Column('size', sa.Text(), nullable=True),
        sa.Column('hq_location', sa.Text(), nullable=True),
        sa.Column('logo_file_object_id', sa.String(length=36), sa.ForeignKey('file_objects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('cover_file_object_id', sa.String(length=36), sa.ForeignKey('file_objects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Unique constraint to enforce one-to-one mapping between user(company) and profile
    try:
        op.create_unique_constraint('uq_company_profiles_company_id', 'company_profiles', ['company_id'])
    except Exception as e:
        logger.error("Failed to create uq_company_profiles_company_id: %s", e, exc_info=True)

    # Indexes for quick lookups on logo/cover
    try:
        op.create_index('idx_company_profiles_logo_file_object_id', 'company_profiles', ['logo_file_object_id'])
    except Exception as e:
        logger.error("Failed to create idx_company_profiles_logo_file_object_id: %s", e, exc_info=True)
    try:
        op.create_index('idx_company_profiles_cover_file_object_id', 'company_profiles', ['cover_file_object_id'])
    except Exception as e:
        logger.error("Failed to create idx_company_profiles_cover_file_object_id: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        op.drop_index('idx_company_profiles_cover_file_object_id', table_name='company_profiles')
    except Exception as e:
        logger.error("Failed to drop idx_company_profiles_cover_file_object_id: %s", e, exc_info=True)
    try:
        op.drop_index('idx_company_profiles_logo_file_object_id', table_name='company_profiles')
    except Exception as e:
        logger.error("Failed to drop idx_company_profiles_logo_file_object_id: %s", e, exc_info=True)
    try:
        op.drop_constraint('uq_company_profiles_company_id', 'company_profiles', type_='unique')
    except Exception as e:
        logger.error("Failed to drop uq_company_profiles_company_id: %s", e, exc_info=True)
    op.drop_table('company_profiles')
