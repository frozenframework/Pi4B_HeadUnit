# Pi4B Headunit

CarPlay / Android Auto + themed offline navigation on a Raspberry Pi 4B.

![Map skins](docs/Map_Skins_Preview.png)

Parts lists (with links and wiring pinouts):
- F-250 and Miata, three budget tiers: [`docs/Pi_HeadUnit_Parts_List.xlsx`](docs/Pi_HeadUnit_Parts_List.xlsx)
- 2017 Nissan Frontier, double-DIN + factory backup camera: [`docs/Frontier_2DIN_Parts_List.xlsx`](docs/Frontier_2DIN_Parts_List.xlsx)

| Vehicle | Profile | Screen | Extras |
|---|---|---|---|
| 1997 Ford F-250 7.3L (crew cab) | `f250` | Waveshare 7.9" 1280×400 over the single-DIN opening | 60 s key-off delay for the diesel |
| 1997 Mazda Miata | `miata` | Waveshare 7.9" 1280×400 over the single-DIN opening | 2 speakers, power antenna |
| 2017 Nissan Frontier SV | `frontier` | 7" 1024×600 900-nit Orient Display, double-DIN | **factory backup camera**, steering-wheel controls |

All run on a Raspberry Pi 4B (4 GB).

| Piece | What does it |
|---|---|
| CarPlay (wired and wireless) | Hudiy + Carlinkit CPC200-CCPA dongle |
| Android Auto (wired and wireless) | Hudiy, built in (your passengers' Android phones) |
| Themed offline navigation, four map skins | `apps/nav`: MapLibre + Protomaps map tiles + Valhalla routing |
| iPod Nano (1st gen and later) / USB music | `apps/music`: MPD reads the song tags, so scrambled iPod file names never show |
| AM/FM radio | `apps/radio` (AM + FM via RTL-SDR Blog V4), plus Hudiy's own FM player with RDS |
| Settings: skin, voice, units, maps, test logs | `apps/theme` |
| Ignition-safe power (no SD card corruption) | `bin/hu-power.py` + relay latch, or CarPiHAT |

The four skins are **original designs** inspired by the mood of driving-game maps. They copy no
game artwork, logos or fonts: Neon Overdrive (cyberpunk neon), Sunset Junction (sunny open-world),
Street Heat (underground street racing), After Midnight (deep-blue night city).

---

## 1. Install: pick one way

### A. Ready-to-flash image (recommended)
**No Docker needed:** download *Raspberry Pi OS (64-bit) with desktop* (`.img.xz`) into `out/cache/`, then:
```bash
python3 -m pip install pyfatfs
python3 image/make-image.py --base out/cache/<raspios file>.img.xz --vehicle both [--hudiy <installer>]
```
This only edits the FAT boot partition: it copies this project to `bootfs/headunit/package` and adds a
one-time `firstrun.sh` hook to `cmdline.txt`. On the first power-up that hook installs the package to
`/opt/headunit`, enables the first-boot service, removes itself, and reboots. The Pi then reboots
once more when setup finishes.

**Or with Docker Desktop** (edits the Linux partition directly):
```bash
./image/build-image.sh --vehicle all --hudiy ~/Downloads/<the Hudiy installer from your email>
```
This creates `out/headunit-f250-YYYYMMDD.img.xz` and `out/headunit-miata-YYYYMMDD.img.xz`.
The script downloads the newest official Raspberry Pi OS (64-bit, Desktop), checks its SHA-256,
and adds this folder plus a one-time first-boot service. **The OS itself stays unmodified until
first boot**, because Hudiy needs to be installed on a clean system.

1. Flash with **Raspberry Pi Imager**: *Choose OS → Use custom*. In *OS customisation*, set your
   username, password, Wi-Fi, time zone (America/Chicago) and keyboard.
2. Before you eject, open the **bootfs** drive → `headunit/headunit.conf`. Check the vehicle, tier and
   map region. The Hudiy installer goes in `headunit/hudiy/`.
3. Boot the Pi on the bench, within range of Wi-Fi. First boot takes **15–45 minutes**: it installs
   Hudiy, then the add-ons, then downloads maps, and reboots on its own. You can read the log at
   `bootfs/headunit/firstboot.log`.

### B. Stock Raspberry Pi OS + setup script (to see each step)
1. Flash **Raspberry Pi OS (64-bit) with desktop** using Imager, with the same customisation as above.
2. Install Hudiy following its email instructions.
3. Copy this folder to the Pi and run:
```bash
sudo ./setup.sh --vehicle f250 --tier 2          # or: --vehicle miata, --tier 1|2|3, --region us
```
`setup.sh` runs the short, commented stages in `setup.d/` in order:

| Stage | What it changes |
|---|---|
| `10-packages` | apt packages (mpd, gpsd, espeak-ng, hfsplus…) and a Python venv with pyvalhalla |
| `20-display` | screen timings, rotation (console + kanshi), touch mapped to HDMI-A-1, no blanking, desktop autologin |
| `30-power` | `gpio-poweroff` hold pin, I2C off (GPIO3 is the ignition sense), power daemon |
| `40-audio` | USB audio output preferred over HDMI (WirePlumber rule) |
| `50-gps` | gpsd with USB auto-detect |
| `60-media` | iPod/USB auto-mount (read-only) + MPD as a user service |
| `70-radio` | RTL-SDR (tier 3 only) |
| `80-nav` | data folders, bridge service, map updater, a two-line sudoers rule |
| `75-camera` | backup camera (Frontier): v4l/GStreamer, `/dev/hu-rearcam` for the USB capture stick, set to NTSC |
| `77-swc` | steering-wheel controls (Frontier): SPI on, `python3-spidev`, `headunit-swc` service |
| `90-hudiy` | adds a **Drive** menu to Hudiy (Navigation, iPod & USB, AM/FM Radio, Head Unit Settings); Android Auto GPS → gpsd |

Every stage can be run again safely: system files are only edited inside `# >>> headunit:` blocks.
To redo one stage: `sudo /opt/headunit/setup.sh --only 20-display`.
To change settings later: edit `/etc/headunit/headunit.conf`, then re-run `sudo /opt/headunit/setup.sh`.

---

## 2. Maps (whole US or Texas + neighbors; update any time)

There are two kinds of map data:

| Data | Made where | How to update |
|---|---|---|
| **Map display** (the themed picture) | Downloaded by the Pi from the daily OpenStreetMap build at Protomaps, cut to your region | *Settings → Maps → Update maps now* on Wi-Fi, or `sudo systemctl start headunit-update-maps` |
| **Routing + search** | Built on your Mac or PC (too heavy for the Pi) | `maps/build-routing.py`, then a USB stick |

Building routing + search on a computer:
```bash
python3 -m venv hu-maps && source hu-maps/bin/activate      # Windows: hu-maps\Scripts\activate
pip install pyvalhalla==3.9.1 osmium
python3 maps/build-routing.py --region texas-plus           # TX, LA, OK, NM, AR (about 30-60 min)
python3 maps/build-routing.py --region us --no-addresses    # whole US: use the Windows PC (16 GB+ RAM, a few hours)
```
Copy the resulting `HEADUNIT_MAPS` folder onto any USB stick and plug it into the head unit. It
imports by itself. Sizes (estimates): texas-plus is about 3–6 GB in total, and the whole US is about
30–45 GB (the 256 GB card in tier 3). Regions are defined in `maps/regions.json`.

---

## 3. Wiring

Full harness pinouts and the parts for each tier are in the parts-list spreadsheet (tabs
*Wiring F-250* and *Wiring Miata*). The Pi side:

| Pi pin | Use |
|---|---|
| GPIO3 (pin 5) | ACC / ignition sense from the optocoupler (active low). It also wakes a halted Pi |
| GPIO4 (pin 7) | Dash-light sense (night dimming) |
| GPIO26 (pin 37) | Power hold → relay module CH1 (`gpio-poweroff`; drops only after a clean halt) |
| GPIO27 (pin 13) | Amp remote turn-on → relay CH2 |
| GPIO22 (pin 15) | Power antenna → relay CH3 (raised only while the radio plays) |
| GPIO5 (pin 29) | Reverse sense (Frontier) → shows the backup camera through Hudiy's API |

How the power sequence works:
- Key on: the relay latches and the Pi boots. The amp switches on 6 seconds later, so there's no pop.
- Key off: the amp cuts out right away, then a countdown starts. It's **60 s on the F-250**, so a
  stall, restart or long glow-plug wait doesn't power-cycle the head unit, and **20 s on the Miata**.
  When the countdown ends, the Pi shuts down cleanly and then releases its own power.
- The power daemon only arms after it has seen the key **on** once, so a Pi on the bench never
  shuts itself off.
- **Tier 3 (CarPiHAT):** confirm the CarPiHAT pin numbers in TJD's manual and put them in
  `/etc/headunit/headunit.conf` before the first drive.
- **Bench testing without the ignition wire:** set `POWER_MODE=none`.

Before connecting anything, **meter every factory wire** with the key off, the key on, and the
lights on.

---

### Backup camera (Frontier)
Factory Nissan camera → Axxess AXBUCS-NI326V (powers the camera with 6 V) → RCA → USB capture stick
(UTV007/STK1160) → Hudiy's reverse-camera view. When reverse is selected, `hu-power` tells Hudiy to show
the camera right away (`SetReverseCameraStatus` over Hudiy's local API, port 44405). It hides the camera
2 s after you leave reverse, so a three-point turn doesn't flicker. The camera only shows while the key is on.

### Steering-wheel controls (Frontier)
The wheel buttons are a resistor ladder: each button connects a key wire to ground through a
different resistor. Each key wire gets a 1 kΩ pull-up to 3.3 V, so every button produces its own
voltage, which an MCP3008 ADC on the SPI bus reads.

Think of it like a dimmer switch with fixed clicks: the Pi doesn't see "which button", only "how much
voltage", so it learns the click positions for your wheel once instead of trusting a generic table.
That's also why it works on other cars with resistive wheel controls.

```bash
sudo /opt/headunit/bin/hu-swc.py --raw      # wiring check: live values; each button should move one channel
sudo /opt/headunit/bin/hu-swc.py --learn    # key ON; press and hold each button when asked
sudo systemctl restart headunit-swc
```
Default actions: volume up/down (repeat while held), next/previous track, mode → home screen,
mute, answer, hang up. Change any of them in `/etc/headunit/headunit.conf`, for example
`SWC_ACTION_MODE=applications_menu`.

## 4. 3D-printed mount (`cad/`)

`python cad/headunit_mount.py` builds the single-DIN set (F-250, Miata) into `cad/print/1din-waveshare79/`.
`python cad/headunit_mount.py 2din-7in` builds the Frontier double-DIN set into `cad/print/2din-7in/`.

All four parts print **without supports** on the P1S in ASA:

| Part | Orientation | Notes |
|---|---|---|
| `1_bezel` | Face **down** on the textured PEI plate | The visible finish. Six M3 heat-set inserts in the back |
| `2_spacer_ring` | Flat | Room for the screen's driver board; clamps the glass |
| `3_back_and_sled` | Plate down, tube straight up | Add a 5 mm brim (tall ASA part). M5 slots for your dash bracket |
| `4_pi_tray` | Flat | Pi 4 standoffs (M2.5), strap slots for an armor case, M3 grid for the relay/converter modules |

Print settings: Bambu ASA profile, 0.2 mm layers, 6 walls (2.4 mm), 25% gyroid infill, enclosure
closed, aux fan off, and let the part cool in the chamber. Put the inserts in at about 245 °C.

**Measure your screen with calipers first.** The screen numbers at the top of
`cad/headunit_mount.py` are estimates from comparable panels. Change them and re-run
`python headunit_mount.py`, or edit the STEP files in Inventor.

---

## 5. Test drives and tuning

Everything is logged to `~/headunit-data/usage/*.jsonl`: taps, routes, reroutes, skin changes,
map frame rate, errors, and power events. To collect the logs, plug in a USB stick **named
HUEXPORT**; the logs copy onto it within a few seconds. Wait 10 seconds before you unplug it.

Bench-test the navigation without driving: open
`http://127.0.0.1:8765/apps/nav/index.html?sim=15`, long-press the map, and tap **Go**. The car
then drives the route by itself at about 35 mph.

---

## 6. Security notes
- The bridge listens on **127.0.0.1 only**, so nothing on Wi-Fi can reach it.
- The only root rights the head unit user gets (`/etc/sudoers.d/headunit`) are to start the map
  updater and to import a map pack from `/media/hu/*/HEADUNIT_MAPS`.
- USB drives mount **read-only** with `noexec,nosuid,nodev`. The one exception is a stick labeled
  HUEXPORT, which is used to export logs.
- Hudiy is closed source. Everything in this folder is plain text you can read.

## 7. Rebuilding the web assets (only if you change themes)
```bash
cd tools && npm install && npm run build      # fonts, styles, sprites, maplibre/pmtiles
```
Skin colors live in `tools/themes.json`.

## Credits and licenses
Map data © OpenStreetMap contributors (ODbL), tiles via Protomaps. Routing: Valhalla (MIT).
Map rendering: MapLibre GL JS (BSD-3). Fonts: Rajdhani, Barlow Condensed, Saira Condensed, Exo 2,
Orbitron (SIL OFL). Hudiy is a separate commercial product (wiboma).
