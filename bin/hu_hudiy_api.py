"""
Tiny client for Hudiy's local API (TCP, Protocol Buffers in length-prefixed frames).
Only what the head unit needs: hello, reverse-camera visibility, dispatching an action, dark mode,
theme colors (SetDarkMode = 37, SetThemeColors = 54 in Hudiy's api/Api.proto), ping/pong.
Frame: <uint32 payload_len><uint32 message_id><uint32 flags=0> + protobuf payload (little-endian),
as in Hudiy's examples/api/python/common/Client.py. Messages are hand-encoded (proto2, api 1.5),
so no generated code or protobuf package is needed.
"""
import socket, struct, threading, time, logging

log = logging.getLogger("hudiy-api")
HELLO_REQUEST, HELLO_RESPONSE, SET_REVERSE_CAMERA_STATUS = 1, 2, 4
DISPATCH_ACTION, PING, PONG, BYEBYE = 43, 47, 48, 49
API_MAJOR, API_MINOR = 1, 5

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: return bytes(out)

def _field_bytes(num, data):
    return _varint((num << 3) | 2) + _varint(len(data)) + data

def _field_varint(num, v):
    return _varint(num << 3) + _varint(v)

def hello_request(name):
    version = _field_varint(1, API_MAJOR) + _field_varint(2, API_MINOR)
    return _field_bytes(1, name.encode()) + _field_bytes(2, version)

def reverse_camera_status(visible):
    return _field_varint(1, 1 if visible else 0)

def dispatch_action(action):
    return _field_bytes(1, action.encode())

def set_dark_mode(enabled):
    return _field_varint(1, 1 if enabled else 0)

def set_theme_colors(dark_color, light_color):
    return _field_bytes(1, dark_color.encode()) + _field_bytes(2, light_color.encode())

def api_port(default=44405):
    """Hudiy's API port from its config (api.tcpEndpointPort) when set there, else the default."""
    import json, os
    from pathlib import Path
    try:
        cfg = json.loads((Path.home() / ".hudiy" / "share" / "config" / "main_configuration.json").read_text())
        return int(cfg.get("api", {}).get("tcpEndpointPort") or os.environ.get("HUDIY_API_PORT") or default)
    except (OSError, ValueError, AttributeError, TypeError):
        return int(os.environ.get("HUDIY_API_PORT") or default)

def send_once(name, messages, host="127.0.0.1", port=None, timeout=3):
    """Connect, say hello, send [(message_id, payload), ...], close. True if it was delivered."""
    try:
        with socket.create_connection((host, port or api_port()), timeout=timeout) as s:
            s.sendall(frame(HELLO_REQUEST, hello_request(name)))
            for mid, payload in messages:
                s.sendall(frame(mid, payload))
            time.sleep(0.3)
            s.sendall(frame(BYEBYE))
        return True
    except OSError:
        return False

def frame(msg_id, payload=b""):
    return struct.pack("<III", len(payload), msg_id, 0) + payload

class HudiyLink:
    """Keeps one connection open (Hudiy may start after us, or restart), answers pings, and
    re-sends the last reverse-camera state after every reconnect."""
    def __init__(self, name="headunit", host="127.0.0.1", port=44405):
        self.name, self.host, self.port = name, host, port
        self.sock = None; self.lock = threading.Lock(); self.reverse = None
        threading.Thread(target=self._loop, daemon=True).start()

    def set_reverse(self, visible):
        self.reverse = bool(visible)
        self._send(SET_REVERSE_CAMERA_STATUS, reverse_camera_status(self.reverse))

    def action(self, name):
        self._send(DISPATCH_ACTION, dispatch_action(name))

    def _send(self, msg_id, payload):
        with self.lock:
            if not self.sock: return False
            try:
                self.sock.sendall(frame(msg_id, payload)); return True
            except OSError:
                self._drop(); return False

    def _drop(self):
        try: self.sock.close()
        except Exception: pass
        self.sock = None

    def _recv_exact(self, s, n):
        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk: raise ConnectionError("closed")
            data += chunk
        return data

    def _loop(self):
        while True:
            try:
                s = socket.create_connection((self.host, self.port), timeout=5)
                s.settimeout(None)
                s.sendall(frame(HELLO_REQUEST, hello_request(self.name)))
                with self.lock: self.sock = s
                log.info("connected to Hudiy API")
                if self.reverse is not None:
                    self.set_reverse(self.reverse)
                while True:
                    size, mid, _ = struct.unpack("<III", self._recv_exact(s, 12))
                    self._recv_exact(s, size) if size else b""
                    if mid == PING:
                        self._send(PONG, b"")
                    elif mid == BYEBYE:
                        raise ConnectionError("bye")
            except (OSError, ConnectionError, struct.error):
                with self.lock: self._drop()
                time.sleep(3)
