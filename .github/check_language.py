#!/usr/bin/env python3
"""Fails the build on non-English content in tracked files.

The repository is English-only (SPEC.md D16): code, comments, docstrings, log
strings, documentation. The rule exists because the project is public and a
half-translated codebase is harder for a contributor to read than one written
badly in a single language.

A rule nobody can check is a rule nobody follows, so this is that check. It is
deliberately narrow: a curated list of words that are unambiguously Italian and
long enough not to collide with identifiers, matched on word boundaries. It will
not catch a fluent Italian sentence built entirely from short words, and it is
not meant to - it catches the realistic case, which is a comment written without
thinking about it.

False positives are expected occasionally. Add the offending line to
.github/language-allowlist.txt rather than weakening the word list, so the
exception is visible and reviewable.

Usage: python .github/check_language.py [path ...]
Exits non-zero on the first file with findings; reports every finding first.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = Path(__file__).resolve().parent / "language-allowlist.txt"

SUFFIXES = {".py", ".ts", ".mts", ".mjs", ".js", ".svelte", ".sh", ".md", ".yml", ".yaml"}

# This file is excluded from its own scan: it necessarily contains the very words
# it looks for. Anything else that must legitimately carry them belongs in the
# allowlist, one full line per entry, so the exception is explicit.
SELF = Path(__file__).resolve()

# Unambiguously Italian, four characters or more.
#
# Two categories are deliberately absent, and both omissions were forced by the
# check's own first run:
#
#   - Short function words (il, la, di, per, con): they collide with identifiers
#     and with ordinary prose in other languages.
#   - Italian words that are also English words. `serve`, `come`, `fare`, `solo`
#     and `prima` all appear legitimately in this repository - `websockets.serve`,
#     "types come from", "serve index.html". A check that cries wolf gets
#     ignored, and an ignored check is worse than no check, so accuracy beats
#     coverage here.
WORDS = [
    "perche", "perché", "poiche", "poiché", "pero", "però", "gia", "già",
    "cosi", "così", "piu", "più", "anziche", "anziché", "affinche", "affinché",
    "della", "dello", "delle", "degli", "dalla", "dallo", "dalle",
    "nella", "nello", "nelle", "negli", "sulla", "sullo", "sulle",
    "questo", "questa", "questi", "queste", "quello", "quella", "quelli",
    "quando", "quindi", "invece", "oppure", "senza", "ancora", "sempre",
    "viene", "vengono", "essere", "fatto", "servono", "occorre",
    "deve", "devono", "possiamo", "abbiamo", "siamo", "sono", "stato",
    "adesso", "allora", "anche", "dopo", "molto", "meglio",
    "niente", "nulla", "tutto", "tutti", "tutte", "ogni", "qualcosa",
    "errore", "esempio", "funzione", "variabile", "modifica", "aggiunge",
    "restituisce", "controlla", "verifica", "impostazione", "configurazione",
]

PATTERN = re.compile(r"\b(" + "|".join(sorted(set(WORDS), key=len, reverse=True)) + r")\b", re.IGNORECASE)


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [REPO_ROOT / name for name in out.split("\0") if name]


def load_allowlist() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    allowed = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            allowed.add(line)
    return allowed


def candidates(argv: list[str]) -> list[Path]:
    if argv:
        return [Path(a).resolve() for a in argv]
    return [p for p in tracked_files() if p.suffix in SUFFIXES and p.resolve() != SELF]


def main() -> int:
    allowed = load_allowlist()
    findings: list[tuple[Path, int, str, str]] = []
    checked = 0

    for path in candidates(sys.argv[1:]):
        if not path.is_file():
            continue
        checked += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped in allowed:
                continue
            hit = PATTERN.search(line)
            if hit:
                findings.append((path, lineno, hit.group(0), stripped[:100]))

    for path, lineno, word, line in findings:
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
        print(f"{rel}:{lineno}: non-English word {word!r}: {line}")

    if findings:
        print(
            f"\n{len(findings)} finding(s) in {checked} file(s). "
            "The repository is English-only (SPEC.md D16).\n"
            "If a hit is legitimate, add the full line to .github/language-allowlist.txt.",
            file=sys.stderr,
        )
        return 1

    print(f"language check: {checked} file(s), all English.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
