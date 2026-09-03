"""`.github/workflows/release.yml`, SPEC.md 5.2 (issue #128).

Issue #128 was not a bug in a file: it was the file's absence. Everything
`SPEC.md` and `tools/install-on-pi.sh` say about a release assumed
`release.yml` existed, nothing ever checked that it did, and tagging
produced nothing for the installer to download without any gate noticing.
`test_release_workflow_exists` below is that missing gate, and it is
deliberately the first and least clever test in this file.

Two kinds of assertion live here, and they are worth different amounts:

- Textual assertions read `.github/workflows/release.yml` as YAML and check
  what the file says: its trigger, its permissions, which step exists, and
  the relative order of steps. These describe the file, not its effect, and
  they cannot run at all before the file exists. Every such test opens with
  a guard that skips, with a stated reason, when the workflow is not yet
  present, so the suite reports "not yet exercised" rather than an
  unrelated collection error.
- Behavioural assertions extract a step's actual script from the parsed
  YAML and run it for real, then inspect what actually happened - not a
  second, independently-typed copy of what the step is supposed to say.
  A textual pin (a regex over the script text) can pass for a rewrite that
  runs from the wrong directory, quotes the wrong file, or drops the one
  argument that made it correct, because a regex describes a *shape*, not
  an *effect*: the archive shape (`tar`, SPEC 5.2/5.3.1), the SHA256SUMS
  content (`sha256sum`, chained after the real "Pack dist.tgz" step so both
  run in one job workspace as they would for real), the ancestor check
  (`git merge-base --is-ancestor`, driven against a real repository built
  for the purpose, in all three of its outcomes) and the existing-release
  check (`gh api -i`, driven against a stub reproducing the real binary's
  own verified behaviour) are all exercised this way. The three-version
  agreement is tested as a property of `plugin/companion.py` and
  `frontend/package.json` as they exist in this checkout today, read
  through `.github/check_release_version.py`'s own functions (imported as
  `crv` below) rather than a second, hand-written copy of that logic - a
  copy that ships nowhere cannot catch its original drifting from it. None
  of the behavioural assertions depend on `release.yml` existing except the
  ones that extract a script from it, and all of them run unconditionally
  once it does.

Nothing here reads `.github/workflows/release.yml`'s *implementation* to
decide what the assertions should be - only SPEC.md 5.2, 5.3, 5.3.1, 2.1, 12
and 13 - so a defect in what that file ends up saying is exactly what this
module exists to catch, not something it has already agreed with by
construction. `.github/check_release_version.py` is imported directly
rather than treated the same way, following the same reasoning
`tests/tools/test_check_release_version.py` states for it: its interface is
declared pure and importable by SPEC.md 5.2 itself, so a test drives it the
way `check_pinned_facts.py` and `check_coverage.py` are already driven
elsewhere in this suite.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
RELEASE_WORKFLOW_PATH = WORKFLOWS_DIR / "release.yml"
PLUGIN_PATH = REPO_ROOT / "plugin" / "companion.py"
PACKAGE_JSON_PATH = REPO_ROOT / "frontend" / "package.json"
CHECK_RELEASE_VERSION_SCRIPT = REPO_ROOT / ".github" / "check_release_version.py"
CI_WORKFLOW_PATH = WORKFLOWS_DIR / "ci.yml"


def _load_check_release_version():
    """Imports `.github/check_release_version.py` by path, the same way
    `tests/tools/test_check_release_version.py` does. Used here only for the
    three-way version agreement, so this module compares this checkout's
    real files through the same regex and the same JSON reader the workflow
    itself calls, rather than a second copy of that logic that could drift
    from the original silently - exactly the class of defect SPEC.md 5.2's
    review found in this file (issue #151, in a fourth place).
    """
    spec = importlib.util.spec_from_file_location(
        "check_release_version", CHECK_RELEASE_VERSION_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crv = _load_check_release_version()


# ---------------------------------------------------------------------------
# The whole of issue #128: the workflow must exist. Every other test in this
# module is downstream of this one and skips, rather than errors, when it
# fails - this is the one test that must fail loudly on its own.
# ---------------------------------------------------------------------------


def test_release_workflow_exists():
    assert RELEASE_WORKFLOW_PATH.is_file(), (
        f"expected a release workflow at {RELEASE_WORKFLOW_PATH} (SPEC 5.2, issue #128); "
        "referencing this path from four other sections and installing against it is not "
        "the same as it existing"
    )


def _load_release_workflow():
    if not RELEASE_WORKFLOW_PATH.is_file():
        return None
    with open(RELEASE_WORKFLOW_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


_RELEASE_WORKFLOW = _load_release_workflow()


def _require_release_workflow():
    if _RELEASE_WORKFLOW is None:
        pytest.skip(
            "release.yml does not exist yet in this checkout; "
            "test_release_workflow_exists already reports this failure"
        )
    return _RELEASE_WORKFLOW


def _triggers(workflow):
    # PyYAML's default loader parses the unquoted `on:` key as the boolean
    # True rather than the string "on" (YAML 1.1's bareword booleans) - the
    # same idiom `tests/test_shipped_files.py` and
    # `tests/test_no_inline_script.py` already use for the same reason.
    return workflow.get("on", workflow.get(True, {})) or {}


def _jobs(workflow):
    return workflow.get("jobs", {}) or {}


def _linearize_steps(workflow):
    """Every step, across every job, in an order consistent with `needs`.

    Ordering assertions below must survive whether SPEC 5.2's checks land as
    separate jobs chained by `needs` or as steps within one job - the
    specification says nothing about which, only about the relative order of
    the checks themselves. Jobs with no `needs` relationship keep the order
    `jobs:` declares them in (Python dicts preserve insertion order, and so
    does PyYAML), which is also the only order GitHub Actions itself would
    pick when nothing forces one job to wait for the other.
    """
    jobs = _jobs(workflow)
    order = []
    visited = set()

    def visit(key):
        if key in visited:
            return
        visited.add(key)
        needs = jobs[key].get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for dependency in needs:
            if dependency in jobs:
                visit(dependency)
        order.append(key)

    for job_key in jobs:
        visit(job_key)

    steps = []
    for job_key in order:
        for step in jobs[job_key].get("steps", []) or []:
            steps.append({**step, "_job_key": job_key})
    return steps


def _script_of(step):
    return step.get("run") or ""


def _uses_of(step):
    return step.get("uses") or ""


def _find_step_index(steps, predicate):
    for index, step in enumerate(steps):
        if predicate(step):
            return index
    return None


def _is_ancestor_check(step):
    # The step this finds is the half of the release guarantee that lives in
    # the tree. The other half, that only the owner can push a v* tag at all,
    # is a tag ruleset in repository settings (SPEC 5.2, issue #181), which no
    # test here can see: a tag carrying its own release.yml runs whatever that
    # copy says, so the workflow cannot be the thing that binds the tag.
    script = _script_of(step)
    return "merge-base" in script and "is-ancestor" in script


def _is_version_check(step):
    """SPEC 5.2: 'The tag is read by a script, not by the workflow.' The
    three-way comparison used to live in this step's own `run:` block
    (matched by mentioning `__version__` and `package.json` directly); it
    now lives in `.github/check_release_version.py`, and this step's only
    job is to invoke it, so the step is identified by that invocation.
    """
    return "check_release_version.py" in _script_of(step)


def _is_build_step(step):
    # "npm run build" is the idiom `ci.yml`'s own frontend job already uses
    # (its step is literally named "Build" and runs exactly this).
    return "npm run build" in _script_of(step)


def _is_archive_step(step):
    script = _script_of(step)
    return "tar" in script and "dist.tgz" in script


def _is_sha256sums_step(step):
    script = _script_of(step)
    return "sha256sum" in script and "SHA256SUMS" in script


def _is_setup_python_step(step):
    return "setup-python" in _uses_of(step).lower()


def _is_setup_node_step(step):
    return "setup-node" in _uses_of(step).lower()


def _is_upload_artifact_step(step):
    return "upload-artifact" in _uses_of(step).lower()


def _is_download_artifact_step(step):
    return "download-artifact" in _uses_of(step).lower()


def _is_publish_step(step):
    uses = _uses_of(step).lower()
    script = _script_of(step)
    return "release" in uses or "gh release create" in script


# ---------------------------------------------------------------------------
# Trigger: SPEC 5.2 - "The trigger is a pushed tag matching v* and nothing
# else." Textual.
# ---------------------------------------------------------------------------


def test_trigger_is_a_pushed_tag_matching_v_star_and_nothing_else():
    workflow = _require_release_workflow()
    triggers = _triggers(workflow)
    assert set(triggers.keys()) == {"push"}, (
        f"expected the only trigger to be `push`, got {sorted(triggers.keys())!r}; "
        "SPEC 5.2 rules out workflow_dispatch (a second way to produce a release "
        "asset nobody can point at a commit) and, by implication, any other trigger"
    )
    push_trigger = triggers["push"] or {}
    assert set(push_trigger.keys()) == {"tags"}, (
        f"expected `push:` to carry only `tags:`, got {sorted(push_trigger.keys())!r}; "
        "a `branches:` entry here would fire the release job on an ordinary push"
    )
    tags = push_trigger.get("tags")
    if isinstance(tags, str):
        tags = [tags]
    assert tags == ["v*"], f"expected `tags: [v*]`, got {tags!r}"


def test_no_workflow_dispatch_trigger():
    workflow = _require_release_workflow()
    assert "workflow_dispatch" not in _triggers(workflow), (
        "SPEC 5.2: 'No workflow_dispatch: a release is a tag, and a second way to "
        "produce one is a second way to produce an asset nobody can point at a commit.'"
    )


def test_no_schedule_trigger():
    workflow = _require_release_workflow()
    assert "schedule" not in _triggers(workflow)


# ---------------------------------------------------------------------------
# Permissions: SPEC 5.2 - "The build and the publish are two jobs, and only
# one of them holds the token." Every job declares its own permissions
# rather than inheriting (checked separately below), and each of the three
# gets a different grant matching its own role: `checks` runs nothing from
# outside this repository and only needs to read (queries the API for an
# existing Release, reads the checkout, runs git), `build` runs untrusted
# `npm ci` lifecycle scripts and gets nothing, `publish` is the only job
# that ever calls `gh release create` and is the only one holding
# `contents: write`. Textual, identified by the step each job contains
# rather than by job key, so a rename of "checks"/"build"/"publish" does not
# stop these from finding the right job.
# ---------------------------------------------------------------------------


def _job_key_of(steps, index):
    return steps[index]["_job_key"]


def _permissions_of_job_containing(workflow, steps, index, description):
    assert index is not None, f"no {description} step found"
    job_key = _job_key_of(steps, index)
    return job_key, _jobs(workflow)[job_key].get("permissions")


def test_checks_job_is_read_only():
    """SPEC 5.2: 'it is safe to let it hold a read-only token' - the job
    containing the ancestry check runs nothing from outside this repository,
    so a compromised dependency is not a risk it needs write access against.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    job_key, permissions = _permissions_of_job_containing(
        workflow, steps, _find_step_index(steps, _is_ancestor_check), "ancestor-check"
    )
    assert permissions == {"contents": "read"}, (
        f"job {job_key!r} (containing the ancestor check): expected permissions "
        f"exactly {{'contents': 'read'}}, got {permissions!r}"
    )


def test_build_job_permissions_are_read_only():
    """SPEC 5.2: the build job's grant is `contents: read` rather than
    `permissions: {}`, deliberately - the repository is public, so `read` is
    what an anonymous clone already has, and the property actually being
    bought (a lifecycle script from the untrusted `npm ci` dependency tree
    cannot reach a token able to write) is identical either way. `{}` would
    additionally risk `actions/checkout` failing with a zero-permission
    token, undiscovered until the first real release - `read` is the version
    of "no write access" that stays exercised in ordinary CI.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    job_key, permissions = _permissions_of_job_containing(
        workflow, steps, _find_step_index(steps, _is_build_step), "build"
    )
    assert permissions == {"contents": "read"}, (
        f"job {job_key!r} (containing `npm run build`): expected permissions "
        f"exactly {{'contents': 'read'}}, got {permissions!r}"
    )


def test_publish_job_permissions_are_contents_write_and_nothing_else():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    job_key, permissions = _permissions_of_job_containing(
        workflow, steps, _find_step_index(steps, _is_publish_step), "publish"
    )
    assert permissions == {"contents": "write"}, (
        f"job {job_key!r} (containing the publish step): expected permissions "
        f"exactly {{'contents': 'write'}}, got {permissions!r} (SPEC 5.2: "
        f"'contents: write and nothing else')"
    )


def test_only_the_publish_job_holds_contents_write():
    """SPEC 5.2: 'The build and the publish are two jobs, and only one of
    them holds the token.' Checked as a property of the whole job set, not
    only of the publish job in isolation: a mutant that also grants write to
    `checks` or `build` would still pass a test that only inspects `publish`,
    and that is exactly the reachability SPEC 5.2 says the split exists to
    remove.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_job_key = _job_key_of(steps, _find_step_index(steps, _is_publish_step))
    jobs = _jobs(workflow)
    write_job_keys = [
        key
        for key, job in jobs.items()
        if (job.get("permissions") or {}).get("contents") == "write"
    ]
    assert write_job_keys == [publish_job_key], (
        f"expected only {publish_job_key!r} to hold contents: write, got "
        f"{write_job_keys!r} (SPEC 5.2: 'only one of them holds the token')"
    )


def test_every_job_declares_its_own_permissions():
    """SPEC 5.2: permissions are 'declared on the job rather than inherited.'
    Checked against every job, not only the one holding write access: a job
    with no `permissions:` block at all inherits the workflow-level
    `permissions: {}` today, which happens to be safe, but the point of
    declaring on the job is that it stays safe regardless of what the
    workflow-level default is - and an inherited value is not a declared one.
    """
    workflow = _require_release_workflow()
    jobs = _jobs(workflow)
    assert jobs, "expected at least one job in release.yml"
    for job_key, job in jobs.items():
        assert "permissions" in job, (
            f"job {job_key!r} has no permissions: block of its own; SPEC 5.2 says "
            f"permissions are declared on the job rather than inherited"
        )


def test_a_job_added_later_would_inherit_nothing():
    """SPEC 5.2: 'the workflow itself declaring permissions: {} so that a job
    added later inherits nothing rather than the repository default. The
    grant has to be written down to exist.'

    Named for what this protects, not for the YAML shape it reads: a future
    job that forgets its own `permissions:` block gets nothing, not whatever
    the repository's default happens to be, because nothing here grants it
    anything to fall back to.
    """
    workflow = _require_release_workflow()
    assert workflow.get("permissions") == {}, (
        f"expected the workflow-level permissions to be the empty mapping {{}}, "
        f"got {workflow.get('permissions')!r}; SPEC 5.2 requires this exact "
        f"declaration so a job added later inherits nothing rather than the "
        f"repository default"
    )


# ---------------------------------------------------------------------------
# needs: edges. SPEC 5.2: "The checks that decide whether a release may
# happen at all ... belong before the build, because a release that must not
# happen should cost nothing." `_linearize_steps` falls back to declaration
# order when it finds no `needs` relation between two jobs (its own
# docstring says so), which means every ordering test above would keep
# passing even if `build`'s `needs: checks` were deleted outright - the
# steps would still be listed in the same order, just no longer actually
# gated on one another when the workflow runs. This section checks the
# `needs:` graph directly, as data, rather than trusting the linearisation
# to stand in for it.
# ---------------------------------------------------------------------------


def _needs_closure(jobs, start_key):
    """Every job `start_key` transitively needs, `start_key` itself excluded."""
    closure = set()
    frontier = [start_key]
    while frontier:
        key = frontier.pop()
        needs = jobs.get(key, {}).get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for dependency in needs:
            if dependency not in closure:
                closure.add(dependency)
                frontier.append(dependency)
    return closure


def test_build_job_needs_the_checks_job():
    """SPEC 5.2: the checks 'belong before the build ... a release that must
    not happen should cost nothing.' If `build`'s `needs: checks` were
    deleted, GitHub Actions would run `build` as soon as the workflow
    started, in parallel with the refusals rather than after them - the
    ordering tests elsewhere in this module would not notice, since they
    read step order within `_linearize_steps`'s topological-or-declared
    fallback, not the `needs:` key that actually gates execution.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    jobs = _jobs(workflow)
    checks_job_key = _job_key_of(steps, _find_step_index(steps, _is_ancestor_check))
    build_job_key = _job_key_of(steps, _find_step_index(steps, _is_build_step))
    assert checks_job_key != build_job_key, (
        "expected the ancestor check and the build step to live in different jobs"
    )
    closure = _needs_closure(jobs, build_job_key)
    assert checks_job_key in closure, (
        f"job {build_job_key!r}'s needs: closure {closure!r} does not include "
        f"{checks_job_key!r}; without it the build can start before a release "
        f"that must not happen has been refused (SPEC 5.2)"
    )


def test_publish_job_needs_the_checks_job():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    jobs = _jobs(workflow)
    checks_job_key = _job_key_of(steps, _find_step_index(steps, _is_ancestor_check))
    publish_job_key = _job_key_of(steps, _find_step_index(steps, _is_publish_step))
    closure = _needs_closure(jobs, publish_job_key)
    assert checks_job_key in closure, (
        f"job {publish_job_key!r}'s needs: closure {closure!r} does not include "
        f"{checks_job_key!r}; the publish job also reads {checks_job_key!r}'s "
        f"prerelease output, which cannot resolve without the dependency either"
    )


def test_publish_job_needs_the_build_job():
    """The sibling edge `test_publish_job_needs_the_checks_job` above does
    not cover: `publish` declares `needs: [checks, build]`, and with only
    `needs: checks` the closure computed above is already `{checks}` -
    satisfying that test - while `publish` would start in parallel with
    `build` on a real run and `download-artifact` would find nothing, since
    the artefact `build` produces would not exist yet. Every ordering test
    that goes through `_linearize_steps` also misses this: its own
    declaration-order fallback still lists `build`'s steps before
    `publish`'s regardless of whether `needs:` says so, because `jobs:` in
    the YAML happens to be written in that order. Only reading the `needs:`
    closure directly catches a deleted `build` dependency.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    jobs = _jobs(workflow)
    build_job_key = _job_key_of(steps, _find_step_index(steps, _is_build_step))
    publish_job_key = _job_key_of(steps, _find_step_index(steps, _is_publish_step))
    closure = _needs_closure(jobs, publish_job_key)
    assert build_job_key in closure, (
        f"job {publish_job_key!r}'s needs: closure {closure!r} does not include "
        f"{build_job_key!r}; without it publish can start before build has "
        f"produced the artefact download-artifact expects to find"
    )


# ---------------------------------------------------------------------------
# The artifact channel between `build` and `publish`. SPEC 5.2: "Handed to
# `publish` as an artefact rather than left in the workspace ... the only
# channel between the two that does not involve giving this job the token
# instead." `actions/upload-artifact`'s `name:` and
# `actions/download-artifact`'s `name:` are plain YAML strings with no
# behaviour a subprocess could exercise; a rename of either fails only on a
# real tag, when the download step finds no artefact matching the name it
# asked for. Textual.
# ---------------------------------------------------------------------------


def test_upload_and_download_artifact_names_match():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    upload_index = _find_step_index(steps, _is_upload_artifact_step)
    download_index = _find_step_index(steps, _is_download_artifact_step)
    assert upload_index is not None, "no actions/upload-artifact step found"
    assert download_index is not None, "no actions/download-artifact step found"
    upload_name = (steps[upload_index].get("with") or {}).get("name")
    download_name = (steps[download_index].get("with") or {}).get("name")
    assert upload_name, "the upload-artifact step has no with.name"
    assert download_name, "the download-artifact step has no with.name"
    assert upload_name == download_name, (
        f"upload-artifact's name ({upload_name!r}) does not match "
        f"download-artifact's name ({download_name!r}); the publish job "
        f"would download nothing, on a real tag, from a runner nothing here "
        f"can observe"
    )


def test_upload_artifact_path_contains_both_release_assets():
    """The name: match above is not enough on its own: dropping SHA256SUMS
    from the upload step's path: passes every other test in this module,
    including the argv test, which asserts what `gh` was *called* with
    (`dist.tgz`, `SHA256SUMS` as literal arguments to `gh release create`),
    not what `download-artifact` actually places on disk for that call to
    find - a mismatch here fails with "SHA256SUMS: no such file" on a real
    tag, the exact "reaches a user rather than a runner" shape SPEC 5.2
    keeps naming, and nowhere a runner ever exercises this repository's own
    CI.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    upload_index = _find_step_index(steps, _is_upload_artifact_step)
    assert upload_index is not None, "no actions/upload-artifact step found"
    path_value = (steps[upload_index].get("with") or {}).get("path") or ""
    uploaded_names = {line.strip() for line in path_value.splitlines() if line.strip()}
    assert "dist.tgz" in uploaded_names, (
        f"expected dist.tgz in the upload-artifact path:, got {uploaded_names!r}"
    )
    assert "SHA256SUMS" in uploaded_names, (
        f"expected SHA256SUMS in the upload-artifact path:, got {uploaded_names!r}"
    )


def test_upload_artifact_path_matches_what_the_publish_step_actually_creates_the_release_with(
    tmp_path,
):
    """Cross-checked against a real run of the publish step, not against a
    one-directional textual containment check: the set of files
    `upload-artifact`'s `path:` names (read from the YAML) must **equal**
    the set of asset filenames `gh release create` was actually called
    with, extracted and run for real with a stub `gh` recording its own
    argv - the same machinery
    `test_publish_step_includes_prerelease_flag_only_when_prerelease_is_true`
    uses, not a second, weaker mechanism. Set equality both ways: dropping
    `SHA256SUMS` from `path:` must fail this (the earlier version of this
    test only checked that every `path:` name appeared somewhere in the
    publish step's script text, which a real run was never involved in and
    which stayed true regardless of what `path:` actually named), and so
    must the publish step naming an asset `upload-artifact` never uploads.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    upload_index = _find_step_index(steps, _is_upload_artifact_step)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert upload_index is not None, "no actions/upload-artifact step found"
    assert publish_index is not None, "no release-publish step found"
    path_value = (steps[upload_index].get("with") or {}).get("path") or ""
    uploaded_names = {line.strip() for line in path_value.splitlines() if line.strip()}

    step = steps[publish_index]
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    marker = tmp_path / "gh-argv"
    _write_gh_argv_recording_stub(stub_dir, marker)
    argv = _run_publish_step_and_capture_argv(step, tmp_path, stub_dir, marker, prerelease="false")
    published_names = _asset_names_from_publish_argv(argv)

    assert published_names == uploaded_names, (
        f"upload-artifact's path: names {uploaded_names!r}, but the publish "
        f"step's real gh release create invocation named {published_names!r} "
        f"as assets; one file gets uploaded across the job boundary and a "
        f"different one gets published from whatever download-artifact found"
    )


def test_upload_artifact_step_precedes_download_artifact_step():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    upload_index = _find_step_index(steps, _is_upload_artifact_step)
    download_index = _find_step_index(steps, _is_download_artifact_step)
    assert upload_index is not None, "no actions/upload-artifact step found"
    assert download_index is not None, "no actions/download-artifact step found"
    assert upload_index < download_index, (
        f"upload-artifact (index {upload_index}) must run before "
        f"download-artifact (index {download_index})"
    )


# ---------------------------------------------------------------------------
# Toolchain versions: SPEC 5.2 - "release.yml declares its own PYTHON_VERSION
# and NODE_VERSION because a workflow cannot read another's env, so the
# numbers exist in two files and a test compares them. Two spellings of one
# version with nothing comparing them is the defect issue #151 was about,
# arriving in a fourth place." Textual: both are read straight out of the two
# workflows' own `env:` blocks, not typed fresh here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["PYTHON_VERSION", "NODE_VERSION"])
def test_toolchain_version_matches_ci_yml(key):
    assert CI_WORKFLOW_PATH.is_file(), f"expected {CI_WORKFLOW_PATH} to exist for comparison"
    workflow = _require_release_workflow()
    with open(CI_WORKFLOW_PATH, encoding="utf-8") as handle:
        ci_workflow = yaml.safe_load(handle)

    release_value = (workflow.get("env") or {}).get(key)
    ci_value = (ci_workflow.get("env") or {}).get(key)
    assert release_value is not None, f"release.yml has no env.{key}"
    assert ci_value is not None, f"ci.yml has no env.{key}"
    assert release_value == ci_value, (
        f"release.yml's env.{key} ({release_value!r}) disagrees with ci.yml's "
        f"({ci_value!r}); a workflow cannot read another's env, so nothing but a "
        f"test catches these drifting apart (SPEC 5.2, issue #151)"
    )


def test_setup_python_step_reads_env_python_version():
    """The toolchain-comparison test above pins that release.yml's
    env.PYTHON_VERSION agrees with ci.yml's, but nothing established that
    anything in release.yml actually reads env.PYTHON_VERSION at all - a
    workflow could compare two numbers correctly and still hard-code a
    third somewhere else. Pinned here: the setup-python step's own
    with.python-version must reference the env var this module already
    checked, not a literal.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_setup_python_step)
    assert index is not None, "no actions/setup-python step found"
    python_version = (steps[index].get("with") or {}).get("python-version")
    assert python_version is not None, "the setup-python step has no with.python-version"
    assert "env.PYTHON_VERSION" in str(python_version), (
        f"expected with.python-version to read env.PYTHON_VERSION, got "
        f"{python_version!r}"
    )


def test_setup_node_step_reads_env_node_version():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_setup_node_step)
    assert index is not None, "no actions/setup-node step found"
    node_version = (steps[index].get("with") or {}).get("node-version")
    assert node_version is not None, "the setup-node step has no with.node-version"
    assert "env.NODE_VERSION" in str(node_version), (
        f"expected with.node-version to read env.NODE_VERSION, got {node_version!r}"
    )


def test_build_job_caches_nothing():
    """SPEC 5.2: 'The build job caches nothing. A dependency cache is
    restored from a key any branch of this repository can populate, which
    is a write-influenced input to the one job the split exists to
    isolate.' A rule stated in the specification with nothing enforcing it
    is exactly issue #128's own shape - re-adding cache: npm to this job's
    setup-node step would fail nothing today; this is the gate.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    build_job_key = _job_key_of(steps, _find_step_index(steps, _is_build_step))
    setup_node_index = _find_step_index(
        steps, lambda s: _is_setup_node_step(s) and s["_job_key"] == build_job_key
    )
    assert setup_node_index is not None, (
        f"expected an actions/setup-node step in job {build_job_key!r}"
    )
    cache = (steps[setup_node_index].get("with") or {}).get("cache")
    assert cache is None, (
        f"expected no with.cache on the build job's setup-node step, got "
        f"{cache!r} (SPEC 5.2: 'The build job caches nothing')"
    )

    # setup-node's own with.cache is one way to cache; a bare
    # actions/cache step alongside it is another, and this test's name
    # promises the whole job, not only that one field.
    cache_action_steps = [
        s
        for s in steps
        if s["_job_key"] == build_job_key and "actions/cache" in _uses_of(s).lower()
    ]
    assert not cache_action_steps, (
        f"expected no actions/cache step in job {build_job_key!r}, found "
        f"{[s.get('name') for s in cache_action_steps]!r} (SPEC 5.2: 'The "
        f"build job caches nothing')"
    )


# ---------------------------------------------------------------------------
# Node floor: SPEC 5.1.1, issue #132 - frontend/package.json declares
# engines.node, and ci.yml and release.yml each declare their own
# NODE_VERSION because a workflow cannot read another's env. Three
# spellings of one constraint. The test above already pins that ci.yml's
# and release.yml's NODE_VERSION agree with each other; the tests below pin
# the third spelling, engines.node, and that its major agrees with both
# pins, plus frontend/.npmrc's engine-strict switch that turns EBADENGINE
# from a warning into a refusal.
#
# What is deliberately not asserted here: SPEC 5.1.1 is explicit that
# NODE_VERSION is a major alone - "the latest of that major the runner can
# get" - while engines.node is a lower bound with three components. Nothing
# here or anywhere else in this checkout can establish that the runner's
# resolved version is at or above the floor's minor without knowing what
# actions/setup-node resolved at the moment the job ran. Asserting that from
# a static checkout would be checking something the spec itself says the
# check cannot check.
#
# SPEC 5.1.1 also says the floor's digits belong in engines.node "and
# nowhere else, including here": no comment or assertion message below
# repeats them. Where a message needs to show a value, it interpolates the
# one the test just read, never a literal typed here.
# ---------------------------------------------------------------------------


def _node_engine_range():
    with open(PACKAGE_JSON_PATH, encoding="utf-8") as handle:
        package = json.load(handle)
    return (package.get("engines") or {}).get("node")


def _node_floor_major(node_range):
    """Reads the floor out of a `>=X.Y.Z`-shaped engines.node range. SPEC
    5.1.1 calls engines.node "a lower bound with three components,
    >=X.Y.Z", so this is deliberately narrow rather than a general
    semver-range parser: a range that is not a single '>=' floor is itself
    a spec violation this function surfaces by returning None rather than
    by guessing.
    """
    if not node_range:
        return None
    match = re.match(r"^>=(\d+)\.(\d+)\.(\d+)$", node_range.strip())
    if not match:
        return None
    return match.group(1)


def test_node_floor_is_a_range_with_a_floor():
    """SPEC 5.1.1 calls engines.node "a lower bound with three components,
    >=X.Y.Z" - not a bare major and not an exact pin, which would forbid
    every newer patch the floor is meant to permit. A missing or empty
    engines.node fails here too, rather than needing a separate "declares a
    floor at all" test: _node_floor_major(None) and
    _node_floor_major("") both return None the same way an unparseable
    range does.
    """
    node_range = _node_engine_range()
    major = _node_floor_major(node_range)
    assert major is not None, (
        f"expected frontend/package.json's engines.node to be a '>=X.Y.Z' floor "
        f"(SPEC 5.1.1: a lower bound with three components), got {node_range!r}"
    )


@pytest.mark.parametrize(
    "workflow_path", [CI_WORKFLOW_PATH, RELEASE_WORKFLOW_PATH], ids=["ci.yml", "release.yml"]
)
def test_node_version_pin_matches_package_json_floor_major(workflow_path):
    """SPEC 5.1.1: "the check is that each workflow's pinned major is the
    floor's major, asserted against package.json separately for each of
    the two rather than only between them -- two pins that drift to the
    same wrong major still agree with each other, which is the one case
    comparing them to each other cannot see." Checked against each
    workflow independently, for exactly that reason, rather than only
    against the other workflow the way the toolchain-version comparison
    above already does.
    """
    if workflow_path is RELEASE_WORKFLOW_PATH:
        _require_release_workflow()
    with open(workflow_path, encoding="utf-8") as handle:
        workflow = yaml.safe_load(handle)
    node_version = (workflow.get("env") or {}).get("NODE_VERSION")
    assert node_version is not None, f"{workflow_path} has no env.NODE_VERSION"

    floor_major = _node_floor_major(_node_engine_range())
    assert floor_major is not None, (
        "frontend/package.json's engines.node is not a '>=X.Y.Z' floor; "
        "test_node_floor_is_a_range_with_a_floor already reports this"
    )
    assert str(node_version) == floor_major, (
        f"{workflow_path}'s env.NODE_VERSION ({node_version!r}) is not the same major as "
        f"frontend/package.json's engines.node floor ({floor_major!r}); SPEC 5.1.1 requires "
        f"each workflow's pinned major to equal the floor's major"
    )


def test_npmrc_sets_engine_strict():
    """SPEC 5.1.1: 'frontend/.npmrc sets engine-strict=true, so npm install
    refuses rather than warning.' Parsed as .npmrc's own key=value lines
    (ignoring comments and blank lines) rather than a raw substring search,
    so a value of engine-strict=false, or the key inside a comment, is not
    mistaken for the switch actually being on - which is the whole
    acceptance criterion issue #132 asks for ('learns that from the
    documentation or from the install, not from a jsdom internal').
    """
    npmrc_path = REPO_ROOT / "frontend" / ".npmrc"
    assert npmrc_path.is_file(), f"expected {npmrc_path} to exist (SPEC 5.1.1)"
    settings = {}
    with open(npmrc_path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            settings[key.strip()] = value.strip()
    # The message names this one key and never the parsed file: a failure here
    # is echoed into a public CI log, and an .npmrc is where npm writes a
    # registry credential if anybody ever runs `npm config set` against it.
    assert settings.get("engine-strict") == "true", (
        f"expected frontend/.npmrc's engine-strict setting to be 'true', "
        f"got {settings.get('engine-strict')!r} (SPEC 5.1.1)"
    )


# ---------------------------------------------------------------------------
# Ancestor check: SPEC 5.2 - "The tag must be an ancestor of master ... The
# job fails, loudly, naming the commit," and the checkout must fetch enough
# history to answer that question (SPEC 5.2, SPEC 13's "an unanswerable
# check that passes is exactly what it forbids"). Textual, plus a real git
# repository below for the check's own three outcomes.
#
# What this section deliberately does not test: SPEC 5.2's "what that check
# is, and what it is not" paragraph - a tag pushed to a commit whose own
# `release.yml` carries no ancestry step runs no ancestry step, since GitHub
# loads the workflow from the tagged ref. That is a property of which
# workflow file *executes* for a given tag on GitHub's own infrastructure,
# which nothing running inside this checkout can observe or drive; closing
# it needs a tag protection ruleset in repository settings, tracked as
# issue #181. The tests below cover the in-repository half - whether the
# check refuses correctly, given that it runs at all - and that is
# deliberately the only half tested here.
# ---------------------------------------------------------------------------


def test_checkout_does_not_use_a_shallow_clone():
    """`actions/checkout` defaults to `fetch-depth: 1`. `git merge-base
    --is-ancestor` needs the tagged commit's and master's full history in
    the local clone to answer honestly; a shallow clone can only report
    'not found', which is indistinguishable from 'not an ancestor' and is
    exactly the unanswerable-check-that-passes SPEC 13 forbids.

    Reads the assertion as `fetch-depth: 0` (the standard "full history"
    value) rather than merely "not 1", since a depth deep enough by
    accident today is not the same guarantee as one that cannot run out.

    Checked only against the checkout in the ancestor check's own job: since
    the job split (SPEC 5.2, "the build and the publish are two jobs"), the
    `build` job also checks out the repository - to build the frontend, not
    to answer an ancestry question - and has no reason to pay for full
    history it never reads. Requiring `fetch-depth: 0` there too would pin
    an accident of the current file rather than the rule this test exists
    to protect.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    ancestor_index = _find_step_index(steps, _is_ancestor_check)
    assert ancestor_index is not None, "no ancestor-check step found"
    ancestor_job_key = _job_key_of(steps, ancestor_index)
    checkout_steps = [
        s
        for s in steps
        if "checkout" in _uses_of(s).lower() and s["_job_key"] == ancestor_job_key
    ]
    assert checkout_steps, (
        f"expected a checkout step in job {ancestor_job_key!r}, the job containing "
        f"the ancestor check"
    )
    for step in checkout_steps:
        fetch_depth = (step.get("with") or {}).get("fetch-depth")
        assert fetch_depth == 0, (
            f"checkout step {step.get('name')!r} in job {ancestor_job_key!r}: "
            f"expected `fetch-depth: 0` (full history), got {fetch_depth!r}; a "
            "shallow clone cannot answer `git merge-base --is-ancestor` "
            "(SPEC 5.2, SPEC 13)"
        )


def test_checkout_does_not_persist_credentials():
    """SPEC 5.2: 'actions/checkout persists the GITHUB_TOKEN into
    .git/config by default, and this job then runs npm ci, which executes
    lifecycle scripts from the whole transitive dependency tree in that
    same directory - in a job holding contents: write on the repository
    that publishes the artefact people install. One compromised transitive
    package would be enough to push or to publish.'

    Named for what it protects - a compromised `npm ci` lifecycle script
    being unable to push or publish - rather than for the YAML shape it
    reads, so this test keeps meaning the same thing if the checkout step
    is ever restructured around it.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    checkout_steps = [s for s in steps if "checkout" in _uses_of(s).lower()]
    assert checkout_steps, "expected at least one actions/checkout step in release.yml"
    for step in checkout_steps:
        persist_credentials = (step.get("with") or {}).get("persist-credentials")
        assert persist_credentials is False, (
            f"checkout step {step.get('name')!r}: expected `persist-credentials: "
            f"false`, got {persist_credentials!r}; a compromised transitive "
            f"dependency's npm lifecycle script must not find a pushable "
            f"credential sitting in .git/config (SPEC 5.2)"
        )


def test_ancestor_check_step_exists():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_ancestor_check)
    assert index is not None, (
        "expected a step whose script checks `git merge-base --is-ancestor` "
        "against master (SPEC 5.2: 'The tag must be an ancestor of master')"
    )


def test_ancestor_check_precedes_publish():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    ancestor_index = _find_step_index(steps, _is_ancestor_check)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert ancestor_index is not None, "no ancestor-check step found"
    assert publish_index is not None, "no release-publish step found"
    assert ancestor_index <= publish_index, (
        "the ancestor check must run before the release is published, or a tag on "
        "a commit that never reached master can still produce a published release "
        "(SPEC 5.2)"
    )


def _git(cwd, *args, check=True):
    # -c commit.gpgsign=false: this host's global git config has
    # commit.gpgsign=true (SPEC.md 13.1, "every commit authored by the
    # owner is GPG-signed"), which a throwaway commit in a scratch
    # repository built for a test has no business inheriting - on a
    # machine without the owner's key, or behind a pinentry prompt, that
    # would fail or hang this test and blame git rather than the actual
    # cause. Passed on every invocation rather than only on `commit`: a
    # `git` alias or config setting affecting some other subcommand the
    # same way is no more wanted here than this one is.
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=check,
    )


