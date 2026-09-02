"""SPEC 2.3.0: no logging call in `plugin/companion.py` interpolates a
configuration value (issue #16's port refusal, and the `gpsd_host` refusal
next to it).

The rule exists because `get_log` (SPEC 2.9) serves this file's log to a
connected client: a config value written into a log line is a config value
on the wire, which is exactly what the `gpsd_host` refusal's own comment
warns against - and what the `ws_port`/`http_port` refusals did anyway,
about twelve hundred lines below it, with that comment sitting in full view.
A rule that survives being restated in prose right next to the code that
breaks it needs a mechanical check, not more prose, so this module reads
`plugin/companion.py` as data with `ast` - the same shape
`tests/test_check_secrets.py` uses to check its own source for a
contiguous probe, and `tests/test_spec_tree.py` uses to check SPEC.md's
tree rather than pattern-match it - and asserts no logging call carries a
configuration value as an argument.

**What counts as "a configuration value" here, and why this reading and not
a wider one.** A single AST-only, single-function pass cannot do real
dataflow analysis, so the scope is deliberately narrow and mechanical:

- Any argument to a `log.<level>(...)` call (`debug`/`info`/`warning`/
  `error`/`critical`/`exception`, resolved by finding the name(s) assigned
  from `logging.getLogger(...)` rather than being hardcoded to `"log"`) that
  is, or contains, a call to the module's `option()` helper, a `DEFAULTS[...]`
  subscript, a `.get(...)` call on something named `options`/`_options`
  (optionally `self.`-qualified), or a subscript on the same - whether that
  expression sits inline in the call or was assigned to a local variable
  earlier in the *same function* and passed to the log call as a bare name
  (chased back through up to three hops of `name = other_name`).
- This is checked in every function/method in the module, not just the two
  the ticket names, so a violation anywhere in the file fails the same way.

**One hop through a parameter is also covered, and no further.** A value
threaded through a function *parameter* and logged inside the callee was
invisible to a single-function pass - `clamp_interval` received `value`,
which every caller passed as `option(self.options, "...")`, as a parameter
and logged it directly when it failed to parse as a number; `build_ssl_context`
received `cert_path`/`key_path` the same way. Both were real, shipped
instances of the shape this file exists to catch, and the fix is one hop,
not a call graph: if *every* call site of a function passes a config-source
expression for parameter *N*, parameter *N* is treated as a configuration
value for the rest of that function's own body, exactly as if it had been
read there directly. `_config_parameters_by_call_site_agreement` computes
this once per function before the per-function scan, by walking every bare
`func_name(...)` call in the module and checking what each one supplies for
that position (positionally or by matching keyword name).

This hop is deliberately bounded to stay decidable without a call graph:

- Only a **bare** call, `func_name(...)`, is matched as a call site -
  `self.func_name(...)` or any other attribute access is not, so this only
  ever applies to a plain module-level function (`clamp_interval` and
  `build_ssl_context` are both this shape; a method is not covered).
- "A configuration read" is narrower here than the direct-call check above:
  a bare `DEFAULTS[key]` subscript does not, by itself, qualify a parameter,
  only `option(...)` or a `.get(...)`/subscript on something named
  `options`/`_options` does. `clamp_interval`'s `default` parameter is
  supplied as `float(DEFAULTS["..."])` at every call site, and it is
  deliberately logged (`"using default %s"`) as the safe value that
  replaced a rejected one - a bare `DEFAULTS[...]` subscript is always the
  same hardcoded constant regardless of what the owner configured, unlike
  `option(...)`, which resolves to the owner's actual value when they set
  one. Without this narrowing the hop would flag the module's own fix.
- A parameter must be supplied by *every* call site to qualify. A call site
  that omits it and falls through to the parameter's own default breaks
  agreement for that parameter, because a default is not a configuration
  read - the parameter is then left untracked rather than flagged.
- `*args`/`**kwargs` and keyword-only parameters are not bound; only
  `func.args.args`, matched positionally or by the declared name.
- The hop is exactly one function deep: a parameter of `clamp_interval` that
  were itself threaded into a second callee would not be seen there, and
  `self.<attr>` set from a config value in one method and read in another
  remains invisible for the same reason as before - only bare local
  variables and, now, bare parameters are tracked, not attributes.
- Two functions of the same name in different scopes (a module-level
  function and an unrelated method sharing its name) would be conflated,
  because call sites are matched on the bare name alone with no scope
  resolution. Neither `clamp_interval` nor `build_ssl_context` collides with
  anything else in this file today, but a same-named method added later
  could make this hop attribute a call site to the wrong function.

**What still slips through, unchanged from before, and now named in SPEC.md
itself rather than left for review alone to rediscover.** SPEC 2.3.0 says it
plainly: "the checker follows locals and one hop through a parameter, not
attributes, so `self._ws_port` is invisible to it... but the same blindness
means an attribute is the one shape in which this leak can return unnoticed."
That is this bullet, restated where the rule lives rather than only here:

- A value assigned to `self.<attr>` in one method and logged from a
  different method: only bare local-variable and, now, bare-parameter
  tracking are implemented, not attribute tracking. Review covers this gap;
  this test does not, and SPEC.md says so rather than leaving it implicit.
- The one place an attribute *is* deliberately logged is the listening line,
  `[companion] listening on wss://<address>:<port>`, which interpolates
  `self._ws_port` and `self._http_port` on purpose (SPEC 2.3.0) - both values
  are already published to every client anyway, once as the port they
  connected on and once inside the CSP's `connect-src` origin (SPEC 2.15.1).
  It is the single exemption the rule carries, and it exists exactly because
  this checker cannot see attributes at all: nothing here enforces that the
  exemption stays this narrow, which is one more reason it is review's job
  and not this file's.
- **An exception object is the other shape**, and SPEC 2.3.0 says it is the
  one that actually produced the two defects this file exists for:
  `except ... as err` followed by logging `err` interpolates nothing this
  checker can see - no `option(...)`, no `DEFAULTS[...]`, no `options.get(...)`
  anywhere in the call - while the library that raised the exception may have
  embedded the rejected value in its own text regardless (`ipaddress`'s
  `ValueError` for `gpsd_host`, `int()`'s `invalid literal for int() with
  base 10: ...` for `gpsd_port`). This is review's gap to cover, the same as
  the attribute one above; the positive controls below prove the checker
  catches the shapes it is supposed to, not that it catches this one, which
  it structurally cannot.
- A config-derived name embedded *inside* a larger expression at the log
  call site (string concatenation, an f-string, a nested function call
  wrapping the name) is only resolved when it is the direct value of that
  expression being walked - not when it is itself a bare `Name` nested two
  or more levels down inside something else that isn't itself walked back
  to its assignment. The inline case (`option(...)` or `DEFAULTS[...]`
  written straight into the call) is still caught wherever it appears,
  nested or not, because that shape needs no variable resolution at all.

So this test proves the two named refusals - and anything shaped exactly
like them, including the one-parameter-hop shape `clamp_interval` and
`build_ssl_context` used - are clean, and it proves the checker actually
bites with positive controls built from the historic shapes (a local
variable read via `options.get(...)` and then logged; a parameter every
call site supplies from a configuration read and then logged), not just
from a trivial inline call. It is not a certificate that no configuration
value is ever logged.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION_PATH = REPO_ROOT / "plugin" / "companion.py"

_LOGGER_LEVELS = {"debug", "info", "warning", "error", "critical", "exception"}
_OPTIONS_RECEIVER = re.compile(r"^(self\.)?_?options$")
_MAX_NAME_HOPS = 3


class Violation:
    def __init__(self, function: str, lineno: int, snippet: str) -> None:
        self.function = function
        self.lineno = lineno
        self.snippet = snippet

    def __str__(self) -> str:
        return f"{self.function}:{self.lineno}: {self.snippet}"


def _logger_names(tree: ast.AST) -> set[str]:
    """Names bound to `logging.getLogger(...)`, found rather than assumed.

    `plugin/companion.py` only ever binds one, `log`, at module scope - but
    finding it by parsing the assignment is the same discipline
    `test_spec_tree.py`'s docstring asks for: guessing the name would make
    this test blind to a rename it should still catch.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        is_getlogger = (isinstance(func, ast.Name) and func.id == "getLogger") or (
            isinstance(func, ast.Attribute) and func.attr == "getLogger"
        )
        if is_getlogger:
            names.add(node.targets[0].id)
    return names


