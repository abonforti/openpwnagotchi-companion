"""`tools/install-on-pi.sh`, driven for real into a `tmp_path`.

The contract under test is SPEC section 5.3, plus pinned facts F23, F24 and F25
for the one thing this script must not get wrong: the custom plugins directory.
Upstream's own default for it moved between versions this project targets
(F23), so a hardcoded fallback is right on one version and silently wrong on
the newer one - the unit keeps running, the web root serves the app, and the
only symptom is a PWA that connects to nothing (issue #157). SPEC 5.3.1 fixes
this by having the script read its own fallback off the unit's `defaults.toml`
rather than carry either candidate path as a literal, and by failing loudly
when none of the three resolution steps answers instead of guessing.

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

# F23: upstream's `defaults.toml` value at 2.9.5.6. F25: the shell profile
# shipped with the image, and upstream's `defaults.toml` value from 2.9.5.8
# onward. SPEC 5.3.1 (issue #157) forbids the script from carrying either as a
# literal: the fallback must be read off the unit's own installed
# `defaults.toml`, never remembered. Both constants exist here only to prove
# neither appears anywhere the script could reach for it - in its own text or
# in anything it prints.
F23_DEFAULT_PLUGINS_DIR = "/usr/local/share/pwnagotchi/custom-plugins/"
F25_PROFILE_ALIAS_DIR = "/etc/pwnagotchi/custom-plugins/"

# F24: the user configuration merged over the defaults.
F24_DEFAULT_CONFIG = "/etc/pwnagotchi/config.toml"

DEFAULT_WEB_ROOT = "/var/www/openpwn-companion"

# SPEC 5.3.1: the image's Python tree, matched by glob rather than a hardcoded
# version. This is only the default; `--pwn-prefix` redirects it, which is how
# the tests below exercise the third resolution step without root and without
# writing anywhere near the real path.
OPT_PWN = Path("/opt/.pwn")

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


def test_the_array_before_the_key_does_not_leak_into_it(pi, tmp_path):
    """The real neighbourhood the parser has to survive.

    In upstream's own `defaults.toml`, `custom_plugins` sits in a top-level
    `[main]` table right after `custom_plugin_repos`, a multi-line array of
    URLs (SPEC 5.3.1). SPEC 5.3.1 says the fallback is read with "the same
    parser" the user configuration goes through, so this shape is exercised
    through `--config` directly rather than only through the root-gated
    `defaults.toml` fixture below. A parser that matches a key prefix, or
    that reads the last assignment in the table regardless of name, returns a
    URL instead of the path.
    """
    target = tmp_path / "from-config-with-array-neighbour"
    pi.config.write_text(
        '[main]\n'
        'name = "pwnagotchi"\n'
        'custom_plugin_repos = [\n'
        '    "https://example.test/plugins/repo-one",\n'
        '    "https://example.test/plugins/repo-two",\n'
        ']\n'
        f'custom_plugins = "{target}"\n'
    )

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


def test_the_script_contains_neither_candidate_path_as_a_literal():
    """SPEC 5.3.1, "The fallback is read, not remembered" (issue #157).

    This is the assertion that would have caught the original defect
    directly: `tools/install-on-pi.sh:413` carried
    `/usr/local/share/pwnagotchi/custom-plugins/` as a literal, which was
    right at 2.9.5.6 and silently wrong at 2.9.5.8. Reading the shipped script
    as text catches that shape of regression before any process is spawned.
    """
    text = INSTALL.read_text()

    assert F23_DEFAULT_PLUGINS_DIR not in text
    assert F25_PROFILE_ALIAS_DIR not in text


@pytest.fixture
def not_a_real_unit():
    """Refuses to run a test that assumes `/opt/.pwn` is absent when it is not.

    Only `test_the_default_pwn_prefix_is_opt_dot_pwn` still needs this: every
    other test below names its own `--pwn-prefix` under `tmp_path` (SPEC
    5.3.1's seam) and no longer depends on what this host happens to have
    under `/opt/.pwn`. That one test is checking the *default* value of the
    flag, which by definition cannot be overridden, so it is the one place
    left where the host's real `/opt/.pwn` still matters.
    """
    if OPT_PWN.exists():
        pytest.skip(f"{OPT_PWN} exists on this host; not a safe test host")


def make_pwn_prefix(root: Path, trees: dict[str, str]) -> Path:
    """Builds a `--pwn-prefix` tree: `trees` maps a Python version such as
    `"3.13"` to the `defaults.toml` contents for that tree. More than one
    entry reproduces SPEC 5.3.1's "more than one Python tree" case.
    """
    prefix = root / "pwn-prefix"
    for version, contents in trees.items():
        site_packages = (
            prefix / "lib" / f"python{version}" / "site-packages" / "pwnagotchi"
        )
        site_packages.mkdir(parents=True)
        (site_packages / "defaults.toml").write_text(contents)
    return prefix


def defaults_toml_path(prefix: Path, version: str) -> Path:
    return prefix / "lib" / f"python{version}" / "site-packages" / "pwnagotchi" / "defaults.toml"


# The fallback cases below name an empty `--pwn-prefix` under `tmp_path`
# explicitly (SPEC 5.3.1's seam), so they no longer depend on this host
# having no real `/opt/.pwn` - the default value is checked separately, in
# `test_the_default_pwn_prefix_is_opt_dot_pwn`.
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
def test_a_silent_config_fails_when_no_pwn_prefix_defaults_toml_answers(pi, tmp_path, body):
    """SPEC 5.3.1 item 4: with the config silent and an empty `--pwn-prefix`,
    resolution has nowhere left to go. Falling back to a remembered literal
    instead of failing is exactly the shape of issue #157.

    This is the real-run form: it proves nothing is written on a genuine
    install, which the `--dry-run` tests below cannot show by themselves
    since they never attempt a write in the first place.
    """
    pi.config.write_text(FALLBACK_CONFIGS[body])
    empty_prefix = tmp_path / "no-pwn-prefix"
    before = pi.state()

    result = pi.install("--pwn-prefix", str(empty_prefix), archive=pi.archive())

    assert result.returncode == 1
    assert pi.state() == before
    assert F23_DEFAULT_PLUGINS_DIR not in result.stdout
    assert F23_DEFAULT_PLUGINS_DIR not in result.stderr
    assert "/decoy/" not in result.stdout
    assert "/commented/out" not in result.stdout


def test_a_missing_config_file_fails_when_no_pwn_prefix_defaults_toml_answers(pi, tmp_path):
    missing = tmp_path / "no" / "such" / "config.toml"
    empty_prefix = tmp_path / "no-pwn-prefix"
    before = pi.state()

    result = pi.install(
        "--dry-run", "--config", str(missing), "--pwn-prefix", str(empty_prefix),
        archive=pi.archive(),
    )

    assert result.returncode == 1
    assert pi.state() == before
    assert F23_DEFAULT_PLUGINS_DIR not in result.stdout


def test_the_f25_profile_alias_is_never_used_as_a_fallback(pi, tmp_path):
    """F25 in one assertion.

    `/etc/pwnagotchi/custom-plugins/` is what the shipped shell profile aliases
    `custom` to. It agrees with `defaults.toml` at the version this project
    targets (issue #157), but the script must still never reach it as a
    literal - the resolution reads the unit, it does not recognise this path
    by name.
    """
    pi.config.write_text('main.name = "testunit"\n')
    empty_prefix = tmp_path / "no-pwn-prefix"

    result = pi.install("--dry-run", "--pwn-prefix", str(empty_prefix), archive=pi.archive())

    assert result.returncode == 1
    assert F25_PROFILE_ALIAS_DIR not in result.stdout
    assert F25_PROFILE_ALIAS_DIR not in result.stderr


def test_all_three_sources_silent_names_them_all_and_points_at_plugins_dir(pi, tmp_path):
    """SPEC 5.3.1 item 4: the failure message is the fix, not just a refusal.

    A wrong directory is invisible - every file is written, the script exits
    0, and the plugin never loads. The message has to name every place that
    was consulted plus the flag that unblocks it, or the owner is left to
    guess the same way the script just refused to.

    This is the load-bearing form of the check, and it has to be `--dry-run`:
    Mutant B (deleting only the final `exit 1` and leaving the diagnostic
    `echo`s in place) still exits non-zero on a real run, because the next
    step tries to `mkdir` an empty `plugins_dir` and *that* fails - the
    message assertions below would then pass by accident, for a reason that
    has nothing to do with the guard under test. `--dry-run` stops before any
    write is attempted, so a passing result here can only mean the guard
    itself fired.
    """
    pi.config.write_text('main.name = "testunit"\nmain.lang = "en"\n')
    empty_prefix = tmp_path / "no-pwn-prefix"

    result = pi.install("--dry-run", "--pwn-prefix", str(empty_prefix), archive=pi.archive())

    assert result.returncode == 1
    assert not pi.installed_plugin.exists()
    assert "--plugins-dir" in result.stderr
    assert str(pi.config) in result.stderr
    assert "defaults.toml" in result.stderr


def test_dry_run_also_fails_when_all_three_sources_are_silent(pi, tmp_path):
    """SPEC 5.3.1: "`--dry-run` verifies before it reports" - it performs every
    check a real run performs, so a resolution failure must not be swallowed
    just because nothing would have been written anyway.

    Load-bearing for the same reason as the test above: a real run can exit
    non-zero here even with the guard removed, because the empty
    `plugins_dir` breaks a later `mkdir` instead. Only `--dry-run` isolates
    the guard itself, since it never reaches that later step.
    """
    pi.config.write_text('main.name = "testunit"\n')
    empty_prefix = tmp_path / "no-pwn-prefix"

    result = pi.install("--dry-run", "--pwn-prefix", str(empty_prefix), archive=pi.archive())

    assert result.returncode == 1
    assert not pi.installed_plugin.exists()


def test_the_default_pwn_prefix_is_opt_dot_pwn(pi, not_a_real_unit):
    """SPEC 5.3.1: "`--pwn-prefix DIR` names the tree, defaulting to
    `/opt/.pwn`." Omitting the flag has to mean the same absolute path the
    script always looked at, which is only observable on a host with no real
    `/opt/.pwn` to answer from - see `not_a_real_unit`.
    """
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--dry-run", archive=pi.archive())

    assert result.returncode == 1
    assert "/opt/.pwn" in result.stderr


# ---------------------------------------------------------------------------
# The third resolution step, through `--pwn-prefix` (SPEC 5.3.1, F26).
# ---------------------------------------------------------------------------

# The real neighbourhood: in upstream's own `defaults.toml`, `custom_plugins`
# sits in a top-level `[main]` table right after `custom_plugin_repos`, a
# multi-line array of URLs.
DEFAULTS_TOML_NEIGHBOURHOOD = """[main]
name = "pwnagotchi"
custom_plugin_repos = [
    "https://example.test/plugins/repo-one",
    "https://example.test/plugins/repo-two",
]
custom_plugins = "{path}"
"""


def test_the_pwn_prefix_defaults_toml_answers_when_the_config_is_silent(pi, tmp_path):
    """SPEC 5.3.1 items 3 and 5: the third step, and stdout naming it as the
    source of the answer."""
    target = tmp_path / "from-pwn-prefix-defaults-toml"
    prefix = make_pwn_prefix(
        tmp_path, {"3.99": DEFAULTS_TOML_NEIGHBOURHOOD.format(path=target)}
    )
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--dry-run", "--pwn-prefix", str(prefix), archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout
    assert "defaults.toml" in result.stdout
    assert "repo-one" not in result.stdout
    assert "repo-two" not in result.stdout


def test_the_pwn_prefix_defaults_toml_without_the_key_still_fails(pi, tmp_path):
    """SPEC 5.3.1 item 4: a `defaults.toml` that exists but never sets
    `custom_plugins` answers nothing, same as one that is absent."""
    prefix = make_pwn_prefix(tmp_path, {"3.99": '[main]\nname = "pwnagotchi"\n'})
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--pwn-prefix", str(prefix), archive=pi.archive())

    assert result.returncode == 1
    assert not pi.installed_plugin.exists()


def test_an_empty_pwn_prefix_tree_still_fails(pi, tmp_path):
    """The trap in an unguarded glob under `set -eu`: with nothing matching
    `python*` under the prefix, a resolver that does not guard the glob risks
    treating the literal pattern string as a path instead of finding
    nothing."""
    prefix = tmp_path / "pwn-prefix"
    (prefix / "lib").mkdir(parents=True)
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--pwn-prefix", str(prefix), archive=pi.archive())

    assert result.returncode == 1
    assert not pi.installed_plugin.exists()


def test_plugins_dir_overrides_a_present_pwn_prefix_defaults_toml(pi, tmp_path):
    """SPEC 5.3.1 item 1: `--plugins-dir` overrides everything, including a
    `defaults.toml` under `--pwn-prefix` that would otherwise answer - the
    resolution order is not just "first non-empty value wins", it stops at
    the first step that applies at all.
    """
    from_defaults = tmp_path / "from-defaults-toml"
    prefix = make_pwn_prefix(
        tmp_path, {"3.99": DEFAULTS_TOML_NEIGHBOURHOOD.format(path=from_defaults)}
    )
    explicit = tmp_path / "explicit"
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install(
        "--plugins-dir", str(explicit), "--pwn-prefix", str(prefix), archive=pi.archive()
    )

    assert result.returncode == 0, result.stderr
    assert (explicit / "companion.py").is_file()
    assert not from_defaults.exists()


def test_config_overrides_a_present_pwn_prefix_defaults_toml(pi, tmp_path):
    """SPEC 5.3.1 item 2: the running configuration is read before the third
    step is ever consulted, so a unit whose config disagrees with its own
    `defaults.toml` still gets what the config says."""
    from_defaults = tmp_path / "from-defaults-toml"
    prefix = make_pwn_prefix(
        tmp_path, {"3.99": DEFAULTS_TOML_NEIGHBOURHOOD.format(path=from_defaults)}
    )
    from_config = tmp_path / "from-config"
    pi.config.write_text(config_with(from_config))

    result = pi.install("--pwn-prefix", str(prefix), archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (from_config / "companion.py").is_file()
    assert not from_defaults.exists()


def test_more_than_one_python_tree_is_refused_not_resolved(pi, tmp_path):
    """SPEC 5.3.1: "More than one Python tree under `/opt/.pwn/` is refused,
    not resolved" - a unit in this state is a partial upgrade, and choosing
    between the trees would put back the silent wrong answer the whole
    change exists to remove. The script lists every `defaults.toml` it
    found and stops.
    """
    first = tmp_path / "from-first-tree"
    second = tmp_path / "from-second-tree"
    prefix = make_pwn_prefix(
        tmp_path,
        {
            "3.11": DEFAULTS_TOML_NEIGHBOURHOOD.format(path=first),
            "3.13": DEFAULTS_TOML_NEIGHBOURHOOD.format(path=second),
        },
    )
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--pwn-prefix", str(prefix), archive=pi.archive())

    assert result.returncode == 1
    assert not pi.installed_plugin.exists()
    assert not first.exists()
    assert not second.exists()
    assert str(defaults_toml_path(prefix, "3.11")) in result.stderr
    assert str(defaults_toml_path(prefix, "3.13")) in result.stderr


@pytest.mark.parametrize("value", ["", "relative/prefix", "./prefix"])
def test_a_pwn_prefix_that_is_not_absolute_is_a_usage_error(pi, value):
    """SPEC 5.3.1: "It is validated the way `--web-root` is, as an absolute
    path"."""
    before = pi.state()

    result = pi.install("--pwn-prefix", value, archive=pi.archive())

    assert result.returncode == 2
    assert result.stderr.strip()
    assert pi.state() == before


def test_a_pwn_prefix_with_unusual_characters_is_not_specially_rejected(pi, tmp_path):
    """SPEC 5.3.1: no `--tag`-style character check, because this value is
    interpolated into a glob rather than a URL - "a strange value here fails
    to match and the script refuses, which is the behaviour it would have
    had anyway." Exit `1` (no match), not `2` (rejected outright), is the
    proof that no extra validation was added. No whitespace here - that is
    its own, deliberate rejection, covered separately below.
    """
    strange = tmp_path / "weird..prefix"
    strange.mkdir(parents=True)
    pi.config.write_text('main.name = "testunit"\n')

    result = pi.install("--pwn-prefix", str(strange), archive=pi.archive())

    assert result.returncode == 1
    assert not pi.installed_plugin.exists()


@pytest.mark.parametrize("whitespace", [" ", "\t", "\n"], ids=["space", "tab", "newline"])
def test_a_pwn_prefix_containing_whitespace_is_rejected_outright(pi, tmp_path, whitespace):
    """SPEC 5.3.1: "Whitespace is the exception and is rejected outright."

    The value feeds an unquoted glob and a space-separated accumulator that
    counts matches, so a prefix containing whitespace word-splits and the
    count comes out wrong - it fails closed either way, which is exactly why
    a usage error is preferred: the caller gets a message it can read instead
    of a script that refuses for a reason it never explains.

    Newline is in the list alongside space and tab, not decorative: the
    guard was widened from exactly those two characters to a `[[:space:]]`
    class specifically because of the newline case, so a guard narrowed
    back to the original two-case form would still pass every other test
    here while leaving a `\n`-containing prefix unrejected.
    """
    strange = tmp_path / f"weird{whitespace}prefix"
    strange.mkdir(parents=True)
    pi.config.write_text('main.name = "testunit"\n')
    before = pi.state()

    result = pi.install("--pwn-prefix", str(strange), archive=pi.archive())

    assert result.returncode == 2
    assert "whitespace" in result.stderr.lower()
    assert pi.state() == before


# ---------------------------------------------------------------------------
# Every route into a write destination gets the same checks (issue #166).
#
# SPEC 5.3.1: the plugins directory can arrive four ways - `--plugins-dir`,
# `main.custom_plugins` read from the unit's `config.toml`, the same key read
# from its `defaults.toml`, and `--pwn-prefix` on the way to that file - and
# `--web-root` gets the identical rule. A value on any of the five is refused
# with exit 2 when it is not absolute, contains whitespace, or contains a
# `..` segment, and the message names the route: the flag, or the file the
# value came from. `--web-root`'s "not absolute" leg and `--pwn-prefix`'s
# "not absolute" and whitespace legs already have coverage above; what
# follows fills in the rest without disturbing those.
# ---------------------------------------------------------------------------


def dotdot_value(tmp_path: Path, name: str = "escape") -> str:
    """An absolute path containing a literal `..` path segment.

    Built by string concatenation rather than `Path` joining, since `Path`
    does not collapse `..` on construction but nothing here should depend on
    that - the point is that the two-character segment reaches the script
    exactly as written, the way a hand-edited config or a typo would produce
    it.
    """
    return f"{tmp_path}/sub/../{name}"


def whitespace_value(tmp_path: Path) -> str:
    return str(tmp_path / "weird plugins" / "dir")


ROUTE_DEFECTS = {
    "relative": lambda tmp_path: "relative/custom-plugins",
    "whitespace": whitespace_value,
    "dotdot": dotdot_value,
}

# The exact wording `require_clean_dir_value` uses for each defect - "the
# usage banner names every flag on any exit-2 path" (SPEC 5.3.1's own
# `usage()` prints `--web-root`, `--plugins-dir` and `--pwn-prefix`
# unconditionally), so asserting the flag name alone would pass just as well
# for a usage error raised for an unrelated reason. The reason word is what
# only the real check prints, the same way the pre-existing whitespace test
# for `--pwn-prefix` asserts "whitespace" rather than the flag.
REASON_WORDS = {
    "relative": "absolute",
    "whitespace": "whitespace",
    "dotdot": ".. segment",
}


def resolved_against_cwd(pi: "Pi", value: str) -> Path:
    """Where `value` lands if a regressed guard let it through.

    `pi.install`/`pi.run` spawn the script with `cwd=pi.root` (see `Pi.run`),
    so a relative defect value - the only non-absolute one `ROUTE_DEFECTS`
    produces - resolves against `pi.root`, not against this test process's
    own working directory.
    """
    candidate = Path(value)
    return candidate if candidate.is_absolute() else pi.root / candidate


def assert_refused_value_untouched(pi: "Pi", value: str) -> None:
    """The state-snapshot equality above only covers the web root's parent
    and the resolved plugins directory; the refused value here points
    somewhere else entirely, so a regressed guard that let the script write
    straight to it would pass that snapshot check by accident. This closes
    that gap directly, at the path the value actually names.
    """
    target = resolved_against_cwd(pi, value)
    assert not target.exists(), f"a refused value must not be written to: {target}"
    assert not (target / "companion.py").exists()


@pytest.mark.parametrize("defect", sorted(ROUTE_DEFECTS), ids=sorted(ROUTE_DEFECTS))
def test_a_plugins_dir_defect_is_a_usage_error(pi, tmp_path, defect):
    """`--plugins-dir` had none of the three checks before issue #166."""
    value = ROUTE_DEFECTS[defect](tmp_path)
    before = pi.state()

    result = pi.install("--plugins-dir", value, archive=pi.archive())

    assert result.returncode == 2
    assert "--plugins-dir" in result.stderr
    assert REASON_WORDS[defect] in result.stderr
    assert pi.state() == before
    assert_refused_value_untouched(pi, value)


@pytest.mark.parametrize("defect", sorted(ROUTE_DEFECTS), ids=sorted(ROUTE_DEFECTS))
def test_a_web_root_defect_is_a_usage_error(pi, tmp_path, defect):
    """The whitespace and `..` legs of the rule for `--web-root`; the "not
    absolute" leg already has coverage in
    `test_a_web_root_that_is_not_absolute_is_a_usage_error`."""
    value = ROUTE_DEFECTS[defect](tmp_path)
    archive = pi.archive()
    before = pi.state()

    result = pi.run(
        "--web-root", value, "--config", str(pi.config), "--archive", str(archive)
    )

    assert result.returncode == 2
    assert "--web-root" in result.stderr
    assert REASON_WORDS[defect] in result.stderr
    assert pi.state() == before
    assert_refused_value_untouched(pi, value)


@pytest.mark.parametrize("defect", sorted(ROUTE_DEFECTS), ids=sorted(ROUTE_DEFECTS))
def test_a_pwn_prefix_defect_names_the_flag_in_the_message(pi, tmp_path, defect):
    """The `..` leg is new for `--pwn-prefix` under issue #166; "not
    absolute" and whitespace already refuse (see the tests above), but this
    also pins that the message names the flag and the reason, which those
    did not check."""
    value = ROUTE_DEFECTS[defect](tmp_path)
    before = pi.state()

    result = pi.install("--pwn-prefix", value, archive=pi.archive())

    assert result.returncode == 2
    assert "--pwn-prefix" in result.stderr
    assert REASON_WORDS[defect] in result.stderr
    assert pi.state() == before
    assert_refused_value_untouched(pi, value)


@pytest.mark.parametrize("defect", sorted(ROUTE_DEFECTS), ids=sorted(ROUTE_DEFECTS))
def test_a_config_custom_plugins_defect_is_a_usage_error(pi, tmp_path, defect):
    """The plugins directory read from `config.toml`'s `main.custom_plugins`
    gets the same three checks. The owner did not type this value, the
    unit's own configuration file did, so the message names that file."""
    value = ROUTE_DEFECTS[defect](tmp_path)
    pi.config.write_text(config_with(value))
    before = pi.state()

    result = pi.install(archive=pi.archive())

    assert result.returncode == 2
    assert str(pi.config) in result.stderr
    assert REASON_WORDS[defect] in result.stderr
    assert pi.state() == before
    assert_refused_value_untouched(pi, value)


@pytest.mark.parametrize("defect", sorted(ROUTE_DEFECTS), ids=sorted(ROUTE_DEFECTS))
def test_a_defaults_toml_custom_plugins_defect_is_a_usage_error(pi, tmp_path, defect):
    """Same rule for the fallback source: `defaults.toml` under
    `--pwn-prefix`, reached when the config is silent. The message names the
    `defaults.toml` path, not the flag - the owner passed `--pwn-prefix`, but
    the offending value came from the file underneath it."""
    value = ROUTE_DEFECTS[defect](tmp_path)
    prefix = make_pwn_prefix(
        tmp_path, {"3.99": DEFAULTS_TOML_NEIGHBOURHOOD.format(path=value)}
    )
    pi.config.write_text('main.name = "testunit"\n')
    before = pi.state()

    result = pi.install("--pwn-prefix", str(prefix), archive=pi.archive())

    assert result.returncode == 2
    assert str(defaults_toml_path(prefix, "3.99")) in result.stderr
    assert REASON_WORDS[defect] in result.stderr
    assert pi.state() == before
    assert_refused_value_untouched(pi, value)


# -- positive controls: the rule matches segments, not substrings, and a
#    clean value on each route still works -----------------------------


def test_a_dotdot_looking_plugins_dir_component_is_not_a_dotdot_segment(pi, tmp_path):
    """`custom..plugins` is a directory *name*, not a `..` segment - the
    check matches whole path components, not the two-character substring, or
    a real plugin directory with two dots anywhere in its name would be
    refused by accident."""
    target = tmp_path / "custom..plugins"

    result = pi.install("--plugins-dir", str(target), archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").is_file()


def test_a_dotdot_looking_custom_plugins_value_is_not_a_dotdot_segment(pi, tmp_path):
    """Same positive control, through `config.toml`'s `main.custom_plugins`
    rather than `--plugins-dir`."""
    target = tmp_path / "custom..plugins"
    pi.config.write_text(config_with(target))

    result = pi.install(archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert (target / "companion.py").is_file()


def test_a_clean_plugins_dir_is_accepted_and_reported_by_the_dry_run(pi, tmp_path):
    target = tmp_path / "clean-plugins-dir"

    result = pi.install("--dry-run", "--plugins-dir", str(target), archive=pi.archive())

    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout


def test_a_clean_web_root_is_accepted_and_reported_by_the_dry_run(pi, tmp_path):
    target = tmp_path / "clean-web-root"
    archive = pi.archive()

    result = pi.run(
        "--dry-run", "--web-root", str(target), "--config", str(pi.config),
        "--archive", str(archive),
    )

    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout


def test_the_default_config_path_is_the_f24_user_config(pi, not_a_real_unit):
    """With no `--config`, SPEC 5.3 pins `/etc/pwnagotchi/config.toml` (F24).

    Neither that file nor a default `--pwn-prefix` answers on this host, so
    resolution fails (SPEC 5.3.1 item 4) - and the failure message is exactly
    where the default path is checked, since item 4 requires it to name the
    config file it looked at. Depends on the host having no real `/opt/.pwn`,
    same as `test_the_default_pwn_prefix_is_opt_dot_pwn`.
    """
    result = pi.run(
        "--web-root", str(pi.web_root), "--dry-run", "--archive", str(pi.archive())
    )

    assert result.returncode == 1
    assert F24_DEFAULT_CONFIG in result.stderr


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


# ---------------------------------------------------------------------------
# Build provenance attestation (SPEC 5.3, issue #182): "the installer asks a
# second question when it can: with gh on the unit, gh attestation verify
# dist.tgz --repo <owner/name> must succeed ... Three cases are told apart
# ... absent ... invalid ... unverifiable ... --require-attestation turns
# both warnings into exit 1."
#
# Every case below except the --archive one (SPEC's own "absent" example) has
# to exercise --tag/--repo, the download path, because SPEC 5.3 groups
# "a --archive built by hand" under absent unconditionally - a hand-built
# archive was never attested in the first place, so nothing here can drive
# the gh call through --archive at all. That in turn means faking a download:
# a `curl` stub good enough to serve a locally built dist.tgz/SHA256SUMS pair
# without touching the network, in place of the real GitHub Releases fetch.
# SPEC 5.3 pins curl as the download tool but not its exact invocation (-o
# vs redirection, retry flags, ...), so the stub is deliberately liberal - it
# matches on the release asset's own fixed name (dist.tgz or SHA256SUMS)
# appearing anywhere in its argv, which is a fact about what a real release
# asset is called rather than a guess about how the script calls curl, and
# falls back to stdout when no -o/--output is present. This is the largest
# assumption in this section; see the report for how it was validated and
# what would have to change if it is wrong.
# ---------------------------------------------------------------------------

GH_ATTESTATION_REPO = "example/companion-fixture"


def write_stub(path: Path, script: str) -> None:
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def write_recording_exit_stub(
    path: Path, marker: Path, *, exit_code: int, stderr: str = ""
) -> None:
    """A stub that succeeds unconditionally for `gh auth status` - every
    case below except the ones that specifically test that check assumes
    the unit is already logged in, per SPEC 5.3's own ordering - and, for
    anything else `gh` is called with (in practice `gh attestation
    verify`), appends its own argv (space-joined) as one line to `marker`,
    optionally writes `stderr`, and exits `exit_code`. Stands in for `gh`
    in every verify-outcome case below: what matters is what the installer
    called it with and how it reacted to the exit code and the message,
    not a faithful reimplementation of `gh` itself.
    """
    write_stub(
        path,
        "#!/bin/sh\n"
        'if [ "$1" = "auth" ]; then exit 0; fi\n'
        f'printf \'%s\\n\' "$*" >> "{marker}"\n'
        + (f'printf \'%s\' "{stderr}" >&2\n' if stderr else "")
        + f"exit {exit_code}\n",
    )


def write_gh_not_logged_in_stub(path: Path, *, auth_marker: Path, verify_marker: Path) -> None:
    """`gh auth status` fails the way a unit with no stored token does,
    printing the `gh auth login` sentence SPEC 5.3 names. `gh attestation
    verify` is stubbed to succeed - a permissive default - so the only way
    this stub can look like it was called is through `verify_marker`
    existing at all: SPEC 5.3 says a `gh` that is not logged in must never
    reach the verify call.
    """
    write_stub(
        path,
        "#!/bin/sh\n"
        'if [ "$1" = "auth" ]; then\n'
        f'  printf \'%s\\n\' "$*" >> "{auth_marker}"\n'
        '  echo "You are not logged into any GitHub hosts." >&2\n'
        '  echo "To authenticate, run: gh auth login" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'printf \'%s\\n\' "$*" >> "{verify_marker}"\n'
        "exit 0\n",
    )


def write_curl_download_stub(path: Path, *, archive: Path, sums: Path) -> None:
    write_stub(
        path,
        "#!/bin/sh\n"
        'out=""\n'
        'prev=""\n'
        'src=""\n'
        'for arg in "$@"; do\n'
        '  case "$prev" in\n'
        '    -o|--output) out="$arg" ;;\n'
        "  esac\n"
        '  case "$arg" in\n'
        f'    *SHA256SUMS*) src="{sums}" ;;\n'
        f'    *dist.tgz*) src="{archive}" ;;\n'
        "  esac\n"
        '  prev="$arg"\n'
        "done\n"
        'if [ -z "$src" ]; then echo "stub curl: could not tell which release '
        'asset was requested: $*" >&2; exit 22; fi\n'
        'if [ -n "$out" ]; then cp "$src" "$out"; else cat "$src"; fi\n'
        "exit 0\n",
    )


def no_real_gh_path(tmp_path: Path, *front_dirs: Path) -> str:
    """A PATH with every real executable this host's own PATH already
    offers - `sh`, `tar`, `sha256sum`, `curl`, and everything else the
    script might reach for - except a real `gh`, which this host has
    installed and which must not be reachable for a "no gh on PATH" case to
    test anything real. Built by mirroring every directory on the ambient
    PATH with symlinks, one name at a time, skipping `gh` specifically:
    excluding a whole directory would also remove `tar` and `sha256sum` on a
    host where all three live in `/usr/bin`, which this one does.
    """
    mirror = tmp_path / "no-real-gh"
    if not mirror.is_dir():
        mirror.mkdir()
        seen: set[str] = set()
        for directory in os.environ.get("PATH", "").split(":"):
            if not directory or not os.path.isdir(directory):
                continue
            try:
                entries = os.listdir(directory)
            except OSError:
                continue
            for name in entries:
                if name == "gh" or name in seen:
                    continue
                target = os.path.join(directory, name)
                if os.path.isdir(target) or not os.access(target, os.X_OK):
                    continue
                try:
                    (mirror / name).symlink_to(target)
                except OSError:
                    continue
                seen.add(name)
    return ":".join([str(d) for d in front_dirs] + [str(mirror)])


@pytest.fixture
def release_assets(tmp_path):
    """A built dist.tgz plus its matching SHA256SUMS, standing in for what a
    real GitHub Release actually serves - what the stub curl below hands
    back for both, so the checksum step downstream of the attestation check
    still has something genuine to verify.
    """
    server = tmp_path / "server"
    server.mkdir()
    archive = build_archive(server / "dist.tgz", pwa_files("1.0.0"))
    sums = write_sha256sums(archive)
    return archive, sums


def run_release_install(pi: "Pi", path_value: str, *extra: str) -> subprocess.CompletedProcess:
    assert INSTALL.is_file(), f"{INSTALL} does not exist; SPEC section 5.3 requires it"
    env = dict(os.environ)
    env["PATH"] = path_value
    args = [
        "--web-root", str(pi.web_root),
        "--config", str(pi.config),
        "--tag", "v1.0.0",
        "--repo", GH_ATTESTATION_REPO,
        *extra,
    ]
    return subprocess.run(
        ["sh", str(INSTALL), *args],
        cwd=str(pi.root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def test_gh_attestation_verify_is_called_and_a_verified_release_installs(
    pi, tmp_path, release_assets
):
    """Case (a): the stub exits 0."""
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(stub_dir / "gh", marker, exit_code=0)

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}")

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()
    combined = (result.stdout + result.stderr).lower()
    assert "attestation ok" in combined, (
        f"expected the installer to report the attestation as verified, got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert marker.is_file(), "gh was never invoked"
    argv = marker.read_text(encoding="utf-8")
    assert "attestation verify" in argv, f"expected 'attestation verify' in gh's argv, got {argv!r}"
    assert "dist.tgz" in argv, f"expected the archive path in gh's argv, got {argv!r}"
    assert "--repo" in argv, f"expected --repo in gh's argv, got {argv!r}"
    assert "--signer-workflow" in argv, (
        f"expected --signer-workflow in gh's argv (SPEC 5.3: 'so that the "
        f"answer is release.yml built it and not someone in this repository "
        f"attested something'), got {argv!r}"
    )
    assert f"{GH_ATTESTATION_REPO}/.github/workflows/release.yml" in argv, (
        f"expected --signer-workflow to name this repository's own "
        f"release.yml, got {argv!r}"
    )


def test_gh_attestation_invalid_refuses_the_install(pi, tmp_path, release_assets):
    """Case (b): the stub exits 1 with a message the script itself never
    prints. Not "verification failed": the script's own boilerplate line
    ("provenance verification failed for $tag") contains that exact phrase
    too, so asserting it proves nothing about whether the stub's own text
    reached stderr - a script that discarded the stub's message and only
    ever printed its own boilerplate would still satisfy that assertion.
    """
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(
        stub_dir / "gh", marker, exit_code=1,
        stderr="signature does not match the expected public key",
    )

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}")

    assert result.returncode == 1
    assert pi.state() == ({}, {})
    assert "signature does not match the expected public key" in result.stderr, (
        f"expected the stub's own distinct message on stderr, got {result.stderr!r}"
    )


def test_gh_attestation_certificate_message_is_invalid(pi, tmp_path, release_assets):
    """SPEC 5.3: 'anything else the verifier says is a verdict and is
    treated as invalid' - a message containing "certificate" used to sit in
    the unverifiable group; SPEC 5.3 now narrows that group to 'an HTTP 401,
    403 or 5xx or a connection that never completed' specifically, so a
    certificate complaint (an answer, not a connection failure) is invalid:
    exit 1, not a warning that continues.
    """
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(
        stub_dir / "gh", marker, exit_code=1,
        stderr="verification error: certificate has expired",
    )

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}")

    assert result.returncode == 1
    assert pi.state() == ({}, {})


def test_gh_not_logged_in_is_unverifiable_and_never_calls_verify(pi, tmp_path, release_assets):
    """SPEC 5.3: 'a gh that is not logged in (the verifier needs a token
    even for a public repository)' is one of the unverifiable cases, checked
    with `gh auth status` before the verify call ever runs.
    """
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    auth_marker = tmp_path / "gh-auth-argv"
    verify_marker = tmp_path / "gh-verify-argv"
    write_gh_not_logged_in_stub(
        stub_dir / "gh", auth_marker=auth_marker, verify_marker=verify_marker
    )

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}")

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()
    assert auth_marker.is_file(), "gh auth status was never invoked"
    assert not verify_marker.exists(), (
        "gh attestation verify must not run once gh auth status has already "
        "answered that the unit is not logged in"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "not logged in" in combined, (
        f"expected the warning to say gh is not logged in, got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_require_attestation_with_gh_not_logged_in_refuses(pi, tmp_path, release_assets):
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    write_gh_not_logged_in_stub(
        stub_dir / "gh",
        auth_marker=tmp_path / "gh-auth-argv",
        verify_marker=tmp_path / "gh-verify-argv",
    )

    result = run_release_install(
        pi, f"{stub_dir}:{os.environ.get('PATH', '')}", "--require-attestation"
    )

    assert result.returncode == 1
    assert pi.state() == ({}, {})


def test_require_attestation_with_plugin_only_is_a_usage_error(pi):
    """SPEC 5.3: '--require-attestation ... is a usage error beside
    --plugin-only since there is then nothing to attest' - --plugin-only
    installs nothing from an archive, so there is no dist.tgz for the flag
    to have an opinion about.
    """
    before = pi.state()

    result = pi.run(
        "--require-attestation", "--plugin-only",
        "--web-root", str(pi.web_root), "--config", str(pi.config),
    )

    assert result.returncode == 2
    assert result.stderr.strip()
    assert pi.state() == before


def test_dry_run_on_the_download_path_reports_a_verified_attestation(pi, tmp_path, release_assets):
    """SPEC 5.3: 'the dry run reports which of the three cases it found' -
    the verified case, driven through --tag/--repo rather than --archive so
    this exercises the same download path as the other dry-run case below,
    not the local-archive shortcut that never involves gh at all.
    """
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    write_recording_exit_stub(stub_dir / "gh", tmp_path / "gh-argv", exit_code=0)

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert pi.state() == ({}, {})
    combined = (result.stdout + result.stderr).lower()
    assert "attestation ok" in combined, (
        f"expected the dry run to report the verified attestation it found, "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_dry_run_on_the_download_path_reports_an_absent_attestation(pi, tmp_path, release_assets):
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    write_recording_exit_stub(
        stub_dir / "gh", tmp_path / "gh-argv", exit_code=1,
        stderr="HTTP 404: Not Found",
    )

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}", "--dry-run")

    assert result.returncode == 0, result.stderr
    assert pi.state() == ({}, {})
    combined = (result.stdout + result.stderr).lower()
    assert "no attestation" in combined, (
        f"expected the dry run to report the absent attestation it found, "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_gh_attestation_unverifiable_warns_and_continues(pi, tmp_path, release_assets):
    """Case (c): the stub exits 2 with a network-style message."""
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(
        stub_dir / "gh", marker, exit_code=2,
        stderr="dial tcp: connection refused",
    )

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}")

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()


def test_gh_attestation_absent_for_a_downloaded_release_warns_and_continues(
    pi, tmp_path, release_assets
):
    """The downloaded-release counterpart to the hand-built-archive "absent"
    case: a real `gh` reports no attestation for a release published before
    attestation existed with `exit 1` and an "HTTP 404" line on stderr - the
    same distinction the script's own `verify_provenance` docstring draws
    ("404 is no attestation for this subject (absent)"). Wording must differ
    from both the unverifiable case (gh ran but could not ask) and the
    hand-built-archive case (gh was never asked at all), since a release
    with nothing to attest and a question that could not be asked are not
    the same fact.
    """
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(
        stub_dir / "gh", marker, exit_code=1,
        stderr='HTTP 404: Not Found (https://api.github.com/repos/example/attestations)',
    )

    result = run_release_install(pi, f"{stub_dir}:{os.environ.get('PATH', '')}")

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()
    combined = (result.stdout + result.stderr).lower()
    assert "no attestation" in combined and "v1.0.0" in combined, (
        f"expected a warning naming the tag with no attestation found, got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    unverifiable_stub_dir = tmp_path / "unverifiable-stub"
    unverifiable_stub_dir.mkdir()
    write_curl_download_stub(unverifiable_stub_dir / "curl", archive=archive, sums=sums)
    write_recording_exit_stub(
        unverifiable_stub_dir / "gh", tmp_path / "gh-argv-unverifiable", exit_code=2,
        stderr="dial tcp: connection refused",
    )
    unverifiable = run_release_install(
        pi, f"{unverifiable_stub_dir}:{os.environ.get('PATH', '')}"
    )
    assert unverifiable.returncode == 0, unverifiable.stderr

    archive_only = pi.archive()
    local_result = subprocess.run(
        [
            "sh", str(INSTALL),
            "--web-root", str(pi.web_root), "--config", str(pi.config),
            "--archive", str(archive_only),
        ],
        cwd=str(pi.root), stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120,
    )
    assert local_result.returncode == 0, local_result.stderr

    absent_release_text = result.stdout + result.stderr
    unverifiable_text = unverifiable.stdout + unverifiable.stderr
    hand_built_text = local_result.stdout + local_result.stderr
    assert absent_release_text != unverifiable_text, (
        "SPEC 5.3: a release with no attestation and a question that could "
        "not be asked must not be reported as the same thing"
    )
    assert absent_release_text != hand_built_text, (
        "SPEC 5.3: a release with no attestation and a hand-built archive "
        "that was never asked about must not be reported as the same thing"
    )


def test_require_attestation_with_no_attestation_for_the_release_refuses(
    pi, tmp_path, release_assets
):
    """The downloaded-release "absent" case under --require-attestation:
    SPEC 5.3 says the flag "turns both warnings into exit 1", and this is
    one of the two warnings.
    """
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(
        stub_dir / "gh", marker, exit_code=1,
        stderr='HTTP 404: Not Found (https://api.github.com/repos/example/attestations)',
    )

    result = run_release_install(
        pi, f"{stub_dir}:{os.environ.get('PATH', '')}", "--require-attestation"
    )

    assert result.returncode == 1
    assert pi.state() == ({}, {})
    assert "no attestation" in result.stderr.lower(), (
        f"expected the refusal to name the missing attestation, got "
        f"stderr={result.stderr!r}"
    )


def test_no_gh_on_path_warns_and_continues(pi, tmp_path, release_assets):
    """Case (d): no gh anywhere on PATH."""
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)

    result = run_release_install(pi, no_real_gh_path(tmp_path, stub_dir))

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()


def test_the_unverifiable_and_no_gh_cases_print_different_sentences(pi, tmp_path, release_assets):
    """Cases (c) and (d) both continue with a warning, but SPEC 5.3 requires
    the wording to differ - "distinct in its wording from the first": a gh
    that ran but could not answer and no gh at all are not the same fact.
    """
    archive, sums = release_assets

    unverifiable_stub_dir = tmp_path / "unverifiable-stub"
    unverifiable_stub_dir.mkdir()
    write_curl_download_stub(unverifiable_stub_dir / "curl", archive=archive, sums=sums)
    write_recording_exit_stub(
        unverifiable_stub_dir / "gh", tmp_path / "gh-argv-unverifiable", exit_code=2,
        stderr="dial tcp: connection refused",
    )
    unverifiable = run_release_install(
        pi, f"{unverifiable_stub_dir}:{os.environ.get('PATH', '')}"
    )
    assert unverifiable.returncode == 0, unverifiable.stderr

    no_gh_stub_dir = tmp_path / "no-gh-stub"
    no_gh_stub_dir.mkdir()
    write_curl_download_stub(no_gh_stub_dir / "curl", archive=archive, sums=sums)
    no_gh = run_release_install(pi, no_real_gh_path(tmp_path, no_gh_stub_dir))
    assert no_gh.returncode == 0, no_gh.stderr

    unverifiable_text = unverifiable.stdout + unverifiable.stderr
    no_gh_text = no_gh.stdout + no_gh.stderr
    assert unverifiable_text != no_gh_text, (
        "SPEC 5.3: 'distinct in its wording from the first' - a gh that ran "
        "but could not answer and no gh at all must not be reported as the "
        "same thing"
    )


def test_require_attestation_with_no_gh_refuses(pi, tmp_path, release_assets):
    """Case (e), first leg: --require-attestation with no gh at all."""
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)

    result = run_release_install(
        pi, no_real_gh_path(tmp_path, stub_dir), "--require-attestation"
    )

    assert result.returncode == 1
    assert pi.state() == ({}, {})


def test_require_attestation_with_unverifiable_gh_refuses(pi, tmp_path, release_assets):
    """Case (e), second leg: --require-attestation with an unverifiable gh."""
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    write_recording_exit_stub(
        stub_dir / "gh", tmp_path / "gh-argv", exit_code=2,
        stderr="dial tcp: connection refused",
    )

    result = run_release_install(
        pi, f"{stub_dir}:{os.environ.get('PATH', '')}", "--require-attestation"
    )

    assert result.returncode == 1
    assert pi.state() == ({}, {})


def test_require_attestation_with_verified_gh_proceeds(pi, tmp_path, release_assets):
    """Case (e), third leg: --require-attestation with a verified gh."""
    archive, sums = release_assets
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    write_curl_download_stub(stub_dir / "curl", archive=archive, sums=sums)
    write_recording_exit_stub(stub_dir / "gh", tmp_path / "gh-argv", exit_code=0)

    result = run_release_install(
        pi, f"{stub_dir}:{os.environ.get('PATH', '')}", "--require-attestation"
    )

    assert result.returncode == 0, result.stderr
    assert (pi.web_root / "index.html").is_file()


def test_archive_install_never_invokes_gh_even_when_present(pi, tmp_path):
    """Case (f): --archive is treated as absent unconditionally, and never
    calls gh even when a gh happens to be on PATH.
    """
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    marker = tmp_path / "gh-argv"
    write_recording_exit_stub(stub_dir / "gh", marker, exit_code=0)
    archive = pi.archive()

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{os.environ.get('PATH', '')}"
    result = subprocess.run(
        [
            "sh", str(INSTALL),
            "--web-root", str(pi.web_root), "--config", str(pi.config),
            "--archive", str(archive),
        ],
        cwd=str(pi.root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), (
        "gh must never be invoked for a hand-built --archive (SPEC 5.3: 'a "
        "--archive built by hand ... is a warning that names the release "
        "and continues')"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "provenance" in combined, (
        f"expected a warning naming the provenance check as skipped, got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_dry_run_reports_the_absent_attestation_case_and_writes_nothing(pi):
    """Case (g): --dry-run reports which of the three cases it found. Driven
    through --archive, the case with no gh involvement at all, so this test
    does not also depend on the curl-stub assumption the download-path cases
    above carry.
    """
    archive = pi.archive()

    result = pi.install("--dry-run", archive=archive)

    assert result.returncode == 0, result.stderr
    assert pi.state() == ({}, {})
    combined = (result.stdout + result.stderr).lower()
    assert "provenance" in combined, (
        f"expected the dry run to report the attestation case it found, got "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
