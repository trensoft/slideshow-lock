"""Start gate, steps (a) to (c), on the INSTALLED package.

(a) the installed slideshow-lock has the version the RPM was built with, and it is the copy in the
    system site-packages, not a checkout;
(b) ``slideshow_lock.service`` and ``slideshow_lock.preview_app`` import from there, with the
    typelibs the RPM pulled in;
(c) the installed ``loop.unix_signal_add`` is registered in a real GLib main loop and a real SIGTERM
    and a real SIGINT arrive in it. This is the call that 1.0.11 broke on EL10 and Fedora 43
    (``GLibUnix`` has no ``signal_add`` there): an import check and a typelib check do not run it.

usage: python3 -I probe_installed.py EXPECTED_VERSION
Prints one line per step, ``START-GATE <step> PASS|FAIL <detail>``; exits 1 when a step failed.
"""

import importlib
import importlib.metadata
import os
import signal
import sys
import warnings


def step_installed(expected: str) -> str:
    version = importlib.metadata.version("slideshow-lock")
    if version != expected:
        raise AssertionError(f"installed version {version}, the RPM has {expected}")
    module = importlib.import_module("slideshow_lock")
    path = os.path.realpath(module.__file__)
    if not path.startswith(os.path.realpath(sys.base_prefix) + os.sep):
        raise AssertionError(f"slideshow_lock is loaded from {path}, not from {sys.base_prefix}")
    return f"version {version} from {path}"


def step_import() -> str:
    service = importlib.import_module("slideshow_lock.service")
    importlib.import_module("slideshow_lock.preview_app")
    return f"service and preview_app import (service at {service.__file__})"


def step_signal() -> str:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    from slideshow_lock import loop

    arrived: list[int] = []
    main = GLib.MainLoop()

    def watcher(signum: int):
        def callback() -> bool:
            arrived.append(signum)
            if len(arrived) == 2:
                main.quit()
            return GLib.SOURCE_REMOVE

        return callback

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ids = [
            loop.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, watcher(signum))
            for signum in (signal.SIGTERM, signal.SIGINT)
        ]
    if 0 in ids:
        raise AssertionError(f"no Unix signal watch was made (ids {ids})")

    def send(signum: int):
        def fire() -> bool:
            os.kill(os.getpid(), signum)
            return GLib.SOURCE_REMOVE

        return fire

    GLib.timeout_add(200, send(signal.SIGTERM))
    GLib.timeout_add(400, send(signal.SIGINT))
    GLib.timeout_add(5000, main.quit)
    main.run()
    names = sorted(signal.Signals(s).name for s in arrived)
    if names != ["SIGINT", "SIGTERM"]:
        raise AssertionError(f"signals that arrived in the main loop: {names}")
    warned = [
        f"{w.category.__name__}: {str(w.message)[:90]}"
        for w in caught
        if "signal" in str(w.message).lower()
    ]
    glib = f"glib {GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}"
    return f"SIGTERM and SIGINT arrived, {glib}, warnings {warned}"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    expected = sys.argv[1]
    steps = (
        ("a", lambda: step_installed(expected)),
        ("b", step_import),
        ("c", step_signal),
    )
    failed = False
    for name, run in steps:
        try:
            print(f"START-GATE {name} PASS {run()}", flush=True)
        except Exception as exc:  # every kind of failure is a failed step
            failed = True
            print(f"START-GATE {name} FAIL {type(exc).__name__}: {exc}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
