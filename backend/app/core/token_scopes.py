"""Functional scopes for Personal Access Tokens (PATs).

A scope is a coarse, user-facing capability bundle. A token's *effective*
permissions are always the scope's permissions intersected with the owner's
role permissions (see `app.core.deps.effective_permissions`), so a scope can
only narrow access — never expand it.

This catalog is the single source of truth: the API exposes it for the UI
dropdown, the create endpoint validates against it, and the auth layer expands
stored scope keys to permissions through it.
"""

from typing import TypedDict

from app.core.permissions import VALID_PERMISSIONS

# Synthetic UI-only key meaning "no restriction". Stored as NULL in the DB.
FULL_ACCESS_KEY = "full_access"

# Every concrete permission. "admin" is a sentinel, not a concrete capability,
# so it is never grantable via a scope.
ALL_PERMISSIONS: frozenset[str] = VALID_PERMISSIONS - {"admin"}

_READ_ONLY_PERMISSIONS: frozenset[str] = frozenset(
    p for p in ALL_PERMISSIONS if p.endswith(".read")
)

class ScopeEntry(TypedDict):
    label: str
    description: str
    permissions: frozenset[str]


# key -> {label, description, permissions}. Insertion order is the UI order.
TOKEN_SCOPES: dict[str, ScopeEntry] = {
    "artifacts.download": {
        "label": "Artifacts Download",
        "description": "Download build artifacts.",
        "permissions": frozenset({"artifacts.read", "builds.read"}),
    },
    "automate.workflows": {
        "label": "Automate Workflows",
        "description": "Trigger and manage builds, and read pipeline definitions.",
        "permissions": frozenset({"builds.manage", "builds.read", "pipelines.read"}),
    },
    "read.only": {
        "label": "Read-only",
        "description": "View-only access across everything your role can see.",
        "permissions": _READ_ONLY_PERMISSIONS,
    },
}


def is_valid_scope_key(key: str) -> bool:
    """True if `key` is the Full-access key or a known scope key."""
    return key == FULL_ACCESS_KEY or key in TOKEN_SCOPES


def expand_scopes(keys: list[str]) -> set[str]:
    """Union of permissions implied by the given scope keys.

    Unknown keys contribute nothing. Never includes the "admin" sentinel.

    Do not pass FULL_ACCESS_KEY here: full access is signalled by a NULL
    `scopes` column (no restriction), not by expanding a key to a permission
    set. Passing it would yield an empty set (deny-all), not full access.
    """
    perms: set[str] = set()
    for key in keys:
        scope = TOKEN_SCOPES.get(key)
        if scope:
            perms |= scope["permissions"]
    return perms


def scope_catalog() -> list[dict]:
    """The catalog for the UI dropdown, Full access first."""
    items: list[dict] = [
        {
            "key": FULL_ACCESS_KEY,
            "label": "Full access",
            "description": "Full access with all of your role's permissions.",
        }
    ]
    items.extend(
        {"key": key, "label": scope["label"], "description": scope["description"]}
        for key, scope in TOKEN_SCOPES.items()
    )
    return items


def resolve_scope(scopes: list[str] | None) -> dict:
    """Resolve a stored `scopes` array to a `{key, label}` for display.

    NULL / empty -> Full access. An unknown/legacy key is shown verbatim.
    """
    if not scopes:
        return {"key": FULL_ACCESS_KEY, "label": "Full access"}
    key = scopes[0]
    scope = TOKEN_SCOPES.get(key)
    if scope is None:
        return {"key": key, "label": key}
    return {"key": key, "label": scope["label"]}
