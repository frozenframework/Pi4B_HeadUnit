"""
Dimensioned drawings of the screen mount, for checking, reverse engineering and changes.

    python make_drawings.py                 all three profiles
    python make_drawings.py 2din-7in        one profile

For each profile (cad/drawings/<profile>/):
    <profile>_drawings.pdf          A3 sheets: stack-up + parameters, then each part (third-angle projection)
    dxf/<part>.dxf                  every view of the part at 1:1 in mm, with real (editable) dimensions,
                                    for importing into Inventor / Fusion / FreeCAD and sketching over

Everything comes from the same model as the STEP/STL files (headunit_mount.py):
  - the views are the solids projected with hidden-line removal (no hand drawing),
  - dimensions are taken from the model's own numbers,
  - the hole tables are read back from the solids' geometry (every round hole and boss found in the part),
so a change to headunit_mount.py followed by running this script gives matching drawings.
Units: mm. Datums: every part's X/Y zero is its center; Z zero is its front face (the face toward the
driver), Z positive going back into the dash. The Pi tray's Z zero is its underside.
"""
import datetime, math, os, runpy, sys
from pathlib import Path
import numpy as np
import cadquery as cq
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Polygon, PathPatch, Rectangle
from matplotlib.path import Path as MPath
import ezdxf
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Line
from OCP.GCPnts import GCPnts_QuasiUniformDeflection

HERE = Path(__file__).resolve().parent
PAPER_W, PAPER_H = 420.0, 297.0           # A3 landscape
TXT = 2.6                                 # text height on paper, mm
SCALES = [(1, 1), (1, 1.5), (1, 2), (1, 2.5), (1, 3), (1, 4)]
DESC = {"1din-waveshare79": 'Waveshare 7.9" 1280x400 bar screen, single-DIN opening (1997 F-250, 1997 Miata)',
        "2din-7in": '7" 1024x600 Orient Display AFY1024600A2, double-DIN opening (2017 Frontier, tier 3)',
        "2din-pitouch2": 'Raspberry Pi Touch Display 2 (7", DSI), double-DIN opening (2017 Frontier, tiers 1-2)'}

# ============================================================================ geometry helpers
def unit(v):
    v = np.asarray(v, float); return v / np.linalg.norm(v)

class Proj:
    """A view direction: n points from the part toward the viewer, xd is the paper's right."""
    def __init__(self, n, xd):
        self.n, self.xd = unit(n), unit(xd); self.yd = np.cross(self.n, self.xd)
    def uv(self, p):
        p = np.asarray(p, float); return np.array([p @ self.xd, p @ self.yd])

def iso_proj(n, up):
    n = unit(n); xd = unit(np.cross(up, n)); return Proj(n, xd)

def _polyline(edge):
    c = BRepAdaptor_Curve(edge)
    a, b = c.FirstParameter(), c.LastParameter()
    if c.GetType() == GeomAbs_Line:
        pts = [c.Value(a), c.Value(b)]
    else:
        d = GCPnts_QuasiUniformDeflection(c, 0.01)
        pts = [d.Value(i) for i in range(1, d.NbPoints() + 1)] if d.IsDone() else [c.Value(a + (b - a) * t) for t in np.linspace(0, 1, 40)]
    return np.array([[p.X(), p.Y()] for p in pts])

def _edges(shape):
    out = []
    if shape is None or shape.IsNull(): return out
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        try: out.append(_polyline(TopoDS.Edge_s(ex.Current())))
        except Exception: pass
        ex.Next()
    return out

def hlr(shape, proj, hidden=True):
    """Visible / tangent / hidden 2-D polylines of a shape seen along proj (paper u, v in model mm)."""
    algo = HLRBRep_Algo(); algo.Add(shape.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*proj.n), gp_Dir(*proj.xd))))
    algo.Update(); algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    vis = _edges(h.VCompound()) + _edges(h.OutLineVCompound())
    tan = _edges(h.Rg1LineVCompound())
    hid = (_edges(h.HCompound()) + _edges(h.OutLineHCompound())) if hidden else []
    return vis, tan, hid

def section_faces(shape, origin, normal, proj):
    """Hatched areas where the plane cuts the shape: list of [outer, *holes] polygons in (u, v)."""
    plane = cq.Face.makePlane(4000, 4000, cq.Vector(*origin), cq.Vector(*normal))
    cut = shape.intersect(plane)
    polys = []
    for f in cut.Faces():
        loops = []
        for w in [f.outerWire()] + f.innerWires():
            pts = []
            for e in w.Edges():
                seg = [proj.uv(v.toTuple()) for v in (e.positions(np.linspace(0, 1, 2 if e.geomType() == "LINE" else 24)))]
                pts += seg
            loops.append(np.array(pts))
        polys.append(loops)
    return polys

def keep_half(shape, origin, normal):
    """The part of shape on the +normal side of the plane (what a section view looks at)."""
    big = 2000.0
    box = cq.Solid.makeBox(big, big, big, cq.Vector(-big / 2, -big / 2, 0))
    n = unit(normal)
    # rotate the box so its +Z faces along normal, then move it to the plane
    z = np.array([0, 0, 1.0])
    axis = np.cross(z, n); ang = math.degrees(math.acos(max(-1, min(1, z @ n))))
    if np.linalg.norm(axis) > 1e-9: box = box.rotate(cq.Vector(0, 0, 0), cq.Vector(*axis), ang)
    elif ang > 90: box = box.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 180)
    box = box.translate(cq.Vector(*origin))
    return shape.intersect(box)

def holes(solid):
    """Every full round hole / boss in a solid, read back from its cylindrical faces."""
    groups = {}
    for f in solid.Faces():
        if f.geomType() != "CYLINDER": continue
        s = BRepAdaptor_Surface(f.wrapped)
        cyl = s.Cylinder(); ax = cyl.Axis(); r = cyl.Radius()
        d = np.array([ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()])
        if d[np.argmax(np.abs(d))] < 0: d = -d
        o = np.array([ax.Location().X(), ax.Location().Y(), ax.Location().Z()])
        u0, u1, v0, v1 = s.FirstUParameter(), s.LastUParameter(), s.FirstVParameter(), s.LastVParameter()
        sgn = 1 if np.allclose(d, [ax.Direction().X(), ax.Direction().Y(), ax.Direction().Z()]) else -1
        a0, a1 = sorted([o @ d + sgn * v0, o @ d + sgn * v1])
        c = o - (o @ d) * d                                         # axis point at axial 0
        pm = s.Value((u0 + u1) / 2, (v0 + v1) / 2); pm = np.array([pm.X(), pm.Y(), pm.Z()])
        nrm = f.normalAt(cq.Vector(*pm)).toTuple()
        radial = pm - (c + (pm @ d) * d)
        kind = "hole" if np.dot(nrm, radial) < 0 else "boss"
        key = (round(2 * r, 3), tuple(np.round(d, 4)), tuple(np.round(c, 2)), round(a0, 2), round(a1, 2), kind)
        groups[key] = groups.get(key, 0) + (u1 - u0)
    out = []
    for (dia, d, c, a0, a1, kind), span in groups.items():
        if span < 2 * math.pi - 1e-3: continue                     # fillets, slot ends: not a full circle
        d, c = np.array(d), np.array(c)
        typ = kind
        if kind == "hole":
            perp = unit(np.cross(d, [1, 0, 0] if abs(d[0]) < 0.9 else [0, 1, 0]))
            off = perp * (dia / 2) * 0.85                               # just inside the hole's wall: a counterbore's floor is material
            ends = [c + (a0 - 0.05) * d + off, c + (a1 + 0.05) * d + off]
            inside = [solid.isInside(cq.Vector(*e), 1e-4) for e in ends]
            typ = "THRU" if not any(inside) else f"BLIND {a1 - a0:.1f} deep"
        else:
            typ = f"BOSS {a1 - a0:.1f} tall"
        out.append(dict(dia=dia, axis="XYZ"[int(np.argmax(np.abs(d)))], c=c, a0=a0, a1=a1, type=typ))
    for h in out:                                                      # blind and on the axis of a smaller hole: a counterbore
        if h["type"].startswith("BLIND") and any(o is not h and o["dia"] < h["dia"] and o["axis"] == h["axis"] and np.allclose(o["c"], h["c"], atol=0.02)
                                                 for o in out):
            h["type"] = "C'BORE " + h["type"][6:]
    return out

