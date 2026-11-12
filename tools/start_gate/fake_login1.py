"""A minimal org.freedesktop.login1 for the start gate, on a private (test) D-Bus system bus.

The service asks logind for its session, a delay inhibitor lock and the delay time, and it ends
at once when logind is not there. A container has no logind, so this stands in for it:
``Manager.GetSessionByPID``, ``Manager.Inhibit`` (answers with a file descriptor), the
``Session.Lock`` call, and the properties ``LockedHint`` and ``InhibitDelayMaxUSec``.

usage: fake_login1.py
(the bus is DBUS_SYSTEM_BUS_ADDRESS; it prints "fake-login1 up" when it owns the name)
"""

import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

MANAGER_PATH = "/org/freedesktop/login1"
SESSION_PATH = "/org/freedesktop/login1/session/1"
INHIBIT_DELAY_USEC = 5_000_000

XML = """<node>
 <interface name="org.freedesktop.login1.Manager">
  <method name="GetSessionByPID">
   <arg type="u" direction="in"/><arg type="o" direction="out"/>
  </method>
  <method name="Inhibit">
   <arg type="s" direction="in"/><arg type="s" direction="in"/>
   <arg type="s" direction="in"/><arg type="s" direction="in"/>
   <arg type="h" direction="out"/>
  </method>
  <signal name="PrepareForSleep"><arg type="b"/></signal>
 </interface>
 <interface name="org.freedesktop.login1.Session">
  <method name="Lock"/>
 </interface>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="Get">
   <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/>
  </method>
 </interface>
</node>"""


def on_call(_conn, _sender, _path, _iface, method, params, invocation):
    if method == "Inhibit":
        fds = Gio.UnixFDList()
        fds.append(os.open("/dev/null", os.O_RDONLY))
        invocation.return_value_with_unix_fd_list(GLib.Variant("(h)", (0,)), fds)
    elif method == "GetSessionByPID":
        invocation.return_value(GLib.Variant("(o)", (SESSION_PATH,)))
    elif method == "Lock":
        invocation.return_value(None)
    elif method == "Get":
        if params.unpack()[1] == "LockedHint":
            value = GLib.Variant("b", False)
        else:
            value = GLib.Variant("t", INHIBIT_DELAY_USEC)
        invocation.return_value(GLib.Variant("(v)", (value,)))
    else:
        invocation.return_error_literal(
            Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method
        )


def main() -> int:
    address = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
    if not address:
        print("fake-login1: DBUS_SYSTEM_BUS_ADDRESS is not set", file=sys.stderr)
        return 2
    flags = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    conn = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    interfaces = {i.name: i for i in Gio.DBusNodeInfo.new_for_xml(XML).interfaces}
    manager = ("org.freedesktop.login1.Manager", "org.freedesktop.DBus.Properties")
    session = ("org.freedesktop.login1.Session", "org.freedesktop.DBus.Properties")
    for path, names in ((MANAGER_PATH, manager), (SESSION_PATH, session)):
        for name in names:
            conn.register_object(path, interfaces[name], on_call, None, None)
    reply = conn.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "RequestName",
        GLib.Variant("(su)", ("org.freedesktop.login1", 0)),
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
    )
    if reply.unpack()[0] != 1:
        print(f"fake-login1: RequestName answered {reply.unpack()}", file=sys.stderr)
        return 1
    print("fake-login1 up", flush=True)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
