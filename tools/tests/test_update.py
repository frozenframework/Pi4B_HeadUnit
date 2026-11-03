"""Software updates end to end, with real git repos and real SSH-signed tags:
signed release installs; unsigned, wrong-key, lightweight, renamed (replayed) and mislabeled tags
are refused; a release that doesn't start is rolled back (after verify fails, after a boot loop,
after setup fails twice) and never retried; the updater is promoted only after a good start;
config/packages.txt covers every package the setup stages install."""
import json, os, re, shutil, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
T = Path(tempfile.mkdtemp())
os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")   # ignore this machine's git settings
def sh(*cmd, cwd=None, check=True, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    if check and r.returncode: raise SystemExit(f"{cmd} failed:\n{r.stdout}{r.stderr}")
    return r

# ---- 0) packages.txt lists every apt package any stage installs
listed = {l.strip() for l in (ROOT / "config/packages.txt").read_text().splitlines() if l.strip() and not l.startswith(("#", "pip:"))}
used = set()
for f in (ROOT / "setup.d").glob("*.sh"):
    s = f.read_text()
    for m in re.finditer(r"apt-get install[^\n]*?--no-install-recommends ([^\n\"$]+)", s): used |= set(m.group(1).split())
    m = re.search(r"PKGS=\((.*?)\n\)", s, re.S)
    if m: used |= {w for line in m.group(1).splitlines() for w in line.split("#")[0].split()}
    used |= {w for m in re.finditer(r"PKGS\+=\(([^)]*)\)", s) for w in m.group(1).split()}
assert used and used <= listed, f"add to config/packages.txt: {sorted(used - listed)}"

# ---- keys: yours, and someone else's
for k in ("trusted", "other"):
    sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", k, "-f", str(T / k))
SIGNERS = f'# test\nheadunit-release namespaces="git" {(T / "trusted.pub").read_text().split()[0]} {(T / "trusted.pub").read_text().split()[1]}\n'

# ---- "GitHub": a repo with the real updater and a stub setup.sh
SRC = T / "src"; SRC.mkdir()
G = lambda *a, **kw: sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "gpg.format=ssh", *a, cwd=SRC, **kw)
G("init", "-q", "-b", "main")
for d in ("bin", "config", "docs", "cad/print"): (SRC / d).mkdir(parents=True)
shutil.copy(ROOT / "bin/hu-update.py", SRC / "bin/hu-update.py")
shutil.copy(ROOT / "config/packages.txt", SRC / "config/packages.txt")
(SRC / "config/release-signers").write_text(SIGNERS)
(SRC / "docs/big.xlsx").write_text("x" * 1000); (SRC / "cad/print/a.stl").write_text("solid")
SETUP_OK = '#!/bin/bash\nV=$(cat "$(dirname "$(readlink -f "$0")")/VERSION")\necho "$V $HU_UPDATE_APPLY" >> "$SETUP_MARK"\n'
def release(v, tag=None, key="trusted", sign=True, annotated=True, setup=SETUP_OK, version_file=None):
    (SRC / "VERSION").write_text((version_file or v) + "\n")
    (SRC / "setup.sh").write_text(setup); (SRC / "setup.sh").chmod(0o755)
    (SRC / "bin/hu-update.py").write_text((ROOT / "bin/hu-update.py").read_text() + f"\n# build {v}\n")
    G("add", "-A"); G("commit", "-q", "--allow-empty", "-m", v)
    tag = tag or "v" + v
    if not annotated: G("tag", tag)
    elif sign: G("-c", f"user.signingkey={T / key}", "tag", "-s", tag, "-m", v)
    else: G("tag", "-a", tag, "-m", v)

# ---- the Pi: version 1.1.0 installed, updater + key list in the known-good place
REL, LIB, STATE = T / "releases", T / "lib", T / "state"
release("1.1.0")
(REL / "1.1.0").mkdir(parents=True); sh("git", "archive", "v1.1.0", "-o", str(T / "a.tar"), cwd=SRC); sh("tar", "-xf", str(T / "a.tar"), "-C", str(REL / "1.1.0"))
LINK = T / "headunit"; LINK.symlink_to(REL / "1.1.0")
LIB.mkdir(); shutil.copy(SRC / "bin/hu-update.py", LIB / "hu-update.py"); (LIB / "release-signers").write_text(SIGNERS)
(T / "headunit.conf").write_text(f"UPDATE_REPO=file://{SRC}\n")