def fmt(x):
    x = round(float(x), 2)
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s

# ============================================================================ a drawing sheet
class View:
    def __init__(self, key, shape, proj, label, hidden=True, section=None, extra=None, k=1.0):
        self.key, self.proj, self.label, self.k = key, proj, label, k      # k: pictorial views drawn smaller
        if section:
            origin, normal, self.cut = section
            half = keep_half(shape, origin, normal)
            self.vis, self.tan, self.hid = hlr(half, proj, hidden=False)
            self.hatch = []
            for i, s in enumerate(self.cut if isinstance(self.cut, list) else [shape]):
                self.hatch.append(section_faces(s, origin, normal, proj))
        else:
            self.vis, self.tan, self.hid = hlr(shape, proj, hidden)
            self.hatch = []
        self.phantom = [np.array([proj.uv(p) for p in loop]) for loop in (extra or [])]
        allp = np.vstack(self.vis + self.tan + (self.phantom or []))
        self.lo, self.hi = allp.min(0), allp.max(0)
        self.dims, self.notes, self.cuts, self.pads = [], [], [], {"l": 6, "r": 6, "t": 9, "b": 6}
        self.origin = None                                          # paper position of model (0, 0)

    def pads_at(self, s):
        """Room needed around the view at scale s: dimensions (fixed) and the notes' text (depends on s)."""
        p = dict(self.pads); sk = s * self.k; W = (self.hi - self.lo) * sk
        for p3, text, dx, dy in self.notes:
            a = (self.proj.uv(p3) - self.lo) * sk
            lines = text.split("\n"); w = max(len(l) for l in lines) * 1.35; h = len(lines) * 3.3
            x0, x1 = (a[0] + dx + 3, a[0] + dx + 3 + w) if dx >= 0 else (a[0] + dx - 3 - w, a[0] + dx - 3)
            y0, y1 = a[1] + dy - h / 2, a[1] + dy + h / 2
            p["l"] = max(p["l"], -x0 + 2); p["r"] = max(p["r"], x1 - W[0] + 2)
            p["b"] = max(p["b"], -y0 + 2); p["t"] = max(p["t"], y1 - W[1] + 2)
        return p

class Sheet:
    def __init__(self, title, part, dwg, source, profile, scale=None):
        self.title, self.part, self.dwg, self.source, self.profile = title, part, dwg, source, profile
        self.views, self.grid, self.tables, self.notes = {}, [], [], []
        self.fixed_scale = scale
    def view(self, *a, **k):
        v = View(*a, **k); self.views[v.key] = v; return v

    # --- annotations (points are 3-D model points; offsets in paper mm)
    def hdim(self, key, p1, p2, side="b", level=0, text=None):
        v = self.views[key]; v.dims.append(("h", p1, p2, side, level, text))
        v.pads[side] = max(v.pads[side], 12 + 7 * level)
    def vdim(self, key, p1, p2, side="l", level=0, text=None):
        v = self.views[key]; v.dims.append(("v", p1, p2, side, level, text))
        v.pads[side] = max(v.pads[side], 12 + 7 * level + (0 if side == "r" else 0))
    def note(self, key, p, text, dx, dy):
        v = self.views[key]; v.notes.append((p, text, dx, dy))
        nl = text.count("\n") + 1                                   # (sideways room is left to the placement)
        if dy > 0: v.pads["t"] = max(v.pads["t"], dy + 2 + 1.8 * TXT * nl / 2)
        if dy < 0: v.pads["b"] = max(v.pads["b"], -dy + 2 + 1.8 * TXT * nl / 2)
    def cutline(self, key, p1, p2, letter, look):
        """Section cutting-plane line on a view; look = 3-D direction the section looks along."""
        self.views[key].cuts.append((p1, p2, letter, look))
    def table(self, title, header, rows, widths):
        self.tables.append((title, header, rows, widths))

    # --- layout
    def layout(self, region):
        x0, y0, x1, y1 = region
        nr, nc = len(self.grid), max(len(r) for r in self.grid)
        PL = PR = PT = PB = None
        cells = [(i, j, self.views[k]) for i, row in enumerate(self.grid) for j, k in enumerate(row) if k]
        def need(s):
            nonlocal PL, PR, PT, PB
            for i, j, v in cells: v.cur = v.pads_at(s)
            # projected views share rows / columns: the same padding across a row (column) keeps them lined up
            PL = [max([v.cur["l"] for i, j, v in cells if j == c], default=0) for c in range(nc)]
            PR = [max([v.cur["r"] for i, j, v in cells if j == c], default=0) for c in range(nc)]
            PT = [max([v.cur["t"] for i, j, v in cells if i == r], default=0) for r in range(nr)]
            PB = [max([v.cur["b"] for i, j, v in cells if i == r], default=0) + 10 for r in range(nr)]
            W = [max([(v.hi - v.lo)[0] * s * v.k for i, j, v in cells if j == c], default=0) for c in range(nc)]
            Hh = [max([(v.hi - v.lo)[1] * s * v.k for i, j, v in cells if i == r], default=0) for r in range(nr)]
            return [W[c] + PL[c] + PR[c] for c in range(nc)], [Hh[r] + PT[r] + PB[r] for r in range(nr)], W, Hh
        choices = [self.fixed_scale] if self.fixed_scale else SCALES
        for a, b in choices:
            s = a / b; cw, rh, Wc, Hr = need(s)
            if sum(cw) <= x1 - x0 and sum(rh) <= y1 - y0: break
        self.s, self.scale_txt = s, f"{a}:{fmt(b)}"
        # spread spare room evenly
        sx = (x1 - x0 - sum(cw)) / (nc + 1); sy = (y1 - y0 - sum(rh)) / (nr + 1)
        xs = [x0 + sx * (j + 1) + sum(cw[:j]) for j in range(nc)]
        ys = [y1 - sy * (i + 1) - sum(rh[:i + 1]) for i in range(nr)]
        # (columns / rows aligned on the views' centers, so projected views line up)
        for i, row in enumerate(self.grid):
            for j, k in enumerate(row):
                if not k: continue
                v = self.views[k]; c = (v.lo + v.hi) / 2
                cx = xs[j] + PL[j] + Wc[j] / 2
                cy = ys[i] + PB[i] + Hr[i] / 2
                v.origin = np.array([cx, cy]) - c * s * v.k

