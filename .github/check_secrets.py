#!/usr/bin/env python3
"""Fails the build on key material or credentials in tracked files.

This is the generic half of the project's leak defence, and it is the only half
that can run in CI. The owner-specific check - hostnames, private addresses,
machine names - runs locally in .githooks/pre-commit against a denylist held
deliberately outside this repository, because a runner that knew those terms
would itself be the leak (SPEC.md section 13). So: CI catches secrets that look
like secrets to anyone; the local gate catches what is only sensitive here.

Do not treat a green run as proof the diff is safe to publish. It proves no
recognisable credential is present, nothing more.

Usage: python .github/check_secrets.py [path ...]
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# Files that must never be tracked at all, whatever their content.
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".srl", ".csr", ".pcap", ".pcapng"}
FORBIDDEN_NAMES = {"config.toml", "ca.crt", "server.crt", "id_rsa", "id_ed25519", ".env"}

# (label, pattern). Each targets a shape that is a credential in essentially every
# context, so a hit is worth a human look even when it turns out to be a sample.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PEM private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("private key in a URL", re.compile(r"://[^/\s:@]+:[^/\s:@]{6,}@")),
    (
        "credential assignment",
        # Requires a non-trivial literal: `token = ""`, `password = <redacted>` and
        # placeholders in documentation must not trip this.
        re.compile(
            r"""(?ix)
            \b (?: password | passwd | secret | api[_-]?key | auth[_-]?token
                 | access[_-]?token | private[_-]?key | client[_-]?secret )
            \s* [:=] \s*
            ["'] (?! \s* $ )
            (?! (?: \.\.\. | x{3,} | \* {3,} | < | \{ | \$ | your | example | changeme
                  | redacted | placeholder | none | null | true | false ) )
            [^"'\s]{8,}
            ["']
            """
        ),
    ),
]


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [REPO_ROOT / name for name in out.split("\0") if name]


def main() -> int:
    paths = [Path(a).resolve() for a in sys.argv[1:]] or tracked_files()
    findings: list[str] = []
    skipped: list[str] = []
    checked = 0

    for path in paths:
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path

        if path.suffix in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
            findings.append(f"{rel}: this file must never be tracked")
            continue

        # This script necessarily contains the patterns it looks for.
        if path.resolve() == SELF:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as err:
            # Say so rather than skipping quietly. A binary blob carrying key
            # material would otherwise produce no output at all, and silence
            # reads identically to "checked, clean".
            kind = "not UTF-8" if isinstance(err, UnicodeDecodeError) else "unreadable"
            skipped.append(f"{rel}: {kind}, not scanned")
            continue

        checked += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    # The matched text is not echoed: it is the secret.
                    findings.append(f"{rel}:{lineno}: possible {label}")

    for line in skipped:
        print(f"warning: {line}", file=sys.stderr)

    for line in findings:
        print(line, file=sys.stderr)

    if findings:
        print(
            f"\n{len(findings)} finding(s). Nothing is printed from the offending lines "
            "on purpose.\nIf a hit is real, the content must move outside the repository, and "
            "a credential that\nwas ever pushed must be rotated - rewriting history is not "
            "remediation on its own.",
            file=sys.stderr,
        )
        return 1

    suffix = f", {len(skipped)} not scanned (see warnings above)" if skipped else ""
    print(f"secret scan: {checked} file(s), no credential material found{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
