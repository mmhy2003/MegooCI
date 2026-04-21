"""Canonical set of valid permission strings for MegooCI RBAC.

Used to validate custom role creation and as the single source of truth
for all permission-based authorization checks.
"""

VALID_PERMISSIONS: frozenset[str] = frozenset({
    "admin",
    "projects.read",
    "projects.manage",
    "pipelines.read",
    "pipelines.manage",
    "builds.read",
    "builds.manage",
    "secrets.read",
    "secrets.manage",
    "agents.read",
    "agents.manage",
    "users.read",
    "users.manage",
    "roles.manage",
    "invites.manage",
    "settings.manage",
    "git_connections.manage",
})


def validate_permissions(permissions: list[str]) -> list[str]:
    """Return a list of invalid permission strings, or empty if all OK."""
    return [p for p in permissions if p not in VALID_PERMISSIONS]
