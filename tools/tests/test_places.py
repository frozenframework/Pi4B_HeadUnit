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
print("places: all tests passed")
