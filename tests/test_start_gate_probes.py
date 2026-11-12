"""The inside of the probes of the start gate (``tools/start_gate/probe_installed.py``,
``probe_service.py`` and ``fake_login1.py``).

``test_start_gate_run.py`` runs ``main()`` of each probe with the steps replaced, so it measures how
the steps are joined and what the exit status is. It does not run a step. These tests do: every
decision a step makes (the version, where the module is loaded from, an import, a signal that does
not arrive, the exit status of the service, a traceback in its log) is run, once with the thing that
passes and once with the thing that must fail. The signal step runs for real in a child process (a
real GLib main loop, a real SIGTERM and SIGINT), with the call of the tree and with the call broken
in the ways that 1.0.11 was and in others. The service step runs with a stand-in ``slideshow-lock``
that behaves the way each case needs, and ``fake_login1.py`` runs on a real private bus.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

from tests.start_gate_helpers import GATE, REPO, stub
from tests.timeout_guard import (
    PER_TEST_DEADLINE_SECONDS,
    per_test_deadline,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.spawns_processes


def _load(name):
    """The probe as a module. No ``__pycache__`` is written into ``tools/start_gate``, and none is
    read: a stale one (the same size, the same second) would run an older text of the script."""
    module = types.ModuleType(f"gate_{name}")
    module.__file__ = str(GATE / f"{name}.py")
    sys.modules[module.__name__] = module
    source = (GATE / f"{name}.py").read_text(encoding="utf-8")
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


@pytest.fixture
def installed():
    return _load("probe_installed")


@pytest.fixture
def service():
    return _load("probe_service")


# ---- the scripts really run their main when they are started as scripts -----------------------


@pytest.mark.parametrize(
    "script, args, status, text",
    [
        ("probe_installed.py", [], 2, "usage"),  # it wants the version of the RPM
        ("probe_service.py", ["--help"], 0, "usage:"),
        ("fake_login1.py", [], 2, "DBUS_SYSTEM_BUS_ADDRESS is not set"),
    ],
)
def test_the_scripts_run_their_main_when_they_are_started_as_scripts(script, args, status, text):
    """check.sh starts them with ``python3 -I script``. A script whose ``__main__`` guard never
    holds would end with 0 and say nothing: the gate would pass with no probe run."""
    pytest.importorskip("gi")
    result = subprocess.run(
        [sys.executable, "-I", str(GATE / script), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == status, (result.stdout, result.stderr)
    assert text in result.stdout + result.stderr


# ---- probe_installed: step (a), the installed version and where it is loaded from -------------


def _version(monkeypatch, value):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: value)


def test_step_a_passes_for_the_version_of_the_rpm_loaded_from_the_system_prefix(
    installed, monkeypatch
):
    _version(monkeypatch, "1.2.3")
    monkeypatch.setattr(sys, "base_prefix", str(REPO))
    text = installed.step_installed("1.2.3")
    assert text.startswith("version 1.2.3 from ")
    assert str(REPO / "slideshow_lock" / "__init__.py") in text


def test_step_a_asks_for_the_package_of_the_program(installed, monkeypatch):
    asked = []
    monkeypatch.setattr(sys, "base_prefix", str(REPO))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: asked.append(name) or "1.2.3")
    installed.step_installed("1.2.3")
    assert asked == ["slideshow-lock"]


@pytest.mark.parametrize("installed_version", ["1.2.4", "1.2", "1.2.3.1", "", "v1.2.3"])
def test_step_a_fails_when_the_installed_version_is_not_the_one_of_the_rpm(
    installed, monkeypatch, installed_version
):
    _version(monkeypatch, installed_version)
    monkeypatch.setattr(sys, "base_prefix", str(REPO))
    with pytest.raises(AssertionError, match="installed version .*, the RPM has 1.2.3"):
        installed.step_installed("1.2.3")


def test_step_a_fails_when_the_module_is_a_checkout_and_not_the_installed_copy(
    installed, monkeypatch, tmp_path
):
    _version(monkeypatch, "1.2.3")
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path))
    with pytest.raises(AssertionError, match="is loaded from .*, not from"):
        installed.step_installed("1.2.3")


def test_step_a_does_not_take_a_directory_that_only_starts_like_the_prefix(
    installed, monkeypatch, tmp_path
):
    """``/usr`` must not match ``/usr2/lib/...``: the prefix is compared with its separator."""
    _version(monkeypatch, "1.2.3")
    prefix = tmp_path / "ab"
    other = tmp_path / "abc"
    (other / "slideshow_lock").mkdir(parents=True)
    (other / "slideshow_lock" / "__init__.py").write_text("", encoding="utf-8")
    prefix.mkdir()
    monkeypatch.setattr(sys, "base_prefix", str(prefix))
    monkeypatch.syspath_prepend(str(other))
    monkeypatch.delitem(sys.modules, "slideshow_lock")  # put back by monkeypatch at the end
    with pytest.raises(AssertionError, match="is loaded from"):
        installed.step_installed("1.2.3")


# ---- probe_installed: step (b), the imports ----------------------------------------------------


def test_step_b_imports_the_service_and_the_preview_app_from_the_tree(installed):
    pytest.importorskip("gi")
    text = installed.step_import()
    assert text.startswith("service and preview_app import (service at ")
    assert str(REPO / "slideshow_lock" / "service.py") in text


@pytest.mark.parametrize("broken", ["slideshow_lock.service", "slideshow_lock.preview_app"])
def test_step_b_fails_when_one_of_the_two_modules_does_not_import(installed, monkeypatch, broken):
    def fake(name, *args):
        if name == broken:
            raise ImportError(f"cannot import {name}")
        return types.SimpleNamespace(__file__=f"/x/{name}.py")

    monkeypatch.setattr(installed.importlib, "import_module", fake)
    with pytest.raises(ImportError, match=broken):
        installed.step_import()


def test_step_b_names_both_modules_when_all_is_well(installed, monkeypatch):
    asked = []

    def fake(name, *args):
        asked.append(name)
        return types.SimpleNamespace(__file__=f"/x/{name}.py")

    monkeypatch.setattr(installed.importlib, "import_module", fake)
    assert installed.step_import() == (
        "service and preview_app import (service at /x/slideshow_lock.service.py)"
    )
    assert asked == ["slideshow_lock.service", "slideshow_lock.preview_app"]


# ---- probe_installed: step (c), the signal watch, run for real in a child process --------------

# The step sends a real SIGTERM and a real SIGINT to its own process: it runs in a child, so that
# a signal that nobody catches ends the child and not the test run. The driver loads the probe
# from the file, breaks ``loop.unix_signal_add`` the way MODE says, runs ``step_signal`` and
# prints the line that ``main()`` prints for it.
DRIVER = textwrap.dedent(
    """
    import importlib.util, signal, sys, time, warnings
    started_at = time.monotonic()
    def phase(name):
        gone = time.monotonic() - started_at
        print("phase %s +%.2fs" % (name, gone), file=sys.stderr, flush=True)
    sys.path.insert(0, {repo!r})
    from slideshow_lock import loop
    from gi.repository import GLib
    mode = sys.argv[1]
    phase("imports done")
    class TracedLoop(GLib.MainLoop):
        def run(self):
            phase("loop starts")
            if mode == "stall":
                time.sleep(3600)
            try:
                super().run()
            finally:
                phase("loop ends")
    GLib.MainLoop = TracedLoop
    real = loop.unix_signal_add
    if mode == "attribute-error":
        def broken(*args, **kwargs):
            raise AttributeError("'gi.repository.GLibUnix' object has no attribute 'signal_add'")
        loop.unix_signal_add = broken
    elif mode == "zero":
        loop.unix_signal_add = lambda *args, **kwargs: 0
    elif mode in ("term-only", "int-only"):
        keep = signal.SIGTERM if mode == "term-only" else signal.SIGINT
        def swallow(prio, signum, cb):
            return real(prio, signum, cb if signum == keep else (lambda: GLib.SOURCE_REMOVE))
        loop.unix_signal_add = swallow
    elif mode == "warns":
        def warns(prio, signum, cb):
            warnings.warn("the old signal_add name is deprecated", DeprecationWarning)
            return real(prio, signum, cb)
        loop.unix_signal_add = warns
    elif mode == "warns-other":
        def warns(prio, signum, cb):
            warnings.warn("something else is deprecated", DeprecationWarning)
            return real(prio, signum, cb)
        loop.unix_signal_add = warns
    spec = importlib.util.spec_from_file_location("probe", {probe!r})
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    started = time.monotonic()
    try:
        print("START-GATE c PASS", probe.step_signal())
        print("took %.2f" % (time.monotonic() - started))
    except Exception as exc:
        print("START-GATE c FAIL", type(exc).__name__ + ":", exc)
        sys.exit(1)
    """
).format(repo=str(REPO), probe=str(GATE / "probe_installed.py"))


def _took(result):
    """The seconds the step took, as the driver measured them (not the start of the child)."""
    return float(result.stdout.rsplit("took ", 1)[1].split()[0])


#: Seconds the child of the signal step gets. It is shorter than the 60 s of the per-test deadline
#: (``timeout_guard``), so that this branch runs first, looks at the child and reports it; the
#: alarm of the deadline would only name the line of ``communicate()``.
CHILD_TIMEOUT = 30
#: Seconds the child gets to be reaped after the kill (a child in the ``D`` state is not). Both
#: together stay below the per-test deadline (a test pins that).
REAP_TIMEOUT = 15


def _child_state(pid):
    """What the kernel says about a child that is still running: the ``State`` line (``S`` sleeping,
    ``R`` running, ``D`` uninterruptible, ``T`` stopped), the kernel function it waits in and the
    system call it is in. Read before the kill: the kill changes all three."""
    lines = []
    for name in ("status", "wchan", "syscall"):
        try:
            text = Path(f"/proc/{pid}/{name}").read_text(encoding="utf-8").strip()
        except OSError as exc:
            text = f"unreadable ({exc.__class__.__name__}: {exc})"
        if name == "status":
            lines.append(next((ln for ln in text.splitlines() if ln.startswith("State:")), text))
        else:
            lines.append(f"{name}: {text}")
    return "\n".join(lines)


def _text(output):
    """The output of a child as text. ``TimeoutExpired`` carries it as bytes (or ``None``)."""
    if output is None:
        return ""
    return output.decode("utf-8", "replace") if isinstance(output, bytes) else output


def _signal_step(mode):
    pytest.importorskip("gi")
    started = time.monotonic()
    child = subprocess.Popen(
        [sys.executable, "-W", "ignore::ResourceWarning", "-c", DRIVER, mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        stdout, stderr = child.communicate(timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        state = _child_state(child.pid)
        killed = time.monotonic()
        child.kill()
        try:
            stdout, stderr = child.communicate(timeout=REAP_TIMEOUT)
            reaped = f"{time.monotonic() - killed:.2f} s"
        except subprocess.TimeoutExpired as late:
            # what the child wrote before is kept by ``communicate()`` and comes with the error
            stdout, stderr = _text(late.stdout), _text(late.stderr)
            reaped = f"not reaped in {REAP_TIMEOUT} s (a child in the D state?)"
        pytest.fail(
            f"the child of the signal step (mode {mode}) was still running after "
            f"{CHILD_TIMEOUT} s\n{state}\nkill -> reap: {reaped}\n"
            f"stderr (the phases): {stderr!r}\nstdout: {stdout!r}"
        )
    finally:
        # a hard timeout or a Ctrl-C must not leave a child that sleeps for an hour behind; no
        # ``wait()``: a child in the D state would hold the test here
        if child.poll() is None:
            child.kill()
        child.stdout.close()
        child.stderr.close()
    result = subprocess.CompletedProcess(child.args, child.returncode, stdout, stderr)
    return result, time.monotonic() - started


def test_step_c_passes_with_the_call_of_the_tree_and_both_signals_arrive():
    result, _ = _signal_step("intact")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "START-GATE c PASS SIGTERM and SIGINT arrived, glib " in result.stdout
    assert "warnings []" in result.stdout
    # the main loop ends when the second signal has come, not at its time limit of 5 s
    assert _took(result) < 3, f"both signals arrive within 0.4 s: {result.stdout}"


def test_the_deadlines_of_the_child_end_before_the_per_test_deadline():
    """If they did not, the alarm of the test would fire first and the report would be lost."""
    assert CHILD_TIMEOUT + REAP_TIMEOUT < PER_TEST_DEADLINE_SECONDS


def test_step_c_says_which_phase_the_child_has_reached():
    result, _ = _signal_step("intact")
    phases = [ln.split(" +")[0] for ln in result.stderr.splitlines() if ln.startswith("phase ")]
    assert phases == ["phase imports done", "phase loop starts", "phase loop ends"], result.stderr


def test_a_child_that_does_not_end_is_reported_with_its_state_and_its_phase(monkeypatch):
    """The 60 s hang of the step (a run of the whole suite, 2026-11-12) left only the line of
    ``communicate()``. A child that sleeps in its main loop (mode ``stall``) is reported by the
    branch of the timeout with the kernel state, the phase it reached and the time of the reap."""
    monkeypatch.setattr(sys.modules[__name__], "CHILD_TIMEOUT", 2)
    with pytest.raises(pytest.fail.Exception) as raised:
        _signal_step("stall")
    message = str(raised.value)
    assert "was still running after 2 s" in message
    assert re.search(r"^State:\s+S \(sleeping\)$", message, re.M), message
    assert "wchan: " in message and "syscall: " in message
    assert "phase loop starts" in message and "phase loop ends" not in message
    assert re.search(r"kill -> reap: \d+\.\d\d s", message), message


def test_a_child_that_is_not_reaped_still_reports_its_phases(monkeypatch):
    """A child that the kill does not end (the ``D`` state) is the case the reap time is for. A
    reap deadline that is over at once stands in for it: the phases the child wrote before are
    still in the message (they come with the second ``TimeoutExpired``)."""
    monkeypatch.setattr(sys.modules[__name__], "CHILD_TIMEOUT", 2)
    monkeypatch.setattr(sys.modules[__name__], "REAP_TIMEOUT", -1)
    with pytest.raises(pytest.fail.Exception) as raised:
        _signal_step("stall")
    message = str(raised.value)
    assert "kill -> reap: not reaped in -1 s" in message, message
    assert "phase imports done" in message and "phase loop starts" in message, message


def test_the_child_is_killed_when_the_wait_for_it_is_cut_short(monkeypatch):
    """A hard timeout of the test (or a Ctrl-C) must not leave the child, which sleeps for an
    hour in mode ``stall``, behind."""

    class Cut(BaseException):
        pass

    seen = []

    def cut(self, *args, **kwargs):
        seen.append(self)
        raise Cut

    monkeypatch.setattr(subprocess.Popen, "communicate", cut)
    try:
        with pytest.raises(Cut):
            _signal_step("stall")
        assert seen[0].wait(timeout=10) == -9, "the child was not killed"
    finally:
        if seen and seen[0].poll() is None:
            seen[0].kill()
            seen[0].wait()


def test_step_c_fails_when_the_call_raises_the_error_of_1_0_11():
    result, _ = _signal_step("attribute-error")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "START-GATE c FAIL AttributeError: 'gi.repository.GLibUnix' object has no attribute" in (
        result.stdout
    )


def test_step_c_fails_when_no_watch_is_made():
    result, _ = _signal_step("zero")
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "no Unix signal watch was made (ids [0, 0])" in result.stdout


@pytest.mark.parametrize(
    "mode, arrived", [("term-only", "['SIGTERM']"), ("int-only", "['SIGINT']")]
)
def test_step_c_fails_when_one_of_the_signals_does_not_arrive(mode, arrived):
    result, _ = _signal_step(mode)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert f"signals that arrived in the main loop: {arrived}" in result.stdout


def test_step_c_reports_a_warning_about_a_signal_and_not_another_one():
    warned, _ = _signal_step("warns")
    assert warned.returncode == 0, (warned.stdout, warned.stderr)
    assert "DeprecationWarning: the old signal_add name is deprecated" in warned.stdout
    other, _ = _signal_step("warns-other")
    assert other.returncode == 0, (other.stdout, other.stderr)
    assert "warnings []" in other.stdout


# ---- probe_service.py: start_bus and wait_for --------------------------------------------------

DBUS_DAEMON = textwrap.dedent(
    """
    for arg; do case "$arg" in --config-file=*) config=${arg#--config-file=} ;; esac; done
    case "${STUB_BUS:-ok}" in
    ok) path=$(sed -n 's/.*<listen>unix:path=\\(.*\\)<\\/listen>.*/\\1/p' "$config"); : > "$path"
        exec sleep 60 ;;
    dies) exit 1 ;;
    silent) exec sleep 60 ;;
    esac
    """
)


@pytest.fixture
def fast(service, monkeypatch):
    """The sleeps of the probe are a fifth as long: the polling and the 2 s of the run."""
    real = time.sleep
    monkeypatch.setattr(service.time, "sleep", lambda seconds: real(seconds / 5))
    return real


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    path = tmp_path / "bin"
    path.mkdir()
    monkeypatch.setenv("PATH", f"{path}:{os.environ.get('PATH', '')}")
    stub(path / "dbus-daemon", DBUS_DAEMON)
    return path


@pytest.fixture
def children():
    """The children a test starts: ended at the end of the test, whatever it did."""
    started = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_start_bus_waits_for_the_socket_and_returns_its_address(
    service, fast, bin_dir, tmp_path, children
):
    proc, address = service.start_bus(str(tmp_path), "session")
    children.append(proc)
    assert address == f"unix:path={tmp_path / 'session.sock'}"
    assert (tmp_path / "session.sock").exists()
    config = (tmp_path / "session.conf").read_text(encoding="utf-8")
    assert f"<listen>unix:path={tmp_path / 'session.sock'}</listen>" in config
    assert "<type>session</type>" in config and "<auth>EXTERNAL</auth>" in config
    assert proc.poll() is None


@pytest.mark.parametrize("mode", ["dies", "silent"])
def test_start_bus_fails_when_the_bus_does_not_come_up(
    service, fast, bin_dir, tmp_path, monkeypatch, children, mode
):
    monkeypatch.setenv("STUB_BUS", mode)
    started = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        children.append(proc)
        return proc

    monkeypatch.setattr(service.subprocess, "Popen", popen)
    sleeps = []
    monkeypatch.setattr(
        service.time, "sleep", lambda seconds: (sleeps.append(seconds), fast(0.01))[0]
    )
    with pytest.raises(RuntimeError, match="the system bus did not come up"):
        service.start_bus(str(tmp_path), "system")
    assert len(started) == 1
    # a bus that has ended is given up at once, a silent one is waited for 100 times 0.1 s
    assert len(sleeps) < 20 if mode == "dies" else len(sleeps) == 100


def test_start_bus_asks_the_daemon_for_a_session_bus_with_the_config_it_wrote(
    service, fast, bin_dir, tmp_path, monkeypatch, children
):
    stub(bin_dir / "dbus-daemon", 'echo "$@" > "$0.args"\n' + DBUS_DAEMON)
    proc, _ = service.start_bus(str(tmp_path), "session")
    children.append(proc)
    args = (bin_dir / "dbus-daemon.args").read_text(encoding="utf-8").split()
    assert args == ["--nofork", f"--config-file={tmp_path / 'session.conf'}"]


class _Proc:
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


def _log(tmp_path, text):
    path = tmp_path / "service.log"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_wait_for_is_true_when_the_text_is_in_the_log(service, fast, tmp_path):
    path = _log(tmp_path, "starting\nservice running\n")
    assert service.wait_for(path, "service running", _Proc(), 1) is True


def test_wait_for_sees_a_text_that_comes_later(service, fast, tmp_path):
    path = _log(tmp_path, "starting\n")
    polls = []

    class Growing(_Proc):
        def poll(self):
            polls.append(1)
            if len(polls) == 3:
                Path(path).write_text("starting\nservice running\n", encoding="utf-8")
            return None

    assert service.wait_for(path, "service running", Growing(), 5) is True
    assert len(polls) >= 3


def test_wait_for_is_false_when_the_process_ended_without_the_text(service, fast, tmp_path):
    path = _log(tmp_path, "starting\n")
    assert service.wait_for(path, "service running", _Proc(3), 5) is False


def test_wait_for_is_false_when_the_time_is_up(service, tmp_path, monkeypatch):
    path = _log(tmp_path, "starting\n")
    clock = iter([0.0, 0.1, 0.2, 5.0, 5.1])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)
    assert service.wait_for(path, "service running", _Proc(), 1) is False


def test_wait_for_reads_a_log_with_bytes_that_are_not_text(service, fast, tmp_path):
    path = tmp_path / "service.log"
    path.write_bytes(b"\xff\xfe service running \xff\n")
    assert service.wait_for(str(path), "service running", _Proc(), 1) is True


# ---- probe_service.py: run, with a stand-in slideshow-lock that behaves as each case needs -----

LOGIN_UP = 'print("fake-login1 up", flush=True)\nimport time\ntime.sleep(60)\n'
SERVICE = textwrap.dedent(
    """
    echo "starting $*"
    case "${STUB_SERVICE:-ok}" in
    early-exit) echo "ERROR: cannot reach logind"; exit 3 ;;
    no-main-loop) exec sleep 60 ;;
    by-itself) echo "service running"; sleep 0.3; exit 0 ;;
    esac
    case "${STUB_SERVICE:-ok}" in
    ignores-term) trap '' TERM ;;
    ok) trap 'echo "service stopped"; exit 0' TERM ;;
    noisy) trap 'echo "service stopped"; exit 0' TERM
           echo "ERROR: no org.gnome.Mutter.DisplayConfig"
           echo "WARNING: no GNOME Shell on the session bus" ;;
    bad-exit) trap 'echo "service stopped"; exit 1' TERM ;;
    no-stopped-line) trap 'exit 0' TERM ;;
    traceback) trap 'echo "Traceback (most recent call last):"
                     echo "service stopped"; exit 0' TERM ;;
    traceback-early) echo "Traceback (most recent call last):"
                     trap 'echo "service stopped"; exit 0' TERM ;;
    esac
    echo "service running"
    while :; do sleep 0.05; done
    """
)


@pytest.fixture
def stand_ins(service, fast, bin_dir, tmp_path, monkeypatch, children):
    """A gate directory with a fake logind that says it is up, a ``slideshow-lock`` that follows
    ``STUB_SERVICE``, and a ``dbus-daemon`` that makes the socket."""
    gate = tmp_path / "gate"
    gate.mkdir()
    (gate / "fake_login1.py").write_text(LOGIN_UP, encoding="utf-8")
    monkeypatch.setattr(service, "HERE", str(gate))
    stub(bin_dir / "slideshow-lock", SERVICE)
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(service.subprocess, "Popen", popen)
    return gate


@pytest.fixture
def _run(service, stand_ins, tmp_path, monkeypatch):
    """``run`` of the probe with the stand-in service in the mode that the test names."""

    def go(mode, timeout=3.0):
        monkeypatch.setenv("STUB_SERVICE", mode)
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        return service.run(str(work), timeout, [])

    return go


def test_run_passes_when_the_service_reaches_its_main_loop_and_ends_with_0_on_sigterm(_run, capsys):
    detail = _run("ok")
    assert detail == (
        "the service reached the main loop, ran 2 s, and ended with exit status 0 on SIGTERM"
    )
    out = capsys.readouterr().out
    assert "---- service log ----" in out and "---- end of the service log ----" in out
    assert "starting service --debug" in out and "service stopped" in out


def test_run_passes_for_a_service_that_logs_errors_for_what_a_container_does_not_have(_run, capsys):
    """The GNOME Shell and mutter interfaces are not there: the service logs them as errors and
    runs on. That is not a failure of this step."""
    assert "ended with exit status 0 on SIGTERM" in _run("noisy")
    assert "ERROR: no org.gnome.Mutter.DisplayConfig" in capsys.readouterr().out


def test_run_gives_the_service_a_home_a_runtime_dir_and_two_private_buses(
    _run, bin_dir, tmp_path, monkeypatch, capsys
):
    stub(
        bin_dir / "slideshow-lock",
        'env | sort > "$STUB_ENV"\n' + SERVICE,
    )
    monkeypatch.setenv("STUB_ENV", str(tmp_path / "env.txt"))
    monkeypatch.setenv("LC_ALL", "hu_HU.UTF-8")
    monkeypatch.setenv("LANGUAGE", "hu")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/somewhere/config")
    monkeypatch.setenv("XDG_DATA_HOME", "/somewhere/data")
    _run("ok")
    env = dict(
        line.split("=", 1)
        for line in (tmp_path / "env.txt").read_text().splitlines()
        if "=" in line
    )
    work = tmp_path / "work"
    assert env["HOME"] == str(work / "home") and (work / "home").is_dir()
    assert env["XDG_RUNTIME_DIR"] == str(work / "xdg")
    assert (work / "xdg").stat().st_mode & 0o777 == 0o700
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={work / 'session.sock'}"
    assert env["DBUS_SYSTEM_BUS_ADDRESS"] == f"unix:path={work / 'system.sock'}"
    assert env["GSETTINGS_BACKEND"] == "memory"
    assert env["LANG"] == "C.UTF-8" and env["PYTHONWARNINGS"] == "ignore"
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert name not in env, name
    capsys.readouterr()


def test_run_starts_both_buses_before_the_service(_run, bin_dir, capsys):
    stub(
        bin_dir / "slideshow-lock",
        'for bus in "$DBUS_SESSION_BUS_ADDRESS" "$DBUS_SYSTEM_BUS_ADDRESS"; do\n'
        '  [ -e "${bus#unix:path=}" ] && echo "bus is there: ${bus##*/}"\n'
        "done\n" + SERVICE,
    )
    assert "ended with exit status 0" in _run("ok")
    out = capsys.readouterr().out
    assert "bus is there: session.sock" in out and "bus is there: system.sock" in out


def test_run_fails_when_the_fake_logind_does_not_say_it_is_up(_run, stand_ins, children):
    (stand_ins / "fake_login1.py").write_text('print("something else")\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="fake_login1.py did not come up"):
        _run("ok")


def test_run_fails_when_the_service_ends_before_its_main_loop(_run, capsys):
    with pytest.raises(
        RuntimeError, match="the service ended with exit status 3 before its main loop"
    ):
        _run("early-exit")
    out = capsys.readouterr().out
    assert "---- service log ----" in out and "ERROR: cannot reach logind" in out


def test_run_fails_when_the_service_never_reaches_its_main_loop(_run, capsys):
    with pytest.raises(RuntimeError, match=r"the service did not reach its main loop in 1 s"):
        _run("no-main-loop", timeout=1.0)
    assert "---- service log ----" in capsys.readouterr().out


def test_run_fails_when_the_service_ends_by_itself_after_its_main_loop(_run, capsys):
    with pytest.raises(RuntimeError, match="the service ended by itself with exit status 0"):
        _run("by-itself")


def test_run_fails_when_the_service_does_not_end_on_sigterm(_run, capsys):
    with pytest.raises(RuntimeError, match=r"the service did not end within 1 s of SIGTERM"):
        _run("ignores-term", timeout=1.0)


def test_run_fails_when_the_service_ends_with_another_status_than_0(_run, capsys):
    with pytest.raises(RuntimeError, match="the service ended with exit status 1 on SIGTERM"):
        _run("bad-exit")


def test_run_fails_when_the_log_has_no_service_stopped_line(_run, capsys):
    with pytest.raises(RuntimeError, match='no "service stopped" line in the log'):
        _run("no-stopped-line")


@pytest.mark.parametrize("mode", ["traceback", "traceback-early"])
def test_run_fails_for_a_traceback_in_the_log_of_a_service_that_ended_with_0(_run, capsys, mode):
    with pytest.raises(RuntimeError, match="there is a traceback in the log of the service"):
        _run(mode)
    assert "Traceback (most recent call last):" in capsys.readouterr().out


def test_run_gives_the_service_the_command_line_that_the_unit_has(_run, capsys):
    _run("ok")
    assert "starting service --debug" in capsys.readouterr().out


def test_main_ends_with_1_and_names_the_failure_in_a_line_of_its_own(
    service, stand_ins, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("STUB_SERVICE", "bad-exit")
    monkeypatch.setattr(sys, "argv", ["probe_service.py", "--timeout", "3"])
    assert service.main() == 1
    out = capsys.readouterr().out
    assert out.rstrip().splitlines()[-1] == (
        "START-GATE d FAIL RuntimeError: the service ended with exit status 1 on SIGTERM"
    )


def test_main_ends_with_0_and_the_pass_line(service, stand_ins, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STUB_SERVICE", "ok")
    monkeypatch.setattr(sys, "argv", ["probe_service.py", "--timeout", "3"])
    assert service.main() == 0
    assert (
        capsys.readouterr()
        .out.rstrip()
        .splitlines()[-1]
        .startswith("START-GATE d PASS the service")
    )


def test_main_takes_the_timeout_from_the_command_line(service, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(service, "run", lambda tmp, timeout, children: seen.append(timeout) or "x")
    monkeypatch.setattr(sys, "argv", ["probe_service.py"])
    assert service.main() == 0
    monkeypatch.setattr(sys, "argv", ["probe_service.py", "--timeout", "7.5"])
    assert service.main() == 0
    assert seen == [30.0, 7.5]


def test_main_ends_every_child_that_the_run_started(service, monkeypatch, capsys):
    class Child:
        def __init__(self, code=None, stubborn=False):
            self.code, self.stubborn, self.events = code, stubborn, []

        def poll(self):
            return self.code

        def terminate(self):
            self.events.append("terminate")

        def wait(self, timeout=None):
            if self.stubborn and "kill" not in self.events:
                raise subprocess.TimeoutExpired("x", timeout)
            return 0

        def kill(self):
            self.events.append("kill")

    running, stubborn, gone = Child(), Child(stubborn=True), Child(code=0)

    def run(tmp, timeout, children):
        children.extend([running, stubborn, gone])
        return "x"

    monkeypatch.setattr(service, "run", run)
    monkeypatch.setattr(sys, "argv", ["probe_service.py"])
    assert service.main() == 0
    assert running.events == ["terminate"]
    assert stubborn.events == ["terminate", "kill"]
    assert gone.events == []


# ---- fake_login1.py, on a real private bus ------------------------------------------------------

gi = pytest.importorskip("gi", reason="PyGObject is not installed")
needs_dbus_daemon = pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="no dbus-daemon")

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>session</type><listen>unix:path={path}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/>
<allow own="*"/></policy></busconfig>
"""


