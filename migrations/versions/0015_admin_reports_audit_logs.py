from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0015_admin_reports_audit_logs'
down_revision = '0014_remove_notifications_message'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add is_removed boolean to jobs to represent admin-removed jobs
    op.add_column('jobs', sa.Column('is_removed', sa.Boolean(), nullable=False, server_default='false'))
    try:
        op.create_index('idx_jobs_is_removed', 'jobs', ['is_removed'])
    except Exception:
        pass

    # Create reports table
    op.create_table(
        'reports',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('reported_by_user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(length=255), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='OPEN'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.CheckConstraint("NOT (status = 'RESOLVED') OR (resolved_at IS NOT NULL AND resolved_by IS NOT NULL)", name='chk_reports_resolved_requires_resolver_and_time')
    )

    op.create_index('idx_reports_reported_by_user_id', 'reports', ['reported_by_user_id'])
    op.create_index('idx_reports_entity', 'reports', ['entity_type', 'entity_id'])
    op.create_index('idx_reports_status', 'reports', ['status'])
    # index on resolved_by per spec
    op.create_index('idx_reports_resolved_by', 'reports', ['resolved_by'])

    # Create admin_audit_logs table
    # Use JSONB on Postgres when available, fallback to generic JSON on other DBs
    details_json_type = sa.JSON()
    if dialect == 'postgresql':
        try:
            from sqlalchemy.dialects import postgresql
            details_json_type = postgresql.JSONB()
        except Exception:
            details_json_type = sa.JSON()

    op.create_table(
        'admin_audit_logs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('admin_user_id', sa.String(length=36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action_type', sa.String(length=255), nullable=False),
        sa.Column('target_type', sa.String(length=255), nullable=False),
        sa.Column('target_id', sa.String(length=36), nullable=True),
        sa.Column('details_json', details_json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index('idx_admin_audit_logs_admin_user_id', 'admin_audit_logs', ['admin_user_id'])
    op.create_index('idx_admin_audit_logs_action_type', 'admin_audit_logs', ['action_type'])
    op.create_index('idx_admin_audit_logs_target', 'admin_audit_logs', ['target_type', 'target_id'])
    op.create_index('idx_admin_audit_logs_created_at', 'admin_audit_logs', ['created_at'])


def downgrade() -> None:
    try:
        op.drop_index('idx_admin_audit_logs_created_at', table_name='admin_audit_logs')
    except Exception:
        pass
    try:
        op.drop_index('idx_admin_audit_logs_target', table_name='admin_audit_logs')
    except Exception:
        pass
    try:
        op.drop_index('idx_admin_audit_logs_action_type', table_name='admin_audit_logs')
    except Exception:
        pass
    try:
        op.drop_index('idx_admin_audit_logs_admin_user_id', table_name='admin_audit_logs')
    except Exception:
        pass

    try:
        op.drop_table('admin_audit_logs')
    except Exception:
        pass

    try:
        op.drop_index('idx_reports_resolved_by', table_name='reports')
    except Exception:
        pass
    try:
        op.drop_index('idx_reports_status', table_name='reports')
    except Exception:
        pass
    try:
        op.drop_index('idx_reports_entity', table_name='reports')
    except Exception:
        pass
    try:
        op.drop_index('idx_reports_reported_by_user_id', table_name='reports')
    except Exception:
        pass

    try:
        op.drop_table('reports')
    except Exception:
        pass

    try:
        op.drop_index('idx_jobs_is_removed', table_name='jobs')
    except Exception:
        pass
    try:
        op.drop_column('jobs', 'is_removed')
    except Exception:
        pass
