"""Two-step verification: authenticator app, passkeys, recovery codes, tickets

Revision ID: 024
Revises: 023
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def _user_id(**kwargs) -> sa.Column:
    return sa.Column(
        "user_id", UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        **kwargs,
    )


def upgrade() -> None:
    op.create_table(
        "user_two_step",
        _user_id(primary_key=True),
        sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("totp_last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_codes_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_passkeys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, index=True),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", JSON(), nullable=False, server_default="[]"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, index=True),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "two_step_tickets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, index=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("webauthn_challenge", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("two_step_tickets")
    op.drop_table("user_recovery_codes")
    op.drop_table("user_passkeys")
    op.drop_table("user_two_step")
