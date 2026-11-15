"""FH1's events and where they happen: gameplay nodes from GameObjs.xml, race routes from TrackRoute<n>.xml, and the event list from
media/db/gamedb.slt with its English names. All positions are in FH1's game axes (x, y up, z), whose z is the mesh z negated."""
import re, sqlite3
import numpy as np
import gamepath, gamezip, strtab

RIBBON = gamepath.RIBBON
NUM = r'(-?[\d.]+(?:e-?\d+)?)'


def nodes():
    """{GameplayID: (position, forward)} for every object in GameObjs.xml, forward being its z axis"""
    t = open(RIBBON + 'GameObjs.xml', errors='replace').read(); out = {}
    for m in re.finditer(rf'GameplayID="([^"]+)">\s*<Pos x="{NUM}" y="{NUM}" z="{NUM}"/>.*?<ZAxis x="{NUM}" y="{NUM}" z="{NUM}"/>', t, re.S):
        out[m[1]] = (np.array([float(m[2]), float(m[3]), float(m[4])]), np.array([float(m[5]), float(m[6]), float(m[7])]))
    return out


def route(n):
    """{kind: [(position, facing, width or None), ...] in index order} for TrackRoute<n>.xml. Kinds include start_location (the grid, pole
    first), route_waypoint, route_checkpoint (with the gate's width), end_race_cannon_left and _right (cannons along the finish), end_race_cannon_trigger (the finish line, with its width).
    Variants such as `_12b` are left out"""
    try:
        t = open(RIBBON + f'TrackRoute{n:03d}.xml', errors='replace').read()
    except FileNotFoundError:
        return {}
    rows = {}
    for m in re.finditer(rf"name='([a-z_]+?)(?:_(\d+))?'(?: width='{NUM}')?>\s*<Transform pos\.x='{NUM}' pos\.y='{NUM}' pos\.z='{NUM}' facing\.x='{NUM}' facing\.y='{NUM}' facing\.z='{NUM}'", t):
        g = m.groups()
        rows.setdefault(m[1], []).append((int(m[2] or 0), np.array([float(v) for v in g[3:6]]), np.array([float(v) for v in g[6:9]]), g[2] and float(g[2])))
    return {k: [r[1:] for r in sorted(v, key=lambda r: r[0])] for k, v in rows.items()}


def races():
    """every event with a route, as dicts: id (Events.HorizonEventID), name, kind (its career type), track (the route's name), route (TrackRoute number), laps, length (FH1's driven metres, one lap)"""
    if gamepath.DBPATCH:
        return dlc_races()
    E, T, C = strtab.read('Events.str'), strtab.read('Tracks.str'), strtab.read('CareerEventTypes.str')
    db = sqlite3.connect(gamezip.MEDIA + 'db/gamedb.slt')
    rows = db.execute('select e.HorizonEventID, e.Name, ct.Name, t.DisplayName, t.RouteId, r.NumLaps, t.Length from Events e join Races r on r.EventId = e.Id '
                      'join Tracks t on t.id = r.TrackId left join CareerEventTypes ct on ct.id = e.CareerTypeId where t.MediaName = "Colorado" order by e.HorizonEventID')
    return [dict(id=i, name=strtab.lookup(E, n), kind=strtab.lookup(C, k), track=strtab.lookup(T, d), route=r, laps=laps, length=length) for i, n, k, d, r, laps, length in rows]


def dlc_races():
    """A DLC track's races. The Rally Expansion has no Events or Races rows: each route is a Tracks row of its database patch, a one-lap stage"""
    T = strtab.read('Tracks.str', dlc=True)
    rows = gamepath.db().execute('select id, DevName, DisplayName, RouteId, Length from patch.Tracks where MediaName = ? and RouteId > 0 and DevName not like "%TEST%" order by RouteId', (gamepath.NAME,))
    return [dict(id=dev, name=strtab.lookup(T, d) or dev.replace('_', ' ').title(), kind='Rally', track=strtab.lookup(T, d) or dev, route=r, laps=1, length=length)
            for _, dev, d, r, length in rows]


def race_routes():
    """the routes the track's races drive, route 0 (free roam) left out"""
    if gamepath.DBPATCH:
        return {r['route'] for r in dlc_races()}
    return {int(r_) for (r_,) in sqlite3.connect(gamezip.MEDIA + 'db/gamedb.slt').execute(
        'select t.RouteId from Events e join Races r on r.EventId = e.Id join Tracks t on t.id = r.TrackId where t.MediaName = ?', (gamepath.NAME,))} - {0}
