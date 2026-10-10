"""Prepare an inactive user-service bundle; never installs, starts or configures Serve.

Run from the deployment checkout with its locked dev+tts .venv already installed.
Outputs contain local paths/hostnames and belong only in ignored runtime storage.
"""

import argparse
from pathlib import Path

from chaptercast.config import Settings


def quote_env(value):
    value = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("paths must not contain control characters")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def quote_unit(value, *, executable=False):
    # systemd specifiers and ExecStart environment expansion are not shell syntax.
    value = str(value).replace('%', '%%')
    return quote_env(value.replace('$', '$$') if executable else value)


def unit_path(value):
    # WorkingDirectory and append: log paths are literal, not quoted argv items.
    value = str(value)
    quote_env(value)
    if '\\' in value or '"' in value or value != value.strip():
        raise ValueError("deployment paths must not contain quotes, backslashes or edge whitespace")
    return value.replace('%', '%%')


def prepare(project, data_dir, hostname, port):
    project, root = Path(project).resolve(), Path(data_dir).resolve()
    Settings(port=port, data_dir=root, trusted_hosts=(hostname,))
    for path in (project, root):
        unit_path(path)  # Validate before making any directories.
    if not (project / "pyproject.toml").is_file() or not (project / ".venv/bin/python").is_file():
        raise ValueError("prepare the deployment checkout and its .venv first")
    bundle = root / "deployment"
    if bundle.exists():
        raise FileExistsError("deployment bundle already exists; review and move it aside while stopped")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise ValueError("runtime directory must be private (mode 0700)")
    for name in ("uploads", "models", "audio", "logs"):
        path = root / name
        if path.is_symlink():
            raise ValueError("runtime subdirectories must not be symlinks")
        path.mkdir(mode=0o700, exist_ok=True)
        if path.stat().st_mode & 0o077:
            raise ValueError("runtime subdirectories must be private (mode 0700)")
    bundle.mkdir(mode=0o700)
    environment = "\n".join([
        "# Private deployment settings; loaded by systemd, not by ChapterCast.",
        "CHAPTERCAST_HOST=127.0.0.1", f"CHAPTERCAST_PORT={port}",
        f"CHAPTERCAST_DATA_DIR={quote_env(root)}", "CHAPTERCAST_INFERENCE_THREADS=4",
        f"CHAPTERCAST_TRUSTED_HOSTS={hostname}", "FORWARDED_ALLOW_IPS=127.0.0.1",
        "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1",
        f"HF_HOME={quote_env(root / 'models/hub')}",
        f"XDG_CACHE_HOME={quote_env(root / 'cache')}", "PYTHONUNBUFFERED=1", "",
    ])
    unit = f"""# Generated privately. Controller installs and activates after review.
[Unit]
Description=ChapterCast private chapter narration
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
WorkingDirectory={unit_path(project)}
EnvironmentFile={unit_path(bundle / 'chaptercast.env')}
ExecStart={quote_unit(project / '.venv/bin/python', executable=True)} -m chaptercast
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=mixed
UMask=0077
NoNewPrivileges=true
StandardOutput=append:{unit_path(root / 'logs/service.log')}
StandardError=inherit

[Install]
WantedBy=default.target
"""
    for name, text in (("chaptercast.env", environment), ("chaptercast.service", unit)):
        with (bundle / name).open("x") as stream:
            (bundle / name).chmod(0o600)
            stream.write(text)
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--data-dir", type=Path, default=Path(".runtime"))
    parser.add_argument("--hostname", required=True, help="Approved exact private HTTPS hostname, without port")
    parser.add_argument("--port", type=int, default=8000, help="Unused loopback application port")
    args = parser.parse_args()
    try:
        prepare(args.project, args.data_dir, args.hostname, args.port)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Preparation failed: {exc}\n")
    print("Prepared inactive private bundle under the data directory's deployment/. No service or route changed.")


if __name__ == "__main__":
    main()