def _annotation_lines(result):
    """The `::error::`-prefixed lines a subprocess result printed - the
    GitHub Actions annotation an operator actually reads, not the combined
    stdout+stderr text.

    Comparing raw combined output between two failing scenarios is not
    reliable: `git merge-base` writes its own diagnostic straight to
    stderr regardless of what the wrapping script does (a `fatal: Not a
    valid object name origin/master` for an unresolvable ref, nothing at
    all for a clean "not an ancestor" exit), so two scenarios can produce
    different raw text purely from that incidental leakage, passing an
    equality check that never exercised the script's own reporting at all.
    Restricting the comparison to `::error::` lines - the message the
    script itself chose to emit - is what actually pins SPEC 5.2's
    'must not be reported as the same thing.'
    """
    combined = result.stdout + result.stderr
    return [line for line in combined.splitlines() if line.startswith("::error::")]


def test_ancestor_check_step_refuses_correctly_in_all_three_git_outcomes(tmp_path):
    """SPEC 5.2: 'git merge-base --is-ancestor exits 1 for "not an ancestor"
    and 128 for a question it could not answer - an unresolvable
    origin/master, a missing object. Both must refuse the release, per
    SPEC 13, and they must not be reported as the same thing.'

    Driven with no runner and no stub: the workflow's own "Tag is on
    master" step is extracted from the YAML and run for real against a
    real git repository built for the purpose, in three configurations - a
    commit that IS an ancestor of master (an already-merged tag), a commit
    on a side branch that never merged into master, and an unresolvable
    `origin/master` (no fetch ever happened, SPEC's own example) - asserting
    the exit status each way and, for the two refusal cases, that the
    *content* of what they report differs in the way SPEC 5.2 specifically
    requires, not merely that it differs. An `!=` check alone passes a
    mutant that swaps the two case-arms' bodies wholesale: the messages
    would still differ from each other, only now attached to the wrong
    scenario, reporting "could not determine" for a real side-branch tag and
    "is not an ancestor" for a ref the check never managed to resolve.
    Checking which specific wording appears in which scenario is what a
    body-swap cannot survive. There is no "Fetch master" step to extract
    separately any more: `actions/checkout`'s own `fetch-depth: 0` is what
    populates `origin/master` in the real job, so this test performs the
    equivalent setup directly with `git fetch` rather than pretending a step
    that no longer exists still does it.
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=master", str(origin))

    work = tmp_path / "work"
    _git(tmp_path, "init", "--initial-branch=master", str(work))
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "remote", "add", "origin", str(origin))

    _git(work, "commit", "--allow-empty", "-m", "base")
    base_sha = _git(work, "rev-parse", "HEAD").stdout.strip()
    _git(work, "commit", "--allow-empty", "-m", "head")
    _git(work, "push", "origin", "master")

    _git(work, "checkout", "-b", "side", base_sha)
    _git(work, "commit", "--allow-empty", "-m", "side")
    side_sha = _git(work, "rev-parse", "HEAD").stdout.strip()
    _git(work, "checkout", "master")

    # The equivalent of what actions/checkout's fetch-depth: 0 does in the
    # real job: make origin/master resolvable in this working copy.
    _git(work, "fetch", "origin", "master")

    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    ancestor_index = _find_step_index(steps, _is_ancestor_check)
    assert ancestor_index is not None, "no ancestor-check step found"
    ancestor_step = steps[ancestor_index]

    def run_ancestor_check(cwd, sha):
        env = {"GITHUB_SHA": sha, "GITHUB_REF_NAME": "v9.9.9"}
        return _run_step_script(ancestor_step, cwd, env=env)

    ancestor_result = run_ancestor_check(work, base_sha)
    assert ancestor_result.returncode == 0, (
        f"a commit that IS an ancestor of master must succeed: "
        f"stdout={ancestor_result.stdout!r} stderr={ancestor_result.stderr!r}"
    )

    not_ancestor_result = run_ancestor_check(work, side_sha)
    assert not_ancestor_result.returncode != 0, (
        "a commit on a side branch that never merged into master must refuse"
    )

    # Unresolvable origin/master: a second repository with the same origin
    # configured but never fetched, so refs/remotes/origin/master does not
    # exist locally - SPEC's own example of "an unresolvable origin/master".
    work2 = tmp_path / "work2"
    _git(tmp_path, "init", "--initial-branch=master", str(work2))
    _git(work2, "config", "user.email", "test@example.com")
    _git(work2, "config", "user.name", "Test")
    _git(work2, "remote", "add", "origin", str(origin))
    _git(work2, "commit", "--allow-empty", "-m", "unrelated")
    unresolvable_sha = _git(work2, "rev-parse", "HEAD").stdout.strip()

    # Sanity check on the scaffolding itself: origin/master really is
    # unresolvable here, not merely untested - or this case proves nothing
    # about the rule it exists to check.
    resolve = _git(work2, "rev-parse", "--verify", "origin/master", check=False)
    assert resolve.returncode != 0, (
        "sanity check: origin/master must be genuinely unresolvable in this "
        "second repository, or the unresolvable-ref case below tests nothing"
    )

    unresolvable_result = run_ancestor_check(work2, unresolvable_sha)
    assert unresolvable_result.returncode != 0, (
        "an unresolvable origin/master must refuse the release, not be read as "
        "'not an ancestor' (SPEC 5.2, SPEC 13)"
    )

    # Normalized against each scenario's own commit SHA before comparing:
    # `side_sha` and `unresolvable_sha` are different commits by construction
    # (there is no way to make two distinct git objects share a hash), so the
    # message text would always differ by that embedded value alone even if
    # the script reported both cases identically otherwise - which is
    # precisely the accidental difference that would make this assertion
    # pass without the script actually distinguishing anything.
    not_ancestor_annotations = [
        line.replace(side_sha, "<SHA>") for line in _annotation_lines(not_ancestor_result)
    ]
    unresolvable_annotations = [
        line.replace(unresolvable_sha, "<SHA>") for line in _annotation_lines(unresolvable_result)
    ]
    assert not_ancestor_annotations, "expected the not-ancestor case to print a ::error:: line"
    assert unresolvable_annotations, "expected the unresolvable case to print a ::error:: line"

    # Content, not merely inequality: a mutant that swaps the 1) and *) arms
    # wholesale still produces two different-looking annotation sets (each
    # scenario still gets *a* message, just the wrong one), so `!=` alone
    # does not catch it. Pinning which specific wording belongs to which
    # scenario does.
    not_ancestor_text = "\n".join(not_ancestor_annotations)
    unresolvable_text = "\n".join(unresolvable_annotations)
    assert "is not an ancestor" in not_ancestor_text, (
        f"expected the real side-branch case to say the commit is not an "
        f"ancestor, got {not_ancestor_annotations!r}"
    )
    assert "could not determine" not in not_ancestor_text, (
        f"the real side-branch case must not be reported as unanswerable, "
        f"got {not_ancestor_annotations!r}"
    )
    assert "could not determine" in unresolvable_text, (
        f"expected the unresolvable-ref case to say the question could not "
        f"be answered, got {unresolvable_annotations!r}"
    )
    assert "is not an ancestor" not in unresolvable_text, (
        f"the unresolvable-ref case must not be reported as though it were a "
        f"provable 'not an ancestor', got {unresolvable_annotations!r}"
    )


# ---------------------------------------------------------------------------
# The high-value one: version agreement, and specifically its ORDER.
# SPEC 5.2 - "any disagreement fails the job before anything is built or
# published." Textual (locating the check) + ordering (the part that would
# survive a rewrite).
# ---------------------------------------------------------------------------


def test_version_check_step_exists():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_version_check)
    assert index is not None, (
        "expected a step that runs .github/check_release_version.py, the tag, "
        "the plugin and the frontend versions (SPEC 5.2, SPEC 2.1)"
    )


def test_version_check_step_has_id_version():
    """The publish step reads `steps.version.outputs.prerelease` (SPEC 5.2:
    'the pre-release decision is made once' - a rename of this step's `id`
    silently breaks that reference at the YAML-expression level, with no
    parse error, since a nonexistent `steps.<id>` context just evaluates
    empty. Pinned by name since nothing else would catch that rename.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_version_check)
    assert index is not None, "no version-check step found"
    assert steps[index].get("id") == "version", (
        f"expected the version-check step's id to be 'version', got "
        f"{steps[index].get('id')!r}"
    )


