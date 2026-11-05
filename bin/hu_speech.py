"""
Make English directions sound like a person said them (used by the voice packs, bin/hu_voice.py):

  "In 0.5 miles, Turn left onto N Main St."  ->  "In half a mile, turn left onto North Main Street."
  "Take exit 87B toward I-45 S/US-59."         ->  "Take exit 87 B toward Interstate 45 South, U.S. 59."

- distances the way people say them (half a mile, a quarter mile, a mile and a half, about 2 miles)
- road abbreviations read out (St, Ave, Rd, Dr, Blvd, Hwy, Pkwy, Ln, Ct, Jr, N/S/E/W...)
- highway names (I-45, US 59, TX-105, SH 6, FM 1488, CR 123, Loop 336, Spur, Beltway 8)
- one capital letter per sentence, sensible punctuation, no doubled spaces or dots
- optional per-pack wording variety ("phrases": {"Turn left": ["Turn left", "Take a left"]})
"""
import random, re

STREET = {"St": "Street", "Ave": "Avenue", "Av": "Avenue", "Rd": "Road", "Dr": "Drive", "Blvd": "Boulevard",
          "Hwy": "Highway", "Pkwy": "Parkway", "Ln": "Lane", "Ct": "Court", "Pl": "Place", "Cir": "Circle",
          "Trl": "Trail", "Fwy": "Freeway", "Expy": "Expressway", "Tpke": "Turnpike", "Ter": "Terrace",
          "Sq": "Square", "Xing": "Crossing", "Bnd": "Bend", "Crk": "Creek", "Mtn": "Mountain", "Ft": "Fort",
          "Jr": "Junior", "Sr": "Senior", "Mt": "Mount", "Hts": "Heights", "Pt": "Point", "Ctr": "Center"}
DIRS = {"N": "North", "S": "South", "E": "East", "W": "West", "NE": "Northeast", "NW": "Northwest",
        "SE": "Southeast", "SW": "Southwest"}
STATES = {"TX": "Texas", "LA": "Louisiana", "OK": "Oklahoma", "NM": "New Mexico", "AR": "Arkansas", "CA": "California",
          "FL": "Florida", "GA": "Georgia", "AZ": "Arizona", "CO": "Colorado"}

def _num(x):
    s = f"{x:.1f}".rstrip("0").rstrip(".")
    return s

def say_distance(value, unit):
    """0.5 miles -> half a mile; 1.5 miles -> a mile and a half; 2.3 miles -> about 2 and a half miles..."""
    v = float(value); u = unit.lower()
    if u.startswith("mi"):
        if v < 0.2: return f"{_num(v)} miles"
        if v < 0.32: return "a quarter mile"
        if v < 0.4: return "a third of a mile"
        if v < 0.62: return "half a mile"
        if v < 0.85: return "three quarters of a mile"
        if v < 1.15: return "a mile"
        if v < 1.35: return "about a mile and a quarter"
        if v < 1.65: return "a mile and a half"
        if v < 1.9: return "almost 2 miles"
        if v < 10:
            whole, frac = int(v), v - int(v)
            if frac < 0.2: return f"about {whole} miles" if frac > 0.05 else f"{whole} miles"
            if frac < 0.65: return f"{whole} and a half miles"
            return f"almost {whole + 1} miles"
        return f"{round(v)} miles"
    if u.startswith("k"):
        if 0.4 <= v < 0.62: return "half a kilometer"
        if 0.9 <= v < 1.15: return "a kilometer"
        if v >= 10: return f"{round(v)} kilometers"
        return f"{_num(v)} kilometers"
    if u.startswith("f"):
        return f"{int(round(v / 50.0) * 50) or 50} feet"
    if u.startswith("m"):
        return f"{int(round(v / 10.0) * 10) or 10} meters"
    return f"{value} {unit}"

