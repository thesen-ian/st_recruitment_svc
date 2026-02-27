from alembic import op
import sqlalchemy as sa
import logging

# revision identifiers, used by Alembic.
revision = '0003_file_objects_constraints_and_tokens_fk'
down_revision = '0002_add_auth_fields'
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Ensure consistent index name for owner_user_id per spec
    OWNER_IDX_NAME = 'idx_file_objects_owner_user_id'

    # PostgreSQL path: ensure enum type for purpose and create constraints
    if dialect != 'sqlite':
        # Create file_purpose enum type if not exists (best-effort)
        try:
            op.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'file_purpose') THEN CREATE TYPE file_purpose AS ENUM ('resume','company_logo','company_cover','application_answer'); END IF; END$$;")
        except Exception as e:
            logger.error("Failed to ensure file_purpose enum type: %s", e, exc_info=True)
            try:
                file_purpose = sa.Enum('resume', 'company_logo', 'company_cover', 'application_answer', name='file_purpose')
                file_purpose.create(op.get_bind(), checkfirst=True)
            except Exception as e2:
                logger.error("Fallback enum creation failed: %s", e2, exc_info=True)

        # Add updated_at column if missing
        try:
            op.add_column('file_objects', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        except Exception as e:
            logger.error("Failed to add updated_at to file_objects: %s", e, exc_info=True)

        # Ensure original_filename allows NULL (spec)
        try:
            op.alter_column('file_objects', 'original_filename', nullable=True)
        except Exception as e:
            logger.error("Failed to alter original_filename nullable: %s", e, exc_info=True)

        # Ensure UNIQUE(storage_path) and CHECK(size_bytes >= 0)
        try:
            op.create_unique_constraint('uq_file_objects_storage_path', 'file_objects', ['storage_path'])
        except Exception as e:
            logger.error("Failed to create uq_file_objects_storage_path: %s", e, exc_info=True)
        try:
            op.create_check_constraint('chk_file_objects_size_bytes_nonnegative', 'file_objects', 'size_bytes >= 0')
        except Exception as e:
            logger.error("Failed to create chk_file_objects_size_bytes_nonnegative: %s", e, exc_info=True)

        # Ensure visibility index
        try:
            op.create_index('idx_file_objects_visibility', 'file_objects', ['visibility'])
        except Exception as e:
            logger.error("Failed to create idx_file_objects_visibility: %s", e, exc_info=True)

        # Ensure owner_user_id index uses spec name. If old ix exists, drop it and create new with correct name.
        try:
            # drop existing ix_file_objects_owner_user_id if present
            op.drop_index('ix_file_objects_owner_user_id', table_name='file_objects')
        except Exception as e:
            logger.debug("ix_file_objects_owner_user_id not dropped or not present: %s", e)
        try:
            op.create_index(OWNER_IDX_NAME, 'file_objects', ['owner_user_id'])
        except Exception as e:
            logger.error("Failed to create %s: %s", OWNER_IDX_NAME, e, exc_info=True)

        # Ensure purpose column uses enum type where possible (Postgres). Attempt ALTER TYPE conversion.
        try:
            # Attempt to alter column type to the enum file_purpose
            op.execute("ALTER TABLE file_objects ALTER COLUMN purpose TYPE file_purpose USING purpose::file_purpose;")
        except Exception as e:
            logger.error("Failed to alter purpose to file_purpose enum: %s", e, exc_info=True)

        # Tokens: add file_object_id column and constraints
        try:
            op.add_column('tokens', sa.Column('file_object_id', sa.String(length=36), nullable=True))
        except Exception as e:
            logger.error("Failed to add file_object_id to tokens: %s", e, exc_info=True)
        try:
            op.create_foreign_key('fk_tokens_file_object_id', 'tokens', 'file_objects', ['file_object_id'], ['id'], ondelete='CASCADE')
        except Exception as e:
            logger.error("Failed to create fk_tokens_file_object_id: %s", e, exc_info=True)

        # Add strict check constraint for tokens
        try:
            op.create_check_constraint('chk_tokens_expires_after_created', 'tokens', 'expires_at > created_at')
        except Exception as e:
            logger.error("Failed to create chk_tokens_expires_after_created: %s", e, exc_info=True)

        # Ensure token_hash uniqueness
        try:
            op.create_unique_constraint('uq_tokens_token_hash', 'tokens', ['token_hash'])
        except Exception as e:
            logger.error("Failed to create uq_tokens_token_hash: %s", e, exc_info=True)

        # Create token indexes avoiding duplicate ix_tokens_user_id
        try:
            op.create_index('idx_tokens_type', 'tokens', ['type'])
        except Exception as e:
            logger.error("Failed to create idx_tokens_type: %s", e, exc_info=True)
        try:
            op.create_index('idx_tokens_file_object_id', 'tokens', ['file_object_id'])
        except Exception as e:
            logger.error("Failed to create idx_tokens_file_object_id: %s", e, exc_info=True)
        try:
            op.create_index('idx_tokens_expires_at', 'tokens', ['expires_at'])
        except Exception as e:
            logger.error("Failed to create idx_tokens_expires_at: %s", e, exc_info=True)

    else:
        # SQLite: recreate tables to enforce constraints reliably
        # Turn off FK checks during table swap
        op.execute('PRAGMA foreign_keys=OFF')

        # Create new file_objects with desired constraints and correct index name
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS file_objects_new (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'private',
                purpose TEXT NOT NULL,
                original_filename TEXT,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                storage_path TEXT NOT NULL UNIQUE,
                created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                deleted_at DATETIME,
                CHECK(size_bytes >= 0),
                FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        # Copy data if present
        try:
            op.execute(
                "INSERT INTO file_objects_new (id, owner_user_id, visibility, purpose, original_filename, content_type, size_bytes, storage_path, created_at, deleted_at) SELECT id, owner_user_id, visibility, purpose, original_filename, content_type, size_bytes, storage_path, created_at, deleted_at FROM file_objects"
            )
        except Exception as e:
            logger.debug("No data copied into file_objects_new or copy failed: %s", e)

        try:
            op.execute('DROP TABLE IF EXISTS file_objects')
        except Exception as e:
            logger.error("Failed to drop old file_objects: %s", e, exc_info=True)
        op.execute('ALTER TABLE file_objects_new RENAME TO file_objects')

        # Create indexes with spec-consistent names
        try:
            op.create_index(OWNER_IDX_NAME, 'file_objects', ['owner_user_id'])
        except Exception as e:
            logger.error("Failed to create %s on sqlite: %s", OWNER_IDX_NAME, e, exc_info=True)
        try:
            op.create_index('idx_file_objects_visibility', 'file_objects', ['visibility'])
        except Exception as e:
            logger.error("Failed to create idx_file_objects_visibility on sqlite: %s", e, exc_info=True)

        # Recreate tokens table with new columns and constraints
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens_new (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                expires_at DATETIME NOT NULL,
                used_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                file_object_id TEXT,
                CHECK (expires_at > created_at),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(file_object_id) REFERENCES file_objects(id) ON DELETE CASCADE
            )
            """
        )
        try:
            op.execute(
                "INSERT INTO tokens_new (id, user_id, token_hash, type, expires_at, used_at, created_at, file_object_id) SELECT id, user_id, token_hash, type, expires_at, used_at, created_at, NULL FROM tokens"
            )
        except Exception as e:
            logger.debug("No data copied into tokens_new or copy failed: %s", e)
        try:
            op.execute('DROP TABLE IF EXISTS tokens')
        except Exception as e:
            logger.error("Failed to drop old tokens: %s", e, exc_info=True)
        op.execute('ALTER TABLE tokens_new RENAME TO tokens')

        try:
            op.create_index('ix_tokens_user_id', 'tokens', ['user_id'])
        except Exception as e:
            logger.error("Failed to create ix_tokens_user_id on sqlite: %s", e, exc_info=True)
        try:
            op.create_index('idx_tokens_type', 'tokens', ['type'])
        except Exception as e:
            logger.error("Failed to create idx_tokens_type on sqlite: %s", e, exc_info=True)
        try:
            op.create_index('idx_tokens_file_object_id', 'tokens', ['file_object_id'])
        except Exception as e:
            logger.error("Failed to create idx_tokens_file_object_id on sqlite: %s", e, exc_info=True)
        try:
            op.create_index('idx_tokens_expires_at', 'tokens', ['expires_at'])
        except Exception as e:
            logger.error("Failed to create idx_tokens_expires_at on sqlite: %s", e, exc_info=True)

        # Re-enable FK enforcement
        op.execute('PRAGMA foreign_keys=ON')


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Drop token-related indexes and constraints
    try:
        op.drop_index('idx_tokens_expires_at', table_name='tokens')
    except Exception as e:
        logger.debug("idx_tokens_expires_at not present or drop failed: %s", e)
    try:
        op.drop_index('idx_tokens_file_object_id', table_name='tokens')
    except Exception as e:
        logger.debug("idx_tokens_file_object_id not present or drop failed: %s", e)
    try:
        op.drop_index('idx_tokens_type', table_name='tokens')
    except Exception as e:
        logger.debug("idx_tokens_type not present or drop failed: %s", e)

    try:
        op.drop_constraint('chk_tokens_expires_after_created', 'tokens', type_='check')
    except Exception as e:
        logger.debug("chk_tokens_expires_after_created not present or drop failed: %s", e)
    try:
        op.drop_constraint('fk_tokens_file_object_id', 'tokens', type_='foreignkey')
    except Exception as e:
        logger.debug("fk_tokens_file_object_id not present or drop failed: %s", e)
    try:
        op.drop_constraint('uq_tokens_token_hash', 'tokens', type_='unique')
    except Exception as e:
        logger.debug("uq_tokens_token_hash not present or drop failed: %s", e)
    try:
        op.drop_column('tokens', 'file_object_id')
    except Exception as e:
        logger.debug("file_object_id column not present or drop failed: %s", e)

    if dialect == 'sqlite':
        # Recreate prior file_objects shape without CHECK/UNIQUE/updated_at
        op.execute('PRAGMA foreign_keys=OFF')
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS file_objects_old (
                id TEXT PRIMARY KEY,
                owner_user_id TEXT,
                visibility TEXT,
                purpose TEXT,
                original_filename TEXT,
                content_type TEXT,
                size_bytes INTEGER,
                storage_path TEXT,
                created_at DATETIME,
                deleted_at DATETIME,
                FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        try:
            op.execute(
                "INSERT INTO file_objects_old (id, owner_user_id, visibility, purpose, original_filename, content_type, size_bytes, storage_path, created_at, deleted_at) SELECT id, owner_user_id, visibility, purpose, original_filename, content_type, size_bytes, storage_path, created_at, deleted_at FROM file_objects"
            )
        except Exception as e:
            logger.debug("No data copied into file_objects_old or copy failed: %s", e)
        try:
            op.execute('DROP TABLE IF EXISTS file_objects')
        except Exception as e:
            logger.error("Failed to drop file_objects during downgrade: %s", e, exc_info=True)
        op.execute('ALTER TABLE file_objects_old RENAME TO file_objects')
        op.execute('PRAGMA foreign_keys=ON')

        # Recreate tokens prior shape
        op.execute('PRAGMA foreign_keys=OFF')
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens_old (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                type TEXT NOT NULL,
                expires_at DATETIME NOT NULL,
                used_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        try:
            op.execute(
                "INSERT INTO tokens_old (id, user_id, token_hash, type, expires_at, used_at, created_at) SELECT id, user_id, token_hash, type, expires_at, used_at, created_at FROM tokens"
            )
        except Exception as e:
            logger.debug("No data copied into tokens_old or copy failed: %s", e)
        try:
            op.execute('DROP TABLE IF EXISTS tokens')
        except Exception as e:
            logger.error("Failed to drop tokens during downgrade: %s", e, exc_info=True)
        op.execute('ALTER TABLE tokens_old RENAME TO tokens')
        op.execute('PRAGMA foreign_keys=ON')
        try:
            op.create_index('ix_tokens_user_id', 'tokens', ['user_id'])
        except Exception as e:
            logger.debug("Failed to create ix_tokens_user_id during downgrade: %s", e)

    else:
        # Drop file_objects added constraints and index names
        try:
            op.drop_index('idx_file_objects_visibility', table_name='file_objects')
        except Exception as e:
            logger.debug("idx_file_objects_visibility not present or drop failed: %s", e)
        try:
            op.drop_constraint('chk_file_objects_size_bytes_nonnegative', 'file_objects', type_='check')
        except Exception as e:
            logger.debug("chk_file_objects_size_bytes_nonnegative not present or drop failed: %s", e)
        try:
            op.drop_constraint('uq_file_objects_storage_path', 'file_objects', type_='unique')
        except Exception as e:
            logger.debug("uq_file_objects_storage_path not present or drop failed: %s", e)
        try:
            op.drop_index(OWNER_IDX_NAME, table_name='file_objects')
        except Exception as e:
            logger.debug("Owner index not present or drop failed: %s", e)

        # Attempt to revert purpose column to text if enum exists
        try:
            op.execute("ALTER TABLE file_objects ALTER COLUMN purpose TYPE varchar USING purpose::varchar;")
        except Exception as e:
            logger.debug("Failed to revert purpose column type: %s", e)

        # Drop updated_at column
        try:
            op.drop_column('file_objects', 'updated_at')
        except Exception as e:
            logger.debug("updated_at not present or drop failed: %s", e)
