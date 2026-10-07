"""MCP settings have safe defaults and read from the environment."""
from app.config import Settings


def test_mcp_defaults(monkeypatch):
    monkeypatch.delenv("MEGOOCI_MCP_ENABLED", raising=False)
    monkeypatch.delenv("MEGOOCI_MCP_ALLOWED_HOSTS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.MEGOOCI_MCP_ENABLED is True
    assert settings.MEGOOCI_MCP_ALLOWED_HOSTS == ""


def test_mcp_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("MEGOOCI_MCP_ENABLED", "false")
    monkeypatch.setenv("MEGOOCI_MCP_ALLOWED_HOSTS", "backend:8000,internal.lan")
    settings = Settings(_env_file=None)
    assert settings.MEGOOCI_MCP_ENABLED is False
    assert settings.MEGOOCI_MCP_ALLOWED_HOSTS == "backend:8000,internal.lan"
