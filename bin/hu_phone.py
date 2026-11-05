"""
What the phone (CarPlay through the Carlinkit adapter, or Android Auto) tells Hudiy, for the offline map:
  - next turn while Apple Maps / Google Maps / Waze guides on the phone: text, arrow picture, distance
  - now playing: title, artist, album, cover art, playing or paused
  - call in progress
and "stay on the map": when a phone connects, Hudiy brings CarPlay / Android Auto to the front; with the
setting phoneScreen = "map" we put the offline map back in front. CarPlay keeps running behind it
(music, calls, Siri, its spoken directions) and the map's phone button brings it up any time.

How a new connection is told apart from you opening CarPlay yourself:
  - a phone "session" starts with the first message from the phone (projection, phone music or phone
    navigation) after the head unit starts or after SESSION_GAP of silence (it was disconnected);
  - the map is put back in front only for the first time CarPlay / Android Auto comes up in a session,
    and only within CONNECT_WINDOW seconds of the session starting;
  - never when you just asked for it (the map's phone button, or Hudiy's own CarPlay / Android Auto
    buttons reported through CURRENT_MENU_ACTION, within USER_WINDOW seconds), or during a call.
So opening CarPlay later, or a call bringing it up, is always left alone.
"""
import base64, threading, time
from hu_hudiy_api import (decode, PROJECTION_STATUS, MEDIA_STATUS, MEDIA_METADATA, NAVIGATION_STATUS,
                          NAVIGATION_MANEUVER_DETAILS, NAVIGATION_MANEUVER_DISTANCE, PHONE_CONNECTION_STATUS,
                          PHONE_VOICE_CALL_STATUS, CURRENT_MENU_ACTION)

MAP_ACTION = "hu_navigation"
SESSION_GAP = 1800        # seconds of silence from the phone that count as "it was disconnected"
CONNECT_WINDOW = 60       # CarPlay coming up this soon after the phone shows up = the automatic switch on connect
USER_WINDOW = 8           # seconds after you ask for CarPlay during which it's left in front
PROJECTION_ACTIONS = {"resume_autobox_projection", "resume_android_auto_projection", "autobox_player",
                      "android_auto_player", "connect_autobox_wifi", "connect_android_auto_wifi",
                      "connect_android_auto_usb", "now_playing_go_to_player"}
NAV_SOURCES = {1: "android_auto", 2: "carplay"}
MEDIA_SOURCES = {1: "android_auto", 2: "carplay", 3: "bluetooth", 4: "usb", 5: "fm", 6: "web", 7: "aux"}
CALL_STATES = {0: None, 1: "incoming", 2: "ringing", 3: "active"}
MANEUVER_TYPES = {0: "unknown", 1: "depart", 2: "name_change", 3: "slight_turn", 4: "turn", 5: "sharp_turn", 6: "u_turn",
                  7: "on_ramp", 8: "off_ramp", 9: "fork", 10: "merge", 11: "roundabout_enter", 12: "roundabout_exit",
                  13: "roundabout", 14: "straight", 16: "ferry", 17: "ferry_train", 19: "destination"}
SIDES = {1: "left", 2: "right", 3: None}

def _s(f, n, default=""):
    v = f.get(n)
    return v[0].decode("utf-8", "replace") if v else default

def _i(f, n, default=None):
    v = f.get(n)
    return v[0] if v else default

