"""Where the version shown in the settings window comes from (``slideshow_lock.version``)."""

import datetime
import importlib.metadata
import inspect
import os
import re

import pytest

from slideshow_lock import preferences, version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tree(tmp_path, pyproject):
    package = tmp_path / "slideshow_lock"
    package.mkdir()
    if pyproject is not None:
        data = pyproject if isinstance(pyproject, bytes) else pyproject.encode("utf-8")
        (tmp_path / "pyproject.toml").write_bytes(data)
    return str(package)


def _no_metadata(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)


def _metadata(monkeypatch, value):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: value)


PYPROJECT = (
    '[build-system]\nrequires = ["x"]\n\n[project]\nname = "slideshow-lock"\nversion = "7.8.9"\n'
)


def test_a_source_tree_gives_the_version_of_its_pyproject(tmp_path, monkeypatch):
    _metadata(monkeypatch, "1.0.0")  # an older package installed too: the tree beside the code wins
    assert version.program_version(_tree(tmp_path, PYPROJECT)) == "7.8.9"


def test_without_a_source_tree_the_installed_metadata_answers(tmp_path, monkeypatch):
    _metadata(monkeypatch, "2.3.4")
    assert version.program_version(_tree(tmp_path, None)) == "2.3.4"


def test_with_neither_the_answer_is_dev_and_not_an_error(tmp_path, monkeypatch):
    _no_metadata(monkeypatch)
    assert version.program_version(_tree(tmp_path, None)) == "dev"


def test_broken_metadata_is_dev_too(tmp_path, monkeypatch):
    def broken(name):
        raise RuntimeError("damaged dist-info")

    monkeypatch.setattr(importlib.metadata, "version", broken)
    assert version.program_version(_tree(tmp_path, None)) == "dev"


@pytest.mark.parametrize(
    "text",
    [
        '[project]\nname = "another-project"\nversion = "7.8.9"\n',  # not this program's file
        '[project]\nname = "slideshow-lock"\n',  # no version
        '[tool.x]\nversion = "7.8.9"\n[project]\nname = "slideshow-lock"\n',  # another table's
        b"\xff\xfe not utf-8",
    ],
    ids=["other-project", "no-version", "other-table", "not-text"],
)
def test_a_pyproject_that_does_not_say_it_is_skipped(tmp_path, monkeypatch, text):
    _metadata(monkeypatch, "2.3.4")
    assert version.program_version(_tree(tmp_path, text)) == "2.3.4"


def test_an_unreadable_pyproject_is_skipped(tmp_path, monkeypatch):
    _no_metadata(monkeypatch)
    package = _tree(tmp_path, None)
    os.mkdir(tmp_path / "pyproject.toml")  # a folder where the file should be
    assert version.program_version(package) == "dev"


def test_the_real_tree_gives_the_version_pyproject_and_the_spec_state():
    """The one number: pyproject.toml has it, the spec says the same, and the code shows it."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as handle:
        written = re.search(r'^version = "([^"]+)"$', handle.read(), re.M).group(1)
    with open(
        os.path.join(ROOT, "packaging", "fedora", "slideshow-lock.spec"), encoding="utf-8"
    ) as handle:
        spec = re.search(r"^Version:\s+(\S+)$", handle.read(), re.M).group(1)
    assert version.program_version() == written == spec


def release_problems(written, metainfo, spec):
    """Where the metainfo and the ``%changelog`` do not tell the version ``written`` the same way.

    The newest ``<release>`` of the metainfo and the newest ``%changelog`` entry are this version.
    The day of the release is the day of the ``-1`` entry of the version: a later entry of the same
    version (``-2``, a rebuild of the spec with no change upstream) is dated later on purpose, and
    the metainfo describes the upstream release, not the rebuild."""
    problems = []
    release = re.search(r'<release version="([^"]+)" date="([^"]+)"', metainfo)
    newest = re.search(r"^%changelog\n\* (\w{3} \w{3} \d\d \d{4}) .* - (\S+)-\d+$", spec, re.M)
    first = re.search(
        rf"^\* (\w{{3}} \w{{3}} \d\d \d{{4}}) .* - {re.escape(written)}-1$", spec, re.M
    )
    if not release or release.group(1) != written:
        problems.append("the newest <release> of the metainfo is not the version")
    if not newest or newest.group(2) != written:
        problems.append("the newest %changelog entry is not the version")
    if not first:
        problems.append("the %changelog has no -1 entry of the version")
    elif release:
        day = datetime.datetime.strptime(first.group(1), "%a %b %d %Y").date().isoformat()
        if release.group(2) != day:
            problems.append(f"the <release> day {release.group(2)} is not the -1 entry day {day}")
    return problems


def test_the_newest_release_of_the_metainfo_and_of_the_changelog_are_this_version_and_day():
    """RELEASING.md, "Before the tag": the version of pyproject.toml is also the newest
    ``<release>`` of the metainfo and the newest ``%changelog`` entry, and the metainfo has the day
    of the ``-1`` entry of the version (a later ``-2`` entry may be dated later)."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as handle:
        written = re.search(r'^version = "([^"]+)"$', handle.read(), re.M).group(1)
    metainfo = os.path.join(ROOT, "data", "io.github.trensoft.slideshowlock.metainfo.xml.in")
    spec = os.path.join(ROOT, "packaging", "fedora", "slideshow-lock.spec")
    with open(metainfo, encoding="utf-8") as handle_m, open(spec, encoding="utf-8") as handle_s:
        assert release_problems(written, handle_m.read(), handle_s.read()) == []


