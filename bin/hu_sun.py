"""
Sunrise and sunset for the day/night map skins (NOAA solar position equations; good to about a minute in
the lower 48). No internet needed: only the time (the Pi's clock / RTC) and a position.

    elevation(lat, lon, when)        sun's height above the horizon in degrees
    is_night(lat, lon, when)         sun below the horizon (incl. refraction, -0.833 deg)
    rise_set(lat, lon, day_start)    (sunrise, sunset) as epoch seconds for the 24 h from day_start,
                                     either can be None (polar day / night)
"""
import math, time

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
