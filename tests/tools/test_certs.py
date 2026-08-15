"""The certificate generation scripts, driven for real.

SPEC 10.6 puts these in pytest rather than bats: the scripts are ordinary
non-interactive commands, so `subprocess` drives them and `openssl` inspects
what came out, which removes a test dependency and keeps one coverage report.

The contract under test is SPEC section 3 - the shared conventions paragraph
plus 3.1 and 3.2. Three of the assertions here encode failures this setup has
already hit once, and each one fails in a way that points somewhere else:

- a CA without `basicConstraints = critical` is accepted by `curl` and rejected
  by Python and iOS, so the tooling says the PKI is fine while the phone says
  the app is broken;
- a server certificate whose address sits in a `DNS:` entry instead of an
  `IP:` one fails on iOS looking exactly like a network fault;
- a validity over 825 days is rejected by iOS with no useful diagnostic.

The scripts are invoked through `sh` rather than executed directly. SPEC 3
requires POSIX `sh`, so running them under `sh` is the check: a bashism fails
here instead of on a Raspberry Pi OS image where `/bin/sh` is dash.
"""

from __future__ import annotations

import re
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GEN_CA = REPO_ROOT / "tools" / "gen-ca.sh"
GEN_CERT = REPO_ROOT / "tools" / "gen-cert.sh"

CA_SUBJECT_CN = "OpenPwnagotchi Companion CA"
CA_DAYS = 3650
SERVER_DAYS = 820
IOS_MAX_DAYS = 825

# Documentation addresses and loopback only: a certificate fixture must never
# name a real host on the owner's network.
IP_A = "127.0.0.1"
IP_B = "192.0.2.10"
IP_C = "198.51.100.7"

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl is not on PATH; the certificate scripts cannot be exercised",
)


# ---------------------------------------------------------------------------
# Running the scripts
# ---------------------------------------------------------------------------


def run_script(script: Path, *args: str, cwd: Path | None = None):
    """Runs one of the scripts non-interactively and returns the CompletedProcess.

    `stdin` is `/dev/null` and there is a timeout: SPEC 3 requires the scripts to
    never prompt, and a prompt would otherwise hang the suite rather than fail
    it. Everything either script needs comes from its arguments.
    """
    assert script.is_file(), (
        f"{script} does not exist; SPEC section 3 requires both generation scripts"
    )
    return subprocess.run(
        ["sh", str(script), *args],
        cwd=None if cwd is None else str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


def gen_ca(out: Path, *args: str, cwd: Path | None = None):
    return run_script(GEN_CA, "--out", str(out), *args, cwd=cwd)


def gen_cert(out: Path, *args: str, cwd: Path | None = None):
    return run_script(GEN_CERT, "--out", str(out), *args, cwd=cwd)


def openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["openssl", *args], capture_output=True, text=True, timeout=60
    )


def cert_text(path: Path) -> str:
    result = openssl("x509", "-in", str(path), "-noout", "-text")
    assert result.returncode == 0, result.stderr
    return result.stdout


def cert_subject(path: Path) -> str:
    result = openssl("x509", "-in", str(path), "-noout", "-subject")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def validity_days(path: Path) -> float:
    result = openssl("x509", "-in", str(path), "-noout", "-dates")
    assert result.returncode == 0, result.stderr
    parsed: dict[str, datetime] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        # openssl pads a single-digit day with a space, so collapse runs of
        # whitespace before handing it to strptime.
        normalised = " ".join(value.replace("GMT", "").split())
        parsed[key.strip()] = datetime.strptime(normalised, "%b %d %H:%M:%S %Y")
    return (parsed["notAfter"] - parsed["notBefore"]).total_seconds() / 86400.0


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def snapshot(directory: Path) -> dict[str, bytes]:
    """Every regular file in the directory, by name and content."""
    return {
        entry.name: entry.read_bytes()
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }


