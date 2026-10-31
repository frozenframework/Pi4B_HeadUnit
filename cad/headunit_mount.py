"""
Parametric 3D-printed mount: Waveshare 7.9" bar screen over a single-DIN opening + Pi 4 sled.
Units: mm. Built for ASA on a Bambu Lab P1S (enclosed), no supports needed.

  1_bezel          front frame, prints FACE DOWN on the textured plate (that's the visible finish)
  2_spacer_ring    room behind the screen for its driver board; clamps the glass into the bezel
  3_back_and_sled  plate + single-DIN tube; prints plate-down, tube straight up
  4_pi_tray        flat tray for the Pi 4 + power/relay modules; slides into the tube
  Stack: bezel <- screen <- spacer ring <- back plate, six M3 screws from the back into bezel inserts.

!! The screen numbers below are estimates from comparable 7.84"/7.9" 1280x400 panels. Measure your
!! screen with calipers when it arrives and edit SCREEN_* (or edit the STEP in Inventor).
Run:  pip install cadquery  &&  python headunit_mount.py
"""
import sys
import cadquery as cq

# ---------------- profiles (pick one on the command line) ----------------
#   python headunit_mount.py              -> 1din-waveshare79   (F-250, Miata)
#   python headunit_mount.py 2din-7in     -> 2din-7in           (2017 Frontier, tier 3: 900-nit Orient Display)
#   python headunit_mount.py 2din-pitouch2 -> 2din-pitouch2     (2017 Frontier, tiers 1-2: Pi Touch Display 2)
PROFILES = {
    "1din-waveshare79": dict(   # Waveshare 7.9" 1280x400 bar, single-DIN opening
        SCREEN_W=205.8, SCREEN_H=67.8, SCREEN_T=7.8, VIEW_W=190.1, VIEW_H=59.4, PCB_DEPTH=9.5, DIN_W=178.0, DIN_H=50.0),
    "2din-pitouch2": dict(      # Raspberry Pi Touch Display 2 (7", DSI), double-DIN opening - Frontier tiers 1-2
        SCREEN_W=189.32, SCREEN_H=120.24, SCREEN_T=10.0, VIEW_W=155.0, VIEW_H=88.0, PCB_DEPTH=8.0, DIN_W=178.0, DIN_H=100.0),
    "2din-7in": dict(           # 7" 1024x600 (Orient Display AFY1024600A2-7.0INTH-C-HDMI), double-DIN opening
        SCREEN_W=164.8, SCREEN_H=116.2, SCREEN_T=12.0, VIEW_W=154.2, VIEW_H=85.9, PCB_DEPTH=8.0, DIN_W=178.0, DIN_H=100.0),
}
PROFILE = next((a for a in sys.argv[1:] if a in PROFILES), "1din-waveshare79")
P = PROFILES[PROFILE]

# ---------------- screen (VERIFY with calipers when it arrives) ----------------
SCREEN_W, SCREEN_H = P["SCREEN_W"], P["SCREEN_H"]   # cover-glass / module outline
SCREEN_T = P["SCREEN_T"]                            # glass + LCD
VIEW_W, VIEW_H = P["VIEW_W"], P["VIEW_H"]           # active (lit) area
PCB_DEPTH = P["PCB_DEPTH"]                          # room behind the LCD for the driver board

# ---------------- fit / print ----------------
CLR = 0.5          # clearance per side around the glass
BORDER = 8.0       # bezel frame width outside the glass pocket
LIP = 2.0          # thickness of the front lip over the glass
LIP_OVER = 2.5     # how far the lip overlaps the glass edge
R_OUT = 6.0        # outer corner radius
INSERT_D, INSERT_L = 4.0, 6.0          # M3 heat-set insert hole (M3 x 5.7 x 4.6 OD inserts)
M3_CLR, M3_HEAD, M3_HEAD_H = 3.4, 6.2, 3.2
WALL = 2.4         # 6 perimeters of a 0.4 nozzle

# ---------------- single DIN (ISO 7736) ----------------
DIN_W, DIN_H = P["DIN_W"], P["DIN_H"]  # tube outer: single-DIN opening 182 x 53, double-DIN 182 x 103
SLED_L = 110.0                         # tube depth behind the screen
PLATE_T = 3.0

GLASS_POCKET_W, GLASS_POCKET_H = SCREEN_W + 2 * CLR, SCREEN_H + 2 * CLR
OUT_W, OUT_H = GLASS_POCKET_W + 2 * BORDER, GLASS_POCKET_H + 2 * BORDER
BEZEL_D = LIP + SCREEN_T + 0.3
assert SCREEN_W - 2 * LIP_OVER > VIEW_W and SCREEN_H - 2 * LIP_OVER > VIEW_H, "lip would cover the picture"

