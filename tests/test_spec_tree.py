"""The repository tree in SPEC.md section 1 against `git ls-files` (SPEC 1.1).

SPEC 1.1 states the contract this module enforces, after the tree itself named
three files that did not exist and missed a hook directory and a whole test
suite without anyone noticing by reading it:

- Every path named in the tree exists as a tracked file or a tracked
  directory.
- Every tracked directory is named in the tree, unless it sits under an
  opaque one.
- A directory whose listed entries include a bare `...` is not enumerated
  for files: extra tracked files directly inside it are fine, but its
  subdirectories are still named and still checked.
- A directory named with no children under it at all is opaque, and nothing
  inside it - files or subdirectories, at any depth - is checked. SPEC 1.1
  names exactly three: `tests/fakes/pwnagotchi_stub/`, `tests/fakes/i2c_stub/`
  and `tests/fakes/netifaces_stub/`.
- Comments after `#` are prose and are not checked.

The hard part here is the parsing, not the comparison, and a parser that
silently returns empty sets would make every "nothing is missing" assertion
pass for the wrong reason - the exact shape of defect this repository has
already shipped once (SPEC 1.1). So this module asserts the parse found what
it should find - known counts and known landmark paths - before it asserts
anything about the difference between the tree and `git ls-files`, and it
proves each of the four ways the tree can drift by constructing a minimal
tree and a minimal tracked-file list for each one, rather than only by
inspecting the real repository (which is expected to be clean).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "SPEC.md"

_TREE_SECTION_START = "## 1. Repository structure"
_TREE_SECTION_END = "### 1.1"
_CONNECTOR = re.compile("(├── |└── )")


@dataclass(frozen=True)
class ParsedTree:
    """What SPEC.md's tree, read literally, claims about the repository."""

    dirs: frozenset[str] = field(default_factory=frozenset)
    files: frozenset[str] = field(default_factory=frozenset)
    ellipsis_dirs: frozenset[str] = field(default_factory=frozenset)
    opaque_dirs: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Violation:
    kind: str
    path: str

    def __str__(self) -> str:
        if self.kind == "missing_file":
            return (
                f"SPEC.md section 1 names '{self.path}' as a file, but it is "
                "not tracked. Either the file was deleted/renamed and SPEC's "
                "tree needs the same edit, or it was never written."
            )
        if self.kind == "missing_dir":
            return (
                f"SPEC.md section 1 names '{self.path}' as a directory, but "
                "no tracked file lives under it. Either the directory is "
                "gone and SPEC's tree needs the same edit, or it was never "
                "created."
            )
        if self.kind == "undeclared_dir":
            return (
                f"'{self.path}' is a tracked directory that SPEC.md section "
                "1's tree does not name. Add a line for it (SPEC 1.1: every "
                "tracked directory is named, even under an already-listed "
                "parent whose file list ends in '...')."
            )
        if self.kind == "undeclared_file":
            return (
                f"'{self.path}' is a tracked file inside a directory whose "
                "listing in SPEC.md section 1 is exhaustive (no trailing "
                "'...'), but the file itself is not named there. Either add "
                "it to the tree or mark that directory's listing open with "
                "'...' if it is meant to grow without a SPEC edit."
            )
        raise AssertionError(f"unknown violation kind: {self.kind!r}")


def extract_tree_block(spec_text: str) -> str:
    """Pulls the fenced tree out of SPEC.md section 1, and only that block."""
    start = spec_text.index(_TREE_SECTION_START)
    end = spec_text.index(_TREE_SECTION_END, start)
    section = spec_text[start:end]
    fence_start = section.index("```") + 3
    fence_end = section.index("```", fence_start)
    return section[fence_start:fence_end]


