#!/usr/bin/env python3
"""
Head-unit software updates from GitHub, signed by you.

  hu-update.py check [--force]   look for a newer signed release; download, verify and stage it
  hu-update.py apply             (at startup, before the screen) switch to a staged release
  hu-update.py verify            (after the desktop starts) confirm it runs, otherwise roll back
  hu-update.py status            print the current state
  hu-update.py go-back X.Y.Z     (Settings > Updates) switch back to an earlier version that's still on
                                 the head unit, skip the version you left from now on, and restart

How a release is trusted
  A release is a git tag "vX.Y.Z" in the GitHub repo. It is installed only if the tag is signed by a
  key listed in the *known-good* release-signers file (/usr/local/lib/headunit/release-signers,
  promoted from config/release-signers of the last release that started correctly). An unsigned
  tag, a tag signed by any other key, a re-named old tag, or an older version is ignored.

How it stays safe in a car
  - Every version lives in its own folder (/opt/headunit-releases/X.Y.Z); /opt/headunit is a
    symlink that is swapped in one step, so a power cut leaves either the old or the new version.
  - Nothing changes while driving: downloads happen in the background, the swap happens at the
    next key-on, before the screen starts.
  - After the swap, "verify" waits for the head-unit services. If they don't come up (or the Pi
    keeps losing power before they do), it switches back to the previous version and never tries
    that release again.
  - This program itself runs from a known-good copy outside the release folders, so a broken
    release can't break the rollback.
"""
import fcntl, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

E = os.environ.get
LINK = Path(E("HU_LINK", "/opt/headunit"))
RELEASES = Path(E("HU_RELEASES", "/opt/headunit-releases"))
STATE_DIR = Path(E("HU_UPDATE_STATE", "/var/lib/headunit/update"))
LIB = Path(E("HU_UPDATE_LIB", "/usr/local/lib/headunit"))
CONF = Path(E("HU_CONF", "/etc/headunit/headunit.conf"))
HEALTH_URL = E("HU_HEALTH_URL", "http://127.0.0.1:8765/api/status")
DEFAULT_REPO = "https://github.com/frozenframework/Pi4B_HeadUnit.git"
LOG = Path(E("HU_UPDATE_LOG", "/var/log/headunit-update.log"))
STATE = STATE_DIR / "state.json"      # {"pending": v} | {"trial": v, "previous": v, "boots": n}
STATUS = STATE_DIR / "status.json"    # what Settings > Updates shows
BAD = STATE_DIR / "bad.json"          # {version: reason} never retried
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
NEEDED = ["VERSION", "setup.sh", "bin/hu-update.py", "config/release-signers", "config/packages.txt"]
EXCLUDE = ["docs", "cad/print", "tools/node_modules"]   # not needed on the Pi; keeps downloads small

def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with open(LOG, "a") as f: f.write(line + "\n")
    except OSError:
        pass

def conf(key, default=""):
    try:
        for line in CONF.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return default

def rj(p, d):
    try: return json.loads(Path(p).read_text())
    except (OSError, ValueError): return d

def wj(p, data):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(data, indent=1)); os.chmod(tmp, 0o644); os.replace(tmp, p)

def vtuple(v): return tuple(int(x) for x in v.split("."))

def current():
    try: return (LINK / "VERSION").read_text().strip()
    except OSError: return "0.0.0"

def status(state, message, **extra):
    st = rj(STATUS, {})
    st.update({"state": state, "message": message, "current": current(), "time": int(time.time())}, **extra)
    wj(STATUS, st); log(f"[{state}] {message}")

def history(event):
    st = rj(STATUS, {}); h = st.get("history", [])
    h.append({"time": int(time.time()), **event}); st["history"] = h[-20:]; wj(STATUS, st)

def mark_bad(v, reason):
    b = rj(BAD, {}); b[v] = reason; wj(BAD, b)

def run(cmd, timeout=600, **kw):
    kw.setdefault("env", {**os.environ, "LC_ALL": "C.UTF-8"})   # untranslated output, whatever language the head unit uses
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)

def git(*args, timeout=600, repo=None):
    cmd = ["git"] + (["-C", str(repo)] if repo else []) + list(args)
    return run(cmd, timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"})

def switch(to: Path):
    """Point /opt/headunit at a release folder in one atomic rename."""
    tmp = LINK.with_name(LINK.name + ".new")
    if tmp.is_symlink() or tmp.exists(): tmp.unlink()
    tmp.symlink_to(to)
    os.replace(tmp, LINK)
    log("now running", to)

def run_setup(env_extra=None):
    """Re-run the (now current) release's setup with the existing /etc/headunit/headunit.conf."""
    cmd = E("HU_SETUP", str(LINK / "setup.sh") + " --yes").split()
    env = {**os.environ, "HU_UPDATE_APPLY": "1", **(env_extra or {})}
    with open(LOG, "a") as out:
        r = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, timeout=1800)
    return r.returncode == 0

