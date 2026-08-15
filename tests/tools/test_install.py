"""`tools/install-on-pi.sh`, driven for real into a `tmp_path`.

The contract under test is SPEC section 5.3, plus pinned facts F23, F24 and F25
for the one thing this script must not get wrong: the custom plugins directory.
The image ships two disagreeing values for it (F25), so a hardcoded path picks
the wrong one on some units and the plugin silently never loads - the unit keeps
running, the web root serves the app, and the only symptom is a PWA that
connects to nothing.

Everything here drives `--archive` with a tarball built in the test. SPEC 5.3
names that flag as the way to exercise the script without a network, and a test
that reaches GitHub is a test that fails on a train.

The script is invoked through `sh` rather than executed directly, for the same
reason as `test_certs.py`: SPEC 5.3 requires POSIX `sh` and `/bin/sh` on
Raspberry Pi OS is dash, so a bashism has to fail here rather than on the unit.

Assumptions this file makes where SPEC 5.3 is silent - each one is reported as
an ambiguity to settle in SPEC, and each one is isolated in a named helper or
constant here so that settling it is a one-line change:

- the release archive holds the built PWA at its root (`index.html`,
  `assets/...`), not under a `dist/` prefix;
- with `--archive FILE`, the `SHA256SUMS` that SPEC says is "fetched alongside"
  is the one sitting next to `FILE`, in `sha256sum` output format;
- `plugin/companion.py` comes from the checkout the script lives in, since the
  release archive of SPEC 5.2 contains only `frontend/dist`.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL = REPO_ROOT / "tools" / "install-on-pi.sh"
PLUGIN_SOURCE = REPO_ROOT / "plugin" / "companion.py"

# F23: the `defaults.toml` value, and the only fallback the script may use when
# the running configuration does not name one. F25: the shell profile shipped
# with the image points at `/etc/pwnagotchi/custom-plugins/` instead, so this
# constant appearing in a resolution the config was supposed to drive is a bug,
# and the profile path appearing anywhere is a hardcoded guess.
F23_DEFAULT_PLUGINS_DIR = "/usr/local/share/pwnagotchi/custom-plugins/"
F25_PROFILE_ALIAS_DIR = "/etc/pwnagotchi/custom-plugins/"

# F24: the user configuration merged over the defaults.
F24_DEFAULT_CONFIG = "/etc/pwnagotchi/config.toml"

DEFAULT_WEB_ROOT = "/var/www/openpwn-companion"

REQUIRED_TOOLS = ("sh", "tar", "sha256sum")
_MISSING = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]

pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"not on PATH: {', '.join(_MISSING)}; SPEC 5.3 requires all of them",
)


# ---------------------------------------------------------------------------
# Building the things the script consumes
# ---------------------------------------------------------------------------


def pwa_files(version: str) -> dict[str, bytes]:
    """A plausible `frontend/dist`, with the version woven into the content.

    Two builds must be distinguishable byte for byte, otherwise an update that
    silently did nothing looks exactly like an update that worked.
    """
    return {
        "index.html": f"<!doctype html><title>Companion {version}</title>".encode(),
        "assets/app.js": f"export const VERSION = '{version}';\n".encode(),
        "assets/app.css": f"/* {version} */\nbody{{margin:0}}\n".encode(),
        "manifest.webmanifest": b'{"name":"OpenPwnagotchi Companion"}\n',
        "sw.js": b"self.addEventListener('fetch', function () {});\n",
        "icons/icon-192.png": b"\x89PNG\r\n\x1a\n" + version.encode(),
    }


def build_archive(path: Path, members: dict[str, bytes]) -> Path:
    """Writes a gzipped tar holding exactly `members`, by name and content.

    Built with `tarfile` rather than by shelling out to `tar` so that a member
    name no sane packer would emit - `../escape` - can still be put inside one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 1700000000
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return path


def write_sha256sums(archive: Path, *, digest: str | None = None, name: str | None = None) -> Path:
    """The `SHA256SUMS` next to the archive, in `sha256sum` output format.

    `digest` and `name` override what is written, which is how a mismatch and a
    checksum for somebody else's file are produced.
    """
    if digest is None:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    line = f"{digest}  {name or archive.name}\n"
    sums = archive.parent / "SHA256SUMS"
    sums.write_text(line)
    return sums


def tree(root: Path) -> dict[str, bytes]:
    """Every path under `root`, by relative name and content.

    Directories and symlinks are recorded too: an install that replaced a file
    with a link to it, or left an empty scratch directory behind, is not the
    same tree even though every file still reads the same.
    """
    if not root.exists():
        return {}
    snapshot: dict[str, bytes] = {}
    for entry in sorted(root.rglob("*")):
        rel = str(entry.relative_to(root))
        if entry.is_symlink():
            snapshot[rel + " -> "] = os.readlink(entry).encode()
        elif entry.is_file():
            snapshot[rel] = entry.read_bytes()
        elif entry.is_dir():
            snapshot[rel + "/"] = b""
    return snapshot


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# Configuration fixtures
#
# The two spellings below are both real. `defaults.toml` and the user
# `config.toml` shipped by the image use flat dotted keys, while a hand-edited
# file is as likely to carry a `[main]` table. A resolver that understands only
# one of them reads the wrong directory on half the units in the field, which is
# precisely the F25 failure the resolution exists to avoid.
# ---------------------------------------------------------------------------

