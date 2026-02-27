from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0008_job_postings'
down_revision = '0007_jobs_table'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    # Create enums for job_postings state and question type
    try:
        job_posting_state = sa.Enum('draft', 'active', 'closed', name='job_posting_state')
        job_posting_state.create(op.get_bind(), checkfirst=True)
    except Exception as e:
        logger.error("Failed to create enum job_posting_state: %s", e, exc_info=True)

    try:
        question_type = sa.Enum('text', 'mcq', 'file', name='job_posting_question_type')
        question_type.create(op.get_bind(), checkfirst=True)
    except Exception as e:
        logger.error("Failed to create enum job_posting_question_type: %s", e, exc_info=True)

    # job_postings table
    try:
        op.create_table(
            'job_postings',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('company_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('state', job_posting_state, nullable=False, server_default='draft'),
            sa.Column('title', sa.String(length=512), nullable=False),
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column('location', sa.Text(), nullable=True),
            sa.Column('employment_type', sa.Text(), nullable=True),
            sa.Column('seniority', sa.Text(), nullable=True),
            sa.Column('salary_min', sa.Integer(), nullable=True),
            sa.Column('salary_max', sa.Integer(), nullable=True),
            sa.Column('currency', sa.String(length=3), nullable=True),
            sa.Column('remote_policy', sa.Text(), nullable=True),
            sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint("state IN ('draft','active','closed')", name='chk_job_postings_state_allowed'),
        )
    except Exception as e:
        logger.error("Failed to create table job_postings: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_postings_company_id', 'job_postings', ['company_id'])
    except Exception as e:
        logger.error("Failed to create index idx_job_postings_company_id: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_postings_state', 'job_postings', ['state'])
    except Exception as e:
        logger.error("Failed to create index idx_job_postings_state: %s", e, exc_info=True)

    # job_posting_questions
    try:
        op.create_table(
            'job_posting_questions',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('job_posting_id', sa.String(length=36), sa.ForeignKey('job_postings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('type', question_type, nullable=False),
            sa.Column('prompt', sa.Text(), nullable=False),
            sa.Column('is_required', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('job_posting_id', 'position', name='uq_job_posting_questions_job_posting_id_position'),
        )
    except Exception as e:
        logger.error("Failed to create table job_posting_questions: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_posting_questions_job_posting_id', 'job_posting_questions', ['job_posting_id'])
    except Exception as e:
        logger.error("Failed to create index idx_job_posting_questions_job_posting_id: %s", e, exc_info=True)

    # job_posting_question_options
    try:
        op.create_table(
            'job_posting_question_options',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('question_id', sa.String(length=36), sa.ForeignKey('job_posting_questions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('label', sa.Text(), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('question_id', 'position', name='uq_job_posting_question_options_question_id_position'),
        )
    except Exception as e:
        logger.error("Failed to create table job_posting_question_options: %s", e, exc_info=True)

    try:
        op.create_index('idx_job_posting_question_options_question_id', 'job_posting_question_options', ['question_id'])
    except Exception as e:
        logger.error("Failed to create index idx_job_posting_question_options_question_id: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        op.drop_constraint('uq_job_posting_question_options_question_id_position', 'job_posting_question_options', type_='unique')
    except Exception as e:
        logger.error("Failed to drop constraint uq_job_posting_question_options_question_id_position: %s", e, exc_info=True)
    try:
        op.drop_index('idx_job_posting_question_options_question_id', table_name='job_posting_question_options')
    except Exception as e:
        logger.error("Failed to drop index idx_job_posting_question_options_question_id: %s", e, exc_info=True)
    try:
        op.drop_table('job_posting_question_options')
    except Exception as e:
        logger.error("Failed to drop table job_posting_question_options: %s", e, exc_info=True)

    try:
        op.drop_constraint('uq_job_posting_questions_job_posting_id_position', 'job_posting_questions', type_='unique')
    except Exception as e:
        logger.error("Failed to drop constraint uq_job_posting_questions_job_posting_id_position: %s", e, exc_info=True)
    try:
        op.drop_index('idx_job_posting_questions_job_posting_id', table_name='job_posting_questions')
    except Exception as e:
        logger.error("Failed to drop index idx_job_posting_questions_job_posting_id: %s", e, exc_info=True)
    try:
        op.drop_table('job_posting_questions')
    except Exception as e:
        logger.error("Failed to drop table job_posting_questions: %s", e, exc_info=True)

    try:
        op.drop_index('idx_job_postings_state', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop index idx_job_postings_state: %s", e, exc_info=True)
    try:
        op.drop_index('idx_job_postings_company_id', table_name='job_postings')
    except Exception as e:
        logger.error("Failed to drop index idx_job_postings_company_id: %s", e, exc_info=True)
    try:
        op.drop_table('job_postings')
    except Exception as e:
        logger.error("Failed to drop table job_postings: %s", e, exc_info=True)

    try:
        op.execute('DROP TYPE IF EXISTS job_posting_question_type')
    except Exception as e:
        logger.error("Failed to drop type job_posting_question_type: %s", e, exc_info=True)
    try:
        op.execute('DROP TYPE IF EXISTS job_posting_state')
    except Exception as e:
        logger.error("Failed to drop type job_posting_state: %s", e, exc_info=True)
