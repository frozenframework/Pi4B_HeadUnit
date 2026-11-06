"""A stand-in for BlueZ's obexd (org.bluez.obex) on a private session bus, for tests. Like the real one,
it ends a session as soon as the program that created it disconnects - which is why the head unit
must do the whole phonebook transfer over one connection.

    python3 fake_obexd.py BUS_ADDRESS LOG VCF STATE_JSON TMP_FILE
LOG gets one JSON line per call: ["obex", member, args...]. STATE_JSON {"refuse": true} = the phone says no.
"""
import itertools, json, os, shutil, sys
from jeepney import new_method_return, new_error, MessageType, HeaderFields, MatchRule
from jeepney.io.blocking import open_dbus_connection
from jeepney.bus_messages import message_bus

addr, LOG, VCF, STATE, TMP = sys.argv[1:6]
conn = open_dbus_connection(bus=addr)
conn.send_and_get_reply(message_bus.RequestName("org.bluez.obex"))
conn.send_and_get_reply(message_bus.AddMatch(MatchRule(type="signal", sender="org.freedesktop.DBus",
                                                      interface="org.freedesktop.DBus", member="NameOwnerChanged")))
sessions, counter, drops = {}, itertools.count(), [0, None]

def flat(x):
    if isinstance(x, (list, tuple)):
        for y in x: yield from flat(y)
    elif isinstance(x, dict):
        for k, v in x.items(): yield k; yield from flat(v)
    else:
        yield str(x)

def state():
    try: return json.load(open(STATE))
    except (OSError, ValueError): return {}

print("ready", flush=True)
while True:
    msg = conn.receive(); h = msg.header; f = h.fields
    if h.message_type == MessageType.signal:
        if f.get(HeaderFields.member) == "NameOwnerChanged":
            name, old, new = msg.body
            if not new:                                   # the creator went away: its sessions end
                for p in [p for p, o in sessions.items() if o == name]: del sessions[p]
        continue
    if h.message_type != MessageType.method_call: continue
    path, iface, member = f.get(HeaderFields.path), f.get(HeaderFields.interface), f.get(HeaderFields.member)
    sender, sig = f.get(HeaderFields.sender), f.get(HeaderFields.signature, "")
    with open(LOG, "a") as fh: fh.write(json.dumps(["obex", member, *flat(msg.body)]) + "\n")
    ok = lambda s=None, *b: conn.send(new_method_return(msg, s, b))
    err = lambda n, t: conn.send(new_error(msg, n, "s", (t,)))
    if path == "/org/bluez/obex" and member == "CreateSession":
        if state().get("refuse"): err("org.bluez.obex.Error.Failed", "Not Allowed"); continue
        p = f"/org/bluez/obex/client/session{next(counter)}"; sessions[p] = sender; ok("o", p)
    elif path == "/org/bluez/obex" and member == "RemoveSession":
        sessions.pop(msg.body[0], None); ok()
    elif path in sessions and iface == "org.bluez.obex.PhonebookAccess1" and member == "Select" and sig == "ss":
        ok()
        st = state()
        if st.get("drop_id") != drops[1]:                 # a new scenario: count again
            drops[:] = [0, st.get("drop_id")]
        if drops[0] < st.get("drop_after_select", 0):      # the phone hangs up right after (as when it asks to allow)
            drops[0] += 1; sessions.pop(path, None)
    elif path in sessions and iface == "org.bluez.obex.PhonebookAccess1" and member == "GetSize":
        ok("q", 3); sessions.pop(path, None)               # what an iPhone seemed to do: closes the connection after it
    elif path in sessions and iface == "org.bluez.obex.PhonebookAccess1" and member == "PullAll":
        shutil.copy(VCF, TMP); ok("oa{sv}", path + "/transfer0", {"Filename": ("s", TMP), "Size": ("t", 0)})
    elif member == "Get" and "/transfer" in (path or ""):
        err("org.freedesktop.DBus.Error.UnknownObject", "finished")      # obexd drops a finished transfer
    elif path and path.startswith("/org/bluez/obex/client/session") and path not in sessions:
        err("org.freedesktop.DBus.Error.UnknownObject",      # libdbus >= 1.13: the object is gone
            f'Method "{member}" with signature "{sig}" on interface "{iface}" doesn\'t exist')
    else:                                                 # what obexd (libdbus) says for a missing interface
        err("org.freedesktop.DBus.Error.UnknownMethod",
            f'Method "{member}" with signature "{sig}" on interface "{iface}" doesn\'t exist')