def image_type(b):
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if b[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "image/webp"
    if b[:6] in (b"GIF87a", b"GIF89a"): return "image/gif"
    if b.lstrip()[:5] in (b"<?xml", b"<svg "): return "image/svg+xml"
    return None

class PhoneState:
    def __init__(self, dispatch, phone_screen, clock=time.monotonic, log=None):
        """dispatch(action) sends a Hudiy action; phone_screen() returns "map" or "carplay"."""
        self.dispatch, self.phone_screen, self.clock, self.log = dispatch, phone_screen, clock, log
        self.lock = threading.Lock()
        self.projection = False; self.last_phone = None; self.user_asked = -1e9; self.source = None
        self.session_start = None; self.session_handled = False
        self.nav = {"active": False, "source": None, "text": "", "distance": "", "type": None, "side": None,
                    "angle": None, "icon": None, "icon_type": None, "v": 0}
        self.media = {"title": "", "artist": "", "album": "", "duration": "", "position": "", "playing": False,
                      "source": None, "cover": None, "cover_type": None, "v": 0}
        self.call = {"state": None, "name": "", "number": ""}
        self.phone_name = ""; self.returned_to_map = 0; self._retry = None

    # -------------------------------------------------------------- incoming messages from Hudiy
    def handle(self, mid, payload):
        f = decode(payload)
        now = self.clock()
        with self.lock:
            if mid == PROJECTION_STATUS:
                active = bool(_i(f, 1, 0))
                came_up = active and not self.projection
                self.projection = active
                if active: self._phone_seen(now)
                if (came_up and not self.session_handled and now - self.session_start <= CONNECT_WINDOW
                        and now - self.user_asked > USER_WINDOW and self.call["state"] is None):
                    self.session_handled = True             # only the switch that comes with connecting
                    if self.phone_screen() == "map": self._back_to_map(now)
            elif mid == MEDIA_STATUS:
                src = MEDIA_SOURCES.get(_i(f, 3, 0))
                self.media.update(position=_s(f, 1), playing=bool(_i(f, 2, 0)), source=src)
                if src in ("carplay", "android_auto"): self._phone_seen(now); self.source = src
            elif mid == MEDIA_METADATA:
                cover = f.get(5, [b""])[0]
                self.media.update(album=_s(f, 1), artist=_s(f, 2), title=_s(f, 3), duration=_s(f, 4),
                                  cover=cover or None, cover_type=image_type(cover) if cover else None, v=self.media["v"] + 1)
            elif mid == NAVIGATION_STATUS:
                src = NAV_SOURCES.get(_i(f, 1, 0)); active = _i(f, 2, 2) == 1
                self.nav.update(active=active and src is not None, source=src)
                if src: self._phone_seen(now); self.source = src
                if not active:
                    self.nav.update(text="", distance="", type=None, side=None, angle=None, icon=None, icon_type=None)
            elif mid == NAVIGATION_MANEUVER_DETAILS:
                icon = f.get(2, [b""])[0]
                self.nav.update(text=_s(f, 1), type=MANEUVER_TYPES.get(_i(f, 4)), side=SIDES.get(_i(f, 3)),
                                angle=_i(f, 5), icon=icon or None, icon_type=image_type(icon) if icon else None,
                                v=self.nav["v"] + 1)
                self._phone_seen(now)
            elif mid == NAVIGATION_MANEUVER_DISTANCE:
                self.nav["distance"] = _s(f, 1); self._phone_seen(now)
            elif mid == PHONE_CONNECTION_STATUS:
                self.phone_name = _s(f, 2) if _i(f, 1) == 1 else ""
            elif mid == PHONE_VOICE_CALL_STATUS:
                self.call = {"state": CALL_STATES.get(_i(f, 1, 0)), "number": _s(f, 2), "name": _s(f, 3)}
            elif mid == CURRENT_MENU_ACTION:
                if _s(f, 1) in PROJECTION_ACTIONS: self.user_asked = now

    def _phone_seen(self, now):
        if self.last_phone is None or now - self.last_phone > SESSION_GAP:
            self.session_start, self.session_handled = now, False          # a (re)connected phone
        self.last_phone = now

    def _back_to_map(self, now):
        self.returned_to_map += 1
        if self.log: self.log.info("phone connected: keeping the offline map in front (CarPlay runs behind it)")
        self.dispatch(MAP_ACTION)
        # Hudiy may still be switching over; check once more a moment later
        def again():
            with self.lock:
                if self.projection and self.clock() - self.user_asked > USER_WINDOW and self.call["state"] is None:
                    self.dispatch(MAP_ACTION)
        if self._retry: self._retry.cancel()
        self._retry = threading.Timer(1.5, again); self._retry.daemon = True; self._retry.start()

    # -------------------------------------------------------------- requests from the map
    def show_phone(self):
        """The map's phone button: bring CarPlay / Android Auto to the front."""
        with self.lock:
            self.user_asked = self.clock()
            src = self.source or "carplay"
        self.dispatch("resume_android_auto_projection" if src == "android_auto" else "resume_autobox_projection")
        return src

    def media_key(self, what):
        action = {"toggle": "now_playing_toggle_play", "next": "now_playing_next_track",
                  "previous": "now_playing_previous_track"}.get(what)
        if action: self.dispatch(action)
        return bool(action)

    def status(self):
        with self.lock:
            connected = self.last_phone is not None and self.clock() - self.last_phone <= SESSION_GAP or self.projection
            nav = {k: v for k, v in self.nav.items() if k not in ("icon",)}
            nav["has_icon"] = self.nav["icon"] is not None and self.nav["icon_type"] is not None
            media = {k: v for k, v in self.media.items() if k != "cover"}
            media["has_cover"] = self.media["cover"] is not None and self.media["cover_type"] is not None
            return {"connected": bool(connected), "source": self.source, "projection": self.projection,
                    "phone": self.phone_name, "nav": nav, "media": media, "call": dict(self.call),
                    "screen": self.phone_screen()}

    def image(self, which):
        with self.lock:
            d = self.nav if which == "icon" else self.media
            data, typ = (d["icon"], d["icon_type"]) if which == "icon" else (d["cover"], d["cover_type"])
        return (data, typ) if data and typ else (None, None)
