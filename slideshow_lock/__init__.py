# SPDX-FileCopyrightText: 2026 TrenSoft
# SPDX-License-Identifier: GPL-3.0-or-later

"""The slideshow-lock package: the application identity and the name shown to the user.

The application itself lives in the modules next to this one (state machine, D-Bus layer,
settings, GTK slideshow and settings window); this module holds what they share. Every string
shown to a user must go through ``_()`` so gettext can extract it (CI's ``tools/i18n.sh check``
compares the extracted template with the strings the source passes to ``_()``: exactly those, each
a plain literal, none called at import).

``APP_ID`` is the single-source application identity constant (no name of its own anywhere): the
`.desktop` file name, the GSettings schema id, the RPM package name, the
systemd unit name and the gettext domain are all meant to derive from this
one value. Nobody picks their own stand-in name; everything references
``APP_ID``.
"""

import gettext

_ = gettext.gettext

APP_ID = "io.github.trensoft.slideshowlock"


def app_display_name() -> str:
    """Return the translatable, user-facing application name placeholder."""
    return _("Slideshow Lock")
