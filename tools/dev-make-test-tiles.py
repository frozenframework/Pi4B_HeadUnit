#!/usr/bin/env python3
"""DEV ONLY: builds a tiny Protomaps-schema PMTiles file from a sample OSM extract so the
map themes can be rendered and tested without downloading real basemaps."""
import sys, gzip, math, osmium, mercantile, mapbox_vector_tile
from shapely.geometry import LineString, Polygon, Point, box, mapping
from shapely.ops import transform
from shapely import wkb as swkb
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression

src, out = sys.argv[1], sys.argv[2]
fab = osmium.geom.WKBFactory()
feats = {"earth": [], "water": [], "landuse": [], "roads": [], "buildings": [], "pois": [], "places": []}
HW = {"motorway": "highway", "trunk": "highway", "primary": "major_road", "secondary": "major_road", "tertiary": "major_road",
      "residential": "minor_road", "unclassified": "minor_road", "living_street": "minor_road", "service": "minor_road",
      "footway": "path", "path": "path", "cycleway": "path", "steps": "path", "pedestrian": "path", "track": "path"}
LU = {"park": "park", "grass": "grass", "scrub": "scrub", "wood": "wood", "forest": "forest", "playground": "playground",
      "commercial": "commercial", "residential": "residential", "industrial": "industrial", "railway": "industrial", "pitch": "park"}

class H(osmium.SimpleHandler):
    def way(self, w):
        t = w.tags
        try:
            if "highway" in t and t["highway"].replace("_link", "") in HW:
                base = t["highway"].replace("_link", "")
                g = swkb.loads(fab.create_linestring(w), hex=True)
                p = {"kind": HW[base], "min_zoom": 12}
                if base == "service": p["kind_detail"] = "service"
                if t["highway"].endswith("_link"): p["is_link"] = True
                if t.get("bridge") == "yes": p["is_bridge"] = True
                if t.get("tunnel") == "yes": p["is_tunnel"] = True
                if t.get("oneway") == "yes": p["oneway"] = "yes"
                if "name" in t: p["name"] = t["name"]
                feats["roads"].append((g, p))
            elif t.get("railway") in ("rail", "tram", "subway"):
                feats["roads"].append((swkb.loads(fab.create_linestring(w), hex=True), {"kind": "rail", "kind_detail": t["railway"]}))
        except Exception:
            pass
    def area(self, a):
        t = a.tags
        try:
            g = swkb.loads(fab.create_multipolygon(a), hex=True)
        except Exception:
            return
        if "building" in t:
            feats["buildings"].append((g, {"kind": "building", "height": float((t.get("building:levels", "3") or "3").split(";")[0]) * 3.2}))
        elif t.get("natural") == "water" or t.get("landuse") in ("basin", "pond") or t.get("water"):
            feats["water"].append((g, {"kind": "water", **({"name": t["name"]} if "name" in t else {})}))
        else:
            k = t.get("leisure") or t.get("landuse") or t.get("natural")
            if k in LU:
                feats["landuse"].append((g, {"kind": LU[k]}))
    def node(self, n):
        t = n.tags
        if "name" in t and ("amenity" in t or "shop" in t or "tourism" in t):
            kind = t.get("amenity") or t.get("tourism") or "shop"
            feats["pois"].append((Point(n.location.lon, n.location.lat), {"kind": kind, "name": t["name"], "min_zoom": 15}))
        if t.get("place") in ("neighbourhood", "suburb", "quarter"):
            feats["places"].append((Point(n.location.lon, n.location.lat), {"kind": "neighbourhood", "name": t.get("name", ""), "min_zoom": 12}))

H().apply_file(src, locations=True)
allg = [g for L in feats.values() for g, _ in L]
minx = min(g.bounds[0] for g in allg); miny = min(g.bounds[1] for g in allg)
maxx = max(g.bounds[2] for g in allg); maxy = max(g.bounds[3] for g in allg)
feats["earth"].append((box(minx - 0.02, miny - 0.02, maxx + 0.02, maxy + 0.02), {"kind": "earth"}))

def merc(x, y):
    X = x * 20037508.34 / 180; Y = math.log(math.tan((90 + y) * math.pi / 360)) * 20037508.34 / math.pi
    return X, Y

mfeats = {k: [(transform(lambda x, y, z=None: merc(x, y), g), p) for g, p in v] for k, v in feats.items()}
EXT = 4096
with open(out, "wb") as f:
    w = Writer(f)
    count = 0
    for z in range(12, 17):
        for tile in sorted(mercantile.tiles(minx - 0.02, miny - 0.02, maxx + 0.02, maxy + 0.02, z), key=lambda t: zxy_to_tileid(t.z, t.x, t.y)):
            b = mercantile.xy_bounds(tile)
            buf = (b.right - b.left) * 0.06
            clipbox = box(b.left - buf, b.bottom - buf, b.right + buf, b.top + buf)
            layers = []
            for name, L in mfeats.items():
                out_f = []
                for g, p in L:
                    if not g.intersects(clipbox): continue
                    if name in ("pois",) and z < 15: continue
                    gg = g.intersection(clipbox)
                    if gg.is_empty: continue
                    gg = transform(lambda x, y, z=None: ((x - b.left) / (b.right - b.left) * EXT, (y - b.bottom) / (b.top - b.bottom) * EXT), gg)
                    out_f.append({"geometry": gg.wkt, "properties": p})
                if out_f: layers.append({"name": name, "features": out_f})
            if not layers: continue
            data = mapbox_vector_tile.encode(layers, default_options={"extents": EXT, "y_coord_down": False})
            w.write_tile(zxy_to_tileid(tile.z, tile.x, tile.y), gzip.compress(data))
            count += 1
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP, "min_zoom": 12, "max_zoom": 16,
                "min_lon_e7": int(minx * 1e7), "min_lat_e7": int(miny * 1e7), "max_lon_e7": int(maxx * 1e7), "max_lat_e7": int(maxy * 1e7),
                "center_zoom": 15, "center_lon_e7": int(cx * 1e7), "center_lat_e7": int(cy * 1e7)},
               {"vector_layers": [{"id": k, "fields": {}} for k in feats], "attribution": "OpenStreetMap contributors (test extract)"})
print("tiles", count, "center", cx, cy)
