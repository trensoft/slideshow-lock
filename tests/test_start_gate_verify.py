"""``tools/start_gate/verify_checks.sh``: the tag condition checked by a machine.

The script reads the chroots of the COPR project and the check runs of a commit from two APIs. Here
both are a local HTTP server that answers what the test says, so every way the answer can be wrong
(a red leg, a missing leg, a leg for a chroot the project has not, a project with a chroot that has
no leg, an API that does not answer) is run, and the script must end with a non-zero status.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.start_gate_helpers import GATE
from tests.timeout_guard import per_test_deadline  # noqa: F401  (autouse fixture)

SHA = "0123456789abcdef0123456789abcdef01234567"
CHROOTS = ["epel-10", "fedora-43", "fedora-44", "fedora-45", "fedora-rawhide"]
COPR_PATH = "/api_3/project/"
CHECKS_PATH = f"/repos/trensoft/slideshow-lock/commits/{SHA}/check-runs"

pytestmark = [
    pytest.mark.spawns_processes,
    pytest.mark.skipif(shutil.which("curl") is None, reason="curl is not installed"),
]


def copr_answer(chroots=CHROOTS, arches=("x86_64",)):
    return {"chroot_repos": {f"{name}-{arch}": "https://x" for name in chroots for arch in arches}}


def run_of(chroot, status="completed", conclusion="success", app="github-actions"):
    return {
        "name": f"start gate ({chroot})",
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": app},
    }


def checks_answer(runs):
    return {"total_count": len(runs), "check_runs": runs}


def all_green():
    return checks_answer([run_of(chroot) for chroot in CHROOTS])


class Server:
    def __init__(self):
        self.answers = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status, body = owner.answers.get(self.path.split("?")[0], (404, {}))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def api():
    server = Server()
    server.answers[COPR_PATH] = (200, copr_answer())
    server.answers[CHECKS_PATH] = (200, all_green())
    yield server
    server.close()


def verify(api, sha=SHA, **env):
    full_env = {
        "PATH": "/usr/bin:/bin",
        "START_GATE_COPR_API": api.url + "/api_3",
        "START_GATE_GITHUB_API": api.url,
    }
    full_env.update(env)
    return subprocess.run(
        ["bash", str(GATE / "verify_checks.sh"), sha],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_all_five_legs_green_is_a_pass(api):
    result = verify(api)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1].startswith(f"VERIFY-CHECKS PASS {SHA}")
    for chroot in CHROOTS:
        assert chroot in result.stdout


def test_the_architectures_of_a_chroot_are_one_chroot(api):
    api.answers[COPR_PATH] = (200, copr_answer(arches=("x86_64", "aarch64")))
    assert verify(api).returncode == 0


@pytest.mark.parametrize(
    "bad",
    [
        run_of("fedora-43", conclusion="failure"),
        run_of("fedora-43", conclusion="cancelled"),
        run_of("fedora-43", conclusion="skipped"),
        run_of("fedora-43", conclusion="neutral"),
        run_of("fedora-43", conclusion=None),
        run_of("fedora-43", status="in_progress", conclusion=None),
        run_of("fedora-43", status="queued", conclusion=None),
        run_of("fedora-43", app="some-other-app"),
    ],
)
def test_a_leg_that_is_not_a_success_of_github_actions_fails(api, bad):
    runs = [bad if run["name"] == bad["name"] else run for run in all_green()["check_runs"]]
    api.answers[CHECKS_PATH] = (200, checks_answer(runs))
    result = verify(api)
    assert result.returncode == 1
    assert "VERIFY-CHECKS FAIL" in result.stdout
    assert "fedora-43" in result.stdout + result.stderr


def test_a_missing_leg_fails(api):
    runs = [run_of(chroot) for chroot in CHROOTS if chroot != "epel-10"]
    api.answers[CHECKS_PATH] = (200, checks_answer(runs))
    result = verify(api)
    assert result.returncode == 1
    assert "epel-10: 0 checks" in result.stderr


def test_no_check_at_all_fails(api):
    api.answers[CHECKS_PATH] = (200, checks_answer([]))
    assert verify(api).returncode == 1


def test_a_chroot_of_the_copr_project_with_no_leg_fails(api):
    api.answers[COPR_PATH] = (200, copr_answer(CHROOTS + ["fedora-46"]))
    result = verify(api)
    assert result.returncode == 1
    assert "fedora-46: 0 checks" in result.stderr


def test_a_leg_for_a_chroot_the_copr_project_has_not_fails(api):
    api.answers[COPR_PATH] = (200, copr_answer([c for c in CHROOTS if c != "fedora-43"]))
    result = verify(api)
    assert result.returncode == 1
    assert "fedora-43: a leg with no chroot in the COPR project" in result.stderr


def test_the_chroot_list_comes_from_the_copr_api_not_from_a_file(api, tmp_path):
    """A copy of the script run from another directory has no docs/RELEASING.md next to it: the
    answer is the same, because the chroots are not read from the tree."""
    copy = tmp_path / "verify_checks.sh"
    shutil.copy(GATE / "verify_checks.sh", copy)
    env = {
        "PATH": "/usr/bin:/bin",
        "START_GATE_COPR_API": api.url + "/api_3",
        "START_GATE_GITHUB_API": api.url,
    }
    result = subprocess.run(
        ["bash", str(copy), SHA], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("which", ["copr", "github"])
@pytest.mark.parametrize("status", [404, 500])
def test_an_api_that_answers_with_an_error_fails(api, which, status):
    """The status counts, not the body: an error page that happens to look like an answer (a proxy,
    a cache) must not be read as one."""
    good = copr_answer() if which == "copr" else all_green()
    api.answers[COPR_PATH if which == "copr" else CHECKS_PATH] = (status, good)
    result = verify(api)
    assert result.returncode == 1
    assert "VERIFY-CHECKS FAIL" in result.stdout


@pytest.mark.parametrize("which", ["copr", "github"])
def test_an_api_that_is_not_there_fails(api, which):
    key = "START_GATE_COPR_API" if which == "copr" else "START_GATE_GITHUB_API"
    result = verify(api, **{key: "http://127.0.0.1:1"})
    assert result.returncode == 1
    assert "VERIFY-CHECKS FAIL" in result.stdout


@pytest.mark.parametrize("body", [{}, {"chroot_repos": {}}, {"chroot_repos": ["epel-10-x86_64"]}])
def test_a_copr_answer_with_no_chroots_fails(api, body):
    api.answers[COPR_PATH] = (200, body)
    assert verify(api).returncode == 1


def test_an_incomplete_list_of_checks_fails(api):
    answer = all_green()
    answer["total_count"] = 130
    api.answers[CHECKS_PATH] = (200, answer)
    assert verify(api).returncode == 1


def test_two_checks_of_one_name_fail(api):
    api.answers[CHECKS_PATH] = (200, checks_answer(all_green()["check_runs"] + [run_of("epel-10")]))
    assert verify(api).returncode == 1


@pytest.mark.parametrize("sha", ["", "main", "v1.0.12", SHA[:7], SHA.upper(), SHA + "0"])
def test_only_a_full_commit_sha_is_accepted(api, sha):
    assert verify(api, sha=sha).returncode == 2


def test_the_token_is_sent_when_there_is_one(api):
    seen = []
    original = api.httpd.RequestHandlerClass.do_GET

    def spy(handler):
        seen.append(handler.headers.get("Authorization"))
        return original(handler)

    api.httpd.RequestHandlerClass.do_GET = spy
    verify(api, GITHUB_TOKEN="t0ken")
    assert "Bearer t0ken" in seen
