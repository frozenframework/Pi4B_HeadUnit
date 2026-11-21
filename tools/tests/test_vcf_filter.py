"""Hudiy's phonebook clean-up (lib/hu-vcf-filter.c, loaded into Hudiy by bin/hu-hudiy-run.sh): when
Hudiy opens its downloaded phonebook it reads it without contacts that have neither a number nor an
address, and without blank numbers (a blank number made Hudiy's call screen name the same wrong person
for every caller). Built and tried with real programs reading the file (cat, Python); Linux only."""
import os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if not sys.platform.startswith("linux") or not shutil.which("gcc"):
    print("vcf filter: skipped (needs Linux and gcc)"); sys.exit(0)
T = Path(tempfile.mkdtemp())
SO = T / "hu-vcf-filter.so"
subprocess.run(["gcc", "-O2", "-Wall", "-Werror", "-shared", "-fPIC", "-o", str(SO), str(ROOT / "lib/hu-vcf-filter.c"), "-ldl"], check=True)
D = T / "home/.hudiy/cache/hfp"; D.mkdir(parents=True)

CARDS = [
    ("me", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:My Number\r\nTEL;TYPE=CELL:+1 936 555 0100\r\nEND:VCARD\r\n", True),
    ("blank", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sam Ortiz\r\nN:Ortiz;Sam;;;\r\nTEL;TYPE=CELL:\r\nEND:VCARD\r\n", False),
    ("addr21", "BEGIN:VCARD\r\nVERSION:2.1\r\nN;ENCODING=QUOTED-PRINTABLE:Pe=C3=B1a;Jo=\r\n=C3=A9\r\nTEL;CELL:\r\n"
               "ADR;HOME;ENCODING=QUOTED-PRINTABLE:;;=31=32 Main=\r\n St;Conroe;TX;77301;\r\nEND:VCARD\r\n", True),
    ("empty-adr", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Only Email\r\nTEL:\r\nADR;TYPE=HOME:;;;;;;\r\nEMAIL:a@b.c\r\nEND:VCARD\r\n", False),
    ("folded", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Folded\r\nitem1.TEL;type=pref:936 555\r\n  0199\r\nTEL;TYPE=WORK:x\r\nEND:VCARD\r\n", True),
    ("lf", "BEGIN:VCARD\nVERSION:3.0\nFN:Unix Lines\nTEL:9365550111\nEND:VCARD\n", True),
    ("short", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Short\r\nTEL:12\r\nEND:VCARD\r\n", False),
]
SRC = "".join(c for _, c, _ in CARDS)
def expected():
    out = []
    for key, c, keep in CARDS:
        if not keep: continue
        if key == "addr21": c = c.replace("TEL;CELL:\r\n", "")
        if key == "folded": c = c.replace("TEL;TYPE=WORK:x\r\n", "")
        out.append(c)
    return "".join(out)
(D / "pb6291.vcf").write_text(SRC, newline=""); (D / "fav6291.vcf").write_text(SRC, newline=""); (D / "cch6291.vcf").write_text(SRC, newline="")
env = lambda exe: {**os.environ, "LD_PRELOAD": str(SO), "HU_VCF_FILTER_EXE": exe}
cat = lambda f, exe="cat": subprocess.run(["cat", str(f)], env=env(exe), capture_output=True, check=True).stdout.decode()

# 1) Hudiy's phonebook as read through the clean-up
got = cat(D / "pb6291.vcf")
assert got == expected(), "\n" + got
assert "Sam Ortiz" not in got and "Only Email" not in got and "TEL;TYPE=CELL:\r\n" not in got and "TEL;CELL:\r\n" not in got
assert "=31=32 Main=\r\n St" in got and "936 555\r\n  0199" in got, "quoted-printable and folded lines kept whole"
assert cat(D / "fav6291.vcf") == expected(), "favorites too"
print("1) left out: no number and no address (blank number, empty address, 2-digit number); blank numbers taken off the rest")

# 2) everything else exactly as it is
assert cat(D / "cch6291.vcf") == SRC, "call history untouched"
assert cat(D / "pb6291.vcf", exe="not-hudiy") == SRC, "other programs read the real file"
assert (D / "pb6291.vcf").read_text(newline="") == SRC, "the file on disk isn't changed"
(T / "pb1.vcf").write_text(SRC, newline=""); assert cat(T / "pb1.vcf") == SRC, "only Hudiy's phonebook folder"
print("2) call history, other programs, other folders and the file itself untouched")

# 3) Python (open / openat, buffered reads, size) and writing still work in the 'Hudiy' process
py = Path(os.path.realpath(sys.executable)).name
code = f"""
import os
p = {str(D / 'pb6291.vcf')!r}
r = open(p, 'rb').read(); fd = os.open(p, os.O_RDONLY); st = os.fstat(fd); os.close(fd)
with open(p, 'r+b') as f: w = f.read()
print(len(r), st.st_size, len(w), r.count(b'BEGIN:VCARD'), repr(os.environ.get('LD_PRELOAD')))
"""
out = subprocess.run([sys.executable, "-c", code], env=env(py), capture_output=True, text=True, check=True).stdout.split()
assert int(out[0]) == int(out[1]) == len(expected().encode()) and int(out[2]) == len(SRC.encode()) and out[3] == "4" and out[4] == "None", out
print("3) Python reads the cleaned copy (size matches); a file opened to write is the real one; LD_PRELOAD dropped for Hudiy's children")

# 4) programs Hudiy's starter runs before Hudiy keep passing it on; nothing breaks without the folder
sh = subprocess.run(["sh", "-c", "echo $LD_PRELOAD"], env=env("hudiy"), capture_output=True, text=True, check=True).stdout.strip()
assert sh == str(SO), sh
(D / "pb0.vcf").write_text(""); assert cat(D / "pb0.vcf") == ""
print("4) the shell that starts Hudiy passes it on; an empty phonebook reads as empty")

# 5) wired in: setup builds it, Hudiy starts through the wrapper, gcc is in the package list, diagnostics show it
setup = (ROOT / "setup.d/90-hudiy.sh").read_text(); wrap = (ROOT / "bin/hu-hudiy-run.sh").read_text()
assert "gcc -O2 -shared -fPIC" in setup and "hu-hudiy-run.sh" in setup and 'exec "$HOME/.hudiy/share/hudiy_run.sh"' in wrap
assert "HUDIY_CONTACTS_FILTER" in wrap and "LD_PRELOAD" in wrap
pk = (ROOT / "config/packages.txt").read_text().split()
assert "gcc" in pk and "libc6-dev" in pk
assert "hu-vcf-filter" in (ROOT / "bin/hu-diag.sh").read_text()
H = "/home/headunit"; WRAP = "/opt/headunit/bin/hu-hudiy-run.sh"
line = next(l for l in setup.splitlines() if l.strip().startswith('sed -i -E "s#^') and "hudiy_run" in l)
(T / "autostart").write_text("/opt/headunit/bin/hu-display-cap.sh\n$HOME/.hudiy/share/hudiy_run.sh\n")
subprocess.run(["bash", "-c", f'H={H}; WRAP={WRAP}; AS={T / "autostart"}; {line.strip()}'], check=True)
assert (T / "autostart").read_text() == f"/opt/headunit/bin/hu-display-cap.sh\n{WRAP}\n", (T / "autostart").read_text()
print("5) setup builds it and switches Hudiy's autostart line to bin/hu-hudiy-run.sh")
print("vcf filter: all tests passed")
