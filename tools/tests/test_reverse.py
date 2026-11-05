"""Reverse camera: power-manager timing + Hudiy API framing against a mock Hudiy server."""
import importlib.util, os, socket, struct, sys, threading, time
os.environ["GPIOZERO_PIN_FACTORY"] = "mock"
from gpiozero import Device, DigitalInputDevice, OutputDevice
from gpiozero.pins.mock import MockFactory
Device.pin_factory = MockFactory()
BIN = os.path.join(os.path.dirname(__file__), "..", "..", "bin")
sys.path.insert(0, BIN)
spec = importlib.util.spec_from_file_location("hp", os.path.join(BIN, "hu-power.py")); hp = importlib.util.module_from_spec(spec); spec.loader.exec_module(hp)
import hu_hudiy_api as api

# --- 1. timing
Device.pin_factory.reset()
acc = DigitalInputDevice(3, pull_up=True); rev = DigitalInputDevice(5, pull_up=True)
calls = []
pm = hp.PowerManager({"SHUTDOWN_DELAY": "20", "CAMERA_OFF_DELAY": "2"}, acc, reverse=rev, camera=calls.append, now=0)
A, Rv = Device.pin_factory.pin(3), Device.pin_factory.pin(5)
A.drive_low(); Rv.drive_high(); pm.tick(0); assert calls == []
Rv.drive_low(); pm.tick(1); assert calls == [True], calls                       # into reverse: instant
Rv.drive_high(); pm.tick(2); pm.tick(3.5); assert calls == [True], "hold 2 s after leaving reverse"
Rv.drive_low(); pm.tick(3.8); Rv.drive_high(); pm.tick(4); pm.tick(5.9); assert calls == [True], "3-point turn: stays on"
pm.tick(6.1); assert calls == [True, False], calls
Rv.drive_low(); A.drive_high(); pm.tick(10); assert calls == [True, False], "key off: no camera"
print("timing ok")

# --- 2. protocol against a mock Hudiy
sys.path.insert(0, os.path.dirname(__file__))
from fake_hudiy import FakeHudiy
hud = FakeHudiy(ping=True, delay=0.5)
link = api.HudiyLink("test", port=hud.port)
link.set_reverse(True)                       # before Hudiy answered hello: must be held, then sent
time.sleep(1.5)
ids = hud.ids()
assert not hud.early, ("sent before Hudiy's hello response", hud.early)
assert api.PONG in ids and api.SET_REVERSE_CAMERA_STATUS in ids, ids
hello = hud.hellos[0]; assert b"test" in hello and hello.endswith(bytes([0x12, 4, 0x08, 1, 0x10, 5])), hello
assert dict(hud.got)[api.SET_REVERSE_CAMERA_STATUS] == b"\x08\x01"
print("protocol ok (waits for Hudiy's hello response):", ids)
