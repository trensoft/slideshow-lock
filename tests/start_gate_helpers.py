"""Helpers of the tests that run the scripts of the start gate (``tools/start_gate``).

The scripts are made for a container: they read ``/gate``, ``/src`` and ``/out`` and call ``dnf``,
``rpm``, ``rpmbuild`` and ``make``. Here a script is copied with those three paths pointed into a
temporary directory and run with a ``PATH`` that holds only stand-ins and a few real tools, so what
is measured is what the script does with the answers of the commands: which commands it calls, with
which arguments, and which exit status it ends with.
"""

from __future__ import annotations

import re
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "tools" / "start_gate"
BASH = shutil.which("bash") or "/bin/bash"
# the real tools the scripts call besides the stand-ins
REAL_TOOLS = (
    "basename",
    "chmod",
    "cp",
    "cut",
    "dirname",
    "ls",
    "mkdir",
    "mktemp",
    "rm",
    "sha256sum",
)
NEVRA = "slideshow-lock-1.2.3-1.fc43.noarch"


def point_paths(text: str, mapping: dict) -> str:
    """The script text with ``/gate``, ``/src`` and ``/out`` (a whole path word) moved."""
    for old, new in mapping.items():
        text = re.sub(r"(?<![\w./-])" + re.escape(old) + r"(?=[/\s\"'])", str(new), text)
    return text


def stub(path: Path, body: str) -> Path:
    path.unlink(missing_ok=True)  # a symlink to a real tool must not be written through
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Box:
    """A directory with ``gate``, ``src``, ``out``, ``bin`` (the only ``PATH``) and a call log."""

    def __init__(self, root: Path):
        self.root = root
        self.gate = root / "gate"
        self.src = root / "src"
        self.out = root / "out"
        self.bin = root / "bin"
        self.tmp = root / "tmp"
        self.log = root / "calls.log"
        for directory in (self.gate, self.src, self.out, self.bin, self.tmp):
            directory.mkdir()
        self.log.write_text("", encoding="utf-8")
        for name in REAL_TOOLS:
            (self.bin / name).symlink_to(shutil.which(name))
        # the shared file of the scripts: no repositories, a dnf that logs and can be made to fail
        (self.gate / "common.sh").write_text(
            'gate_repos() { echo "gate_repos" >> "$STUB_LOG"; }\n'
            'gate_dnf() { echo "dnf $*" >> "$STUB_LOG"; [ "${STUB_DNF_RC:-0}" -eq 0 ]; }\n',
            encoding="utf-8",
        )

    def script(self, name: str) -> Path:
        """Copy ``tools/start_gate/<name>`` here with the container paths pointed into the box."""
        text = (GATE / name).read_text(encoding="utf-8")
        target = self.gate / name
        mapping = {"/gate": self.gate, "/src": self.src, "/out": self.out}
        target.write_text(point_paths(text, mapping), encoding="utf-8")
        return target

    def run(self, script: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
        full_env = {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "TMPDIR": str(self.tmp),
            "STUB_LOG": str(self.log),
        }
        full_env.update(env)
        return subprocess.run(
            [BASH, str(script), *args],
            env=full_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self) -> list:
        return self.log.read_text(encoding="utf-8").splitlines()


def make_tree(path: Path) -> Path:
    """The smallest tree ``start_gate.sh`` accepts: it has a ``.copr/Makefile``."""
    (path / ".copr").mkdir(parents=True)
    (path / ".copr" / "Makefile").write_text("srpm:\n", encoding="utf-8")
    return path


def fake_engine(path: Path, stage_output: dict, stage_rc: dict) -> Path:
    """A stand-in for podman/docker. It logs its arguments to ``$STUB_LOG`` and, by the script
    that the container would run (``build.sh`` or ``check.sh``), prints and ends as told."""
    cases = ""
    for script in ("build.sh", "check.sh"):
        text = shlex.quote(stage_output.get(script, ""))
        cases += f"    */{script}) printf '%s\\n' {text}; exit {stage_rc.get(script, 0)} ;;\n"
    return stub(
        path,
        'echo "engine $*" >> "$STUB_LOG"\n'
        "for last; do :; done\n"
        'case "$last" in\n' + cases + "esac\n"
        "exit 99\n",
    )
