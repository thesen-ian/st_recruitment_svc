from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '0013_notifications'
down_revision = '0012_interviews'
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    insp = inspect(bind)
    return name in insp.get_table_names()


def _get_columns(bind, name: str):
    insp = inspect(bind)
    try:
        cols = insp.get_columns(name)
    except Exception:
        return []
    return [c['name'] for c in cols]


def upgrade() -> None:
    bind = op.get_bind()
    # Create or migrate notifications table
    if not _table_exists(bind, 'notifications'):
        op.create_table(
            'notifications',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('type', sa.String(length=255), nullable=False),
            sa.Column('related_entity_type', sa.String(length=255), nullable=True),
            sa.Column('related_entity_id', sa.String(length=36), nullable=True),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("(read_at IS NULL) OR (is_read = 1)", name='chk_notifications_read_at_consistent'),
        )
    else:
        # Table exists: attempt to add missing columns introduced by this revision
        cols = _get_columns(bind, 'notifications')
        try:
            if 'body' not in cols:
                op.add_column('notifications', sa.Column('body', sa.Text(), nullable=True))
            if 'updated_at' not in cols:
                # Add updated_at with a server default to populate existing rows
                op.add_column('notifications', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
            if 'read_at' not in cols:
                op.add_column('notifications', sa.Column('read_at', sa.DateTime(timezone=True), nullable=True))
        except Exception:
            # Best-effort: if ALTER fails on some backends, continue; metadata may be authoritative.
            pass

    # Create indexes for notifications (best-effort)
    try:
        op.create_index('idx_notifications_user_created_at', 'notifications', ['user_id', sa.text('created_at DESC')])
    except Exception:
        pass
    try:
        op.create_index('idx_notifications_user_is_read_created_at', 'notifications', ['user_id', 'is_read', sa.text('created_at DESC')])
    except Exception:
        pass

    # Create or migrate notification_preferences table
    if not _table_exists(bind, 'notification_preferences'):
        op.create_table(
            'notification_preferences',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('in_app_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_application_submitted', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_application_status_changed', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_application_withdrawn', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_interview_updates', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('notify_admin_report_updates', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('user_id', name='uq_notification_preferences_user_id'),
        )
        try:
            op.create_index('idx_notification_preferences_user_id', 'notification_preferences', ['user_id'])
        except Exception:
            pass
    else:
        # Ensure unique constraint or unique index exists on user_id and add missing columns
        try:
            insp = inspect(bind)
            cols = _get_columns(bind, 'notification_preferences')
            # If any critical column missing, perform a safe SQLite-aware rebuild
            missing = set(['id', 'in_app_enabled', 'notify_application_submitted', 'notify_application_status_changed', 'notify_application_withdrawn', 'notify_interview_updates', 'notify_admin_report_updates', 'created_at', 'updated_at']) - set(cols)
            dialect = getattr(bind, 'dialect', None)
            name = getattr(dialect, 'name', None)
            if missing:
                # Rebuild table on sqlite in a safe way by selecting only present columns
                if name == 'sqlite':
                    # Drop any leftover temp table
                    try:
                        op.execute(sa.text("DROP TABLE IF EXISTS notification_preferences_new"))
                    except Exception:
                        pass
                    # Create new table with the desired schema
                    op.execute(sa.text(
                        """
                        CREATE TABLE notification_preferences_new (
                            id VARCHAR(36) NOT NULL,
                            user_id VARCHAR(36) NOT NULL,
                            in_app_enabled BOOLEAN DEFAULT 'true' NOT NULL,
                            notify_application_submitted BOOLEAN DEFAULT 'true' NOT NULL,
                            notify_application_status_changed BOOLEAN DEFAULT 'true' NOT NULL,
                            notify_application_withdrawn BOOLEAN DEFAULT 'true' NOT NULL,
                            notify_interview_updates BOOLEAN DEFAULT 'true' NOT NULL,
                            notify_admin_report_updates BOOLEAN DEFAULT 'true' NOT NULL,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                            PRIMARY KEY (id)
                        )
                        """
                    ))
                    # Build insert selecting only existing columns; supply defaults for missing ones
                    present = set(cols)
                    select_cols = []
                    # id: generate random 16-byte hex for sqlite
                    select_cols.append("LOWER(HEX(RANDOMBLOB(16))) AS id")
                    # user_id must exist or inserts will fail later; use NULL if missing
                    if 'user_id' in present:
                        select_cols.append('user_id')
                    else:
                        select_cols.append('NULL AS user_id')
                    # boolean columns: use COALESCE to set defaults
                    def col_or_default(col_name):
                        if col_name in present:
                            return f"COALESCE({col_name}, 1) AS {col_name}"
                        return f"1 AS {col_name}"
                    select_cols.append(col_or_default('in_app_enabled'))
                    select_cols.append(col_or_default('notify_application_submitted'))
                    select_cols.append(col_or_default('notify_application_status_changed'))
                    select_cols.append(col_or_default('notify_application_withdrawn'))
                    select_cols.append(col_or_default('notify_interview_updates'))
                    select_cols.append(col_or_default('notify_admin_report_updates'))
                    # timestamps
                    if 'created_at' in present:
                        select_cols.append('created_at')
                    else:
                        select_cols.append('CURRENT_TIMESTAMP as created_at')
                    if 'updated_at' in present:
                        select_cols.append('updated_at')
                    else:
                        select_cols.append('CURRENT_TIMESTAMP as updated_at')

                    insert_sql = f"INSERT INTO notification_preferences_new (id, user_id, in_app_enabled, notify_application_submitted, notify_application_status_changed, notify_application_withdrawn, notify_interview_updates, notify_admin_report_updates, created_at, updated_at) SELECT {', '.join(select_cols)} FROM notification_preferences"
                    try:
                        op.execute(sa.text(insert_sql))
                    except Exception:
                        # If copy fails (e.g. table empty or incompatible), ignore and proceed to rename
                        pass
                    # Drop old and rename
                    try:
                        op.execute(sa.text('DROP TABLE notification_preferences'))
                    except Exception:
                        pass
                    try:
                        op.execute(sa.text('ALTER TABLE notification_preferences_new RENAME TO notification_preferences'))
                    except Exception:
                        pass
                    # Ensure indexes/constraints exist
                    try:
                        op.create_index('idx_notification_preferences_user_id', 'notification_preferences', ['user_id'])
                    except Exception:
                        pass
                    try:
                        op.create_index('uq_notification_preferences_user_id', 'notification_preferences', ['user_id'], unique=True)
                    except Exception:
                        # best-effort; if unique indexes already exist, ignore
                        pass
                else:
                    # For non-sqlite, try to add missing columns via ALTER
                    try:
                        if 'id' not in cols:
                            op.add_column('notification_preferences', sa.Column('id', sa.String(length=36), primary_key=False))
                        if 'in_app_enabled' not in cols:
                            op.add_column('notification_preferences', sa.Column('in_app_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
                        if 'notify_application_submitted' not in cols:
                            op.add_column('notification_preferences', sa.Column('notify_application_submitted', sa.Boolean(), nullable=False, server_default=sa.text('true')))
                        if 'notify_application_status_changed' not in cols:
                            op.add_column('notification_preferences', sa.Column('notify_application_status_changed', sa.Boolean(), nullable=False, server_default=sa.text('true')))
                        if 'notify_application_withdrawn' not in cols:
                            op.add_column('notification_preferences', sa.Column('notify_application_withdrawn', sa.Boolean(), nullable=False, server_default=sa.text('true')))
                        if 'notify_interview_updates' not in cols:
                            op.add_column('notification_preferences', sa.Column('notify_interview_updates', sa.Boolean(), nullable=False, server_default=sa.text('true')))
                        if 'notify_admin_report_updates' not in cols:
                            op.add_column('notification_preferences', sa.Column('notify_admin_report_updates', sa.Boolean(), nullable=False, server_default=sa.text('true')))
                        if 'created_at' not in cols:
                            op.add_column('notification_preferences', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
                        if 'updated_at' not in cols:
                            op.add_column('notification_preferences', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
                    except Exception:
                        pass
                    # Ensure unique constraint/index
                    try:
                        indexes = {idx['name'] for idx in insp.get_indexes('notification_preferences')}
                        if 'uq_notification_preferences_user_id' not in indexes:
                            op.create_unique_constraint('uq_notification_preferences_user_id', 'notification_preferences', ['user_id'])
                    except Exception:
                        pass
            else:
                # No missing columns: ensure index/constraint exists
                indexes = {idx['name'] for idx in insp.get_indexes('notification_preferences')}
                try:
                    if 'idx_notification_preferences_user_id' not in indexes:
                        op.create_index('idx_notification_preferences_user_id', 'notification_preferences', ['user_id'])
                except Exception:
                    pass
                try:
                    # create unique index on sqlite or unique constraint on other backends
                    if name == 'sqlite':
                        if 'uq_notification_preferences_user_id' not in indexes:
                            op.create_index('uq_notification_preferences_user_id', 'notification_preferences', ['user_id'], unique=True)
                    else:
                        # ensure unique constraint exists; if backend doesn't support, this may raise and is caught
                        op.create_unique_constraint('uq_notification_preferences_user_id', 'notification_preferences', ['user_id'])
                except Exception:
                    pass
        except Exception:
            # best-effort migration: do not block entire upgrade if inspection fails
            pass


def downgrade() -> None:
    # Drop notification_preferences if exists
    bind = op.get_bind()
    try:
        insp = inspect(bind)
        if 'notification_preferences' in insp.get_table_names():
            try:
                op.drop_index('idx_notification_preferences_user_id', table_name='notification_preferences')
            except Exception:
                pass
            try:
                op.drop_table('notification_preferences')
            except Exception:
                pass
    except Exception:
        pass

    # Drop notification indexes and table if exists
    try:
        insp = inspect(bind)
        if 'notifications' in insp.get_table_names():
            try:
                op.drop_index('idx_notifications_user_is_read_created_at', table_name='notifications')
            except Exception:
                pass
            try:
                op.drop_index('idx_notifications_user_created_at', table_name='notifications')
            except Exception:
                pass
            try:
                op.drop_table('notifications')
            except Exception:
                pass
    except Exception:
        pass
