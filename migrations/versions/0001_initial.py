from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=512), nullable=False),
        sa.Column('role', sa.Enum('job_seeker', 'company', 'admin', name='user_role'), nullable=False),
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('status', sa.Enum('active', 'suspended', 'banned', name='user_status'), nullable=False, server_default='active'),
        sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('email', name='uq_users_email'),
    )

    # tokens table
    op.create_table(
        'tokens',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(length=512), nullable=False),
        sa.Column('type', sa.Enum('verify', 'reset', 'download', name='token_type'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    )
    # index for FK user_id on tokens
    op.create_index('ix_tokens_user_id', 'tokens', ['user_id'])

    # file_objects table
    op.create_table(
        'file_objects',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('owner_user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('visibility', sa.Enum('public', 'private', name='file_visibility'), nullable=False, server_default='private'),
        sa.Column('purpose', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=1024), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('storage_path', sa.String(length=2048), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    # index for FK owner_user_id on file_objects
    op.create_index('ix_file_objects_owner_user_id', 'file_objects', ['owner_user_id'])

    # notifications table
    op.create_table(
        'notifications',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('type', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('related_entity_type', sa.String(length=255), nullable=True),
        sa.Column('related_entity_id', sa.String(length=255), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # index for FK user_id on notifications
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])

    # notification_preferences table
    op.create_table(
        'notification_preferences',
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('email_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sms_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('push_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('per_event_settings_json', sa.JSON(), nullable=False, server_default='{}'),
    )


def downgrade() -> None:
    # drop indexes first
    try:
        op.drop_index('ix_notifications_user_id', table_name='notifications')
    except Exception:
        pass
    try:
        op.drop_index('ix_file_objects_owner_user_id', table_name='file_objects')
    except Exception:
        pass
    try:
        op.drop_index('ix_tokens_user_id', table_name='tokens')
    except Exception:
        pass

    op.drop_table('notification_preferences')
    op.drop_table('notifications')
    op.drop_table('file_objects')
    op.drop_table('tokens')
    op.drop_table('users')
    # drop enums where possible
    try:
        op.execute('DROP TYPE IF EXISTS user_role')
        op.execute('DROP TYPE IF EXISTS user_status')
        op.execute('DROP TYPE IF EXISTS token_type')
        op.execute('DROP TYPE IF EXISTS file_visibility')
    except Exception:
        # best effort; SQLite will not support DROP TYPE
        pass