# ------------------------------------------------------------------ check: find, verify, stage
def remote_versions(repo_url):
    r = git("ls-remote", "--tags", repo_url, timeout=60)
    if r.returncode != 0:
        raise ConnectionError(r.stderr.strip()[-200:] or "no connection")
    out = set()
    for line in r.stdout.splitlines():
        ref = line.split("\t")[-1]
        name = ref.removeprefix("refs/tags/")
        if TAG_RE.match(name): out.add(name[1:])
    return sorted(out, key=vtuple, reverse=True)

def verify_tag(repo, tag, signers):
    """True only for an annotated tag, signed by an allowed key, whose own name matches the ref."""
    obj = git("cat-file", "-p", f"refs/tags/{tag}", repo=repo)
    if obj.returncode != 0 or git("cat-file", "-t", f"refs/tags/{tag}", repo=repo).stdout.strip() != "tag":
        return False, "not a signed release tag"
    head = dict(l.split(" ", 1) for l in obj.stdout.split("\n\n", 1)[0].splitlines() if " " in l)
    if head.get("tag") != tag or head.get("type") != "commit":
        return False, f"tag object says '{head.get('tag')}', not '{tag}' (renamed old release?)"
    r = git("-c", "gpg.format=ssh", "-c", "gpg.ssh.program=ssh-keygen", "-c", f"gpg.ssh.allowedSignersFile={signers}",
            "verify-tag", tag, repo=repo)
    if r.returncode != 0:
        return False, "signature is not from your release key"
    return True, (r.stderr.strip().splitlines() or [""])[0]