# ============================================================================ output: PDF page + DXF
class Out:
    """Draws one sheet into a matplotlib page and (per part) into a DXF at 1:1 model mm."""
    LW_VIS, LW_TAN, LW_HID, LW_THIN = 1.1, 0.35, 0.5, 0.4

    def __init__(self, sheet, sheet_no, sheets, dxf):
        self.sh, self.no, self.n, self.dxf = sheet, sheet_no, sheets, dxf
        self.fig = plt.figure(figsize=(PAPER_W / 25.4, PAPER_H / 25.4))
        self.ax = self.fig.add_axes([0, 0, 1, 1]); self.ax.set_xlim(0, PAPER_W); self.ax.set_ylim(0, PAPER_H)
        self.ax.set_aspect("equal"); self.ax.axis("off")
        self.fs = TXT / 0.3528 * 0.92                                 # mm -> pt
        self.msp = dxf.modelspace() if dxf else None

    # paper <-> dxf: the dxf is the paper layout at 1:1 model size
    def P(self, v, uv): return v.origin + np.asarray(uv) * self.sh.s * v.k
    def D(self, paper): return tuple(np.asarray(paper) / self.sh.s)

    def line(self, pts, lw, ls="-", color="k", layer="VISIBLE", z=2):
        pts = np.asarray(pts)
        self.ax.plot(pts[:, 0], pts[:, 1], lw=lw, ls=ls, color=color, solid_capstyle="round", zorder=z)
        if self.msp is not None and layer:
            self.msp.add_lwpolyline([self.D(p) for p in pts], dxfattribs={"layer": layer})

    def text(self, xy, s, size=None, ha="left", va="baseline", rot=0, layer="NOTES", weight="normal", dxf=True):
        self.ax.text(xy[0], xy[1], s, fontsize=(size or TXT) / 0.3528 * 0.92, ha=ha, va=va, rotation=rot,
                     family="DejaVu Sans", weight=weight, zorder=5)
        if self.msp is not None and dxf and layer:
            al = {("left", "baseline"): 7, ("center", "baseline"): 8, ("right", "baseline"): 9, ("center", "center"): 5,
                  ("left", "center"): 4, ("right", "center"): 6, ("left", "top"): 1, ("center", "top"): 2, ("left", "bottom"): 7,
                  ("center", "bottom"): 8, ("right", "top"): 3}.get((ha, va), 7)
            self.msp.add_mtext(s.replace("Ø", "%%c").replace("\n", "\\P"), dxfattribs={"layer": layer, "char_height": (size or TXT) / self.sh.s, "insert": self.D(xy),
                                               "attachment_point": al, "rotation": rot})

    def arrow(self, tip, direction, layer="DIMS"):
        d = unit(direction); n = np.array([-d[1], d[0]]); L, W = 2.6, 0.45
        tri = np.array([tip, tip - d * L + n * W, tip - d * L - n * W])
        self.ax.add_patch(Polygon(tri, closed=True, color="k", lw=0, zorder=4))

    def draw_view(self, v):
        s = self.sh.s * v.k
        L = (lambda name: name) if v.k == 1 else (lambda name: None)       # pictorial views: PDF only
        for pl in v.hid: self.line(self.P(v, pl), self.LW_HID, (0, (3, 1.6)), "0.35", L("HIDDEN"), 1)
        for pl in v.tan: self.line(self.P(v, pl), self.LW_TAN, "-", "0.55", L("TANGENT"), 1)
        for pl in v.vis: self.line(self.P(v, pl), self.LW_VIS if v.k == 1 else 0.8, "-", "k", L("VISIBLE"), 2)
        for pl in v.phantom: self.line(self.P(v, np.vstack([pl, pl[:1]])), self.LW_THIN, (0, (8, 2, 1.5, 2)), "0.3", "PHANTOM", 1)
        for i, polys in enumerate(v.hatch):
            for loops in polys:
                verts, codes = [], []
                for k, lp in enumerate(loops):
                    lp = self.P(v, lp)
                    area = 0.5 * np.sum(lp[:-1, 0] * lp[1:, 1] - lp[1:, 0] * lp[:-1, 1])
                    if (k == 0) != (area > 0): lp = lp[::-1]
                    verts += list(lp) + [lp[0]]; codes += [MPath.MOVETO] + [MPath.LINETO] * (len(lp) - 1) + [MPath.CLOSEPOLY]
                self.ax.add_patch(PathPatch(MPath(verts, codes), fc="none", ec="0.25", lw=0, hatch=["////", "\\\\\\\\", "xx", "...."][i % 4], zorder=0))
                if self.msp is not None:
                    h = self.msp.add_hatch(dxfattribs={"layer": "HATCH"})
                    h.set_pattern_fill("ANSI31", scale=0.6 / s, angle=[0, 90, 45, 135][i % 4])
                    for k, lp in enumerate(loops):
                        h.paths.add_polyline_path([self.D(self.P(v, p)) for p in lp], is_closed=True, flags=1 if k == 0 else 16)
        # view label
        lab = f"{v.label}" + ("  (not to scale)" if v.k != 1 else "")
        cx = v.origin[0] + (v.lo[0] + v.hi[0]) / 2 * s
        self.text((cx, v.origin[1] + v.lo[1] * s - v.cur["b"] - 7), lab, size=TXT * 1.15, ha="center", weight="bold", layer=L("NOTES"))
        for d in v.dims: self.draw_dim(v, *d)
        for p, t, dx, dy in v.notes: self.draw_note(v, p, t, dx, dy)
        for c in v.cuts: self.draw_cut(v, *c)

    def draw_dim(self, v, kind, p1, p2, side, level, text):
        s = self.sh.s
        a, b = self.P(v, v.proj.uv(p1)), self.P(v, v.proj.uv(p2))
        lo, hi = self.P(v, v.lo), self.P(v, v.hi)
        off = 8 + 7 * level
        if kind == "h":
            val = abs(v.proj.uv(p2)[0] - v.proj.uv(p1)[0])
            y = (lo[1] - off) if side == "b" else (hi[1] + off)
            ext = [(a, np.array([a[0], y])), (b, np.array([b[0], y]))]
            d1, d2 = np.array([min(a[0], b[0]), y]), np.array([max(a[0], b[0]), y])
        else:
            val = abs(v.proj.uv(p2)[1] - v.proj.uv(p1)[1])
            x = (lo[0] - off) if side == "l" else (hi[0] + off)
            ext = [(a, np.array([x, a[1]])), (b, np.array([x, b[1]]))]
            d1, d2 = np.array([x, min(a[1], b[1])]), np.array([x, max(a[1], b[1])])
        for p, q in ext:
            dirv = q - p; L = np.linalg.norm(dirv)
            if L < 1e-6: continue
            u = dirv / L
            self.ax.plot(*np.array([p + u * 1.2, q + u * 1.6]).T, lw=self.LW_THIN, color="k", zorder=3)
        span = np.linalg.norm(d2 - d1)
        self.ax.plot(*np.array([d1, d2]).T, lw=self.LW_THIN, color="k", zorder=3)
        if span > 7:
            self.arrow(d1, d1 - d2); self.arrow(d2, d2 - d1)
        else:                                                         # small: arrows from outside
            u = unit(d2 - d1) if span > 0 else np.array([1.0, 0])
            self.ax.plot(*np.array([d1 - u * 6, d2 + u * 6]).T, lw=self.LW_THIN, color="k", zorder=3)
            self.arrow(d1, u); self.arrow(d2, -u)
        t = text if text is not None else fmt(val)
        t = t.replace("{}", fmt(val))
        mid = (d1 + d2) / 2
        tl = len(t) * TXT * 0.56                                     # text length on paper
        if kind == "h":
            pos = mid + np.array([0, 1.3]) if span > tl + 6 else d2 + np.array([7 + tl / 2, 1.3])
            self.text(pos, t, ha="center", dxf=False)
        else:
            if span > tl + 6:
                pos = mid + np.array([-1.3, 0])
                self.ax.text(pos[0], pos[1], t, fontsize=self.fs, ha="center", va="bottom", rotation=90, rotation_mode="anchor", zorder=5)
            else:                                                     # short: level text beside the end
                right = side == "r"
                self.ax.text(d2[0] + (1.5 if right else -1.5), d2[1] + 2.5, t, fontsize=self.fs, ha="left" if right else "right",
                             va="bottom", zorder=5)
        if self.msp is not None:                                     # a real, editable dimension
            base = self.D(d1)
            dim = self.msp.add_linear_dim(base=base, p1=self.D(a), p2=self.D(b), angle=0 if kind == "h" else 90,
                                          text=(text.replace("{}", "<>") if text else "<>"), dimstyle="HU",
                                          dxfattribs={"layer": "DIMS"})
            dim.render()

    def draw_note(self, v, p, t, dx, dy):
        a = self.P(v, v.proj.uv(p)); b = a + np.array([dx, dy])
        self.ax.plot(*np.array([a, b]).T, lw=self.LW_THIN, color="k", zorder=3)
        self.ax.add_patch(plt.Circle(a, 0.45, color="k", zorder=4))
        lines = t.split("\n"); w = max(len(l) for l in lines) * TXT * 0.6
        end = b + np.array([2 if dx >= 0 else -2, 0])
        self.ax.plot(*np.array([b, end]).T, lw=self.LW_THIN, color="k", zorder=3)
        self.text(end + np.array([1 if dx >= 0 else -1, 0]), t, ha="left" if dx >= 0 else "right", va="center", layer="NOTES")
        if self.msp is not None:
            self.msp.add_lwpolyline([self.D(a), self.D(b), self.D(end)], dxfattribs={"layer": "NOTES"})

    def draw_cut(self, v, p1, p2, letter, look):
        a, b = self.P(v, v.proj.uv(p1)), self.P(v, v.proj.uv(p2))
        u = unit(b - a)
        side = lambda w: v.pads["t"] if w[1] > 0.5 else v.pads["b"] if w[1] < -0.5 else v.pads["r"] if w[0] > 0 else v.pads["l"]   # (dims only)
        a2, b2 = a - u * (side(-u) + 1), b + u * (side(u) + 1)               # ends clear of the dimensions
        self.ax.plot(*np.array([a2, b2]).T, lw=0.6, color="k", ls=(0, (10, 2, 2, 2)), zorder=3)
        lk = v.proj.uv(look); lk = unit(lk) if np.linalg.norm(lk) > 1e-6 else np.array([0, 1.0])
        for e in (a2, b2):
            self.ax.plot(*np.array([e, e + lk * 7]).T, lw=1.2, color="k", zorder=3)
            self.arrow(e + lk * 7, lk)
            self.text(e + lk * 10.5, letter, size=TXT * 1.5, ha="center", va="center", weight="bold")
        if self.msp is not None:
            self.msp.add_lwpolyline([self.D(a2), self.D(b2)], dxfattribs={"layer": "CUTLINE"})

    def frame(self, region_tables):
        ax, sh = self.ax, self.sh
        ax.add_patch(Rectangle((10, 10), PAPER_W - 20, PAPER_H - 20, fill=False, lw=1.0))
        # title block (bottom right, 190 x 44)
        x0, y0, w = PAPER_W - 10 - 190, 10, 190
        rows = [("TITLE", sh.title), ("PART / FILE", sh.part), ("PROFILE", sh.profile),
                ("MATERIAL", "ASA, Bambu Lab P1S (enclosed)  -  0.4 nozzle"),
                ("UNITS / SCALE", f"mm   -   scale {sh.scale_txt} (dimensions govern; do not scale the print)"),
                ("SOURCE", sh.source), ("DWG / SHEET", f"{sh.dwg}     sheet {self.no} of {self.n}     {datetime.date.today():%Y-%m-%d}")]
        h = 44 / len(rows)
        ax.add_patch(Rectangle((x0, y0), w, 44, fill=False, lw=0.9))
        for i, (k, val) in enumerate(rows):
            y = y0 + 44 - (i + 1) * h
            ax.plot([x0, x0 + w], [y, y], lw=0.4, color="k")
            ax.text(x0 + 1.5, y + h / 2, k, fontsize=5, va="center", color="0.3")
            ax.text(x0 + 30, y + h / 2, val, fontsize=6.6 if i else 7.6, va="center", weight="bold" if i == 0 else "normal")
        ax.plot([x0 + 28.5, x0 + 28.5], [y0, y0 + 44], lw=0.4, color="k")
        # third-angle projection symbol
        sx, sy = x0 - 26, y0 + 8
        ax.add_patch(Polygon([(sx, sy), (sx + 9, sy + 1.8), (sx + 9, sy + 6.2), (sx, sy + 8)], closed=True, fill=False, lw=0.6))
        ax.add_patch(plt.Circle((sx + 17, sy + 4), 4, fill=False, lw=0.6)); ax.add_patch(plt.Circle((sx + 17, sy + 4), 2.2, fill=False, lw=0.6))
        ax.text(sx + 10.5, sy - 3.5, "THIRD ANGLE", fontsize=5, ha="center")
        # general notes (bottom left)
        y = 10 + 44 - 4
        for t in sh.notes:
            ax.text(14, y, t, fontsize=5.6, va="top"); y -= 3.6 * (t.count("\n") + 1)
        # tables
        for (title, header, rows, widths), (tx, ty) in zip(sh.tables, region_tables):
            self.draw_table(tx, ty, title, header, rows, widths)

    def draw_table(self, x, y, title, header, rows, widths):
        ax = self.ax; rh = 3.6
        ax.text(x, y + 1.2, title, fontsize=6.4, weight="bold")
        tw = sum(widths)
        for i, row in enumerate([header] + rows):
            yy = y - (i + 1) * rh
            ax.add_patch(Rectangle((x, yy), tw, rh, fill=(i == 0), fc="0.9", ec="0.5", lw=0.3))
            cx = x
            for val, w in zip(row, widths):
                ax.text(cx + 1, yy + rh / 2, str(val), fontsize=5.2, va="center", weight="bold" if i == 0 else "normal")
                cx += w
        if self.msp is not None:
            txt = title + "\\P" + "\\P".join("   ".join(str(c) for c in r) for r in [header] + rows)
            txt = txt.replace("Ø", "%%c")
            self.msp.add_mtext(txt, dxfattribs={"layer": "TABLES", "char_height": 2.2 / self.sh.s, "insert": self.D((x, y + 4))})

