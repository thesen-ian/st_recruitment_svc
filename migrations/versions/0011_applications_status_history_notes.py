from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0011_applications_status_history_notes'
down_revision = '0010_applications'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    # Create application_status_history table
    try:
        op.create_table(
            'application_status_history',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('application_id', sa.String(length=36), sa.ForeignKey('applications.id', ondelete='CASCADE'), nullable=False),
            # from_status nullable to allow initial records
            sa.Column('from_status', sa.Enum('Applied', name='application_status'), nullable=True),
            sa.Column('to_status', sa.Enum('Applied', name='application_status'), nullable=False),
            sa.Column('changed_by_user_id', sa.String(length=36), nullable=False),
            sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('notes', sa.Text(), nullable=True),
        )
    except Exception as e:
        logger.error("Failed to create table application_status_history: %s", e, exc_info=True)

    try:
        # Create index with DESC ordering for changed_at to optimize newest-first queries
        op.create_index('idx_application_status_history_application_id_changed_at', 'application_status_history', ['application_id', sa.text('changed_at DESC')])
    except Exception as e:
        logger.error("Failed to create index idx_application_status_history_application_id_changed_at: %s", e, exc_info=True)
    try:
        op.create_index('idx_application_status_history_application_id', 'application_status_history', ['application_id'])
    except Exception as e:
        logger.error("Failed to create index idx_application_status_history_application_id: %s", e, exc_info=True)

    # Create application_notes table
    try:
        op.create_table(
            'application_notes',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('application_id', sa.String(length=36), sa.ForeignKey('applications.id', ondelete='CASCADE'), nullable=False),
            sa.Column('note_text', sa.Text(), nullable=False),
            sa.Column('created_by_user_id', sa.String(length=36), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    except Exception as e:
        logger.error("Failed to create table application_notes: %s", e, exc_info=True)

    try:
        # Create index with DESC ordering for created_at to optimize newest-first queries
        op.create_index('idx_application_notes_application_id_created_at', 'application_notes', ['application_id', sa.text('created_at DESC')])
    except Exception as e:
        logger.error("Failed to create index idx_application_notes_application_id_created_at: %s", e, exc_info=True)

    # Ensure cascade behavior in SQLite tests if needed
    try:
        if op.get_bind().dialect.name == 'sqlite':
            # When deleting an application, ensure related history and notes are removed
            op.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_applications_delete_history_notes
                AFTER DELETE ON applications
                FOR EACH ROW
                BEGIN
                    DELETE FROM application_status_history WHERE application_id = OLD.id;
                    DELETE FROM application_notes WHERE application_id = OLD.id;
                END;
                """
            )
    except Exception as e:
        logger.error("Failed to create sqlite delete trigger for application history/notes: %s", e, exc_info=True)


def downgrade() -> None:
    try:
        # Drop SQLite trigger if present
        if op.get_bind().dialect.name == 'sqlite':
            op.execute('DROP TRIGGER IF EXISTS trg_applications_delete_history_notes')
    except Exception as e:
        logger.error("Failed to drop sqlite trigger trg_applications_delete_history_notes: %s", e, exc_info=True)

    try:
        op.drop_index('idx_application_notes_application_id_created_at', table_name='application_notes')
    except Exception as e:
        logger.error("Failed to drop index idx_application_notes_application_id_created_at: %s", e, exc_info=True)
    try:
        op.drop_table('application_notes')
    except Exception as e:
        logger.error("Failed to drop table application_notes: %s", e, exc_info=True)

    try:
        op.drop_index('idx_application_status_history_application_id_changed_at', table_name='application_status_history')
    except Exception as e:
        logger.error("Failed to drop index idx_application_status_history_application_id_changed_at: %s", e, exc_info=True)
    try:
        op.drop_index('idx_application_status_history_application_id', table_name='application_status_history')
    except Exception as e:
        logger.error("Failed to drop index idx_application_status_history_application_id: %s", e, exc_info=True)
    try:
        op.drop_table('application_status_history')
    except Exception as e:
        logger.error("Failed to drop table application_status_history: %s", e, exc_info=True)