HEALTH = {"version": None}
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({"version": HEALTH["version"]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
ENV = dict(HU_LINK=str(LINK), HU_RELEASES=str(REL), HU_UPDATE_STATE=str(STATE), HU_UPDATE_LIB=str(LIB),
           HU_CONF=str(T / "headunit.conf"), HU_UPDATE_LOG=str(T / "update.log"), HU_PREFETCH="0",
           HU_HEALTH_URL=f"http://127.0.0.1:{srv.server_port}/", HU_VERIFY_SECONDS="4", HU_NO_REBOOT="1",
           SETUP_MARK=str(T / "setup.mark"))
def upd(*a):
    return sh(sys.executable, str(LIB / "hu-update.py"), *a, env=ENV, check=False)  # always the known-good copy, like systemd
cur = lambda: (LINK / "VERSION").read_text().strip()
state = lambda: json.loads((STATE / "state.json").read_text()) if (STATE / "state.json").exists() else {}
status = lambda: json.loads((STATE / "status.json").read_text())
bad = lambda: json.loads((STATE / "bad.json").read_text()) if (STATE / "bad.json").exists() else {}
marks = lambda: (T / "setup.mark").read_text().split("\n") if (T / "setup.mark").exists() else []

# ---- 1) one good release among bad ones
release("1.2.0")
G("update-ref", "refs/tags/v1.6.0", G("rev-parse", "v1.2.0").stdout.strip())     # old signed tag re-published under a new name
release("1.3.0", key="other")                                                   # wrong key
release("1.4.0", annotated=False)                                               # lightweight tag
release("1.5.0", sign=False)                                                    # annotated, unsigned
release("1.7.0", version_file="1.2.0")                                          # signed, but VERSION inside disagrees
upd("check", "--force")
assert state() == {"pending": "1.2.0"}, (state(), (T / "update.log").read_text())
assert set(bad()) == {"1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"}, bad()
assert "renamed" in bad()["1.6.0"] and "VERSION" in bad()["1.7.0"], bad()
assert not (REL / "1.2.0" / "docs").exists() and not (REL / "1.2.0" / "cad").exists(), "docs/cad excluded"
assert status()["state"] == "ready" and cur() == "1.1.0", "downloaded, not installed while running"
print("1) only the release signed with your key was downloaded; 5 bad tags refused")

# ---- 2) next key-on: apply swaps + runs setup; verify confirms and promotes the updater
upd("apply")
assert cur() == "1.2.0" and state()["trial"] == "1.2.0" and "1.2.0 1" in marks(), (state(), marks())
assert "# build 1.1.0" in (LIB / "hu-update.py").read_text(), "not promoted before it proves itself"
HEALTH["version"] = "1.2.0"; upd("verify")
assert state() == {} and status()["state"] == "updated", status()
assert "# build 1.2.0" in (LIB / "hu-update.py").read_text(), "promoted after a good start"
upd("check", "--force"); assert status()["state"] == "up_to_date", status()
print("2) installed at key-on, confirmed, updater promoted; nothing newer to get")

# ---- 3) a release whose apps don't come up -> rolled back, never retried
release("1.8.0"); upd("check", "--force"); upd("apply"); assert cur() == "1.8.0"
upd("verify")   # health still reports 1.2.0
assert cur() == "1.2.0" and state() == {} and "1.8.0" in bad() and status()["state"] == "rolled_back", (cur(), status())
assert marks()[-2] == "1.2.0 1", "previous version's setup re-run"
assert "# build 1.2.0" in (LIB / "hu-update.py").read_text(), "known-good updater untouched"
upd("check", "--force"); assert status()["state"] == "up_to_date" and state() == {}, status()
print("3) broken release rolled back to 1.2.0 and blacklisted")

# ---- 4) boot loop (power keeps dropping before verify) -> rollback on the 4th start
release("1.9.0"); upd("check", "--force"); upd("apply"); assert cur() == "1.9.0"
for _ in range(2): upd("apply"); assert cur() == "1.9.0"
upd("apply"); assert cur() == "1.2.0" and "1.9.0" in bad(), (cur(), bad())
print("4) boot loop rolled back after 3 tries")

# ---- 5) setup fails at key-on (e.g. offline + a new package) -> stays on old, retries once, then gives up
release("2.0.0", setup="#!/bin/bash\nexit 1\n")
upd("check", "--force"); upd("apply")
assert cur() == "1.2.0" and state() == {"pending": "2.0.0", "tries": 1}, (cur(), state())
upd("apply"); assert cur() == "1.2.0" and state() == {} and "2.0.0" in bad(), (cur(), state(), bad())
print("5) failed install kept 1.2.0, retried once, then gave up")

# ---- 6) no signers file -> refuses everything
release("2.1.0"); (LIB / "release-signers").write_text("# empty\n")
upd("check", "--force"); assert status()["state"] == "error" and state() == {}, status()
print("6) without a release key nothing is trusted")

# ---- 7) offline
(T / "headunit.conf").write_text("UPDATE_REPO=file:///nonexistent\n"); (LIB / "release-signers").write_text(SIGNERS)
upd("check", "--force"); assert status()["state"] == "offline", status()
print("7) offline handled")
print("updates: all tests passed")
