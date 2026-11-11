"""``loop.unix_signal_add``: the Unix signal watch of the two programs.

``GLib.unix_signal_add`` is gone from the ``GLib`` typelib of glib 2.88 (Fedora 44, rawhide, the
ubuntu-26.04 runner of the CI), where ``GLibUnix.signal_add`` is the only place; Fedora 43 and EL10
have both. On Fedora 44 and rawhide pygobject keeps the old name working with a deprecation warning
(measured, 2026-11-11); the ubuntu-26.04 runner has no such wrapper. The helper takes the new place
where there is one and the old one where there is not, and answers 0 with a warning where there is
neither.
"""

from __future__ import annotations

import builtins
import logging
import signal
import time
from types import SimpleNamespace

from gi.repository import GLib

from slideshow_lock import loop
from tests.timeout_guard import per_test_deadline  # noqa: F401  (autouse fixture)


def _callback():
    return GLib.SOURCE_REMOVE


def test_the_new_place_is_used_where_there_is_one(monkeypatch):
    calls = []
    unix = SimpleNamespace(signal_add=lambda *args: calls.append(("new", args)) or 7)
    old = SimpleNamespace(unix_signal_add=lambda *args: calls.append(("old", args)) or 8)
    monkeypatch.setattr(loop, "_glib_unix", lambda: unix)
    monkeypatch.setattr(loop, "GLib", old)
    assert loop.unix_signal_add(0, signal.SIGUSR1, _callback) == 7
    assert calls == [("new", (0, signal.SIGUSR1, _callback))]


def test_the_old_place_is_used_where_there_is_no_glibunix(monkeypatch):
    calls = []
    old = SimpleNamespace(unix_signal_add=lambda *args: calls.append(args) or 8)
    monkeypatch.setattr(loop, "_glib_unix", lambda: None)
    monkeypatch.setattr(loop, "GLib", old)
    assert loop.unix_signal_add(0, signal.SIGUSR1, _callback) == 8
    assert calls == [(0, signal.SIGUSR1, _callback)]


def test_glib_without_the_old_name_works_through_glibunix(monkeypatch):
    """glib 2.88: the ``GLib`` module has no ``unix_signal_add`` at all."""
    unix = SimpleNamespace(signal_add=lambda priority, signum, callback: 9)
    monkeypatch.setattr(loop, "_glib_unix", lambda: unix)
    monkeypatch.setattr(loop, "GLib", SimpleNamespace())
    assert loop.unix_signal_add(0, signal.SIGUSR1, _callback) == 9


def test_glib_with_no_signal_watch_at_all_warns_and_answers_zero(monkeypatch, caplog):
    monkeypatch.setattr(loop, "_glib_unix", lambda: None)
    monkeypatch.setattr(loop, "GLib", SimpleNamespace())
    with caplog.at_level(logging.WARNING, logger=loop.__name__):
        assert loop.unix_signal_add(0, signal.SIGUSR1, _callback) == 0
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert str(int(signal.SIGUSR1)) in caplog.records[0].getMessage()


def test_a_namespace_the_typelib_lacks_is_none(monkeypatch):
    def refuse(name, version):
        raise ValueError("Namespace %s not available" % name)

    monkeypatch.setattr(loop.gi, "require_version", refuse)
    assert loop._glib_unix() is None


def test_a_namespace_that_cannot_be_imported_is_none_too(monkeypatch):
    """The version is accepted but the import of ``GLibUnix`` fails: the old place is the way."""
    real_import = builtins.__import__

    def refusing(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "gi.repository" and "GLibUnix" in (fromlist or ()):
            raise ImportError("cannot import name 'GLibUnix'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(loop.gi, "require_version", lambda name, version: None)
    monkeypatch.setattr(builtins, "__import__", refusing)
    assert loop._glib_unix() is None


def test_a_signal_reaches_the_callback_on_the_main_loop_and_the_source_goes():
    """The real thing, on whichever glib this is: the callback runs on the loop, once, and the
    source is gone after it returned ``SOURCE_REMOVE``."""
    heard = []
    context = GLib.MainContext.default()
    source_id = loop.unix_signal_add(
        GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: heard.append(1) or GLib.SOURCE_REMOVE
    )
    assert source_id > 0
    signal.raise_signal(signal.SIGUSR1)
    deadline = time.monotonic() + 2.0
    while not heard and time.monotonic() < deadline:
        context.iteration(False)
    assert heard == [1]
    assert context.find_source_by_id(source_id) is None
