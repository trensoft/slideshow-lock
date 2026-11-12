"""The scripts of the start gate (``tools/start_gate``), run with stand-ins for dnf, rpm, rpmbuild
and the container engine (``tests/start_gate_helpers.py``).

The gate itself needs a container engine and the network, so CI runs it
(``.github/workflows/start-gate.yml``). What is checked here is its logic, the part that makes it a
gate: a failed step ends the script with a non-zero status, no step is left out, the install has no
weak dependencies, a failure of ``rpmbuild`` is not passed over, and a tag that is not the version
of the RPM does not pass. A change that makes the gate soft fails one of these tests.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from tests.start_gate_helpers import GATE, NEVRA, Box, fake_engine, make_tree, stub
from tests.timeout_guard import per_test_deadline  # noqa: F401  (autouse fixture)

pytestmark = pytest.mark.spawns_processes


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


# ---- check.sh: stage 2 ----------------------------------------------------------------------


@pytest.fixture
def check(box):
    """check.sh in a box: one RPM in /out/rpm, rpm -q that agrees with it, two probes that pass."""
    rpm_dir = box.out / "rpm"
    rpm_dir.mkdir()
    (rpm_dir / f"{NEVRA}.rpm").write_bytes(b"not a real rpm")
    stub(box.bin / "rpm", f'echo -n "${{STUB_RPM_NVRA:-{NEVRA}}}"\n')
    stub(
        box.bin / "python3",
        'echo "python3 $*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '    *probe_installed*) exit "${STUB_INSTALLED_RC:-0}" ;;\n'
        '    *probe_service*) exit "${STUB_SERVICE_RC:-0}" ;;\n'
        "esac\n"
        "exit 99\n",
    )
    script = box.script("check.sh")
    return lambda **env: box.run(script, **env)


def _last_line(result) -> str:
    return result.stdout.strip().splitlines()[-1]


def test_check_passes_when_everything_passes(box, check):
    result = check()
    assert result.returncode == 0, result.stdout + result.stderr
    assert _last_line(result).startswith(f"START-GATE RESULT PASS {NEVRA} sha256 ")
    assert "START-GATE rpm PASS" in result.stdout


def test_check_installs_the_rpm_without_weak_dependencies(box, check):
    check()
    installs = [call for call in box.calls() if call.startswith("dnf install")]
    assert len(installs) == 1
    assert "--setopt=install_weak_deps=false" in installs[0]
    assert str(box.out / "rpm" / f"{NEVRA}.rpm") in installs[0]


def test_check_runs_both_probes_from_the_installed_copy(box, check):
    check()
    probes = [call for call in box.calls() if call.startswith("python3")]
    # -I: no current directory and no PYTHONPATH, so the installed copy is the one that is tested
    assert probes == [
        f"python3 -I {box.gate}/probe_installed.py 1.2.3",
        f"python3 -I {box.gate}/probe_service.py",
    ]


@pytest.mark.parametrize("failing", ["STUB_INSTALLED_RC", "STUB_SERVICE_RC"])
def test_check_fails_when_a_probe_fails_and_still_runs_the_other(box, check, failing):
    result = check(**{failing: "1"})
    assert result.returncode == 1
    assert _last_line(result).startswith(f"START-GATE RESULT FAIL {NEVRA}")
    assert len([c for c in box.calls() if c.startswith("python3")]) == 2


def test_check_stops_when_the_install_fails(box, check):
    result = check(STUB_DNF_RC="1")
    assert result.returncode != 0
    assert not [call for call in box.calls() if call.startswith("python3")]
    assert "START-GATE RESULT PASS" not in result.stdout


def test_check_fails_when_rpm_q_names_another_package(box, check):
    result = check(STUB_RPM_NVRA="slideshow-lock-1.0.11-1.fc43.noarch")
    assert result.returncode == 1
    assert "START-GATE rpm FAIL" in result.stdout
    assert _last_line(result).startswith("START-GATE RESULT FAIL")


def test_check_fails_when_the_image_has_no_rpm(box, check):
    (box.bin / "rpm").unlink()
    result = check()
    assert result.returncode == 1
    assert "START-GATE rpm FAIL there is no rpm" in result.stdout


@pytest.mark.parametrize("count", [0, 2])
def test_check_wants_exactly_one_rpm(box, check, count):
    rpm_dir = box.out / "rpm"
    for rpm in rpm_dir.glob("*.rpm"):
        rpm.unlink()
    for number in range(count):
        (rpm_dir / f"slideshow-lock-1.2.{number}-1.fc43.noarch.rpm").write_bytes(b"x")
    result = check()
    assert result.returncode == 1
    assert "START-GATE RESULT FAIL: expected one RPM" in result.stdout
    assert not [call for call in box.calls() if call.startswith("python3")]


def test_check_for_a_tag_wants_the_tag_to_be_v_and_the_version(box, check):
    result = check(GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v1.2.3")
    assert result.returncode == 0, result.stdout
    assert "START-GATE tag PASS" in result.stdout


@pytest.mark.parametrize("name", ["v1.2.4", "1.2.3", "v1.2.3-rc1", ""])
def test_check_fails_when_the_tag_is_not_v_and_the_version(box, check, name):
    result = check(GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=name)
    assert result.returncode == 1
    assert "START-GATE tag FAIL" in result.stdout
    assert _last_line(result).startswith("START-GATE RESULT FAIL")


def test_check_ignores_the_ref_name_when_the_run_is_not_for_a_tag(box, check):
    result = check(GITHUB_REF_TYPE="branch", GITHUB_REF_NAME="main")
    assert result.returncode == 0
    assert "START-GATE tag" not in result.stdout


# ---- build.sh: stage 1 ----------------------------------------------------------------------


@pytest.fixture
def build(box):
    """build.sh in a box: make makes a source RPM, rpmbuild answers with the codes in
    STUB_RPMBUILD_RCS (one per call, 0 after the list), and an RPM of an earlier run is in the
    build directory, as it can be left there."""
    stub(
        box.bin / "make",
        'echo "make $*" >> "$STUB_LOG"\n'
        'for arg; do case "$arg" in outdir=*) outdir=${arg#outdir=} ;; esac; done\n'
        'mkdir -p "$outdir"; : > "$outdir/slideshow-lock-1.2.3-1.src.rpm"\n',
    )
    stub(
        box.bin / "rpmbuild",
        'echo "rpmbuild $*" >> "$STUB_LOG"\n'
        'n=0; [ -f "$STUB_LOG.count" ] && read -r n < "$STUB_LOG.count"\n'
        'n=$((n + 1)); echo "$n" > "$STUB_LOG.count"\n'
        'read -r -a rcs <<< "${STUB_RPMBUILD_RCS:-0}"\n'
        "rc=${rcs[$((n - 1))]:-0}\n"
        'if [ "$rc" -eq 0 ]; then\n'
        '    mkdir -p "$STUB_OUT/build/RPMS/noarch"\n'
        f'    : > "$STUB_OUT/build/RPMS/noarch/{NEVRA}.rpm"\n'
        "fi\n"
        'exit "$rc"\n',
    )
    (box.src / ".copr").mkdir()
    script = box.script("build.sh")
    return lambda **env: box.run(script, STUB_OUT=str(box.out), **env)


def test_build_makes_the_rpm_and_copies_it_out(box, build):
    result = build()
    assert result.returncode == 0, result.stdout + result.stderr
    assert [p.name for p in (box.out / "rpm").iterdir()] == [f"{NEVRA}.rpm"]
    calls = box.calls()
    assert any(call.startswith("make -f") and " srpm outdir=" in call for call in calls)
    assert any(call.startswith("dnf builddep") and call.endswith(".src.rpm") for call in calls)


def test_build_installs_the_second_round_of_build_requirements_on_code_11(box, build):
    result = build(STUB_RPMBUILD_RCS="11 0")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = box.calls()
    assert len([c for c in calls if c.startswith("rpmbuild")]) == 2
    assert any("buildreqs.nosrc.rpm" in c and c.startswith("dnf builddep") for c in calls)


@pytest.mark.parametrize("codes, expected", [("1", 1), ("2", 2), ("11 1", 1), ("11 11", 11)])
def test_build_ends_with_the_failure_of_rpmbuild_and_copies_nothing(box, build, codes, expected):
    # an RPM left in the build directory (an earlier run) must not be taken for the result
    stale = box.out / "build" / "RPMS" / "noarch"
    stale.mkdir(parents=True)
    (stale / f"{NEVRA}.rpm").write_bytes(b"left over")
    result = build(STUB_RPMBUILD_RCS=codes)
    assert result.returncode == expected, result.stdout + result.stderr
    assert not list((box.out / "rpm").iterdir())


def test_build_stops_when_dnf_fails(box, build):
    result = build(STUB_DNF_RC="1")
    assert result.returncode != 0
    assert not [call for call in box.calls() if call.startswith("rpmbuild")]


def test_build_leaves_the_files_removable_for_whoever_started_the_container(box, build):
    # build.sh ends with chmod -R a+rwX on /out, also after a failure
    stub(box.bin / "chmod", 'echo "chmod $*" >> "$STUB_LOG"\n')
    build(STUB_RPMBUILD_RCS="1")
    assert any(call.startswith("chmod -R a+rwX") for call in box.calls())


# ---- start_gate.sh: the two containers -------------------------------------------------------


@pytest.fixture
def gate_run(box):
    tree = make_tree(box.root / "tree")

    def run(stage_rc=None, stage_output=None, **env):
        engine = fake_engine(box.root / "engine", stage_output or {}, stage_rc or {})
        return box.run(
            GATE / "start_gate.sh",
            "registry.example/image:1",
            str(tree),
            START_GATE_ENGINE=str(engine),
            PATH=f"{box.bin}:/usr/bin",
            **env,
        )

    return run


def test_start_gate_runs_the_two_stages_in_two_containers(box, gate_run):
    result = gate_run()
    assert result.returncode == 0, result.stdout + result.stderr
    stage1, stage2 = box.calls()
    assert stage1.endswith("bash /gate/build.sh") and stage2.endswith("bash /gate/check.sh")
    # stage 1 has the tree, stage 2 has not: the installed copy is what is tested
    assert f"{box.root / 'tree'}:/src:ro" in stage1 and ":/src" not in stage2
    assert ":/out registry.example/image:1 bash" in stage1
    assert ":/out:ro registry.example/image:1 bash" in stage2


def test_start_gate_passes_the_tag_of_the_run_to_stage_2(box, gate_run):
    gate_run()
    stage2 = box.calls()[1]
    assert "-e GITHUB_REF_TYPE" in stage2 and "-e GITHUB_REF_NAME" in stage2


def test_start_gate_does_not_start_stage_2_after_a_failed_stage_1(box, gate_run):
    result = gate_run(stage_rc={"build.sh": 7})
    assert result.returncode == 7
    assert len(box.calls()) == 1


def test_start_gate_ends_with_the_failure_of_stage_2(box, gate_run):
    result = gate_run(stage_rc={"check.sh": 1})
    assert result.returncode == 1
    assert len(box.calls()) == 2


def test_start_gate_without_an_engine_is_an_error(box):
    tree = make_tree(box.root / "tree")
    result = box.run(GATE / "start_gate.sh", "image", str(tree))
    assert result.returncode == 2
    assert "no podman or docker" in result.stderr


def test_start_gate_wants_a_tree_with_the_copr_makefile(box):
    result = box.run(GATE / "start_gate.sh", "image", str(box.root), START_GATE_ENGINE="/bin/true")
    assert result.returncode == 2


# ---- negative_control.sh ---------------------------------------------------------------------

# stage 2 of the gate on the tree of 1.0.11 (EL10): the install and the import go through, the
# signal watch fails with the error that 1.0.11 shipped with
A_PASS = "START-GATE a PASS version 1.2.3"
B_PASS = "START-GATE b PASS ok"
C_FAIL = "START-GATE c FAIL AttributeError: 'GLibUnix' object has no attribute 'signal_add'"
RESULT_FAIL = "START-GATE RESULT FAIL x sha256 y"


def stage2(*lines):
    return {"check.sh": "\n".join(lines)}


FAIL_C = stage2(A_PASS, B_PASS, C_FAIL, RESULT_FAIL)


@pytest.fixture
def control(box):
    tree = make_tree(box.root / "tree")

    def run(stage_rc=None, stage_output=None):
        engine = fake_engine(box.root / "engine", stage_output or {}, stage_rc or {})
        return box.run(
            GATE / "negative_control.sh",
            "registry.example/image:1",
            str(tree),
            START_GATE_ENGINE=str(engine),
            PATH=f"{box.bin}:/usr/bin",
        )

    return run


def test_the_control_passes_when_the_gate_fails_in_step_c(control):
    result = control({"check.sh": 1}, FAIL_C)
    assert result.returncode == 0, result.stdout
    assert "NEGATIVE-CONTROL PASS" in result.stdout


def test_the_control_passes_when_the_probe_for_d_fails_as_well(control):
    """On 1.0.11 the service dies of the same error, so (d) fails too: that is no reason to stop."""
    output = stage2(A_PASS, B_PASS, C_FAIL, "START-GATE d FAIL service ended", RESULT_FAIL)
    assert control({"check.sh": 1}, output).returncode == 0


def test_the_control_fails_when_the_gate_passes_on_the_bad_tree(control):
    result = control({}, stage2("START-GATE RESULT PASS x sha256 y"))
    assert result.returncode == 1
    assert "it is soft" in result.stdout


@pytest.mark.parametrize(
    "rc, output",
    [
        ({"build.sh": 1}, {}),  # the build broke: nothing was started
        ({"check.sh": 1}, stage2(A_PASS, B_PASS, RESULT_FAIL)),  # (c) did not fail
        ({"check.sh": 1}, stage2(A_PASS, B_PASS, C_FAIL)),  # no summary: stage 2 did not end
        ({"check.sh": 1}, stage2("network is unreachable")),
    ],
)
def test_the_control_proves_nothing_when_the_gate_fails_for_another_reason(control, rc, output):
    result = control(rc, output)
    assert result.returncode == 1
    assert "NEGATIVE-CONTROL FAIL" in result.stdout


@pytest.mark.parametrize(
    "c_line",
    [
        "START-GATE c FAIL ModuleNotFoundError: No module named 'gi'",  # no gi on the image
        "START-GATE c FAIL AttributeError: module 'slideshow_lock.loop' has no attribute 'x'",
        "START-GATE c FAIL AssertionError: no signal arrived",  # the probe's own failure
        "START-GATE c FAIL OSError: signal_add",  # signal_add, but not the AttributeError
        "START-GATE c FAIL",  # no reason at all
    ],
)
def test_the_control_is_red_when_step_c_fails_for_another_reason_than_1_0_11(control, c_line):
    """The gate fails on the same step but not for the reason of 1.0.11: that is not the proof."""
    result = control({"check.sh": 1}, stage2(A_PASS, B_PASS, c_line, RESULT_FAIL))
    assert result.returncode == 1, result.stdout
    assert "NEGATIVE-CONTROL FAIL" in result.stdout
    assert "NEGATIVE-CONTROL PASS" not in result.stdout


@pytest.mark.parametrize(
    "lines",
    [
        # a step before (c) failed too: the install or the import is broken, not only the signal
        # watch
        ("START-GATE a FAIL AssertionError: wrong version", B_PASS, C_FAIL, RESULT_FAIL),
        (A_PASS, "START-GATE b FAIL ImportError: x", C_FAIL, RESULT_FAIL),
        # a step before (c) did not run at all
        (B_PASS, C_FAIL, RESULT_FAIL),
        (A_PASS, C_FAIL, RESULT_FAIL),
        (C_FAIL, RESULT_FAIL),
    ],
)
def test_the_control_is_red_when_a_step_before_c_does_not_pass(control, lines):
    result = control({"check.sh": 1}, stage2(*lines))
    assert result.returncode == 1, result.stdout
    assert "NEGATIVE-CONTROL FAIL" in result.stdout


@pytest.mark.parametrize("which", range(4), ids=["a", "b", "c", "summary"])
@pytest.mark.parametrize("prefix", ["x ", "  ", "+ "], ids=["word", "indent", "diff"])
def test_the_control_wants_every_line_of_stage_2_as_a_line_of_its_own(control, which, prefix):
    """A line that only contains the words (a log of the build, an echo of a command) counts for
    nothing: every one of the four lines is matched at the start of the line."""
    lines = [A_PASS, B_PASS, C_FAIL, RESULT_FAIL]
    lines[which] = prefix + lines[which]
    result = control({"check.sh": 1}, stage2(*lines))
    assert result.returncode == 1, result.stdout


# ---- the probes: a failed step is the exit status 1 ------------------------------------------


def _load(name):
    """The probe as a module. No ``__pycache__`` is written into ``tools/start_gate``, and none is
    read: a stale one (the same size, the same second) would run an older text of the script."""
    spec = importlib.util.spec_from_file_location(f"gate_{name}", GATE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    source = (GATE / f"{name}.py").read_text(encoding="utf-8")
    exec(compile(source, str(GATE / f"{name}.py"), "exec"), module.__dict__)  # noqa: S102
    return module


def test_probe_installed_ends_with_1_when_one_step_fails(monkeypatch, capsys):
    probe = _load("probe_installed")

    def broken():
        raise AssertionError("no signal watch")

    monkeypatch.setattr(sys, "argv", ["probe_installed.py", "1.2.3"])
    monkeypatch.setattr(probe, "step_installed", lambda expected: "ok")
    monkeypatch.setattr(probe, "step_import", lambda: "ok")
    monkeypatch.setattr(probe, "step_signal", broken)
    assert probe.main() == 1
    out = capsys.readouterr().out
    assert "START-GATE a PASS" in out and "START-GATE b PASS" in out
    assert "START-GATE c FAIL AssertionError: no signal watch" in out


@pytest.mark.parametrize("failing", ["step_installed", "step_import"])
def test_probe_installed_runs_every_step_and_fails_for_the_first_two_as_well(
    monkeypatch, capsys, failing
):
    probe = _load("probe_installed")

    def broken(*_args):
        raise ImportError("gone")

    monkeypatch.setattr(sys, "argv", ["probe_installed.py", "1.2.3"])
    monkeypatch.setattr(probe, "step_installed", lambda expected: "ok")
    monkeypatch.setattr(probe, "step_import", lambda: "ok")
    monkeypatch.setattr(probe, "step_signal", lambda: "ok")
    monkeypatch.setattr(probe, failing, broken)
    assert probe.main() == 1
    assert capsys.readouterr().out.count("PASS") == 2


def test_probe_installed_passes_when_all_steps_pass(monkeypatch, capsys):
    probe = _load("probe_installed")
    monkeypatch.setattr(sys, "argv", ["probe_installed.py", "1.2.3"])
    monkeypatch.setattr(probe, "step_installed", lambda expected: f"version {expected}")
    monkeypatch.setattr(probe, "step_import", lambda: "ok")
    monkeypatch.setattr(probe, "step_signal", lambda: "ok")
    assert probe.main() == 0
    assert "START-GATE a PASS version 1.2.3" in capsys.readouterr().out


def test_probe_installed_wants_the_version(monkeypatch, capsys):
    probe = _load("probe_installed")
    monkeypatch.setattr(sys, "argv", ["probe_installed.py"])
    assert probe.main() == 2


@pytest.mark.parametrize("error", [RuntimeError("down"), OSError("no bus"), KeyError("x")])
def test_probe_service_ends_with_1_when_the_run_fails(monkeypatch, capsys, error):
    probe = _load("probe_service")

    def broken(tmp, timeout, children):
        raise error

    monkeypatch.setattr(sys, "argv", ["probe_service.py"])
    monkeypatch.setattr(probe, "run", broken)
    assert probe.main() == 1
    assert "START-GATE d FAIL" in capsys.readouterr().out


def test_probe_service_ends_with_0_when_the_run_passes(monkeypatch, capsys):
    probe = _load("probe_service")
    monkeypatch.setattr(sys, "argv", ["probe_service.py"])
    monkeypatch.setattr(probe, "run", lambda tmp, timeout, children: "it ran")
    assert probe.main() == 0
    assert "START-GATE d PASS it ran" in capsys.readouterr().out


def test_probe_service_stops_the_children_also_after_a_failure(monkeypatch):
    probe = _load("probe_service")

    class Child:
        stopped = False

        def poll(self):
            return None

        def terminate(self):
            self.stopped = True

        def wait(self, timeout=None):
            return 0

    child = Child()

    def broken(tmp, timeout, children):
        children.append(child)
        raise RuntimeError("down")

    monkeypatch.setattr(sys, "argv", ["probe_service.py"])
    monkeypatch.setattr(probe, "run", broken)
    assert probe.main() == 1
    assert child.stopped
