"""Where the FH1 game files are: the folder holding default.xex and media/, and which FH1 track to read.

From FH1_DUMP, else the path written in dump_path.txt in the work folder (one line; the kn5port app writes it), else an
error that says how to set it. Every script reads the game through DUMP or MEDIA here, and the track through the names below.

The work folder is the one above the script folder: fh1/ for fh1/pipeline, the data scripts read and write as ../.
A track.json there picks a track other than Colorado: {"track": "coloradodirt", "dlc": "<the DLC package's Media folder>"}
for the Rally Expansion, whose files prepare_dlc.py lays out in the work folder's track/. FH1_TRACK overrides it.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.normpath(os.path.join(_HERE, '..'))
_SAVED = os.path.join(WORK, 'dump_path.txt')
_TRACKS = {'colorado': 'Colorado', 'coloradodirt': 'ColoradoDirt'}


def _find():
    p = os.environ.get('FH1_DUMP', '').strip()
    if not p and os.path.exists(_SAVED):
        p = open(_SAVED, encoding='utf8').read().strip()
    if not p:
        raise SystemExit('FH1 game folder not set: set FH1_DUMP, or write its path into dump_path.txt in the work folder. '
                         'It is the folder holding default.xex and media/')
    p = p.replace('\\', '/').rstrip('/')
    if not os.path.exists(p + '/media/tracks/colorado/bin.zip'):
        raise SystemExit(f'{p} is not an extracted FH1 game folder: media/tracks/colorado/bin.zip is missing')
    return p


def _track():
    t = json.load(open(os.path.join(WORK, 'track.json'))) if os.path.exists(os.path.join(WORK, 'track.json')) else {}
    k = os.environ.get('FH1_TRACK', '').strip().lower() or t.get('track', 'colorado')
    if k not in _TRACKS:
        raise SystemExit(f'unknown FH1 track {k!r}: one of {", ".join(_TRACKS)}')
    return k, (os.environ.get('FH1_DLC') or t.get('dlc', '')).replace('\\', '/').rstrip('/')


DUMP = _find()
MEDIA = DUMP + '/media/'
TRACK, DLC = _track()
NAME = _TRACKS[TRACK]                       # the track's MediaName in gamedb.slt, and the stem of its file names
MESH = TRACK + 'out'                        # mesh files are <MESH>.<number>.rmb.bin
if TRACK == 'colorado':
    TRACKDIR = MEDIA + 'tracks/colorado/'
    BINZIP = TRACKDIR + 'bin.zip'
    AUDIO = MEDIA + 'audio/tracks/colorado/'
    DBPATCH = None
else:
    if not DLC:
        raise SystemExit(f'{TRACK} needs the DLC folder: "dlc" in track.json, or FH1_DLC')
    TRACKDIR = WORK.replace('\\', '/') + '/track/'          # prepare_dlc.py extracts it here
    BINZIP = DLC + f'/DLCZips/{NAME}_pri_65.zip'
    AUDIO = DLC + f'/DLCZips/1600_pri_65/Media/audio/Tracks/{NAME}/'
    DBPATCH = TRACKDIR + '1600000_merge.slt'
RIBBON = TRACKDIR + 'Ribbon_00/'
PVS = RIBBON + f'{NAME}_00.pvs'
HEX = RIBBON + f'{NAME}_00.hex'
NAV = TRACKDIR + f'{NAME}.nav'


def db():
    """gamedb.slt, with the track's DLC patch attached as `patch` when it has one"""
    import sqlite3
    c = sqlite3.connect(MEDIA + 'db/gamedb.slt')
    if DBPATCH:
        c.execute('attach database ? as patch', (DBPATCH,))
    return c
