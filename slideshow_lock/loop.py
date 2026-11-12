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
    of glib 2.88 no longer has the old name. The new place is used where it has the call, the old
    one where it does not. Measured (the call run in the unpacked RPM roots, 2026-11-12): the
    ``GLibUnix`` namespace is importable on EL10 (glib 2.80) and Fedora 43 (glib 2.86) but has
    only ``signal_add_full`` there, no ``signal_add``; the call exists from glib 2.88 (Fedora 44
    and later). So the call itself is looked up, and an ``AttributeError`` from it counts as
    absent. No deprecation warning on glib 2.88 and newer. Without a watch the default action of
    the signal applies: the program ends without the shutdown of the application; a start must
    not fail for that."""
    unix = _glib_unix()
    new = getattr(unix, "signal_add", None)
    if new is not None:
        try:
            return new(priority, signum, callback)
        except AttributeError:
            pass
    old = getattr(GLib, "unix_signal_add", None)
    if old is not None:
        try:
            return old(priority, signum, callback)
        except AttributeError:
            pass
    _LOG.warning("no Unix signal watch in this glib: signal %s ends the program as is", signum)
    return 0
