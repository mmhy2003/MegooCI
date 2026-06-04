from app.core.permissions import VALID_PERMISSIONS
from app.core.token_scopes import (
    ALL_PERMISSIONS,
    FULL_ACCESS_KEY,
    TOKEN_SCOPES,
    expand_scopes,
    is_valid_scope_key,
    resolve_scope,
    scope_catalog,
)


def test_all_permissions_excludes_admin_sentinel():
    assert "admin" not in ALL_PERMISSIONS
    assert ALL_PERMISSIONS == VALID_PERMISSIONS - {"admin"}


def test_catalog_permissions_are_all_valid_and_not_admin():
    for scope in TOKEN_SCOPES.values():
        assert scope["permissions"] <= VALID_PERMISSIONS
        assert "admin" not in scope["permissions"]


def test_expand_scopes_unions_permissions():
    assert expand_scopes(["artifacts.download"]) == {"artifacts.read", "builds.read"}


def test_expand_scopes_ignores_unknown_keys():
    assert expand_scopes(["nope"]) == set()


def test_read_only_is_every_read_permission():
    assert expand_scopes(["read.only"]) == {
        p for p in VALID_PERMISSIONS if p.endswith(".read")
    }


def test_scope_catalog_lists_full_access_first():
    catalog = scope_catalog()
    assert [c["key"] for c in catalog] == [
        FULL_ACCESS_KEY,
        "artifacts.download",
        "automate.workflows",
        "read.only",
    ]
    assert catalog[0]["label"] == "Full access"


def test_resolve_scope_null_is_full_access():
    assert resolve_scope(None) == {"key": FULL_ACCESS_KEY, "label": "Full access"}


def test_resolve_scope_known_key():
    assert resolve_scope(["artifacts.download"]) == {
        "key": "artifacts.download",
        "label": "Artifacts Download",
    }


def test_resolve_scope_unknown_key_shows_raw():
    assert resolve_scope(["legacy.thing"]) == {
        "key": "legacy.thing",
        "label": "legacy.thing",
    }


def test_is_valid_scope_key():
    assert is_valid_scope_key(FULL_ACCESS_KEY)
    assert is_valid_scope_key("read.only")
    assert not is_valid_scope_key("bogus")