def _is_config_source(node: ast.AST) -> bool:
    """A direct read of a configuration value: `option(...)`, `DEFAULTS[...]`,
    or a `.get(...)`/subscript on something named `options`/`_options`
    (optionally `self.`-qualified).
    """
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "option":
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and _OPTIONS_RECEIVER.match(_unparse(func.value))
        ):
            return True
        return False
    if isinstance(node, ast.Subscript):
        base = node.value
        if isinstance(base, ast.Name) and base.id == "DEFAULTS":
            return True
        if _OPTIONS_RECEIVER.match(_unparse(base)):
            return True
    return False


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _contains_config_source(expr: ast.AST) -> bool:
    return any(_is_config_source(n) for n in ast.walk(expr))


def _is_owner_read_source(node: ast.AST) -> bool:
    """A read of the *owner's* configuration specifically: `option(...)` or
    a `.get(...)`/subscript on something named `options`/`_options` - the
    same as `_is_config_source` but without its `DEFAULTS[...]` branch.

    This narrower test is used only to decide whether a *parameter* counts
    as configuration for the one-hop rule below, because a bare
    `DEFAULTS[key]` passed as an argument - unlike `option(...)`, which
    resolves to `DEFAULTS[key]` only when the owner did not set anything -
    is always the same hardcoded constant no matter what the owner
    configured. `clamp_interval`'s `default` parameter is exactly this: every
    call site supplies it as `float(DEFAULTS["..."])`, and logging it is the
    correct behaviour the fix relies on ("using default %s"), not a leak.
    Treating it as tainted here would flag the module's own fix, which is
    the wrong outcome for a checker whose job is to find leaks, not to
    manufacture one on the safe path. The direct-call check
    (`_is_config_source`/`_contains_config_source`) is unchanged and still
    flags a bare `DEFAULTS[...]` written straight into a log call, because
    there the reasoning does not apply: nothing has narrowed it to "the
    constant that replaced a rejected value" yet, it is just a raw
    dictionary lookup.
    """
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "option":
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and _OPTIONS_RECEIVER.match(_unparse(func.value))
        ):
            return True
        return False
    if isinstance(node, ast.Subscript):
        return _OPTIONS_RECEIVER.match(_unparse(node.value)) is not None
    return False


