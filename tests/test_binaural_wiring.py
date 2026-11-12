"""The binaural sound in the programs: the service (``PreviewSlideshow`` on the state machine) and
the preview (``preview_app.start_preview``).

The rule these tests hold: the lock never depends on the sound. The slideshow starts and ends, and
the session is locked, whatever the sound does: no sound device, a sound that raises, a device that
hangs. The lock call goes out before the fade-out begins, and long before it is over.

The sound is a ``SlideshowSound`` on a real ``BinauralPlayer`` over a fake backend and a clock the
test moves (``pump``), or a double that records its calls. Nothing here plays anything: how it
sounds on a real device is for a person to try.
"""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest
from gi.repository import GLib

from slideshow_lock import binaural, preview_app
from slideshow_lock.binaural import FADE_SECONDS, BinauralPlayer, SlideshowSound
from slideshow_lock.preview_app import SOUND_CLOSE_MARGIN_SECONDS, start_preview
from slideshow_lock.service import PreviewSlideshow
from slideshow_lock.state_machine import State, StateMachine
from tests.fakes import (
    FakeIdleWatcher,
    FakeInhibition,
    FakeSessionLock,
    FakeSettings,
)
from tests.test_binaural import FakeBackend
from tests.test_image_source import FakeWatcher, ManualScheduler, make_image, started
from tests.test_preview import FakeClock as PreviewClock
from tests.test_preview import FakeScaler, FakeWindow
from tests.test_preview import FakeSettings as PreviewSettings
from tests.test_preview_app import FakeApplication
from tests.test_service_wiring import FakeController, FakeSource
from tests.timeout_guard import per_test_deadline  # noqa: F401  (autouse fixture)


class SoundSettings:
    """The four getters of the binaural sound, and nothing else."""

    def __init__(self, enabled=True, volume=30, beat="alpha", carrier=200):
        self.enabled, self.volume, self.beat, self.carrier = enabled, volume, beat, carrier

    def get_binaural_enabled(self):
        return self.enabled

    def get_binaural_volume(self):
        return self.volume

    def get_binaural_beat(self):
        return self.beat

    def get_binaural_carrier_hz(self):
        return self.carrier


class Moment:
    """The one clock of a test: the state machine's, the player's, the events' time."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class SoundRig:
    """A real player over a fake backend, driven by hand (``pump``), and the events it causes."""

    def __init__(self, moment, events, settings=None):
        self.moment, self.events = moment, events
        self.backends = []
        self.player = BinauralPlayer(self._make, clock=moment, start_thread=False)
        self.sound = SlideshowSound(settings or SoundSettings(), self.player)

    def _make(self):
        backend = FakeBackend(self.events)
        self.backends.append(backend)
        return backend

    @property
    def backend(self):
        return self.backends[-1]

    def pump(self):
        return self.player.pump(self.moment())


class Deferred:
    """The main loop's poster as a test can hold it: what is deferred runs when the test says."""

    def __init__(self):
        self.queue = []

    def __call__(self, fn):
        self.queue.append(fn)

    def run(self):
        queue, self.queue = self.queue, []
        for fn in queue:
            fn()


class TimedLock(FakeSessionLock):
    """Notes the time of every lock call, and the order among the other events of the test."""

    def __init__(self, moment, events):
        super().__init__()
        self._moment, self._events = moment, events
        self.times = []

    def lock(self, on_done):
        self.times.append(self._moment())
        self._events.append("lock")
        super().lock(on_done)


class Stand:
    """Idle watcher, lock and state machine around a ``PreviewSlideshow`` with a sound."""

    def __init__(self, sound, *, moment=None, events=None, defer=None, controller=None):
        self.moment = moment or Moment()
        self.events = [] if events is None else events
        self.defer = defer or Deferred()
        self.controller = controller or FakeController()
        self.slideshow = PreviewSlideshow(
            self.controller, FakeSource(), None, sound=sound, defer=self.defer
        )
        self.idle = FakeIdleWatcher()
        self.inhibition = FakeInhibition()
        self.lock = TimedLock(self.moment, self.events)
        self.machine = StateMachine(
            idle=self.idle,
            inhibition=self.inhibition,
            lock=self.lock,
            slideshow=self.slideshow,
            settings=FakeSettings(idle=120, grace=0),
            guard=None,
            clock=self.moment,
        )
        self.machine.enable()

    def start_slideshow(self):
        self.idle.fire_idle()
        assert self.machine.state is State.SLIDESHOW_RUNNING


