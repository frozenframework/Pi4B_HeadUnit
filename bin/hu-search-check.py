#!/usr/bin/env python3
"""Why doesn't an address show up in search?   python3 /opt/headunit/bin/hu-search-check.py "1234 Main St, Conroe"
Prints, step by step, what the head unit's search data has for it (nothing is sent anywhere):
  1) whether the search data has street addresses at all (built without them = rebuild with build-routing.py)
  2) how the address was understood   3) whether the street is known   4) the closest mapped house numbers
  5) what search shows."""
import os, sqlite3, sys, time
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent)); import hu_addr
if len(sys.argv) < 2: sys.exit(__doc__)
db = sys.argv[2] if len(sys.argv) > 2 else '/var/lib/headunit/search/search.sqlite'
if not os.path.exists(db): sys.exit(f'0) no search data at {db}: build it with maps/build-routing.py and push it with tools/push-maps.py')
print('0) search data built', time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(db))))
con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
if not hu_addr.has_addresses(con):
    sys.exit('1) search data has NO street addresses (built with --no-addresses, or before addresses existed).\n'
             '   Fix: rebuild on the PC with build-routing.py (addresses are on by default), then push-maps.py.\n'
             '   Until then search shows the street for a typed address.')
print('1) street addresses in search data:', con.execute('select count(*) from addr').fetchone()[0])
p = hu_addr.parse_query(sys.argv[1])
if not p: sys.exit('2) not read as an address: it must start with the house number ("1234 Main St")')
print('2) understood as: number', p[0], '| street', repr(p[2]), '| city words', p[3], '| zip', p[4] or '-')
words, tried, core = hu_addr.tokens(p[2]), [], ''
for cut in range(len(words), 0, -1):
    c = hu_addr.core_street(' '.join(words[:cut]))
    if not c or c in tried: continue
    tried.append(c)
    n = con.execute('select count(*) from addr where core=?', (c,)).fetchone()[0]
    s = con.execute('select count(*) from street_pts where core=?', (c,)).fetchone()[0]
    print(f'3) street "{c}": {n} house numbers, {s} street points')
    if n or s: core = c; break
if not core:
    sys.exit('   The street isn\'t in the map data under that name. Try how a street sign or OpenStreetMap spells it\n'
             '   (e.g. "FM 1488", "Hwy 105", "County Road 123"), or the region you built may not cover it.')
rows = con.execute('select num, street, city from addr where core=? order by abs(num-?) limit 6', (core, p[0])).fetchall()
print('4) closest numbers on that street:', [f'{r[0]} {r[1]} ({r[2] or "no city"})' for r in rows] or 'none')
if rows and rows[0][0] != p[0]:
    print('   Your house number isn\'t in OpenStreetMap; search estimates it between its neighbours (or shows the street).')
print('5) search result:', hu_addr.lookup(con, sys.argv[1]) or 'nothing')
