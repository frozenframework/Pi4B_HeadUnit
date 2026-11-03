"""Screen text: every phrase the apps use exists in English, every language has every phrase with the
same {placeholders}, the turn phrases exist for languages the router can't write, and the bridge's
language table matches the apps' list."""
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
D = json.loads((ROOT / "apps/common/i18n.json").read_text())
en = D["en"]
used = set()
for f in (ROOT / "apps").glob("*/*.html"): used |= set(re.findall(r'data-i18n(?:-aria)?="([a-z_.]+)"', f.read_text()))
for f in (ROOT / "apps").glob("*/*.js"): used |= set(re.findall(r"\bt\('([a-z_.]+)'", f.read_text()))
assert not {u for u in used if not u.startswith("m.")} - set(en), "missing in en: %s" % sorted({u for u in used if not u.startswith("m.")} - set(en))
apps_langs = re.findall(r"code: '(\w+)'", (ROOT / "apps/common/i18n.js").read_text())
nav = (ROOT / "apps/nav/nav.js").read_text()
man_keys = set(re.findall(r"\d+: '(\w+)'", nav[nav.index("const MAN_KEY"):nav.index("function phrase")])) | {"onto"}
bridge = (ROOT / "bin/hu-bridge.py").read_text()
table = dict(re.findall(r'"(\w\w)": \{"valhalla": (None|"[\w-]+")', bridge))
assert set(table) == set(apps_langs) == set(D) - {"_comment"}, (sorted(table), apps_langs)
ph = lambda s: sorted(re.findall(r"\{\w+\}", s))
for code in apps_langs:
    d = D[code]
    miss = [k for k in en if k not in d]
    assert not miss, f"{code} is missing {miss}"
    bad = [k for k in en if ph(en[k]) != ph(d[k])]
    assert not bad, f"{code}: placeholders differ in {bad}"
    if table[code] == "None":
        need = {"m." + k for k in man_keys}
        assert need <= set(d), f"{code} needs turn phrases {sorted(need - set(d))}"
    elif code != "en":
        assert not any(k.startswith("m.") for k in d), f"{code} has m.* phrases but the router writes its turns"
assert not any(k.startswith("m.") for k in en), "en must not have m.* (the router writes English turns)"
system = (ROOT / "bin/hu-system.sh").read_text()
assert all(re.search(rf"\b{c}\) echo \w+\.UTF-8", system) for c in apps_langs), "hu-system.sh locale table"
print(f"i18n: {len(apps_langs)} languages x {len(en)} phrases complete; turn phrases for", [c for c in apps_langs if table[c] == 'None'])
