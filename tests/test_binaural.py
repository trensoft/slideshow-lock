"""Tests for the binaural sound engine (``slideshow_lock.binaural``).

The ramps and the failure paths run on a fake backend and a clock that moves only when the test
says so (``BinauralPlayer.pump(now)``). The real pipeline is measured too: two channels, each with
its own frequency, read back from an ``appsink``. What these tests cannot say is how the sound
comes out of a speaker: no sound device is played here.
"""

from __future__ import annotations

import ast
import logging
import os
import struct
import threading
import time

import pytest

from slideshow_lock import binaural
from slideshow_lock.binaural import (
    BEAT_PRESETS,
    FADE_SECONDS,
    BinauralPlayer,
    GstToneBackend,
    Tone,
    channel_frequencies,
    fade_level,
    make_tone,
    pipeline_description,
)
from tests.timeout_guard import per_test_deadline  # noqa: F401  (autouse fixture)

# -- fakes ----------------------------------------------------------------------------------


class FakeBackend:
    def __init__(self, log, fail_open=None, fail_volume_after=None):
        self.log = log
        self.fail_open = fail_open
        self.fail_volume_after = fail_volume_after
        self.opened = None
        self.volumes = []
        self.closed = False

    def open(self, left_hz, right_hz):
        self.opened = (left_hz, right_hz)
        self.log.append(("open", left_hz, right_hz))
        if self.fail_open is not None:
            raise self.fail_open

    def set_volume(self, level):
        if self.fail_volume_after is not None and len(self.volumes) >= self.fail_volume_after:
            raise RuntimeError("the device went away")
        self.volumes.append(level)

    def close(self):
        self.closed = True
        self.log.append(("close",))


class Rig:
    """A player on a fake backend and a hand-made clock."""

    def __init__(self, **backend_args):
        self.now = 100.0
        self.log = []
        self.backends = []
        self.backend_args = backend_args
        self.player = BinauralPlayer(self.make_backend, clock=lambda: self.now, start_thread=False)

    def make_backend(self):
        backend = FakeBackend(self.log, **self.backend_args)
        self.backends.append(backend)
        return backend

    @property
    def backend(self):
        return self.backends[-1]

    def pump_at(self, seconds_after_start, origin):
        self.now = origin + seconds_after_start
        return self.player.pump(self.now)


TONE = Tone(200.0, 210.0, 0.30)


# -- the tone -------------------------------------------------------------------------------


@pytest.mark.parametrize("name, beat_hz", [("delta", 4), ("theta", 6), ("alpha", 10), ("beta", 14)])
def test_each_preset_puts_the_carrier_left_and_the_carrier_plus_the_beat_right(name, beat_hz):
    assert BEAT_PRESETS[name] == beat_hz
    tone = make_tone(30, name, 200)
    assert (tone.left_hz, tone.right_hz) == (200.0, 200.0 + beat_hz)


def test_the_presets_are_exactly_the_four_named_ones():
    assert BEAT_PRESETS == {"delta": 4.0, "theta": 6.0, "alpha": 10.0, "beta": 14.0}


def test_the_carrier_moves_both_channels_and_keeps_the_beat():
    assert channel_frequencies(300, 6) == (300.0, 306.0)
    tone = make_tone(30, "theta", 300)
    assert (tone.left_hz, tone.right_hz) == (300.0, 306.0)


def test_the_defaults_are_alpha_200_hz_and_30_percent():
    tone = make_tone()
    assert (tone.left_hz, tone.right_hz, tone.volume) == (200.0, 210.0, 0.30)


@pytest.mark.parametrize("percent, level", [(0, 0.0), (30, 0.3), (100, 1.0), (150, 1.0), (-5, 0.0)])
def test_the_volume_is_a_percentage_cut_to_0_100(percent, level):
    assert make_tone(percent).volume == pytest.approx(level)


def test_a_carrier_outside_its_range_is_cut_and_an_unknown_beat_is_alpha():
    assert make_tone(30, "alpha", 5).left_hz == binaural.CARRIER_MIN_HZ
    assert make_tone(30, "alpha", 50000).left_hz == binaural.CARRIER_MAX_HZ
    unknown = make_tone(30, "gamma", 200)
    assert (unknown.left_hz, unknown.right_hz) == (200.0, 210.0)


