"""Tests for the scripts of the start gate (``tools/start_gate``) and the tag condition in the docs.

The gate itself needs podman or docker and the network (it builds an RPM and installs it with dnf in
a container of each COPR chroot), so it is not run here. What is checked is what a change can break
without anybody seeing it: the scripts parse and stop on an error, the lines that make the gate hard
are there (each probe is a step that sets the failure status, the status is the exit status, nothing
is passed over with ``|| true``), and the tag condition of the release procedure names the gate, the
chroots and the script that checks the tag condition. What the scripts do is run in
``test_start_gate_run.py`` and ``test_start_gate_verify.py``, with stand-ins for the commands. The
workflow is checked in ``test_start_gate_workflow.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "tools" / "start_gate"
RELEASING = REPO / "docs" / "RELEASING.md"
SHELL_SCRIPTS = (
    "start_gate.sh",
    "build.sh",
    "check.sh",
    "verify_checks.sh",
    "negative_control.sh",
)
PYTHON_SCRIPTS = ("probe_installed.py", "probe_service.py", "fake_login1.py")


def _lines(name: str) -> list:
    return (GATE / name).read_text(encoding="utf-8").splitlines()


def _code(name: str) -> list:
    """The lines of a script without the comments."""
    return [line for line in _lines(name) if not line.lstrip().startswith("#")]


@pytest.mark.parametrize("name", SHELL_SCRIPTS)
def test_shell_script_stops_on_the_first_error(name):
    """A failed command cannot be passed over: the first line names bash and the strict mode is
    set. ``negative_control.sh`` has no ``-e``: it takes the exit status of the gate to judge it."""
    lines = _lines(name)
    assert lines[0] in ("#!/bin/bash", "#!/usr/bin/env bash")
    strict = "set -uo pipefail" if name == "negative_control.sh" else "set -euo pipefail"
    assert any(line.startswith(strict) for line in lines)


@pytest.mark.parametrize("name", SHELL_SCRIPTS + ("common.sh",))
def test_no_failure_is_passed_over_with_or_true(name):
    for line in _code(name):
        if name == "start_gate.sh" and "command -v docker" in line:
            continue  # the choice of the engine: no podman and no docker is an error of its own
        assert "|| true" not in line and "||true" not in line, line
        assert "|| :" not in line, line


@pytest.mark.parametrize("name", ["start_gate.sh", "build.sh", "verify_checks.sh"])
def test_the_strict_mode_is_never_turned_off(name):
    assert not [line for line in _code(name) if line.strip().startswith(("set +e", "set +o"))]


def test_check_turns_off_the_exit_on_error_once_and_only_after_the_install():
    """The probes must all run, so ``set -e`` goes off after the install; what ends a failed probe
    is ``|| status=1`` and the exit status at the end. The install (a failed one stops the script)
    comes first."""
    code = _code("check.sh")
    assert len([line for line in code if line.strip() == "set +e"]) == 1
    off = code.index("set +e")
    install = [i for i, line in enumerate(code) if "gate_dnf install" in line]
    assert install and install[0] < off


def test_check_installs_without_weak_dependencies():
    assert any(
        "gate_dnf install --setopt=install_weak_deps=false" in line for line in _code("check.sh")
    )


def test_check_runs_both_probes_and_a_failure_of_either_is_the_status():
    code = [line.strip() for line in _code("check.sh")]
    assert 'python3 -I /gate/probe_installed.py "$version" || status=1' in code
    assert "python3 -I /gate/probe_service.py || status=1" in code
    assert code[-1] == 'exit "$status"'


def test_check_fails_without_rpm_and_for_a_wrong_tag():
    text = "\n".join(_code("check.sh"))
    assert "START-GATE rpm FAIL there is no rpm" in text
    assert 'GITHUB_REF_NAME:-}" = "v$version"' in text


def test_build_does_not_swallow_a_failure_of_rpmbuild():
    """rpmbuild ends with 11 when %generate_buildrequires names more build requirements: that code
    is the only one that is answered with a second round; any other non-zero code ends the stage."""
    text = "\n".join(_code("build.sh"))
    assert 'rpmbuild --rebuild --define "_topdir /out/build" "$srpm" || rc=$?' in text
    assert '[ "$rc" -eq 11 ]' in text
    assert '[ "$rc" -ne 0 ]' in text and 'exit "$rc"' in text


@pytest.mark.parametrize("name", PYTHON_SCRIPTS)
def test_python_script_parses(name):
    ast.parse((GATE / name).read_text(encoding="utf-8"), filename=name)


@pytest.mark.parametrize("name", SHELL_SCRIPTS + PYTHON_SCRIPTS)
def test_script_is_executable(name):
    assert (GATE / name).stat().st_mode & 0o111, f"{name} is not executable"


def test_the_tag_condition_names_the_gate():
    text = RELEASING.read_text(encoding="utf-8")
    assert "all three of these hold" in text
    assert "The start gate (the section" in text
    assert "if one chroot did\n   not run, there is no tag" in text


def test_the_tag_condition_makes_verify_checks_the_step_before_the_tag():
    text = RELEASING.read_text(encoding="utf-8")
    assert "tools/start_gate/verify_checks.sh <sha>" in text
    assert "required step before the tag" in text
    assert "the chroot list of the COPR project is read from the COPR API" in text
    assert "the push of a `v*` tag is not a gate" in text
    assert "tools/start_gate/negative_control.sh" in text


def test_the_sha_to_verify_is_the_head_that_is_tagged():
    text = RELEASING.read_text(encoding="utf-8")
    assert '`"$(git rev-parse HEAD)"` of the commit that is tagged' in text
    assert "not the head of a pull request" in text


def test_the_architectures_the_script_knows_are_named():
    """``verify_checks.sh`` cuts a fixed list of architectures off the chroot names; the file says
    which, so a COPR project that gets another one is a change of the script."""
    script = (GATE / "verify_checks.sh").read_text(encoding="utf-8")
    text = RELEASING.read_text(encoding="utf-8")
    assert "riscv64" not in script
    for arch in ("x86_64", "aarch64", "ppc64le", "s390x", "i386", "armhfp"):
        assert arch in script and f"`{arch}`" in text
