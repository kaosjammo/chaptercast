from pathlib import Path

import pytest

from chaptercast.config import Settings


def test_defaults_are_loopback_and_project_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings.from_env({})
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.data_dir == tmp_path / ".runtime"


def test_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings.from_env({
        "CHAPTERCAST_HOST": "::1",
        "CHAPTERCAST_PORT": "8123",
        "CHAPTERCAST_DATA_DIR": "private-data",
    })
    assert settings.host == "::1"
    assert settings.port == 8123
    assert settings.data_dir == tmp_path / "private-data"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.1", "example.com"])
def test_non_loopback_hosts_are_rejected(host):
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_env({"CHAPTERCAST_HOST": host})


@pytest.mark.parametrize("port", ["0", "65536", "invalid"])
def test_invalid_ports_are_rejected(port):
    with pytest.raises(ValueError, match="port"):
        Settings.from_env({"CHAPTERCAST_PORT": port})


def test_direct_settings_validate_binding(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        Settings(host="0.0.0.0", data_dir=Path(tmp_path))
