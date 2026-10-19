# Fedora and EL spec for slideshow-lock.

%global app_id io.github.trensoft.slideshowlock
# app_id is APP_ID of slideshow_lock/__init__.py: the GSettings schema id, the gettext domain and the
# name of the .desktop, metainfo and icon files

%{!?_metainfodir:%global _metainfodir %{_datadir}/metainfo}

%bcond tests 0
# The test suite needs a private dbus-daemon and the GTK 4 typelibs, so it is off by default
# (rpmbuild --with tests turns it on). %%check still imports every module and validates the installed files.

Name:           slideshow-lock
Version:        1.0.10
Release:        2%{?dist}
Summary:        Idle slideshow screensaver for GNOME on Wayland that locks on input
License:        GPL-3.0-or-later AND CC-BY-SA-4.0
# CC-BY-SA-4.0 is the license of the seven sample pictures (data/pictures/CREDITS.txt)
URL:            https://github.com/trensoft/slideshow-lock
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  gettext
BuildRequires:  glib2-devel
BuildRequires:  systemd-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
BuildRequires:  libappstream-glib
# The next lines are needed by the import test in %%check
BuildRequires:  python3-gobject
BuildRequires:  gtk4
BuildRequires:  graphene
BuildRequires:  gdk-pixbuf2
BuildRequires:  glib2
BuildRequires:  libadwaita
BuildRequires:  gobject-introspection
# gobject-introspection has the cairo typelib, which the Gtk and Gdk typelibs load; on Fedora
# neither gtk4 nor python3-gobject-base pulls it in
%if %{with tests}
BuildRequires:  python3-pytest
BuildRequires:  gstreamer1-plugins-base
BuildRequires:  dbus-daemon
%endif

# The program calls GNOME Shell and mutter over D-Bus (IdleMonitor, SessionManager, ScreenSaver)
Requires:       gnome-shell
Requires:       gtk4
Requires:       libadwaita >= 1.2
# 1.2 is the oldest libadwaita with every class the settings window uses (Adw.EntryRow); a bare
# Requires on a library name would make rpmlint print explicit-lib-dependency
Requires:       graphene
Requires:       gdk-pixbuf2
Requires:       gobject-introspection
# the cairo typelib, see the BuildRequires above
Requires:       hicolor-icon-theme
Recommends:     gstreamer1-plugins-base
# optional: scaling falls back to GdkPixbuf bilinear without the GStreamer videoscale element

%if 0%{?rhel}
# On EL10 the TIFF and GIF loaders are in gdk-pixbuf2-modules; the BMP and WebP ones are only in
# EPEL 10, so they are weak dependencies and the package does not need EPEL
Requires:       gdk-pixbuf2-modules
Recommends:     webp-pixbuf-loader
Recommends:     gdk-pixbuf2-modules-extra
%endif

%description
slideshow-lock starts a full-screen picture slideshow on the screens of a
GNOME session on Wayland when the session has been idle, and locks the
session when the user touches the keyboard or the mouse. The pictures come
from a folder, the settings have a GTK 4 window of their own, and the
interface follows the language of the session.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l slideshow_lock
# -l fails the build when the wheel carries no license file

install -Dpm 0755 packaging/%{name} %{buildroot}%{_bindir}/%{name}
install -Dpm 0755 packaging/slideshowlock %{buildroot}%{_bindir}/slideshowlock