CONFIG_FORMS = {
    "dotted": 'main.custom_plugins = "{path}"\n',
    "dotted-no-spaces": 'main.custom_plugins="{path}"\n',
    "dotted-single-quotes": "main.custom_plugins = '{path}'\n",
    "table": '[main]\ncustom_plugins = "{path}"\n',
    "table-indented": '[main]\n    custom_plugins = "{path}"\n',
}

CONFIG_PREAMBLE = 'main.name = "testunit"\nmain.lang = "en"\n'


def config_with(path: str | Path, form: str = "dotted") -> str:
    return CONFIG_PREAMBLE + CONFIG_FORMS[form].format(path=path)


@dataclass
class Pi:
    """The pretend unit: a web root, a plugins directory, a config, a release."""

    root: Path
    web_root: Path
    plugins_dir: Path
    config: Path
    release: Path

    # -- invocation ---------------------------------------------------------

    def run(self, *args: str) -> subprocess.CompletedProcess:
        """Runs the script non-interactively.

        `stdin` is closed and the run is bounded: SPEC 5.3 requires a script
        that never prompts, and a prompt must fail the suite rather than hang
        it.
        """
        assert INSTALL.is_file(), (
            f"{INSTALL} does not exist; SPEC section 5.3 requires it"
        )
        return subprocess.run(
            ["sh", str(INSTALL), *args],
            cwd=str(self.root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def install(self, *extra: str, archive: Path | None = None) -> subprocess.CompletedProcess:
        args = ["--web-root", str(self.web_root), "--config", str(self.config)]
        if archive is not None:
            args += ["--archive", str(archive)]
        return self.run(*args, *extra)

    # -- release artefacts --------------------------------------------------

    def archive(self, version: str = "1.0.0", *, name: str = "dist.tgz") -> Path:
        """A release archive plus its matching `SHA256SUMS`."""
        path = build_archive(self.release / name, pwa_files(version))
        write_sha256sums(path)
        return path

    # -- observation --------------------------------------------------------

    @property
    def installed_plugin(self) -> Path:
        return self.plugins_dir / "companion.py"

    @property
    def previous(self) -> Path:
        return self.web_root.with_name(self.web_root.name + ".previous")

    def state(self) -> tuple[dict[str, bytes], dict[str, bytes]]:
        """Everything an install is allowed to touch, in one comparable value.

        The web root's *parent* is snapshotted rather than the web root itself,
        so a stray `.previous`, a leftover extraction directory or a rotation
        that accumulated shows up as a difference.
        """
        return tree(self.web_root.parent), tree(self.plugins_dir)


@pytest.fixture
def pi(tmp_path) -> Pi:
    root = tmp_path / "unit"
    srv = root / "srv"
    plugins_dir = root / "usr" / "local" / "share" / "pwnagotchi" / "custom-plugins"
    etc = root / "etc"
    release = root / "release"
    for directory in (srv, plugins_dir, etc, release):
        directory.mkdir(parents=True)
    config = etc / "config.toml"
    config.write_text(config_with(plugins_dir))
    return Pi(
        root=root,
        web_root=srv / "openpwn-companion",
        plugins_dir=plugins_dir,
        config=config,
        release=release,
    )


# ---------------------------------------------------------------------------
# The happy path: both halves land (SPEC 5.3 items 1 and 2)
# ---------------------------------------------------------------------------


def test_a_local_archive_installs_and_exits_zero(pi):
    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr


def test_the_pwa_lands_in_the_web_root(pi):
    pi.install(archive=pi.archive("1.0.0"))

    assert (pi.web_root / "index.html").read_bytes() == pwa_files("1.0.0")["index.html"]


def test_the_whole_archive_lands_with_its_layout_intact(pi):
    """A PWA is not one file.

    A service worker or a hashed asset that did not survive the unpack breaks
    the app in a way that looks like a caching problem on the phone.
    """
    pi.install(archive=pi.archive("1.0.0"))

    assert tree(pi.web_root) == {
        **pwa_files("1.0.0"),
        "assets/": b"",
        "icons/": b"",
    }


def test_the_plugin_lands_in_the_resolved_plugins_directory(pi):
    """SPEC 5.3 item 1: the unit needs the plugin, not just the web root.

    A run that installs the PWA and leaves `companion.py` to a manual `scp`
    produces a working web server serving an app with nothing to talk to.
    """
    pi.install(archive=pi.archive())

    assert pi.installed_plugin.read_bytes() == PLUGIN_SOURCE.read_bytes()


def test_the_web_root_is_created_when_it_does_not_exist(pi):
    assert not pi.web_root.exists()

    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()


def test_the_plugins_directory_is_created_when_it_does_not_exist(pi, tmp_path):
    # A unit that has never had a custom plugin has no such directory, and
    # F23's default path is absent on a stock image until something creates it.
    target = tmp_path / "brand" / "new" / "custom-plugins"
    pi.config.write_text(config_with(target))

    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").read_bytes() == PLUGIN_SOURCE.read_bytes()


def test_the_installed_pwa_is_world_readable(pi):
    # The HTTPS static server of SPEC 2.15 reads these files; a mode carried
    # over from a private extraction directory serves 403s for every asset.
    pi.install(archive=pi.archive())

    for name in ("index.html", "assets/app.js"):
        assert mode_of(pi.web_root / name) & 0o004, f"{name} is not world readable"


# ---------------------------------------------------------------------------
# Checksum verification (SPEC 5.3: "verified before anything is unpacked")
# ---------------------------------------------------------------------------


def test_a_mismatched_checksum_is_a_hard_failure(pi):
    """An archive that fails its checksum is corrupt or hostile, and no third
    case exists, so it is refused rather than unpacked with a warning."""
    archive = pi.archive()
    write_sha256sums(archive, digest="0" * 64)

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert result.stderr.strip(), "a checksum failure must say so on stderr"


def test_a_mismatched_checksum_writes_nothing_at_all(pi):
    archive = pi.archive()
    write_sha256sums(archive, digest="0" * 64)

    pi.install(archive=archive)

    assert pi.state() == ({}, {})


def test_a_mismatched_checksum_leaves_an_existing_installation_byte_identical(pi):
    """The case the rule exists for.

    A failed update that half-replaced a working install is worse than no
    update: the unit is now broken and the previous version is gone.
    """
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = pi.state()

    bad = build_archive(pi.release / "dist.tgz", pwa_files("2.0.0"))
    write_sha256sums(bad, digest="0" * 64)
    result = pi.install(archive=bad)

    assert result.returncode == 1
    assert pi.state() == before


def test_a_checksum_that_matches_installs(pi):
    # The positive control for the two tests above: they must fail because the
    # digest is wrong, not because the file was rejected for some other reason.
    archive = build_archive(pi.release / "dist.tgz", pwa_files("1.0.0"))
    write_sha256sums(archive)

    result = pi.install(archive=archive)

    assert result.returncode == 0, result.stderr


def test_an_absent_sha256sums_is_a_failure(pi):
    """Unverifiable is not the same as verified.

    SPEC 5.3 makes verification unconditional, so a missing checksum file
    cannot degrade into installing anyway.
    """
    archive = build_archive(pi.release / "dist.tgz", pwa_files("1.0.0"))

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == ({}, {})


def test_a_sha256sums_that_names_a_different_file_is_a_failure(pi):
    # A checksum file that happens to be lying around, for some other release,
    # provides no digest for this archive - and matching "some line in the
    # file" instead of "the line for this file" is how that passes anyway.
    archive = pi.archive()
    write_sha256sums(archive, name="some-other-release.tgz")

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == ({}, {})


def test_a_truncated_archive_with_a_stale_checksum_is_refused(pi):
    """The interrupted-download shape: the file is short, the digest is the
    one for the whole file."""
    archive = pi.archive("1.0.0")
    full = archive.read_bytes()
    write_sha256sums(archive)
    archive.write_bytes(full[: len(full) // 2])

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == ({}, {})


# ---------------------------------------------------------------------------
# Resolving the custom plugins directory (SPEC 5.3 item 1; F23, F24, F25)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("form", sorted(CONFIG_FORMS), ids=sorted(CONFIG_FORMS))
def test_the_plugins_directory_is_read_from_the_config(pi, tmp_path, form):
    """Both spellings are real: the image writes dotted keys, a hand-edited
    file carries a `[main]` table, and a resolver that reads only one of them
    silently installs into the wrong directory on the other kind of unit."""
    target = tmp_path / "configured" / form
    pi.config.write_text(config_with(target, form))

    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").read_bytes() == PLUGIN_SOURCE.read_bytes()


def test_a_trailing_slash_on_the_configured_value_is_tolerated(pi, tmp_path):
    # F23's own default value ends in a slash, so the value that ships with the
    # image is exactly the shape a naive concatenation gets wrong.
    target = tmp_path / "configured-with-slash"
    pi.config.write_text(config_with(str(target) + "/"))

    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").is_file()


def test_the_configured_directory_is_used_and_the_f23_default_is_not(pi, tmp_path):
    """The whole point of F25: never write to a guessed path.

    An install that lands in the default directory while the config names
    another one leaves the plugin somewhere pwnagotchi will not look.
    """
    target = tmp_path / "configured"
    pi.config.write_text(config_with(target))

    result = pi.install("--dry-run", archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout
    assert F23_DEFAULT_PLUGINS_DIR not in result.stdout
    assert F25_PROFILE_ALIAS_DIR not in result.stdout


# The fallback cases are asserted through `--dry-run`, because the answer they
# must produce is the real F23 path and a test is not allowed to write there.
FALLBACK_CONFIGS = {
    # F23: `load_from_path` runs only `if 'custom_plugins' in config['main']`,
    # so the key really is optional and its absence is the common case.
    "key-absent": 'main.name = "testunit"\nmain.lang = "en"\n',
    "commented-dotted": '# main.custom_plugins = "/commented/out"\nmain.name = "x"\n',
    "commented-in-table": '[main]\nname = "x"\n#custom_plugins = "/commented/out"\n',
    "empty-file": "",
    # A different table entirely: the key name is not unique in a pwnagotchi
    # configuration, and grepping the file for it finds somebody else's.
    "other-table": '[main.plugins.someplugin]\ncustom_plugins = "/decoy/table"\n',
    "other-dotted-key": 'main.plugins.other.custom_plugins = "/decoy/dotted"\n',
    "commented-out-header": '#[main]\ncustom_plugins = "/decoy/commented-header"\n',
}


@pytest.mark.parametrize("body", sorted(FALLBACK_CONFIGS), ids=sorted(FALLBACK_CONFIGS))
def test_the_f23_default_is_used_when_the_config_names_no_directory(pi, body):
    pi.config.write_text(FALLBACK_CONFIGS[body])

    result = pi.install("--dry-run", archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert F23_DEFAULT_PLUGINS_DIR in result.stdout, (
        "with no `main.custom_plugins` the only permitted answer is the "
        f"defaults.toml value (F23); got:\n{result.stdout}"
    )
    assert "/decoy/" not in result.stdout
    assert "/commented/out" not in result.stdout


def test_the_f23_default_is_used_when_the_config_file_is_missing(pi, tmp_path):
    missing = tmp_path / "no" / "such" / "config.toml"

    result = pi.install("--dry-run", "--config", str(missing), archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert F23_DEFAULT_PLUGINS_DIR in result.stdout


def test_the_f25_profile_alias_is_never_used_as_a_fallback(pi):
    """F25 in one assertion.

    `/etc/pwnagotchi/custom-plugins/` is what the shipped shell profile aliases
    `custom` to, and it is *not* the `defaults.toml` value. Falling back to it
    is the specific guess this resolution exists to prevent.
    """
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--dry-run", archive=pi.archive())

    assert F25_PROFILE_ALIAS_DIR not in result.stdout


def test_the_default_config_path_is_the_f24_user_config(pi):
    # With no `--config`, SPEC 5.3 pins `/etc/pwnagotchi/config.toml` (F24).
    result = pi.run(
        "--web-root", str(pi.web_root), "--dry-run", "--archive", str(pi.archive())
    )

    assert result.returncode == 0, result.stderr
    assert F24_DEFAULT_CONFIG in result.stdout


def test_the_default_web_root_is_the_documented_one(pi):
    # SPEC 2.2 and 5.3 both name it, and the plugin's `web_root` default has to
    # agree with wherever the installer actually puts the files.
    result = pi.run("--dry-run", "--config", str(pi.config), "--archive", str(pi.archive()))

    assert result.returncode == 0, result.stderr
    assert DEFAULT_WEB_ROOT in result.stdout


def test_an_explicit_plugins_dir_overrides_the_config(pi, tmp_path):
    target = tmp_path / "explicit"
    pi.config.write_text(config_with(tmp_path / "from-config"))

    result = pi.install("--plugins-dir", str(target), archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").is_file()
    assert not (tmp_path / "from-config").exists()


def test_the_config_file_is_never_edited(pi):
    """SPEC 5.3: it does not edit `config.toml`.

    Enabling the plugin is the user's step. A script that rewrites the main
    configuration of a running pwnagotchi can break far more than it installs.
    """
    before = pi.config.read_bytes(), mode_of(pi.config)

    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    pi.install("--dry-run", archive=pi.archive("2.0.0"))
    pi.install(archive=pi.archive("2.0.0"))

    assert (pi.config.read_bytes(), mode_of(pi.config)) == before


def test_the_config_file_is_not_edited_even_when_the_key_is_absent(pi):
    # The tempting repair - "the key is missing, let me add it" - is exactly
    # what SPEC 5.3 forbids.
    pi.config.write_text('main.name = "testunit"\n')
    before = pi.config.read_bytes()

    pi.install("--dry-run", archive=pi.archive())

    assert pi.config.read_bytes() == before


# ---------------------------------------------------------------------------
# Tar path traversal
# ---------------------------------------------------------------------------


TRAVERSAL_MEMBERS = {
    "parent": "../escaped.txt",
    "deep-parent": "../../escaped.txt",
    "nested-parent": "assets/../../escaped.txt",
    "absolute": "/escaped.txt",
    "absolute-nested": "/etc/pwnagotchi/escaped.txt",
    "dot-dot-alone": "..",
}


@pytest.mark.parametrize("member", sorted(TRAVERSAL_MEMBERS), ids=sorted(TRAVERSAL_MEMBERS))
def test_an_archive_that_escapes_the_web_root_is_refused(pi, member):
    """A release asset is untrusted input the moment its origin is.

    The checksum here is correct on purpose: this is about the *contents* of an
    archive that verified, which is the case a checksum cannot speak to.
    """
    members = dict(pwa_files("1.0.0"))
    members[TRAVERSAL_MEMBERS[member]] = b"escaped\n"
    archive = build_archive(pi.release / "dist.tgz", members)
    write_sha256sums(archive)

    result = pi.install(archive=archive)

    assert result.returncode == 1, (
        "an archive carrying an escaping member must be refused, not unpacked"
    )
    assert result.stderr.strip()


@pytest.mark.parametrize("member", sorted(TRAVERSAL_MEMBERS), ids=sorted(TRAVERSAL_MEMBERS))
def test_a_traversing_archive_writes_nothing_outside_the_target(pi, tmp_path, member):
    before = tree(tmp_path)

    members = dict(pwa_files("1.0.0"))
    members[TRAVERSAL_MEMBERS[member]] = b"escaped\n"
    archive = build_archive(pi.release / "dist.tgz", members)
    write_sha256sums(archive)
    pi.install(archive=archive)

    after = tree(tmp_path)
    escaped = {
        name: content
        for name, content in after.items()
        if content == b"escaped\n" or name.endswith("escaped.txt")
    }
    assert escaped == {}, f"the archive escaped into {sorted(escaped)}"
    # Nothing new anywhere outside the web root and the plugins directory
    # either: an "escape" that merely lands in the wrong sibling is still a
    # write the caller did not ask for.
    unexpected = {
        name
        for name in set(after) - set(before)
        if not name.startswith(("unit/srv", "unit/usr", "unit/release"))
    }
    assert unexpected == set(), sorted(unexpected)


def test_a_traversing_archive_leaves_an_existing_installation_intact(pi):
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = pi.state()

    members = dict(pwa_files("2.0.0"))
    members["../escaped.txt"] = b"escaped\n"
    archive = build_archive(pi.release / "dist.tgz", members)
    write_sha256sums(archive)
    pi.install(archive=archive)

    assert pi.state() == before


def test_an_archive_that_is_not_a_tarball_is_refused(pi):
    """Garbage with a correct digest: the checksum passed and the unpack still
    has to fail closed, leaving the web root as it was."""
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = pi.state()

    archive = pi.release / "dist.tgz"
    archive.write_bytes(b"this is not a gzipped tar archive\n")
    write_sha256sums(archive)
    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == before


# ---------------------------------------------------------------------------
# The previous web root (SPEC 5.3: "kept as <web-root>.previous")
# ---------------------------------------------------------------------------


def test_an_update_keeps_what_was_installed_before(pi):
    """The first thing anyone wants after a bad update is the version that
    worked."""
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    first = tree(pi.web_root)

    assert pi.install(archive=pi.archive("2.0.0")).returncode == 0

    assert tree(pi.previous) == first
    assert (pi.web_root / "index.html").read_bytes() == pwa_files("2.0.0")["index.html"]


def test_a_second_update_replaces_the_previous_rather_than_accumulating(pi):
    """`.previous.previous`, `.previous.1`, `.previous.2`: a rotation that
    grows fills the unit's SD card one release at a time."""
    for version in ("1.0.0", "2.0.0", "3.0.0"):
        assert pi.install(archive=pi.archive(version)).returncode == 0

    assert tree(pi.previous) == pwa_files("2.0.0") | {"assets/": b"", "icons/": b""}
    assert {entry.name for entry in pi.web_root.parent.iterdir()} == {
        pi.web_root.name,
        pi.previous.name,
    }


def test_a_first_install_leaves_no_previous(pi):
    # There is nothing to keep, and an empty `.previous` invites a rollback to
    # an empty web root.
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0

    assert not pi.previous.exists()


def test_a_failed_update_does_not_rotate_the_previous(pi):
    """A rotation on a failed update destroys the only working copy while
    leaving the broken one in place."""
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    assert pi.install(archive=pi.archive("2.0.0")).returncode == 0
    before = pi.state()

    bad = build_archive(pi.release / "dist.tgz", pwa_files("3.0.0"))
    write_sha256sums(bad, digest="0" * 64)
    assert pi.install(archive=bad).returncode == 1

    assert pi.state() == before


def test_the_previous_is_not_rotated_by_a_plugin_only_run(pi):
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = tree(pi.web_root)

    assert pi.install("--plugin-only", archive=pi.archive("2.0.0")).returncode == 0

    assert tree(pi.web_root) == before
    assert not pi.previous.exists()


# ---------------------------------------------------------------------------
# Idempotence (SPEC 5.3: "idempotent and re-runnable")
# ---------------------------------------------------------------------------


def test_installing_the_same_archive_twice_leaves_the_same_tree(pi):
    archive = pi.archive("1.0.0")
    assert pi.install(archive=archive).returncode == 0
    first = tree(pi.web_root), tree(pi.plugins_dir)

    assert pi.install(archive=archive).returncode == 0

    assert (tree(pi.web_root), tree(pi.plugins_dir)) == first


def test_a_third_identical_run_changes_nothing_at_all(pi):
    """From the second run onward the whole tree, `.previous` included, has
    reached a fixed point. Anything still moving is state that grows."""
    archive = pi.archive("1.0.0")
    for _ in range(2):
        assert pi.install(archive=archive).returncode == 0
    after_two = pi.state()

    assert pi.install(archive=archive).returncode == 0

    assert pi.state() == after_two


def test_re_running_leaves_no_extraction_scratch_behind(pi):
    """SPEC 5.3 extracts beside the target and swaps in.

    A scratch directory left next to the web root is served by the HTTPS
    server as a second, stale copy of the app.
    """
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    assert pi.install(archive=pi.archive("2.0.0")).returncode == 0

    assert {entry.name for entry in pi.web_root.parent.iterdir()} == {
        pi.web_root.name,
        pi.previous.name,
    }


def test_an_update_removes_files_the_new_build_no_longer_ships(pi):
    """The swap is a replacement, not a merge.

    A hashed asset from the old build that survives is a file the service
    worker will never reference again and the server will happily serve.
    """
    stale = dict(pwa_files("1.0.0"))
    stale["assets/legacy-chunk.js"] = b"// removed in 2.0.0\n"
    first = build_archive(pi.release / "old.tgz", stale)
    write_sha256sums(first)
    assert pi.install(archive=first).returncode == 0

    assert pi.install(archive=pi.archive("2.0.0")).returncode == 0

    assert not (pi.web_root / "assets" / "legacy-chunk.js").exists()


# ---------------------------------------------------------------------------
# Halves, usage errors and dry runs (SPEC 5.3)
# ---------------------------------------------------------------------------


def test_plugin_only_installs_the_plugin_and_leaves_the_web_root_alone(pi):
    result = pi.install("--plugin-only", archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert pi.installed_plugin.read_bytes() == PLUGIN_SOURCE.read_bytes()
    assert tree(pi.web_root) == {}


def test_web_only_installs_the_pwa_and_leaves_the_plugin_alone(pi):
    result = pi.install("--web-only", archive=pi.archive("1.0.0"))

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()
    assert tree(pi.plugins_dir) == {}


def test_both_halves_at_once_is_a_usage_error(pi):
    result = pi.install("--plugin-only", "--web-only", archive=pi.archive())

    assert result.returncode == 2
    assert result.stderr.strip(), "a usage error must say why on stderr"


def test_both_halves_at_once_writes_nothing(pi):
    pi.install("--plugin-only", "--web-only", archive=pi.archive())

    assert pi.state() == ({}, {})


def test_both_halves_at_once_does_not_disturb_an_existing_installation(pi):
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = pi.state()

    result = pi.install("--plugin-only", "--web-only", archive=pi.archive("2.0.0"))

    assert result.returncode == 2
    assert pi.state() == before


def test_an_unknown_flag_is_a_usage_error(pi):
    result = pi.install("--reinstall-everything", archive=pi.archive())

    assert result.returncode == 2
    assert result.stderr.strip()
    assert pi.state() == ({}, {})


def test_a_positional_argument_is_a_usage_error(pi):
    # The usage line in SPEC 5.3 has no positional arguments; a path given
    # where `--archive` was meant must not be silently ignored.
    result = pi.install(str(pi.archive()))

    assert result.returncode == 2
    assert pi.state() == ({}, {})


@pytest.mark.parametrize(
    "flag",
    ["--web-root", "--plugins-dir", "--config", "--tag", "--archive", "--repo"],
)
def test_a_flag_without_its_value_is_a_usage_error(pi, flag):
    result = pi.run(flag)

    assert result.returncode == 2
    assert result.stderr.strip()
    assert pi.state() == ({}, {})


def test_a_dry_run_writes_nothing_and_succeeds(pi):
    result = pi.install("--dry-run", archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert pi.state() == ({}, {})


def test_a_dry_run_says_what_it_would_do(pi):
    """The output is the whole product of a dry run.

    A silent one that exits 0 is indistinguishable from a run that decided
    there was nothing to do.
    """
    result = pi.install("--dry-run", archive=pi.archive())

    assert str(pi.web_root) in result.stdout
    assert str(pi.plugins_dir) in result.stdout


def test_a_dry_run_over_an_existing_installation_changes_nothing(pi):
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = pi.state()

    result = pi.install("--dry-run", archive=pi.archive("2.0.0"))

    assert result.returncode == 0, result.stderr
    assert pi.state() == before


def test_a_dry_run_with_a_bad_checksum_still_fails(pi):
    """A dry run that reports success for an archive it would have refused is
    worse than no dry run: it is a green light for a broken update."""
    archive = pi.archive()
    write_sha256sums(archive, digest="0" * 64)

    result = pi.install("--dry-run", archive=archive)

    assert result.returncode == 1
    assert pi.state() == ({}, {})


# ---------------------------------------------------------------------------
# Exit status (SPEC 5.3: 0 success, 1 failure, 2 usage; non-zero changes
# nothing)
# ---------------------------------------------------------------------------


def test_a_missing_archive_file_is_a_failure_not_a_usage_error(pi, tmp_path):
    # SPEC 5.3 lists "missing source file" under exit 1: the invocation is
    # well formed, the world is not.
    result = pi.install(archive=tmp_path / "nowhere" / "dist.tgz")

    assert result.returncode == 1
    assert result.stderr.strip()
    assert pi.state() == ({}, {})


def test_a_missing_plugin_source_is_a_failure(pi, tmp_path):
    """The plugin is copied out of the checkout the script lives in.

    Run from a directory with no `plugin/companion.py` beside it, the script
    must fail rather than install half of itself.
    """
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    copy = lonely / INSTALL.name
    shutil.copy2(INSTALL, copy)

    result = subprocess.run(
        [
            "sh",
            str(copy),
            "--plugin-only",
            "--web-root",
            str(pi.web_root),
            "--config",
            str(pi.config),
            "--archive",
            str(pi.archive()),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 1
    assert result.stderr.strip()
    assert tree(pi.plugins_dir) == {}


@pytest.mark.parametrize(
    "failure",
    ["bad-checksum", "absent-checksum", "missing-archive", "corrupt-archive", "traversal"],
)
def test_a_failing_run_leaves_the_installation_exactly_as_it_was(pi, tmp_path, failure):
    """SPEC 5.3's closing rule, over every way to fail that needs no network.

    Each of these fails at a different stage - before the download, after it,
    during the unpack - and the stage is precisely what an implementation gets
    wrong: the later the failure, the more likely something was already
    written.
    """
    assert pi.install(archive=pi.archive("1.0.0")).returncode == 0
    before = pi.state()

    if failure == "bad-checksum":
        archive = build_archive(pi.release / "next.tgz", pwa_files("2.0.0"))
        write_sha256sums(archive, digest="0" * 64)
    elif failure == "absent-checksum":
        (pi.release / "SHA256SUMS").unlink()
        archive = build_archive(pi.release / "next.tgz", pwa_files("2.0.0"))
    elif failure == "missing-archive":
        archive = tmp_path / "nowhere" / "dist.tgz"
    elif failure == "corrupt-archive":
        archive = pi.release / "next.tgz"
        archive.write_bytes(b"not a tarball\n")
        write_sha256sums(archive)
    else:
        members = dict(pwa_files("2.0.0"))
        members["../escaped.txt"] = b"escaped\n"
        archive = build_archive(pi.release / "next.tgz", members)
        write_sha256sums(archive)

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == before


# ---------------------------------------------------------------------------
# The script itself (SPEC 5.3: POSIX sh, curl/tar/sha256sum only)
# ---------------------------------------------------------------------------


def test_the_script_starts_with_a_posix_sh_shebang():
    assert INSTALL.read_bytes().startswith(b"#!/bin/sh"), (
        "install-on-pi.sh must start with #!/bin/sh so it cannot silently "
        "become bash-only; /bin/sh on the unit is dash"
    )


def test_the_script_is_committed_executable():
    """`./tools/install-on-pi.sh` is what SETUP.md tells the user to run.

    On the mode git records rather than the one on disk: git tracks only the
    executable bit, so a checkout under a 002 umask leaves 775 in the working
    copy and says nothing about what was committed. Asserting the filesystem
    mode fails for a contributor whose umask differs while still passing for a
    file added as 100644, which is precisely the wrong way round.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-s", "--", str(INSTALL.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert listed, f"{INSTALL} is not tracked by git"
    assert listed[0] == "100755", f"{INSTALL} is committed as {listed[0]}, not 100755"


# ---------------------------------------------------------------------------
# Hardening: cases a security audit of the script raised, each one a way the
# name-based checks above can be walked around. They are tests rather than
# review notes because a protection with no test is a protection the next
# change removes without anyone noticing.
# ---------------------------------------------------------------------------


def build_archive_with_link(path: Path, link_name: str, target: str,
                            payload_name: str, payload: bytes) -> Path:
    """An archive whose member names are all innocent, and which still escapes.

    Every name here passes a check that only looks at paths: there is no `..`
    and nothing absolute. The escape is in the member *type* - a symlink to a
    directory, followed by a regular file underneath it - and tar extracting the
    two in order as root writes through the link.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        link = tarfile.TarInfo(link_name)
        link.type = tarfile.SYMTYPE
        link.linkname = target
        link.mode = 0o777
        archive.addfile(link)

        info = tarfile.TarInfo(payload_name)
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    return path


def test_a_symlink_member_is_refused(pi, tmp_path):
    """The path check is on names, and a symlink member has an innocent name."""
    escape = tmp_path / "escape"
    escape.mkdir()
    archive = build_archive_with_link(
        pi.release / "dist.tgz", "assets", str(escape), "assets/pwned.txt", b"pwned\n"
    )
    write_sha256sums(archive)
    before = pi.state()

    result = pi.install(archive=archive)

    # The assertion is on the refusal, not merely on the absence of damage.
    # GNU tar happens to unlink a symlink before writing through it, so an
    # installer with no check at all still leaves `escape` untouched here and a
    # test that only looked at the outcome would pass against both. What has to
    # hold is that the archive was rejected before extraction, by this check.
    assert result.returncode == 1
    assert "link member" in result.stderr, result.stderr
    assert not (escape / "pwned.txt").exists()
    assert pi.state() == before


def test_a_hardlink_member_is_refused(pi, tmp_path):
    """Same escape, the other link type."""
    victim = tmp_path / "victim.txt"
    victim.write_text("original\n")
    archive_path = pi.release / "dist.tgz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as archive:
        link = tarfile.TarInfo("index.html")
        link.type = tarfile.LNKTYPE
        link.linkname = str(victim)
        archive.addfile(link)
    write_sha256sums(archive_path)
    before = pi.state()

    result = pi.install(archive=archive_path)

    assert result.returncode == 1
    assert "link member" in result.stderr, result.stderr
    assert victim.read_text() == "original\n"
    assert pi.state() == before


@pytest.mark.skipif(
    os.geteuid() != 0,
    reason=(
        "only meaningful as root: tar already applies the umask and ignores "
        "recorded ownership for an ordinary user, so this would pass without "
        "the installer doing anything"
    ),
)
def test_archive_recorded_modes_are_not_restored(pi):
    """tar as root honours whatever the packer recorded, unless told not to.

    A setuid bit in an archive is not a mode the installer should be willing to
    reproduce, and the group and other write bits are how a web root becomes
    editable by anyone with a shell on the unit.

    This is skipped for an ordinary user rather than left to pass, because
    passing would mean nothing: the protection it checks only has an effect for
    the one caller who cannot run the rest of this suite.
    """
    path = pi.release / "dist.tgz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, mode in (("index.html", 0o4777), ("assets/app.css", 0o666)):
            data = b"x\n"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            archive.addfile(info, io.BytesIO(data))
    write_sha256sums(path)

    result = pi.install(archive=path)

    assert result.returncode == 0, result.stderr
    assert mode_of(pi.web_root / "index.html") == 0o644
    assert mode_of(pi.web_root / "assets" / "app.css") == 0o644


def test_a_value_with_a_trailing_comment_is_read_correctly(pi):
    """A comment after the value is legal TOML, and mangling it is not harmless.

    Taking the whole remainder yields a path with quotes and a comment inside
    it, which the installer then creates as a directory, putting the plugin
    somewhere pwnagotchi will never look while reporting success.
    """
    target = pi.root / "opt" / "commented-plugins"
    pi.config.write_text(
        f'[main]\ncustom_plugins = "{target}"  # where my plugins live\n'
    )

    result = pi.install("--plugin-only", archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").is_file()
    assert '"' not in "".join(
        str(path) for path in pi.root.rglob("*") if path.is_dir()
    ), "a quoted path was taken literally and created as a directory"


def test_a_checksum_entry_for_another_path_does_not_satisfy_a_bare_name(pi):
    """`other/dist.tgz` describes a different file, however it ends."""
    archive = build_archive(pi.release / "dist.tgz", pwa_files("1.0.0"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (pi.release / "SHA256SUMS").write_text(f"{digest}  other/dist.tgz\n")
    before = pi.state()

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == before


def test_two_entries_for_the_same_name_are_refused(pi):
    """Ambiguity here decides which digest is enforced, so it is not tolerated."""
    archive = build_archive(pi.release / "dist.tgz", pwa_files("1.0.0"))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (pi.release / "SHA256SUMS").write_text(
        f"{'0' * 64}  dist.tgz\n{digest}  dist.tgz\n"
    )
    before = pi.state()

    result = pi.install(archive=archive)

    assert result.returncode == 1
    assert pi.state() == before


@pytest.mark.parametrize("value", ["", "relative/web", "./web"])
def test_a_web_root_that_is_not_absolute_is_a_usage_error(pi, value):
    """An empty or relative web root is resolved against the current directory.

    The staging directory is `<web-root>.new` and is removed before use, so an
    empty value turns a stray `.new` in the working directory into something the
    script deletes.
    """
    archive = pi.archive()
    before = pi.state()

    result = pi.run(
        "--web-root", value, "--config", str(pi.config), "--archive", str(archive)
    )

    assert result.returncode == 2
    assert result.stderr.strip()
    assert pi.state() == before
    assert not (pi.root / ".new").exists()


@pytest.mark.parametrize(
    "value",
    ["owner/../other", "owner/name/extra", "owner", "/owner/name", "own er/name"],
)
def test_a_malformed_repo_is_a_usage_error(pi, value):
    """`--repo` is interpolated into a URL.

    A `..` segment is normalised away by curl, which would quietly fetch a
    different repository whose own SHA256SUMS matches its own archive and so
    verifies perfectly well.
    """
    result = pi.run("--repo", value, "--web-root", str(pi.web_root),
                    "--config", str(pi.config), "--dry-run")

    assert result.returncode == 2
    assert result.stderr.strip()


@pytest.mark.parametrize(
    "extra",
    [
        ["--tag", "v1.2.3"],
        ["--repo", "someone/else"],
        ["--tag", "v1.2.3", "--repo", "someone/else"],
    ],
    ids=["tag", "repo", "both"],
)
def test_a_local_archive_and_a_release_cannot_both_be_named(pi, extra):
    """Two sources, and no way for the user to tell which one was used.

    Silently preferring either one means a run that reports success having
    installed a build nobody asked for (SPEC 5.3.1, flag interactions).
    """
    archive = pi.archive()
    before = pi.state()

    result = pi.install(*extra, archive=archive)

    assert result.returncode == 2
    assert result.stderr.strip()
    assert pi.state() == before


@pytest.mark.parametrize("value", ["../../other/repo/releases/download/v1", "v1 2", "", "v1/../v2"])
def test_a_malformed_tag_is_a_usage_error(pi, value):
    """`--tag` reaches the download URL exactly as `--repo` does.

    A `..` segment is normalised away by curl, so the fetch lands on another
    repository whose SHA256SUMS covers its own archive and verifies cleanly.
    """
    result = pi.run("--tag", value, "--web-root", str(pi.web_root),
                    "--config", str(pi.config), "--dry-run")

    assert result.returncode == 2
    assert result.stderr.strip()


def test_plugin_only_does_not_require_an_archive(pi):
    """SPEC 5.3.1 says so explicitly: it installs nothing from one.

    Every other --plugin-only case here passes an archive anyway, so without
    this the rule is stated and never exercised.
    """
    result = pi.run("--plugin-only", "--web-root", str(pi.web_root),
                    "--config", str(pi.config))

    assert result.returncode == 0, result.stderr
    assert pi.installed_plugin.is_file()
    assert not pi.web_root.exists(), "--plugin-only touched the web root"


def test_the_installed_modes_are_the_ones_spec_pins(pi):
    """Files 0644 and directories 0755 (SPEC 5.3.1).

    Asserted exactly rather than by testing one bit: the web root is served by
    a process that only needs to read it, and a mode set from whatever the
    packer recorded is how a directory ends up group-writable on a unit nobody
    is auditing.
    """
    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert mode_of(pi.web_root) == 0o755
    assert mode_of(pi.web_root / "assets") == 0o755
    assert mode_of(pi.web_root / "index.html") == 0o644
    assert mode_of(pi.web_root / "assets" / "app.js") == 0o644
    assert mode_of(pi.installed_plugin) == 0o644
