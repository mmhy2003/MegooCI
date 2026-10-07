"""GET /system/info advertises the MCP endpoint for the Settings page."""
from types import SimpleNamespace

from app.api.v1.system import SystemInfo, _build_mcp_info


def _settings(enabled=True, api_url="http://localhost:8000"):
    return SimpleNamespace(MEGOOCI_MCP_ENABLED=enabled, MEGOOCI_PUBLIC_API_URL=api_url)


def test_url_is_the_public_api_url_plus_mcp():
    info = _build_mcp_info(_settings(api_url="https://ci.example.com"))
    assert info.enabled is True
    assert info.url == "https://ci.example.com/mcp"


def test_trailing_slash_is_not_doubled():
    assert _build_mcp_info(_settings(api_url="https://ci.example.com/")).url == (
        "https://ci.example.com/mcp"
    )


def test_disabled_flag_is_reported():
    assert _build_mcp_info(_settings(enabled=False)).enabled is False


def test_system_info_has_an_mcp_field():
    assert "mcp" in SystemInfo.model_fields