def parse_tree(tree_text: str) -> ParsedTree:
    """Parses a `tree`-style block into the four sets SPEC 1.1 defines.

    Depth is read from the column at which the connector ('├── ' or
    '└── ') starts: each ancestor level contributes exactly 4 characters of
    prefix ('│   ' or four spaces), so that column, divided by 4, is the
    depth. The very first line (the project root itself) has no connector
    and is skipped; everything else must be a multiple of 4 or the tree is
    not in the expected format and parsing stops loudly rather than guessing.
    """
    entries: list[tuple[int, str]] = []
    for line in tree_text.splitlines():
        if not line.strip():
            continue
        match = _CONNECTOR.search(line)
        if match is None:
            continue  # the bare project-root line
        column = match.start()
        assert column % 4 == 0, (
            f"tree line does not sit on a 4-character depth boundary, column "
            f"{column}: {line!r}"
        )
        depth = column // 4
        rest = line[match.end() :]
        name = rest.split("#", 1)[0].strip()
        assert name, f"tree line has an empty entry name: {line!r}"
        entries.append((depth, name))

    dirs: set[str] = set()
    files: set[str] = set()
    ellipsis_dirs: set[str] = set()
    opaque_dirs: set[str] = set()
    stack: list[str] = []

    for index, (depth, name) in enumerate(entries):
        if name == "...":
            ellipsis_dirs.add("/".join(stack[:depth]))
            continue
        is_dir = name.endswith("/")
        clean_name = name.rstrip("/")
        full_path = "/".join(stack[:depth] + [clean_name])
        if is_dir:
            dirs.add(full_path)
            stack = stack[:depth] + [clean_name]
            next_is_child = index + 1 < len(entries) and entries[index + 1][0] > depth
            if not next_is_child:
                opaque_dirs.add(full_path)
        else:
            files.add(full_path)

    return ParsedTree(
        dirs=frozenset(dirs),
        files=frozenset(files),
        ellipsis_dirs=frozenset(ellipsis_dirs),
        opaque_dirs=frozenset(opaque_dirs),
    )


