"""Start gate, step (d): the real ``slideshow-lock service`` of the installed package.

It must come up to its main loop and end with exit status 0 on SIGTERM. There is no desktop in a
container, so the program gets a private D-Bus session bus and a private "system" bus with
``fake_login1.py`` on it as logind. What is not there (the GNOME Shell and mutter interfaces) the
service logs as an error and still runs: that is not a failure of this step. A traceback in the
log, an exit before the main loop, no exit within the time, or an exit status other than 0 is.

usage: python3 -I probe_service.py [--timeout SECONDS]
Prints ``START-GATE d PASS|FAIL <detail>``; exits 1 on FAIL. The log of the service is printed
before that line.
"""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={path}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""
# No service directory is listed, so the bus starts no program of the installed GNOME packages.


def start_bus(tmp: str, name: str) -> tuple[subprocess.Popen, str]:
    path = os.path.join(tmp, f"{name}.sock")
    config = os.path.join(tmp, f"{name}.conf")
    with open(config, "w", encoding="utf-8") as fh:
        fh.write(BUS_CONFIG.format(path=path))
    proc = subprocess.Popen(["dbus-daemon", "--nofork", f"--config-file={config}"])
    for _ in range(100):
        if os.path.exists(path):
            return proc, f"unix:path={path}"
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    raise RuntimeError(f"the {name} bus did not come up")


def wait_for(log_path: str, text: str, proc: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            if text in fh.read():
                return True
        if proc.poll() is not None:
            return False
        time.sleep(0.2)
    return False


def run(tmp: str, timeout: float, children: list) -> str:
    home = os.path.join(tmp, "home")
    runtime = os.path.join(tmp, "xdg")
    os.makedirs(home)
    os.makedirs(runtime, mode=0o700)
    session_proc, session = start_bus(tmp, "session")
    children.append(session_proc)
    system_proc, system = start_bus(tmp, "system")
    children.append(system_proc)
    env = dict(
        os.environ,
        HOME=home,
        XDG_RUNTIME_DIR=runtime,
        GSETTINGS_BACKEND="memory",
        DBUS_SESSION_BUS_ADDRESS=session,
        DBUS_SYSTEM_BUS_ADDRESS=system,
        LANG="C.UTF-8",
        PYTHONWARNINGS="ignore",
    )
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        env.pop(key, None)
    login = subprocess.Popen(
        [sys.executable, "-I", os.path.join(HERE, "fake_login1.py")],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    children.append(login)
    if login.stdout.readline().strip() != "fake-login1 up":
        raise RuntimeError("fake_login1.py did not come up")

    log_path = os.path.join(tmp, "service.log")
    with open(log_path, "w", encoding="utf-8") as log:
        svc = subprocess.Popen(
            ["slideshow-lock", "service", "--debug"], env=env, stdout=log, stderr=subprocess.STDOUT
        )
    children.append(svc)
    try:
        if not wait_for(log_path, "service running", svc, timeout):
            code = svc.poll()
            if code is not None:
                raise RuntimeError(
                    f"the service ended with exit status {code} before its main loop"
                )
            raise RuntimeError(f"the service did not reach its main loop in {timeout:.0f} s")
        time.sleep(2)
        if svc.poll() is not None:
            raise RuntimeError(f"the service ended by itself with exit status {svc.returncode}")
        svc.send_signal(signal.SIGTERM)
        try:
            code = svc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"the service did not end within {timeout:.0f} s of SIGTERM"
            ) from None
        if code != 0:
            raise RuntimeError(f"the service ended with exit status {code} on SIGTERM")
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if "service stopped" not in text:
            raise RuntimeError('no "service stopped" line in the log')
        if "Traceback" in text:
            raise RuntimeError("there is a traceback in the log of the service")
        return "the service reached the main loop, ran 2 s, and ended with exit status 0 on SIGTERM"
    finally:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        print("---- service log ----", flush=True)
        print(text.rstrip(), flush=True)
        print("---- end of the service log ----", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    children: list = []
    with tempfile.TemporaryDirectory(prefix="start-gate-") as tmp:
        try:
            detail = run(tmp, args.timeout, children)
        except Exception as exc:  # every kind of failure is a failed step
            print(f"START-GATE d FAIL {type(exc).__name__}: {exc}", flush=True)
            return 1
        finally:
            for proc in reversed(children):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
    print(f"START-GATE d PASS {detail}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
