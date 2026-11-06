"""
Sunrise and sunset for the day/night map skins (NOAA solar position equations; good to about a minute in
the lower 48). No internet needed: only the time (the Pi's clock / RTC) and a position.

    elevation(lat, lon, when)        sun's height above the horizon in degrees
    is_night(lat, lon, when)         sun below the horizon (incl. refraction, -0.833 deg)
    rise_set(lat, lon, day_start)    (sunrise, sunset) as epoch seconds for the 24 h from day_start,
                                     either can be None (polar day / night)
    tz_position(tz)                  a rough position for a time zone name (when there's no GPS yet)
    gps_epoch(iso)                   a GPS time ("2026-11-06T23:41:07.000Z") as epoch seconds
"""
import calendar, math, re, time

# Where the sun is worked out before the head unit has ever had a GPS fix: the middle of where people
# live in each US time zone (a few minutes of sunrise / sunset at most for most of the zone)
TZ_POS = {"America/New_York": (39.5, -78.5), "America/Detroit": (42.5, -84.0), "America/Indiana/Indianapolis": (39.8, -86.2),
          "America/Kentucky/Louisville": (38.2, -85.7), "America/Chicago": (33.5, -94.0), "America/Indiana/Knox": (41.3, -86.6),
          "America/Menominee": (45.1, -87.6), "America/North_Dakota/Center": (47.1, -101.3), "America/Denver": (39.0, -105.5),
          "America/Boise": (43.6, -116.2), "America/Phoenix": (33.4, -112.0), "America/Los_Angeles": (36.5, -119.5),
          "America/Anchorage": (61.2, -149.9), "America/Juneau": (58.3, -134.4), "Pacific/Honolulu": (21.3, -157.9),
          "America/Puerto_Rico": (18.4, -66.1), "US/Central": (33.5, -94.0), "US/Eastern": (39.5, -78.5),
          "US/Mountain": (39.0, -105.5), "US/Pacific": (36.5, -119.5), "America/Mexico_City": (19.4, -99.1),
          "America/Toronto": (43.7, -79.4), "America/Vancouver": (49.3, -123.1), "Europe/London": (51.5, -0.1),
          "Europe/Berlin": (52.5, 13.4), "Europe/Paris": (48.9, 2.35)}

def tz_position(tz):
    return TZ_POS.get(tz or "")

def gps_epoch(iso):
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?Z?$", str(iso or ""))
    if not m: return None
    y, mo, d, h, mi, se = (int(x) for x in m.groups()[:6])
    return calendar.timegm((y, mo, d, h, mi, se)) + float(m.group(7) or 0)

HORIZON = -0.833      # degrees: the sun's upper edge touches the horizon, with refraction

def _jd(t):
    return t / 86400.0 + 2440587.5

def elevation(lat, lon, t=None):
    t = time.time() if t is None else t
    jc = (_jd(t) - 2451545.0) / 36525.0
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - jc * (0.004817 + 0.000014 * jc)) + math.sin(2 * mr) * (0.019993 - 0.000101 * jc)
         + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * jc
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(omega))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app_long)))
    y = math.tan(math.radians(eps / 2)) ** 2
    l0r = math.radians(l0)
    eqtime = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                              - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    utc_min = (t % 86400) / 60.0
    tst = (utc_min + eqtime + 4 * lon) % 1440
    ha = tst / 4 - 180 if tst / 4 >= 0 else tst / 4 + 180
    latr = math.radians(lat)
    cosz = math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(math.radians(ha))
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cosz))))

def is_night(lat, lon, t=None):
    return elevation(lat, lon, t) < HORIZON

def rise_set(lat, lon, day_start):
    """Sunrise and sunset in the 24 hours after day_start (e.g. local midnight)."""
    def crossing(a, b):
        fa = elevation(lat, lon, a) - HORIZON
        for _ in range(40):                                  # bisection to well under a second
            mid = (a + b) / 2; fm = elevation(lat, lon, mid) - HORIZON
            if (fm > 0) == (fa > 0): a, fa = mid, fm
            else: b = mid
        return round((a + b) / 2)
    rise = sett = None
    step = 600
    prev_t, prev = day_start, elevation(lat, lon, day_start) - HORIZON
    for k in range(1, 24 * 3600 // step + 1):
        t = day_start + k * step; cur = elevation(lat, lon, t) - HORIZON
        if prev <= 0 < cur and rise is None: rise = crossing(prev_t, t)
        if prev > 0 >= cur and sett is None: sett = crossing(prev_t, t)
        prev_t, prev = t, cur
    return rise, sett

def local_midnight(t=None):
    t = time.time() if t is None else t
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def corrected_now(fix, now, memo):
    """The time to use: the Pi's clock, unless a GPS fix says it's more than 2 minutes off (no internet
    and no clock battery: the Pi starts at the time it was switched off). memo keeps the offset for a
    while after the GPS drops out (tunnels, garages). Returns (time, offset in seconds or 0)."""
    g = gps_epoch(fix.get("time")) if (fix or {}).get("mode", 0) >= 2 else None
    if g is not None:
        memo["off"] = g - now if abs(g - now) > 120 else 0; memo["at"] = now
    elif memo.get("off") and now - memo.get("at", 0) > 6 * 3600:
        memo["off"] = 0
    return now + memo.get("off", 0), memo.get("off", 0)
