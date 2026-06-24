"""Convert global developer/viewer roles to per-project assignments

Revision ID: 023
Revises: 022
Create Date: 2026-06-23
"""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    project_ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM projects")).fetchall()]
    nonadmin = [r[0] for r in conn.execute(
        sa.text("SELECT id FROM roles WHERE name IN ('developer','viewer')")
    ).fetchall()]
    if not nonadmin:
        return
    rows = conn.execute(sa.text(
        "SELECT id, user_id, role_id FROM user_roles "
        "WHERE scope_type='global' AND role_id = ANY(:rids)"
    ), {"rids": nonadmin}).fetchall()
    for ur_id, user_id, role_id in rows:
        for pid in project_ids:
            conn.execute(sa.text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_type, scope_id) "
                "VALUES (:id, :uid, :rid, 'project', :pid)"
            ), {"id": str(uuid.uuid4()), "uid": user_id, "rid": role_id, "pid": pid})
        conn.execute(sa.text("DELETE FROM user_roles WHERE id=:id"), {"id": ur_id})


def downgrade() -> None:
    # Best-effort: collapse each user's project-scoped developer/viewer rows back
    # to a single global row of that role, then drop the project rows.
    conn = op.get_bind()
    nonadmin = [r[0] for r in conn.execute(
        sa.text("SELECT id FROM roles WHERE name IN ('developer','viewer')")
    ).fetchall()]
    if not nonadmin:
        return
    pairs = conn.execute(sa.text(
        "SELECT DISTINCT user_id, role_id FROM user_roles "
        "WHERE scope_type='project' AND role_id = ANY(:rids)"
    ), {"rids": nonadmin}).fetchall()
    conn.execute(sa.text(
        "DELETE FROM user_roles WHERE scope_type='project' AND role_id = ANY(:rids)"
    ), {"rids": nonadmin})
    for user_id, role_id in pairs:
        conn.execute(sa.text(
            "INSERT INTO user_roles (id, user_id, role_id, scope_type, scope_id) "
            "VALUES (:id, :uid, :rid, 'global', NULL) ON CONFLICT DO NOTHING"
        ), {"id": str(uuid.uuid4()), "uid": user_id, "rid": role_id})
