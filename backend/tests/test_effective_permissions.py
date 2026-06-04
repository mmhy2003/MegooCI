import uuid

from app.core.deps import effective_permissions, effective_scoped_permissions


def test_full_access_non_admin_returns_role_permissions(make_user):
    user = make_user(
        role_permissions={"artifacts.read", "builds.manage"},
        active_token_scopes=None,
    )
    assert effective_permissions(user) == {"artifacts.read", "builds.manage"}


def test_jwt_session_unaffected(make_user):
    # active_token_scopes left unset -> behaves like Full access.
    user = make_user(role_permissions={"artifacts.read"})
    assert effective_permissions(user) == {"artifacts.read"}


def test_full_access_admin_includes_admin_sentinel(make_user):
    user = make_user(is_admin=True, active_token_scopes=None)
    assert "admin" in effective_permissions(user)


def test_scoped_non_admin_is_intersected_with_role(make_user):
    # scope perms {artifacts.read, builds.read} ∩ role {artifacts.read}
    user = make_user(
        role_permissions={"artifacts.read"},
        active_token_scopes=["artifacts.download"],
    )
    assert effective_permissions(user) == {"artifacts.read"}


def test_scoped_admin_is_capped_to_scope_without_admin(make_user):
    user = make_user(is_admin=True, active_token_scopes=["artifacts.download"])
    perms = effective_permissions(user)
    assert perms == {"artifacts.read", "builds.read"}
    assert "admin" not in perms


def test_scoped_resource_permissions_match_only_their_resource(make_user):
    pid = uuid.uuid4()
    user = make_user(
        role_permissions={"builds.read"},
        active_token_scopes=None,
        scope_type="project",
        scope_id=pid,
    )
    assert "builds.read" in effective_scoped_permissions(user, "project", pid)
    assert effective_scoped_permissions(user, "project", uuid.uuid4()) == set()
