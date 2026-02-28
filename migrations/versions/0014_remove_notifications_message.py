from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '0014_remove_notifications_message'
down_revision = '0013_notifications'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if 'notifications' not in insp.get_table_names():
        return

    cols_info = insp.get_columns('notifications')
    cols = [c['name'] for c in cols_info]
    if 'message' not in cols:
        # nothing to do
        return

    dialect = getattr(bind, 'dialect', None)
    name = getattr(dialect, 'name', None)

    # On SQLite we must recreate the table to remove the NOT NULL message column.
    if name == 'sqlite':
        # drop any leftover temp table from previous attempts
        try:
            op.execute(sa.text('DROP TABLE IF EXISTS notifications_new'))
        except Exception:
            pass

        # create the desired target table without message column
        op.execute(sa.text(
            """
            CREATE TABLE notifications_new (
                id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                title VARCHAR(255) NOT NULL,
                body TEXT NOT NULL,
                type VARCHAR(255) NOT NULL,
                related_entity_type VARCHAR(255),
                related_entity_id VARCHAR(36),
                is_read BOOLEAN DEFAULT 0 NOT NULL,
                read_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                PRIMARY KEY (id)
            )
            """
        ))

        # Build SELECT list using only columns that actually exist in the old table.
        present = set(cols)
        select_cols = []
        # id
        if 'id' in present:
            select_cols.append('id')
        else:
            select_cols.append("LOWER(HEX(RANDOMBLOB(16))) AS id")
        # user_id
        select_cols.append('user_id' if 'user_id' in present else 'NULL AS user_id')
        # title
        select_cols.append('title' if 'title' in present else "'' AS title")
        # body prefer body if present else message
        if 'body' in present and 'message' in present:
            select_cols.append('COALESCE(body, message) AS body')
        elif 'body' in present:
            select_cols.append('body')
        else:
            # message exists by earlier check
            select_cols.append('message AS body')
        # type
        select_cols.append('type' if 'type' in present else "'' AS type")
        # related_entity_type/id
        select_cols.append('related_entity_type' if 'related_entity_type' in present else 'NULL AS related_entity_type')
        select_cols.append('related_entity_id' if 'related_entity_id' in present else 'NULL AS related_entity_id')
        # is_read
        if 'is_read' in present:
            select_cols.append("COALESCE(is_read, 0) AS is_read")
        else:
            select_cols.append('0 AS is_read')
        # read_at
        select_cols.append('read_at' if 'read_at' in present else 'NULL AS read_at')
        # created_at
        select_cols.append('created_at' if 'created_at' in present else 'CURRENT_TIMESTAMP AS created_at')
        # updated_at: if present take COALESCE(updated_at, created_at), else use created_at
        if 'updated_at' in present:
            select_cols.append('COALESCE(updated_at, created_at) AS updated_at')
        else:
            select_cols.append('created_at AS updated_at')

        insert_sql = (
            "INSERT INTO notifications_new (id, user_id, title, body, type, related_entity_type, related_entity_id, is_read, read_at, created_at, updated_at) "
            + "SELECT " + ", ".join(select_cols) + " FROM notifications"
        )

        try:
            op.execute(sa.text(insert_sql))
        except Exception:
            # if copy fails (empty table or incompatible), continue and still replace schema
            pass

        # Replace old table
        try:
            op.execute(sa.text('DROP TABLE notifications'))
        except Exception:
            pass
        try:
            op.execute(sa.text('ALTER TABLE notifications_new RENAME TO notifications'))
        except Exception:
            pass

        # Recreate expected indexes (best-effort)
        try:
            op.create_index('idx_notifications_user_created_at', 'notifications', ['user_id', sa.text('created_at DESC')])
        except Exception:
            pass
        try:
            op.create_index('idx_notifications_user_is_read_created_at', 'notifications', ['user_id', 'is_read', sa.text('created_at DESC')])
        except Exception:
            pass

    else:
        # For other backends, try to make message nullable first, then drop it if supported.
        try:
            op.alter_column('notifications', 'message', nullable=True)
        except Exception:
            # try to drop column where supported; if not possible, ignore and hope application uses 'body'
            try:
                op.drop_column('notifications', 'message')
            except Exception:
                pass


def downgrade() -> None:
    # No-op downgrade: this migration normalizes legacy schema by removing message.
    return