def table_height(t): return 3.6 * (len(t[2]) + 1) + 6

def render(sheets, pdf, dxf_dir):
    dxfs = {}
    for i, sh in enumerate(sheets, 1):
        notes_h = 44 + 4
        widths = [sum(t[3]) for t in sh.tables]
        across = sum(widths) + 8 * len(widths) <= PAPER_W - 28            # tables side by side
        tab_h = max([table_height(t) for t in sh.tables], default=0) if across else sum(table_height(t) for t in sh.tables)
        tab_w = sum(widths) + 8 * len(widths) if across else max(widths, default=0)
        def bottom():
            tpos, tx, ty = [], 14, 10 + notes_h + tab_h - 6
            for t, w in zip(sh.tables, widths):
                tpos.append((tx, ty))
                if across: tx += w + 8
                else: ty -= table_height(t)
            return (14, 10 + notes_h + tab_h, PAPER_W - 14, PAPER_H - 12), tpos
        def right():                                                   # tables in a right-hand column
            tpos, tx, ty = [], PAPER_W - 12 - max(widths), PAPER_H - 18
            for t in sh.tables: tpos.append((tx, ty)); ty -= table_height(t)
            return (14, 10 + notes_h, PAPER_W - 14 - max(widths) - 8, PAPER_H - 12), tpos
        options = [bottom()] if not sh.tables else [bottom()] + ([right()] if sum(table_height(t) for t in sh.tables) < PAPER_H - 30 else [])
        best = None
        for region, tp in options:
            if PAPER_H - 22 - notes_h - tab_h < 60 and region[1] > 10 + notes_h: continue
            sh.layout(region)
            if best is None or sh.s > best[0] + 1e-9: best = (sh.s, region, tp)
        _, region, tpos = best
        sh.layout(region)
        doc = None
        if sh.dxf_name:
            doc = dxfs.get(sh.dxf_name)
            if doc is None:
                doc = ezdxf.new("R2018", setup=True); doc.units = ezdxf.units.MM
                for name, color, lt in (("VISIBLE", 7, "CONTINUOUS"), ("HIDDEN", 8, "HIDDEN"), ("TANGENT", 9, "CONTINUOUS"),
                                        ("PHANTOM", 8, "PHANTOM"), ("HATCH", 8, "CONTINUOUS"), ("DIMS", 3, "CONTINUOUS"),
                                        ("NOTES", 2, "CONTINUOUS"), ("CUTLINE", 1, "CENTER"), ("TABLES", 4, "CONTINUOUS")):
                    doc.layers.add(name, color=color, linetype=lt)
                dxfs[sh.dxf_name] = doc
            ds = doc.dimstyles.get("HU") if doc.dimstyles.has_entry("HU") else doc.dimstyles.duplicate_entry("EZDXF", "HU")
            ds.dxf.dimtxt = TXT / sh.s; ds.dxf.dimasz = 2.5 / sh.s; ds.dxf.dimexo = 1.2 / sh.s; ds.dxf.dimexe = 1.6 / sh.s
            ds.dxf.dimdec = 2; ds.dxf.dimzin = 8; ds.dxf.dimgap = 0.8 / sh.s; ds.dxf.dimdsep = ord(".")
            ds.dxf.dimlfac = 1.0                                       # (ezdxf's own style reports metres as cm)
        o = Out(sh, i, len(sheets), doc if sh.dxf_name else None)
        if doc is not None:
            # each further sheet of the same part goes beside the first one in the drawing
            shift = sum(1 for s in sheets[:i - 1] if s.dxf_name == sh.dxf_name) * (PAPER_W + 40)
            o.D = (lambda paper, sh=sh, shift=shift: tuple((np.asarray(paper) + [shift, 0]) / sh.s))
        for v in sh.views.values():
            if v.origin is not None: o.draw_view(v)
        o.frame(tpos)
        pdf.savefig(o.fig); plt.close(o.fig)
    for name, doc in dxfs.items():
        doc.saveas(dxf_dir / f"{name}.dxf")