def prefetch(rel: Path):
    """Download (not install) any new system packages the release needs, while we have internet."""
    if E("HU_PREFETCH", "1") == "0": return
    apt, pip = [], []
    for line in (rel / "config" / "packages.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line: continue
        (pip if line.startswith("pip:") else apt).append(line.removeprefix("pip:"))
    missing = [p for p in apt if run(["dpkg-query", "-W", "-f=${Status}", p]).stdout != "install ok installed"]
    if missing:
        log("downloading packages for later:", " ".join(missing))
        run(["apt-get", "update", "-qq"], timeout=900)
        r = run(["apt-get", "install", "-y", "-qq", "--download-only", "--no-install-recommends", *missing], timeout=1800)
        if r.returncode != 0: raise RuntimeError("package download failed: " + r.stderr.strip()[-200:])
    venv = Path("/opt/headunit-venv/bin")
    for spec in pip:
        name, _, ver = spec.partition("==")
        have = run([str(venv / "python"), "-c", f"import importlib.metadata as m; print(m.version('{name}'))"]).stdout.strip()
        if have != ver:
            log("downloading python package for later:", spec)
            r = run([str(venv / "pip"), "download", "-q", "-d", "/var/cache/headunit/wheels", spec], timeout=1800)
            if r.returncode != 0: raise RuntimeError("python package download failed: " + r.stderr.strip()[-200:])

def check(force=False):
    if conf("UPDATES", "1") == "0":
        status("off", "Automatic updates are turned off (UPDATES=0 in /etc/headunit/headunit.conf)"); return 0
    st = rj(STATE, {})
    if st.get("trial"):
        log("an update is still being confirmed; not checking"); return 0
    last = rj(STATUS, {}).get("last_check", 0)
    if not force and time.time() - last < 600:
        return 0
    repo_url = conf("UPDATE_REPO", DEFAULT_REPO)
    status("checking", "Checking for updates…")
    try:
        versions = remote_versions(repo_url)
    except Exception as e:
        status("offline", "No internet right now; will check again when connected", last_error=str(e)[:200]); return 0
    cur, bad = current(), rj(BAD, {})
    newer = [v for v in versions if vtuple(v) > vtuple(cur) and v not in bad]
    if st.get("pending"):
        newer = [v for v in newer if vtuple(v) > vtuple(st["pending"])]
    if not newer:
        p = st.get("pending")
        status("ready" if p else "up_to_date",
               f"Version {p} is ready; installs at the next key-on" if p else f"Up to date (version {cur})",
               last_check=int(time.time()), available=p or None)
        return 0
    signers = LIB / "release-signers"
    if not signers.exists() or not re.search(r"^[^#\n]*\bssh-ed25519 ", signers.read_text(), re.M):
        status("error", "No release key installed on this head unit; can't trust any update", last_check=int(time.time())); return 1
    for v in newer:
        tag = "v" + v
        status("downloading", f"Downloading version {v}…")
        with tempfile.TemporaryDirectory(dir=STATE_DIR) as td:
            repo = Path(td) / "repo.git"
            git("init", "-q", "--bare", str(repo))
            r = git("fetch", "-q", "--depth", "1", "--no-tags", repo_url, f"+refs/tags/{tag}:refs/tags/{tag}", repo=repo, timeout=1800)
            if r.returncode != 0:
                status("offline", "Download interrupted; will try again", last_error=r.stderr.strip()[-200:]); return 0
            ok, why = verify_tag(repo, tag, signers)
            if not ok:
                log(f"REJECTED {tag}: {why}"); mark_bad(v, why); history({"event": "rejected", "version": v, "why": why}); continue
            log(f"{tag} signature ok: {why}")
            RELEASES.mkdir(parents=True, exist_ok=True)
            stage = RELEASES / f".{v}.incoming"
            shutil.rmtree(stage, ignore_errors=True); stage.mkdir()
            spec = ["."] + [f":(exclude){x}" for x in EXCLUDE]
            arch = subprocess.Popen(["git", "-C", str(repo), "archive", "--format=tar", tag, "--", *spec], stdout=subprocess.PIPE)
            tar = subprocess.run(["tar", "-x", "-C", str(stage)], stdin=arch.stdout, capture_output=True)
            arch.stdout.close()
            if arch.wait() != 0 or tar.returncode != 0:
                shutil.rmtree(stage, ignore_errors=True)
                status("error", f"Couldn't unpack version {v}"); return 1
        missing = [n for n in NEEDED if not (stage / n).exists()]
        inner = (stage / "VERSION").read_text().strip() if (stage / "VERSION").exists() else ""
        if missing or inner != v:
            why = f"release is incomplete ({', '.join(missing)})" if missing else f"VERSION file says {inner}, tag says {v}"
            shutil.rmtree(stage, ignore_errors=True); mark_bad(v, why); log(f"REJECTED {tag}: {why}"); continue
        try:
            prefetch(stage)
        except Exception as e:
            shutil.rmtree(stage, ignore_errors=True)
            status("offline", f"Version {v}: {e}; will try again"); return 0
        final = RELEASES / v
        shutil.rmtree(final, ignore_errors=True)
        os.replace(stage, final)
        wj(STATE, {"pending": v})
        status("ready", f"Version {v} is downloaded and verified; it installs at the next key-on",
               available=v, last_check=int(time.time()))
        history({"event": "downloaded", "version": v})
        return 0
    status("up_to_date", f"Up to date (version {cur})", last_check=int(time.time()), available=None)
    return 0

# ------------------------------------------------------------------ apply / verify / rollback
def promote():
    """The running release passed its checks: make its updater + key list the known-good ones."""
    LIB.mkdir(parents=True, exist_ok=True)
    for src, dst in ((LINK / "bin" / "hu-update.py", LIB / "hu-update.py"),
                     (LINK / "config" / "release-signers", LIB / "release-signers")):
        if src.exists():
            tmp = dst.with_suffix(".tmp"); shutil.copyfile(src, tmp); os.chmod(tmp, 0o755 if dst.suffix == ".py" else 0o644); os.replace(tmp, dst)

def prune(keep):
    keep = set(keep)
    vers = sorted([p for p in RELEASES.iterdir() if p.is_dir() and not p.name.startswith(".")],
                  key=lambda p: vtuple(p.name) if re.match(r"^\d+\.\d+\.\d+$", p.name) else (0,), reverse=True)
    for p in vers[2:]:
        if p.name not in keep and p.resolve() != LINK.resolve():
            shutil.rmtree(p, ignore_errors=True); log("removed old version", p.name)

def rollback(st, reason):
    v, prev = st["trial"], st.get("previous")
    log(f"ROLLBACK {v} -> {prev}: {reason}")
    mark_bad(v, reason)
    if prev and (RELEASES / prev).is_dir():
        switch(RELEASES / prev)
        if not run_setup():
            log("setup of the previous version reported errors (see this log)")
    STATE.unlink(missing_ok=True)
    status("rolled_back", f"Version {v} didn't start properly ({reason}); went back to {prev}. It won't be tried again.",
           available=None)
    history({"event": "rolled_back", "version": v, "to": prev, "why": reason})

def apply():
    st = rj(STATE, {})
    if st.get("trial"):
        st["boots"] = st.get("boots", 1) + 1
        if st["boots"] > 3:
            rollback(st, "it never finished starting in 3 tries"); return 0
        wj(STATE, st); log(f"trial boot {st['boots']} of version {st['trial']}"); return 0
    v = st.get("pending")
    if not v: return 0
    rel = RELEASES / v
    if not (rel / "VERSION").exists():
        STATE.unlink(missing_ok=True); status("error", f"Version {v} folder is missing; will download again"); return 0
    prev = current()
    prev_dir = LINK.resolve()
    status("installing", f"Installing version {v}…")
    switch(rel)
    if not run_setup():
        n = st.get("tries", 0) + 1
        switch(prev_dir); run_setup()
        if n >= 2:
            mark_bad(v, "setup failed twice"); STATE.unlink(missing_ok=True)
            status("rolled_back", f"Version {v} failed to install twice; staying on {prev}. Log: /var/log/headunit-update.log")
            history({"event": "install_failed", "version": v})
        else:
            wj(STATE, {"pending": v, "tries": n})
            status("ready", f"Version {v} couldn't finish installing (offline?); will retry at the next key-on")
        return 0
    wj(STATE, {"trial": v, "previous": prev, "previous_dir": str(prev_dir), "boots": 1})
    status("verifying", f"Version {v} installed; checking that everything starts…")
    return 0

def healthy(v):
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
            if r.status != 200 or json.loads(r.read() or b"{}").get("version") != v:
                return False
    except Exception:
        return False
    if shutil.which("systemctl") and run(["systemctl", "is-enabled", "headunit-power.service"]).stdout.strip() == "enabled":
        if run(["systemctl", "is-active", "headunit-power.service"]).stdout.strip() != "active":
            return False
    return True

def verify():
    st = rj(STATE, {})
    v = st.get("trial")
    if not v: return 0
    deadline = time.time() + float(E("HU_VERIFY_SECONDS", "240"))
    while time.time() < deadline:
        if healthy(v):
            promote(); STATE.unlink(missing_ok=True)
            prune(keep=[v, st.get("previous", "")])
            status("updated", f"Updated to version {v}", available=None, updated=int(time.time()))
            history({"event": "installed", "version": v, "from": st.get("previous")})
            return 0
        time.sleep(3)
    rollback(st, "the head-unit apps didn't come up within 4 minutes")
    if E("HU_NO_REBOOT") != "1":
        subprocess.run(["systemctl", "reboot"])
    return 0

# ------------------------------------------------------------------ go back to an earlier version (by hand)
def older_versions():
    """Earlier versions still kept on the head unit (complete folders only), newest first."""
    cur = current(); out = []
    for p in RELEASES.iterdir() if RELEASES.exists() else []:
        if p.is_dir() and re.match(r"^\d+\.\d+\.\d+$", p.name) and vtuple(p.name) < vtuple(cur):
            try:
                if (p / "VERSION").read_text().strip() == p.name and all((p / n).exists() for n in NEEDED): out.append(p.name)
            except OSError:
                pass
    return sorted(out, key=vtuple, reverse=True)

def go_back(v):
    cur, prev_dir = current(), LINK.resolve()
    if v not in older_versions():
        status("error", f"Version {v} isn't on this head unit any more"); return 1
    log(f"GO BACK {cur} -> {v} (asked for in Settings)")
    status("installing", f"Going back to version {v}…")
    STATE.unlink(missing_ok=True)                         # drop a downloaded-but-not-installed update too
    switch(RELEASES / v)
    if not run_setup():
        log("setup of", v, "reported errors; staying on", cur)
        switch(prev_dir); run_setup()
        status("error", f"Couldn't go back to {v}; still on {cur}. Log: /var/log/headunit-update.log"); return 1
    mark_bad(cur, f"you went back to {v}")                # never re-installed; newer versions still are
    status("went_back", f"Went back to version {v}. Version {cur} won't be installed again; newer versions will.",
           available=None)
    history({"event": "went_back", "version": v, "from": cur})
    if E("HU_NO_REBOOT") != "1":
        subprocess.run(["systemctl", "reboot"])
    return 0

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps({"current": current(), "state": rj(STATE, {}), "status": rj(STATUS, {}), "rejected": rj(BAD, {}),
                          "older": older_versions()}, indent=1)); return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True); os.chmod(STATE_DIR, 0o755)
    with open(STATE_DIR / ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if cmd == "check": return check(force="--force" in sys.argv)
        if cmd == "apply": return apply()
        if cmd == "verify": return verify()
        if cmd == "go-back" and len(sys.argv) > 2 and re.match(r"^\d+\.\d+\.\d+$", sys.argv[2]): return go_back(sys.argv[2])
    print(__doc__); return 2

if __name__ == "__main__":
    sys.exit(main())