# six screw positions (corners + middle of the long sides), on the frame centerline
ins = BORDER / 2
SCREWS = [(sx * (OUT_W / 2 - ins - 2), sy * (OUT_H / 2 - ins)) for sx in (-1, 1) for sy in (-1, 1)] + [(0, OUT_H / 2 - ins), (0, -(OUT_H / 2 - ins))]

def rounded_box(w, h, d, r):
    return cq.Workplane("XY").rect(w, h).extrude(d).edges("|Z").fillet(r)

# ---------------- 1: bezel (front face at z=0, printed face down) ----------------
bezel = rounded_box(OUT_W, OUT_H, BEZEL_D, R_OUT)
bezel = bezel.edges("<Z").chamfer(0.8)                                    # soft front edge, prints as a 45 deg slope
window = cq.Workplane("XY").rect(SCREEN_W - 2 * LIP_OVER, SCREEN_H - 2 * LIP_OVER).extrude(LIP).edges("|Z").fillet(2.0)
bezel = bezel.cut(window)
pocket = cq.Workplane("XY").workplane(offset=LIP).rect(GLASS_POCKET_W, GLASS_POCKET_H).extrude(BEZEL_D).edges("|Z").fillet(1.5)
bezel = bezel.cut(pocket)
for x, y in SCREWS:
    bezel = bezel.cut(cq.Workplane("XY").workplane(offset=BEZEL_D - INSERT_L).center(x, y).circle(INSERT_D / 2).extrude(INSERT_L))

# ---------------- 2: spacer ring (room for the screen's driver board; clamps the glass) ----------------
z0 = BEZEL_D                      # sits on the bezel's back face
ring_d = PCB_DEPTH
ring = rounded_box(OUT_W, OUT_H, ring_d, R_OUT).translate((0, 0, z0))
ring = ring.cut(cq.Workplane("XY").rect(SCREEN_W - 6, SCREEN_H - 6).extrude(ring_d).edges("|Z").fillet(2).translate((0, 0, z0)))
# cable exits on both ends, in case the screen's HDMI/USB ports point sideways
for sx in (-1, 1):
    ring = ring.cut(cq.Workplane("XY").center(sx * (SCREEN_W / 2 - 3), 0).rect(12, 30).extrude(ring_d).translate((0, 0, z0)))
for x, y in SCREWS:
    ring = ring.cut(cq.Workplane("XY").center(x, y).circle(M3_CLR / 2).extrude(ring_d).translate((0, 0, z0)))

# ---------------- 3: back plate + DIN tube (prints plate-down, tube straight up) ----------------
back = rounded_box(OUT_W, OUT_H, PLATE_T, R_OUT).translate((0, 0, z0 + ring_d))
tube = (cq.Workplane("XY").rect(DIN_W, DIN_H).extrude(SLED_L).edges("|Z").fillet(2)
        .faces(">Z").shell(-WALL)).translate((0, 0, z0 + ring_d + PLATE_T))   # open at the back
back = back.union(tube)
back = back.cut(cq.Workplane("XY").rect(DIN_W - 2 * WALL, DIN_H - 2 * WALL).extrude(PLATE_T + 0.1).translate((0, 0, z0 + ring_d - 0.05)))
tz = z0 + ring_d + PLATE_T
# gussets in the corner between the plate and the tube (stiffen the screen against bumps)
for sy in (-1, 1):
    for x in ((-55, 0, 55) if OUT_H / 2 - DIN_H / 2 >= 10 else ()):
        g = cq.Workplane("YZ").polyline([(sy * DIN_H / 2, tz), (sy * (DIN_H / 2 + 10), tz), (sy * DIN_H / 2, tz + 12)]).close().extrude(WALL / 2, both=True)
        back = back.union(g.translate((x, 0, 0)))
if OUT_W / 2 - DIN_W / 2 >= 10:          # side gussets only where the plate is wider than the tube
    for sx in (-1, 1):
        g = cq.Workplane("XZ").polyline([(sx * DIN_W / 2, tz), (sx * (DIN_W / 2 + 10), tz), (sx * DIN_W / 2, tz + 12)]).close().extrude(WALL / 2, both=True)
        back = back.union(g)
# screw clearance + counterbores (screws go in from the back into the bezel's inserts)
for x, y in SCREWS:
    back = back.cut(cq.Workplane("XY").center(x, y).circle(M3_CLR / 2).extrude(PLATE_T).translate((0, 0, z0 + ring_d)))
    back = back.cut(cq.Workplane("XY").center(x, y).circle(M3_HEAD / 2).extrude(M3_HEAD_H).translate((0, 0, z0 + ring_d + PLATE_T - M3_HEAD_H)))
