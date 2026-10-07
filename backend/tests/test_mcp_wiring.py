"""Transport allow-lists and result rendering."""
from types import SimpleNamespace

from app.mcp.server import _result_text, build_transport_security


def _settings(api_url, extra="", public_url="http://localhost:3000"):
    return SimpleNamespace(
        MEGOOCI_PUBLIC_API_URL=api_url,
        MEGOOCI_PUBLIC_URL=public_url,
        MEGOOCI_MCP_ALLOWED_HOSTS=extra,
    )


def test_public_api_host_without_port_is_allowed_with_and_without_port():
    security = build_transport_security(_settings("https://ci.example.com"))
    assert "ci.example.com" in security.allowed_hosts
    assert "ci.example.com:*" in security.allowed_hosts
    assert security.enable_dns_rebinding_protection is True


def test_public_api_host_with_port_is_allowed_exactly():
    security = build_transport_security(_settings("http://10.0.0.5:8000"))
    assert "10.0.0.5:8000" in security.allowed_hosts
    assert "10.0.0.5:8000:*" not in security.allowed_hosts


def test_path_in_public_api_url_is_ignored():
    security = build_transport_security(_settings("https://example.com/megooci"))
    assert "example.com" in security.allowed_hosts


def test_localhost_is_always_allowed():
    security = build_transport_security(_settings("https://ci.example.com"))
    assert {"localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"} <= set(security.allowed_hosts)


def test_extra_hosts_are_split_trimmed_and_blank_entries_ignored():
    security = build_transport_security(
        _settings("https://ci.example.com", extra=" backend:8000 , internal.lan ,, ")
    )
    assert "backend:8000" in security.allowed_hosts
    assert "internal.lan" in security.allowed_hosts and "internal.lan:*" in security.allowed_hosts
    assert "" not in security.allowed_hosts


def test_frontend_and_api_origins_are_allowed():
    security = build_transport_security(
        _settings("https://api.example.com", public_url="https://ci.example.com")
    )
    assert "https://api.example.com" in security.allowed_origins
    assert "https://ci.example.com" in security.allowed_origins


def test_result_text_passes_strings_through():
    assert _result_text("plain log text") == "plain log text"


def test_result_text_keeps_non_ascii_readable():
    text = _result_text({"name": "Проект-β", "note": "日本語"})
    assert "Проект-β" in text and "日本語" in text
    assert "\\u" not in text
