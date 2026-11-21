#!/usr/bin/env bash
# Why Hudiy's own call screen shows the wrong name: looks at Hudiy's copy of the phone's contacts on
# the head unit (~/.hudiy/cache/hfp/pb*.vcf) for the usual causes, and prints what it finds HERE only
# (nothing is saved or copied off the head unit):
#   - the contact Hudiy keeps showing: its position in the phonebook and its phone numbers
#   - phone numbers too short to be a real number (an area code, an extension, "1"), which a loose
#     match can find inside every caller's number
#   - the first contacts in the phonebook (some car stereos show the first one when nothing matches)
#
#   tools/check-hudiy-contacts.sh "Austin Gonzalez"                       (miata-headunit.local)
#   tools/check-hudiy-contacts.sh "Austin Gonzalez" f250-headunit.local
# Needs Settings > Developer > SSH login over Wi-Fi on.
set -euo pipefail
NAME=${1:?usage: tools/check-hudiy-contacts.sh \"Name shown on the call screen\" [host]}
HOST=${2:-miata-headunit.local}; U=${HU_SSH_USER:-headunit}
ssh "$U@$HOST" "python3 - $(printf '%q' "$NAME")" <<'PY'
import glob, os, re, sys
want = sys.argv[1].strip().lower()
files = sorted(glob.glob(os.path.expanduser("~/.hudiy/cache/hfp/pb*.vcf")), key=os.path.getmtime, reverse=True)
if not files: sys.exit("Hudiy has no copy of the phone's contacts (~/.hudiy/cache/hfp/pb*.vcf)")
def cards(text):
    text = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))
    cur = None
    for line in text.split("\n"):
        k, _, v = line.partition(":"); prop = k.split(";")[0].split(".")[-1].upper()
        if prop == "BEGIN": cur = {"fn": "", "n": "", "tel": []}
        elif cur is None: continue
        elif prop == "FN": cur["fn"] = v.strip()
        elif prop == "N": cur["n"] = " ".join(x for x in reversed(v.split(";")[:2]) if x).strip()
        elif prop == "TEL": cur["tel"].append(v.strip())
        elif prop == "END": yield cur; cur = None
for f in files[:2]:
    cs = list(cards(open(f, encoding="utf-8", errors="replace").read()))
    name = lambda c: c["fn"] or c["n"] or "(no name)"
    print(f"\n{os.path.basename(f)}: {len(cs)} contacts")
    print("  first in the phonebook:", "; ".join(f"#{i} {name(c)}" for i, c in enumerate(cs[:3])))
    hits = [(i, c) for i, c in enumerate(cs) if want in name(c).lower()]
    for i, c in hits:
        print(f"  #{i} {name(c)}: numbers {c['tel'] or '(none)'}")
    if not hits: print(f"  no contact named like '{sys.argv[1]}'")
    short = [(i, c, t) for i, c in enumerate(cs) for t in c["tel"] if len(re.sub(r"\D", "", t)) < 7]
    print(f"  numbers with fewer than 7 digits: {len(short)}")
    for i, c, t in short[:15]: print(f"    #{i} {name(c)}: '{t}'")
    print(f"  contacts with no number (or only blank ones): {sum(1 for c in cs if not any(re.sub(r'[^0-9]', '', t) for t in c['tel']))}")
    print("  (that's the file as the phone sent it: Hudiy reads it through the clean-up, without those)")
import subprocess
pid = subprocess.run(["pgrep", "-x", "hudiy"], capture_output=True, text=True).stdout.split()
loaded = bool(pid) and "hu-vcf-filter" in open(f"/proc/{pid[0]}/maps").read()
print("\nHudiy reads the contacts through the clean-up (lib/hu-vcf-filter.so):", "yes" if loaded else "NO" if pid else "Hudiy isn't running")
PY
