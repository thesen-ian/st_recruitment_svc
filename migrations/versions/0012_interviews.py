from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0012_interviews'
down_revision = '0011_applications_status_history_notes'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    # Choose JSON type and server default depending on backend to avoid type mismatches
    try:
        if is_postgres:
            from sqlalchemy.dialects.postgresql import JSONB  # type: ignore

            json_type = JSONB
            json_server_default = sa.text("'[]'::jsonb")
        else:
            json_type = sa.JSON
            json_server_default = sa.text("'[]'")
    except Exception:
        # Defensive fallback
        json_type = sa.JSON
        json_server_default = sa.text("'[]'")

    # create enum for interview status (best-effort)
    try:
        interview_status = sa.Enum('proposed', 'accepted', 'reschedule_requested', 'cancelled', name='interview_status')
        interview_status.create(op.get_bind(), checkfirst=True)
    except Exception as e:
        logger.error("Failed to create enum interview_status: %s", e, exc_info=True)

    try:
        op.create_table(
            'interviews',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('application_id', sa.String(length=36), sa.ForeignKey('applications.id', ondelete='CASCADE'), nullable=False),
            sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('duration_minutes', sa.Integer(), nullable=False),
            sa.Column('format', sa.String(length=255), nullable=False),
            sa.Column('location_or_link', sa.Text(), nullable=False),
            sa.Column('interviewer_names', json_type(), nullable=False, server_default=json_server_default),
            sa.Column('status', sa.Enum('proposed', 'accepted', 'reschedule_requested', 'cancelled', name='interview_status'), nullable=False, server_default='proposed'),
            sa.Column('reschedule_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    except Exception as e:
        logger.error("Failed to create table interviews: %s", e, exc_info=True)

    try:
        op.create_index('idx_interviews_application_id', 'interviews', ['application_id'])
    except Exception as e:
        logger.error("Failed to create index idx_interviews_application_id: %s", e, exc_info=True)

    try:
        op.create_table(
            'interview_reschedule_requests',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('interview_id', sa.String(length=36), sa.ForeignKey('interviews.id', ondelete='CASCADE'), nullable=False),
            sa.Column('requested_by_user_id', sa.String(length=36), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('preferred_times', json_type(), nullable=False, server_default=json_server_default),
            sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    except Exception as e:
        logger.error("Failed to create table interview_reschedule_requests: %s", e, exc_info=True)

    try:
        op.create_index('idx_interview_reschedule_requests_interview_id', 'interview_reschedule_requests', ['interview_id'])
    except Exception as e:
        logger.error("Failed to create index idx_interview_reschedule_requests_interview_id: %s", e, exc_info=True)

    # Create index for requested_by_user_id as model declares index=True
    try:
        op.create_index('idx_interview_reschedule_requests_requested_by_user_id', 'interview_reschedule_requests', ['requested_by_user_id'])
    except Exception as e:
        logger.error("Failed to create index idx_interview_reschedule_requests_requested_by_user_id: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        op.drop_index('idx_interview_reschedule_requests_requested_by_user_id', table_name='interview_reschedule_requests')
    except Exception as e:
        logger.error("Failed to drop index idx_interview_reschedule_requests_requested_by_user_id: %s", e, exc_info=True)

    try:
        op.drop_index('idx_interview_reschedule_requests_interview_id', table_name='interview_reschedule_requests')
    except Exception as e:
        logger.error("Failed to drop index idx_interview_reschedule_requests_interview_id: %s", e, exc_info=True)

    try:
        op.drop_table('interview_reschedule_requests')
    except Exception as e:
        logger.error("Failed to drop table interview_reschedule_requests: %s", e, exc_info=True)

    try:
        op.drop_index('idx_interviews_application_id', table_name='interviews')
    except Exception as e:
        logger.error("Failed to drop index idx_interviews_application_id: %s", e, exc_info=True)
    try:
        op.drop_table('interviews')
    except Exception as e:
        logger.error("Failed to drop table interviews: %s", e, exc_info=True)

    try:
        op.execute('DROP TYPE IF EXISTS interview_status')
    except Exception as e:
        logger.error("Failed to drop type interview_status: %s", e, exc_info=True)
