import uuid

import pytest
from fastapi import HTTPException

from app.core.deps import (
    check_scoped_permission,
    get_current_admin_user,
    require_permission,
)


async def test_require_permission_allows_when_scope_grants(make_user):
    user = make_user(
        role_permissions={"artifacts.read"},
        active_token_scopes=["artifacts.download"],
    )
    check = require_permission("artifacts.read")
    assert await check(current_user=user) is user


async def test_require_permission_denies_out_of_scope(make_user, monkeypatch):
    async def _noop(**kwargs):
        return None

    # The deny path schedules a fire-and-forget audit record; stub it out.
    monkeypatch.setattr("app.core.audit.record", _noop)
    user = make_user(
        role_permissions={"artifacts.read", "builds.manage"},
        active_token_scopes=["artifacts.download"],  # excludes builds.manage
    )
    check = require_permission("builds.manage")
    with pytest.raises(HTTPException) as exc:
        await check(current_user=user)
    assert exc.value.status_code == 403


async def test_full_access_admin_allowed_on_admin_endpoint(make_user):
    user = make_user(is_admin=True, active_token_scopes=None)
    assert await get_current_admin_user(current_user=user) is user


async def test_scoped_admin_token_denied_on_admin_endpoint(make_user):
    user = make_user(is_admin=True, active_token_scopes=["artifacts.download"])
    with pytest.raises(HTTPException) as exc:
        await get_current_admin_user(current_user=user)
    assert exc.value.status_code == 403


def test_check_scoped_permission_denies_out_of_scope(make_user):
    pid = uuid.uuid4()
    user = make_user(
        role_permissions={"builds.manage"},
        active_token_scopes=["artifacts.download"],
        scope_type="project",
        scope_id=pid,
    )
    with pytest.raises(HTTPException) as exc:
        check_scoped_permission(user, "builds.manage", "project", pid)
    assert exc.value.status_code == 403


def test_collect_permissions_reflects_token_scope(make_user):
    # External readouts (/me, search, AI assistant) must see the scoped set,
    # not the full role permissions.
    from app.core.deps import _collect_permissions

    user = make_user(
        role_permissions={"artifacts.read", "builds.manage"},
        active_token_scopes=["artifacts.download"],
    )
    assert _collect_permissions(user) == {"artifacts.read"}
