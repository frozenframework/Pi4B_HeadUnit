"""Phone info on the offline map: Hudiy's status messages (encoded as in Hudiy's api/Api.proto) become the
phone's next turn, now playing and call; "stay on the map" sends the map back to front only for the switch
that comes with a phone connecting, never when you open CarPlay yourself or a call brings it up.
Part 2 runs the real HudiyLink against a fake Hudiy API server (subscriptions, framing)."""
import socket, struct, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
import hu_phone
from hu_phone import PhoneState
import hu_hudiy_api as H

def v(n):
    o = bytearray()
    while True:
        b = n & 0x7F; n >>= 7; o.append(b | (0x80 if n else 0))
        if not n: return bytes(o)
def fi(num, x): return v(num << 3) + v(x)
def fb(num, b): b = b.encode() if isinstance(b, str) else b; return v((num << 3) | 2) + v(len(b)) + b

class Clock:
    t = 1000.0
    def __call__(self): return self.t
clock = Clock(); sent = []; screen = {"v": "map"}
hu_phone.threading.Timer = lambda d, f: type("T", (), {"start": lambda s: None, "cancel": lambda s: None, "daemon": True})()
p = PhoneState(sent.append, lambda: screen["v"], clock=clock)
proj = lambda on: p.handle(H.PROJECTION_STATUS, fi(1, 1 if on else 0))

# 1) phone connects: Hudiy shows CarPlay (after some CarPlay music messages) -> map goes back in front
p.handle(H.MEDIA_STATUS, fb(1, "0:01") + fi(2, 1) + fi(3, 2)); clock.t += 2
proj(True)
assert sent == ["hu_navigation"], sent
st = p.status(); assert st["connected"] and st["source"] == "carplay" and st["media"]["playing"]
print("1) phone connects -> offline map back in front; CarPlay keeps running behind it")

# 2) opening CarPlay yourself later (map's phone button, or Hudiy's own button) is left alone
proj(False); clock.t += 120; sent.clear()
assert p.show_phone() == "carplay" and sent == ["resume_autobox_projection"]; sent.clear()
proj(True); assert sent == [], sent
proj(False); clock.t += 300
p.handle(H.CURRENT_MENU_ACTION, fb(1, "resume_autobox_projection")); clock.t += 1
proj(True); assert sent == [], sent
proj(False); clock.t += 30
proj(True); assert sent == [], "CarPlay coming up later in the drive (e.g. a call) must be left alone"
print("2) your own CarPlay taps and later switches are left alone")

# 3) next turn from the phone, distance, end of guidance
png = b"\x89PNG\r\n\x1a\n" + b"x" * 40
p.handle(H.NAVIGATION_STATUS, fi(1, 2) + fi(2, 1))
p.handle(H.NAVIGATION_MANEUVER_DETAILS, fb(1, "Turn left onto Main St") + fb(2, png) + fi(3, 1) + fi(4, 4) + fi(5, 90))
p.handle(H.NAVIGATION_MANEUVER_DISTANCE, fb(1, "0.3 mi"))
n = p.status()["nav"]
assert n["active"] and n["source"] == "carplay" and n["text"] == "Turn left onto Main St" and n["distance"] == "0.3 mi"
assert n["type"] == "turn" and n["side"] == "left" and n["angle"] == 90 and n["has_icon"]
assert p.image("icon") == (png, "image/png")
p.handle(H.NAVIGATION_STATUS, fi(1, 2) + fi(2, 2))
assert not p.status()["nav"]["active"] and p.image("icon") == (None, None)
print("3) phone's next turn: text, arrow picture, distance; cleared when guidance ends")

# 4) now playing + cover, call, media keys
jpg = b"\xff\xd8\xff\xe0" + b"y" * 50
p.handle(H.MEDIA_METADATA, fb(1, "Album") + fb(2, "Artist") + fb(3, "Song") + fb(4, "3:45") + fb(5, jpg))
m = p.status()["media"]; assert (m["title"], m["artist"], m["has_cover"]) == ("Song", "Artist", True)
assert p.image("cover") == (jpg, "image/jpeg")
p.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 3) + fb(2, "5551234") + fb(3, "Mom"))
assert p.status()["call"] == {"state": "active", "number": "5551234", "name": "Mom"}
sent.clear(); assert p.media_key("toggle") and sent == ["now_playing_toggle_play"]
print("4) now playing with cover art, call status, play/pause through Hudiy")

# 5) setting "Show CarPlay": a new connection is left on CarPlay; a new session after a long gap counts again
p2 = PhoneState(sent.append, lambda: "carplay", clock=clock); sent.clear()
p2.handle(H.PROJECTION_STATUS, fi(1, 1)); assert sent == []
p.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 0)); proj(False); clock.t += hu_phone.SESSION_GAP + 10; sent.clear()
proj(True); assert sent == ["hu_navigation"], "reconnect after being away = new connection"
print("5) 'Show CarPlay' setting respected; reconnecting after being away is a new connection")

# Part 2: the real link: subscribes, receives framed messages, dispatches actions back to Hudiy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_hudiy import FakeHudiy
def fr(mid, pl=b""): return mid, pl
hud = FakeHudiy(after_hello=[fr(H.PROJECTION_STATUS, fi(1, 1)), fr(H.NAVIGATION_MANEUVER_DISTANCE, fb(1, "500 ft"))], ping=True)
port = hud.port; seen = []
p3 = PhoneState(lambda a: link.action(a), lambda: "map")
def on(mid, pl): seen.append(mid); p3.handle(mid, pl)
link = H.HudiyLink("headunit-phone-test", port=port, on_message=on,
                   subscriptions=[H.SUB_PROJECTION, H.SUB_MEDIA, H.SUB_NAVIGATION, H.SUB_PHONE, H.SUB_CURRENT_MENU_ACTION])
time.sleep(2.5)
got = hud.got; ids = hud.ids()
assert not hud.early, ("sent before Hudiy's hello response", hud.early)
assert ids[0] == H.SET_STATUS_SUBSCRIPTIONS, ids
assert H.decode(dict(got)[H.SET_STATUS_SUBSCRIPTIONS]) == {1: [1, 2, 3, 5, 7]}
assert H.PONG in ids and H.DISPATCH_ACTION in ids, ids
assert b"hu_navigation" in dict(got)[H.DISPATCH_ACTION]
assert p3.status()["nav"]["distance"] == "500 ft"
print("6) real link: hello, subscriptions [projection, media, navigation, phone, menu action], ping/pong, map action sent")
print("phone: all tests passed")