# -- the ramp -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "elapsed, expected",
    [(0.0, 0.0), (0.375, 0.075), (0.75, 0.15), (1.125, 0.225), (1.5, 0.3), (9.0, 0.3), (-1.0, 0.0)],
)
def test_the_fade_in_goes_from_0_to_the_target_in_1_5_seconds(elapsed, expected):
    assert FADE_SECONDS == 1.5
    assert fade_level(0.0, 0.3, elapsed) == pytest.approx(expected)


@pytest.mark.parametrize(
    "elapsed, expected",
    [(0.0, 0.3), (0.375, 0.225), (0.75, 0.15), (1.125, 0.075), (1.5, 0.0), (7.0, 0.0)],
)
def test_the_fade_out_goes_from_the_target_to_0_in_1_5_seconds(elapsed, expected):
    assert fade_level(0.3, 0.0, elapsed) == pytest.approx(expected)


def test_a_ramp_that_takes_no_time_is_at_its_end():
    assert fade_level(0.0, 0.5, 0.0, 0.0) == 0.5


def test_the_player_fades_in_over_1_5_seconds_from_silence():
    rig = Rig()
    rig.player.start(TONE)
    origin = rig.now
    assert rig.pump_at(0.0, origin) == binaural.STEP_SECONDS
    assert rig.backend.opened == (200.0, 210.0)
    assert rig.backend.volumes[0] == 0.0  # the first thing the device hears is silence
    for seconds, share in [(0.375, 0.25), (0.75, 0.5), (1.125, 0.75)]:
        rig.pump_at(seconds, origin)
        assert rig.backend.volumes[-1] == pytest.approx(0.30 * share)
    rig.pump_at(1.5, origin)
    assert rig.backend.volumes[-1] == pytest.approx(0.30)
    assert rig.pump_at(1.6, origin) == binaural.IDLE_SECONDS  # settled: only watching
    assert not rig.backend.closed


def test_the_fade_in_is_monotonic_and_never_above_the_target():
    rig = Rig()
    rig.player.start(TONE)
    origin = rig.now
    for step in range(0, 120):
        rig.pump_at(step * 0.02, origin)
    volumes = rig.backend.volumes
    assert volumes == sorted(volumes)
    assert max(volumes) == pytest.approx(0.30)
    assert volumes[0] == 0.0


def test_the_player_fades_out_over_1_5_seconds_and_then_takes_the_pipeline_down():
    rig = Rig()
    rig.player.start(TONE)
    origin = rig.now
    rig.pump_at(0.0, origin)
    rig.pump_at(5.0, origin)
    assert rig.backend.volumes[-1] == pytest.approx(0.30)
    assert rig.player.stop() == FADE_SECONDS
    stop_at = origin + 10.0
    for seconds, share in [(0.0, 1.0), (0.375, 0.75), (0.75, 0.5), (1.125, 0.25)]:
        assert rig.pump_at(10.0 + seconds, origin) == binaural.STEP_SECONDS
        assert rig.backend.volumes[-1] == pytest.approx(0.30 * share)
    assert not rig.backend.closed
    assert rig.pump_at(11.5, origin) is None
    assert rig.backend.volumes[-1] == pytest.approx(0.0)
    assert rig.backend.closed
    assert stop_at == pytest.approx(origin + 10.0)


def test_a_stop_in_the_middle_of_the_fade_in_fades_out_from_where_it_is():
    rig = Rig()
    rig.player.start(TONE)
    origin = rig.now
    rig.pump_at(0.0, origin)
    rig.pump_at(0.75, origin)  # half way up: 0.15
    rig.player.stop()
    rig.pump_at(0.75, origin)
    assert rig.backend.volumes[-1] == pytest.approx(0.15)
    rig.pump_at(1.5, origin)  # half of the fade-out
    assert rig.backend.volumes[-1] == pytest.approx(0.075)
    assert rig.pump_at(2.25, origin) is None
    assert rig.backend.closed


