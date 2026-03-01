from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_add_auth_tables'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user table
    op.create_table(
        'user',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('email_verified_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('email', name='uq_user_email'),
        sa.CheckConstraint("role IN ('job_seeker','company','admin')", name='ck_user_role'),
        sa.CheckConstraint("status IN ('active','suspended')", name='ck_user_status'),
    )

    # email verification token
    op.create_table(
        'email_verification_token',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('used_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_evt_user_id', 'email_verification_token', ['user_id'])
    op.create_index('ix_evt_token_hash', 'email_verification_token', ['token_hash'])

    # password reset token
    op.create_table(
        'password_reset_token',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('used_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_prt_user_id', 'password_reset_token', ['user_id'])
    op.create_index('ix_prt_token_hash', 'password_reset_token', ['token_hash'])

    # auth rate limit counter
    op.create_table(
        'auth_rate_limit_counter',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('identifier', sa.String(length=255), nullable=False),
        sa.Column('action', sa.String(length=128), nullable=False),
        sa.Column('bucket_start', sa.DateTime(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('identifier', 'action', 'bucket_start', name='uq_rl_identifier_action_bucket'),
        sa.CheckConstraint('count >= 0', name='ck_rate_count_nonnegative'),
    )
    op.create_index('ix_rl_identifier_action_bucket', 'auth_rate_limit_counter', ['identifier', 'action', 'bucket_start'])


def downgrade() -> None:
    # Drop in reverse order of dependencies
    op.drop_index('ix_rl_identifier_action_bucket', table_name='auth_rate_limit_counter')
    op.drop_table('auth_rate_limit_counter')

    op.drop_index('ix_prt_token_hash', table_name='password_reset_token')
    op.drop_index('ix_prt_user_id', table_name='password_reset_token')
    op.drop_table('password_reset_token')

    op.drop_index('ix_evt_token_hash', table_name='email_verification_token')
    op.drop_index('ix_evt_user_id', table_name='email_verification_token')
    op.drop_table('email_verification_token')

    op.drop_table('user')