# The folder is made here so that it is root:root 0755 whatever the umask of the build is
install -d -m 0755 %{buildroot}%{_datadir}/%{name}/pictures
install -pm 0644 data/pictures/* %{buildroot}%{_datadir}/%{name}/pictures/

install -Dpm 0644 data/%{app_id}.gschema.xml \
    %{buildroot}%{_datadir}/glib-2.0/schemas/%{app_id}.gschema.xml

install -Dpm 0644 data/%{name}.service %{buildroot}%{_userunitdir}/%{name}.service

# The XDG autostart entry starts the user unit at login (GNOME only); the package does not enable the unit
install -Dpm 0644 data/%{app_id}.autostart.desktop \
    %{buildroot}%{_sysconfdir}/xdg/autostart/%{app_id}.desktop

install -Dpm 0644 packaging/man/slideshow-lock.1 %{buildroot}%{_mandir}/man1/slideshow-lock.1
install -Dpm 0644 packaging/man/slideshowlock.1 %{buildroot}%{_mandir}/man1/slideshowlock.1

# The .desktop file and the metainfo are generated from the .in files with the translations
bash tools/i18n.sh data generated-data
install -Dpm 0644 generated-data/%{app_id}.desktop \
    %{buildroot}%{_datadir}/applications/%{app_id}.desktop
install -Dpm 0644 generated-data/%{app_id}.metainfo.xml \
    %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
install -Dpm 0644 data/icons/hicolor/scalable/apps/%{app_id}.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
install -Dpm 0644 data/icons/hicolor/symbolic/apps/%{app_id}-symbolic.svg \
    %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/%{app_id}-symbolic.svg

bash tools/i18n.sh build %{buildroot}%{_datadir}/locale
%find_lang %{app_id}
# find_lang fails without a catalog, so a build that lost its translations does not ship English only

%check
%pyproject_check_import
glib-compile-schemas --strict --dry-run %{buildroot}%{_datadir}/glib-2.0/schemas
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
desktop-file-validate %{buildroot}%{_sysconfdir}/xdg/autostart/%{app_id}.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
%if %{with tests}
%pytest
%endif

# The running service is restarted after an upgrade in %%posttrans and not in %%postun: on an upgrade
# the %%postun that runs is the one of the OLD package, so it would act only from the second upgrade on
%post
%systemd_user_post %{name}.service
# fedora-review wants a line after the macro

%preun
%systemd_user_preun %{name}.service
# fedora-review wants a line after the macro

%posttrans
%systemd_user_posttrans_with_restart %{name}.service

%files -f %{pyproject_files} -f %{app_id}.lang
%doc README.md
%license packaging/licenses/CC-BY-SA-4.0.txt
# the license of the pictures; the wheel carries LICENSE only, so nothing is listed twice
%{_bindir}/%{name}
%{_bindir}/slideshowlock
%{_mandir}/man1/slideshow-lock.1*
%{_mandir}/man1/slideshowlock.1*
%{_datadir}/glib-2.0/schemas/%{app_id}.gschema.xml
%{_userunitdir}/%{name}.service
%{_datadir}/applications/%{app_id}.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/%{app_id}.desktop
%{_metainfodir}/%{app_id}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_datadir}/icons/hicolor/symbolic/apps/%{app_id}-symbolic.svg
%{_datadir}/%{name}

%changelog
* Mon Oct 19 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.10-2
- Simplify %%files for /usr/share/slideshow-lock; trim spec comments (review feedback)

* Fri Oct 09 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.10-1
- Add a Donate button next to Preview in the settings window; it opens the donation page in the browser
- Remove the main menu with the About entry from the header bar of the settings window
- Show the version and the maker ("1.0.10 by TrenSoft") at the bottom right of the settings window

* Fri Oct 09 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.9-1
- Add an About window to the settings window, with a link to support the project
- List exit status 1 in the manual page of slideshow-lock
- Documentation follow-ups: the settings window is a libadwaita window

* Fri Oct 09 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.8-1
- Add manual pages for slideshow-lock and slideshowlock
- A slideshow window whose size never settles no longer makes the picture over and over; it is shown after
  at most three redos
- Require libadwaita 1.2 or newer, the version the settings window needs
- Add SPDX licence headers to the source files of the program

* Fri Oct 09 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.7-1
- The first picture of a window (and the first one after the empty-state message) gets the slow Ken
  Burns move by the same rule as every later picture; before, it stood still for its whole interval.
  The later pictures and the order of the transitions are unchanged

* Fri Oct 09 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.6-1
- The seven sample pictures no longer carry the colour profiles (ICC) that someone else wrote; the
  author and the licence stay in each picture, and the author is named "TrenSoft"
- The address of the project is github.com/trensoft/slideshow-lock (the package URL, the AppStream
  data and the service file)
- The application has a new identifier, io.github.trensoft.slideshowlock. The settings start from their
  defaults once; whoever has set a picture folder of their own has to set it again. At the first start
  the seven sample pictures are copied once into the folder Pictures/trensoft, on a new installation and
  on an update alike
- The program does not delete the files of the earlier identifier: the earlier picture folder, its state
  file and an autostart entry that was made by hand under the earlier name stay where they are, and the
  user deletes them. An autostart that was switched off by hand under the earlier name is active again
  under the new identifier
- The README says that the code was written with the help of AI agents

* Thu Oct 08 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.5-2
- Remove the Vendor tag: Fedora packages do not use it (fedora-review: "Packager, Vendor, PreReq,
  Copyright tags should not be in spec"). The upstream sources are the same as in 1.0.5-1

* Thu Oct 08 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.5-1
- Random order: a picture shown just before the order was changed to random no longer comes back after
  fewer than 3 other pictures
- Random order: when the picture that is next in line is deleted, the picture that takes its place also
  keeps the distance of 3 pictures
- When the picture on screen is deleted or can no longer be read and the program redraws it (after a
  change of scaling, panning or window size), the slideshow no longer skips a picture or shows one twice

* Thu Oct 08 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.4-1
- Remove the hardware acceleration of 1.0.3: the "Hardware acceleration" switch, the frame-time guard and
  the graphics-card probe are gone. Nine of the ten transitions are drawn as in 1.0.1; the soft edges
  of 1.0.2 are gone
- Only a picture that comes in with the Ken Burns transition moves; its move lasts until the next
  picture comes, instead of stopping after 90 % of the display time
- Settings are checked and saved the moment they are changed; the Save button is gone
- The settings window is one window with two columns of groups and no scrolled page
- In random order a picture is not shown again before at least 3 other pictures have been shown (a folder
  of fewer than 4 pictures keeps as many apart as it has)

* Thu Oct 08 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.3-1
- Settings window: a "Hardware acceleration" switch in the Transitions group. The soft edges and the slow
  move of the pictures are drawn only where the machine is known to draw with a GPU and the switch is on;
  otherwise the animation is drawn as in 1.0.1. Without a GPU, or where that cannot be found out, the
  switch is off and greyed out
- A picture that moves is redrawn at every tick of the frame clock; the limit of 30 frames a second that
  1.0.2 put on the move is removed
- A frame-time guard: if the frames of a screen come slower than 25 ms, the program draws the plain
  animation for the rest of the run
- Settings window: the version of the program is shown at the bottom right
- A yes/no setting that the installed settings schema lacks (a program newer than its schema, while an
  update is not finished) gives its default value, instead of ending the program

* Wed Oct 07 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.2-1
- Restart the running user service after the package is updated, so that the new version runs without
  a new login
- Soft edges on the circle, wipe, slide-in, push, zoom and rotate transitions, instead of a hard line
- Ken Burns: a stronger zoom
- A picture that does not scroll moves slowly during its whole interval and under the transitions, so
  there is no still moment before or after a transition
- Settings window: the Save button sits next to the Preview button
- Name TrenSoft as the developer: the Vendor tag of the package, the metainfo and the README

* Wed Oct 07 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.1-1
- Add the slideshowlock command, which starts the service and opens the settings window
- Start the service at login through an XDG autostart entry, and open the settings window once on the
  first start
- Close the shell's overview before the idle slideshow opens and when the Preview button is pressed
- Refuse an oversized picture also when the picture loader announces its size only when it is closed
- On EL10, require gdk-pixbuf2-modules (TIFF and GIF) and recommend webp-pixbuf-loader and
  gdk-pixbuf2-modules-extra (BMP and WebP, from EPEL 10)
- Add 35 translations of the interface, 40 languages in all; the 35 are AI-assisted and not reviewed by
  a native speaker
- Add a usage section to the README
- Ship seven sample pictures (CC BY-SA 4.0) that are copied into Pictures/trensoft at the first
  login, and add their licence to the License tag
- Rebuild the settings window on libadwaita, with a Save button: a change is kept until Save, and is
  also saved when the window is closed; add the dependency on libadwaita
- Add ten transitions between pictures, a "Random mix" of eight of them and a slider for the length of a
  transition
- Make Ken Burns the default transition; a transition the user has chosen is kept
- Show a picture for 10 seconds by default (it was 5)
- Leave screenshots out of the slideshow by default, judged by the name of the folder or the file; a
  switch in the settings window shows them again

* Tue Oct 06 2026 TrenSoft <trensoft@fedoraproject.org> - 1.0.0-1
- Initial package
