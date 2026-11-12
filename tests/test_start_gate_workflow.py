"""Tests for the workflows of the start gate (``.github/workflows/start-gate.yml`` and
``negative-control.yml``).

The legs of the matrix of the first must be the chroot list that ``docs/RELEASING.md`` names as the
chroots of the COPR project, and it must run on the events a tag depends on. The lines that make it
a hard gate are checked too: no ``continue-on-error``, no leg that stops the others, a time limit on
every job, no token kept in ``.git/config``. The second is the negative control (the tree of 1.0.11
must make the gate fail): it is a file of its own, run on a pull request that changes the gate and
by hand, and it leaves no check named like a leg of the gate on a commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "start-gate.yml"
NEGATIVE = REPO / ".github" / "workflows" / "negative-control.yml"
#: the commit of the tag v1.0.11 (the tag can be moved, the commit cannot)
V1_0_11 = "adb4c224d32bebbda3a4f76a20d41ce9a7d338b6"
RELEASING = REPO / "docs" / "RELEASING.md"
#: a line that sets an ``if`` key, also as the first key of a step (``- if:``) or quoted
IF_KEY = re.compile(r"^\s*(?:-\s+)?[\"']?if[\"']?\s*:", re.M)


def _text(path: Path = WORKFLOW) -> str:
    return path.read_text(encoding="utf-8")


def _matrix_chroots() -> list:
    return re.findall(r"^\s+- chroot: (\S+)\s*$", _text(), re.M)


def _documented_chroots() -> list:
    for line in RELEASING.read_text(encoding="utf-8").splitlines():
        if line.startswith("Start gate chroots"):
            return re.findall(r"`((?:epel|fedora)-[a-z0-9]+)`", line)
    raise AssertionError("docs/RELEASING.md has no line that starts with 'Start gate chroots'")


def test_matrix_is_the_documented_copr_chroot_list():
    matrix = _matrix_chroots()
    assert matrix, "no leg in the matrix of the workflow"
    assert len(set(matrix)) == len(matrix), f"a chroot twice in the matrix: {matrix}"
    assert sorted(matrix) == sorted(_documented_chroots())


def test_epel_10_leg_comes_first():
    assert _matrix_chroots()[0] == "epel-10"


def test_every_leg_has_an_image_with_a_fixed_tag():
    pairs = re.findall(r"- chroot: (\S+)\n\s+image: (\S+)", _text())
    assert [chroot for chroot, _ in pairs] == _matrix_chroots()
    for chroot, image in pairs:
        assert ":" in image and "latest" not in image, f"{chroot}: the image has no tag: {image}"


def test_workflow_runs_on_pull_requests_main_and_tags():
    text = _text()
    assert re.search(r"^on:\n(?:.*\n)*?  pull_request:", text, re.M)
    assert re.search(r"^    branches: \[main\]", text, re.M)
    assert re.search(r"^    tags: \[\"v\*\"\]", text, re.M)


def test_no_leg_stops_the_others():
    assert re.search(r"^      fail-fast: false$", _text(), re.M)


def _jobs(path: Path = WORKFLOW) -> dict:
    """The text of every job of a workflow, by the name of the job."""
    head, _, rest = _text(path).partition("\njobs:\n")
    assert rest, "the workflow has no jobs"
    parts = re.split(r"^  ([a-z][a-z-]*):\n", rest, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def test_the_gate_workflow_has_the_gate_job_and_nothing_else():
    assert sorted(_jobs()) == ["start-gate"]
    assert sorted(_jobs(NEGATIVE)) == ["negative-control"]


def test_the_gate_workflow_has_no_run_by_hand_and_no_condition_on_a_job():
    """A leg that is skipped leaves a check named ``start gate (${{ matrix.chroot }})`` on the
    commit, which ``verify_checks.sh`` takes for a leg with no chroot and fails on. So the gate
    has no job-level ``if``, and the run by hand lives in the other file."""
    text = _text()
    assert not IF_KEY.search(text)
    assert "workflow_dispatch" not in text
    assert "github.event_name" not in text


def test_no_job_may_fail_without_failing_the_check():
    assert "continue-on-error" not in _text()
    assert "continue-on-error" not in _text(NEGATIVE)


@pytest.mark.parametrize("path", [WORKFLOW, NEGATIVE], ids=lambda p: p.name)
def test_every_job_has_a_time_limit_that_is_a_limit(path):
    for name, job in _jobs(path).items():
        limits = re.findall(r"^    timeout-minutes: (\d+)$", job, re.M)
        assert len(limits) == 1, f"{name}: no timeout-minutes"
        assert 1 <= int(limits[0]) <= 60, f"{name}: timeout-minutes {limits[0]}"


@pytest.mark.parametrize("path", [WORKFLOW, NEGATIVE], ids=lambda p: p.name)
def test_the_workflow_token_only_reads_in_every_workflow(path):
    assert re.search(r"^permissions:\n  contents: read$", _text(path), re.M)


@pytest.mark.parametrize(
    "path, count", [(WORKFLOW, 1), (NEGATIVE, 2)], ids=["start-gate.yml", "negative-control.yml"]
)
def test_the_token_is_not_kept_in_the_checkout(path, count):
    """The code under test runs in containers that read the checkout: the token of the job is not
    left in ``.git/config`` (``persist-credentials: false`` on every checkout)."""
    text = _text(path)
    checkouts = re.findall(
        r"uses: actions/checkout@v4\n((?:        with:\n(?:          .*\n)*)?)", text
    )
    assert len(checkouts) == text.count("actions/checkout")
    assert len(checkouts) == count
    for options in checkouts:
        assert "persist-credentials: false" in options


def _run_lines(path: Path) -> list:
    """The whole text after ``run:`` of every step of a workflow: a line that only starts with the
    right command (``... || true``, ``...; exit 0``) is another line."""
    return re.findall(r"^\s+(?:- )?run: (.*)$", _text(path), re.M)


def _engines(path: Path) -> list:
    return re.findall(r"^\s+START_GATE_ENGINE: (.*)$", _text(path), re.M)


def test_the_gate_job_runs_the_gate_script_on_the_image_of_its_leg():
    assert _run_lines(WORKFLOW) == ['bash tools/start_gate/start_gate.sh "${{ matrix.image }}"']
    assert "    name: start gate (${{ matrix.chroot }})\n" in _jobs()["start-gate"]


@pytest.mark.parametrize("path", [WORKFLOW, NEGATIVE], ids=lambda p: p.name)
def test_the_engine_of_the_gate_is_docker_and_nothing_else(path):
    """``START_GATE_ENGINE`` names the program that runs the containers: ``true`` would run
    nothing and end with 0, and the gate would be green without having started anything."""
    assert _engines(path) == ["docker"]
    assert len(_run_lines(path)) == 1


@pytest.mark.parametrize("path", [WORKFLOW, NEGATIVE], ids=lambda p: p.name)
def test_no_step_of_a_workflow_of_the_gate_may_be_skipped(path):
    """A step with an ``if:`` (``if: false``) is skipped and the job is green: the gate file has
    none anywhere, and the control has none either."""
    assert not IF_KEY.search(_text(path))


def test_the_negative_control_runs_on_a_pull_request_that_changes_the_gate_and_by_hand():
    """The control runs when the gate changes: on a pull request that touches the gate scripts or
    one of the two workflows (``paths``), and by hand (``workflow_dispatch``), nothing else."""
    text = _text(NEGATIVE)
    assert re.search(
        r"^on:\n"
        r"  pull_request:\n"
        r"    paths:\n"
        r"      - tools/start_gate/\*\*\n"
        r"      - \.github/workflows/start-gate\.yml\n"
        r"      - \.github/workflows/negative-control\.yml\n"
        r"  workflow_dispatch:\n\n",
        text,
        re.M,
    )
    assert not re.search(r"^\s+(?:push|schedule|workflow_run|pull_request_target):", text, re.M)


def test_the_negative_control_has_no_condition_on_a_job():
    """A skipped job would leave a check on the commit; the paths filter decides whether the
    workflow starts at all."""
    assert not IF_KEY.search(_text(NEGATIVE))
    assert "github.event_name" not in _text(NEGATIVE)


def test_no_job_of_the_negative_control_is_named_like_a_leg_of_the_gate():
    """``verify_checks.sh`` counts every check named ``start gate (...)``: the control must not
    leave one on the commit."""
    for workflow in (WORKFLOW.parent).glob("*.yml"):
        if workflow == WORKFLOW:
            continue
        for job in re.findall(r"^    name: (.*)$", _text(workflow), re.M):
            assert not job.lower().startswith("start gate"), f"{workflow.name}: {job}"


def test_the_negative_control_runs_on_the_commit_of_1_0_11_not_on_a_tag():
    """The tree of 1.0.11 is checked out by the SHA of its commit: a tag can be moved to the fixed
    tree, and the control would then run on a tree that passes and call the gate soft."""
    job = _jobs(NEGATIVE)["negative-control"]
    refs = re.findall(r"^          ref: (\S+)$", job, re.M)
    assert refs == [V1_0_11]
    assert "path: v1.0.11" in job
    assert not re.search(r"ref: v\d", job)
    assert not re.search(r"ref: \S*(?:main|master|HEAD)", job)


def test_the_negative_control_runs_the_control_script_on_the_epel_10_image():
    job = _jobs(NEGATIVE)["negative-control"]
    assert _run_lines(NEGATIVE) == [
        "bash tools/start_gate/negative_control.sh quay.io/centos/centos:stream10 v1.0.11"
    ]
    images = dict(re.findall(r"- chroot: (\S+)\n\s+image: (\S+)", _text()))
    assert images["epel-10"] == "quay.io/centos/centos:stream10"
    assert "negative_control.sh " + images["epel-10"] + " " in job
    assert "    name: negative control (the gate must fail on 1.0.11)\n" in job


def test_the_releasing_file_names_the_commit_of_the_control():
    text = RELEASING.read_text(encoding="utf-8")
    assert V1_0_11 in text, "docs/RELEASING.md does not name the commit the control runs on"
    assert ".github/workflows/negative-control.yml" in text