# side bracket slots (M5) to match a dash kit / DIN cage bracket
for sx in (-1, 1):
    for zc in (35, 80):
        slot = cq.Workplane("YZ").slot2D(30, 5.5, 90).extrude(WALL * 3, both=True).translate((sx * DIN_W / 2, 0, tz + zc))
        back = back.cut(slot)
# fan / vent slots in the top wall over the Pi (vertical when printed: no supports)
for i in range(9):
    back = back.cut(cq.Workplane("XZ").center(-30 + i * 7.5, tz + 60).rect(3.0, 60).extrude(WALL * 3, both=True).translate((0, DIN_H / 2, 0)))
# tray screw holes in the floor
TRAY_HOLES = [(sx * 70, zc) for sx in (-1, 1) for zc in (25, 95)]
for x, zc in TRAY_HOLES:
    back = back.cut(cq.Workplane("XZ").center(x, tz + zc).circle(M3_CLR / 2).extrude(WALL * 3, both=True).translate((0, -DIN_H / 2, 0)))

# ---------------- 4: Pi tray ----------------
TRAY_W, TRAY_L, TRAY_T = DIN_W - 2 * WALL - 0.6, SLED_L - 4, 2.4
tray = cq.Workplane("XY").rect(TRAY_W, TRAY_L).extrude(TRAY_T)
for sx in (-1, 1):   # stiffening side rails
    tray = tray.union(cq.Workplane("XY").center(sx * (TRAY_W / 2 - TRAY_T / 2), 0).rect(TRAY_T, TRAY_L).extrude(8))
# Pi 4: 85 x 56 board, holes 58 x 49 (3.5 from the edges). USB/Ethernet end toward the open back of the tube.
PI_X = -TRAY_W / 2 + 10 + 28        # board center (left side of the tray), board long axis along the tube
PI_Y = TRAY_L / 2 - 4 - 42.5
pi_holes = [(PI_X - 24.5, PI_Y + 42.5 - 3.5), (PI_X + 24.5, PI_Y + 42.5 - 3.5), (PI_X - 24.5, PI_Y + 42.5 - 3.5 - 58), (PI_X + 24.5, PI_Y + 42.5 - 3.5 - 58)]
for x, y in pi_holes:
    tray = tray.union(cq.Workplane("XY").center(x, y).circle(3.2).extrude(TRAY_T + 8))
    tray = tray.cut(cq.Workplane("XY").center(x, y).circle(1.6).extrude(TRAY_T + 8.1))     # M2.5 heat-set insert / self-tap
# vent slots under the Pi
for i in range(6):
    tray = tray.cut(cq.Workplane("XY").center(PI_X - 15 + i * 6, PI_Y).rect(3, 60).extrude(TRAY_T))
# strap slots for an armor-style case (alternative to the standoffs)
for sx in (-1, 1):
    tray = tray.cut(cq.Workplane("XY").center(PI_X + sx * 32, PI_Y).rect(4, 22).extrude(TRAY_T))
# module mounting grid (relay module, converters, opto board): M3 holes on a 10 mm grid
for gx in range(5):
    for gy in range(8):
        tray = tray.cut(cq.Workplane("XY").center(PI_X + 45 + gx * 10, -TRAY_L / 2 + 14 + gy * 10).circle(1.6).extrude(TRAY_T))
# matching holes for the tube floor
for x, zc in TRAY_HOLES:
    tray = tray.cut(cq.Workplane("XY").center(x, zc - SLED_L / 2 - 2).circle(M3_CLR / 2).extrude(TRAY_T))

if __name__ == "__main__":
    import os
    od = os.path.join("print", PROFILE); os.makedirs(od, exist_ok=True)
    parts = {"1_bezel": bezel, "2_spacer_ring": ring, "3_back_and_sled": back, "4_pi_tray": tray}
    for name, p in parts.items():
        cq.exporters.export(p, f"{od}/{name}.step")
        cq.exporters.export(p, f"{od}/{name}.stl", tolerance=0.02, angularTolerance=0.1)
        bb = p.val().BoundingBox()
        print(f"[{PROFILE}] {name}: {bb.xlen:.1f} x {bb.ylen:.1f} x {bb.zlen:.1f} mm  volume {p.val().Volume()/1000:.0f} cm3")
    asm = cq.Assembly()
    asm.add(bezel, name="bezel", color=cq.Color(0.1, 0.1, 0.1))
    asm.add(ring, name="spacer_ring", color=cq.Color(0.15, 0.15, 0.15))
    asm.add(back, name="back_and_sled", color=cq.Color(0.2, 0.2, 0.25))
    asm.add(tray, name="pi_tray", loc=cq.Location((0, -DIN_H / 2 + WALL + 0.2, tz + SLED_L / 2 + 2), (1, 0, 0), -90), color=cq.Color(0.0, 0.6, 0.7))
    asm.save(f"{od}/headunit_mount_assembly.step")
