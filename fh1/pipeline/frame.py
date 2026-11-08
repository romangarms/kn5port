"""Where FH1's world sits in each BeamNG level. A point (x, y, z) in FH1's axes, y up, lands at (x - CX, -(z - CZ), y + LIFT), with the .pgeo
and anim_proc records' z already negated, so there it's (x - CX, z + CZ, y + LIFT). build3.py's CHUNKS gives the same centres and half sizes."""
# each level's centre in FH1 x and z, and half its width in metres
LEVELS = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0), 'fh1_clearsprings': (1700.0, 2000.0, 2048.0)}
# Metres added to every FH1 height. BeamNG's physics puts a ground plane at z = 0 in a level without a TerrainBlock, and cars rode on it
# wherever FH1's ground dips below zero, in Carson and on the golf course. FH1's drivable surfaces go down to -46 m, the dam's gorge to
# about -103 m and one floodlight to -145 m; only far backdrop terrain sits lower
LIFT = 150.0
