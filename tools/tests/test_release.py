"""tools/release.sh: a wrong passphrase (three tries) changes nothing and the same version can be run
again; half-made releases left by the older script (a "Release X" commit, no tag, not sent) are cleared
by themselves; a signing failure after the commit is undone too; uncommitted docs/ edits survive; a
release whose commit was already pushed without a tag is finished with the same number; the tag
verifies against config/release-signers and reaches the remote."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if not all(shutil.which(x) for x in ("ssh-agent", "ssh-add", "ssh-keygen", "git")):
    print("release: skipped (needs git and OpenSSH)"); sys.exit(0)
T = Path(tempfile.mkdtemp())
os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")     # this machine's own git signing setup stays out of it
key = T / "key"; subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "right pass", "-C", "t", "-f", str(key)], check=True)
pub = key.with_suffix(".pub").read_text().split()
def git(*a, cwd=None, check=True):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd or repo, capture_output=True, text=True, check=check)
remote = T / "remote.git"; subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
repo = T / "repo"; (repo / "tools").mkdir(parents=True); (repo / "config").mkdir(); (repo / "docs").mkdir()
shutil.copy(ROOT / "tools/release.sh", repo / "tools/release.sh")
(repo / "config/release-signers").write_text(f'headunit-release namespaces="git" {pub[0]} {pub[1]}\n')
(repo / "VERSION").write_text("1.2.29\n"); (repo / "docs/parts.txt").write_text("v1\n")
git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "base"); git("remote", "add", "origin", str(remote)); git("push", "-qu", "origin", "main")
(repo / "docs/parts.txt").write_text("my edit\n")                      # an uncommitted docs/ edit, like the parts list

def askpass(*answers):
    """A stand-in for typing the passphrase: gives the answers in turn."""
    n = T / "n"; n.write_text("0"); f = T / "askpass.sh"
    f.write_text("#!/bin/sh\ni=$(cat %s); echo $((i+1)) > %s\ncase $i in\n%s*) echo "";;\nesac\n" %
                 (n, n, "".join(f"{i}) echo '{a}';;\n" for i, a in enumerate(answers))))
    f.chmod(0o755); return {"SSH_ASKPASS": str(f), "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": ":0"}
def release(ver, env, k=key):
    e = {**os.environ, "HU_RELEASE_KEY": str(k), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", **env}
    e.pop("SSH_AUTH_SOCK", None)
    return subprocess.run(["bash", "tools/release.sh", ver, "notes"], cwd=repo, env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL)
head = lambda: git("rev-parse", "HEAD").stdout.strip()
tags = lambda: git("tag").stdout.split()
rtags = lambda: subprocess.run(["git", "--git-dir", str(remote), "tag"], capture_output=True, text=True).stdout.split()

h0 = head()
r = release("1.2.30", askpass("wrong", "also wrong", "nope"))
assert r.returncode == 1 and "wasn't unlocked" in r.stdout and "nothing was changed" in r.stdout, r.stdout + r.stderr
assert head() == h0 and (repo / "VERSION").read_text() == "1.2.29\n" and tags() == [] and (repo / "docs/parts.txt").read_text() == "my edit\n"
r = release("1.2.30", askpass("wrong", "right pass"))               # the same number again; second try is right
assert r.returncode == 0 and "released 1.2.30" in r.stdout, r.stdout + r.stderr
assert (repo / "VERSION").read_text() == "1.2.30\n" and tags() == ["v1.2.30"] and rtags() == ["v1.2.30"]
assert git("log", "-1", "--format=%s").stdout.strip() == "Release 1.2.30" and (repo / "docs/parts.txt").read_text() == "my edit\n"
signers = T / "signers"; signers.write_text((repo / "config/release-signers").read_text())
v = git("-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={signers}", "verify-tag", "v1.2.30", check=False); assert v.returncode == 0, v.stderr
print("1) wrong passphrase three times: nothing changes; run again with the same number: released, signed, sent")

# 2) what the older script left behind: two "Release" commits with no tags, not sent
for ver in ("1.2.31", "1.2.32"):
    (repo / "VERSION").write_text(ver + "\n"); git("commit", "-qm", f"Release {ver}", "VERSION")
r = release("1.2.31", askpass("right pass"))
assert r.returncode == 0 and r.stdout.count("clearing the unfinished release") == 2 and "released 1.2.31" in r.stdout, r.stdout + r.stderr
log = git("log", "--format=%s", "-3").stdout.split("\n")[:3]
assert log == ["Release 1.2.31", "Release 1.2.30", "base"] and tags() == ["v1.2.30", "v1.2.31"] and rtags() == ["v1.2.30", "v1.2.31"], (log, tags())
print("2) half-made releases from before (no tag, not sent) are cleared by themselves; the number can be used again")

# 3) signing fails after the commit (here: the public key file doesn't belong to the key): undone
bad = T / "bad"; shutil.copy(key, bad); other = T / "other"
subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
shutil.copy(other.with_suffix(".pub"), bad.with_suffix(".pub"))
(repo / "config/release-signers").write_text((repo / "config/release-signers").read_text() + f'x namespaces="git" {other.with_suffix(".pub").read_text()}')
git("commit", "-qm", "signers", "config/release-signers"); h1 = head()
r = release("1.2.32", askpass("right pass"), k=bad)
assert r.returncode != 0 and "wasn't made" in r.stdout, r.stdout + r.stderr
assert head() == h1 and (repo / "VERSION").read_text() == "1.2.31\n" and "v1.2.32" not in tags() and (repo / "docs/parts.txt").read_text() == "my edit\n"
print("3) a failure while signing: the release commit is taken back, files as they were")

# 4) a release commit that already went out with a plain "git push" (no tag): the same number finishes it
(repo / "VERSION").write_text("1.2.32\n"); git("commit", "-qm", "Release 1.2.32", "VERSION"); git("push", "-q", "origin", "main"); h2 = head()
r = release("1.2.31", askpass("right pass")); assert r.returncode == 1 and "must be higher than the current 1.2.32" in r.stdout
r = release("1.2.32", askpass("right pass"))
assert r.returncode == 0 and head() == h2 and "v1.2.32" in rtags(), r.stdout + r.stderr
r = release("1.2.32", askpass("right pass")); assert r.returncode == 1 and "already released" in r.stdout
print("4) a release commit pushed without its tag is finished with the same number; a finished one isn't made twice")
print("release: all tests passed")
