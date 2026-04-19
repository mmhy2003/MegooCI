"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("auth_provider", sa.String(50), nullable=False, server_default="local"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("allow_ai_repo_context", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "pipelines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_repo_url", sa.String(2048), nullable=True),
        sa.Column("default_branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("definition_path", sa.String(1024), nullable=False, server_default="megooci.yaml"),
        sa.Column("definition_format", sa.String(20), nullable=False, server_default="yaml"),
        sa.Column("yaml_content", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "builds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_id", UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triggered_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("trigger_type", sa.String(50), nullable=False, server_default="manual"),
        sa.Column("params_json", JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "stages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("build_id", UUID(as_uuid=True), sa.ForeignKey("builds.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("stage_id", UUID(as_uuid=True), sa.ForeignKey("stages.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("command", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "log_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("step_id", UUID(as_uuid=True), sa.ForeignKey("steps.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stream", sa.String(10), nullable=False, server_default="stdout"),
        sa.Column("content", sa.Text, nullable=False),
    )
    op.create_index("ix_log_chunks_step_id_seq", "log_chunks", ["step_id", "seq"])

    op.create_table(
        "secrets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("secret_type", sa.String(20), nullable=False, server_default="text"),
        sa.Column("encrypted_payload", sa.LargeBinary, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "env_vars",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("is_secret_ref", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("metadata_json", JSON, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_target_type_target_id", "audit_log", ["target_type", "target_id"])

    op.create_table(
        "triggers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_id", UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("config_json", JSON, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_id", UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("secret_hash", sa.String(255), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("labels", JSON, nullable=False, server_default="[]"),
        sa.Column("os", sa.String(50), nullable=True),
        sa.Column("arch", sa.String(50), nullable=True),
        sa.Column("capacity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("build_id", UUID(as_uuid=True), sa.ForeignKey("builds.id"), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_artifacts_build_id", "artifacts", ["build_id"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("agents")
    op.drop_table("webhook_endpoints")
    op.drop_table("triggers")
    op.drop_table("audit_log")
    op.drop_table("env_vars")
    op.drop_table("secrets")
    op.drop_table("log_chunks")
    op.drop_table("steps")
    op.drop_table("stages")
    op.drop_table("builds")
    op.drop_table("pipelines")
    op.drop_table("projects")
    op.drop_table("users")
