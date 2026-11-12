# Acceptance Criteria for Core Behavior (3.1-3.7)

Owner: product requirements. Scope: the core behavior, sections 3.1-3.7 below.
No D-Bus/state-machine design (`docs/architecture/dbus-state-machine.md`) and no test
implementation here.

Legend:
- **[AUTO]** — verifiable by an automated test against a simulated D-Bus (CI-runnable).
- **[MANUAL]** — verifiable ONLY in a real GNOME/Wayland session on real hardware.
  Goes to the manual test list, never claimed as "working" from CI alone.

## 3.1 — Idle-triggered slideshow start

**AC-3.1-1 [AUTO]**
Given idle time has reached the configured threshold (default 120 s) AND no application
holds an active idle-inhibit AND the session is not locked AND no slideshow is currently
running,
When the threshold is reached,
Then the service starts a fullscreen slideshow sourced from the configured image folder
(verified at the control-layer call, not the rendered output; what appears on screen is
not claimed by this [AUTO] criterion).

**AC-3.1-2 [MANUAL]** (the idle trigger itself is AC-3.1-1, automated)
Given the slideshow has started (AC-3.1-1),
When the system has more than one connected monitor,
Then exactly one fullscreen slideshow window appears per monitor.
(Multi-monitor rendering cannot be exercised on a simulated bus; this line stays
"not measured" in CI and is part of the manual test list.)

## 3.2 — Input stops the slideshow; grace-period lock decision

**AC-3.2-1 [AUTO]**
Given the slideshow is actively running,
When the first mouse movement, click, or key press occurs,
Then the slideshow window closes immediately, independent of any timing calculation
(verified at the control-layer call, not the rendered output; what appears on screen is
not claimed by this [AUTO] criterion).

**AC-3.2-2 [AUTO]** (boundary set at `G = 0` and at a `G > 0`, strict `<`)
Given the slideshow has been running for duration `t` seconds since it started, with a
configured grace period `G` seconds,
When input arrives at time `t`,
Then the lock-eligibility decision is:
- if `G = 0`: a single boundary point, `t = 0` -> lock-eligible.
- if `G > 0`: three boundary points, `t = 0` -> not lock-eligible, `t = G-1` -> not
  lock-eligible, `t = G` -> lock-eligible.
("Lock-eligible" here means: the grace-period test alone would allow a lock; whether the
lock actually fires is governed by AC-3.3.)

## 3.3 — Lock only for a service-started, currently-running, idle-triggered slideshow

**AC-3.3-1 [AUTO]** (positive case)
Given a slideshow is currently running AND it was started by the idle-timeout path (not by
the Preview action) AND the input is lock-eligible per AC-3.2-2,
When that input is processed,
Then the session lock is actually invoked.

**AC-3.3-2 [AUTO]** (negative case: a preview never locks, and its input comes from its own windows)
Given a slideshow is running because the user triggered "Preview" in the settings app,
When any input occurs, at any time, under any grace-period value,
Then the session is NEVER locked as a result of that input.

**AC-3.3-3 [AUTO]** (negative case)
Given the slideshow has NOT yet started (idle timer running, threshold not yet reached, or
no slideshow active for any other reason),
When input occurs,
Then no lock occurs; the event is treated as an ordinary idle-timer reset only.

> Note: AC-3.3-1/2/3 rest on how 3.3 and 3.5 fit together, which is decided (see "Open items" below). The
> sleep-triggered lock is an independent trigger, so the idle-triggered-and-running
> precondition in this section does not apply to it. These three criteria are final as written.

## 3.4 — Idle-inhibit blocks start (and stops an active slideshow if raised mid-run, locking once the grace period is over)

**AC-3.4-1 [AUTO]**
Given an application holds an active idle-inhibit at the moment the idle threshold would be
reached,
When the threshold is reached,
Then the slideshow does NOT start.