def _under_any(path: str, prefixes: frozenset[str]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _ancestor_dirs(tracked_files: list[str]) -> set[str]:
    result: set[str] = set()
    for f in tracked_files:
        parts = f.split("/")
        for i in range(1, len(parts)):
            result.add("/".join(parts[:i]))
    return result


def check_tree(tree: ParsedTree, tracked_files: list[str]) -> list[Violation]:
    """Compares a parsed tree against a list of tracked file paths.

    `tracked_files` uses forward-slash-separated paths relative to the
    repository root, the shape `git ls-files` prints.
    """
    violations: list[Violation] = []
    tracked_set = set(tracked_files)
    all_dirs = _ancestor_dirs(tracked_files)

    for named_file in sorted(tree.files):
        if named_file not in tracked_set:
            violations.append(Violation("missing_file", named_file))

    for named_dir in sorted(tree.dirs):
        if named_dir not in all_dirs:
            violations.append(Violation("missing_dir", named_dir))

    for tracked_dir in sorted(all_dirs):
        if tracked_dir in tree.dirs:
            continue
        if _under_any(tracked_dir, tree.opaque_dirs):
            continue
        violations.append(Violation("undeclared_dir", tracked_dir))

    for tracked_file in sorted(tracked_set):
        if tracked_file in tree.files:
            continue
        if _under_any(tracked_file, tree.opaque_dirs):
            continue
        parent = "/".join(tracked_file.split("/")[:-1])
        parent_is_declared = parent == "" or parent in tree.dirs
        if parent_is_declared and parent not in tree.ellipsis_dirs:
            violations.append(Violation("undeclared_file", tracked_file))

    return violations


# ---------------------------------------------------------------------------
# Violation messages: the audience for a red build is a person who has no
# idea what SPEC.md has to do with a file they just added, so the message
# text is worth asserting on its own rather than leaving unexercised.
# ---------------------------------------------------------------------------


def test_missing_file_message_names_the_path_and_the_fix():
    message = str(Violation("missing_file", "b.py"))
    assert "b.py" in message
    assert "not tracked" in message


def test_missing_dir_message_names_the_path_and_the_fix():
    message = str(Violation("missing_dir", "gone"))
    assert "gone" in message
    assert "no tracked file lives under it" in message


def test_undeclared_dir_message_names_the_path_and_the_fix():
    message = str(Violation("undeclared_dir", "surprise"))
    assert "surprise" in message
    assert "does not name" in message


def test_undeclared_file_message_names_the_path_and_the_fix():
    message = str(Violation("undeclared_file", "a/extra.py"))
    assert "a/extra.py" in message
    assert "not named there" in message


def test_unknown_violation_kind_fails_loudly_rather_than_silently():
    with pytest.raises(AssertionError, match="unknown violation kind: 'bogus'"):
        str(Violation("bogus", "irrelevant"))


# ---------------------------------------------------------------------------
# The parse itself, checked before anything is compared against it.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec_tree_text() -> str:
    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    return extract_tree_block(spec_text)


@pytest.fixture(scope="module")
def parsed(spec_tree_text: str) -> ParsedTree:
    return parse_tree(spec_tree_text)


def test_tree_block_is_extracted(spec_tree_text: str):
    """Sanity on the extraction step itself, independent of the tree parser."""
    assert "openpwnagotchi-companion/" in spec_tree_text
    assert "companion.py" in spec_tree_text
    # The heading of the next section must not have leaked into the block.
    assert "### 1.1" not in spec_tree_text


def test_parse_found_the_expected_number_of_directories(parsed: ParsedTree):
    """A parser that lost the '│   ' continuation lines or the 4-space
    indentation returns far fewer than this, or zero - this is the guard
    the task calls for: the count is asserted before any diffing happens.

    This is a floor, not the exact count (31 as of this writing), and it
    should stay a floor: an exact count makes this test a second place the
    truth lives, so every future SPEC tree edit that adds a directory would
    have to update a number here that has nothing to do with what changed.
    A floor still catches a parser that dropped the continuation lines or
    returned empty - the failure this test exists for - without demanding
    an edit for ordinary tree growth. The landmark-path tests below are the
    half of the guard a floor cannot give: they prove the parse produced the
    right entries, not merely enough of them.
    """
    assert len(parsed.dirs) >= 25


def test_parse_found_the_expected_number_of_files(parsed: ParsedTree):
    """Floor, not exact count - see the directory test above for why."""
    assert len(parsed.files) >= 80


def test_parse_found_the_three_opaque_directories(parsed: ParsedTree):
    """SPEC 1.1 names exactly three opaque directories, by name."""
    assert parsed.opaque_dirs == {
        "tests/fakes/pwnagotchi_stub",
        "tests/fakes/i2c_stub",
        "tests/fakes/netifaces_stub",
    }


def test_parse_found_the_open_listed_directories(parsed: ParsedTree):
    assert parsed.ellipsis_dirs == {
        "docs/schemas/incoming",
        "docs/schemas/outgoing",
        "frontend/src/__tests__",
        "frontend/src/__tests__/helpers",
        "frontend/tests/e2e",
        "tests",
        "tests/fakes/fixtures",
        "tests/fakes/fixtures/handshakes",
    }


@pytest.mark.parametrize(
    "landmark",
    [
        "plugin/companion.py",
        "SPEC.md",
        "docs/schemas/common.json",
        "frontend/src/lib/ws.ts",
        "frontend/src/App.svelte",
        ".claude/agents/companion-test-author.md",
        "tests/tools/test_certs.py",
        "LICENSE",
    ],
)
def test_parse_found_landmark_files(parsed: ParsedTree, landmark: str):
    assert landmark in parsed.files


@pytest.mark.parametrize(
    "landmark",
    [
        ".github/workflows",
        "docs/schemas",
        "frontend/src/lib",
        "tests/fakes",
        "tools",
    ],
)
def test_parse_found_landmark_directories(parsed: ParsedTree, landmark: str):
    assert landmark in parsed.dirs


# ---------------------------------------------------------------------------
# The real repository against the real SPEC.md.
# ---------------------------------------------------------------------------


def test_repository_tree_matches_spec(parsed: ParsedTree):
    tracked = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=True,
    ).stdout.splitlines()
    assert tracked, "git ls-files returned nothing; is this actually a git checkout?"

    violations = check_tree(parsed, tracked)
    if violations:
        details = "\n".join(f"- {v}" for v in violations)
        pytest.fail(
            "SPEC.md section 1's repository tree disagrees with `git ls-files`:\n"
            f"{details}"
        )