def test_a_start_while_it_fades_out_turns_it_up_again_on_the_same_pipeline():
    rig = Rig()
    rig.player.start(TONE)
    origin = rig.now
    rig.pump_at(0.0, origin)
    rig.pump_at(3.0, origin)
    rig.player.stop()
    rig.pump_at(3.0, origin)  # the worker hears the stop at once: the ramp starts here
    rig.pump_at(3.75, origin)  # half way down
    assert rig.backend.volumes[-1] == pytest.approx(0.15)
    rig.player.start(TONE)
    rig.pump_at(3.75, origin)
    assert rig.backend.volumes[-1] == pytest.approx(0.15)
    rig.pump_at(5.25, origin)
    assert rig.backend.volumes[-1] == pytest.approx(0.30)
    assert len(rig.backends) == 1 and not rig.backend.closed


def test_a_start_with_other_frequencies_opens_a_new_pipeline():
    rig = Rig()
    rig.player.start(TONE)
    rig.pump_at(0.0, rig.now)
    first = rig.backend
    rig.player.start(Tone(300.0, 304.0, 0.5))
    rig.pump_at(0.0, rig.now)
    assert first.closed
    assert len(rig.backends) == 2
    assert rig.backend.opened == (300.0, 304.0)
    assert rig.backend.volumes[0] == 0.0


def test_the_fade_in_counts_from_the_moment_the_sound_is_open():
    """A slow device must not eat the fade-in: the ramp starts when the pipeline is up."""
    rig = Rig()
    real_open = FakeBackend.open

    def slow_open(backend, left, right):
        real_open(backend, left, right)
        rig.now += 0.8  # the device took 0.8 s

    rig.player = BinauralPlayer(
        lambda: _with_open(rig.make_backend(), slow_open), clock=lambda: rig.now, start_thread=False
    )
    rig.player.start(TONE)
    origin = rig.now
    rig.player.pump(origin)
    assert rig.backend.volumes[-1] == 0.0
    rig.now = origin + 0.8 + 0.75
    rig.player.pump(rig.now)
    assert rig.backend.volumes[-1] == pytest.approx(0.15)


def _with_open(backend, open_fn):
    backend.open = lambda left, right: open_fn(backend, left, right)
    return backend


def test_stop_without_a_start_does_nothing_and_says_there_is_nothing_to_wait_for():
    rig = Rig()
    assert rig.player.stop() == 0.0
    assert rig.player.pump(rig.now) is None
    assert rig.backends == []


def test_close_takes_the_pipeline_down_at_once_and_a_later_start_is_ignored():
    rig = Rig()
    rig.player.start(TONE)
    rig.pump_at(0.0, rig.now)
    rig.player.close()
    assert rig.backend.closed
    rig.player.close()  # idempotent
    rig.player.start(TONE)
    assert rig.player.pump(rig.now) is None
    assert len(rig.backends) == 1


# -- failure and absence: a line in the log, no exception, no sound -------------------------


def _sound_lines(caplog):
    return [r for r in caplog.records if "[sound]" in r.getMessage()]


def test_no_backend_at_all_is_one_log_line_and_no_exception(caplog):
    player = BinauralPlayer(
        lambda: (_ for _ in ()).throw(ImportError("No module named 'gi'")), start_thread=False
    )
    with caplog.at_level(logging.DEBUG):
        player.start(TONE)
        assert player.pump(time.monotonic()) is None
        assert player.pump(time.monotonic()) is None  # nothing more, no second line
        assert player.stop() == 0.0
        player.close()
    lines = _sound_lines(caplog)
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    assert "ImportError" in lines[0].getMessage()
    assert "goes on without" in lines[0].getMessage()


def test_a_pipeline_that_does_not_start_is_closed_and_logged_once(caplog):
    rig = Rig(fail_open=RuntimeError("the pipeline could not be started"))
    with caplog.at_level(logging.DEBUG):
        rig.player.start(TONE)
        assert rig.player.pump(rig.now) is None
        assert rig.player.pump(rig.now + 1) is None
    assert rig.backend.closed
    assert len(_sound_lines(caplog)) == 1
    assert len(rig.backends) == 1  # no retry in the same run