# ============================================================================ the parts
def build(profile):
    sys.argv = ["headunit_mount.py", profile]
    G = runpy.run_path(str(HERE / "headunit_mount.py"), run_name="headunit_mount")
    g = lambda k: G[k]
    W, H, SW, SH = g("OUT_W"), g("OUT_H"), g("SCREEN_W"), g("SCREEN_H")
    LO, LIP, BD, R = g("LIP_OVER"), g("LIP"), g("BEZEL_D"), g("R_OUT")
    GPW, GPH = g("GLASS_POCKET_W"), g("GLASS_POCKET_H")
    WW, WH = SW - 2 * LO, SH - 2 * LO
    z0, rd, PT, SL, WALL = g("z0"), g("ring_d"), g("PLATE_T"), g("SLED_L"), g("WALL")
    DW, DH = g("DIN_W"), g("DIN_H")
    sx, sy = abs(g("SCREWS")[0][0]), abs(g("SCREWS")[0][1])
    src = f"cad/headunit_mount.py  ->  cad/print/{profile}/"
    bezel = g("bezel").val()
    ring = g("ring").val().translate(cq.Vector(0, 0, -z0))
    back = g("back").val().translate(cq.Vector(0, 0, -(z0 + rd)))
    tray = g("tray").val()
    FRONT = Proj((0, 0, -1), (-1, 0, 0)); REAR = Proj((0, 0, 1), (1, 0, 0))
    TOP = Proj((0, 1, 0), (-1, 0, 0)); RIGHT = Proj((-1, 0, 0), (0, 0, 1)); BOTTOM = Proj((0, -1, 0), (-1, 0, 0))
    ISO = iso_proj((-1, 0.8, -1.3), (0, 1, 0)); ISO_B = iso_proj((-1, 0.9, 1.2), (0, 1, 0))
    common = ["NOTES:",
              "1. Dimensions in mm. X/Y zero = part center, Z zero = front face (toward the driver), unless noted.",
              "2. Views are third-angle projections of the solids in the STEP files; hole tables are read from the solids.",
              "3. Fit: 0.5 mm per side around the screen glass. Screen sizes are estimates - measure the screen and edit",
              "    SCREEN_* in headunit_mount.py, then re-run it and this script (cad/make_drawings.py)."]
    sheets = []

    def hole_table(sheet, solid, tag, datum="X, Y from center; Z from front face"):
        hs = sorted(holes(solid), key=lambda h: (h["type"], -h["dia"], h["axis"], round(h["c"][0], 1), round(h["c"][1], 1), round(h["c"][2], 1)))
        rows, groups = [], {}
        for h in hs: groups.setdefault((h["dia"], h["axis"], h["type"], round(h["a0"], 2), round(h["a1"], 2)), []).append(h)
        n = 0
        for (dia, axis, typ, a0, a1), grp in groups.items():
            def coord(h):
                c = dict(zip("XYZ", h["c"])); c[axis] = None
                return c
            cs = [coord(h) for h in grp]
            free = [k for k in "XYZ" if k != axis]
            xs = sorted({round(c[free[0]], 2) for c in cs}); ys = sorted({round(c[free[1]], 2) for c in cs})
            pitch = lambda v: (len(v) > 2 and len({round(b - a, 2) for a, b in zip(v, v[1:])}) == 1)
            span = f"{fmt(a0)} to {fmt(a1)}"
            if len(grp) > 8 and len(xs) * len(ys) == len(grp) and pitch(xs) and pitch(ys):
                n += 1
                rows.append([f"{tag}{n}", f"{len(grp)}x  Ø{fmt(dia)}", typ, axis,
                             f"{free[0]} {fmt(xs[0])} ... {fmt(xs[-1])} ({len(xs)} @ {fmt(xs[1] - xs[0])})",
                             f"{free[1]} {fmt(ys[0])} ... {fmt(ys[-1])} ({len(ys)} @ {fmt(ys[1] - ys[0])})", f"{axis} {span}"])
            else:
                for h, c in zip(grp, cs):
                    n += 1
                    rows.append([f"{tag}{n}", f"Ø{fmt(dia)}", typ, axis, f"{free[0]} {fmt(c[free[0]])}", f"{free[1]} {fmt(c[free[1]])}", f"{axis} {span}"])
        sheet.table(f"HOLE / BOSS TABLE ({datum}) - read back from the solid", ["ID", "SIZE", "TYPE", "AXIS", "POSITION", "POSITION", "AXIAL EXTENT"],
                    rows, [10, 20, 30, 11, 45, 45, 28])
        return hs

    # ------------------------------------------------------------------ 0: stack-up
    sh = Sheet("Screen mount - stack-up, parts and parameters", "headunit_mount_assembly.step", f"HU-{profile}-00", src, profile)
    sh.dxf_name = "0_stack_up"
    zr, zb = BD, BD + rd
    tz = zb + PT
    # tray in its place: +Y of the tray toward the open back of the sleeve, standoffs up
    tray_in = tray.moved(g("TRAY_LOC"))
    parts = [bezel, ring.translate(cq.Vector(0, 0, zr)), back.translate(cq.Vector(0, 0, zb)), tray_in]
    asm = cq.Compound.makeCompound(parts)
    glass = [[(0, -SH / 2, LIP), (0, SH / 2, LIP), (0, SH / 2, LIP + g("SCREEN_T")), (0, -SH / 2, LIP + g("SCREEN_T"))]]
    v = sh.view("sec", asm, RIGHT, "SECTION A-A  (through the middle, looking from the driver's right)",
                section=((0, 0, 0), (1, 0, 0), parts), extra=glass)
    sh.view("iso", asm, ISO_B, "ASSEMBLY (from behind)", hidden=False, k=0.55)
    sh.grid = [["sec", "iso"]]
    yb = -H / 2
    sh.hdim("sec", (0, yb, 0), (0, yb, BD), "b", 0, "{}  bezel")
    sh.hdim("sec", (0, yb, zr), (0, yb, zr + rd), "b", 1, "{}  ring")
    sh.hdim("sec", (0, H / 2, zb), (0, H / 2, zb + PT), "t", 1, "{}  plate")
    sh.hdim("sec", (0, -DH / 2, tz), (0, -DH / 2, tz + SL), "b", 2, "{}  sleeve")
    sh.hdim("sec", (0, H / 2, 0), (0, H / 2, tz + SL), "t", 2, "{}  overall depth")
    sh.hdim("sec", (0, H / 2, LIP), (0, H / 2, LIP + g("SCREEN_T")), "t", 0, "{}  screen (phantom)")
    sh.vdim("sec", (0, -H / 2, 0), (0, H / 2, 0), "l", 0)
    sh.vdim("sec", (0, -DH / 2, tz + SL), (0, DH / 2, tz + SL), "r", 0, "{}  sleeve")
    sh.note("sec", (0, -DH / 2 + WALL + 1.4, tz + 50), "4  Pi tray (on the sleeve floor,\n    +Y end toward the open back)", 30, -25)
    sh.table("PARTS", ["ITEM", "PART", "QTY", "PRINT", "FILE", "SHEET"],
             [["1", "Bezel", "1", "face down on the textured plate", "1_bezel.step / .stl", "2"],
              ["2", "Spacer ring", "1", "flat", "2_spacer_ring.step / .stl", "3"],
              ["3", "Back plate + DIN sleeve", "1", "plate down, sleeve straight up", "3_back_and_sled.step / .stl", "4-5"],
              ["4", "Pi tray", "1", "flat, standoffs up", "4_pi_tray.step / .stl", "6"],
              ["5", "M3 heat-set insert, M3 x 5.7 x 4.6 OD", "6", "pressed into the bezel's Ø4.0 holes", "-", "2"],
              ["6", f"M3 x {g('SCREW_L')} screw (socket / button head)", "6", "from the back into the inserts", "-", "2-4"],
              ["7", "M2.5 screw / insert, Pi on the standoffs", "4", "-", "-", "6"],
              ["8", "M3 screw + nut, tray to sleeve floor", "4", "-", "-", "5-6"]], [11, 62, 10, 52, 46, 12])
    prm = [("SCREEN_W x SCREEN_H x SCREEN_T", f"{fmt(SW)} x {fmt(SH)} x {fmt(g('SCREEN_T'))}", "screen module outline (estimate - verify)"),
           ("VIEW_W x VIEW_H", f"{fmt(g('VIEW_W'))} x {fmt(g('VIEW_H'))}", "lit picture area (window must stay larger)"),
           ("PCB_DEPTH", fmt(g("PCB_DEPTH")), "room behind the screen = spacer ring thickness"),
           ("CLR / BORDER / LIP / LIP_OVER", f"{fmt(g('CLR'))} / {fmt(g('BORDER'))} / {fmt(LIP)} / {fmt(LO)}", "fit per side / frame / front lip / lip overlap"),
           ("R_OUT", fmt(R), "outer corner radius"),
           ("DIN_W x DIN_H", f"{fmt(DW)} x {fmt(DH)}", "sleeve outside (opening 182 x 53 single, 182 x 103 double DIN)"),
           ("SLED_L / PLATE_T / WALL", f"{fmt(SL)} / {fmt(PT)} / {fmt(WALL)}", "sleeve depth / back plate / walls"),
           ("INSERT_D x INSERT_L", f"{fmt(g('INSERT_D'))} x {fmt(g('INSERT_L'))}", "M3 heat-set insert hole"),
           ("M3_CLR / M3_HEAD / M3_HEAD_H", f"{fmt(g('M3_CLR'))} / {fmt(g('M3_HEAD'))} / {fmt(g('M3_HEAD_H'))}", "screw clearance / counterbore Ø / depth"),
           ("derived: outer W x H", f"{fmt(W)} x {fmt(H)}", "SCREEN + 2*CLR + 2*BORDER"),
           ("derived: bezel depth", fmt(BD), "LIP + SCREEN_T + 0.3"),
           ("derived: screw X / Y", f"±{fmt(sx)} (and 0) / ±{fmt(sy)}", "6 screws on the frame centerline")]
    sh.table("PARAMETERS (headunit_mount.py)", ["NAME", "VALUE", "MEANING"], [list(r) for r in prm], [52, 34, 72])
    sh.notes = common + [f"4. Screws M3 x {g('SCREW_L')}: under the head {fmt(PT - g('M3_HEAD_H'))} (plate below the counterbore) + ring {fmt(rd)},",
                         f"    then {fmt(g('SCREW_L') - g('GRIP'))} into the bezel's insert (insert hole {fmt(g('INSERT_L'))} deep)."]
    sheets.append(sh)

    # ------------------------------------------------------------------ 1: bezel
    sh = Sheet("Bezel", "1_bezel.step", f"HU-{profile}-01", src, profile); sh.dxf_name = "1_bezel"
    sh.view("top", bezel, TOP, "TOP")
    sh.view("front", bezel, FRONT, "FRONT (driver's view)")
    sh.view("secA", bezel, RIGHT, "SECTION A-A", section=((0, 0, 0), (1, 0, 0), None))
    sh.view("rear", bezel, REAR, "REAR")
    sh.view("iso", bezel, ISO_B, "ISO (from behind)", hidden=False, k=0.55)
    sh.grid = [["top", None, "iso"], ["front", "secA", "rear"]]
    sh.hdim("front", (-W / 2, -H / 2, 0), (W / 2, -H / 2, 0), "b", 0)
    sh.vdim("front", (W / 2, -H / 2, 0), (W / 2, H / 2, 0), "l", 0)
    sh.hdim("front", (-WW / 2, WH / 2, 0), (WW / 2, WH / 2, 0), "t", 0, "{}  window")
    sh.vdim("front", (-WW / 2, -WH / 2, 0), (-WW / 2, WH / 2, 0), "r", 0, "{}  window")
    sh.note("front", (W / 2 - R * 0.3, H / 2 - R * 0.3, 0), f"R{fmt(R)} (4x)", -10, 10)
    sh.note("front", (WW / 2 - 0.6, WH / 2 - 0.6, 0), "R2 (4x) window corners", 18, -14)
    sh.cutline("front", (0, H / 2, 0), (0, -H / 2, 0), "A", (1, 0, 0))
    sh.vdim("top", (0, H / 2, 0), (0, H / 2, BD), "l", 0)
    sh.hdim("secA", (0, -H / 2, 0), (0, -H / 2, BD), "b", 0)
    sh.hdim("secA", (0, WH / 2, 0), (0, WH / 2, LIP), "t", 0, "{} lip")
    sh.hdim("secA", (0, sy, BD - g("INSERT_L")), (0, sy, BD), "t", 1, "{} insert")
    sh.vdim("secA", (0, -GPH / 2, BD), (0, GPH / 2, BD), "r", 0, "{}  pocket")
    sh.note("secA", (0, -H / 2 + 0.3, 0.3), "0.8 x 45°\nfront edge", -8, -14)
    sh.hdim("rear", (-GPW / 2, -GPH / 2, BD), (GPW / 2, -GPH / 2, BD), "b", 0, "{}  pocket")
    sh.vdim("rear", (GPW / 2, -GPH / 2, BD), (GPW / 2, GPH / 2, BD), "r", 0, "{}  pocket")
    sh.hdim("rear", (-sx, sy, BD), (sx, sy, BD), "t", 0)
    sh.hdim("rear", (0, sy, BD), (sx, sy, BD), "t", 1)
    sh.vdim("rear", (-sx, -sy, BD), (-sx, sy, BD), "l", 0)
    sh.note("rear", (sx, -sy, BD), f"6x Ø{fmt(g('INSERT_D'))} ↧{fmt(g('INSERT_L'))}\nM3 heat-set inserts", -30, 16)
    sh.note("rear", (GPW / 2 - 0.3, GPH / 2 - 0.3, BD), "R1.5 (4x) pocket corners", -26, -14)
    sh.notes = common + [f"4. Pocket = screen + 2 x {fmt(g('CLR'))}; window = screen - 2 x {fmt(LO)} (lip overlap). Prints face down: the front is the textured-plate side."]
    hole_table(sh, bezel, "A")
    sheets.append(sh)

    # ------------------------------------------------------------------ 2: spacer ring
    sh = Sheet("Spacer ring", "2_spacer_ring.step", f"HU-{profile}-02", src, profile); sh.dxf_name = "2_spacer_ring"
    OW, OH = SW - 6, SH - 6; nx = SW / 2 - 3
    sh.view("top", ring, TOP, "TOP")
    sh.view("front", ring, FRONT, "FRONT")
    sh.view("secA", ring, RIGHT, "SECTION A-A", section=((0, 0, 0), (1, 0, 0), None))
    sh.view("iso", ring, ISO, "ISO", hidden=False, k=0.55)
    sh.grid = [["top", None, "iso"], ["front", "secA", None]]
    sh.hdim("front", (-W / 2, -H / 2, 0), (W / 2, -H / 2, 0), "b", 0)
    sh.vdim("front", (W / 2, -H / 2, 0), (W / 2, H / 2, 0), "l", 0)
    sh.hdim("front", (-OW / 2, OH / 2, 0), (OW / 2, OH / 2, 0), "t", 0, "{}  opening")
    sh.vdim("front", (-OW / 2, -OH / 2, 0), (-OW / 2, OH / 2, 0), "r", 0, "{}  opening")
    sh.hdim("front", (-sx, sy, 0), (sx, sy, 0), "t", 1)
    sh.vdim("front", (sx, -sy, 0), (sx, sy, 0), "l", 1)
    sh.hdim("front", (0, -15, 0), (-nx, -15, 0), "b", 1, "{}  notch center")
    sh.hdim("front", (-nx - 6, 15, 0), (-nx + 6, 15, 0), "b", 2, "{}  notch")
    sh.vdim("front", (-nx - 6, -15, 0), (-nx - 6, 15, 0), "r", 1, "{}  notch")
    sh.note("front", (sx, sy, 0), f"6x Ø{fmt(g('M3_CLR'))} THRU", -12, 12)
    sh.note("front", (OW / 2 - 0.6, -OH / 2 + 0.6, 0), "R2 (4x)", 12, -14)
    sh.cutline("front", (0, H / 2, 0), (0, -H / 2, 0), "A", (1, 0, 0))
    sh.vdim("top", (0, H / 2, 0), (0, H / 2, rd), "l", 0)
    sh.hdim("secA", (0, -H / 2, 0), (0, -H / 2, rd), "b", 0)
    sh.vdim("secA", (0, OH / 2, rd), (0, H / 2, rd), "r", 0)
    sh.notes = common + ["4. Cable notches 12 x 30 at both ends (screen HDMI / USB may exit to either side). Thickness = PCB_DEPTH."]
    sh.table("NOTCH TABLE", ["ID", "W x H", "FOR", "X", "Y", "DEPTH"],
             [[f"G{i + 1}", "12 x 30", "cable exit", f"X {fmt(sx_ * nx)}", "Y 0", "THRU"] for i, sx_ in enumerate((-1, 1))], [10, 20, 30, 20, 14, 14])
    hole_table(sh, ring, "B")
    sheets.append(sh)

    # ------------------------------------------------------------------ 3: back plate + sleeve (2 sheets)
    tz = PT
    sh = Sheet("Back plate + DIN sleeve (1 of 2)", "3_back_and_sled.step", f"HU-{profile}-03", src, profile); sh.dxf_name = "3_back_and_sled"
    sh.view("top", back, TOP, "TOP")
    sh.view("front", back, FRONT, "FRONT (plate face, toward the screen)")
    sh.view("right", back, RIGHT, "RIGHT SIDE")
    sh.view("iso", back, ISO_B, "ISO (from behind)", hidden=False, k=0.55)
    sh.grid = [["top", "iso"], ["front", "right"]]
    IW, IH = DW - 2 * WALL, DH - 2 * WALL
    sh.hdim("front", (-W / 2, -H / 2, 0), (W / 2, -H / 2, 0), "b", 0)
    sh.vdim("front", (W / 2, -H / 2, 0), (W / 2, H / 2, 0), "l", 0)
    sh.hdim("front", (-IW / 2, -IH / 2, 0), (IW / 2, -IH / 2, 0), "b", 1, "{}  opening")
    sh.vdim("front", (-IW / 2, -IH / 2, 0), (-IW / 2, IH / 2, 0), "r", 0, "{}  opening")
    sh.hdim("front", (-sx, sy, 0), (sx, sy, 0), "t", 0)
    sh.vdim("front", (W / 2 - 1, -sy, 0), (W / 2 - 1, sy, 0), "l", 1)
    sh.note("front", (sx, sy, 0), f"6x Ø{fmt(g('M3_CLR'))} THRU\nC'BORE Ø{fmt(g('M3_HEAD'))} ↧{fmt(g('M3_HEAD_H'))} from the back", -14, 13)
    sh.vdim("top", (0, H / 2, 0), (0, H / 2, PT), "l", 0)
    sh.vdim("top", (0, H / 2, PT), (0, H / 2, PT + SL), "l", 1, "{}  sleeve")
    sh.vdim("top", (DW / 2, DH / 2, PT), (DW / 2, DH / 2, PT + 30), "r", 0)
    sh.vdim("top", (DW / 2, DH / 2, PT + 30), (DW / 2, DH / 2, PT + 90), "r", 1, "{}  vents")
    sh.hdim("top", (-30, DH / 2, PT + 90), (30, DH / 2, PT + 90), "t", 0, "{}  (8 x 7.5)")
    sh.hdim("top", (-DW / 2, DH / 2, PT + SL), (DW / 2, DH / 2, PT + SL), "t", 1, "{}  sleeve")
    sh.note("top", (30, DH / 2, PT + 60), "9x vent slots 3.0 x 60\n7.5 pitch, through the top wall", 32, 18)
    sh.hdim("right", (-DW / 2, -H / 2, 0), (-DW / 2, -H / 2, PT + 35), "b", 0)
    sh.hdim("right", (-DW / 2, -H / 2, 0), (-DW / 2, -H / 2, PT + 80), "b", 1)
    sh.vdim("right", (-DW / 2, -DH / 2, PT + SL), (-DW / 2, DH / 2, PT + SL), "r", 0, "{}  sleeve")
    sh.vdim("right", (-W / 2, -H / 2, 0), (-W / 2, H / 2, 0), "l", 0)
    sh.note("right", (-DW / 2, 2.75, PT + 80 + 12.25), "4x slots 5.5 x 30 (M5)\nboth sides, for a DIN cage bracket", 18, 22)
    sh.notes = common + [
        f"4. Screw holes: Ø{fmt(g('M3_CLR'))} through, counterbored Ø{fmt(g('M3_HEAD'))} x {fmt(g('M3_HEAD_H'))} from the back (sleeve side), leaving",
        f"    {fmt(PT - g('M3_HEAD_H'))} under the head. M3 x {g('SCREW_L')} screws; heads sit below the plate's back face.",
        "5. Gussets 10 x 12 x 2.4: top and bottom at X = -55, 0, +55; one each side when the plate is >= 10 wider than the sleeve."]
    sheets.append(sh)

    sh = Sheet("Back plate + DIN sleeve (2 of 2)", "3_back_and_sled.step", f"HU-{profile}-04", src, profile); sh.dxf_name = "3_back_and_sled"
    sh.view("rear", back, REAR, "REAR (open end of the sleeve)", hidden=False)
    sh.view("secA", back, RIGHT, "SECTION A-A (X = 0)", section=((0, 0, 0), (1, 0, 0), None))
    sh.view("bottom", back, BOTTOM, "BOTTOM", hidden=False)
    sh.grid = [["rear", "secA", "bottom"]]
    sh.hdim("rear", (-DW / 2, -DH / 2, PT + SL), (DW / 2, -DH / 2, PT + SL), "b", 0, "{}  sleeve")
    sh.vdim("rear", (DW / 2, -DH / 2, PT + SL), (DW / 2, DH / 2, PT + SL), "r", 0, "{}  sleeve")
    sh.note("rear", (DW / 2 - 0.6, DH / 2 - 0.6, PT + SL), f"R2 (4x) outside, walls {fmt(WALL)}", 10, 14)
    sh.cutline("rear", (0, H / 2, PT + SL), (0, -H / 2, PT + SL), "A", (1, 0, 0))
    sh.hdim("secA", (0, -H / 2, 0), (0, -H / 2, PT), "b", 0)
    sh.hdim("secA", (0, -DH / 2, 0), (0, -DH / 2, PT + SL), "b", 1)
    sh.hdim("secA", (0, DH / 2, PT), (0, DH / 2, PT + 12), "t", 0, "{} gusset")
    sh.vdim("secA", (0, DH / 2, PT), (0, DH / 2 + 10, PT), "l", 0)
    sh.vdim("secA", (0, DH / 2 - WALL, PT + SL), (0, DH / 2, PT + SL), "r", 0, "{} wall")
    sh.vdim("secA", (0, -DH / 2, PT + SL), (0, DH / 2, PT + SL), "r", 1)
    sh.hdim("bottom", (-70, -DH / 2, PT + 25), (70, -DH / 2, PT + 25), "b", 0)
    sh.vdim("bottom", (70, -DH / 2, 0), (70, -DH / 2, PT + 25), "r", 0)
    sh.vdim("bottom", (70, -DH / 2, 0), (70, -DH / 2, PT + 95), "r", 1)
    sh.note("bottom", (-70, -DH / 2, PT + 95), f"4x Ø{fmt(g('M3_CLR'))} THRU floor\n(tray screws)", 14, -14)
    sh.notes = common + ["4. Hole table positions: X / Y from center, Z from the plate's front face (sleeve starts at Z = {}).".format(fmt(PT))]
    sl = [[f"F{i + 1}", "3.0 x 60", "vent, top wall", f"X {fmt(-30 + i * 7.5)}", f"Z {fmt(PT + 60)}", "length along Z", "THRU"] for i in range(9)]
    sl += [[f"F{10 + i}", "5.5 x 30", "bracket (M5), both side walls", f"Y 0", f"Z {fmt(z)}", "length along Z", "THRU, round ends"]
           for i, z in enumerate((PT + 35, PT + 80))]
    sh.table("SLOT TABLE (center positions)", ["ID", "W x L", "FOR", "", "", "", ""], sl, [10, 20, 46, 18, 18, 26, 26])
    hole_table(sh, back, "C")
    sheets.append(sh)

    # ------------------------------------------------------------------ 4: Pi tray
    sh = Sheet("Pi tray", "4_pi_tray.step", f"HU-{profile}-05", src, profile); sh.dxf_name = "4_pi_tray"
    TW, TL, TT = g("TRAY_W"), g("TRAY_L"), g("TRAY_T")
    PX, PY = g("PI_X"), g("PI_Y")
    PLAN = Proj((0, 0, 1), (1, 0, 0)); TFRONT = Proj((0, -1, 0), (1, 0, 0)); TRIGHT = Proj((1, 0, 0), (0, 1, 0))
    sh.view("plan", tray, PLAN, "PLAN (standoff side)")
    sh.view("front", tray, TFRONT, "FRONT (end toward the screen)")
    sh.grid = [["plan"], ["front"]]
    ph = g("pi_holes")
    sh.hdim("plan", (-TW / 2, -TL / 2, 0), (TW / 2, -TL / 2, 0), "b", 0)
    sh.vdim("plan", (-TW / 2, -TL / 2, 0), (-TW / 2, TL / 2, 0), "l", 0)
    sh.hdim("plan", (ph[0][0], ph[0][1], 0), (ph[1][0], ph[1][1], 0), "t", 0, "{}  Pi")
    sh.hdim("plan", (-TW / 2, TL / 2, 0), (ph[0][0], ph[0][1], 0), "t", 1)
    sh.vdim("plan", (ph[1][0], ph[3][1], 0), (ph[1][0], ph[1][1], 0), "r", 0, "{}  Pi")
    sh.vdim("plan", (TW / 2, ph[1][1], 0), (TW / 2, TL / 2, 0), "r", 1)
    gx0, gy0 = PX + 45, -TL / 2 + 14
    sh.hdim("plan", (-TW / 2, -TL / 2, 0), (gx0, gy0, 0), "b", 1)
    sh.hdim("plan", (gx0, gy0, 0), (gx0 + 40, gy0, 0), "b", 2, "{}  (4 x 10)")
    sh.vdim("plan", (TW / 2, -TL / 2, 0), (gx0 + 40, gy0, 0), "r", 2)
    sh.vdim("plan", (gx0 + 40, gy0, 0), (gx0 + 40, gy0 + 70, 0), "r", 3, "{}  (7 x 10)")
    sh.note("plan", (gx0 + 40, gy0 + 70, TT), "40x Ø3.2 THRU module grid 10 x 10", 10, 36)
    sh.note("plan", (ph[2][0], ph[2][1], TT + 8), "4x boss Ø6.4 x 8 high\nØ3.2 hole (M2.5)", -10, -12)
    sh.note("plan", (PX - 15, PY + 30, TT), "6x vent + 2x strap slots\n(slot table)", -8, 22)
    sh.note("plan", (70, 38, TT), "4x Ø3.4 THRU to sleeve floor", 10, 18)
    sh.vdim("front", (TW / 2, 0, 0), (TW / 2, 0, TT), "r", 0)
    sh.vdim("front", (TW / 2, 0, 0), (TW / 2, 0, 8), "r", 1, "{}  rails")
    sh.vdim("front", (ph[0][0], 0, 0), (ph[0][0], 0, TT + 8), "l", 0, "{}  standoffs")
    sh.hdim("front", (-TW / 2, 0, 8), (-TW / 2 + TT, 0, 8), "t", 0, "{}  rail")
    sh.notes = ["NOTES:", "1. Dimensions in mm. X/Y zero = tray center, Z zero = underside. +Y end goes toward the OPEN BACK of the sleeve",
                "    (Pi USB / Ethernet end); then the 4 floor holes line up with the sleeve floor holes.",
                "2. Views are third-angle projections of the solid; hole table read from the solid.",
                f"3. Width = sleeve inside - 0.6 (0.3 per side); length = SLED_L - 4. Pi 4 holes 58 x 49 (board 85 x 56)."]
    sl = [[f"E{i + 1}", "3 x 60", "vent, under the Pi", f"X {fmt(PX - 15 + i * 6)}", f"Y {fmt(PY)}", "length along Y", "THRU"] for i in range(6)]
    sl += [[f"E{7 + i}", "4 x 22", "strap (armor case)", f"X {fmt(PX + sx_ * 32)}", f"Y {fmt(PY)}", "length along Y", "THRU"] for i, sx_ in enumerate((-1, 1))]
    sh.table("SLOT TABLE (center positions; square-ended)", ["ID", "W x L", "FOR", "X", "Y", "", ""], sl, [10, 20, 36, 22, 22, 26, 12])
    hole_table(sh, tray, "D", "X, Y from center; Z from the underside")
    sheets.append(sh)
    return sheets, G

def main():
    profs = [a for a in sys.argv[1:] if not a.startswith("-")] or ["1din-waveshare79", "2din-7in", "2din-pitouch2"]
    for prof in profs:
        sheets, G = build(prof)
        od = HERE / "drawings" / prof; (od / "dxf").mkdir(parents=True, exist_ok=True)
        with PdfPages(od / f"{prof}_drawings.pdf") as pdf:
            info = pdf.infodict(); info["Title"] = f"Head-unit screen mount - {prof}"; info["Subject"] = DESC.get(prof, prof)
            render(sheets, pdf, od / "dxf")
        print(f"[{prof}] {len(sheets)} sheets -> {od}")

if __name__ == "__main__":
    main()