@pytest.fixture
def bus(tmp_path):
    path = tmp_path / "bus.sock"
    config = tmp_path / "bus.conf"
    config.write_text(BUS_CONFIG.format(path=path), encoding="utf-8")
    daemon = subprocess.Popen(["dbus-daemon", "--nofork", f"--config-file={config}"])
    for _ in range(100):
        if path.exists():
            break
        time.sleep(0.05)
    else:
        daemon.kill()
        pytest.fail("the test bus did not come up")
    yield f"unix:path={path}"
    daemon.terminate()
    daemon.wait(timeout=10)


def _connection(address):
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    flags = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    return Gio.DBusConnection.new_for_address_sync(address, flags, None, None)


def _fake_login1(address):
    return subprocess.Popen(
        [sys.executable, "-W", "ignore", str(GATE / "fake_login1.py")],
        env={"PATH": "/usr/bin:/bin", "DBUS_SYSTEM_BUS_ADDRESS": address},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture
def logind(bus):
    child = _fake_login1(bus)
    assert child.stdout.readline().strip() == "fake-login1 up"
    yield bus
    child.terminate()
    child.wait(timeout=10)


NAME, PATH = "org.freedesktop.login1", "/org/freedesktop/login1"
MANAGER = "org.freedesktop.login1.Manager"
SESSION = "/org/freedesktop/login1/session/1"
PROPERTIES = "org.freedesktop.DBus.Properties"


def _call(conn, path, interface, method, args=None, signature=None):
    from gi.repository import Gio, GLib

    return conn.call_sync(
        NAME,
        path,
        interface,
        method,
        GLib.Variant(signature, args) if signature else None,
        None,
        Gio.DBusCallFlags.NONE,
        10000,
        None,
    )


@needs_dbus_daemon
def test_logind_gives_the_session_of_a_process(logind):
    answer = _call(_connection(logind), PATH, MANAGER, "GetSessionByPID", (1234,), "(u)")
    assert answer.unpack() == (SESSION,)


@needs_dbus_daemon
def test_logind_takes_a_lock_call_on_the_session_and_answers_nothing(logind):
    answer = _call(_connection(logind), SESSION, "org.freedesktop.login1.Session", "Lock")
    assert answer.unpack() == ()


@needs_dbus_daemon
def test_logind_hands_out_a_delay_lock_as_a_file_descriptor(logind):
    from gi.repository import Gio, GLib

    reply, fds = _connection(logind).call_with_unix_fd_list_sync(
        NAME,
        PATH,
        MANAGER,
        "Inhibit",
        GLib.Variant("(ssss)", ("sleep", "who", "why", "delay")),
        GLib.VariantType("(h)"),
        Gio.DBusCallFlags.NONE,
        10000,
        None,
        None,
    )
    assert reply.unpack() == (0,)
    assert fds.get_length() == 1


@needs_dbus_daemon
@pytest.mark.parametrize(
    "path, interface, name, value, type_string",
    [
        (PATH, MANAGER, "InhibitDelayMaxUSec", 5_000_000, "t"),
        (SESSION, "org.freedesktop.login1.Session", "LockedHint", False, "b"),
    ],
)
def test_logind_has_the_two_properties_the_service_reads(
    logind, path, interface, name, value, type_string
):
    answer = _call(_connection(logind), path, PROPERTIES, "Get", (interface, name), "(ss)")
    assert answer.unpack() == (value,)
    assert answer.get_child_value(0).get_variant().get_type_string() == type_string


@needs_dbus_daemon
def test_logind_that_cannot_own_its_name_says_so_and_ends_with_1(bus):
    from gi.repository import Gio, GLib

    owner = _connection(bus)
    owner.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "RequestName",
        GLib.Variant("(su)", (NAME, 0)),
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )
    child = _fake_login1(bus)
    out, err = child.communicate(timeout=30)
    owner.close_sync(None)
    assert child.returncode == 1, (out, err)
    assert "fake-login1: RequestName answered" in err
    assert "fake-login1 up" not in out