def test_the_next_start_tries_again_after_a_failure(caplog):
    rig = Rig(fail_open=RuntimeError("no device"))
    with caplog.at_level(logging.DEBUG):
        rig.player.start(TONE)
        rig.player.pump(rig.now)
        rig.player.stop()
        rig.player.start(TONE)
        rig.player.pump(rig.now)
    assert len(rig.backends) == 2
    assert len(_sound_lines(caplog)) == 2


def test_a_device_that_fails_in_the_middle_of_the_fade_stops_the_sound_quietly(caplog):
    rig = Rig(fail_volume_after=3)
    with caplog.at_level(logging.DEBUG):
        rig.player.start(TONE)
        origin = rig.now
        for step in range(10):
            rig.pump_at(step * 0.1, origin)
        assert rig.player.stop() == 0.0  # failed: nobody has to wait for a fade-out
    assert rig.backend.closed
    assert len(_sound_lines(caplog)) == 1


def test_a_pipeline_that_cannot_be_closed_does_not_raise(caplog):
    rig = Rig()
    rig.player.start(TONE)
    rig.pump_at(0.0, rig.now)

    def broken_close():
        raise RuntimeError("stuck")

    rig.backend.close = broken_close
    with caplog.at_level(logging.DEBUG):
        rig.player.close()  # must not raise


# -- the main thread never waits for the device ------------------------------------------------


def test_start_stop_and_close_do_not_wait_for_a_device_that_hangs():
    """The lock is made on the main loop: whatever the sound device does, the calls the main loop
    makes return at once."""
    release = threading.Event()
    entered = threading.Event()
    made = []

    def hanging_factory():
        entered.set()
        release.wait(30)
        backend = FakeBackend([])
        made.append(backend)
        return backend

    player = BinauralPlayer(hanging_factory)
    began = time.monotonic()
    player.start(TONE)
    player.stop()
    player.start(TONE)
    took = time.monotonic() - began
    assert entered.wait(5), "the worker never reached the device"
    assert took < 0.5, f"start/stop waited {took:.2f}s for the device"
    began = time.monotonic()
    closer = threading.Thread(target=player.close)  # close() waits a moment for the worker
    closer.start()
    release.set()
    closer.join(10)
    assert not closer.is_alive()
    assert time.monotonic() - began < 5
    assert all(backend.closed for backend in made)


def test_the_worker_thread_fades_in_and_out_on_its_own():
    opened, closed = threading.Event(), threading.Event()
    seen = []

    class Backend(FakeBackend):
        def open(self, left_hz, right_hz):
            super().open(left_hz, right_hz)
            opened.set()

        def set_volume(self, level):
            seen.append(level)

        def close(self):
            super().close()
            closed.set()

    player = BinauralPlayer(lambda: Backend([]), fade_seconds=0.2, step_seconds=0.01)
    player.start(TONE)
    assert opened.wait(5)
    deadline = time.monotonic() + 5
    while (not seen or seen[-1] < 0.30) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen[-1] == pytest.approx(0.30)
    assert player.stop() == 0.2
    assert closed.wait(5), "the pipeline was not taken down after the fade-out"
    assert seen[-1] == pytest.approx(0.0)
    assert seen.index(max(seen)) < len(seen) - 1
    player.close()


def _wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)


def _idle_player(backend_log, *, fade=0.1):
    """A player on a real worker thread whose steady wait is very long: only a command can wake it
    in time, so a missed wake-up shows as a timeout."""
    return BinauralPlayer(
        lambda: _Signalling(backend_log),
        fade_seconds=fade,
        step_seconds=0.01,
        idle_seconds=60.0,
    )


class _Signalling(FakeBackend):
    """Tells the test when it was opened and when it was closed."""

    def __init__(self, registry):
        super().__init__([])
        self.opened_event = threading.Event()
        self.closed_event = threading.Event()
        registry.append(self)

    def open(self, left_hz, right_hz):
        super().open(left_hz, right_hz)
        self.opened_event.set()

    def close(self):
        super().close()
        self.closed_event.set()


