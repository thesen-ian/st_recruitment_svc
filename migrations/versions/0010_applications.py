from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0010_applications'
down_revision = '0009_add_fts_search_columns'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    # Create enum for application status
    try:
        application_status = sa.Enum('Applied', name='application_status')
        application_status.create(op.get_bind(), checkfirst=True)
    except Exception as e:
        logger.error("Failed to create enum application_status: %s", e, exc_info=True)

    # applications table
    try:
        op.create_table(
            'applications',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('job_id', sa.String(length=36), sa.ForeignKey('jobs.id', ondelete='CASCADE'), nullable=False),
            sa.Column('job_seeker_user_id', sa.String(length=36), nullable=False),
            sa.Column('selected_resume_id', sa.String(length=36), sa.ForeignKey('resumes.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('cover_letter', sa.Text(), nullable=True),
            sa.Column('status', application_status, nullable=False, server_default='Applied'),
            sa.Column('applied_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('job_id', 'job_seeker_user_id', name='uq_applications_job_id_job_seeker_user_id'),
        )
    except Exception as e:
        logger.error("Failed to create table applications: %s", e, exc_info=True)

    try:
        op.create_index('idx_applications_job_seeker_user_id', 'applications', ['job_seeker_user_id'])
    except Exception as e:
        logger.error("Failed to create index idx_applications_job_seeker_user_id: %s", e, exc_info=True)
    try:
        op.create_index('idx_applications_job_id', 'applications', ['job_id'])
    except Exception as e:
        logger.error("Failed to create index idx_applications_job_id: %s", e, exc_info=True)
    try:
        op.create_index('idx_applications_status', 'applications', ['status'])
    except Exception as e:
        logger.error("Failed to create index idx_applications_status: %s", e, exc_info=True)
    try:
        op.create_index('idx_applications_job_id_status', 'applications', ['job_id', 'status'])
    except Exception as e:
        logger.error("Failed to create index idx_applications_job_id_status: %s", e, exc_info=True)

    # application_answers table
    try:
        op.create_table(
            'application_answers',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('application_id', sa.String(length=36), sa.ForeignKey('applications.id', ondelete='CASCADE'), nullable=False),
            sa.Column('question_id', sa.String(length=36), sa.ForeignKey('job_posting_questions.id', ondelete='RESTRICT'), nullable=False),
            sa.Column('answer_text', sa.Text(), nullable=True),
            sa.Column('selected_option_id', sa.String(length=36), sa.ForeignKey('job_posting_question_options.id', ondelete='RESTRICT'), nullable=True),
            sa.Column('file_object_id', sa.String(length=36), sa.ForeignKey('file_objects.id', ondelete='RESTRICT'), nullable=True),
            # Ensure exactly one of the answer fields is populated
            sa.CheckConstraint("((answer_text IS NOT NULL) + (selected_option_id IS NOT NULL) + (file_object_id IS NOT NULL)) = 1", name='chk_application_answers_single_answer_field'),
            sa.UniqueConstraint('application_id', 'question_id', name='uq_application_answers_application_id_question_id'),
        )
    except Exception as e:
        logger.error("Failed to create table application_answers: %s", e, exc_info=True)

    try:
        op.create_index('idx_application_answers_application_id', 'application_answers', ['application_id'])
    except Exception as e:
        logger.error("Failed to create index idx_application_answers_application_id: %s", e, exc_info=True)
    try:
        op.create_index('idx_application_answers_question_id', 'application_answers', ['question_id'])
    except Exception as e:
        logger.error("Failed to create index idx_application_answers_question_id: %s", e, exc_info=True)

    # SQLite does not enforce ON DELETE CASCADE by default in some test environments,
    # and ALTER of constraints during earlier migrations can also leave constraints
    # in a state where cascade behavior is not reliable. Create an explicit
    # trigger to ensure answers are removed when an application is deleted.
    try:
        if op.get_bind().dialect.name == 'sqlite':
            op.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_applications_delete_cascade
                AFTER DELETE ON applications
                FOR EACH ROW
                BEGIN
                    DELETE FROM application_answers WHERE application_id = OLD.id;
                END;
                """
            )
    except Exception as e:
        logger.error("Failed to create sqlite delete trigger for applications: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        op.drop_index('idx_application_answers_question_id', table_name='application_answers')
    except Exception as e:
        logger.error("Failed to drop index idx_application_answers_question_id: %s", e, exc_info=True)
    try:
        op.drop_index('idx_application_answers_application_id', table_name='application_answers')
    except Exception as e:
        logger.error("Failed to drop index idx_application_answers_application_id: %s", e, exc_info=True)
    try:
        op.drop_constraint('uq_application_answers_application_id_question_id', 'application_answers', type_='unique')
    except Exception as e:
        logger.error("Failed to drop constraint uq_application_answers_application_id_question_id: %s", e, exc_info=True)
    try:
        op.drop_constraint('chk_application_answers_single_answer_field', 'application_answers', type_='check')
    except Exception as e:
        logger.error("Failed to drop check constraint chk_application_answers_single_answer_field: %s", e, exc_info=True)
    try:
        # Drop SQLite trigger if present
        if op.get_bind().dialect.name == 'sqlite':
            op.execute('DROP TRIGGER IF EXISTS trg_applications_delete_cascade')
    except Exception as e:
        logger.error("Failed to drop sqlite trigger trg_applications_delete_cascade: %s", e, exc_info=True)
    try:
        op.drop_table('application_answers')
    except Exception as e:
        logger.error("Failed to drop table application_answers: %s", e, exc_info=True)

    try:
        op.drop_index('idx_applications_job_id_status', table_name='applications')
    except Exception as e:
        logger.error("Failed to drop index idx_applications_job_id_status: %s", e, exc_info=True)
    try:
        op.drop_index('idx_applications_status', table_name='applications')
    except Exception as e:
        logger.error("Failed to drop index idx_applications_status: %s", e, exc_info=True)
    try:
        op.drop_index('idx_applications_job_id', table_name='applications')
    except Exception as e:
        logger.error("Failed to drop index idx_applications_job_id: %s", e, exc_info=True)
    try:
        op.drop_index('idx_applications_job_seeker_user_id', table_name='applications')
    except Exception as e:
        logger.error("Failed to drop index idx_applications_job_seeker_user_id: %s", e, exc_info=True)
    try:
        op.drop_constraint('uq_applications_job_id_job_seeker_user_id', 'applications', type_='unique')
    except Exception as e:
        logger.error("Failed to drop constraint uq_applications_job_id_job_seeker_user_id: %s", e, exc_info=True)
    try:
        op.drop_table('applications')
    except Exception as e:
        logger.error("Failed to drop table applications: %s", e, exc_info=True)

    try:
        op.execute('DROP TYPE IF EXISTS application_status')
    except Exception as e:
        # best effort, keep downgrade resilient for SQLite
        logger.error("Failed to drop type application_status: %s", e, exc_info=True)
