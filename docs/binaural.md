# The binaural sound (1.0.11)

Two sine tones play while the slideshow shows, and in the preview: the **carrier** (200 Hz by default) in the
left ear and the carrier plus the **beat** in the right one. It is off by default. The settings
(`binaural-enabled`, `binaural-volume`, `binaural-beat`, `binaural-carrier-hz`; `docs/preferences.md`) are read when
the slideshow starts, so a change is heard at the next one.

| | |
|---|---|
| Beat presets | delta 4 Hz, theta 6 Hz, alpha 10 Hz (the default), beta 14 Hz |
| Carrier | 100 to 500 Hz (200 by default); the right channel is the carrier plus the beat |
| Volume | 0 to 100 percent (30 by default), the level the fade-in ends at |
| Fade | linear in amplitude: 0 -> the volume in 1.5 s at the start, the level now -> 0 in 1.5 s at the end |

## How it is made

`slideshow_lock/binaural.py`, on its own: the standard library at module level, no D-Bus, no session, no lock, no GTK.

* **Two separate channels.** Two mono `audiotestsrc` elements, each moved to one side by `audioconvert` (its
  `mix-matrix`) and put together by `audiomixer`, then `audioconvert`, `audioresample`, `volume` and `autoaudiosink`.
  The tones come from `gstreamer1-plugins-base` (`audiotestsrc`, `audioconvert`, `audioresample`, `audiomixer`,
  `volume`); `autoaudiosink` is in `gstreamer1-plugins-good`. Both are in Fedora and in the EL10 AppStream, EPEL is not
  needed (measured in the repository metadata of Fedora 43 and CentOS Stream 10, 2026-11-11). The spec has
  `Recommends` for both: the sound is off by default and a missing element is a log line, not a failed start.
* **The tones are not live** (`audiotestsrc is-live=false`, `binaural.LIVE_TONES`): they are made as fast as the sink
  takes them, and the sink paces the pipeline with its own clock. 1.0.11 and 1.0.12 had live tones, and the sound
  stuttered now and then (Attila, RHEL 10). Measured 2026-11-12 in a CentOS Stream 10 root (GStreamer 1.26.7, PipeWire
  1.4.11, `pipewire-pulse` with a null sink, `autoaudiosink` -> `pulsesink`), 120 s each, before -> after:
  "correct clock skew" 34 -> 0, `audiomixer` late 50 -> 0, dropped 44 -> 0. Two causes. With live tones the sink cannot
  give the pipeline its clock in time (its ring buffer is not up yet when the clock is chosen), so it follows the system
  clock and jumps 20 ms in its buffer every 9 s or so, a click in both ears at once. And `audiomixer` (an aggregator,
  `latency` 0) does not wait for a live tone that is late: a tone whose thread is held up for a moment (a busy machine,
  a picture change) loses that piece, a hole of silence in one ear (`tests/test_binaural.py` holds one tone up for 0.3 s
  and counts the silence). Not live, the mixer waits and the sink's buffer (200 ms) covers the wait. The cost: a volume
  change is heard up to that buffer later, which the 1.5 s fades do not notice.
* **A thread of its own.** GStreamer is touched only on the worker thread of `BinauralPlayer`. `start`, `stop` and
  `close` only queue a command and return at once, so a sound device that hangs cannot stop the main loop, and the
  lock is made on the main loop.
* **The ramp** is `BinauralPlayer.pump(now)`: the worker calls it (every 20 ms while a ramp runs), a test calls it with a
  time of its own. The fade-in counts from the moment the pipeline is up; a stop in the middle of a fade-in fades out
  from where it is; a start in the middle of a fade-out turns it up again on the same pipeline.

## The lock never depends on the sound

The slideshow and the lock are the parts the program is for; the sound is a side dish.

* **Order.** `PreviewSlideshow` begins the fade-out through the main loop's poster (`loop.current_poster`), so it runs
  after the code that ended the slideshow has gone on to make its lock call: the lock call goes out first, the sound
  fades in the background. The state machine does not know the sound exists; no protocol of `session.py` has it.
* **Failure.** No GStreamer, a missing element, no sound device, or a pipeline that does not start: one `[sound]` warning, the slideshow goes on without sound, and the next slideshow tries again. A device that goes away in the middle of a slideshow is not noticed: nothing reads the bus of the pipeline, so there is no `[sound]` warning for it (not measured with a real device). A sound that plays logs one `[sound] playing (...)` line at INFO, which is what to look for when no sound is heard. `start` and
  `stop` never raise; `PreviewSlideshow` catches whatever the sound raises all the same.
* **The schema.** The service reads the four keys when a slideshow starts. GLib aborts the process at a key the
  installed schema lacks, and the service is what locks, so `Settings` asks the schema first (`_get_with_default`) and
  gives the default for a missing key, as it does for the older boolean keys.
* **D11.** The preview modules may not name the lock, the session or the bus (`tests/test_preview.py`): `binaural.py`
  is scanned like every other module that is not on the lock side, and it passes without an allowance.

## The preview

`preview_app.start_preview` starts a `SlideshowSound` once the windows are up and fades it out when the preview ends.
It holds the `Gtk.Application` until the fade-out is over, so that the command line (which ends with the preview) and
a closing settings window do not cut the sound off.

## Not measured

The sound itself on a real device. This was built in a container with no sound device (`autoaudiosink` falls back to a
fake sink there): the channels were measured from the samples of an `appsink` (left and right frequency, volume), not
heard. Try it with headphones: the beat should be a slow pulse, and the fades should be smooth.
