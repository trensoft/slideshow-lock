# Slideshow Lock: features

A screensaver for GNOME on Wayland. When you step away, your pictures fill the screen. When you come back, your session is locked (unless you return within a grace period you set).

## Away from the desk

- **Your pictures instead of a blank screen.** After a set idle time, a fullscreen slideshow starts.
- **Every monitor.** With more than one monitor, all of them show the same picture and change together.
- **Locks when you come back.** The first key press, click or mouse movement ends the slideshow and locks the session.
- **A grace period for quick returns.** If you come back within the grace period you set, the slideshow ends without locking. The default is 0 seconds: every return locks.
- **Locks before suspend.** Before the computer suspends, the session is locked, even while the slideshow is running.
- **Holds back for other apps.** When an application asks the desktop not to go idle, the slideshow does not start. If an app asks for that while the slideshow is already running, the slideshow ends. If the grace period is already over, the session locks as well.

## Your pictures

- **Pick a folder, subfolders included.** You choose the folder in the settings window, and pictures in its subfolders play too. Without a choice, your Pictures folder is used.
- **Picks up changes on its own.** Pictures you copy in or delete are noticed while it runs, with no restart.
- **Starts before the whole folder is read.** In a large folder, the first picture is ready long before the rest have been found.
- **Leaves screenshots out.** By default, folders named like a screenshot folder and files named like a screenshot are skipped. A folder you chose on purpose is not emptied. One switch brings them back.
- **Fill or fit.** Fill the screen and crop the edges, or show the whole picture with bars on the sides.
- **Tall pictures can scroll.** If you turn this on, a portrait picture can slowly scroll from top to bottom instead of being cropped. It is off by default to save battery.
- **Random or by name.** Show pictures in random order, or alphabetically by file name.
- **Sample pictures to start with.** At the first login, seven sample pictures are copied once into your Pictures folder. A picture you delete does not come back, and none of your files are overwritten.
- **Common formats.** JPEG, PNG, GIF and TIFF play on RHEL 10, AlmaLinux and Rocky. BMP and WebP need an extra loader from EPEL 10.

## Transitions

- **Ten transitions.** Cross-fade, fade through black, slide in, push, Ken Burns, zoom, wipe, circle reveal, blur and rotate. You can also choose a random mix, or no transition at all.
- **You set the length.** From 0.2 to 5 seconds.
- **Respects your desktop.** If animations are turned off on your desktop, pictures change at once.

## Settings

- **One window, saved as you go.** Every change is stored the moment you make it, and the running screensaver picks it up immediately. There is no Save button.
- **Set in seconds.** The idle time and the grace period are set in seconds. Each picture can stay on screen from 1 second up to almost a full day.
- **Try it first.** The Preview button plays the slideshow with your settings. A preview never locks the session, and any key or mouse movement ends it.

## Install and run

- **RPM packages.** For RHEL 10 and its rebuilds AlmaLinux and Rocky, and for Fedora, from the COPR project `trensoft/slideshow-lock`. Updates come through `dnf`.
- **Starts at login.** After installation, it starts at every GNOME login. The settings window opens once at the first login, if no folder has been chosen yet.
- **Stop it when you need to.** `systemctl --user stop slideshow-lock` stops it until the next login. The README shows how to keep it from starting at all.
- **Manual pages.** `man slideshow-lock` and `man slideshowlock`.

## Languages and licence

- **41 languages.** English and 40 translations. 35 of them were translated with AI assistance and have not yet been checked by a native speaker.
- **Free software.** The program is GPL-3.0-or-later, and the sample pictures are CC BY-SA 4.0.