class RecordingSound:
    """A sound that notes its calls (and may raise), for the order of events."""

    def __init__(self, events, start_error=None, stop_error=None):
        self.events, self.start_error, self.stop_error = events, start_error, stop_error

    def start(self):
        self.events.append("sound start")
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.events.append("sound stop")
        if self.stop_error is not None:
            raise self.stop_error
        return 0.0


# -- the slideshow starts, the sound follows -----------------------------------------------------


def test_the_sound_starts_once_the_windows_are_up_and_not_before():
    events = []
    controller = FakeController()
    real_start = controller.start
    controller.start = lambda: (events.append("windows up"), real_start())
    stand = Stand(RecordingSound(events), events=events, controller=controller)
    stand.start_slideshow()
    assert events == ["windows up", "sound start"]


def test_no_sound_when_the_slideshow_does_not_start_for_want_of_a_monitor():
    events = []
    stand = Stand(RecordingSound(events), events=events, controller=FakeController(monitors=0))
    assert stand.slideshow.start() == "no monitor found"
    assert "sound start" not in events


def test_no_sound_when_the_slideshow_does_not_start_for_want_of_a_picture():
    events = []
    slideshow = PreviewSlideshow(
        FakeController(), FakeSource(current=None), None, sound=RecordingSound(events)
    )
    assert slideshow.start() is not None
    assert events == []


def test_a_slideshow_without_a_sound_is_the_old_one():
    stand = Stand(None)
    stand.start_slideshow()
    stand.controller.input()
    assert stand.events == ["lock"]
    assert stand.defer.queue == []


def test_the_sound_plays_at_the_set_tone_and_fades_in_when_the_slideshow_starts():
    moment = Moment()
    events = []
    rig = SoundRig(moment, events, SoundSettings(volume=40, beat="theta", carrier=300))
    stand = Stand(rig.sound, moment=moment, events=events)
    stand.start_slideshow()
    assert rig.pump() == binaural.STEP_SECONDS
    assert rig.backend.opened == (300.0, 306.0)
    assert rig.backend.volumes == [0.0]
    moment.advance(FADE_SECONDS / 2)
    rig.pump()
    assert rig.backend.volumes[-1] == pytest.approx(0.20)
    moment.advance(FADE_SECONDS / 2)
    rig.pump()
    assert rig.backend.volumes[-1] == pytest.approx(0.40)


def test_a_sound_that_is_off_in_the_settings_makes_no_pipeline():
    moment = Moment()
    rig = SoundRig(moment, [], SoundSettings(enabled=False))
    stand = Stand(rig.sound, moment=moment)
    stand.start_slideshow()
    assert rig.pump() is None
    assert rig.backends == []


def test_the_settings_are_read_at_each_start_so_a_change_is_heard_next_time():
    moment = Moment()
    settings = SoundSettings(enabled=False)
    rig = SoundRig(moment, [], settings)
    stand = Stand(rig.sound, moment=moment)
    stand.start_slideshow()
    assert rig.pump() is None and rig.backends == []
    stand.controller.input()
    stand.defer.run()
    settings.enabled = True
    settings.beat = "beta"
    stand.lock.set_locked(False)
    stand.idle.fire_idle()
    assert rig.pump() is not None
    assert rig.backend.opened == (200.0, 214.0)


# -- the lock first, the sound after -------------------------------------------------------------


def test_input_locks_first_and_the_fade_out_begins_after_the_lock_call():
    events = []
    stand = Stand(RecordingSound(events), events=events)
    stand.start_slideshow()
    events.clear()
    stand.controller.input()
    assert events == ["lock"]  # the lock is out; the sound has not been told yet
    stand.defer.run()
    assert events == ["lock", "sound stop"]


