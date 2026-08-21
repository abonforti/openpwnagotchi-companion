"""Regression test for the plugin failing to import under pwnagotchi's own loader.

Every other test file in this suite reaches `plugin/companion.py` through
`from plugin import companion` (directly, or via `conftest.py`'s module-scope
import), which is an ordinary Python import: it registers the module in
`sys.modules` under its dotted name before the module body finishes running,
and keeps it there.

`pwnagotchi/plugins/__init__.py`'s `load_from_file` does not do that. It is
three lines:

    spec = importlib.util.spec_from_file_location(plugin_name, filename)
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)

`module_from_spec` builds the module object; `exec_module` runs its body
against that object's own `__dict__`. Neither step puts the module into
`sys.modules`. Any code that runs during import and depends on finding its own
module there via `sys.modules.get(__name__)` (directly, or through a stdlib
facility that does that lookup on the caller's behalf, e.g. the way
`dataclasses` resolves string annotations) sees `None` instead and the import
dies. pwnagotchi swallows that into a single warning line and the unit boots
with no companion plugin at all - the whole 925-test suite was green while
this was true, because none of it exercised the loader that actually runs on
the device.

This file is the one exception to "import through `plugin.companion`": it
deliberately reproduces both loader shapes byte for byte, using
`importlib.util` directly rather than any fixture that imports the plugin the
easy way.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

import pwnagotchi.plugins as plugins

PLUGIN_FILE = Path(__file__).resolve().parent.parent / "plugin" / "companion.py"


def _load(module_name: str, *, register_first: bool) -> ModuleType:
    """Reproduces exactly what `pwnagotchi.plugins.load_from_file` does.

    `register_first` toggles the one thing that file does *not* do: assigning
    `sys.modules[module_name]` before `exec_module` runs. `register_first=False`
    is the real loader's behaviour and the one that broke on hardware;
    `register_first=True` is the ordinary-import shape every other test in this
    suite already relies on, kept here so this file proves both work rather than
    silently pinning the broken one.
    """
    assert module_name not in sys.modules, (
        f"test setup error: {module_name!r} is already in sys.modules; "
        "choose a name unique to this test"
    )
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_FILE)
    assert spec is not None and spec.loader is not None, (
        f"could not build an import spec for {PLUGIN_FILE}"
    )
    module = importlib.util.module_from_spec(spec)
    if register_first:
        sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - re-raised as a named assertion below
        sys.modules.pop(module_name, None)
        pytest.fail(
            "plugin/companion.py failed to import the way pwnagotchi actually "
            "loads a plugin: importlib.util.spec_from_file_location() + "
            "exec_module(), WITHOUT the module ever being registered in "
            "sys.modules first. pwnagotchi/plugins/__init__.py's "
            "load_from_file() never assigns sys.modules[plugin_name] before "
            "calling exec_module, unlike an ordinary `import`, so any import-"
            "time code that expects to find its own module via "
            "sys.modules.get(__name__) - directly or through a stdlib facility "
            "that does that lookup on its behalf - finds None instead and "
            f"raises. Underlying exception: {exc!r}"
        )
    return module


#: The module names this file's own `_load()` calls register, and only those
#: - not every name that happened to enter `sys.modules` while a test ran.
#: `plugin/companion.py` imports real modules at its own top level (`ssl`,
#: `websockets`, ...), and the day it grows one that no other test file
#: imported first, sweeping every newcomer would evict a genuinely shared
#: module from the cache and hand the next importer a second copy with its
#: own state, rather than the one everything else is using.
_OWNED_MODULE_NAMES = (
    "companion_unregistered_loader_test",
    "companion_registered_loader_test",
)


@pytest.fixture(autouse=True)
def _no_leaked_module_names():
    """Leaves `sys.modules` exactly as found, whichever way the test goes.

    A half-initialised module left under one of this file's own names would
    poison the module cache for every test file collected afterwards in the
    same process, and the failures that produces point at whatever file runs
    next rather than at this one.
    """
    yield
    for name in _OWNED_MODULE_NAMES:
        sys.modules.pop(name, None)


def test_imports_without_being_registered_in_sys_modules():
    """The loader contract that broke on the device.

    `load_from_file` never puts the module in `sys.modules`, so this must
    succeed with the name absent throughout - the precondition is asserted by
    `_load` itself, not merely hoped for.
    """
    module = _load("companion_unregistered_loader_test", register_first=False)

    assert hasattr(module, "Companion"), (
        "the module imported but does not expose a `Companion` class - "
        "SPEC.md section 2 declares `class Companion(plugins.Plugin)`"
    )
    assert isinstance(module.Companion, type)
    assert issubclass(module.Companion, plugins.Plugin), (
        "Companion must subclass pwnagotchi.plugins.Plugin (SPEC.md section 2)"
    )

    # Instantiable, the way pwnagotchi's loader does after exec_module returns.
    instance = module.Companion()
    assert isinstance(instance, module.Companion)


def test_imports_when_already_registered_in_sys_modules():
    """The ordinary-import shape, so this file says "both ways work".

    Without this test, a change that only fixes the unregistered case by
    special-casing "module absent from sys.modules" - rather than removing the
    dependency on sys.modules registration altogether - could regress the
    shape every other test file in this suite relies on without anything here
    noticing.
    """
    module = _load("companion_registered_loader_test", register_first=True)

    assert sys.modules["companion_registered_loader_test"] is module
    assert hasattr(module, "Companion")
    assert isinstance(module.Companion, type)
    assert issubclass(module.Companion, plugins.Plugin)

    instance = module.Companion()
    assert isinstance(instance, module.Companion)