# ---------------------------------------------------------------------------
# The four ways the tree can drift, each established by construction.
# ---------------------------------------------------------------------------


def test_clean_tree_reports_no_violations():
    tree = parse_tree(
        "root/\n"
        "├── a/\n"
        "│   └── one.py\n"
        "└── b.py\n"
    )
    tracked = ["a/one.py", "b.py"]
    assert check_tree(tree, tracked) == []


def test_named_file_that_does_not_exist_is_reported():
    tree = parse_tree(
        "root/\n"
        "├── a/\n"
        "│   └── one.py\n"
        "└── b.py\n"
    )
    tracked = ["a/one.py"]  # b.py is named but never written

    violations = check_tree(tree, tracked)

    assert Violation("missing_file", "b.py") in violations


def test_named_directory_that_does_not_exist_is_reported():
    tree = parse_tree(
        "root/\n"
        "├── a/\n"
        "│   └── one.py\n"
        "├── gone/\n"
        "│   └── two.py\n"
        "└── b.py\n"
    )
    tracked = ["a/one.py", "b.py"]  # nothing tracked lives under gone/

    violations = check_tree(tree, tracked)

    assert Violation("missing_dir", "gone") in violations


def test_tracked_directory_nobody_named_is_reported():
    tree = parse_tree(
        "root/\n"
        "├── a/\n"
        "│   └── one.py\n"
        "└── b.py\n"
    )
    tracked = ["a/one.py", "b.py", "surprise/three.py"]  # surprise/ is not in the tree

    violations = check_tree(tree, tracked)

    assert Violation("undeclared_dir", "surprise") in violations


def test_tracked_file_in_an_enumerated_directory_nobody_named_is_reported():
    tree = parse_tree(
        "root/\n"
        "├── a/\n"
        "│   └── one.py\n"
        "└── b.py\n"
    )
    # a/ has no trailing '...', so its file listing is exhaustive.
    tracked = ["a/one.py", "a/extra.py", "b.py"]

    violations = check_tree(tree, tracked)

    assert Violation("undeclared_file", "a/extra.py") in violations


def test_open_listed_directory_tolerates_an_unnamed_file():
    tree = parse_tree(
        "root/\n"
        "├── open/\n"
        "│   └── ...\n"
        "└── b.py\n"
    )
    tracked = ["open/whatever.py", "b.py"]

    assert check_tree(tree, tracked) == []


def test_open_listed_directory_still_requires_its_subdirectories_named():
    tree = parse_tree(
        "root/\n"
        "├── open/\n"
        "│   └── ...\n"
        "└── b.py\n"
    )
    tracked = ["open/whatever.py", "open/nested/deep.py", "b.py"]

    violations = check_tree(tree, tracked)

    assert Violation("undeclared_dir", "open/nested") in violations


def test_opaque_directory_tolerates_an_unnamed_subdirectory():
    tree = parse_tree(
        "root/\n"
        "├── opaque/\n"
        "└── b.py\n"
    )
    tracked = ["opaque/anything/at/any/depth.py", "b.py"]

    assert check_tree(tree, tracked) == []


def test_opaque_directory_tolerates_an_unnamed_direct_file():
    """A file directly inside the opaque directory, not nested further.

    This is distinct from the previous test: there, the file's immediate
    parent ('opaque/anything/at/any') is itself undeclared, so the
    undeclared_file check never fires regardless of whether the opaque
    exemption is applied to files at all - it is silently masked by the
    undeclared_dir check instead. Putting the file directly under the named
    opaque directory means its parent ('opaque') *is* declared, so only the
    file-level opaque exemption can be why no violation is reported here.
    """
    tree = parse_tree(
        "root/\n"
        "├── opaque/\n"
        "└── b.py\n"
    )
    tracked = ["opaque/direct.py", "b.py"]

    assert check_tree(tree, tracked) == []
