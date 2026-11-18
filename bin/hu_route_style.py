"""
Route styles: "fastest" (Valhalla's own choice) and "manual" - fewer stops, for a stick shift: fewer
traffic lights, fewer stops where a small road meets a bigger one, fewer turns (left turns most),
even when the drive is longer.

How "manual" picks a route:
  1. candidates: Valhalla's fastest route and its alternatives, plus routes asked for with turns made
     expensive and highways preferred (MANUAL_COSTING), each with their alternatives;
  2. each candidate is walked edge by edge (Valhalla trace_attributes): traffic lights are in the map
     data; a likely stop is where the route enters or crosses a more important road without a light
     (stop signs aren't in Valhalla's data, but that's where they are); turns come from the directions;
  3. the one with the lowest "time + stops" score wins, as long as it takes at most MAX_EXTRA longer
     than the fastest.
"""
import json

MANUAL_COSTING = {"maneuver_penalty": 30, "use_highways": 1.0, "use_living_streets": 0.0, "service_penalty": 60}
WEIGHTS = {"signal": 25, "stop": 20, "left": 14, "right": 8, "uturn": 30}     # seconds each, on top of the drive time
MAX_EXTRA = 0.5                                                                 # at most 50 % longer than the fastest
RANK = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3, "tertiary": 4, "unclassified": 5,
        "residential": 6, "service_other": 7}
TURNS = {10: "right", 11: "right", 12: "uturn", 13: "uturn", 14: "left", 15: "left"}   # Valhalla maneuver types
ATTRS = ["edge.road_class", "edge.use", "edge.roundabout", "node.traffic_signal", "node.intersecting_edge.road_class"]

def count(edges, maneuvers):
    """{signal, stop, left, right, uturn} along a route: edges from trace_attributes, maneuvers from the route."""
    c = {"signal": 0, "stop": 0, "left": 0, "right": 0, "uturn": 0}
    for i, e in enumerate(edges[:-1]):                   # (the last node is the destination)
        node = e.get("end_node") or {}
        if node.get("traffic_signal"):
            c["signal"] += 1; continue
        nxt = edges[i + 1]
        if e.get("use") == "ramp" or nxt.get("use") == "ramp" or e.get("roundabout") or nxt.get("roundabout"):
            continue                                     # merging from a ramp, roundabouts: no full stop
        inc, out = RANK.get(e.get("road_class"), 7), RANK.get(nxt.get("road_class"), 7)
        cross = min((RANK.get(x.get("road_class"), 7) for x in node.get("intersecting_edges", [])), default=9)
        if out < inc or cross < min(inc, out):           # onto a bigger road, or across one: most likely a stop
            c["stop"] += 1
    for m in maneuvers:
        k = TURNS.get(m.get("type"))
        if k: c[k] += 1
    return c

def score(time_s, c, w=WEIGHTS):
    return time_s + sum(w[k] * c[k] for k in w)

def stops(c): return c["signal"] + c["stop"]

def plan(actor, req, quick=False):
    """The fewer-stops route for a Valhalla route request (two locations). Returns (trip, info):
    info = {stops, signals, turns, fastest: {time, stops}} for the screen."""
    def ask(costing_options, alternates):
        r = json.loads(actor.route(json.dumps({**req, "costing_options": {"auto": costing_options} if costing_options else {},
                                                "alternates": alternates})))
        return [r["trip"]] + [a["trip"] for a in r.get("alternates", [])]
    trips = []
    if not quick: trips += ask(None, 2)                  # the fastest first (a reroute while driving skips it)
    trips += ask(MANUAL_COSTING, 1 if quick else 2)
    seen, cands = set(), []
    for t in trips:
        key = (round(t["summary"]["length"], 2), round(t["summary"]["time"]))
        if key in seen: continue
        seen.add(key)
        leg = t["legs"][0]
        try:
            ta = json.loads(actor.trace_attributes(json.dumps({"encoded_polyline": leg["shape"], "shape_format": "polyline6", "costing": "auto",
                                                               "shape_match": "edge_walk", "filters": {"attributes": ATTRS, "action": "include"}})))
            edges = ta.get("edges", [])
        except Exception:
            edges = []
        cands.append((t, count(edges, leg["maneuvers"])))
    fastest = cands[0]
    limit = fastest[0]["summary"]["time"] * (1 + MAX_EXTRA)
    best = min((c for c in cands if c[0]["summary"]["time"] <= limit), key=lambda c: score(c[0]["summary"]["time"], c[1]))
    t, c = best
    return t, {"stops": stops(c), "signals": c["signal"], "turns": c["left"] + c["right"] + c["uturn"],
               "fastest": {"time": round(fastest[0]["summary"]["time"]), "stops": stops(fastest[1])} if not quick else None,
               "candidates": len(cands)}
