"""`tests/e2e_unit.py` and `tests/fakes/harness.py` themselves (issue #185).

Nothing in the rest of the pytest suite imports either module, so before this
file the frontend e2e job was the only gate that ever executed them, and
`harness.py`'s own claim -- "any drift between the two [FakeAgent
definitions] is a defect in this file, since SPEC section 11 is the single
source both are reduced from" -- was enforced by nobody. This file is the
enforcement: it imports both modules, so a `ruff`/import error in either one
now fails here rather than only in a Playwright job three minutes into a
browser download, and it compares harness.py's fakes against conftest.py's
by construction rather than by eye.

No listener is ever started here. `tests.e2e_unit.main()` is never called;
only its pure helpers (`build_options`, `build_agent`) and the module-level
constants are exercised, the same restraint `tests/conftest.py` already
applies to the plugin itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from conftest import DepsHarness as ConftestDepsHarness
from conftest import FakeAgent as ConftestFakeAgent
from plugin import companion

import tests.e2e_unit as e2e_unit
import tests.fakes.harness as harness


def _public_attributes(obj: object) -> set[str]:
    """Every non-dunder name `dir()` reports for `obj`.

    Used instead of `vars(obj)` because a fake's methods (`session`, `view`)
    are class attributes, not instance ones, and both need to be in this
    comparison for it to be the "public attribute set" the review asked for
    rather than only the instance `__dict__`.
    """
    return {name for name in dir(obj) if not name.startswith("__")}


# ---------------------------------------------------------------------------
# The stub injection: harness.install_pwnagotchi_stub() must be a no-op (or
# at least idempotent) under a pytest session where conftest.py already put
# the stub on sys.path and imported it, module-level, before this file's own
# `import tests.e2e_unit` (which calls the same function again at its own
# module scope) ever ran.
# ---------------------------------------------------------------------------


def test_install_pwnagotchi_stub_is_idempotent_under_conftests_own_injection():
    import pwnagotchi

    before_file = pwnagotchi.__file__
    before_module = pwnagotchi

    result = harness.install_pwnagotchi_stub()

    # Same module object back, not a second import that happened to agree --
    # `sys.modules['pwnagotchi']` was never replaced, and importing it again
    # is a cache hit rather than a second stub package taking its place.
    assert result is before_module
    assert result.__file__ == before_file
    assert Path(result.__file__).is_relative_to(harness.STUB_DIR)


def test_e2e_unit_module_already_ran_the_same_stub_injection_at_import_time():
    # tests/e2e_unit.py calls install_pwnagotchi_stub() at module scope,
    # before its own `from plugin import companion`, mirroring
    # conftest.py's ordering (module docstring, point 1). Importing it above
    # already proved that ran without raising; this pins which pwnagotchi it
    # left behind, so a future version that imports companion first and only
    # then stubs it fails here instead of only on a real pwnagotchi install.
    import pwnagotchi

    assert Path(pwnagotchi.__file__).is_relative_to(harness.STUB_DIR)


# ---------------------------------------------------------------------------
# build_options(): the host and the two ports resolve where §10.5 (issue
# #185) says they must -- `bind_addresses` a single-element list, never
# `0.0.0.0`, and each port carried through rather than defaulted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,ws_port,http_port",
    [
        ("127.0.0.1", 8082, 8443),
        # A second, distinct set of values: if build_options ever hardcoded
        # the first pair instead of resolving its arguments, this is the
        # case that would notice.
        ("10.0.0.2", 18082, 18443),
    ],
)
def test_build_options_resolves_bind_addresses_and_ports(tmp_path, host, ws_port, http_port):
    options = e2e_unit.build_options(
        host,
        ws_port,
        http_port,
        tmp_path / "web",
        tmp_path / "server.crt",
        tmp_path / "server.key",
    )

    assert options["bind_addresses"] == [host]
    assert options["ws_port"] == ws_port
    assert options["http_port"] == http_port


# ---------------------------------------------------------------------------
# build_agent(): the same shape as conftest.py's own FakeAgent, by
# construction rather than by eye (harness.py's own stated intent).
# ---------------------------------------------------------------------------


def test_build_agent_public_attributes_match_conftest_fake_agent(tmp_path):
    conftest_agent = ConftestFakeAgent()
    e2e_agent = e2e_unit.build_agent(tmp_path)

    conftest_public = _public_attributes(conftest_agent)
    e2e_public = _public_attributes(e2e_agent)

    assert e2e_public == conftest_public

    # The eight names the review named explicitly, checked one by one on top
    # of the set equality above: the equality would already fail if any of
    # these were missing from either side, but a name-by-name failure says
    # which one rather than only that the two sets differ.
    for name in (
        "mode",
        "_access_points",
        "_handshakes",
        "_peers",
        "_current_channel",
        "_config",
        "session",
        "view",
    ):
        assert hasattr(conftest_agent, name), f"conftest.FakeAgent has no {name!r}"
        assert hasattr(e2e_agent, name), f"tests.e2e_unit.build_agent()'s agent has no {name!r}"


# ---------------------------------------------------------------------------
# harness.DepsHarness fills exactly the Deps seam names conftest.py's own
# DepsHarness does -- compared by intercepting the keyword names each one
# actually passes to companion.Deps, not by reading the two source files
# side by side.
# ---------------------------------------------------------------------------


def test_harness_deps_harness_passes_the_same_deps_seam_names_as_conftest(monkeypatch):
    def _capturing(target: dict) -> type:
        class _Recorder:
            def __init__(self, **kwargs):
                target.update(kwargs)

        return _Recorder

    conftest_kwargs: dict = {}
    monkeypatch.setattr(companion, "Deps", _capturing(conftest_kwargs))
    ConftestDepsHarness()

    harness_kwargs: dict = {}
    monkeypatch.setattr(companion, "Deps", _capturing(harness_kwargs))
    harness.DepsHarness()

    assert set(harness_kwargs) == set(conftest_kwargs)
    # Not merely equal keys: an empty pair of sets would satisfy the
    # assertion above for the wrong reason if neither harness reached
    # companion.Deps at all.
    assert set(harness_kwargs) == {
        "now",
        "restart_pwnagotchi",
        "reboot_device",
        "shutdown_device",
        "run_command",
        "read_gpsd",
        "read_pisugar_i2c",
        "list_local_ipv4",
        "spawn",
    }


# ---------------------------------------------------------------------------
# A stats payload built by the real Router, over the harness's own fakes,
# validates against the schema -- the seam this whole file exists to gate
# is a Router that a real client will actually be sent, and a schema
# violation here would otherwise only surface as `bad_request`-shaped
# silence in a Playwright job.
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def registry(schemas_dir) -> Registry:
    common = Resource.from_contents(
        _load(schemas_dir / "common.json"), default_specification=DRAFT202012
    )
    return Registry().with_resources(
        [
            ("common.json", common),
            ("outgoing/common.json", common),
        ]
    )


@pytest.fixture(scope="session")
def stats_validator(schemas_dir, registry) -> Draft202012Validator:
    schema = _load(schemas_dir / "outgoing" / "stats.json")
    schema.pop("$id", None)
    return Draft202012Validator(schema, registry=registry)


def test_router_stats_over_the_harness_agent_validates_against_the_schema(
    tmp_path, stats_validator
):
    web_root = tmp_path / "web"
    web_root.mkdir()
    options = e2e_unit.build_options(
        "127.0.0.1",
        8082,
        8443,
        web_root,
        tmp_path / "server.crt",
        tmp_path / "server.key",
    )

    deps_harness = harness.DepsHarness()
    deps_harness.local_ipv4 = ["127.0.0.1"]
    deps = deps_harness.deps

    agent = e2e_unit.build_agent(tmp_path)
    agent_getter = lambda: agent  # noqa: E731

    session = companion.SessionCache(agent_getter, deps)
    gps = companion.GpsResolver(options, deps, session)
    battery = companion.BatteryReader(options, deps)
    handshake_store = lambda: companion.HandshakeStore(  # noqa: E731
        agent._config["bettercap"]["handshakes"]
    )
    router = companion.Router(
        options,
        deps,
        agent_getter,
        session,
        gps,
        battery,
        handshake_store,
        companion.__version__,
    )

    message = companion.envelope("stats", router.stats(), 1.0)

    stats_validator.validate(message)