def san_block(text: str) -> str:
    """The indented body under `X509v3 Subject Alternative Name`.

    Taken by indentation rather than by regex over the whole block: `openssl
    x509 -text` wraps a long SAN list across lines, and matching only the first
    one would quietly stop checking after the second address.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "X509v3 Subject Alternative Name" not in line:
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for following in lines[index + 1 :]:
            if following.strip() and (len(following) - len(following.lstrip())) <= indent:
                break
            body.append(following)
        return "\n".join(body)
    raise AssertionError("the server certificate carries no subjectAltName at all")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def out_dir(tmp_path) -> Path:
    directory = tmp_path / "pki"
    directory.mkdir()
    return directory


@pytest.fixture
def ca_dir(out_dir) -> Path:
    """A directory holding a freshly generated CA."""
    result = gen_ca(out_dir)
    assert result.returncode == 0, result.stderr
    return out_dir


# ---------------------------------------------------------------------------
# gen-ca.sh - what it produces (SPEC 3.1)
# ---------------------------------------------------------------------------


def test_the_ca_run_succeeds_and_writes_both_files(out_dir):
    result = gen_ca(out_dir)

    assert result.returncode == 0, result.stderr
    assert (out_dir / "ca.key").is_file()
    assert (out_dir / "ca.crt").is_file()


def test_the_out_directory_is_created_if_absent(tmp_path):
    target = tmp_path / "does" / "not" / "exist"

    result = gen_ca(target)

    assert result.returncode == 0, result.stderr
    assert (target / "ca.crt").is_file()


def test_out_defaults_to_the_current_directory(tmp_path):
    result = run_script(GEN_CA, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ca.key").is_file()
    assert (tmp_path / "ca.crt").is_file()


def test_the_ca_subject_is_the_pinned_common_name(ca_dir):
    assert CA_SUBJECT_CN in cert_subject(ca_dir / "ca.crt")


def test_the_ca_basic_constraints_are_critical_and_say_ca_true(ca_dir):
    """The failure this setup has already hit.

    Without `critical`, `curl` happily validates the chain while Python's ssl
    module and iOS both refuse it, so every diagnostic that is easy to run says
    the PKI is fine.
    """
    text = cert_text(ca_dir / "ca.crt")

    match = re.search(
        r"X509v3 Basic Constraints:\s*critical\s*\n\s*CA:TRUE", text
    )
    assert match, (
        "the CA must carry `basicConstraints = critical, CA:TRUE`; got:\n" + text
    )


def test_the_ca_key_usage_is_critical_and_allows_signing(ca_dir):
    text = cert_text(ca_dir / "ca.crt")

    match = re.search(r"X509v3 Key Usage:\s*critical\s*\n(?P<body>\s+.*)", text)
    assert match, "the CA keyUsage must be critical"
    body = match.group("body")
    assert "Certificate Sign" in body
    assert "CRL Sign" in body


def test_the_ca_is_valid_for_3650_days(ca_dir):
    assert validity_days(ca_dir / "ca.crt") == pytest.approx(CA_DAYS, abs=1)


def test_the_ca_key_is_rsa_4096_with_sha256(ca_dir):
    text = cert_text(ca_dir / "ca.crt")

    assert "Public-Key: (4096 bit)" in text
    assert "sha256WithRSAEncryption" in text


def test_the_ca_private_key_parses(ca_dir):
    result = openssl("pkey", "-in", str(ca_dir / "ca.key"), "-noout")

    assert result.returncode == 0, result.stderr


def test_the_ca_is_self_signed(ca_dir):
    result = openssl(
        "verify", "-CAfile", str(ca_dir / "ca.crt"), str(ca_dir / "ca.crt")
    )

    assert result.returncode == 0, result.stderr


def test_the_ca_file_modes_are_0600_and_0644(ca_dir):
    # The private key is the whole trust anchor: world-readable here means every
    # process on the workstation can mint a certificate the phone trusts.
    assert mode_of(ca_dir / "ca.key") == 0o600
    assert mode_of(ca_dir / "ca.crt") == 0o644


def test_a_successful_ca_run_leaves_no_intermediates(ca_dir):
    assert set(snapshot(ca_dir)) == {"ca.key", "ca.crt"}


# ---------------------------------------------------------------------------
# gen-ca.sh - the refusal to clobber (SPEC 3.1)
# ---------------------------------------------------------------------------


def test_a_second_run_refuses_and_changes_nothing(ca_dir):
    """A regenerated CA invalidates every certificate and every phone silently.

    The failure surfaces much later as an app that will not load, with nothing
    pointing back at the moment the CA was replaced, so the refusal has to be
    the default.
    """
    before = snapshot(ca_dir)

    result = gen_ca(ca_dir)

    assert result.returncode == 1
    assert result.stderr.strip(), "a refusal must say why on stderr"
    assert snapshot(ca_dir) == before


@pytest.mark.parametrize("survivor", ["ca.key", "ca.crt"])
def test_either_half_of_an_existing_ca_is_enough_to_refuse(ca_dir, survivor):
    for name in ("ca.key", "ca.crt"):
        if name != survivor:
            (ca_dir / name).unlink()
    before = snapshot(ca_dir)

    result = gen_ca(ca_dir)

    assert result.returncode == 1
    assert snapshot(ca_dir) == before


def test_force_replaces_the_ca(ca_dir):
    before = snapshot(ca_dir)

    result = gen_ca(ca_dir, "--force")

    assert result.returncode == 0, result.stderr
    assert snapshot(ca_dir) != before
    assert mode_of(ca_dir / "ca.key") == 0o600


def test_a_replaced_ca_is_still_a_valid_ca(ca_dir):
    gen_ca(ca_dir, "--force")

    assert "CA:TRUE" in cert_text(ca_dir / "ca.crt")
    assert validity_days(ca_dir / "ca.crt") == pytest.approx(CA_DAYS, abs=1)


# ---------------------------------------------------------------------------
# gen-ca.sh - usage errors (SPEC 3)
# ---------------------------------------------------------------------------


def test_an_unknown_flag_to_gen_ca_is_a_usage_error(out_dir):
    result = gen_ca(out_dir, "--overwrite-everything")

    assert result.returncode == 2
    assert result.stderr.strip()


def test_a_usage_error_writes_nothing_at_all(out_dir):
    gen_ca(out_dir, "--overwrite-everything")

    assert snapshot(out_dir) == {}


def test_a_usage_error_does_not_disturb_an_existing_ca(ca_dir):
    before = snapshot(ca_dir)

    result = gen_ca(ca_dir, "--overwrite-everything")

    assert result.returncode == 2
    assert snapshot(ca_dir) == before


def test_a_positional_argument_to_gen_ca_is_a_usage_error(out_dir):
    # gen-ca.sh takes no positional arguments; passing an IP is the classic
    # confusion with gen-cert.sh and must not be silently ignored.
    result = gen_ca(out_dir, IP_A)

    assert result.returncode == 2
    assert snapshot(out_dir) == {}


# ---------------------------------------------------------------------------
# gen-cert.sh - what it produces (SPEC 3.2)
# ---------------------------------------------------------------------------


@pytest.fixture
def server_dir(ca_dir) -> Path:
    result = gen_cert(ca_dir, IP_A, IP_B)
    assert result.returncode == 0, result.stderr
    return ca_dir


def test_the_cert_run_succeeds_and_writes_both_files(ca_dir):
    result = gen_cert(ca_dir, IP_A)

    assert result.returncode == 0, result.stderr
    assert (ca_dir / "server.key").is_file()
    assert (ca_dir / "server.crt").is_file()


def test_the_san_holds_an_ip_entry_for_every_argument(server_dir):
    body = san_block(cert_text(server_dir / "server.crt"))

    assert f"IP Address:{IP_A}" in body
    assert f"IP Address:{IP_B}" in body


def test_no_dns_entry_is_ever_emitted(server_dir):
    """The `DNS:`-instead-of-`IP:` trap.

    iOS refuses a certificate whose address only appears as a dNSName, and the
    refusal looks like a network fault rather than a certificate one, so nothing
    in the app points at the real cause.
    """
    text = cert_text(server_dir / "server.crt")

    assert "DNS:" not in text


def test_the_subject_common_name_is_the_first_ip(server_dir):
    # openssl prints `CN = x` on 1.1.1+ and `CN=x` on older builds.
    subject = cert_subject(server_dir / "server.crt")

    assert re.search(rf"CN\s*=\s*{re.escape(IP_A)}\b", subject), subject


def test_the_server_certificate_is_valid_for_820_days(server_dir):
    assert validity_days(server_dir / "server.crt") == pytest.approx(
        SERVER_DAYS, abs=1
    )


def test_the_server_certificate_is_within_the_ios_825_day_limit(server_dir):
    # iOS rejects anything longer outright, with no diagnostic worth reading.
    assert validity_days(server_dir / "server.crt") <= IOS_MAX_DAYS


def test_the_server_certificate_verifies_against_the_ca(server_dir):
    result = openssl(
        "verify",
        "-CAfile",
        str(server_dir / "ca.crt"),
        str(server_dir / "server.crt"),
    )

    assert result.returncode == 0, result.stderr


def test_the_server_key_is_rsa_2048_with_sha256(server_dir):
    text = cert_text(server_dir / "server.crt")

    assert "Public-Key: (2048 bit)" in text
    assert "sha256WithRSAEncryption" in text


def test_the_server_private_key_parses_and_matches_the_certificate(server_dir):
    # Compared as public keys rather than as RSA moduli: `openssl pkey` has no
    # `-modulus`, and an algorithm-specific check would have to be rewritten the
    # day the key type changes.
    parsed = openssl("pkey", "-in", str(server_dir / "server.key"), "-noout")
    assert parsed.returncode == 0, parsed.stderr

    from_key = openssl("pkey", "-in", str(server_dir / "server.key"), "-pubout")
    from_cert = openssl("x509", "-in", str(server_dir / "server.crt"), "-noout", "-pubkey")
    assert from_key.returncode == 0, from_key.stderr
    assert from_cert.returncode == 0, from_cert.stderr

    assert from_key.stdout.strip() == from_cert.stdout.strip()


def test_the_server_certificate_is_not_a_ca(server_dir):
    text = cert_text(server_dir / "server.crt")

    assert "CA:FALSE" in text


def test_the_server_key_usage_is_critical_and_scoped(server_dir):
    text = cert_text(server_dir / "server.crt")

    match = re.search(r"X509v3 Key Usage:\s*critical\s*\n(?P<body>\s+.*)", text)
    assert match, "the server keyUsage must be critical"
    body = match.group("body")
    assert "Digital Signature" in body
    assert "Key Encipherment" in body


def test_the_extended_key_usage_is_server_auth(server_dir):
    text = cert_text(server_dir / "server.crt")

    match = re.search(r"X509v3 Extended Key Usage:.*\n(?P<body>\s+.*)", text)
    assert match, "the server certificate must carry extendedKeyUsage"
    assert "TLS Web Server Authentication" in match.group("body")


def test_the_server_file_modes_are_0600_and_0644(server_dir):
    assert mode_of(server_dir / "server.key") == 0o600
    assert mode_of(server_dir / "server.crt") == 0o644


def test_a_single_ip_yields_exactly_one_san_entry(ca_dir):
    gen_cert(ca_dir, IP_A)

    body = san_block(cert_text(ca_dir / "server.crt"))
    assert body.count("IP Address:") == 1


def test_three_ips_all_reach_the_san(ca_dir):
    result = gen_cert(ca_dir, IP_A, IP_B, IP_C)

    assert result.returncode == 0, result.stderr
    body = san_block(cert_text(ca_dir / "server.crt"))
    for address in (IP_A, IP_B, IP_C):
        assert f"IP Address:{address}" in body


def test_the_paths_to_configure_are_printed(server_dir):
    """The output is the only place the user learns what to put in the config."""
    result = gen_cert(server_dir, IP_A)

    assert result.returncode == 0, result.stderr
    assert str(server_dir / "server.crt") in result.stdout
    assert str(server_dir / "server.key") in result.stdout
    assert str(server_dir / "ca.crt") in result.stdout


# ---------------------------------------------------------------------------
# gen-cert.sh - reissue and refusals (SPEC 3.2)
# ---------------------------------------------------------------------------


def test_reissuing_overwrites_without_a_flag(server_dir):
    """Reissue after an address change is the routine case.

    Requiring a flag for the routine operation trains the user to pass it for
    the dangerous one too, which is what `gen-ca.sh --force` guards.
    """
    before = (server_dir / "server.key").read_bytes()

    result = gen_cert(server_dir, IP_C)

    assert result.returncode == 0, result.stderr
    assert (server_dir / "server.key").read_bytes() != before
    assert f"IP Address:{IP_C}" in san_block(cert_text(server_dir / "server.crt"))


def test_reissuing_never_touches_the_ca(server_dir):
    ca_before = (server_dir / "ca.key").read_bytes(), (
        server_dir / "ca.crt"
    ).read_bytes()

    gen_cert(server_dir, IP_C)

    assert (
        (server_dir / "ca.key").read_bytes(),
        (server_dir / "ca.crt").read_bytes(),
    ) == ca_before


def test_a_missing_ca_is_a_refusal_not_a_usage_error(out_dir):
    result = gen_cert(out_dir, IP_A)

    assert result.returncode == 1
    assert result.stderr.strip()
    assert snapshot(out_dir) == {}


def test_a_nonexistent_out_directory_is_the_missing_ca_refusal(tmp_path):
    """`gen-cert.sh` does not create `--out`, unlike `gen-ca.sh`.

    A directory with no CA in it is already the missing-CA refusal, and creating
    it first would only make the error point at a path the script had just
    invented.
    """
    target = tmp_path / "never" / "created"

    result = gen_cert(target, IP_A)

    assert result.returncode == 1
    assert result.stderr.strip()
    assert not target.exists()


def test_no_ip_argument_is_a_usage_error(ca_dir):
    before = snapshot(ca_dir)

    result = gen_cert(ca_dir)

    assert result.returncode == 2
    assert result.stderr.strip()
    assert snapshot(ca_dir) == before


def test_an_unknown_flag_to_gen_cert_is_a_usage_error(ca_dir):
    before = snapshot(ca_dir)

    result = gen_cert(ca_dir, "--san", IP_A)

    assert result.returncode == 2
    assert snapshot(ca_dir) == before


@pytest.mark.parametrize(
    "argument",
    [
        "pwnagotchi.local",
        "localhost",
        "::1",
        "fe80::1",
        "1.2.3.4/24",
        "256.1.1.1",
        "192.0.2",
        "192.0.2.1.5",
        "192.0.2.a",
        "010.1.1.1",
        "01.2.3.4",
        "1.2.3.4.",
        "0.0.0.0",
        "",
    ],
    ids=[
        "hostname",
        "localhost",
        "ipv6-loopback",
        "ipv6-link-local",
        "cidr",
        "octet-out-of-range",
        "too-few-octets",
        "too-many-octets",
        "non-numeric-octet",
        "leading-zero-three-digit",
        "leading-zero-two-digit",
        "trailing-dot",
        "wildcard",
        "empty",
    ],
)
def test_anything_that_is_not_an_ipv4_address_is_a_usage_error(ca_dir, argument):
    """A hostname here is the trap that only shows up on the phone.

    It is rejected at the one point where the mistake is still visible, so the
    check is on the argument rather than on the certificate that would result.

    Leading zeros are rejected rather than guessed at: `010.1.1.1` is ten to
    `inet_pton` and eight to anything that reads the octet as octal, and a
    certificate is the wrong place to discover which. `0.0.0.0` is rejected
    because it is the wildcard D5 exists to keep out and never an address the
    phone can reach the Pi on.
    """
    before = snapshot(ca_dir)

    result = gen_cert(ca_dir, argument)

    assert result.returncode == 2, (
        f"{argument!r} is not an IPv4 address and must exit 2; "
        f"got {result.returncode}"
    )
    assert result.stderr.strip()
    assert snapshot(ca_dir) == before


@pytest.mark.parametrize(
    "argument",
    ["0.1.2.3", "255.255.255.255", "10.0.0.2", "172.20.10.7"],
    ids=["zero-first-octet", "broadcast", "usb-gadget", "bt-pan"],
)
def test_a_canonical_dotted_quad_is_accepted(ca_dir, argument):
    """The boundaries of the rule, from the other side.

    A validator strict enough to reject `010.1.1.1` is easy to make strict
    enough to reject `0.1.2.3` too, and the two tether addresses this project
    exists for are the ones that must never regress.
    """
    result = gen_cert(ca_dir, argument)

    assert result.returncode == 0, result.stderr
    assert f"IP Address:{argument}" in san_block(cert_text(ca_dir / "server.crt"))


def test_one_bad_argument_among_good_ones_writes_nothing(ca_dir):
    # Validation happens before any file is produced: a half-written pair with
    # a missing address is worse than a clean refusal.
    before = snapshot(ca_dir)

    result = gen_cert(ca_dir, IP_A, "pwnagotchi.local", IP_B)

    assert result.returncode == 2
    assert snapshot(ca_dir) == before


def test_a_refusal_leaves_an_earlier_server_pair_untouched(server_dir):
    before = snapshot(server_dir)

    result = gen_cert(server_dir, "pwnagotchi.local")

    assert result.returncode == 2
    assert snapshot(server_dir) == before


# ---------------------------------------------------------------------------
# Both scripts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", [GEN_CA, GEN_CERT], ids=["gen-ca", "gen-cert"])
def test_both_scripts_start_with_a_posix_sh_shebang(script):
    assert script.read_bytes().startswith(b"#!/bin/sh"), (
        f"{script.name} must start with #!/bin/sh so it cannot silently become "
        "bash-only"
    )


@pytest.mark.parametrize("script", [GEN_CA, GEN_CERT], ids=["gen-ca", "gen-cert"])
def test_both_scripts_are_committed_executable(script):
    """`./tools/gen-ca.sh` is what the documentation tells the user to run.

    The assertion is on the mode git records, not the one on disk. Git tracks
    only the executable bit, and a checkout applies the local umask to the rest,
    so a working copy that reads 775 is normal and says nothing about what was
    committed. Asserting the filesystem mode makes this test fail for anyone
    whose umask is 002 while still passing for a file added as 100644, which is
    exactly backwards.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-s", "--", str(script.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert listed, f"{script} is not tracked by git"
    assert listed[0] == "100755", f"{script} is committed as {listed[0]}, not 100755"


