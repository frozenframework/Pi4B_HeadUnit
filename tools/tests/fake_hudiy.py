"""A strict stand-in for Hudiy's API (TCP, length-prefixed protobuf frames) for the tests.

Like the real one, it only listens once it has answered the client's hello: anything a client sends
before the HelloResponse lands in `early` (and would be ignored by Hudiy). Everything after is in `got`
as (message_id, payload). `after_hello` = frames to send right after the hello response; ping=True
also sends a PING then (the client must answer PONG). `delay` = seconds before Hudiy answers hello."""
import socket, struct, threading, time

HELLO_REQUEST, HELLO_RESPONSE, PING = 1, 2, 47
OK_RESPONSE = bytes([0x0A, 4, 0x08, 9, 0x10, 0, 0x12, 4, 0x08, 1, 0x10, 5, 0x18, 1])   # app 9.0, api 1.5, result OK

def frame(mid, payload=b""):
    return struct.pack("<III", len(payload), mid, 0) + payload

class FakeHudiy:
    def __init__(self, port=0, after_hello=(), ping=False, delay=0.2, listen_for=6.0):
        self.got, self.early, self.hellos = [], [], []
        self.after_hello, self.ping, self.delay, self.listen_for = list(after_hello), ping, delay, listen_for
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port)); self.srv.listen(5); self.port = self.srv.getsockname()[1]
        self.srv.settimeout(listen_for + 5)
        threading.Thread(target=self._serve, daemon=True).start()

    def ids(self): return [m for m, _ in self.got]

    def _serve(self):
        end = time.time() + self.listen_for + 5
        while time.time() < end:
            try: c, _ = self.srv.accept()
            except OSError: return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        c.settimeout(self.listen_for); buf = b""; answered = False; end = time.time() + self.listen_for
        try:
            while time.time() < end:
                d = c.recv(4096)
                if not d: break
                buf += d
                while len(buf) >= 12:
                    n, mid, _ = struct.unpack("<III", buf[:12])
                    if len(buf) < 12 + n: break
                    body = buf[12:12 + n]; buf = buf[12 + n:]
                    if mid == HELLO_REQUEST and not answered:
                        self.hellos.append(body); time.sleep(self.delay)
                        # what arrived during the delay was sent before the response: Hudiy ignores it
                        c.setblocking(False)
                        try:
                            while True:
                                more = c.recv(65536)
                                if not more: break
                                buf += more
                        except (BlockingIOError, OSError): pass
                        c.setblocking(True); c.settimeout(self.listen_for)
                        while len(buf) >= 12:
                            n2, m2, _ = struct.unpack("<III", buf[:12])
                            if len(buf) < 12 + n2: break
                            self.early.append((m2, buf[12:12 + n2])); buf = buf[12 + n2:]
                        c.sendall(frame(HELLO_RESPONSE, OK_RESPONSE)); answered = True
                        for m, p in self.after_hello: c.sendall(frame(m, p))
                        if self.ping: c.sendall(frame(PING))
                    elif answered:
                        self.got.append((mid, body))
                    else:
                        self.early.append((mid, body))
        except OSError:
            pass
        finally:
            c.close()
