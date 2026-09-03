"""The upstream MIT notice is retained verbatim, and the places that describe upstream's
licence say both things it says about itself (SPEC.md D1, F34; docs/LICENSING.md; issue #25).

Upstream `BraedenP232/PwnIOS` declares GPL3 in `pwnios.py` and MIT in its `LICENSE`. Under the
MIT reading the copyright and permission notice must be retained in copies and substantial
portions, which prose attribution does not satisfy. These tests pin that the notice is there,
whole, and that no description of upstream in this repository has gone back to naming only one
of the two, which is the drift that produced the issue.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Upstream's LICENSE file, whole, as observed at the commit F34 records. "Verbatim" means this
# block and not a sample of it: the retention sentence in the middle is the clause the issue is
# about, and a NOTICE that lost it would still contain every memorable line around it.
UPSTREAM_MIT = """\
MIT License

Copyright (c) 2025 Bready2Crumble

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def test_the_notice_file_retains_the_upstream_mit_notice_whole():
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert UPSTREAM_MIT in notice
    # The notice names this project's own licence first, so a reader who stops at the top
    # does not take the MIT text for ours.
    assert notice.index("GNU General Public License") < notice.index(UPSTREAM_MIT)


def test_the_project_licence_is_still_gpl3():
    licence = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in licence
    assert "Version 3" in licence


def _passage(path: str) -> str:
    """The one passage in each file that describes upstream's licence, not the whole file:
    `HANDSHAKE_LIMIT` contains the letters MIT and the plugin's own `__license__` says GPL3,
    so a whole-file search would pass with the passage deleted."""
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    if path == "plugin/companion.py":
        return text.split('"""', 2)[1]
    if path == "README.md":
        start = text.index("## Licence and credit")
        return text[start : text.index("pwnagotchi itself", start)]
    if path == "CLAUDE.md":
        start = text.index("## What this is")
        return text[start : text.index("## ", start + 1)]
    if path == "CONTRIBUTING.md":
        start = text.index("## Licence")
        return text[start:]
    if path == "docs/LICENSING.md":
        return text
    raise AssertionError(path)


def test_every_description_of_upstream_says_both_things():
    for path in (
        "README.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "plugin/companion.py",
        "docs/LICENSING.md",
    ):
        passage = _passage(path)
        assert "GPL3" in passage, f"{path}: the passage no longer says upstream declares GPL3"
        assert re.search(r"\bMIT\b", passage), f"{path}: the passage no longer mentions MIT"


def test_spec_d1_records_both_readings_and_points_at_the_notice():
    spec = (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8")
    d1 = next(line for line in spec.splitlines() if line.startswith("| D1 |"))
    assert "GPL-3.0" in d1 and "MIT" in d1 and "`NOTICE`" in d1
    assert "| F34 |" in spec
