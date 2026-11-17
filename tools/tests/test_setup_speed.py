"""Software updates install before the screen comes up, so setup has to be quick when little changed:
systemd re-reads its unit files once (not after every enable/disable - ~1.2 s each on a Pi 4, some 40 of
them per setup), but still before anything is started; and when every package is already installed
there's no apt run, no package-list download and no loading of the router just to check it."""
import os, shutil, stat, subprocess, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())
stubs = T / "stubs"; stubs.mkdir()
LOG = T / "calls.log"
def stub(name, body):
    p = stubs / name; p.write_text("#!/bin/sh\n" + body + "\n"); p.chmod(p.stat().st_mode | stat.S_IEXEC)
stub("systemctl", f'echo "systemctl $*" >> {LOG}')
env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}

# 1) one re-read: enables / disables / masks with --no-reload; the re-read just before a start, then once at the end
script = f"""
source {ROOT}/lib/common.sh
export HU_DEFER_RELOAD={T}/reload.flag
systemctl daemon-reload
systemctl enable a.service
systemctl disable --now b.service c.socket
systemctl mask d.service
systemctl daemon-reload
systemctl is-enabled a.service
systemctl start --no-block e.service
systemctl enable f.service
systemctl enable --now g.service
systemctl disable h.service
[ -e "$HU_DEFER_RELOAD" ] && {{ rm -f "$HU_DEFER_RELOAD"; command systemctl daemon-reload; }}   # (setup.sh's end)
"""
r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True); assert r.returncode == 0, r.stderr
calls = LOG.read_text().splitlines()
assert calls == ["systemctl --no-reload enable a.service", "systemctl --no-reload disable --now b.service c.socket",
                 "systemctl --no-reload mask d.service", "systemctl is-enabled a.service",
                 "systemctl daemon-reload", "systemctl start --no-block e.service",
                 "systemctl --no-reload enable f.service",
                 "systemctl daemon-reload", "systemctl --no-reload enable --now g.service",
                 "systemctl --no-reload disable h.service", "systemctl daemon-reload"], calls
LOG.unlink()
r = subprocess.run(["bash", "-c", f"source {ROOT}/lib/common.sh; unset HU_DEFER_RELOAD; systemctl enable x.service; systemctl daemon-reload"],
                   env=env, capture_output=True, text=True)
assert LOG.read_text().splitlines() == ["systemctl enable x.service", "systemctl daemon-reload"], "a stage run by itself acts as before"
src = (ROOT / "setup.sh").read_text()
assert "export HU_DEFER_RELOAD" in src and "trap" in src and src.index("export HU_DEFER_RELOAD") < src.index('for stage in "$HU_ROOT"/setup.d')
print("1) systemd re-reads its unit files once per setup, and before anything is started")

# 2) packages: nothing to install -> no apt, no router load; one missing -> apt as before
hu = T / "hu"; (hu / "lib").mkdir(parents=True); (hu / "setup.d").mkdir(); (hu / "config").mkdir()
(hu / "lib/common.sh").write_text((ROOT / "lib/common.sh").read_text().replace("HU_ROOT=/opt/headunit", f"HU_ROOT={hu}")
                                  .replace("HU_CONF=/etc/headunit/headunit.conf", f"HU_CONF={T}/hu.conf")
                                  .replace("HU_LOG=/var/log/headunit-setup.log", f"HU_LOG={T}/setup.log"))
shutil.copy(ROOT / "setup.d/10-packages.sh", hu / "setup.d/")
shutil.copy(ROOT / "config/packages.txt", hu / "config/")
(T / "hu.conf").write_text("RADIO=1\n")
pins = dict(l[4:].split("==") for l in (ROOT / "config/packages.txt").read_text().splitlines() if l.startswith("pip:"))
venv = T / "venv"; (venv / "bin").mkdir(parents=True)
cases = "\n".join(f"  *\"'{n}'\"*) echo {v};;" for n, v in pins.items())
(venv / "bin/python").write_text(f"""#!/bin/sh
case "$*" in
  *"import valhalla"*) echo "router loaded" >> {LOG}; echo valhalla ok;;
{cases}
esac
""")
(venv / "bin/python").chmod(0o755)
stub("apt-get", f'echo "apt-get $*" >> {LOG}')
def dpkg(missing):
    stub("dpkg-query", 'for a in "$@"; do case "$a" in -*) ;; ' + (f'{missing}) ;; ' if missing else '') + '*) echo "ii ";; esac; done')
def stage():
    if LOG.exists(): LOG.unlink()
    r = subprocess.run(["bash", str(hu / "setup.d/10-packages.sh")], env={**env, "HU_VENV": str(venv)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout, LOG.read_text() if LOG.exists() else ""
dpkg(None)
out, calls = stage()
assert "all" in out and "installed" in out and "valhalla ok" in out and "apt-get" not in calls and "router loaded" not in calls, (out, calls)
dpkg("rtl-sdr")
out, calls = stage()
assert "apt-get update" in calls and "apt-get install" in calls and "rtl-sdr" in calls, calls
print("2) packages all there: no apt run, no router load; one missing: installed as before")
print("setup speed: all tests passed")