def test_a_stop_the_state_machine_asks_for_locks_before_the_fade_out_too():
    """An application that inhibits idle ends the slideshow from the machine's side
    (``slideshow.stop()``), and after the grace period that locks."""
    events = []
    stand = Stand(RecordingSound(events), events=events)
    stand.start_slideshow()
    events.clear()
    stand.moment.advance(5)
    stand.inhibition.set_inhibited(True)
    assert events == ["lock"]
    stand.defer.run()
    assert events == ["lock", "sound stop"]


def test_the_lock_call_is_made_while_the_sound_still_plays_at_full_level():
    """Measured in time on the fake clock: at the moment of the lock call the sound has not begun
    to fade, and the call is made before the fade-out is a thousandth over."""
    moment = Moment()
    events = []
    rig = SoundRig(moment, events)
    stand = Stand(rig.sound, moment=moment, events=events)
    stand.start_slideshow()
    rig.pump()
    moment.advance(60)  # a long slideshow: the fade-in is long over
    rig.pump()
    assert rig.backend.volumes[-1] == pytest.approx(0.30)
    input_at = moment()
    stand.controller.input()
    assert stand.lock.times == [input_at]  # the lock call, at the time of the input
    assert rig.backend.volumes[-1] == pytest.approx(0.30)  # still full: the fade-out has not begun
    assert not rig.backend.closed
    stand.defer.run()  # now the sound is told
    rig.pump()
    fade_ends_at = input_at + FADE_SECONDS
    assert stand.lock.times[0] < fade_ends_at
    moment.advance(FADE_SECONDS / 2)
    rig.pump()
    assert rig.backend.volumes[-1] == pytest.approx(
        0.15
    )  # half way down, and the lock was long out
    assert not rig.backend.closed
    moment.advance(FADE_SECONDS / 2)
    assert rig.pump() == pytest.approx(binaural.TAIL_SECONDS)  # at 0, the output plays out
    moment.advance(binaural.TAIL_SECONDS)
    assert rig.pump() is None
    assert rig.backend.closed
    assert rig.backend.volumes[-1] == pytest.approx(0.0)
    assert stand.lock.lock_calls == 1


def test_a_slideshow_that_ends_by_the_grace_period_fades_out_without_a_lock():
    events = []
    stand = Stand(RecordingSound(events), events=events)
    stand.machine._settings.values["lock-grace-period-seconds"] = 30
    stand.start_slideshow()
    events.clear()
    stand.controller.input()  # within the grace period: no lock
    assert events == []
    stand.defer.run()
    assert events == ["sound stop"]


def test_the_stop_is_deferred_to_the_main_loop_by_default():
    """With no poster given the service defers to the main loop of the thread it was made on: the
    controller's stop returns before the sound is told, the next turn of the loop tells it."""
    events = []
    controller = FakeController()
    slideshow = PreviewSlideshow(controller, FakeSource(), None, sound=RecordingSound(events))
    assert slideshow.start() is None
    events.clear()
    controller.stop()
    assert events == []
    context = GLib.MainContext.default()
    for _ in range(50):
        if events:
            break
        context.iteration(False)
    assert events == ["sound stop"]


def test_a_poster_that_fails_does_not_stop_the_lock(caplog):
    """The fade-out is begun through the main loop's poster; if that raises, the slideshow's end
    and the lock go on all the same."""

    def broken(_fn):
        raise RuntimeError("the loop is gone")

    events = []
    stand = Stand(RecordingSound(events), events=events, defer=broken)
    stand.start_slideshow()
    with caplog.at_level(logging.ERROR):
        stand.controller.input()
    assert stand.machine.state is State.LOCKED
    assert "lock" in events


# -- the lock does not depend on the sound -------------------------------------------------------


@pytest.mark.parametrize(
    "start_error, stop_error",
    [
        (RuntimeError("no device"), None),
        (None, RuntimeError("stuck")),
        (OSError("x"), OSError("y")),
    ],
)
def test_a_sound_that_raises_neither_stops_the_slideshow_nor_the_lock(
    caplog, start_error, stop_error
):
    events = []
    stand = Stand(RecordingSound(events, start_error, stop_error), events=events)
    with caplog.at_level(logging.ERROR):
        stand.start_slideshow()  # the slideshow is up although the sound failed
        assert stand.controller.running
        stand.controller.input()
        stand.defer.run()
    assert "lock" in events
    assert stand.machine.state is State.LOCKED
    assert stand.lock.locked


