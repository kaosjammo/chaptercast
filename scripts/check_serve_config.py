"""Read-only check of private Serve JSON snapshots. Never contacts Tailscale.

Before activation, omit --after to check that the chosen port is unused.
After activation, require precisely one root HTTPS proxy addition and no other
changes, including Funnel flags. Unknown changes fail closed for manual review.
"""

import argparse
import copy
import json
from pathlib import Path

from chaptercast.config import Settings


def validate_port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")


def check_unused(config, port):
    validate_port(port)
    if not isinstance(config, dict):
        raise ValueError("Serve snapshot must be an object")
    key = str(port)
    if (key in config.get("TCP", {}) or any(
        authority.rsplit(":", 1)[-1] == key
        for section in ("Web", "AllowFunnel") for authority in config.get(section, {})
    )):
        raise ValueError("selected Serve port is occupied")
    for session in config.get("Foreground", {}).values():
        check_unused(session, port)


def normalise(config):
    if not isinstance(config, dict):
        raise ValueError("Serve snapshot must be an object")
    # Only known empty maps can be omitted by the CLI's JSON serializer.
    optional = {"TCP", "Web", "AllowFunnel", "Foreground"}
    return {key: value for key, value in config.items()
            if key not in optional or (value != {} and value is not None)}


def check_addition(before, after, hostname, https_port, local_port):
    Settings(port=local_port, trusted_hosts=(hostname,))
    check_unused(before, https_port)
    authority = f"{hostname}:{https_port}"
    expected = copy.deepcopy(before)
    expected.setdefault("TCP", {})[str(https_port)] = {"HTTPS": True}
    expected.setdefault("Web", {})[authority] = {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{local_port}"}}}
    if normalise(after) != normalise(expected):
        raise ValueError("Serve changes differ from the dedicated private HTTPS addition; review private snapshots")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--https-port", type=int, required=True)
    parser.add_argument("--hostname")
    parser.add_argument("--local-port", type=int, default=8000)
    args = parser.parse_args()
    try:
        before = json.loads(args.before.read_text())
        if args.after:
            if not args.hostname:
                raise ValueError("--hostname is required with --after")
            check_addition(before, json.loads(args.after.read_text()), args.hostname, args.https_port, args.local_port)
        else:
            check_unused(before, args.https_port)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Config check failed: {exc}\n")
    print("PASS dedicated private addition" if args.after else "PASS unused Serve port in snapshot")


if __name__ == "__main__":
    main()
