import importlib.util, os, sys
os.environ["GPIOZERO_PIN_FACTORY"] = "mock"
from gpiozero import Device, DigitalInputDevice, OutputDevice
from gpiozero.pins.mock import MockFactory
Device.pin_factory = MockFactory()
spec = importlib.util.spec_from_file_location("hp", __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "..", "bin", "hu-power.py")); hp = importlib.util.module_from_spec(spec); spec.loader.exec_module(hp)

def rig(delay=60):
    Device.pin_factory.reset()
    acc = DigitalInputDevice(3, pull_up=True); amp = OutputDevice(27, initial_value=False)
    calls = []
    pm = hp.PowerManager({"SHUTDOWN_DELAY": str(delay), "AMP_ON_DELAY": "6"}, acc, None, amp, shutdown=lambda: calls.append("off"), now=0)
    return pm, Device.pin_factory.pin(3), amp, calls

# 1) Bench: key never seen on -> never shuts down
pm, pin, amp, calls = rig(); pin.drive_high()
for t in range(0, 600): pm.tick(t)
assert calls == [] and not pm.armed, "bench must not shut down"
# 2) Key on -> amp after 6 s; key off -> amp off immediately; shutdown at exactly 60 s
pm, pin, amp, calls = rig(); pin.drive_low()
for t in range(0, 6): pm.tick(t); assert not amp.value
pm.tick(6); assert amp.value, "amp should be on after 6 s"
pin.drive_high(); pm.tick(100); assert not amp.value, "amp off at key off"
for t in range(101, 160): pm.tick(t)
assert calls == [], "too early"
pm.tick(160); assert calls == ["off"], calls
pm.tick(170); assert calls == ["off"], "shutdown only once"
# 3) Diesel crank: ACC drops for 4 s during cranking, comes back -> no shutdown, amp re-arms delay
pm, pin, amp, calls = rig(); pin.drive_low()
for t in range(0, 20): pm.tick(t)
pin.drive_high()
for t in range(20, 24): pm.tick(t)
pin.drive_low()
for t in range(24, 200): pm.tick(t)
assert calls == [] and amp.value
# 4) Quick fuel stop: key off 45 s then back on -> countdown cancelled
pin.drive_high()
for t in range(200, 245): pm.tick(t)
pin.drive_low(); pm.tick(245)
pin.drive_high()
for t in range(246, 300): pm.tick(t)
assert calls == [], "countdown must restart after key cycle"
pm.tick(306); assert calls == ["off"]
print("power manager: all 4 scenarios pass")