def test_a_player_whose_device_is_missing_costs_a_log_line_and_nothing_else(caplog):
    moment = Moment()
    events = []
    player = BinauralPlayer(
        lambda: (_ for _ in ()).throw(RuntimeError("no sound device")),
        clock=moment,
        start_thread=False,
    )
    stand = Stand(SlideshowSound(SoundSettings(), player), moment=moment, events=events)
    with caplog.at_level(logging.DEBUG):
        stand.start_slideshow()
        assert player.pump(moment()) is None
        stand.controller.input()
        stand.defer.run()
        assert player.pump(moment()) is None
    assert stand.machine.state is State.LOCKED
    warnings = [
        r for r in caplog.records if "[sound]" in r.getMessage() and r.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1


def test_a_device_that_hangs_does_not_delay_the_lock():
    """The real worker thread, on a factory that never comes back: the slideshow starts and the
    input is locked at once. The sound is on its own thread, the main loop is never in it."""
    release = threading.Event()
    entered = threading.Event()

    def hanging():
        entered.set()
        release.wait(30)
        return FakeBackend([])

    events = []
    player = BinauralPlayer(hanging)
    stand = Stand(SlideshowSound(SoundSettings(), player), events=events)
    try:
        began = time.monotonic()
        stand.start_slideshow()
        assert entered.wait(5), "the worker never reached the device"
        stand.controller.input()
        stand.defer.run()
        took = time.monotonic() - began
        assert stand.machine.state is State.LOCKED
        assert events == ["lock"]
        assert took < 0.5, f"the lock waited {took:.2f}s for a hung sound device"
    finally:
        release.set()
        player.close()


# -- the preview ---------------------------------------------------------------------------------


class SoundDouble:
    """``SlideshowSound`` as ``start_preview`` uses it; *wait* is what ``stop`` answers."""

    instances = []

    def __init__(self, settings, wait=FADE_SECONDS):
        self.settings = settings
        self.wait = wait
        self.calls = []
        self.close_waits = []
        SoundDouble.instances.append(self)

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")
        return self.wait

    def close(self, wait=True):
        self.calls.append("close")
        self.close_waits.append(wait)


class HoldingApplication(FakeApplication):
    def __init__(self):
        super().__init__()
        self.events = []

    def hold(self):
        self.events.append("hold")

    def release(self):
        self.events.append("release")


def preview_with_sound(tmp_path, monkeypatch, application=None, wait=FADE_SECONDS, windows=None):
    make_image(tmp_path / "a.png")
    source = started(tmp_path, (FakeWatcher(), ManualScheduler()))
    windows = [FakeWindow()] if windows is None else windows
    clock = PreviewClock()
    SoundDouble.instances.clear()
    monkeypatch.setattr(preview_app, "open_monitor_windows", lambda: windows)
    monkeypatch.setattr(preview_app, "ImageScaler", FakeScaler)
    monkeypatch.setattr(preview_app, "GLibClock", lambda: clock)
    monkeypatch.setattr(preview_app, "SlideshowSound", lambda settings: SoundDouble(settings, wait))
    controller = start_preview(PreviewSettings(), source, application)
    (sound,) = SoundDouble.instances
    return controller, sound, clock, windows


def test_the_preview_starts_its_sound_once_it_shows(tmp_path, monkeypatch):
    controller, sound, _clock, _windows = preview_with_sound(tmp_path, monkeypatch)
    try:
        assert controller.running
        assert sound.calls == ["start"]
    finally:
        controller.stop()


def test_a_preview_that_has_no_monitor_starts_no_sound(tmp_path, monkeypatch):
    controller, sound, _clock, _windows = preview_with_sound(tmp_path, monkeypatch, windows=[])
    assert not controller.running
    assert sound.calls == []


def test_the_preview_fades_the_sound_out_when_it_ends_and_takes_it_down_after_the_fade(
    tmp_path, monkeypatch
):
    controller, sound, clock, windows = preview_with_sound(tmp_path, monkeypatch)
    windows[0].fire_input()
    assert not controller.running
    assert sound.calls == ["start", "stop"]  # the fade-out has begun, the pipeline is not down yet
    clock.advance(FADE_SECONDS)
    assert sound.calls == ["start", "stop"]
    clock.advance(SOUND_CLOSE_MARGIN_SECONDS)
    assert sound.calls == ["start", "stop", "close"]


def test_the_application_is_kept_alive_until_the_fade_out_is_over(tmp_path, monkeypatch):
    """The command line ends with the preview and the settings window may be closing: a program
    that ends in the middle of the fade-out cuts the sound off."""
    application = HoldingApplication()
    controller, sound, clock, windows = preview_with_sound(tmp_path, monkeypatch, application)
    windows[0].fire_input()
    assert application.events == ["hold"]
    clock.advance(FADE_SECONDS)
    assert application.events == ["hold"]
    clock.advance(SOUND_CLOSE_MARGIN_SECONDS)
    assert application.events == ["hold", "release"]
    assert sound.calls[-1] == "close"


def test_a_preview_that_played_no_sound_keeps_nothing_alive_and_closes_at_once(
    tmp_path, monkeypatch
):
    application = HoldingApplication()
    controller, sound, clock, windows = preview_with_sound(
        tmp_path, monkeypatch, application, wait=0.0
    )
    windows[0].fire_input()
    assert application.events == []
    assert sound.calls == ["start", "stop", "close"]


def test_a_preview_without_an_application_closes_its_sound_after_the_fade_out(
    tmp_path, monkeypatch
):
    controller, sound, clock, windows = preview_with_sound(tmp_path, monkeypatch)
    windows[0].fire_input()
    clock.advance(FADE_SECONDS + SOUND_CLOSE_MARGIN_SECONDS)
    assert sound.calls == ["start", "stop", "close"]


def test_the_preview_never_waits_for_the_sound_worker_when_it_closes_the_sound(
    tmp_path, monkeypatch
):
    """The preview closes on the main loop of the settings window: a device that hangs while it is
    taken down must not stop that loop for the 2 s that the service's exit path may wait."""
    controller, sound, clock, windows = preview_with_sound(tmp_path, monkeypatch)
    windows[0].fire_input()
    clock.advance(FADE_SECONDS + SOUND_CLOSE_MARGIN_SECONDS)
    assert sound.close_waits == [False]
    controller, sound, clock, windows = preview_with_sound(tmp_path, monkeypatch, wait=0.0)
    windows[0].fire_input()
    assert sound.close_waits == [False]


def test_the_preview_reads_the_sound_from_its_own_settings(tmp_path, monkeypatch):
    settings = PreviewSettings()
    make_image(tmp_path / "a.png")
    source = started(tmp_path, (FakeWatcher(), ManualScheduler()))
    monkeypatch.setattr(preview_app, "open_monitor_windows", lambda: [FakeWindow()])
    monkeypatch.setattr(preview_app, "ImageScaler", FakeScaler)
    monkeypatch.setattr(preview_app, "GLibClock", lambda: PreviewClock())
    SoundDouble.instances.clear()
    monkeypatch.setattr(preview_app, "SlideshowSound", lambda s: SoundDouble(s))
    controller = start_preview(settings, source)
    controller.stop()
    assert SoundDouble.instances[0].settings is settings


def test_the_preview_on_a_real_player_plays_the_tone_the_settings_ask_for(tmp_path, monkeypatch):
    """No double: the preview's own ``SlideshowSound`` on a player over a fake backend."""
    moment = Moment()
    rig = SoundRig(moment, [], SoundSettings(volume=50, beat="delta", carrier=250))
    make_image(tmp_path / "a.png")
    source = started(tmp_path, (FakeWatcher(), ManualScheduler()))
    windows = [FakeWindow()]
    monkeypatch.setattr(preview_app, "open_monitor_windows", lambda: windows)
    monkeypatch.setattr(preview_app, "ImageScaler", FakeScaler)
    monkeypatch.setattr(preview_app, "GLibClock", lambda: PreviewClock())
    monkeypatch.setattr(preview_app, "SlideshowSound", lambda settings: rig.sound)
    controller = start_preview(PreviewSettings(), source)
    try:
        rig.pump()
        assert rig.backend.opened == (250.0, 254.0)
        moment.advance(FADE_SECONDS)
        rig.pump()
        assert rig.backend.volumes[-1] == pytest.approx(0.5)
    finally:
        controller.stop()


# -- the command line ends with the preview, but not before the sound has faded out ---------------


class ReleasingApplication:
    """``Gtk.Application`` as ``preview_app.main`` uses it: it holds, releases and quits."""

    instances = []

    def __init__(self, application_id=None):
        self.handlers = {}
        self.events = []
        ReleasingApplication.instances.append(self)

    def connect(self, name, callback):
        self.handlers[name] = callback

    def hold(self):
        self.events.append("hold")

    def release(self):
        self.events.append("release")

    def quit(self):
        self.events.append("quit")

    def run(self, _argv):
        self.handlers["activate"](self)
        return 0


def test_the_command_line_gives_its_own_hold_back_instead_of_quitting(monkeypatch):
    """``quit`` ends the application at once and would cut the fade-out off; ``release`` lets it
    end when the last hold (the fade-out's) is given back."""
    callbacks = []
    controller = SimpleNamespaceController()
    ReleasingApplication.instances.clear()
    monkeypatch.setattr(
        preview_app,
        "Gtk",
        SimpleNamespace(
            Application=ReleasingApplication,
            ApplicationInhibitFlags=preview_app.Gtk.ApplicationInhibitFlags,
        ),
    )
    monkeypatch.setattr(
        preview_app.Gio.SettingsSchemaSource,
        "get_default",
        staticmethod(lambda: SimpleNamespace(lookup=lambda _id, _recursive: object())),
    )
    monkeypatch.setattr(preview_app, "Settings", lambda: PreviewSettings())
    monkeypatch.setattr(
        preview_app,
        "build_source",
        lambda _settings: SimpleNamespace(start=lambda: None, stop=lambda: None),
    )
    monkeypatch.setattr(preview_app, "start_preview", lambda *_args: controller)
    monkeypatch.setattr(preview_app, "unix_signal_add", lambda *_args: 1)
    monkeypatch.setattr(preview_app.GLib, "idle_add", lambda fn, *args: callbacks.append(fn) or 1)
    assert preview_app.main([]) == 0
    (application,) = ReleasingApplication.instances
    assert application.events == ["hold"]
    controller.stop_by_input()  # the preview ends: the command line queues the end of its hold
    for fn in callbacks:
        fn()
    assert application.events == ["hold", "release"]


class SimpleNamespaceController:
    """The preview controller as ``main`` sees it: running, and told when it stops."""

    running = True

    def __init__(self):
        self.listeners = []

    def connect_stopped(self, callback):
        self.listeners.append(callback)

    def stop(self, _reason="requested"):
        pass

    def stop_by_input(self):
        for callback in self.listeners:
            callback("input")


def test_the_preview_settings_give_the_sound_values_of_this_run_over_the_stored_ones():
    from slideshow_lock.preview_app import SessionSettings
    from slideshow_lock.settings import (
        KEY_BINAURAL_BEAT,
        KEY_BINAURAL_CARRIER_HZ,
        KEY_BINAURAL_ENABLED,
        KEY_BINAURAL_VOLUME,
        Settings,
    )

    plain = SessionSettings(Settings(), {})
    assert (
        plain.get_binaural_enabled(),
        plain.get_binaural_volume(),
        plain.get_binaural_beat(),
        plain.get_binaural_carrier_hz(),
    ) == (False, 30, "alpha", 200)
    replaced = SessionSettings(
        Settings(),
        {
            KEY_BINAURAL_ENABLED: True,
            KEY_BINAURAL_VOLUME: 70,
            KEY_BINAURAL_BEAT: "beta",
            KEY_BINAURAL_CARRIER_HZ: 440,
        },
    )
    assert (
        replaced.get_binaural_enabled(),
        replaced.get_binaural_volume(),
        replaced.get_binaural_beat(),
        replaced.get_binaural_carrier_hz(),
    ) == (True, 70, "beta", 440)
    tone = binaural.tone_from_settings(replaced)
    assert (tone.left_hz, tone.right_hz, tone.volume) == (440.0, 454.0, 0.7)
