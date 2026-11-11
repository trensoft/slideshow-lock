# SPDX-FileCopyrightText: 2026 TrenSoft
# SPDX-License-Identifier: GPL-3.0-or-later

"""Posting a function to a GLib main context, from any thread (CORE-1), and the Unix signal watch.

The service has two loops: the main one (GTK, the idle path, the slideshow) and the sleep
guard's own (``sleep_guard.GuardThread``). ``current_poster()`` captures the loop of the
calling thread, so a callback can later be handed back to it from another thread without
waiting: a post only queues the function, it never runs it in the caller, and it does not
block if the target loop is busy or stuck.
"""

from __future__ import annotations

import logging
from typing import Callable

import gi

gi.require_version("GLib", "2.0")

from gi.repository import GLib  # noqa: E402

_LOG = logging.getLogger(__name__)

Poster = Callable[[Callable[[], None]], None]


def poster_for(context: "GLib.MainContext") -> Poster:
    """A function that runs what it is given once, later, on *context*."""

    def post(fn: Callable[[], None]) -> None:
        def run(*_user_data) -> bool:
            fn()
            return GLib.SOURCE_REMOVE

        source = GLib.idle_source_new()
        source.set_callback(run)
        source.attach(context)

    return post


def current_poster() -> Poster:
    """``poster_for`` the loop that runs the calling thread (its thread-default context, else
    the global default one)."""
    context = GLib.MainContext.ref_thread_default()
    if context is None:
        context = GLib.MainContext.default()
    return poster_for(context)


def _glib_unix():
    """The ``GLibUnix`` namespace (glib 2.79.2 and later), or None where the typelib has none."""
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
    except (ValueError, ImportError):
        return None
    return GLibUnix


def unix_signal_add(priority: int, signum: int, callback: Callable[[], bool]) -> int:
    """Call *callback()* on the main loop when the process gets signal *signum*; returns the id of
    the source, or 0 (and a warning) where this glib has no Unix signal watch at all.

    glib 2.79.2 moved ``GLib.unix_signal_add`` to ``GLibUnix.signal_add``, and the ``GLib`` typelib
    of glib 2.88 no longer has the old name. The new place is used where it exists and the old one
    where it does not (glib before 2.79.2): no deprecation warning on glib 2.88 and newer.
    Measured (the RPMs of each root unpacked and run, 2026-11-11): on Fedora 44 and rawhide the
    old call too works, because pygobject adds a wrapper that warns; the ubuntu-26.04 CI runner has
    none, and there the old name is an ``AttributeError``. Without a watch the default action of the
    signal applies: the program ends without the shutdown of the application."""
    unix = _glib_unix()
    if unix is not None:
        return unix.signal_add(priority, signum, callback)
    old = getattr(GLib, "unix_signal_add", None)
    if old is None:
        _LOG.warning("no Unix signal watch in this glib: signal %s ends the program as is", signum)
        return 0
    return old(priority, signum, callback)