def _contains_owner_read_source(expr: ast.AST) -> bool:
    return any(_is_owner_read_source(n) for n in ast.walk(expr))


_FUNC_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Positional-or-keyword parameter names, in call order. `*args`,
    `**kwargs` and keyword-only parameters are not tracked: this hop covers
    the plain call shape `clamp_interval`/`build_ssl_context` use, not a
    general call-argument binder.
    """
    return [a.arg for a in func.args.args]


def _bare_call_sites(tree: ast.AST, func_name: str) -> list[ast.Call]:
    """Every bare `func_name(...)` call in the module - not
    `self.func_name(...)` or any other attribute access, so this hop only
    ever attributes call sites to a plain module-level function.
    """
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func_name
    ]


def _config_parameters_by_call_site_agreement(
    tree: ast.AST, func: ast.FunctionDef | ast.AsyncFunctionDef
) -> set[str]:
    """The one-hop rule: a parameter of `func` counts as a configuration
    value inside `func`'s own body if *every* call site that supplies it
    does so with an expression containing a configuration read
    (`option(...)`, `DEFAULTS[...]`, an `options.get(...)`/subscript).

    A parameter is left untracked, not flagged, when: `func` has no call
    sites at all (nothing to agree on); some call site omits it and falls
    through to the parameter's own default (a default is not a
    configuration read); or it is only ever reachable through `*args`/
    `**kwargs` (out of scope - see `_param_names`).
    """
    params = _param_names(func)
    call_sites = _bare_call_sites(tree, func.name)
    if not call_sites:
        return set()
    qualifying: set[str] = set(params)
    for call in call_sites:
        supplied: dict[str, ast.expr] = {}
        for i, arg in enumerate(call.args):
            if i < len(params):
                supplied[params[i]] = arg
        for kw in call.keywords:
            if kw.arg is not None:
                supplied[kw.arg] = kw.value
        for name in list(qualifying):
            if name not in supplied or not _contains_owner_read_source(supplied[name]):
                qualifying.discard(name)
    return qualifying


def _all_config_parameters(tree: ast.AST) -> dict[str, set[str]]:
    """`{function name: {parameter names that qualify}}` for every function
    or method definition in the module, computed once before the
    per-function scan below.
    """
    result: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_DEF_TYPES):
            params = _config_parameters_by_call_site_agreement(tree, node)
            if params:
                result[node.name] = params
    return result


# A stand-in configuration-read expression, used to seed a qualifying
# parameter's entry in `_FunctionScanner._assigned` so the existing
# name-resolution and `_is_config_source` machinery treats a bare read of
# that parameter exactly like a bare read of `option(...)`, with no separate
# code path to keep in sync.
_CONFIG_PARAMETER_SENTINEL: ast.expr = ast.parse(
    "option(__companion_test_hop_sentinel__, '')", mode="eval"
).body


_SCOPE_BOUNDARY = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own_scope_statements(node: ast.AST) -> list[ast.AST]:
    """Every `Assign` and `Call` textually inside `node`'s own body, in line
    order, without crossing into a nested function, class or lambda - those
    are separate scopes and are scanned as their own units. Without this
    boundary, a variable assigned inside a nested class defined in a method
    (this file has one: `make_http_handler`'s request-handler subclass)
    would be attributed to the enclosing method's scope instead of its own,
    which could hide a violation behind an unrelated same-named variable or
    manufacture one that never reaches the log call it looks like it does.
    """
    found: list[ast.AST] = []

    def walk(n: ast.AST, at_top: bool) -> None:
        if not at_top and isinstance(n, _SCOPE_BOUNDARY):
            return  # a nested scope: scanned separately, not descended into
        if isinstance(n, (ast.Assign, ast.Call)):
            found.append(n)
        for child in ast.iter_child_nodes(n):
            walk(child, at_top=False)

    walk(node, at_top=True)
    found.sort(key=lambda n: (n.lineno, n.col_offset))
    return found


class _FunctionScanner:
    """Scans one function's own scope for logging calls that carry a
    configuration value, tracking simple `name = expr` local assignments in
    line-number order so a value read once and logged later (the real
    `ws_port` / `http_port` shape) is still found. Not a real control-flow
    analysis: a branch that reassigns a name is treated the same as one that
    runs unconditionally before it - "assigned earlier" means "earlier by
    line number", nothing more.
    """

    def __init__(
        self,
        function_name: str,
        logger_names: set[str],
        config_params: set[str] | None = None,
    ) -> None:
        self.function_name = function_name
        self.logger_names = logger_names
        self.violations: list[Violation] = []
        # Seeded, not special-cased: a qualifying parameter starts out
        # "assigned" to a stand-in configuration read, so a bare log of the
        # parameter name resolves through the same path a local variable
        # read from `option(...)` would.
        self._assigned: dict[str, ast.expr] = {
            name: _CONFIG_PARAMETER_SENTINEL for name in (config_params or set())
        }

    def scan(self, node: ast.AST) -> None:
        for stmt in _own_scope_statements(node):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    self._assigned[target.id] = stmt.value
            elif isinstance(stmt, ast.Call) and self._is_logger_call(stmt):
                self._check_call(stmt)

    def _is_logger_call(self, call: ast.Call) -> bool:
        func = call.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr in _LOGGER_LEVELS
            and isinstance(func.value, ast.Name)
            and func.value.id in self.logger_names
        )

    def _resolve(self, expr: ast.expr, hops: int = 0) -> ast.expr:
        if isinstance(expr, ast.Name) and hops < _MAX_NAME_HOPS and expr.id in self._assigned:
            return self._resolve(self._assigned[expr.id], hops + 1)
        return expr

    def _check_call(self, call: ast.Call) -> None:
        args: list[ast.expr] = list(call.args)
        args += [kw.value for kw in call.keywords]
        for arg in args:
            resolved = self._resolve(arg) if isinstance(arg, ast.Name) else arg
            if _contains_config_source(resolved):
                self.violations.append(
                    Violation(self.function_name, call.lineno, _unparse(call))
                )
                return


def find_violations(source: str) -> list[Violation]:
    tree = ast.parse(source)
    logger_names = _logger_names(tree)
    config_params = _all_config_parameters(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_DEF_TYPES):
            scanner = _FunctionScanner(
                node.name, logger_names, config_params.get(node.name)
            )
            scanner.scan(node)
            violations.extend(scanner.violations)
    return violations


# ---------------------------------------------------------------------------
# The module itself
# ---------------------------------------------------------------------------


def test_companion_module_never_logs_a_configuration_value():
    source = COMPANION_PATH.read_text(encoding="utf-8")
    violations = find_violations(source)
    assert not violations, (
        "SPEC 2.3.0: no logging call in plugin/companion.py may interpolate a "
        "configuration value - it is served to a client by get_log. Found:\n"
        + "\n".join(str(v) for v in violations)
    )


def test_gpsd_host_refusal_is_the_model_the_checker_recognises_as_clean():
    """A narrower pin than the whole-module scan above: the exact function
    SPEC.md cites as the correct shape must itself come back clean, so a
    checker that is merely too weak to find anything doesn't get credit for
    finding nothing.
    """
    source = COMPANION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "__init__"
        and _defines("gpsd_host_valid", n)
    )
    logger_names = _logger_names(tree)
    scanner = _FunctionScanner(target.name, logger_names)
    scanner.scan(target)
    assert not scanner.violations


def _defines(name: str, func: ast.FunctionDef) -> bool:
    return any(
        isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == name
        for n in ast.walk(func)
    )


# ---------------------------------------------------------------------------
# Positive controls: the checker must actually bite
# ---------------------------------------------------------------------------


def test_inline_option_call_as_a_log_argument_is_caught():
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def refuse(self):\n"
        "    log.error('bad port %r', option(self._options, 'ws_port'))\n"
    )
    violations = find_violations(source)
    assert len(violations) == 1
    assert violations[0].function == "refuse"


def test_inline_defaults_subscript_as_a_log_argument_is_caught():
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def refuse(self):\n"
        "    log.error('bad port %r', DEFAULTS['ws_port'])\n"
    )
    violations = find_violations(source)
    assert len(violations) == 1
    assert violations[0].function == "refuse"


def test_the_historic_shape_is_caught_a_local_variable_read_via_options_get():
    """The actual shape the audit found: the value is not passed inline, it
    is read into a local variable first and the local variable is what
    reaches the log call - exactly `raw_ws_port`/`raw_http_port` in
    `Listeners.__init__` before the fix.
    """
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "DEFAULTS = {'ws_port': 8082}\n"
        "class Listeners:\n"
        "    def __init__(self, options):\n"
        "        _options = options or {}\n"
        "        raw_ws_port = _options.get('ws_port', DEFAULTS['ws_port'])\n"
        "        ws_port = valid_port(raw_ws_port)\n"
        "        if ws_port is None:\n"
        "            log.error('ws_port %r is not a valid port', raw_ws_port)\n"
    )
    violations = find_violations(source)
    assert len(violations) == 1
    assert violations[0].function == "__init__"


def test_naming_the_key_without_the_value_is_not_flagged():
    """The correct shape (the `gpsd_host` refusal, and what the port
    refusals were fixed to look like): the key name is a string literal,
    the value never reaches the log call, even though the same function
    reads the value earlier for an unrelated decision.
    """
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "class GpsResolver:\n"
        "    def __init__(self, options):\n"
        "        host = option(self._options, 'gpsd_host')\n"
        "        valid = is_gpsd_host_literal(host)\n"
        "        if not valid:\n"
        "            log.error('gpsd_host is not an IPv4 or IPv6 address literal')\n"
    )
    violations = find_violations(source)
    assert not violations


def test_an_unrelated_log_call_in_a_config_reading_function_is_not_flagged():
    """A function that reads config for one purpose and logs a fixed string
    for something else entirely must not be flagged just because `option()`
    appears somewhere in its body - the check has to be about what reaches
    the log call, not about what the function happens to touch.
    """
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def start(self):\n"
        "    port = option(self._options, 'ws_port')\n"
        "    log.info('listener starting')\n"
        "    return port\n"
    )
    violations = find_violations(source)
    assert not violations


def test_a_non_logger_dot_error_call_is_not_flagged():
    """A method named `error` on some other object, or a variable that
    happens to be named `log` but was never bound to `logging.getLogger`,
    must not be mistaken for the module's logger - the check resolves the
    logger name from its assignment rather than matching on the word `log`.
    """
    source = (
        "def refuse(self):\n"
        "    log = self._build_reporter()\n"
        "    log.error('bad port %r', option(self._options, 'ws_port'))\n"
    )
    violations = find_violations(source)
    assert not violations


# ---------------------------------------------------------------------------
# The one-hop parameter case: clamp_interval / build_ssl_context's shape
# ---------------------------------------------------------------------------


def test_a_parameter_every_call_site_supplies_from_config_and_then_logged_is_caught():
    """The actual `clamp_interval` defect: the value is not read from
    config inside the function that logs it, it arrives as a parameter, but
    every call site supplies that parameter from `option(...)`, so the
    one-hop rule attributes the parameter to configuration for the rest of
    `clamp` and the bare log of it must be flagged."""
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def clamp(name, value, low, high, default):\n"
        "    numeric = float(value)\n"
        "    if numeric is None:\n"
        "        log.warning('%s is not a usable number (%r); using default %s',"
        " name, value, default)\n"
        "        return default\n"
        "    return numeric\n"
        "def on_loaded(self):\n"
        "    clamp('keepalive_interval', option(self.options, 'keepalive_interval'),"
        " 5, 20, 20)\n"
        "    clamp('rebind_interval', option(self.options, 'rebind_interval'),"
        " 5, 300, 30)\n"
    )
    violations = find_violations(source)
    assert len(violations) == 1
    assert violations[0].function == "clamp"


def test_a_parameter_some_call_site_supplies_from_a_literal_is_not_flagged():
    """The one-hop rule requires *every* call site to agree. One call
    passing a plain literal for `value` breaks that agreement, so the
    parameter is left untracked and the bare log of it is not flagged -
    correctly conservative, since `value` is genuinely not always a
    configuration read here."""
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def clamp(name, value, low, high, default):\n"
        "    numeric = float(value)\n"
        "    if numeric is None:\n"
        "        log.warning('%s is not a usable number (%r); using default %s',"
        " name, value, default)\n"
        "        return default\n"
        "    return numeric\n"
        "def on_loaded(self):\n"
        "    clamp('keepalive_interval', option(self.options, 'keepalive_interval'),"
        " 5, 20, 20)\n"
        "    clamp('fixed_probe', 42, 5, 300, 30)\n"
    )
    violations = find_violations(source)
    assert not violations


def test_a_parameter_only_reached_through_a_method_call_is_not_flagged():
    """The hop matches a bare `func_name(...)` call only, deliberately not
    `self.func_name(...)` - so a method sharing this shape is not attributed
    a call site at all and its parameter is never tracked as configuration,
    even though the argument passed is itself a configuration read."""
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "class Clamper:\n"
        "    def clamp(self, value):\n"
        "        log.warning('using %r', value)\n"
        "def on_loaded(self):\n"
        "    self._clamper.clamp(option(self.options, 'keepalive_interval'))\n"
    )
    violations = find_violations(source)
    assert not violations


def test_a_parameter_only_ever_supplied_from_defaults_is_not_flagged():
    """`clamp_interval`'s actual `default` parameter: every call site
    supplies it as `float(DEFAULTS["..."])`, a bare dictionary lookup with
    no `option(...)` involved. Logging it is the safe, required behaviour
    (SPEC 2.3.0: "using default %s"), so the hop must not treat it as
    configuration - only `option(...)`/`options.get(...)` reads qualify a
    parameter, a bare `DEFAULTS[...]` subscript on its own does not."""
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "DEFAULTS = {'keepalive_interval': 20}\n"
        "def clamp(name, default):\n"
        "    log.warning('%s fell back to %s', name, default)\n"
        "def on_loaded(self):\n"
        "    clamp('keepalive_interval', float(DEFAULTS['keepalive_interval']))\n"
    )
    violations = find_violations(source)
    assert not violations


def test_a_parameter_omitted_at_one_call_site_and_defaulted_is_not_flagged():
    """A call site that omits the parameter and falls through to its own
    default breaks agreement for that parameter - a default is not a
    configuration read, so the parameter is left untracked rather than
    flagged, even though the other call site does supply it from config."""
    source = (
        "import logging\n"
        "log = logging.getLogger(__name__)\n"
        "def clamp(name, value=None, low=5, high=20, default=20):\n"
        "    log.warning('using %r', value)\n"
        "def on_loaded(self):\n"
        "    clamp('keepalive_interval', option(self.options, 'keepalive_interval'))\n"
        "    clamp('rebind_interval')\n"
    )
    violations = find_violations(source)
    assert not violations
