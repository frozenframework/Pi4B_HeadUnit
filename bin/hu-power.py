#!/usr/bin/env python3
"""
Ignition-aware power manager (runs as root).

Key ON  -> ACC_GPIO goes active -> amp turns on after AMP_ON_DELAY (no turn-on pop).
          DIY board: the optocoupler pulls ACC_GPIO (GPIO3) low. CarPiHAT (tier 3): its 12V-sense
          input drives GPIO12 high (ACC_ACTIVE=high, ILLUM_ACTIVE=high, REVERSE_ACTIVE=high).
Key OFF -> amp off at once, then a SHUTDOWN_DELAY countdown. Key back on cancels it.
          When the countdown ends: clean shutdown. The gpio-poweroff overlay drops the hold
          relay only after the Pi has fully halted, which cuts power to everything.
Safety:  it only "arms" after it has seen the key ON once since boot, so a Pi on the bench
          (no ignition wire) never shuts itself down.
"""
import json, os, subprocess, sys, time
from pathlib import Path

def load_conf(path="/etc/headunit/headunit.conf"):
    c = {}
    for line in Path(path).read_text().splitlines() if Path(path).exists() else []:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); c[k.strip()] = v.strip().strip('"')
    return c

class PowerManager:
    def __init__(self, conf, acc, illum=None, amp=None, shutdown=None, state_file=None, now=None, reverse=None, camera=None):
        self.delay = float(conf.get("SHUTDOWN_DELAY", 30)); self.amp_delay = float(conf.get("AMP_ON_DELAY", 6))
        self.mode = conf.get("POWER_MODE", "diy")
        self.acc, self.illum, self.amp = acc, illum, amp
        self.shutdown = shutdown or (lambda: (subprocess.run(["sync"]), subprocess.run(["systemctl", "poweroff"])))
        self.state_file = state_file
        self.armed = False; self.off_since = None; self.on_since = None; self.done = False
        # backup camera: shown at once when reverse is selected, hidden CAMERA_OFF_DELAY s after leaving it
        self.reverse, self.camera = reverse, camera
        self.cam_off_delay = float(conf.get("CAMERA_OFF_DELAY", 2)); self.cam_on = False; self.rev_off_since = None
        self.boot = now if now is not None else time.monotonic()

    def tick(self, now):
        key = self.acc.is_active
        if self.reverse is not None and self.camera:
            rev = self.reverse.is_active and key
            if rev:
                self.rev_off_since = None
                if not self.cam_on:
                    self.cam_on = True; self.camera(True); print("reverse: camera on", flush=True)
            elif self.cam_on:
                self.rev_off_since = self.rev_off_since if self.rev_off_since is not None else now
                if now - self.rev_off_since >= self.cam_off_delay:
                    self.cam_on = False; self.camera(False); print("reverse: camera off", flush=True)
        if key:
            self.armed = True; self.off_since = None
            self.on_since = self.on_since if self.on_since is not None else now
            if self.amp and not self.amp.value and now - self.on_since >= self.amp_delay and now - self.boot >= self.amp_delay:
                self.amp.on(); print("amp on", flush=True)
        else:
            self.on_since = None
            if self.amp and self.amp.value:
                self.amp.off(); print("key off: amp off", flush=True)
            if self.armed and not self.done:
                self.off_since = self.off_since if self.off_since is not None else now
                if now - self.off_since >= self.delay:
                    self.done = True; print("key off long enough: shutting down", flush=True)
                    self.write(key); self.shutdown()
        self.write(key, now)

    def write(self, key, now=None):
        if not self.state_file: return
        left = None if self.off_since is None or now is None else max(0, round(self.delay - (now - self.off_since)))
        data = {"mode": self.mode, "acc": key, "reverse": self.cam_on, "illum": bool(self.illum and self.illum.is_active), "armed": self.armed, "shutdown_in": 0 if self.done else left}
        tmp = self.state_file.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); tmp.replace(self.state_file)
        os.chmod(self.state_file, 0o644)

def main():
    C = load_conf()
    state = Path("/run/headunit/state.json"); state.parent.mkdir(parents=True, exist_ok=True)
    if C.get("POWER_MODE", "diy") == "none":
        state.write_text(json.dumps({"mode": "none", "acc": True, "illum": False, "armed": False})); os.chmod(state, 0o644)
        print("power management disabled (bench mode)"); return
    from gpiozero import DigitalInputDevice, OutputDevice
    low = lambda key: C.get(key, "low") != "high"     # DIY optocouplers pull low; CarPiHAT inputs drive high
    acc = DigitalInputDevice(int(C.get("ACC_GPIO", 3)), pull_up=low("ACC_ACTIVE"), bounce_time=0.05)
    illum = DigitalInputDevice(int(C["ILLUM_GPIO"]), pull_up=low("ILLUM_ACTIVE"), bounce_time=0.2) if C.get("ILLUM_GPIO") else None
    amp = OutputDevice(int(C["AMP_GPIO"]), active_high=True, initial_value=False) if C.get("AMP_GPIO") else None
    reverse = camera = None
    if C.get("CAMERA") == "1" and C.get("REVERSE_GPIO"):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from hu_hudiy_api import HudiyLink
        reverse = DigitalInputDevice(int(C["REVERSE_GPIO"]), pull_up=low("REVERSE_ACTIVE"), bounce_time=0.1)
        link = HudiyLink("headunit-power", port=int(C.get("HUDIY_API_PORT", 44405)))
        camera = link.set_reverse
    pm = PowerManager(C, acc, illum, amp, state_file=state, reverse=reverse, camera=camera)
    print(f"power manager: mode={pm.mode} delay={pm.delay}s", flush=True)
    while True:
        pm.tick(time.monotonic())
        if pm.done: time.sleep(60)
        time.sleep(0.5)

if __name__ == "__main__":
    main()