def test_a_stop_wakes_a_worker_that_waits_for_a_long_time():
    made = []
    player = _idle_player(made)
    try:
        player.start(TONE)
        _wait_for(lambda: made)
        assert made[0].opened_event.wait(5)
        time.sleep(0.3)  # the fade-in is over: the worker waits its long steady wait
        player.stop()
        assert made[0].closed_event.wait(5), "the stop did not wake the worker"
    finally:
        player.close()


def test_a_start_wakes_a_worker_that_has_nothing_to_do():
    made = []
    player = _idle_player(made)
    try:
        player.start(TONE)
        _wait_for(lambda: made)
        assert made[0].opened_event.wait(5)
        player.stop()
        assert made[0].closed_event.wait(5)
        time.sleep(0.2)  # the worker now waits for the next command, without a time limit
        player.start(TONE)
        _wait_for(lambda: len(made) == 2)
        assert len(made) == 2 and made[1].opened_event.wait(5), "the start did not wake the worker"
    finally:
        player.close()


def test_a_command_that_arrives_while_the_worker_is_busy_is_not_lost():
    """The start comes in while the worker is closing the pipeline of the stop before it, so before
    it goes to wait: it must still be picked up."""
    made = []
    player = None

    class Backend(_Signalling):
        def close(self):
            first = not made[0].closed_event.is_set() and self is made[0]
            super().close()
            if first:
                player.start(TONE)  # a command in the middle of the worker's own step

    player = BinauralPlayer(
        lambda: Backend(made), fade_seconds=0.05, step_seconds=0.01, idle_seconds=60.0
    )
    try:
        player.start(TONE)
        _wait_for(lambda: made)
        assert made[0].opened_event.wait(5)
        player.stop()
        _wait_for(lambda: len(made) == 2)
        assert len(made) == 2, "the command that came in during the worker's step was lost"
        assert made[1].opened_event.wait(5)
    finally:
        player.close()


def test_the_worker_is_a_daemon_so_that_a_stuck_device_cannot_keep_the_program_alive():
    made = []
    player = _idle_player(made)
    try:
        player.start(TONE)
        assert player._thread is not None and player._thread.daemon
    finally:
        player.close()


def test_a_start_after_close_makes_no_thread():
    player = BinauralPlayer(lambda: FakeBackend([]))
    player.close()
    player.start(TONE)
    assert player._thread is None


def test_the_module_is_on_its_own():
    """No D-Bus, no session, no GTK, nothing of this package: only the standard library at module
    level (GStreamer is imported inside the backend, on the worker thread)."""
    with open(binaural.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "logging", "threading", "time", "dataclasses", "typing"}


# -- the real pipeline ----------------------------------------------------------------------


def _gst():
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        from gi.repository import Gst, GstApp  # noqa: F401  (GstApp: appsink methods)
    except (ValueError, ImportError) as exc:
        pytest.skip(f"GStreamer typelibs are missing: {exc}")
    Gst.init(None)
    missing = [name for name in binaural.REQUIRED_ELEMENTS if Gst.ElementFactory.find(name) is None]
    if missing or Gst.ElementFactory.find("appsink") is None:
        pytest.skip(f"GStreamer elements are missing: {missing}")
    return Gst


#: A sink that is an ``appsink`` on 16 bit stereo, so that the test can read what was made.
PROBE_SINK = (
    "audio/x-raw,format=S16LE,rate=44100,channels=2 ! appsink name=probe sync=false max-buffers=4"
)


