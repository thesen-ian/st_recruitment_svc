from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002_add_auth_fields'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add updated_at to users with a safe default
    op.add_column(
        'users',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Company metadata (nullable)
    op.add_column('users', sa.Column('company_name', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('business_registration_number', sa.String(length=255), nullable=True))
    # JSON type is supported in SQLite via SQLAlchemy; use sa.JSON where available
    op.add_column('users', sa.Column('contact_information', sa.JSON(), nullable=True))

    # Add created_at to tokens
    op.add_column(
        'tokens',
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Ensure a unique index on token_hash to prevent duplicate tokens
    op.create_index('uq_tokens_token_hash', 'tokens', ['token_hash'], unique=True)

    # For PostgreSQL, rename enum values to match application TokenType
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # These ALTER TYPE commands will fail if values already renamed; that's acceptable.
        op.execute("ALTER TYPE token_type RENAME VALUE 'verify' TO 'email_verification'")
        op.execute("ALTER TYPE token_type RENAME VALUE 'reset' TO 'password_reset'")


def downgrade() -> None:
    # For PostgreSQL, revert enum value renames to original values
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # Reverse the renames applied in upgrade so downgrading restores previous DB state
        op.execute("ALTER TYPE token_type RENAME VALUE 'email_verification' TO 'verify'")
        op.execute("ALTER TYPE token_type RENAME VALUE 'password_reset' TO 'reset'")

    # Remove unique index on token_hash
    op.drop_index('uq_tokens_token_hash', table_name='tokens')

    # Drop columns from tokens
    op.drop_column('tokens', 'created_at')

    # Drop company columns and updated_at from users
    op.drop_column('users', 'contact_information')
    op.drop_column('users', 'business_registration_number')
    op.drop_column('users', 'company_name')
    op.drop_column('users', 'updated_at')
