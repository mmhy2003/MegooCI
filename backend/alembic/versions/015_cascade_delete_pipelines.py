"""Add ON DELETE CASCADE to pipeline → build → stage → step → log chain.

Revision ID: 015
Revises: 014_global_deploy_tokens
"""

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

# (table, constraint_name, local_col, remote_table.remote_col)
_FK_UPDATES = [
    ("builds",             "builds_pipeline_id_fkey",             "pipeline_id",  "pipelines.id"),
    ("stages",             "stages_build_id_fkey",                "build_id",     "builds.id"),
    ("steps",              "steps_stage_id_fkey",                 "stage_id",     "stages.id"),
    ("log_chunks",         "log_chunks_step_id_fkey",             "step_id",      "steps.id"),
    ("artifacts",          "artifacts_build_id_fkey",             "build_id",     "builds.id"),
    ("triggers",           "triggers_pipeline_id_fkey",           "pipeline_id",  "pipelines.id"),
    ("webhook_endpoints",  "webhook_endpoints_pipeline_id_fkey",  "pipeline_id",  "pipelines.id"),
]


def upgrade() -> None:
    for table, fk_name, local_col, ref in _FK_UPDATES:
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            fk_name,
            table,
            ref.split(".")[0],  # referent table
            [local_col],
            [ref.split(".")[1]],  # referent column
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table, fk_name, local_col, ref in _FK_UPDATES:
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            fk_name,
            table,
            ref.split(".")[0],
            [local_col],
            [ref.split(".")[1]],
        )