_DIST = re.compile(r"\b(\d+(?:\.\d+)?)\s*(miles?|mi|kilometers?|km|feet|ft|meters?|m)\b(?!\w)")
_HWY = [
    (re.compile(r"\bI-?\s?(\d+[A-Z]?)\b"), lambda m: f"Interstate {m.group(1)}"),
    (re.compile(r"\bUS[- ]?(\d+[A-Z]?)\b"), lambda m: f"U.S. {m.group(1)}"),
    (re.compile(r"\b(?:SH|State Highway)[- ]?(\d+)\b"), lambda m: f"State Highway {m.group(1)}"),
    (re.compile(r"\bFM[- ]?(\d+)\b"), lambda m: f"F.M. {m.group(1)}"),
    (re.compile(r"\bRM[- ]?(\d+)\b"), lambda m: f"Ranch Road {m.group(1)}"),
    (re.compile(r"\bCR[- ]?(\d+)\b"), lambda m: f"County Road {m.group(1)}"),
    (re.compile(r"\bBW[- ]?(\d+)\b"), lambda m: f"Beltway {m.group(1)}"),
    (re.compile(r"\b(" + "|".join(STATES) + r")-(\d+)\b"), lambda m: f"{STATES[m.group(1)]} {m.group(2)}"),
]

def humanize(text, phrases=None, rng=random):
    if not text: return text
    t = " ".join(str(text).split())
    # highway names before the generic abbreviations (I-45 S -> Interstate 45 South)
    for rx, fn in _HWY: t = rx.sub(fn, t)
    # exits: 87B -> 87 B
    t = re.sub(r"\b([Ee]xit\s+\d+)([A-Z])\b", r"\1 \2", t)
    # directions written as single capitals around road names: N Main St, I-45 S
    t = re.sub(r"(?<![\w.'-])(NE|NW|SE|SW|N|S|E|W)\.?(?=\s+[A-Z0-9])", lambda m: DIRS[m.group(1)], t)
    t = re.sub(r"(?<=\d)\s+(NE|NW|SE|SW|N|S|E|W)\b(?!\.\w)", lambda m: " " + DIRS[m.group(1)], t)
    t = re.sub(r"(?<=[a-z])\s+(NE|NW|SE|SW|N|S|E|W)\b(?=[\s,.;/]|$)", lambda m: " " + DIRS[m.group(1)], t)
    # St / Dr / Mt / Ft before a name are Saint / Doctor / Mount / Fort, otherwise Street / Drive
    # (only where a name starts: after onto / on / to / toward / at / via / a comma, or at the start)
    lead = r"((?:^|\b(?:onto|on|to|toward|towards|at|via|and|then)\s+|,\s+))"
    t = re.sub(lead + r"St\.?(?=\s+[A-Z][a-z])", r"\1Saint", t)
    t = re.sub(lead + r"Dr\.?(?=\s+[A-Z][a-z])", r"\1Doctor", t)
    def street(m):                                   # keep a dot that also ends the sentence ("Main St. Then ...")
        dot = m.group(2) and re.match(r"\s+[A-Z]|\s*$", t[m.end():])
        return STREET[m.group(1)] + ("." if dot else "")
    t = re.sub(r"\b(" + "|".join(sorted(STREET, key=len, reverse=True)) + r")\b(\.?)", street, t)
    # slashes between road names read as a pause
    t = re.sub(r"\s*/\s*", ", ", t)
    # distances the way people say them
    t = _DIST.sub(lambda m: say_distance(m.group(1), m.group(2)), t)
    # per-pack wording variety at the start of a sentence or after "then"
    for src, alts in (phrases or {}).items():
        if not alts: continue
        t = re.sub(r"(^|[.!?]\s+|,\s+then\s+)" + re.escape(src) + r"\b",
                   lambda m: m.group(1) + (rng.choice(alts) if not m.group(1).strip().endswith("then") else rng.choice(alts).lower()), t)
    # "In half a mile, Turn left" -> "In half a mile, turn left"
    t = re.sub(r"(,\s+(?:then\s+|and\s+)?|\b[Tt]hen\s+)([A-Z])([a-z]+\b)", lambda m: m.group(1) + (m.group(2).lower() if m.group(2) + m.group(3) in
               ("Turn", "Keep", "Take", "Bear", "Continue", "Make", "Merge", "Stay", "Head", "Go", "Enter", "Exit", "Drive",
                "Follow", "You", "Your", "Then", "At", "Use", "Hang", "Slight", "Sharp") else m.group(2)) + m.group(3), t)
    t = re.sub(r"\s+([,.!?])", r"\1", t); t = re.sub(r"([,.!?])\1+", r"\1", t); t = re.sub(r",\.", ".", t)
    t = t.strip()
    if t and t[-1] not in ".!?": t += "."
    return t[0].upper() + t[1:] if t else t