@pytest.mark.parametrize("which", ["gen-ca", "gen-cert"])
def test_a_flag_without_its_value_is_a_usage_error(which, tmp_path):
    result = (
        run_script(GEN_CA, "--out", cwd=tmp_path)
        if which == "gen-ca"
        else run_script(GEN_CERT, "--out", cwd=tmp_path)
    )

    assert result.returncode == 2
    assert result.stderr.strip()
    assert snapshot(tmp_path) == {}


def test_a_usage_error_outranks_a_refusal(out_dir):
    """SPEC section 3: usage errors are decided before refusals.

    This invocation is wrong twice over - there is no CA to sign with *and* the
    argument is not an address - and the exit status has to name the mistake the
    user can act on.
    """
    result = gen_cert(out_dir, "pwnagotchi.local")

    assert result.returncode == 2
    assert snapshot(out_dir) == {}


def test_a_usage_error_outranks_the_existing_ca_refusal(ca_dir):
    result = gen_ca(ca_dir, "--nonsense")

    assert result.returncode == 2


@pytest.mark.parametrize("which", ["gen-ca", "gen-cert"])
def test_neither_script_ever_prompts(which, out_dir):
    """SPEC 3 requires non-interactive scripts.

    `run_script` closes stdin and bounds the run with a timeout, so a script
    that waits for input fails the test instead of hanging the suite. `openssl
    req` prompts for every subject field unless `-subj` or a `prompt = no`
    config is supplied, which is the usual way this regresses.
    """
    assert gen_ca(out_dir).returncode == 0

    result = gen_ca(out_dir, "--force") if which == "gen-ca" else gen_cert(out_dir, IP_A)

    assert result.returncode == 0, result.stderr


def test_the_whole_chain_works_end_to_end(out_dir):
    """The sequence a user actually runs, in order, once."""
    assert gen_ca(out_dir).returncode == 0
    assert gen_cert(out_dir, IP_A, IP_B).returncode == 0

    assert (
        openssl(
            "verify",
            "-CAfile",
            str(out_dir / "ca.crt"),
            str(out_dir / "server.crt"),
        ).returncode
        == 0
    )
    # Exactly the four files the user is told about. The CSR, the generated
    # openssl config and the CA serial live in a temp directory and must never
    # turn up next to them.
    assert set(snapshot(out_dir)) == {
        "ca.key",
        "ca.crt",
        "server.key",
        "server.crt",
    }