**AC-3.4-2 [AUTO]**
Given an idle-triggered slideshow has been running for `t` seconds, with a configured
grace period `G` seconds,
When an application raises an idle-inhibit during that run,
Then the slideshow stops immediately, and the lock decision is the same one as for input
(AC-3.2-2, strict `<`, the same elapsed time):
- if `t < G`: the session is NOT locked as a result of this stop.
- if `t >= G`: the session locks at once, without waiting for the next input.
(With `G = 0` the lock is always due.) A slideshow started by the Preview action is never
locked by this event. The idle-inhibit suppresses only the idle-start branch and this stop;
it never suppresses the sleep-lock branch (AC-3.5-2).

## 3.5 — Sleep stops the slideshow and locks (independent trigger)

**AC-3.5-1 [AUTO]** (resolved: an independent trigger, inside a held delay inhibitor; no revision pending)
Given the system is about to suspend (`PrepareForSleep(true)` received while the sleep
delay-inhibitor is held),
When this signal is processed,
Then: (i) if a slideshow is running, it stops; (ii) the session locks — this lock fires
regardless of whether a slideshow was running and regardless of how it was started
(idle-triggered or Preview). This is an independent trigger, not a special case of AC-3.3.

**AC-3.5-2 [AUTO]** (safety condition)
Given an application holds an active idle-inhibit AND `PrepareForSleep(true)` is received,
When this signal is processed,
Then the session still locks. An idle-inhibit must suppress only the idle-start branch
(3.1/3.4), never the sleep-lock branch (3.5).

**AC-3.5-3 [AUTO]** (the round trip has logind's `InhibitDelayMaxSec`, 5 s by default)
Given the `Lock` round-trip starts upon `PrepareForSleep(true)`,
When `PrepareForSleep(false)` arrives before the round-trip has completed,
Then a WARNING-level log entry is written identifying the incomplete round-trip
(verified with a simulated bus plus an artificial delay — exact log wording/level is
`docs/logging-and-lifecycle.md`'s, not redefined here).

**AC-3.5-4 [MANUAL]** (a real suspend cannot be simulated)
Given a real suspend/resume cycle on the target hardware (RHEL 10.2, GNOME, Wayland),
When the machine wakes,
Then the login screen is shown, with no slideshow and no unlocked session visible.

## 3.6 — No-op when the session is already locked

**AC-3.6-1 [AUTO]**
Given the session is already locked (`ScreenSaver.GetActive` = true, or `ActiveChanged`
reported true),
When an idle-threshold event, an input event, or an inhibit event occurs,
Then the service takes no action: no slideshow start, no stop call, no additional lock
call.

## 3.7 — Missing or empty image folder

**AC-3.7-1 [AUTO]**
Given the configured image folder is missing, or exists but contains zero valid image
files,
When the idle threshold is reached,
Then: (i) no slideshow starts; (ii) a WARNING-level log entry is written identifying the
cause; (iii) the service process keeps running (no crash, no exit).
(The level follows the logging convention of `docs/logging-and-lifecycle.md`; exact message wording and
format are that document's, this criterion only fixes the externally observable behavior.)

## Out of scope here

- D-Bus abstraction layer and state machine design -> `docs/architecture/dbus-state-machine.md`.
- Test implementation (fixtures, simulated bus harness) -> `tests/`.
- Exact log message wording/level -> `docs/logging-and-lifecycle.md`.
- Settings storage format (GSettings vs TOML) -> decided separately (GSettings, `slideshow_lock/settings.py`).

## Open items (need a decision before these criteria can be called final)

1. **3.3 vs 3.5 interaction -- resolved.** Before suspend, the session locks
   unconditionally, independent of whether a slideshow was running or how it was started.
   Locking is a standalone safety function, not a side effect of the slideshow. AC-3.5-1 is
   final as written; no revision pending.
2. **Idle-inhibit raised during a running idle slideshow -- resolved.** The slideshow stops;
   if it ran for the grace period or longer (the same strict `<` as for input) the session
   also locks at once, within the grace period it does not. AC-3.4-2 is final as written;
   no revision pending.
