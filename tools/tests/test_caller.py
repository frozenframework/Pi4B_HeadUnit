"""Who's calling, on the map's call banner: the name comes from the phone's own contacts by the exact
number (bin/hu_contacts.py caller), not Hudiy's name, which can belong to someone else (a call from
Alex showed as Sam Ortiz). Numbers only in memory; two contacts with one number show both names;
a number in no contact shows as the number."""
import os, json, re, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
import hu_contacts as HC, hu_hudiy_api as H
from hu_phone import PhoneState
T = Path(tempfile.mkdtemp())

# 1) numbers written any way match
for a in ("+1 (936) 555-0142", "19365550142", "936.555.0142", "tel:+19365550142", "011 1 936 555 0142"):
    assert HC.numbers_key(HC.norm_number(a.removeprefix("tel:"))) == "9365550142", a
assert HC.numbers_key(HC.norm_number("+44 20 7946 0958")) == HC.numbers_key(HC.norm_number("00442079460958"))
assert HC.numbers_key(HC.norm_number("555-0142")) == "5550142"
print("1) +1 (936) 555-0142, 19365550142, 936.555.0142 ... are the same number")

# 2) the phonebook's numbers (vCard 3.0 from an iPhone, 2.1 with quoted-printable names, item1.TEL)
VCF = """BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Sam Ortiz\r\nN:Ortiz;Sam;;;\r\nTEL;TYPE=CELL:(936) 555-0177\r\nEND:VCARD\r
BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alex\r\nN:;Alex;;;\r\nitem1.TEL;type=pref:+1 936-555-0142\r\nTEL;TYPE=HOME:936 555 0100\r\nEND:VCARD\r
BEGIN:VCARD\r\nVERSION:2.1\r\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Pe=C3=B1a;Jos=C3=A9\r\nTEL;CELL:+19365550188\r\nEND:VCARD\r
BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Home\r\nTEL:936-555-0100\r\nEND:VCARD\r\n"""
idx = HC.parse_numbers(VCF)
assert idx["9365550142"] == ["Alex"] and idx["9365550177"] == ["Sam Ortiz"] and idx["9365550188"] == ["José Peña"], idx
assert idx["9365550100"] == ["Alex", "Home"], idx
print("2) every contact's numbers read (vCard 3.0 and 2.1)")

# 3) caller(): the phone's own pull (in memory), else Hudiy's copy of the phonebook
c = HC.Contacts(T / "contacts", T / "none.sqlite", bus=object())
c.hudiy_pb = str(T / "hudiy" / "pb*.vcf")
assert c.caller("+19365550142", "Sam Ortiz") == "Sam Ortiz", "nothing to check against yet: Hudiy's name"
(T / "hudiy").mkdir(); (T / "hudiy" / "pb1234.vcf").write_text(VCF)
assert c.caller("+19365550142", "Sam Ortiz") == "Alex", "Hudiy's copy says the number is Alex's"
c.numbers["AA:BB"] = (time.time() + 5, HC.parse_numbers(VCF.replace("Alex", "Alex Rivera")))
c.connected = ["AA:BB"]
assert c.caller("9365550142", "Sam Ortiz") == "Alex Rivera", "the newer phonebook wins"
assert c.caller("9365550177", "Sam Ortiz") == "Sam Ortiz"
assert c.caller("9365550100", "Home") == "Home", "Hudiy's name kept when it's one of the owners"
assert c.caller("9365550100", "Someone") == "Alex Rivera / Home"
assert c.caller("+1 512 555 0199", "Sam Ortiz") == "", "a number nobody has: no one else's name"
assert c.caller("", "Unknown") == "Unknown" and c.caller("12", "x") == "x"
assert c.shared_numbers() == 1
c.forget("AA:BB"); assert "AA:BB" not in c.numbers
print("3) caller: the exact number's contact (newest phonebook first), both names for a shared number, none for a stranger")

# 4) the pull asks the phone for numbers too, and they never go to disk
src = (ROOT / "bin/hu_contacts.py").read_text()
assert '"Fields": ("as", ["VERSION", "FN", "N", "TEL", "ADR"])' in src
class Bus:
    def pull_phonebook(self, address, progress=None): return VCF
c2 = HC.Contacts(T / "c2", T / "none.sqlite", bus=Bus())
c2.refresh({"address": "11:22", "name": "Phone"})
assert c2.numbers["11:22"][1]["9365550142"] == ["Alex"]
saved = (T / "c2").glob("*.json"); text = "".join(f.read_text() for f in saved)
assert "555" not in text and "0142" not in text, "no phone numbers saved"
print("4) the phonebook pull includes numbers; kept in memory only")

# 5) the map's call banner (Hudiy's call status -> PhoneState)
def v(n):
    o = bytearray()
    while True:
        b = n & 0x7F; n >>= 7; o.append(b | (0x80 if n else 0))
        if not n: return bytes(o)
def fi(num, x): return v(num << 3) + v(x)
def fb(num, b): b = b.encode(); return v((num << 3) | 2) + v(len(b)) + b
asked = []
def caller(n, g): asked.append(n); return c.caller(n, g)
c.numbers["AA:BB"] = (time.time() + 5, HC.parse_numbers(VCF)); c.connected = ["AA:BB"]
p = PhoneState(lambda a: True, lambda: "map", caller=caller)
p.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 1) + fb(2, "+19365550142") + fb(3, "Sam Ortiz"))
assert p.call == {"state": "incoming", "number": "+19365550142", "name": "Alex"}, p.call
p.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 3) + fb(2, "+19365550142") + fb(3, "Sam Ortiz"))
assert p.call["state"] == "active" and p.call["name"] == "Alex" and len(asked) == 1, (p.call, asked)
p.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 1) + fb(2, "+15125550199") + fb(3, "Sam Ortiz"))
assert p.call["name"] == "", p.call
p.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 0) + fb(2, "") + fb(3, ""))
assert p.call["state"] is None
def broken(n, g): raise RuntimeError("boom")
p2 = PhoneState(lambda a: True, lambda: "map", caller=broken)
p2.handle(H.PHONE_VOICE_CALL_STATUS, fi(1, 1) + fb(2, "123456") + fb(3, "Hudiy Name")); assert p2.call["name"] == "Hudiy Name"
print("5) call banner: Alex (not Hudiy's Sam Ortiz), looked up once per call; a stranger shows as the number")

# 6) a number with no contact is written the usual way on the banner
nav = (ROOT / "apps/nav/nav.js").read_text()
fn = re.search(r"function phoneNumber\(n\) \{.*?\n\}", nav, re.S).group(0)
out = subprocess.run(["node", "-e", fn + "\nconsole.log(JSON.stringify(['+15125550199','5125550199','+442079460958','',null].map(phoneNumber)))"],
                     capture_output=True, text=True, check=True).stdout
assert json.loads(out) == ["(512) 555-0199", "(512) 555-0199", "+442079460958", "", ""], out
assert "const who = c.name || phoneNumber(c.number)" in nav
en = json.loads((ROOT / "apps/common/i18n.json").read_text())["en"]
assert "{n}" in en["diag.shared_numbers"] and "shared_numbers" in (ROOT / "apps/common/hu.js").read_text()
print("6) banner: (512) 555-0199 for a stranger; Settings > Developer > Check counts numbers on two contacts")
print("caller: all tests passed")
