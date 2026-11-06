"""Places (address book): one Home / Work / Gym / School each, saved favorites without duplicates, old
favorites carried over, recent destinations, places you go often learned from arrivals and where the
car was parked (not offered once saved), history that can be cleared and ages out."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
from hu_places import Places, dist_m

T = Path(tempfile.mkdtemp())
(T / "favorites.json").write_text(json.dumps([{"name": "Joe's Tacos", "lnglat": [-95.45, 30.31]}, {"name": "", "lnglat": [0, 0]}]))
class Clock:
    t = 1_800_000_000.0
    def __call__(self): return self.t
clock = Clock(); P = Places(T, clock=clock)
assert [p["name"] for p in P.places()] == ["Joe's Tacos"] and P.places()[0]["label"] == "favorite"
print("1) old favorites carried over into Places")

HOME, GYM, WORK = [-95.4561, 30.3119], [-95.4700, 30.3300], [-95.3600, 30.1600]
P.save("home", "Home", HOME); P.save("home", "New home", [-95.40, 30.30])
assert [p["name"] for p in P.places() if p["label"] == "home"] == ["New home"], "only one Home"
P.save("favorite", "Gas", [-95.5, 30.4]); P.save("favorite", "Gas station", [-95.50001, 30.40001])
assert len([p for p in P.places() if p["label"] == "favorite"]) == 2, "same spot saved once (+ Joe's)"
P.save("evil", "x", [1, 2]); assert P.places()[0]["label"] == "favorite"
for bad in ([999, 0], "nope", None):
    try: P.save("work", "W", bad); raise SystemExit("bad position accepted")
    except ValueError: pass
pid = P.places()[0]["id"]; P.rename(pid, "Fuel"); assert P.places()[0]["name"] == "Fuel"
assert P.remove(pid) and not any(p["id"] == pid for p in P.places())
print("2) one Home/Work/Gym/School each, no duplicate favorites, bad input refused, rename/remove")

# a few weeks of driving: gym 4x by route, work 3x just parked there (never navigated), a one-off
for i in range(4):
    clock.t += 86400; P.record("route", GYM, "Iron Gym"); P.record("arrive", [GYM[0] + 0.0004, GYM[1] - 0.0003], "Iron Gym")
for i in range(3):
    clock.t += 86400; P.record("parked", [WORK[0] + i * 0.0002, WORK[1]])
P.record("parked", [WORK[0], WORK[1]])            # same stay again (minutes later): not counted twice
clock.t += 3600; P.record("route", [-96.0, 31.0], "Grandma's"); P.record("arrive", [-96.0, 31.0], "Grandma's")
fr = P.frequent()
assert [f["visits"] for f in fr] == [4, 3], fr
assert fr[0]["name"] == "Iron Gym" and dist_m(fr[0]["lnglat"], GYM) < 60 and fr[1]["name"] == ""
assert [r["name"] for r in P.recent()] == ["Grandma's", "Iron Gym"]
print("3) learned: gym (4 arrivals) and work (3 parked starts) offered; recent destinations listed once each")

P.save("gym", "Iron Gym", fr[0]["lnglat"])
assert [f["visits"] for f in P.frequent()] == [3], "a saved place isn't offered again"
clock.t += 200 * 86400
assert P.frequent() == [] and P.recent() == [], "history older than 120 days ages out"
clock.t -= 200 * 86400; P.clear_history(); assert P.history() == []
assert any(p["label"] == "gym" for p in P.places()), "clearing history keeps saved places"
print("4) saved places drop out of suggestions; history ages out and can be cleared, saved places stay")

# 5) starred contacts (from the Address book): at most 3, follow the phone's address, Forget clears them
PH = "AA:BB:CC:DD:EE:FF"
sean = P.favorite_contact(PH, "Sean", "home", "140 N Main St, Conroe TX 77301", [-95.456, 30.312], "Robert's iPhone")
P.favorite_contact(PH, "Amy", "home", "1 Elm St", [-95.40, 30.30]); P.favorite_contact(PH, "Joe", "work", "2 Oak St", [-95.41, 30.31])
try: P.favorite_contact(PH, "Dana", "home", "3 Pine St", [-95.42, 30.32]); raise AssertionError("a 4th")
except ValueError as e: assert str(e) == "limit"
again = P.favorite_contact(PH, "sean", "work", "4500 SH 105 W", [-95.50, 30.33])           # the other address: same entry
assert again["id"] == sean["id"] and len(P.fav_contacts()) == 3 and again["ctype"] == "work"
P.favorite_contact(PH, "Sean", "home", "140 N Main St, Conroe TX 77301", [-95.456, 30.312])
phone = [{"name": "Sean", "addresses": [{"label": "home", "text": "200 N Main St, Conroe TX 77301", "lon": -95.457, "lat": 30.315},
                                        {"label": "work", "text": "4500 SH 105 W", "lon": -95.50, "lat": 30.33}]},
         {"name": "Amy", "addresses": [{"label": "home", "text": "1 Elm St", "lon": -95.40, "lat": 30.30}]},
         {"name": "Joe", "addresses": [{"label": "home", "text": "9 Ash St", "lon": -95.43, "lat": 30.29}]}]       # Joe has no work address now
assert P.update_contacts(PH, phone) == 2, "Sean moved; Joe's only address on the phone is now his home"
s2 = next(p for p in P.places() if p["name"] == "Sean")
assert s2["id"] == sean["id"] and s2["lnglat"] == [-95.457, 30.315] and s2["sub"].startswith("200 N Main") and s2["ctype"] == "home"
assert next(p for p in P.places() if p["name"] == "Joe")["sub"] == "9 Ash St"
assert P.update_contacts(PH, phone) == 0 and P.update_contacts("11:22:33:44:55:66", phone) == 0, "another phone's Sean isn't ours"
assert P.update_contacts(PH, []) == 0 and len(P.fav_contacts()) == 3, "deleted on the phone: stays starred as it was"
P.remove(s2["id"]); assert len(P.fav_contacts()) == 2
P.favorite_contact(PH, "Dana", "home", "3 Pine St", [-95.42, 30.32]); assert len(P.fav_contacts()) == 3, "room again after one is taken off"
for i in range(205): clock.t += 1; P.save("favorite", f"Spot {i}", [-97 + i * 0.01, 31.0])
assert sum(p["label"] == "favorite" for p in P.places()) == 200 and len(P.fav_contacts()) == 3, "your 200 places and 3 contacts don't push each other out"
assert P.remove_contacts(PH) == 3 and not P.fav_contacts()
# an older version put every contact in Places: those go, starred ones stay
import json as _j
pf = T / "places.json"; ps = _j.loads(pf.read_text())
ps += [{"id": "x1", "label": "contact", "name": "Bulk", "lnglat": [-95, 30], "src": PH}, {"id": "x2", "label": "contact", "name": "Star", "lnglat": [-95, 30], "fav": True}]
pf.write_text(_j.dumps(ps)); P2 = Places(T, clock=clock)
assert [p["name"] for p in P2.fav_contacts()] == ["Star"]
print("5) starred contacts: at most 3, replaced by the phone's new address, Forget clears; old bulk-imported ones removed")
print("places: all tests passed")
