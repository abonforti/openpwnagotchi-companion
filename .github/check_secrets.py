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

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# Files that must never be tracked at all, whatever their content.
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".srl", ".csr", ".pcap", ".pcapng"}
FORBIDDEN_NAMES = {"config.toml", "ca.crt", "server.crt", "id_rsa", "id_ed25519", ".env"}

# The one exemption, and it is narrow. Test fixtures need capture files with
# real extensions to exercise the handshake listing at all. They are synthetic
# by policy - SPEC section 13 - which is enforced by review and by the security
# auditor, not by this scanner. Nothing else in the tree gets an exemption.
FIXTURE_PREFIX = "tests/fakes/fixtures/"

# (label, pattern). Each targets a shape that is a credential in essentially every
# context, so a hit is worth a human look even when it turns out to be a sample.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # The header alone is not enough: a test that checks how malformed TLS
    # material is rejected legitimately contains one with a junk body. A real
    # key carries a long base64 payload, so that is what is matched.
    (
        "PEM private key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"
            r"[\s\S]{0,80}?[A-Za-z0-9+/=]{60,}",
        ),
    ),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # A gate of this kind may be a superset of the real shape and may not be a
    # subset, so both bounds here are the loose one where a reading is in doubt.
    #
    # The alphabet is wider than the published Gitleaks pattern's
    # `[a-z0-9]{36}`, because npm's own announcement puts a base62 CRC32
    # checksum in the last six characters and base62 has capitals in it. The
    # length is `{36,}` and not `{36}` for the same reason and not for a known
    # one: 36 is npm's shape today, a fixed count closed by `\b` matches
    # nothing at all for a body one character longer, and a token format that
    # grows is a gate that silently stops finding anything. It costs nothing:
    # `_` is a word character, so `npm_config_registry` and its kind never
    # reach 36 consecutive alphanumerics.
    ("npm access token", re.compile(r"\bnpm_[A-Za-z0-9]{36,}\b")),
    # An .npmrc is a registry credential's own file format, and this repository
    # tracks one from issue #132 onward (SPEC 5.1.1). The credential assignment
    # pattern below cannot see it: npm writes `_authToken=<value>` with no
    # quotes, and the `_` in front of `authToken` is a word character, so the
    # word boundary that pattern opens with never matches there. Matched by the
    # key rather than by the value's shape, because a registry that is not npm's
    # own issues whatever it likes.
    #
    # The registry prefix is optional, and that is the half a first version of
    # this got wrong. `npm config set _authToken <v> --location=project` writes
    # the key at column 0 with no `//registry/:` in front of it, and a pattern
    # requiring the prefix missed the exact line the file's own header promises
    # will fail the build. Verified against npm 9.2.0 rather than read off the
    # documentation, which describes the scoped form and not what the CLI does.
    #
    # The line anchor is a concession and is named as one. It spares prose that
    # writes `_authToken=<your token>` mid-sentence, and only mid-sentence: the
    # same words at column 0 still fire. What it costs is that a credential
    # pasted after a `#` in any tracked file escapes this pattern. That is a
    # limit, not a policy, and nothing may assert that a `#` defeats the
    # scanner. A quoted credential in prose is still the next pattern's
    # business.
    (
        "npm registry credential",
        re.compile(
            r"(?i)^\s*(?:(?://|@)[^\s=]*:)?_(?:authToken|auth|password)\s*=\s*\S", re.M
        ),
    ),
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
    errors: list[str] = []
    ignored: list[str] = []
    checked = 0
    exempted = 0

    for path in paths:
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path

        try:
            is_regular = stat.S_ISREG(path.stat().st_mode)
        except OSError as err:
            # Stat'd directly rather than through `Path.is_file()`, which
            # swallows a missing path, a missing parent, and a handful of
            # other common failures internally and reports False for every
            # one of them - the exact swallowing this section exists to stop
            # doing. A path that does not exist and a parent directory that
            # cannot be traversed both fail here instead, and both are an
            # error to name rather than a file that happened not to be one.
            errors.append(f"{rel}: unreadable ({err.strerror or err})")
            continue
        if not is_regular:
            # A directory (or another non-regular entry: a socket, a FIFO, a
            # device node) is not unreadable and is not this scanner's job to
            # expand into the files under it - `git ls-files` never names
            # one, and the hook of issue #203 stages files, never
            # directories. Left unscanned is still the right call. What was
            # wrong was leaving no trace: this used to `continue` here
            # without being counted anywhere, so a run handed one of these
            # alongside an exempt path could describe itself as "all exempt"
            # while one of the paths it was given was never looked at in any
            # sense. Named here so the summary below can never make that
            # claim again.
            ignored.append(str(rel))
            continue

        is_fixture = str(rel).replace(os.sep, "/").startswith(FIXTURE_PREFIX)

        if not is_fixture and (path.suffix in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES):
            findings.append(f"{rel}: this file must never be tracked")
            continue

        # This script necessarily contains the patterns it looks for. Counted
        # as exempted rather than dropped without a trace: a commit that
        # stages only this file hands the pre-commit hook of issue #203 a
        # single path, and that path being exempt is not the same thing as
        # nothing having been supplied to look at.
        if path.resolve() == SELF:
            exempted += 1
            continue

        try:
            raw = path.read_bytes()
        except OSError as err:
            # An unread file is not a file that was checked and found clean -
            # it is a file this run says nothing about at all. Reported by
            # name and counted against the run rather than skipped quietly,
            # because a warning that does not change the exit status reads
            # exactly like "checked, clean" to anything that only looks at
            # the status code.
            errors.append(f"{rel}: unreadable ({err.strerror or err})")
            continue

        # Decoded as latin-1 rather than UTF-8: every one of the 256 byte
        # values maps to a character under latin-1, so this can never raise,
        # and there is no longer a "file this scanner could not read" outcome
        # distinct from the OSError above. The ASCII range is unchanged by
        # the choice, and ASCII is the alphabet every pattern above is
        # written in, so a credential is found the same way whether the file
        # around it is text or a binary blob such as an icon or a capture.
        text = raw.decode("latin-1")

        checked += 1
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                # A PEM block spans lines, so patterns run over the whole file
                # and the line number is derived from the match offset.
                lineno = text.count("\n", 0, match.start()) + 1
                # The matched text is not echoed: it is the secret.
                findings.append(f"{rel}:{lineno}: possible {label}")

    # Three outcomes, not two. `0` is clean, `1` is a credential to act on,
    # and `2` is a run that could not be trusted to have looked at everything
    # - the reader has to be able to tell those apart from the output alone,
    # not only from the exit status.
    for line in errors:
        print(f"error: {line}", file=sys.stderr)

    for line in findings:
        print(line, file=sys.stderr)

    # This paragraph is the only actionable instruction the script ever
    # gives, so it has to survive on the exit-2 path too: a run that also
    # hit an unreadable file is exactly the run where a person is already
    # dealing with a broken gate and least likely to go looking for it.
    # Printed once, ahead of whichever return below fires, rather than
    # duplicated into both branches.
    if findings:
        print(
            f"\n{len(findings)} finding(s). Nothing is printed from the offending lines "
            "on purpose.\nIf a hit is real, the content must move outside the repository, and "
            "a credential that\nwas ever pushed must be rotated - rewriting history is not "
            "remediation on its own.",
            file=sys.stderr,
        )

    if errors:
        detail = f"{len(errors)} file(s) unreadable"
        if findings:
            detail += f", {len(findings)} finding(s) reported above"
        print(
            f"\nsecret scan: did not run to completion, {detail}. "
            "An unread file cannot be called clean, so this is reported as the gate failing, "
            "not as a pass.",
            file=sys.stderr,
        )
        return 2

    # Checked here, ahead of the vacuity check below, and not merely as an
    # optimisation: a run over nothing but by-name refusals scans zero files
    # yet has something real to report, and it has to exit 1 for that reason
    # rather than 2 for the unrelated reason that nothing was scanned.
    if findings:
        return 1

    if checked == 0 and exempted == 0:
        # A verdict earned by examining nothing is the vacuity this scanner
        # exists to refuse, but "nothing was examined" is not the same claim
        # as "nothing was scanned": a path that was refused by name already
        # returned above, and a path that was exempted counts here too,
        # because it was supplied and accounted for rather than absent. Exit
        # 2 is left for when the argument list truly gave this run nothing -
        # including a path that does not exist, which is deliberate: the
        # hook of issue #203 passes paths it staged, and a name in that list
        # that is not on disk really is a broken gate.
        print(
            "secret scan: 0 file(s) scanned, nothing was examined. Reported as the gate "
            "failing rather than as a clean run.",
            file=sys.stderr,
        )
        return 2

    if checked == 0:
        # Every path this run considered was the scanner exempting itself -
        # the only way to reach here with no findings, no errors, and
        # `exempted` above zero. No pattern was run over anything, so this
        # is not the verdict "no credential material found" earned by
        # looking: it is "there was nothing to look at". Reusing the clean
        # wording here would print a count of zero beside a clean bill of
        # health, which is the exact shape this section exists to refuse.
        #
        # `exempted` alone is not the whole story if `ignored` is not empty
        # too: a directory in the same argument list stats cleanly, is not
        # exempt, and is not scanned, so a sentence naming only `exempted`
        # would claim "all" of a set smaller than the one this run was
        # actually handed. Named rather than folded into the count, because
        # a number alone would not say what kind of path it was.
        if ignored:
            named = ", ".join(ignored)
            print(
                f"secret scan: {exempted} file(s) exempt, {len(ignored)} argument(s) not a "
                f"regular file and not scanned ({named}). Nothing to report."
            )
        else:
            print(f"secret scan: {exempted} file(s) considered, all exempt. Nothing to report.")
        return 0

    print(f"secret scan: {checked} file(s), no credential material found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
