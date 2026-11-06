"""Quick start: the boot settings go in once (no duplicates on the one-line cmdline), the desktop's
pre-Hudiy step is one program that only writes Hudiy's config when something changed, and the start-up
timeline (Linux -> system -> desktop -> bridge -> Hudiy -> map) is recorded per boot and kept for the
last starts (Settings > Developer)."""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
T = Path(tempfile.mkdtemp())

# 1) setup stage 22 on stand-in boot files (twice: nothing doubles)
boot = T / "boot"; boot.mkdir()
(boot / "config.txt").write_text("[all]\ndtoverlay=vc4-kms-v3d\n")
(boot / "cmdline.txt").write_text("console=serial0,115200 console=tty1 root=PARTUUID=abc rootfstype=ext4 fsck.repair=yes rootwait quiet splash\n")
home = T / "home"; (home / ".config").mkdir(parents=True)
lib = T / "lib"; lib.mkdir()
common = (ROOT / "lib/common.sh").read_text().replace("BOOT_CFG=/boot/firmware/config.txt", f"BOOT_CFG={boot}/config.txt") \
    .replace("BOOT_CMDLINE=/boot/firmware/cmdline.txt", f"BOOT_CMDLINE={boot}/cmdline.txt").replace("HU_LOG=/var/log/headunit-setup.log", f"HU_LOG={T}/setup.log")
(lib / "common.sh").write_text(common + f'\nhu_user() {{ echo "$(id -un)"; }}\nhu_home() {{ echo "{home}"; }}\nudir() {{ mkdir -p "$@"; }}\nchown() {{ :; }}\nloginctl() {{ echo "linger $*" >> {T}/linger.log; }}\n')
stage = T / "setup.d"; stage.mkdir()
(stage / "22-fastboot.sh").write_text((ROOT / "setup.d/22-fastboot.sh").read_text())
for _ in range(2):
    r = subprocess.run(["bash", str(stage / "22-fastboot.sh")], capture_output=True, text=True); assert r.returncode == 0, r.stderr
cfg = (boot / "config.txt").read_text(); cmd = (boot / "cmdline.txt").read_text()
assert cfg.count("boot_delay=0") == 1 and "disable_splash=1" in cfg and "camera_auto_detect=0" in cfg, cfg
assert cmd.count("\n") == 1 and cmd.count("quiet") == 1 and cmd.count("loglevel=3") == 1 and "vt.global_cursor_default=0" in cmd and "logo.nologo" in cmd, cmd
assert "enable-linger" in (T / "linger.log").read_text()
assert "desktop_bg=#000000" in (home / ".config/pcmanfm/default/desktop-items-HDMI-A-1.conf").read_text()
assert "autohide = true" in (home / ".config/wf-panel-pi.ini").read_text()
print("1) boot settings once (no pause, no rainbow, no boot text), services with the system, black desktop, taskbar hidden")

# 2) the one pre-Hudiy program: Hudiy's config only written when something changed; a desktop mark
run = T / "run"; run.mkdir()
cfgdir = home / ".hudiy/share/config"; cfgdir.mkdir(parents=True)
hcfg = cfgdir / "main_configuration.json"
hcfg.write_text(json.dumps({"theme": {"darkThemeEnabled": False, "dark": {"sourceColor": "#000000"}, "light": {"sourceColor": "#000000"}},
                            "application": {"defaultAction": ""}}))
(home / ".config/headunit").mkdir(parents=True); (home / ".config/headunit/settings.json").write_text(json.dumps({"theme": "neon-overdrive"}))
env = {**os.environ, "HOME": str(home), "XDG_RUNTIME_DIR": str(run), "HU_STYLES": str(ROOT / "apps/styles"), "HU_HUDIY_BG": str(T / "bg"), "HU_CONF": str(T / "none")}
r = subprocess.run([sys.executable, str(ROOT / "bin/hu-desktop-start.py")], env=env, capture_output=True, text=True); assert r.returncode == 0, r.stderr
assert "Hudiy skin: updated" in r.stdout and "start screen: updated" in r.stdout, r.stdout
d = json.loads(hcfg.read_text()); assert d["theme"]["darkThemeEnabled"] is True and d["application"]["defaultAction"] == "hu_navigation"
m0 = hcfg.stat().st_mtime_ns; time.sleep(0.05)
r = subprocess.run([sys.executable, str(ROOT / "bin/hu-desktop-start.py")], env=env, capture_output=True, text=True)
assert "Hudiy skin: unchanged" in r.stdout and "start screen: unchanged" in r.stdout and hcfg.stat().st_mtime_ns == m0, r.stdout
marks = json.loads((run / "headunit-boot.json").read_text())["marks"]; assert "desktop" in marks
print("2) one quick program before Hudiy; Hudiy's config left alone when it's already right; desktop mark")

# 3) systemd-analyze parsing (with and without the boot RAM disk)
import hu_boot
class R:
    def __init__(self, out): self.stdout = out
orig = hu_boot.subprocess.run
for out, want in [("Startup finished in 2.104s (kernel) + 1.5s (initrd) + 9.832s (userspace) = 13.4s\ngraphical.target reached after 9.6s in userspace.\n",
                   {"kernel": 2.1, "initrd": 1.5, "userspace": 9.83, "graphical": 9.6}),
                  ("Startup finished in 1.9s (kernel) + 1min 2.5s (userspace) = 1min 4.4s\ngraphical.target reached after 1min 1.2s in userspace.\n",
                   {"kernel": 1.9, "userspace": 62.5, "graphical": 61.2})]:
    hu_boot.subprocess.run = lambda *a, **k: R(out)
    assert hu_boot.systemd() == want, hu_boot.systemd()
hu_boot.subprocess.run = orig
print("3) systemd's kernel / boot RAM disk / system times read")

# 4) through the bridge: its own mark, the map's, a history of starts
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
(T / "hu.conf").write_text(f"BRIDGE_PORT={port}\nPHONE_LINK=0\nCONTACTS=0\nROUTING_WARM_DELAY=9999\n")
(T / "user").mkdir(); (T / "usage").mkdir(); (T / "data").mkdir()
benv = {**os.environ, "HU_CONF": str(T / "hu.conf"), "USER_DIR": str(T / "user"), "DATA_DIR": str(T / "data"), "APP_DIR": str(ROOT / "apps"),
        "USAGE_LOG_DIR": str(T / "usage"), "MEDIA_ROOT": str(T / "media"), "POWER_STATE_FILE": str(T / "st.json"), "HOME": str(T), "XDG_RUNTIME_DIR": str(run)}
b = subprocess.Popen([sys.executable, str(ROOT / "bin/hu-bridge.py")], env=benv, stdout=open(T / "b.log", "w"), stderr=subprocess.STDOUT)
def call(p, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{p}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())
try:
    for _ in range(60):
        try: r = call("/api/boot"); break
        except OSError: time.sleep(0.25)
    assert "bridge" in r["now"] and "desktop" in r["now"] and "map" not in r["now"], r
    call("/api/boot", {"mark": "map"}); call("/api/boot", {"mark": "map"})        # a second one (reload) doesn't count again
    r = call("/api/boot"); assert r["now"]["map"] >= r["now"]["bridge"] and len(r["history"]) == 1 and r["history"][0]["map"] == r["now"]["map"], r
finally:
    b.terminate(); b.wait(5)
print("4) bridge and map marks; one history entry per start")
print("boot: all tests passed")
