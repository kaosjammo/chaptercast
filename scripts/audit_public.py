"""Read-only publication tripwire over reachable Git history and current files.

This cannot decide copyright or detect every secret. Review fixtures and flagged
files manually; never copy private findings into a public report.
"""

import json
from pathlib import PurePosixPath, Path
import re
import subprocess
import sys


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


PRIVATE_PARTS = {".runtime", ".evidence", ".venv", "credentials", "secrets", "uploads", "models", "outputs", "data"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".epub", ".wav", ".mp3", ".opus", ".m4a", ".m4b", ".pt", ".pth", ".onnx", ".safetensors", ".log"}
ICONS = {f"src/chaptercast/static/icons/{name}.png" for name in ("icon-192", "icon-512", "icon-maskable-512")}
PATTERNS = {
    "private_absolute_path": rb"/(?:home|Users)/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\",
    "private_tailnet_name": rb"\b[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\b",
    "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "github_token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b",
    "aws_access_key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "credential_url": rb"https?://[^\s/:@]+:[^\s/@]+@",
}


def inspect(name, data, identity):
    path = PurePosixPath(name)
    findings = []
    if (set(path.parts) & PRIVATE_PARTS or path.suffix.lower() in PRIVATE_SUFFIXES
            or (path.name.startswith(".env") and path.name != ".env.example")):
        findings.append("private_artifact_name")
    if len(data) > 2 * 1024 * 1024:
        findings.append("unexpected_large_blob")
    if name in ICONS and data.startswith(b"\x89PNG\r\n\x1a\n"):
        pass  # Original app icons; their authorship still needs human review.
    else:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            findings.append("unexpected_binary")
        if b"\x00" in data:
            findings.append("unexpected_binary")
        for label, pattern in PATTERNS.items():
            if re.search(pattern, data, re.I if label in {"private_absolute_path", "private_tailnet_name"} else 0):
                findings.append(label)
    # Never print the matching value: output belongs in ignored evidence.
    return [{"file": name, "identity": identity, "kind": kind} for kind in sorted(set(findings))]


def main():
    findings, checked = [], 0
    objects = set()
    for commit in git("rev-list", "--all").decode().splitlines():
        findings.extend(inspect("<commit metadata>", git("cat-file", "commit", commit), commit))
        for entry in git("ls-tree", "-rz", "--full-tree", commit).split(b"\x00"):
            if not entry:
                continue
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            name = name.decode()
            if mode == "120000" or kind != "blob":
                findings.append({"file": name, "identity": commit, "kind": "symlink_or_submodule_requires_review"})
            if kind == "blob":
                objects.add((oid, name))
    for oid, name in sorted(objects):
        checked += 1
        findings.extend(inspect(name, git("cat-file", "blob", oid), oid))
    names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\x00")
    for name in filter(None, names):
        path = Path(name)
        if path.is_symlink():
            findings.append({"file": name, "identity": "worktree", "kind": "symlink_requires_review"})
        elif path.is_file():
            findings.extend(inspect(name, path.read_bytes(), "worktree"))
    result = {"head": git("rev-parse", "HEAD").decode().strip(), "history_blobs_checked": checked,
              "findings": findings, "result": "PASS_TRIPWIRE" if not findings else "REVIEW_REQUIRED",
              "limits": "Pattern scan only; manual copyright, fixture authorship and unrecognised secret review required."}
    print(json.dumps(result, indent=2))
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