METAINFO_TEXT = '<releases>\n  <release version="1.2.3" date="2026-10-09">\n</releases>\n'
SPEC_TEXT = (
    "%changelog\n* {top} X <x@y.z> - 1.2.3-{rel}\n- change\n\n"
    "* Fri Oct 09 2026 X <x@y.z> - 1.2.3-1\n- first\n\n"
    "* Fri Oct 02 2026 X <x@y.z> - 1.2.2-1\n- older\n"
)


@pytest.mark.parametrize(
    "top, rel",
    [("Fri Oct 09 2026", 1), ("Mon Oct 19 2026", 2), ("Tue Oct 20 2026", 3)],
    ids=["same-day", "later-rebuild", "second-rebuild"],
)
def test_a_later_release_of_the_spec_does_not_move_the_day_of_the_upstream_release(top, rel):
    spec = SPEC_TEXT.format(top=top, rel=rel)
    if rel == 1:  # the only -1 entry is the newest one
        spec = spec.replace("* Fri Oct 09 2026 X <x@y.z> - 1.2.3-1\n- first\n\n", "")
    assert release_problems("1.2.3", METAINFO_TEXT, spec) == []


@pytest.mark.parametrize(
    "metainfo, spec, fragment",
    [
        # the -1 entry has another day than the metainfo, newest entry or not
        (METAINFO_TEXT, SPEC_TEXT.format(top="Mon Oct 19 2026", rel=2).replace(
            "Fri Oct 09 2026", "Thu Oct 08 2026"), "-1 entry day"),
        (METAINFO_TEXT.replace("2026-10-09", "2026-10-10"), SPEC_TEXT.format(
            top="Mon Oct 19 2026", rel=2), "-1 entry day"),
        # the metainfo and the changelog do not tell the version
        (METAINFO_TEXT.replace("1.2.3", "1.2.2"), SPEC_TEXT.format(top="Mon Oct 19 2026", rel=2),
         "<release> of the metainfo"),
        (METAINFO_TEXT, SPEC_TEXT.format(top="Mon Oct 19 2026", rel=2).replace(
            "- 1.2.3-2", "- 1.2.4-1"), "newest %changelog"),
        # there is no -1 entry to compare with
        (METAINFO_TEXT, SPEC_TEXT.format(top="Mon Oct 19 2026", rel=2).replace(
            "- 1.2.3-1", "- 1.2.3-9"), "no -1 entry"),
    ],
    ids=["first-entry-day", "metainfo-day", "metainfo-version", "changelog-version", "no-first"],
)  # fmt: skip
def test_the_release_check_sees_a_day_or_a_version_that_does_not_match(metainfo, spec, fragment):
    problems = release_problems("1.2.3", metainfo, spec)
    assert any(fragment in problem for problem in problems), problems


def test_no_version_number_is_written_in_the_code():
    """No second copy to keep: the modules that show the version contain no version literal."""
    for module in (version, preferences):
        source = inspect.getsource(module)
        assert not re.search(r"""["']\d+\.\d+\.\d+["']""", source), module.__name__


def test_the_line_is_the_version_and_the_maker(monkeypatch):
    monkeypatch.setattr(preferences, "program_version", lambda: "1.0.10")
    assert preferences.version_text() == "1.0.10 by TrenSoft"
    monkeypatch.undo()
    assert preferences.version_text() == version.program_version() + " by TrenSoft"


def test_the_window_shows_it_small_and_faint_at_the_end_of_the_footer():
    """The constructor cannot run here, so its statements are read (the real placement is the
    smoke test's): the label is made from ``version_text()`` (the version of the package, "by"
    and the maker), is dim, and is the last thing put in the footer."""
    source = inspect.getsource(preferences.PreferencesWindow.__init__)
    assert "label=version_text()" in source
    assert 'self.version_label.add_css_class("dim-label")' in source
    appended = re.findall(r"footer\.append\(([^)]*)\)", source)
    assert appended == [
        "self.preview_button",
        "self.donate_button",
        "self.status",
        "self.version_label",
    ]