def test_setup_python_precedes_the_version_check():
    """`check_release_version.py` runs as `python .github/check_release_version.py`
    (SPEC 5.2), which needs a Python interpreter on the runner before it can
    be invoked - `ubuntu-latest` is not guaranteed to carry the version this
    project targets without `actions/setup-python` asking for it explicitly.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    version_index = _find_step_index(steps, _is_version_check)
    setup_python_index = _find_step_index(steps, _is_setup_python_step)
    assert version_index is not None, "no version-check step found"
    assert setup_python_index is not None, "no actions/setup-python step found"
    assert setup_python_index < version_index, (
        "actions/setup-python must run before the step that invokes "
        "check_release_version.py with `python`"
    )


def test_version_check_precedes_the_build():
    """No "combined step" fallback here: `_is_version_check` matches the
    step that invokes `check_release_version.py` (SPEC 5.2: "the tag is
    read by a script, not by the workflow"), which cannot also be the
    `npm run build` step this codebase's one-step-one-job convention would
    ever merge them into - a line-order fallback for that case would search
    for `__version__` text the version step no longer contains, following
    a shape the version check stopped having once it moved into a script.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    version_index = _find_step_index(steps, _is_version_check)
    build_index = _find_step_index(steps, _is_build_step)
    assert version_index is not None, "no version-check step found"
    assert build_index is not None, "no `npm run build` step found"
    assert version_index != build_index, (
        "the version-check step and the build step must be distinct steps"
    )
    assert version_index < build_index, (
        f"the version-check step (index {version_index}) must run before the "
        f"build step (index {build_index}); SPEC 5.2: 'any disagreement fails "
        "the job before anything is built or published'"
    )


def test_version_check_precedes_the_publish():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    version_index = _find_step_index(steps, _is_version_check)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert version_index is not None, "no version-check step found"
    assert publish_index is not None, "no release-publish step found"
    assert version_index != publish_index, (
        "the version-check step and the publish step must be distinct steps"
    )
    assert version_index < publish_index, (
        f"the version-check step (index {version_index}) must run before the "
        f"publish step (index {publish_index}); a mismatch must not be able to "
        "produce a half-finished release (SPEC 5.2)"
    )


def test_version_check_precedes_the_ancestor_check():
    """SPEC 5.2: 'The order of the refusals is part of the rule. The grammar
    is checked first, then the versions, then the ancestry, then the
    existing Release.' Two of the three edges in that chain live here and
    in the test below, the same shape as the version-before-build/publish
    tests above but pinning the order *among* the refusals themselves, not
    just that they all precede the build.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    version_index = _find_step_index(steps, _is_version_check)
    ancestor_index = _find_step_index(steps, _is_ancestor_check)
    assert version_index is not None, "no version-check step found"
    assert ancestor_index is not None, "no ancestor-check step found"
    assert version_index < ancestor_index, (
        f"the version-check step (index {version_index}) must run before the "
        f"ancestor check (index {ancestor_index}); SPEC 5.2: 'the grammar is "
        "checked first, then the versions, then the ancestry'"
    )


def test_ancestor_check_precedes_the_existing_release_check():
    """SPEC 5.2: the existing-Release check runs last among the refusals
    because it 'interpolates the tag into a URL before anything has decided
    the tag is a version', which is a step trusting a value nobody has
    looked at - so it must run after both the version check (already
    covered by the ancestor-check's own ordering above) and the ancestor
    check specifically.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    ancestor_index = _find_step_index(steps, _is_ancestor_check)
    existing_release_index = _find_step_index(steps, _is_existing_release_check)
    assert ancestor_index is not None, "no ancestor-check step found"
    assert existing_release_index is not None, "no existing-release check step found"
    assert ancestor_index < existing_release_index, (
        f"the ancestor check (index {ancestor_index}) must run before the "
        f"existing-release check (index {existing_release_index}); SPEC 5.2: "
        "'then the ancestry, then the existing Release'"
    )


# ---------------------------------------------------------------------------
# Pre-release marking. SPEC 5.2 - "A tag carrying a pre-release suffix marks
# the Release as a pre-release," and "the pre-release decision is made once":
# the workflow no longer decides this itself, `check_release_version.py`'s
# `is_prerelease` does, and that function is driven directly - table-driven,
# including an inverted-condition mutant that killed it - by
# `tests/tools/test_check_release_version.py::test_is_prerelease`. This
# module used to carry a heuristic textual test here
# (`test_publish_step_text_ties_prerelease_status_to_the_tag`, matching
# "case"/"if" and "$tag" and "prerelease" as loose substrings near each
# other) that could tell the concept was wired to the tag but never whether
# the condition's polarity was right - the exact gap the inverted-condition
# mutant (M11) walked through. It is removed rather than kept alongside the
# behavioural test: SPEC 5.2's own new paragraph is the argument for why, and
# keeping both would suggest the textual one still adds coverage it doesn't.
# What remains here is only the wiring: that the workflow actually reads the
# script's answer instead of re-deriving it.
# ---------------------------------------------------------------------------


def test_publish_step_reads_the_version_steps_prerelease_output():
    """SPEC 5.2: 'the pre-release decision is made once' - the workflow must
    read the version-check step's `prerelease` output rather than compute
    its own condition against the tag a second time.

    Handles both a same-job and a cross-job reference, since the job split
    (SPEC 5.2, "the build and the publish are two jobs") puts the version
    check in a different job from the publish step in the current file:
    `steps.<id>.outputs.<name>` only resolves within the job that ran the
    step, so a value read across a job boundary has to travel through that
    step's own job declaring a job-level `outputs:` entry re-exposing it,
    and the reading job's `needs:` naming the job it is reading from - only
    then does `needs.<job>.outputs.<name>` in the reading step actually
    resolve to anything. All three links are checked, not just the last
    one, since a broken middle link (a job output the step's own job never
    declares) fails silently at the point nothing reads it, not here.

    Checked against the step's `env:` block as well as its `run:` script:
    SPEC 5.2's interpolation rule ('nothing reaches a run: block by
    expression interpolation ... values arrive through env: and are read as
    variables') means the reference legitimately lives in `env:` rather than
    inline in the shell, and a test that only reads `run:` would not find it
    there and would wrongly conclude the wiring was gone.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    version_index = _find_step_index(steps, _is_version_check)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert version_index is not None, "no version-check step found"
    assert publish_index is not None, "no release-publish step found"
    version_step = steps[version_index]
    publish_step = steps[publish_index]
    version_id = version_step.get("id")
    assert version_id, "the version-check step has no `id`, so its output cannot be referenced"

    version_job_key = _job_key_of(steps, version_index)
    publish_job_key = _job_key_of(steps, publish_index)
    haystack = _script_of(publish_step) + "\n" + str(publish_step.get("env") or "")

    if version_job_key == publish_job_key:
        expected_reference = f"steps.{version_id}.outputs.prerelease"
        assert expected_reference in haystack, (
            f"expected the publish step to read {expected_reference}, not to "
            f"re-derive the pre-release decision itself; got "
            f"env={publish_step.get('env')!r} script:\n{_script_of(publish_step)}"
        )
    else:
        version_job = _jobs(workflow)[version_job_key]
        job_outputs = version_job.get("outputs") or {}
        assert "prerelease" in job_outputs, (
            f"job {version_job_key!r} declares no outputs.prerelease; without it "
            f"needs.{version_job_key}.outputs.prerelease can never resolve across "
            f"the job boundary, whatever the publish step reads"
        )
        assert f"steps.{version_id}.outputs.prerelease" in str(job_outputs["prerelease"]), (
            f"expected job {version_job_key!r}'s outputs.prerelease to re-expose "
            f"steps.{version_id}.outputs.prerelease, got {job_outputs['prerelease']!r}"
        )
        publish_job = _jobs(workflow)[publish_job_key]
        needs = publish_job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        assert version_job_key in needs, (
            f"job {publish_job_key!r} has no needs: naming {version_job_key!r}; "
            f"needs.{version_job_key}.outputs.prerelease does not resolve to a "
            f"job this one never declared a dependency on"
        )
        expected_reference = f"needs.{version_job_key}.outputs.prerelease"
        assert expected_reference in haystack, (
            f"expected the publish step to read {expected_reference}, not to "
            f"re-derive the pre-release decision itself; got "
            f"env={publish_step.get('env')!r} script:\n{_script_of(publish_step)}"
        )

    assert not re.search(r"\bcase\b", _script_of(publish_step)), (
        "the publish step must not contain its own case statement deciding "
        "pre-release status - that logic belongs in check_release_version.py, "
        "where a test can drive it directly (SPEC 5.2)"
    )


def test_publish_step_does_not_interpolate_the_prerelease_expression_into_the_shell():
    """SPEC 5.2: '${{ }} inside a shell body is substituted before the shell
    sees it, so a value containing shell syntax becomes shell ... That is
    true even of values this repository generates itself.' The prerelease
    flag must be read as a shell variable (`$PRERELEASE` or similar, via
    `env:`), never spliced as `${{ steps.<id>.outputs.prerelease }}`
    directly inside the `run:` text - the harmless case today
    (`check_release_version.py` only ever writes `true` or `false`) is not
    a safety property of this file, since nothing here enforces what the
    other file writes.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert publish_index is not None, "no release-publish step found"
    run_script = _script_of(steps[publish_index])
    assert "${{" not in run_script, (
        f"expected no `${{{{ }}}}` expression interpolation inside the publish "
        f"step's `run:` block; found some in:\n{run_script}"
    )


def _is_existing_release_check(step):
    # Matches "already exists" (today's `gh api -i` implementation) and the
    # earlier "gh release view" shape this step used before SPEC 5.2's HTTP
    # status distinction landed, so a predicate written against one
    # implementation does not stop finding the step under the other.
    script = _script_of(step)
    return "gh release view" in script or "already exists" in script.lower()


def test_existing_release_check_step_exists_and_precedes_publish():
    """SPEC 5.2 - 'An existing Release for the tag is a failure, not
    something to overwrite. ... The job fails, loudly, naming the commit'
    (the sibling ancestor-check rule uses the same wording; this rule gets
    the same treatment). A dedicated pre-check is one legitimate way to get
    this - `gh release create` on its own does not obviously refuse a
    duplicate tag by default, and this module cannot verify a third-party
    action's semantics without guessing an API - so this pins the step's
    presence and its position, not which mechanism it uses internally.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    check_index = _find_step_index(steps, _is_existing_release_check)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert check_index is not None, (
        "expected a step that checks whether a release already exists for the "
        "tag before one is created (SPEC 5.2)"
    )
    assert publish_index is not None, "no release-publish step found"
    assert check_index < publish_index, (
        "the existing-release check must run before the release is published, or "
        "a duplicate tag is only caught after the publish attempt has already run"
    )


def test_existing_release_check_branches_on_http_status_not_swallowing_every_failure(tmp_path):
    """SPEC 5.2: 'Absent is not the same as unanswerable here either. The
    check asks whether a Release exists, and a query that fails on a 401, a
    5xx or a rate limit has not answered no. Reading any failure as "no
    release exists" is the SPEC.md 13 mistake in the one step whose entire
    purpose is to refuse.'

    Driven by extracting the workflow's own "No existing release for this
    tag" step and running it for real against a stub `gh` that reproduces
    the real binary's own measured behaviour with `-i`: against the
    installed `gh` 2.97.0, `gh api -i <path>` exits 0 for a 200 and exits 1
    for a 404 and for a 401 - measured directly for those three, not
    assumed, and consistent with `gh help exit-codes`'s own documented
    general rule ("If a command fails for any reason, the exit code will be
    1"), which is the basis for treating a 500 the same way below without
    it being independently measured. The script's own branching does not
    depend on this exit code either way - it parses the HTTP status line
    out of stdout regardless of `gh`'s own exit status - so the stub's exit
    code is not what makes this test meaningful, but a stub claimed to
    reproduce a binary should actually reproduce it.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_existing_release_check)
    assert index is not None, "no existing-release check step found"
    step = steps[index]

    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()

    def run_with_status(status_line):
        status_code = status_line.split()[1]
        exit_status = 0 if status_code.startswith("2") else 1
        gh_stub = stub_dir / "gh"
        gh_stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\r\\n" "{status_line}"\n'
            'printf "\\r\\n"\n'
            'printf "{}"\n'
            f"exit {exit_status}\n",
            encoding="utf-8",
        )
        gh_stub.chmod(0o755)
        env = {
            "GITHUB_REPOSITORY": "example-owner/example-repo",
            "GITHUB_REF_NAME": "v0.1.0",
        }
        return _run_step_script(step, tmp_path, env=env, stub_dir=stub_dir)

    not_found = run_with_status("HTTP/2.0 404 Not Found")
    assert not_found.returncode == 0, (
        f"a genuine 404 must proceed (no existing release): "
        f"stdout={not_found.stdout!r} stderr={not_found.stderr!r}"
    )
    assert "No existing release" in not_found.stdout
    assert "::error::" not in not_found.stdout

    # SPEC 5.2's carriage-return strip, exercised rather than merely
    # present: a status line WITH a reason phrase absorbs a trailing \r
    # into that phrase regardless of whether the strip runs, so
    # "HTTP/2.0 404 Not Found" above cannot tell the strip apart from its
    # absence. A status line with no reason phrase is different - the \r
    # lands on status_code itself ("404\r"), which matches no case arm
    # without the strip, and a genuine 404 would be wrongly reported as
    # unanswerable.
    not_found_no_reason_phrase = run_with_status("HTTP/2.0 404")
    assert not_found_no_reason_phrase.returncode == 0, (
        f"a 404 with no reason phrase must still proceed (no existing "
        f"release): stdout={not_found_no_reason_phrase.stdout!r} "
        f"stderr={not_found_no_reason_phrase.stderr!r}"
    )
    assert "No existing release" in not_found_no_reason_phrase.stdout

    exists = run_with_status("HTTP/2.0 200 OK")
    assert exists.returncode != 0, "a 200 (release exists) must refuse"
    exists_text = "\n".join(_annotation_lines(exists))
    assert "already exists" in exists_text, (
        f"expected the collision case to say a release already exists, got "
        f"{exists_text!r}"
    )
    assert "could not determine" not in exists_text, (
        f"the collision case must not be reported as unanswerable, got {exists_text!r}"
    )

    server_error = run_with_status("HTTP/2.0 500 Internal Server Error")
    assert server_error.returncode != 0, (
        "SPEC 5.2: a 5xx has not answered no; it must refuse, not proceed as "
        "though no release exists"
    )
    server_error_text = "\n".join(_annotation_lines(server_error))
    assert "could not determine" in server_error_text, (
        f"expected the 5xx case to say the question could not be answered, got "
        f"{server_error_text!r}"
    )
    assert "already exists" not in server_error_text, (
        f"the 5xx case must not be reported as though a release collided, got "
        f"{server_error_text!r}"
    )

    auth_error = run_with_status("HTTP/2.0 401 Unauthorized")
    assert auth_error.returncode != 0, "SPEC 5.2: a 401 has not answered no either; it must refuse"
    auth_error_text = "\n".join(_annotation_lines(auth_error))
    assert "could not determine" in auth_error_text
    assert "already exists" not in auth_error_text


def test_publish_step_does_not_swallow_a_failure():
    """SPEC 5.2 - 'An existing Release for the tag is a failure, not
    something to overwrite.' This module cannot verify the full rule without
    guessing a third-party action's exact input semantics (see report), so
    it pins only what SPEC.md text supports directly: the publish step must
    not be marked `continue-on-error`, which would turn any failure -
    including 'release already exists' - into a green job.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert publish_index is not None, "no release-publish step found"
    publish_step = steps[publish_index]
    assert not publish_step.get("continue-on-error"), (
        "the publish step must not set continue-on-error: an existing release for "
        "the tag failing must fail the job, not be swallowed (SPEC 5.2)"
    )
    job = _jobs(workflow)[publish_step["_job_key"]]
    assert not job.get("continue-on-error"), (
        "the job hosting the publish step must not set continue-on-error either"
    )


def _write_gh_argv_recording_stub(stub_dir, marker):
    """A `gh` stub that records its own argv, one token per line, into
    `marker`, and exits 0. Shared by every test that drives the real
    "Create the GitHub Release" step and inspects what `gh release create`
    was actually called with, so the recording mechanism is proven once
    rather than retyped - and kept identical - per test.
    """
    gh_stub = stub_dir / "gh"
    gh_stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > \"{marker}\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    gh_stub.chmod(0o755)


def _run_publish_step_and_capture_argv(step, tmp_path, stub_dir, marker, *, prerelease):
    marker.unlink(missing_ok=True)
    env = {"GITHUB_REF_NAME": "v0.1.0", "PRERELEASE": prerelease}
    result = _run_step_script(step, tmp_path, env=env, stub_dir=stub_dir)
    assert result.returncode == 0, (
        f"the extracted publish step failed to run for real with "
        f"PRERELEASE={prerelease!r}: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert marker.is_file(), "the stub gh was never invoked"
    return marker.read_text(encoding="utf-8").splitlines()


def _asset_names_from_publish_argv(argv):
    """The asset filename tokens in a `gh release create <tag> <asset...>
    <flags...>` invocation: everything after the tag (argv[2]) up to the
    first token that looks like a flag, as opposed to the tag itself or a
    flag's own value.
    """
    assets = []
    for token in argv[3:]:
        if token.startswith("--"):
            break
        assets.append(token)
    return set(assets)


def test_publish_step_includes_prerelease_flag_only_when_prerelease_is_true(tmp_path):
    """SPEC 5.2's own argument against inline shell logic (the M11 mutant
    that survived `release.yml`'s first version, moving the pre-release
    decision itself into `check_release_version.py`) applies one step
    downstream too: `if [ "$PRERELEASE" = "true" ]; then args+=(--prerelease);
    fi` is exactly the shape no workflow-as-data test can tell from its
    opposite - invert the condition and every textual assertion about this
    step still passes while every stable release publishes as a
    pre-release. Driven by extracting the real publish step and running it
    with `PRERELEASE` set directly (bypassing however the value's `${{ }}`
    expression actually resolves on a real runner, which is not this
    process's job to evaluate) and a stub `gh` on PATH recording its own
    argv, so the assertion is about what `gh release create` was actually
    called with, not about the shell text that produced it.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert publish_index is not None, "no release-publish step found"
    step = steps[publish_index]

    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    marker = tmp_path / "gh-argv"
    _write_gh_argv_recording_stub(stub_dir, marker)

    def run_with_prerelease(value):
        return _run_publish_step_and_capture_argv(
            step, tmp_path, stub_dir, marker, prerelease=value
        )

    true_argv = run_with_prerelease("true")
    assert "--prerelease" in true_argv, (
        f"expected --prerelease in the gh release create argv when "
        f"PRERELEASE=true, got {true_argv!r}"
    )

    false_argv = run_with_prerelease("false")
    assert "--prerelease" not in false_argv, (
        f"expected no --prerelease in the gh release create argv when "
        f"PRERELEASE=false, got {false_argv!r}"
    )
    assert "dist.tgz" in false_argv, (
        f"expected dist.tgz in the gh release create argv - drop it and every "
        f"other test in this module still passes, while install-on-pi.sh's "
        f"digest check fails against a real release with nothing to check "
        f"(SPEC 5.2, 5.3.1), got {false_argv!r}"
    )
    assert "SHA256SUMS" in false_argv, (
        f"expected SHA256SUMS in the gh release create argv for the same "
        f"reason, got {false_argv!r}"
    )


def test_publish_step_env_declares_gh_repo():
    """SPEC 5.2: 'The publish job has no checkout, so it must be told which
    repository it is publishing to. gh resolves the repository from the
    git remote, and a job with no working tree has none; it reads GH_REPO
    and not GITHUB_REPOSITORY, so the value has to be passed deliberately.'

    Verified against the real `gh` binary, not assumed: outside a git
    repository and with `GH_REPO` unset, `gh release view` fails with
    'failed to run git: fatal: not a git repository (or any of the parent
    directories): .git' before it reaches the network at all; with `GH_REPO`
    set, that specific failure disappears (the command then fails or
    succeeds for an entirely different reason - authentication, or the
    query itself). This is the review's blocking finding, one step removed:
    it must not be possible to delete this line without a test noticing.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert publish_index is not None, "no release-publish step found"
    env = steps[publish_index].get("env") or {}
    assert "GH_REPO" in env, (
        f"expected the publish step's env: to declare GH_REPO (SPEC 5.2: 'a "
        f"job with no working tree has none'), got env={env!r}"
    )
    assert "github.repository" in str(env["GH_REPO"]), (
        f"expected GH_REPO to read github.repository, got {env['GH_REPO']!r}"
    )


def test_publish_job_has_no_checkout():
    """The dependency test_publish_step_env_declares_gh_repo above rests on:
    SPEC 5.2 says GH_REPO is necessary specifically because this job has no
    working tree. If a checkout were ever added back to this job, `gh` could
    resolve the repository from the git remote again and GH_REPO would
    become merely redundant rather than load-bearing - which the assertion
    above would not notice, since a redundant env: entry is still present.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert publish_index is not None, "no release-publish step found"
    publish_job_key = _job_key_of(steps, publish_index)
    checkout_steps = [
        s
        for s in steps
        if "checkout" in _uses_of(s).lower() and s["_job_key"] == publish_job_key
    ]
    assert not checkout_steps, (
        f"expected no actions/checkout step in job {publish_job_key!r}; SPEC "
        f"5.2's whole argument for GH_REPO being necessary, not merely "
        f"redundant, is that this job has no working tree to resolve a "
        f"repository from"
    )


def test_publish_step_script_does_not_shadow_an_inherited_gh_repo_value(tmp_path):
    """Narrower than its previous name and docstring claimed: this does
    *not* prove the workflow declares GH_REPO - `test_publish_step_env_declares_gh_repo`
    does that, by reading the YAML, and is the right tool for it. What this
    test actually checks is that the publish step's own shell text does not
    shadow, unset, or otherwise mangle a `GH_REPO` value it is handed before
    `gh` runs - a fact a subprocess *can* observe, since it is about what the
    extracted script's own body does with a value already present in its
    environment, not about whether `release.yml`'s `env:` block put it
    there. The value used here is supplied by this test, exactly the way a
    real runner's `${{ github.repository }}` would have to be for anything
    to reach this script at all (this test does not evaluate that
    expression) - so deleting `GH_REPO: ${{ github.repository }}` from the
    workflow does not, and must not be expected to, make this test fail;
    that is the textual test's job.

    Driven with a stub `gh` that writes back the `GH_REPO` it actually saw
    in its own environment, not the real binary: the real binary reaching a
    live GitHub endpoint is not something an automated test may risk
    triggering, on this repository or any other.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    publish_index = _find_step_index(steps, _is_publish_step)
    assert publish_index is not None, "no release-publish step found"
    step = steps[publish_index]

    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    repo_marker = tmp_path / "gh-repo-seen"
    gh_stub = stub_dir / "gh"
    gh_stub.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\' "${{GH_REPO:-<unset>}}" > "{repo_marker}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    gh_stub.chmod(0o755)

    env = {
        "GITHUB_REF_NAME": "v0.1.0",
        "PRERELEASE": "false",
        # Stands in for whatever a real runner's ${{ github.repository }}
        # would have resolved to and handed this script through env: -
        # supplied by this test, not read from the workflow, which is
        # exactly why this test cannot and does not claim to verify that
        # the workflow supplies it.
        "GH_REPO": "example-owner/example-repo",
    }
    result = _run_step_script(step, tmp_path, env=env, stub_dir=stub_dir)
    assert result.returncode == 0, (
        f"the extracted publish step failed to run for real: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert repo_marker.is_file(), "the stub gh was never invoked"
    assert repo_marker.read_text(encoding="utf-8") == "example-owner/example-repo", (
        f"expected the script not to have altered the inherited GH_REPO "
        f"before gh ran, got {repo_marker.read_text(encoding='utf-8')!r}"
    )


# ---------------------------------------------------------------------------
# Archive and SHA256SUMS command lines as written. Textual pin of the exact
# strings SPEC 5.2 quotes; the behavioural proof that these strings actually
# produce the right shape is in the section below.
# ---------------------------------------------------------------------------


def test_archive_command_matches_the_pinned_command_line():
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    index = _find_step_index(steps, _is_archive_step)
    assert index is not None, "no step packs dist.tgz"
    script = _script_of(steps[index])
    assert re.search(r"tar\s+-czf\s+dist\.tgz\s+-C\s+frontend/dist\s+\.", script), (
        f"expected the literal command `tar -czf dist.tgz -C frontend/dist .` "
        f"(SPEC 5.2, SPEC 5.3.1), got script:\n{script}"
    )


def _step_cwd(workspace, step):
    """The directory a step actually runs in: `workspace` by default, or
    `workspace / working-directory` when the step sets one - GitHub Actions'
    own rule for `working-directory:`.
    """
    working_directory = step.get("working-directory")
    return (workspace / working_directory) if working_directory else workspace


def _apply_step_env(step, base_env):
    """The environment a step actually sees when it runs: `base_env` with
    the step's own `env:` block applied on top of it.

    A literal value is set directly - that is what a real runner would
    export for this step, and if a mutant deletes the `env:` entry the
    resulting environment genuinely no longer has it, which is the point.
    A `${{ ... }}` expression is left alone: evaluating GitHub Actions'
    expression syntax is not this test harness's job, so the caller
    supplies the value a real runner's expression would have resolved to,
    through the environment layered on top of this one's result, the same
    honest way the PRERELEASE tests already do. Before this function
    existed, `_run_step_script` ran only `step["run"]` and ignored
    `step["env"]` entirely, so every value an extracted script read -
    `GH_TOKEN` in the existing-release check, `PRERELEASE` and `GH_REPO` in
    the publish step - came from the test process's own environment
    regardless of what `release.yml` declared, and deleting an `env:`
    entry from the workflow made no behavioural test notice.
    """
    env = dict(base_env)
    for key, value in (step.get("env") or {}).items():
        if isinstance(value, str) and "${{" in value:
            continue
        env[key] = str(value)
    return env


# Tools a release workflow step could plausibly reach for that talk to a
# network. Every one of these gets a stub that fails loudly rather than
# running for real, in every `_run_step_script` call, whether or not the
# calling test cares about that tool - the security audit's finding, after
# an agent's own test once ran a live `gh release create` against a
# repository this project does not own: a step that starts calling a tool
# nobody thought to stub must not be able to reach it silently, and a
# barrier that only some call sites remember to raise is not a barrier.
_NETWORK_TOOLS = ("gh", "curl", "wget", "ssh", "scp", "npm", "npx", "pip", "pip3")


def _path_barrier_dir(workspace):
    """A directory holding a stub for every tool in `_NETWORK_TOOLS` that
    refuses to run and says so, cached per `workspace` so repeated calls
    against the same `tmp_path` do not keep rewriting identical files.
    Placed on `PATH` below any stub a test supplies of its own (so a test
    that means to drive a real `gh` invocation through its own stub still
    can) and above the real `PATH` (so `bash`, `git`, `tar`, `sha256sum`
    and the rest of what a step legitimately needs keep working unstubbed).

    Written into `workspace` itself, which in the ancestor-check test is a
    live git working tree: harmless today, since nothing there asserts on
    `git status`, but worth knowing before that test grows one - a barrier
    directory is exactly the kind of untracked entry that would show up.
    """
    barrier_dir = workspace / ".path-barrier"
    if barrier_dir.is_dir():
        return barrier_dir
    barrier_dir.mkdir(exist_ok=True)
    for tool in _NETWORK_TOOLS:
        script = barrier_dir / tool
        script.write_text(
            "#!/bin/sh\n"
            f'echo "{tool}: not stubbed by this test - refusing to run a real '
            'network-capable tool from inside a test" >&2\n'
            "exit 97\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    return barrier_dir


def _isolated_home_dir(workspace):
    """A directory under `workspace` this test's step processes see as
    `HOME` - and, redundantly, as `GH_CONFIG_DIR`/`XDG_CONFIG_HOME` - so
    `~/.config/gh/hosts.yml`, `~/.ssh` and a git `credential.helper` cache
    on the host running the test are never reachable from inside an
    extracted step, however the tool it calls looks them up. `PATH`
    ordering already makes a test's own `gh` stub win over the real
    binary, but that is one layer, and the isolation should not depend on
    the stub always being found first. Same caveat as the barrier
    directory above about living inside a git working tree in the
    ancestor-check test.
    """
    home_dir = workspace / ".isolated-home"
    home_dir.mkdir(exist_ok=True)
    return home_dir


# Used only when the ambient PATH is unset or empty - a container, a
# systemd unit, a cron-invoked run - rather than falling through to
# bash's own compiled-in default: verified directly on the development
# host that default ends in `.`, for the identical empty-component reason
# `_system_path` exists to avoid, so relying on it would just move the
# hazard one layer down instead of closing it. This list is deliberately
# fixed and reviewed, not discovered from whatever binary happens to be on
# the machine running the suite.
_FALLBACK_SYSTEM_PATH = "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"


def _system_path():
    """The ambient `PATH`, with every non-absolute component removed, or
    `_FALLBACK_SYSTEM_PATH` when nothing absolute remains.

    Filtered on `os.path.isabs`, not on truthiness: an empty PATH
    component - `PATH=""`, a trailing `:`, two consecutive `:`s - is
    POSIX for "the current working directory", and so is a relative one
    written out loud - `.`, `..`, a bare `bin`. A truthiness filter drops
    the first and keeps the second, which is the identical hazard wearing
    a name. `_step_cwd`'s directory is a live git working tree in the
    ancestor-check test, and an unbarriered `git`, `tar` or `sha256sum`
    planted there would win on any host whose ambient PATH carries a `.`
    or a relative entry, barrier or no barrier - the barrier only ever
    covers the tools in `_NETWORK_TOOLS`.
    """
    parts = [part for part in os.environ.get("PATH", "").split(":") if os.path.isabs(part)]
    return ":".join(parts) if parts else _FALLBACK_SYSTEM_PATH


def _run_step_script(step, workspace, *, env=None, stub_dir=None):
    """Runs `step`'s script for real, against an environment built as an
    **allow-list** rather than a filtered copy of the ambient one.

    Three layers, each able to override the one before it:

    1. What any extracted step genuinely needs to exist at all - `PATH`
       (a caller's stub directory, then the network-tool barrier, then the
       real `PATH`, so `bash`/`git`/`tar`/`sha256sum`/`mktemp` resolve and
       `gh`/`curl`/etc do not reach a real binary unstubbed), `HOME`
       pointed at an isolated directory under `workspace`, and a fixed,
       obviously-fake `GH_TOKEN`/`GITHUB_TOKEN` - assigned unconditionally
       here, not with `dict.setdefault`, so a host that exports a real
       `GH_TOKEN` cannot hand it to a step through this process regardless
       of what a caller's `env=` does or does not set.
    2. The step's own `env:` block, literal values only (`_apply_step_env`).
    3. Whatever the caller passes as `env=`, layered last - the specific
       values a real runner's own context or a `${{ }}` expression would
       have supplied (`GITHUB_SHA`, `GITHUB_REF_NAME`, `PRERELEASE`,
       `GH_REPO`, ...), which this harness does not evaluate, and nothing
       else: a caller that wants the real `PATH`, a real `HOME`, or a real
       token has to say so by name, the same way adding any other variable
       does.

    Deliberately not `dict(os.environ)` at any layer, in the function or
    in a caller: a security audit found an unrelated host secret reaching
    a child process through exactly that pattern, once as `_run_step_script`'s
    own default and once again in every caller that built its own `env=`
    from a full copy of the ambient environment before adding a handful of
    overrides on top of it - an allow-list only holds if nothing upstream
    of it re-introduces the whole environment it exists to exclude.

    Run with `bash -e -c`, not `bash -c`: GitHub's own default shell for a
    `run:` step with no `shell:` key is `bash -e {0}`, and a harness that
    runs plainer than that continues past a failing command in a
    multi-line step the way a real runner never would - `false` followed
    by `echo` would exit 0 here and 1 on a real tag, silently weakening
    every assertion this module makes about what a failing step reports.
    """
    allowed_env = {
        "HOME": str(_isolated_home_dir(workspace)),
        "GH_CONFIG_DIR": str(_isolated_home_dir(workspace)),
        "XDG_CONFIG_HOME": str(_isolated_home_dir(workspace)),
        "GH_TOKEN": "test-token",
        "GITHUB_TOKEN": "test-token",
    }
    path_entries = []
    if stub_dir is not None:
        path_entries.append(str(stub_dir))
    path_entries.append(str(_path_barrier_dir(workspace)))
    path_entries.append(_system_path())
    # Filtered rather than joined blindly: POSIX reads an empty PATH
    # component as the current working directory, so a trailing (or
    # embedded) empty entry - which `os.environ.get("PATH", "")` produces
    # whenever the ambient PATH is unset or itself empty, the case in a
    # container, a systemd unit or a cron job - would silently put
    # `_step_cwd`'s directory on the search path for every unbarriered
    # tool. In the ancestor-check test that directory is a live git
    # working tree the test itself populates - verified directly: a
    # trailing colon on PATH executes a script placed in the current
    # directory, the same colon's absence does not.
    allowed_env["PATH"] = ":".join(entry for entry in path_entries if entry)

    run_env = _apply_step_env(step, allowed_env)
    if env is not None:
        run_env.update(env)

    return subprocess.run(
        ["bash", "-e", "-c", _script_of(step)],
        cwd=_step_cwd(workspace, step),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# The barrier `_run_step_script` raises by default (SPEC 5.2 names none of
# this - it is this module's own mitigation, after an agent's own test once
# ran a live `gh release create` against a repository this project does not
# own). `_run_step_script` accepts any step mapping, not only one extracted
# from `release.yml`, so the barrier is demonstrated directly against a
# synthetic step rather than through a real workflow step - the "Pack
# dist.tgz" demonstration attempted earlier was unsound (it has no `set -e`
# of its own, and a step's overall status is the last command's, so an
# injected `curl` failing ahead of it proved nothing either way) and is not
# repeated. A mitigation nothing tests is a mitigation that lasts until the
# next refactor, not a property of this file.
#
# The target below is http://127.0.0.1:1/, not a real host: these tests
# exist for the day the barrier is broken, and on that day the "network
# tool" line actually runs. A reachable domain - even a safe one meant for
# exactly this, like example.com - would then make a real outbound request
# from whatever machine is running the suite, including CI, and the
# assertion would mean "the request happened to fail" rather than "the
# barrier held". Port 1 on loopback refuses locally and immediately.
# ---------------------------------------------------------------------------


def test_path_barrier_refuses_an_unstubbed_network_tool(tmp_path):
    result = _run_step_script({"run": "curl http://127.0.0.1:1/"}, tmp_path)
    assert result.returncode == 97, (
        f"expected the barrier's refusal exit status, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "not stubbed by this test" in result.stderr, (
        f"expected the barrier's own refusal message, got stderr={result.stderr!r}"
    )


def test_caller_supplied_stub_still_wins_over_the_path_barrier(tmp_path):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    gh_stub = stub_dir / "curl"
    gh_stub.write_text("#!/bin/sh\necho called-the-real-stub\nexit 0\n", encoding="utf-8")
    gh_stub.chmod(0o755)
    result = _run_step_script(
        {"run": "curl http://127.0.0.1:1/"}, tmp_path, stub_dir=stub_dir
    )
    assert result.returncode == 0, (
        f"expected the caller's own stub to run instead of the barrier's "
        f"refusal, got returncode={result.returncode} stderr={result.stderr!r}"
    )
    assert "called-the-real-stub" in result.stdout


def test_path_barrier_leaves_unbarriered_tools_reachable(tmp_path):
    """The claim this test's name makes is not proven by a bare
    returncode, and not even by checking that `git`/`tar` resolve from
    *somewhere* `$PATH` names: if the `PATH` key is left out of the child's
    environment entirely (not merely empty), Python's own subprocess
    resolves `bash` via the platform's compiled-in default search path,
    bash then runs with no `$PATH` of its own, and bash's *own* compiled-in
    fallback (the same one whose trailing `.` was this branch's
    PATH-empty-component finding) supplies `git` and `tar` from within
    itself - so an assertion that only checks internal consistency between
    the observed `$PATH` and where the tools resolved from would still pass
    for the wrong reason, since bash's fallback is self-consistent too.
    Caught three times on this branch by exactly this shape: a name
    promising more than the body checks. The one fact bash's own fallback
    cannot produce by accident is this harness's own barrier directory - a
    fresh, per-test path under `tmp_path` - so its presence in the
    child's observed `$PATH` is what actually distinguishes "the allow-list
    supplied this" from "bash defaulted to something that happened to work".
    """
    barrier_dir = str(_path_barrier_dir(tmp_path))
    result = _run_step_script(
        {"run": 'echo "$PATH"; command -v git; command -v tar'}, tmp_path
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 3, f"expected PATH, then git's and tar's resolved paths, got {lines!r}"
    observed_path, git_path, tar_path = lines
    observed_components = set(observed_path.split(":"))
    assert barrier_dir in observed_components, (
        f"expected this harness's own barrier directory ({barrier_dir!r}) on "
        f"the child's $PATH ({observed_path!r}); its absence means the PATH "
        f"observed here was not the one this harness constructed, whatever "
        f"else happens to be consistent about it"
    )
    for name, resolved in (("git", git_path), ("tar", tar_path)):
        resolved_dir = str(Path(resolved).parent)
        assert resolved_dir in observed_components, (
            f"expected {name} ({resolved!r}) to resolve from a directory this "
            f"harness put on PATH ({sorted(observed_components)!r}), not from "
            f"a resolution bash performed on its own"
        )


@pytest.mark.parametrize("clear", ["set_empty", "unset"])
def test_system_path_falls_back_when_the_ambient_path_is_empty(monkeypatch, tmp_path, clear):
    """`_FALLBACK_SYSTEM_PATH` exists for exactly one branch of
    `_system_path` - an ambient PATH with nothing absolute left in it -
    and nothing in this module forced that branch to run before this
    test: SPEC 13's rule in a different hat, a path nothing exercises is
    a path nobody knows works. Parametrized over PATH set to the empty
    string and PATH unset entirely, since `os.environ.get("PATH", "")`
    treats both identically but a fix that only handled one would not be
    caught by exercising just the other.
    """
    if clear == "set_empty":
        monkeypatch.setenv("PATH", "")
    else:
        monkeypatch.delenv("PATH", raising=False)

    result = _run_step_script({"run": 'echo "$PATH"; command -v git; command -v tar'}, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 3, f"expected PATH, then git's and tar's resolved paths, got {lines!r}"
    observed_path, git_path, tar_path = lines

    assert observed_path.endswith(_FALLBACK_SYSTEM_PATH), (
        f"expected the fallback system path to be the tail of the observed "
        f"PATH when the ambient PATH is empty, got {observed_path!r}"
    )
    fallback_components = set(_FALLBACK_SYSTEM_PATH.split(":"))
    for name, resolved in (("git", git_path), ("tar", tar_path)):
        resolved_dir = str(Path(resolved).parent)
        assert resolved_dir in fallback_components, (
            f"expected {name} ({resolved!r}) to resolve from the fallback "
            f"system path {sorted(fallback_components)!r}, got directory "
            f"{resolved_dir!r}"
        )


def test_system_path_drops_a_relative_ambient_entry(monkeypatch, tmp_path):
    """The identical hazard to the empty-component one, spelled out loud:
    a `.`, a `..` or a bare `bin` in the ambient PATH is POSIX for "the
    current working directory" (or wherever the shell's cwd happens to
    put it) just as an empty component is, and a truthiness filter would
    keep it while dropping the empty one. `_step_cwd`'s directory is a
    live git working tree in the ancestor-check test; an unbarriered
    `git` or `tar` planted there must not win because a relative entry on
    the ambient PATH survived into the constructed one.
    """
    monkeypatch.setenv("PATH", ".:bin:/usr/bin:/bin")
    result = _run_step_script({"run": 'echo "$PATH"'}, tmp_path)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    observed_components = result.stdout.strip().split(":")
    for relative_entry in (".", "bin"):
        assert relative_entry not in observed_components, (
            f"expected no relative entry {relative_entry!r} on the constructed "
            f"PATH, got {result.stdout.strip()!r}"
        )
    assert "/usr/bin" in observed_components and "/bin" in observed_components, (
        f"expected the ambient PATH's absolute entries to survive, got "
        f"{result.stdout.strip()!r}"
    )


def test_run_step_script_stops_on_a_failing_command_like_the_real_runner_does(tmp_path):
    """GitHub's own default shell for a `run:` step with no `shell:` key is
    `bash -e {0}`, not plain `bash`. Every multi-line step in today's
    `release.yml` happens to start with its own `set -euo pipefail`, so
    this cannot be demonstrated by extracting and running one of them
    unchanged - the difference only shows up on a step that forgot to, or
    on a synthetic one, which is what `_run_step_script` accepts and what
    this test drives directly: `false` followed by a further command must
    fail the whole script, the way it would on a real runner and did not
    under a bare `bash -c`.
    """
    result = _run_step_script({"run": "false\necho still ran"}, tmp_path)
    assert result.returncode != 0, (
        f"expected a failing command to stop the script and fail it, matching "
        f"GitHub's own bash -e {{0}} default; got returncode "
        f"{result.returncode}, stdout={result.stdout!r}"
    )


def test_run_step_script_uses_an_isolated_home_not_the_real_one(tmp_path):
    """The allow-list's own point, demonstrated directly rather than
    inferred from `GH_CONFIG_DIR`/`XDG_CONFIG_HOME` alone: `HOME` itself
    must resolve to a directory under this test's `tmp_path`, not the real
    home of whatever host runs the suite, or `~/.ssh`, `~/.gnupg` and a
    git `credential.helper` cache stay reachable from inside a step.
    """
    result = _run_step_script({"run": 'echo "$HOME"'}, tmp_path)
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    reported_home = result.stdout.strip()
    real_home = os.environ.get("HOME", "")
    assert reported_home and reported_home != real_home, (
        f"expected an isolated HOME under {tmp_path}, got the real host "
        f"HOME ({real_home!r}) instead"
    )
    assert reported_home.startswith(str(tmp_path)), (
        f"expected the isolated HOME to live under {tmp_path}, got "
        f"{reported_home!r}"
    )


def test_run_step_script_env_is_an_allow_list_not_the_ambient_environment(tmp_path):
    """The fix's whole point, proven with a concrete value rather than
    argued from the absence of `dict(os.environ)` in the source: a
    variable exported into this test process's own environment - standing
    in for a real secret a host running this suite might export, the way
    the security audit found one reaching a child process - must not reach
    an extracted step's process just because it happened to be ambient
    when the test ran.
    """
    sentinel_name = "OPENPWNAGOTCHI_TEST_HOST_SECRET_SENTINEL"
    os.environ[sentinel_name] = "should-not-leak-into-a-step"
    try:
        result = _run_step_script(
            {"run": f'echo "${{{sentinel_name}:-<absent>}}"'}, tmp_path
        )
    finally:
        del os.environ[sentinel_name]
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert result.stdout.strip() == "<absent>", (
        f"expected an arbitrary ambient environment variable not to reach "
        f"the extracted step's process, got {result.stdout.strip()!r}"
    )


def test_pack_and_sha256sums_steps_run_for_real_and_produce_a_bare_named_entry(tmp_path):
    """SPEC 5.2's `SHA256SUMS` rule, driven by extracting and running the
    workflow's own "Pack dist.tgz" and "Generate SHA256SUMS" steps against a
    synthetic `frontend/dist`, in that order, in one job workspace - not by
    typing an independently-correct `sha256sum` invocation into the test.

    A textual pin (a regex ruling out a `/` before `dist.tgz`) passes for
    `cd frontend && sha256sum dist.tgz > ../SHA256SUMS` just as happily as
    for the rule it is meant to check, because that command never mentions
    a `/` next to `dist.tgz` at all - and it would fail in exactly the way
    that produces a blank page on somebody's unit: `dist.tgz` is not in
    `frontend/`, so the command errors and no `SHA256SUMS` is written.
    Running the extracted scripts for real makes that failure visible
    instead of invisible to the check.
    """
    workflow = _require_release_workflow()
    steps = _linearize_steps(workflow)
    pack_index = _find_step_index(steps, _is_archive_step)
    sums_index = _find_step_index(steps, _is_sha256sums_step)
    assert pack_index is not None, "no step packs dist.tgz"
    assert sums_index is not None, "no step generates SHA256SUMS"

    workspace = tmp_path
    _build_synthetic_frontend_dist(workspace)

    pack_step = steps[pack_index]
    pack_result = _run_step_script(pack_step, workspace)
    assert pack_result.returncode == 0, (
        f"the extracted 'Pack dist.tgz' step failed to run for real: "
        f"stdout={pack_result.stdout!r} stderr={pack_result.stderr!r}"
    )

    sums_step = steps[sums_index]
    sums_result = _run_step_script(sums_step, workspace)
    assert sums_result.returncode == 0, (
        f"the extracted 'Generate SHA256SUMS' step failed to run for real: "
        f"stdout={sums_result.stdout!r} stderr={sums_result.stderr!r}"
    )

    sums_path = _step_cwd(workspace, sums_step) / "SHA256SUMS"
    assert sums_path.is_file(), (
        f"expected {sums_path} to exist after running the extracted step; "
        f"the workflow's real steps must produce SHA256SUMS beside dist.tgz"
    )
    content = sums_path.read_text(encoding="utf-8").strip()
    digest, _, name = content.partition("  ")
    assert re.fullmatch(r"[0-9a-f]{64}", digest), (
        f"expected a 64-hex sha256 digest as the first field, got {content!r}"
    )
    assert name == "dist.tgz", (
        f"expected the recorded name to be the bare filename 'dist.tgz' with no "
        f"path component, got {name!r} from the real SHA256SUMS content {content!r}"
    )


# ---------------------------------------------------------------------------
# Behavioural: the three-way version agreement is a property of this
# checkout's files, testable with no workflow involved. Read through
# `check_release_version.py`'s own `plugin_version`/`frontend_version`/
# `disagreements` (imported as `crv` above), not a second copy of that
# regex and that JSON reader: a copy that ships nowhere cannot catch its
# original drifting from it, which was review's exact finding on the
# version returned here - the every-parametrised-case table this module
# used to carry (`versions_agree`, `test_versions_agree_helper`) is deleted
# rather than kept, since `tests/tools/test_check_release_version.py`
# already drives `disagreements` and `tag_version` directly and more
# thoroughly than a copy here ever did.
# ---------------------------------------------------------------------------


def test_current_repository_plugin_and_frontend_versions_agree_with_each_other():
    """A property of two files in this checkout today, with no workflow, no
    tag and no CI run involved, read through `check_release_version.py`
    itself: SPEC 12 says the plugin, the frontend package and the tag
    "always carry the same number" - not only at release time. If this
    fails, the next tag pushed would fail release.yml's own check, and it
    is cheaper to learn that here than from a broken release.
    """
    plugin_version = crv.plugin_version(PLUGIN_PATH.read_text(encoding="utf-8"))
    frontend_version = crv.frontend_version(PACKAGE_JSON_PATH.read_text(encoding="utf-8"))
    assert plugin_version == frontend_version, (
        f"plugin/companion.py __version__ = {plugin_version!r} disagrees with "
        f"frontend/package.json version = {frontend_version!r}"
    )


def test_current_repository_versions_agree_with_a_correctly_cut_tag():
    plugin_version = crv.plugin_version(PLUGIN_PATH.read_text(encoding="utf-8"))
    frontend_version = crv.frontend_version(PACKAGE_JSON_PATH.read_text(encoding="utf-8"))
    correct_tag = f"v{plugin_version}"
    assert crv.disagreements(correct_tag, plugin_version, frontend_version) == []


def test_current_repository_versions_disagree_with_a_wrong_tag():
    plugin_version = crv.plugin_version(PLUGIN_PATH.read_text(encoding="utf-8"))
    frontend_version = crv.frontend_version(PACKAGE_JSON_PATH.read_text(encoding="utf-8"))
    wrong_tag = f"v{plugin_version}-definitely-not-the-real-tag"
    assert crv.disagreements(wrong_tag, plugin_version, frontend_version) != []


# ---------------------------------------------------------------------------
# Behavioural: the archive shape, produced with the literal `tar` command
# SPEC 5.2 and 5.3.1 pin and inspected the way it will actually be read.
# ---------------------------------------------------------------------------


def _build_synthetic_frontend_dist(root):
    dist_dir = root / "frontend" / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>synthetic</body></html>\n", encoding="utf-8")
    (dist_dir / "assets" / "app.js").write_text("// synthetic\n", encoding="utf-8")
    return dist_dir


def test_archive_layout_places_index_html_at_the_archive_root(tmp_path):
    _build_synthetic_frontend_dist(tmp_path)
    archive = tmp_path / "dist.tgz"
    subprocess.run(
        ["tar", "-czf", str(archive), "-C", "frontend/dist", "."],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=30,
    )
    with tarfile.open(archive) as tar:
        names = {name.removeprefix("./").rstrip("/") for name in tar.getnames()}
    assert "index.html" in names, (
        f"expected index.html at the archive root, got members {sorted(names)!r}"
    )
    assert not any(name.startswith("dist/") or name.startswith("frontend/") for name in names), (
        f"no member may sit under a dist/ or frontend/ prefix, got members {sorted(names)!r}"
    )


def test_archive_extracts_index_html_directly_into_the_target_directory(tmp_path):
    """The installer's own contract (SPEC 5.3.1): 'no dist/ prefix ... the
    installer never needs --strip-components.' Proven by actually
    extracting, not by inspecting names, since a member name without a
    prefix and a member that merely lacks a leading slash are not the same
    fact and only extraction distinguishes a relative subdirectory member
    from a root one landing in the wrong relative place.
    """
    source_root = tmp_path / "source"
    _build_synthetic_frontend_dist(source_root)
    archive = source_root / "dist.tgz"
    subprocess.run(
        ["tar", "-czf", str(archive), "-C", "frontend/dist", "."],
        cwd=source_root,
        check=True,
        capture_output=True,
        timeout=30,
    )
    web_root = tmp_path / "web_root"
    web_root.mkdir()
    subprocess.run(
        ["tar", "-xzf", str(archive), "-C", str(web_root)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    installed_names = sorted(p.name for p in web_root.iterdir())
    assert (web_root / "index.html").is_file(), (
        f"expected index.html directly under {web_root}, got: {installed_names}"
    )
    assert (web_root / "assets" / "app.js").is_file()
