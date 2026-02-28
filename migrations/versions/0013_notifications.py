from alembic import op
import sqlalchemy as sa
from sqlalchemy import CheckConstraint
import logging

# revision identifiers, used by Alembic.
revision = '0013_notifications'
down_revision = '0012_interviews'
branch_labels = None
dependencies = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    # Create notification_preferences table
    try:
        op.create_table(
            'notification_preferences',
            sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
            sa.Column('user_id', sa.String(length=36), nullable=False, unique=True),
            sa.Column('in_app_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_application_submitted', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_application_status_changed', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_application_withdrawn', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_interview_updates', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_admin_report_updates', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        )
    except Exception as e:
        logger.error("Failed to create table notification_preferences: %s", e, exc_info=True)

    # Create notifications table
    try:
        op.create_table(
            'notifications',
            sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
            sa.Column('user_id', sa.String(length=36), nullable=False),
            sa.Column('type', sa.String(length=64), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('related_entity_type', sa.String(length=64), nullable=True),
            sa.Column('related_entity_id', sa.String(length=36), nullable=True),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            CheckConstraint("((is_read = false AND read_at IS NULL) OR (is_read = true AND read_at IS NOT NULL))", name='chk_notifications_read_state'),
        )
    except Exception as e:
        logger.error("Failed to create table notifications: %s", e, exc_info=True)

    # Indexes to support queries
    try:
        op.create_index('idx_notifications_user_id_created_at', 'notifications', ['user_id', sa.text('created_at DESC')])
    except Exception as e:
        logger.error("Failed to create index idx_notifications_user_id_created_at: %s", e, exc_info=True)

    try:
        op.create_index('idx_notifications_user_id_is_read_created_at', 'notifications', ['user_id', 'is_read', sa.text('created_at DESC')])
    except Exception as e:
        logger.error("Failed to create index idx_notifications_user_id_is_read_created_at: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        op.drop_index('idx_notifications_user_id_is_read_created_at', table_name='notifications')
    except Exception as e:
        logger.error("Failed to drop index idx_notifications_user_id_is_read_created_at: %s", e, exc_info=True)
    try:
        op.drop_index('idx_notifications_user_id_created_at', table_name='notifications')
    except Exception as e:
        logger.error("Failed to drop index idx_notifications_user_id_created_at: %s", e, exc_info=True)
    try:
        op.drop_table('notifications')
    except Exception as e:
        logger.error("Failed to drop table notifications: %s", e, exc_info=True)
    try:
        op.drop_table('notification_preferences')
    except Exception as e:
        logger.error("Failed to drop table notification_preferences: %s", e, exc_info=True)
