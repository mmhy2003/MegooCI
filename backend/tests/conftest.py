"""Shared test fixtures.

`make_user` builds an in-memory User (no database session) for permission
tests. The functions under test only read attributes, so transient model
instances are sufficient.
"""

import pytest

from app.models.role import Role, UserRole
from app.models.user import User


@pytest.fixture
def make_user():
    def _make(
        *,
        is_admin: bool = False,
        role_permissions: set[str] | None = None,
        active_token_scopes: object = "__unset__",
        scope_type: str = "global",
        scope_id=None,
    ) -> User:
        """Build a transient User.

        active_token_scopes:
          - "__unset__" (default): JWT-style session, no active token scope.
          - None: a Full-access token.
          - ["artifacts.download"]: a scoped token.
        """
        user = User(email="t@example.com", name="Test", is_admin=is_admin, is_active=True)
        if role_permissions is not None:
            role = Role(name="role", permissions=list(role_permissions))
            user.user_roles.append(
                UserRole(role=role, scope_type=scope_type, scope_id=scope_id)
            )
        if active_token_scopes != "__unset__":
            user.active_token_scopes = active_token_scopes
        return user

    return _make