def _measure(Gst, tone_left, tone_right, volume, seconds=2):
    """Open the real pipeline on the probe sink, set the volume, read *seconds* of sound; return
    ((hz, peak) of the left channel, (hz, peak) of the right one)."""
    backend = GstToneBackend(sink=PROBE_SINK, live=False)
    backend.open(tone_left, tone_right)
    try:
        probe = backend._pipeline.get_by_name("probe")
        assert backend._pipeline.get_by_name("level").get_property("volume") == 0.0
        backend.set_volume(volume)
        for _ in range(12):  # what was made before the volume was set
            probe.try_pull_sample(Gst.SECOND)
        data = b""
        while len(data) < 44100 * 4 * seconds:
            sample = probe.try_pull_sample(Gst.SECOND)
            assert sample is not None, "the pipeline made no more sound"
            buffer = sample.get_buffer()
            ok, info = buffer.map(Gst.MapFlags.READ)
            assert ok
            data += bytes(info.data)
            buffer.unmap(info)
    finally:
        backend.close()
    frames = len(data) // 4
    values = struct.unpack("<" + "h" * (frames * 2), data[: frames * 4])
    result = []
    for channel in (0, 1):
        samples = values[channel::2]
        crossings = sum(1 for a, b in zip(samples, samples[1:]) if a <= 0 < b)
        result.append((crossings / (frames / 44100.0), max(abs(v) for v in samples)))
    return result[0], result[1]


@pytest.mark.parametrize("name", ["delta", "theta", "alpha", "beta"])
def test_the_real_pipeline_makes_two_separate_channels_with_the_preset_frequencies(name):
    Gst = _gst()
    tone = make_tone(100, name, 200)
    (left_hz, left_peak), (right_hz, right_peak) = _measure(Gst, tone.left_hz, tone.right_hz, 1.0)
    assert left_hz == pytest.approx(200.0, abs=1.0)
    assert right_hz == pytest.approx(200.0 + BEAT_PRESETS[name], abs=1.0)
    assert left_peak > 30000 and right_peak > 30000  # both sound, at full volume


def test_the_real_pipeline_follows_the_volume_and_is_silent_at_0():
    Gst = _gst()
    (_hz, half_left), (_hz2, half_right) = _measure(Gst, 200.0, 210.0, 0.5, seconds=1)
    assert half_left == pytest.approx(16383, rel=0.02)
    assert half_right == pytest.approx(16383, rel=0.02)
    (_hz3, silent_left), (_hz4, silent_right) = _measure(Gst, 200.0, 210.0, 0.0, seconds=1)
    assert silent_left == 0 and silent_right == 0


def test_the_real_pipeline_with_a_missing_element_raises_and_names_the_package(monkeypatch):
    Gst = _gst()
    monkeypatch.setitem(
        binaural.REQUIRED_ELEMENTS, "nosuchelementxyz", "gstreamer1-plugins-nowhere"
    )
    with pytest.raises(RuntimeError, match="nosuchelementxyz.*gstreamer1-plugins-nowhere"):
        GstToneBackend(sink=PROBE_SINK).open(200.0, 210.0)
    assert Gst.ElementFactory.find("nosuchelementxyz") is None


def test_the_player_on_the_real_backend_with_a_missing_sink_logs_and_goes_on(caplog):
    _gst()
    player = BinauralPlayer(lambda: GstToneBackend(sink="nosuchsinkxyz"), start_thread=False)
    with caplog.at_level(logging.DEBUG):
        player.start(TONE)
        assert player.pump(time.monotonic()) is None
        assert player.stop() == 0.0
    lines = _sound_lines(caplog)
    assert len(lines) == 1
    assert "nosuchsinkxyz" in lines[0].getMessage()


def test_the_description_has_one_tone_per_side_and_one_volume():
    text = pipeline_description(200.0, 210.0, sink="fakesink", live=True)
    assert text.count("audiotestsrc") == 2
    assert "freq=200.000" in text and "freq=210.000" in text
    assert text.count("<<(float)1.0>, <(float)0.0>>") == 1  # mono -> left only
    assert text.count("<<(float)0.0>, <(float)1.0>>") == 1  # mono -> right only
    assert text.count("volume name=level") == 1
    assert text.count("is-live=true") == 2
    assert "is-live=false" in pipeline_description(1, 2, live=False)
    assert os.path.basename(binaural.__file__) == "binaural.py"


def test_a_pipeline_whose_sink_cannot_start_is_a_failure_not_a_silent_success():
    _gst()
    backend = GstToneBackend(sink="filesink location=/nonexistent-dir-xyz/out.raw")
    with pytest.raises(RuntimeError, match="could not be started"):
        backend.open(200.0, 210.0)
    assert backend._pipeline is None  # and nothing is left running
