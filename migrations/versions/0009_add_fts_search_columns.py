from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0009_add_fts_search_columns'
down_revision = '0008_job_postings'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _is_postgres(bind):
    try:
        return getattr(bind.dialect, "name", None) == "postgresql"
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = _is_postgres(bind)

    # Add denormalized company_name and search_document (tsvector on Postgres)
    try:
        if is_pg:
            # Add company_name as denormalized field to include company name in tsvector
            op.add_column('job_postings', sa.Column('company_name', sa.String(length=255), nullable=True))
            # Add a generated tsvector column combining weighted fields
            op.execute(
                """
                ALTER TABLE job_postings
                ADD COLUMN search_document tsvector
                GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(company_name, '')), 'B') ||
                    setweight(to_tsvector('english', coalesce(employment_type, '')), 'C') ||
                    setweight(to_tsvector('english', coalesce(location, '')), 'C') ||
                    setweight(to_tsvector('english', coalesce(seniority, '')), 'C') ||
                    setweight(to_tsvector('english', coalesce(remote_policy, '')), 'C') ||
                    setweight(to_tsvector('english', coalesce(description, '')), 'D')
                ) STORED
                """
            )
            # Create GIN index for FTS
            try:
                op.execute('CREATE INDEX idx_job_postings_search_document_gin ON job_postings USING GIN(search_document)')
            except Exception as e:
                logger.error("Failed to create GIN index on search_document: %s", e, exc_info=True)
        else:
            # SQLite / other dialects: add compatible text columns to avoid migration failures
            op.add_column('job_postings', sa.Column('company_name', sa.String(length=255), nullable=True))
            # Add a text column to keep ORM aligned (not functionally used for FTS in SQLite)
            op.add_column('job_postings', sa.Column('search_document', sa.Text(), nullable=True))
    except Exception as e:
        logger.error("Failed to add search columns to job_postings: %s", e, exc_info=True)
        raise

    # Create b-tree indexes for common filters (guard with try/except to be idempotent)
    try:
        op.create_index('idx_job_postings_created_at', 'job_postings', ['created_at'])
    except Exception as e:
        logger.error("Failed to create idx_job_postings_created_at: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_postings_location', 'job_postings', ['location'])
    except Exception as e:
        logger.error("Failed to create idx_job_postings_location: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_postings_employment_type', 'job_postings', ['employment_type'])
    except Exception as e:
        logger.error("Failed to create idx_job_postings_employment_type: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_postings_seniority', 'job_postings', ['seniority'])
    except Exception as e:
        logger.error("Failed to create idx_job_postings_seniority: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_postings_remote_policy', 'job_postings', ['remote_policy'])
    except Exception as e:
        logger.error("Failed to create idx_job_postings_remote_policy: %s", e, exc_info=True)

    # Company-level attributes indexes (company_profiles) to support joined filters
    try:
        op.create_index('idx_company_profiles_industry', 'company_profiles', ['industry'])
    except Exception as e:
        logger.error("Failed to create idx_company_profiles_industry: %s", e, exc_info=True)

    try:
        op.create_index('idx_company_profiles_size', 'company_profiles', ['size'])
    except Exception as e:
        logger.error("Failed to create idx_company_profiles_size: %s", e, exc_info=True)


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = _is_postgres(bind)

    # Drop indexes created in upgrade (safe to attempt even if some don't exist)
    try:
        op.drop_index('idx_job_postings_remote_policy', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop idx_job_postings_remote_policy: %s", e, exc_info=True)

    try:
        op.drop_index('idx_job_postings_seniority', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop idx_job_postings_seniority: %s", e, exc_info=True)

    try:
        op.drop_index('idx_job_postings_employment_type', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop idx_job_postings_employment_type: %s", e, exc_info=True)

    try:
        op.drop_index('idx_job_postings_location', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop idx_job_postings_location: %s", e, exc_info=True)

    try:
        op.drop_index('idx_job_postings_created_at', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop idx_job_postings_created_at: %s", e, exc_info=True)

    try:
        op.drop_index('idx_company_profiles_industry', table_name='company_profiles')
    except Exception as e:
        logger.error("Failed to drop idx_company_profiles_industry: %s", e, exc_info=True)

    try:
        op.drop_index('idx_company_profiles_size', table_name='company_profiles')
    except Exception as e:
        logger.error("Failed to drop idx_company_profiles_size: %s", e, exc_info=True)

    # Drop the search columns
    try:
        if is_pg:
            try:
                op.execute('DROP INDEX IF EXISTS idx_job_postings_search_document_gin')
            except Exception as e:
                logger.error("Failed to drop GIN index idx_job_postings_search_document_gin: %s", e, exc_info=True)
            try:
                op.drop_column('job_postings', 'search_document')
            except Exception as e:
                logger.error("Failed to drop column search_document from job_postings: %s", e, exc_info=True)
            try:
                op.drop_column('job_postings', 'company_name')
            except Exception as e:
                logger.error("Failed to drop column company_name from job_postings: %s", e, exc_info=True)
        else:
            # SQLite: drop text columns if present
            try:
                op.drop_column('job_postings', 'search_document')
            except Exception as e:
                logger.error("Failed to drop column search_document from job_postings: %s", e, exc_info=True)
            try:
                op.drop_column('job_postings', 'company_name')
            except Exception as e:
                logger.error("Failed to drop column company_name from job_postings: %s", e, exc_info=True)
    except Exception as e:
        logger.error("Failed during downgrade column/index cleanup: %s", e, exc_info=True)
        raise
