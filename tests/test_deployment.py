"""Original deployment fixtures; no service or tailnet is changed by tests."""

import importlib.util
from pathlib import Path
import stat
import shutil
import subprocess

import pytest


def script(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_is_private_inactive_and_refuses_replacement(tmp_path):
    prepare = script("prepare_service")
    project = tmp_path / "project with spaces"
    (project / ".venv/bin").mkdir(parents=True)
    (project / ".venv/bin/python").touch()
    (project / "pyproject.toml").touch()
    root = project / ".runtime"
    bundle = prepare.prepare(project, root, "private.example", 8123)
    unit = (bundle / "chaptercast.service").read_text()
    env = (bundle / "chaptercast.env").read_text()
    assert 'ExecStart="' in unit and '.venv/bin/python" -m chaptercast' in unit
    assert "Restart=on-failure" in unit and "KillMode=mixed" in unit
    assert "UMask=0077" in unit and "StandardOutput=append:" in unit
    assert "CHAPTERCAST_HOST=127.0.0.1" in env
    assert "CHAPTERCAST_PORT=8123" in env
    assert "CHAPTERCAST_TRUSTED_HOSTS=private.example" in env
    assert "FORWARDED_ALLOW_IPS=127.0.0.1" in env
    assert "HF_HUB_OFFLINE=1" in env
    for name in ("uploads", "models", "audio", "logs", "deployment"):
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o700
    assert stat.S_IMODE((bundle / "chaptercast.env").stat().st_mode) == 0o600
    assert not (root / "metadata.sqlite3").exists()
    with pytest.raises(FileExistsError):
        prepare.prepare(project, root, "private.example", 8123)
    assert (bundle / "chaptercast.env").read_text() == env


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze unavailable")
def test_systemd_accepts_generated_paths_with_spaces(tmp_path):
    project = tmp_path / "project with spaces"
    (project / ".venv/bin").mkdir(parents=True)
    executable = project / ".venv/bin/python"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (project / "pyproject.toml").touch()
    bundle = script("prepare_service").prepare(project, project / ".runtime", "private.example", 8123)
    result = subprocess.run(["systemd-analyze", "--user", "verify", str(bundle / "chaptercast.service")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr


@pytest.mark.parametrize("hostname,port", [("*", 8000), ("https://private.example", 8000), ("private.example:8443", 8000), ("private.example", 0)])
def test_invalid_preparation_makes_no_runtime(tmp_path, hostname, port):
    with pytest.raises(ValueError):
        script("prepare_service").prepare(tmp_path, tmp_path / ".runtime", hostname, port)
    assert not (tmp_path / ".runtime").exists()


def test_systemd_path_escaping():
    prepare = script("prepare_service")
    assert prepare.quote_unit('/tmp/price$100%/"folder', executable=True) == '"/tmp/price$$100%%/\\"folder"'
    assert prepare.unit_path('/tmp/price$100%/folder with spaces') == '/tmp/price$100%%/folder with spaces'
    assert prepare.quote_env('/tmp/price$100%/"folder') == '"/tmp/price$100%/\\"folder"'
    with pytest.raises(ValueError):
        prepare.quote_unit("/tmp/injected\nEnvironment=unsafe")


def serve_config():
    # Original synthetic tailnet config with an unrelated route and Funnel entry.
    return {"TCP": {"443": {"HTTPS": True}},
            "Web": {"other.example:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9000"}}}},
            "AllowFunnel": {"other.example:443": False}}


def test_only_dedicated_addition_is_accepted():
    import copy
    checker = script("check_serve_config")
    before = serve_config()
    after = copy.deepcopy(before)
    after["TCP"]["8443"] = {"HTTPS": True}
    after["Web"]["private.example:8443"] = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8123"}}}
    checker.check_unused(before, 8443)
    checker.check_addition(before, after, "private.example", 8443, 8123)
    assert before == serve_config()
    with pytest.raises(ValueError, match="occupied"):
        checker.check_unused(before, 443)
    with pytest.raises(ValueError):
        checker.check_addition(before, after, "private.example", 8443, 8000)
    after["AllowFunnel"]["private.example:8443"] = True
    with pytest.raises(ValueError):
        checker.check_addition(before, after, "private.example", 8443, 8123)


@pytest.mark.parametrize("change", ["existing_route", "extra_route", "tcp", "funnel", "unknown_field", "empty_unknown"])
def test_unrelated_config_changes_fail(change):
    import copy
    checker = script("check_serve_config")
    before = serve_config()
    after = copy.deepcopy(before)
    after["TCP"]["8443"] = {"HTTPS": True}
    after["Web"]["private.example:8443"] = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8123"}}}
    if change == "existing_route":
        after["Web"]["other.example:443"]["Handlers"] = {}
    elif change == "extra_route":
        after["Web"]["private.example:8443"]["Handlers"]["/extra"] = {"Text": "unexpected"}
    elif change == "tcp":
        after["TCP"]["8443"] = {"TCPForward": "127.0.0.1:8123"}
    elif change == "funnel":
        after["AllowFunnel"]["other.example:443"] = True
    elif change == "unknown_field":
        after["Services"] = {"unexpected": {}}
    else:
        after["Unknown"] = {}
    with pytest.raises(ValueError):
        checker.check_addition(before, after, "private.example", 8443, 8123)


def test_empty_config_and_foreground_port_collisions():
    checker = script("check_serve_config")
    after = {"TCP": {"8443": {"HTTPS": True}}, "Web": {
        "private.example:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}}}
    checker.check_addition({}, after, "private.example", 8443, 8000)
    for before in ({"Web": {"other.example:8443": {}}}, {"AllowFunnel": {"other.example:8443": False}},
                   {"Foreground": {"session": {"TCP": {"8443": {"HTTPS": True}}}}}):
        with pytest.raises(ValueError, match="occupied"):
            checker.check_unused(before, 8443)
