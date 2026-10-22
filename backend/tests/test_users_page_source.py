"""The users page offers one Administrator or Member choice and per-project
roles only. The frontend has no test harness, so its sources are read."""
import pathlib

import pytest

FRONTEND_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
USERS = FRONTEND_SRC / "components" / "users"
PAGE = FRONTEND_SRC / "app" / "admin" / "users" / "page.tsx"


def _read(path: pathlib.Path) -> str:
    if not FRONTEND_SRC.exists():
        pytest.skip("the frontend sources are not part of this checkout")
    return path.read_text(encoding="utf-8")


def test_the_users_page_has_no_global_role_assignment():
    page = _read(PAGE)
    for gone in ("Assign role", "openAssignRole", "Initial role", "Make admin"):
        assert gone not in page, gone


def test_the_old_project_editor_is_gone():
    _read(PAGE)
    assert not (FRONTEND_SRC / "components" / "ProjectAssignmentsEditor.tsx").exists()
    importers = [
        p for p in FRONTEND_SRC.rglob("*.tsx")
        if "ProjectAssignmentsEditor" in p.read_text(encoding="utf-8")
    ]
    assert importers == []


def test_invitations_show_administrator_or_member():
    tab = _read(USERS / "InvitationsTab.tsx")
    assert "Administrator" in tab and "Member" in tab
    assert "Role:" not in tab


def test_project_roles_offered_exclude_admin_permission():
    assert "export function isAssignablePerProject" in _read(USERS / "access.ts")
    assert "isAssignablePerProject" in _read(USERS / "ProjectAccessEditor.tsx")


def test_escape_closes_only_the_topmost_dialog():
    """A dialog opened inside another (copy access inside create user) must
    not take the outer one, and its one-time password, with it on Escape."""
    dialog = _read(FRONTEND_SRC / "components" / "ui" / "dialog.tsx")
    assert "openDialogs" in dialog
    assert "openDialogs[openDialogs.length - 1] === token" in dialog


def test_the_project_picker_keeps_escape_to_itself():
    picker = _read(USERS / "ProjectMultiSelect.tsx")
    assert 'e.key === "Escape"' in picker and "e.stopPropagation()" in picker
