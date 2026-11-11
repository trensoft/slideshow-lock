# SPDX-FileCopyrightText: 2026 TrenSoft
# SPDX-License-Identifier: GPL-3.0-or-later

"""Binaural beat sound for the slideshow (1.0.11): two sine tones, one in each ear.

The left channel carries the *carrier* (200 Hz by default), the right channel the carrier plus the
*beat* (a preset: delta 4, theta 6, alpha 10, beta 14 Hz). The two tones are really separate
channels: two mono ``audiotestsrc`` elements, each one moved to its own side by ``audioconvert``
(``mix-matrix``) and put together by ``audiomixer``. Those three, ``volume`` and ``appsink`` are in
``gstreamer1-plugins-base``; the sound goes out through ``autoaudiosink`` of
``gstreamer1-plugins-good`` (measured 2026-11-11: Fedora 43 and CentOS Stream 10 AppStream, EPEL is
not needed).

The sound is a side dish and never gets in the way of the slideshow or of the lock:

* The module is on its own. It has no D-Bus, no session, no lock and no GTK; it only touches
  GStreamer, and only from a worker thread of its own. ``start`` and ``stop`` only queue a
  command and return at once: nothing the audio device does (a sound server that hangs, a slow
  ``set_state``) can stop the main loop, and so nothing it does can delay a lock, which is made on
  the main loop. The tests of ``tests/test_preview.py`` (D11) scan this file like every other module
  that is not on the lock side.
* A failure (GStreamer or an element missing, no sound device, a pipeline that does not start) is
  one line in the log (``[sound]``); the slideshow goes on without sound and the next start tries
  again. ``start`` and ``stop`` never raise.

The volume ramps are linear in amplitude: 0 -> the set volume in ``FADE_SECONDS`` at the start,
the level now -> 0 in ``FADE_SECONDS`` at the end, then the pipeline is taken down. The ramp is
driven by ``BinauralPlayer.pump(now)``, which the worker thread calls and a test calls with a time
of its own. The settings are read once, when the sound starts: a change while it plays is heard at
the next start.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

_LOG = logging.getLogger(__name__)

#: Fade in at the start and fade out at the end, seconds.
FADE_SECONDS = 1.5

#: How often the worker sets the volume while a ramp runs (50 Hz: a step is under 1 % of the level).
STEP_SECONDS = 0.02

#: How often the worker wakes while a pipeline has settled: it sets the same volume again. It does
#: not read the bus, so a device that goes away in the middle of a slideshow is not noticed.
IDLE_SECONDS = 1.0

#: The beat presets, in Hz: the right channel is the carrier plus this.
BEAT_PRESETS: Dict[str, float] = {"delta": 4.0, "theta": 6.0, "alpha": 10.0, "beta": 14.0}
DEFAULT_BEAT = "alpha"

CARRIER_MIN_HZ = 100
CARRIER_MAX_HZ = 500
DEFAULT_CARRIER_HZ = 200

VOLUME_MIN_PERCENT = 0
VOLUME_MAX_PERCENT = 100
DEFAULT_VOLUME_PERCENT = 30

#: The sink of the real pipeline. ``autoaudiosink`` picks the desktop's own sound output.
DEFAULT_SINK = "autoaudiosink"

#: Elements the real pipeline needs apart from the sink, with the package that has them.
REQUIRED_ELEMENTS = {
    "audiotestsrc": "gstreamer1-plugins-base",
    "audioconvert": "gstreamer1-plugins-base",
    "audioresample": "gstreamer1-plugins-base",
    "audiomixer": "gstreamer1-plugins-base",
    "volume": "gstreamer1-plugins-base",
}

#: Seconds a state change to PLAYING may take before the worker stops waiting for its answer. An
#: answer that has not come is not a failure: a sound server can be slow, and the worker thread is
#: not the main loop.
START_WAIT_SECONDS = 1.0


@dataclass(frozen=True)
class Tone:
    """What to play: the two frequencies and the level (0.0 to 1.0) the fade-in ends at."""

    left_hz: float
    right_hz: float
    volume: float


def channel_frequencies(carrier_hz: float, beat_hz: float) -> Tuple[float, float]:
    """(left, right): the carrier, and the carrier plus the beat."""
    return float(carrier_hz), float(carrier_hz) + float(beat_hz)


def make_tone(
    volume_percent: float = DEFAULT_VOLUME_PERCENT,
    beat: str = DEFAULT_BEAT,
    carrier_hz: float = DEFAULT_CARRIER_HZ,
) -> Tone:
    """The tone for the stored settings. A value outside its range is cut to it, a beat that is
    not a preset is the default one: whatever a settings file holds, the tone can be played."""
    percent = min(max(float(volume_percent), VOLUME_MIN_PERCENT), VOLUME_MAX_PERCENT)
    carrier = min(max(float(carrier_hz), CARRIER_MIN_HZ), CARRIER_MAX_HZ)
    beat_hz = BEAT_PRESETS.get(beat, BEAT_PRESETS[DEFAULT_BEAT])
    left, right = channel_frequencies(carrier, beat_hz)
    return Tone(left, right, percent / 100.0)


def tone_from_settings(settings) -> Optional[Tone]:
    """The tone the settings ask for, or None when the sound is off. The four values are read once.

    *settings* has ``get_binaural_enabled()``, ``get_binaural_volume()``, ``get_binaural_beat()``
    and ``get_binaural_carrier_hz()``; an object without them (an older one) means no sound. A
    failure to read them is a log line and no sound, never an exception."""
    enabled = getattr(settings, "get_binaural_enabled", None)
    if enabled is None:
        return None
    try:
        if not enabled():
            return None
        return make_tone(
            settings.get_binaural_volume(),
            settings.get_binaural_beat(),
            settings.get_binaural_carrier_hz(),
        )
    except Exception as exc:
        _LOG.warning("[sound] the sound settings could not be read (%s): no sound", exc)
        return None


def fade_level(start: float, end: float, elapsed: float, seconds: float = FADE_SECONDS) -> float:
    """The level *elapsed* seconds into a linear ramp from *start* to *end* that takes *seconds*."""
    if seconds <= 0 or elapsed >= seconds:
        return end
    if elapsed <= 0:
        return start
    return start + (end - start) * (elapsed / seconds)


def pipeline_description(
    left_hz: float, right_hz: float, sink: str = DEFAULT_SINK, live: bool = True
) -> str:
    """The ``gst_parse_launch`` text: the left tone to the left channel only, the right tone to the
    right channel only, both through one ``volume`` named ``level``, then *sink*."""
    flag = "true" if live else "false"

    def side(freq: float, matrix: str) -> str:
        return (
            f"audiotestsrc wave=sine freq={freq:.3f} volume=1.0 is-live={flag} ! "
            "audio/x-raw,rate=44100,channels=1 ! "
            f'audioconvert mix-matrix="{matrix}" ! audio/x-raw,channels=2 ! mixer.'
        )

    return (
        f"audiomixer name=mixer ! audioconvert ! audioresample ! volume name=level volume=0.0 "
        f"! {sink} "
        + side(left_hz, "<<(float)1.0>, <(float)0.0>>")
        + " "
        + side(right_hz, "<<(float)0.0>, <(float)1.0>>")
    )


class GstToneBackend:
    """The real pipeline. ``open`` raises if the tone cannot be played; every method may block
    (the device is out there), which is why the player calls them from its worker thread only."""

    def __init__(self, sink: str = DEFAULT_SINK, live: bool = True) -> None:
        self._sink = sink
        self._live = live
        self._pipeline = None
        self._level = None
        self._gst = None

    def open(self, left_hz: float, right_hz: float) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        for name, package in REQUIRED_ELEMENTS.items():
            if Gst.ElementFactory.find(name) is None:
                raise RuntimeError(f"the {name} element is not installed ({package})")
        pipeline = Gst.parse_launch(pipeline_description(left_hz, right_hz, self._sink, self._live))
        self._gst, self._pipeline = Gst, pipeline
        self._level = pipeline.get_by_name("level")
        try:
            change = pipeline.set_state(Gst.State.PLAYING)
            if change == Gst.StateChangeReturn.ASYNC:
                change = pipeline.get_state(int(START_WAIT_SECONDS * Gst.SECOND))[0]
            if change == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("the pipeline could not be started (is there a sound device?)")
        except BaseException:
            self.close()
            raise

    def set_volume(self, level: float) -> None:
        self._level.set_property("volume", float(level))

    def close(self) -> None:
        pipeline, self._pipeline, self._level = self._pipeline, None, None
        if pipeline is not None:
            pipeline.set_state(self._gst.State.NULL)


class BinauralPlayer:
    """Plays a ``Tone`` with a fade-in, and fades out when told to stop.

    *backend_factory* makes a backend (``open(left_hz, right_hz)``, ``set_volume(level)``,
    ``close()``); the default is the real pipeline. *clock* is ``time.monotonic``-like.
    ``start_thread=False`` is for the tests, which then call ``pump`` themselves.

    ``start``, ``stop`` and ``close`` are for the main thread and only queue a command: they do not
    wait for the device, and they do not raise.
    """

    def __init__(
        self,
        backend_factory: Optional[Callable[[], object]] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        fade_seconds: float = FADE_SECONDS,
        step_seconds: float = STEP_SECONDS,
        idle_seconds: float = IDLE_SECONDS,
        start_thread: bool = True,
    ) -> None:
        self._factory = backend_factory or GstToneBackend
        self._clock = clock
        self._fade = fade_seconds
        self._step = step_seconds
        self._idle = idle_seconds
        self._use_thread = start_thread
        self._cond = threading.Condition()
        # shared with the caller (under _cond)
        self._target: Optional[Tone] = None
        self._generation = 0  # one more for every start()
        self._version = 0  # one more for every command, so the worker does not miss one
        self._started = False
        self._failed = False
        self._closed = False
        self._thread: Optional[threading.Thread] = None
        # the worker's own (the test's, with start_thread=False)
        self._backend = None
        self._open_tone: Optional[Tone] = None
        self._seen_generation = 0
        self._level = 0.0
        self._ramp: Optional[Tuple[float, float, float]] = None  # (started at, from, to)
        self._goal: Optional[float] = None

    # -- the main thread ------------------------------------------------------------------

    def start(self, tone: Tone) -> None:
        """Begin the sound (or, if it is still fading out, turn it up again). Returns at once."""
        with self._cond:
            if self._closed:
                return
            self._target = tone
            self._generation += 1
            self._version += 1
            self._started = True
            self._failed = False
            self._ensure_thread()
            self._cond.notify_all()

    def stop(self) -> float:
        """Begin the fade-out. Returns at once; the number is the longest the fade-out can take
        (0.0 when no sound was started or it failed): a program about to exit waits so long."""
        with self._cond:
            wanted = self._started and not self._failed and not self._closed
            self._target = None
            self._version += 1
            self._cond.notify_all()
        return self._fade if wanted else 0.0

    def close(self, wait: bool = True) -> None:
        """Take the pipeline down now, without a fade, and stop the worker. With *wait* it waits
        for the worker for 2 s at most (the program that is ending); without, it returns at once
        and the worker (a daemon thread) takes the pipeline down on its own (the preview, which
        runs on a main loop that a hanging device must not hold). Idempotent."""
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._target = None
            self._version += 1
            thread = self._thread
            self._cond.notify_all()
        if thread is not None:
            if wait:
                thread.join(2.0)
        else:
            self._teardown()

    # -- the worker -------------------------------------------------------------------------

    def _ensure_thread(self) -> None:
        if not self._use_thread or self._thread is not None:
            return
        try:
            thread = threading.Thread(target=self._run, name="binaural-sound", daemon=True)
            thread.start()
        except Exception as exc:  # no thread: no sound, and the caller is not told
            self._failed = True
            _LOG.warning("[sound] the sound thread could not be started (%s)", exc)
            return
        self._thread = thread

    def _run(self) -> None:
        while True:
            with self._cond:
                version, closed = self._version, self._closed
            delay = self.pump(self._clock())
            if closed:
                return
            with self._cond:
                if self._version == version and not self._closed:
                    self._cond.wait(delay)

    def pump(self, now: float) -> Optional[float]:
        """Do what is due at *now*; the seconds until the next call, or None: nothing to do until
        the next command. Called by the worker thread only (or by a test)."""
        with self._cond:
            target, generation, closed = self._target, self._generation, self._closed
        if closed:
            self._teardown()
            return None
        if target is not None:
            if generation != self._seen_generation:
                self._seen_generation = generation
                with self._cond:
                    self._failed = False  # a new start tries again, whatever the last one did
                if self._backend is not None and not self._same_tones(target):
                    self._teardown()
            with self._cond:
                if self._failed:
                    return None
            if self._backend is None:
                if not self._open(target):
                    return None
                now = self._clock()
        goal = target.volume if target is not None else 0.0
        if self._backend is None:
            return None
        if goal != self._goal:
            self._goal = goal
            self._ramp = (now, self._level, goal)
        if self._ramp is not None:
            started, level_from, level_to = self._ramp
            self._level = fade_level(level_from, level_to, now - started, self._fade)
            if now - started >= self._fade:
                self._ramp = None
        try:
            self._backend.set_volume(self._level)
        except Exception as exc:
            self._fail("setting the volume", exc)
            return None
        if self._ramp is not None:
            return self._step
        if target is None:  # faded out: nothing plays any more
            self._teardown()
            return None
        return self._idle

    def _same_tones(self, tone: Tone) -> bool:
        open_tone = self._open_tone
        return (
            open_tone is not None
            and open_tone.left_hz == tone.left_hz
            and open_tone.right_hz == tone.right_hz
        )

    def _open(self, tone: Tone) -> bool:
        try:
            backend = self._factory()
            self._backend = backend
            backend.open(tone.left_hz, tone.right_hz)
        except Exception as exc:
            self._fail("starting", exc)
            return False
        self._open_tone = tone
        self._level = 0.0
        self._goal = None
        self._ramp = None
        _LOG.info(
            "[sound] playing (left %.1f Hz, right %.1f Hz, volume %d %%)",
            tone.left_hz,
            tone.right_hz,
            round(tone.volume * 100),
        )
        return True

    def _fail(self, what: str, exc: Exception) -> None:
        with self._cond:
            self._failed = True
        _LOG.warning(
            "[sound] binaural sound stopped while %s (%s: %s): the slideshow goes on without it",
            what,
            type(exc).__name__,
            exc,
        )
        self._teardown()

    def _teardown(self) -> None:
        backend, self._backend = self._backend, None
        self._open_tone = None
        self._level = 0.0
        self._goal = None
        self._ramp = None
        if backend is not None:
            try:
                backend.close()
            except Exception as exc:
                _LOG.debug("[sound] closing the pipeline failed (%s)", exc)
            else:
                _LOG.debug("[sound] stopped")


class SlideshowSound:
    """What the slideshow calls: ``start`` when it shows, ``stop`` when it ends.

    The settings are read when the sound starts, so a change is heard at the next slideshow. Nothing
    here raises and nothing waits for the device (see ``BinauralPlayer``): the slideshow and the
    lock go on whatever the sound does. *player* is for the tests."""

    def __init__(self, settings, player: Optional[BinauralPlayer] = None) -> None:
        self._settings = settings
        self._player = player or BinauralPlayer()

    def start(self) -> None:
        try:
            tone = tone_from_settings(self._settings)
            if tone is not None:
                self._player.start(tone)
        except Exception as exc:
            _LOG.warning("[sound] the sound could not be started (%s): no sound", exc)

    def stop(self) -> float:
        """Begin the fade-out; the seconds it can take (0.0 when nothing plays)."""
        try:
            return self._player.stop()
        except Exception as exc:
            _LOG.warning("[sound] the sound could not be stopped (%s)", exc)
            return 0.0

    def close(self, wait: bool = True) -> None:
        """Take the pipeline down at once and end the worker thread (the program is ending, or
        this sound will not be used again). Without *wait* it does not wait for the worker."""
        try:
            self._player.close(wait=wait)
        except Exception as exc:
            _LOG.warning("[sound] closing the sound failed (%s)", exc)
