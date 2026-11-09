"""Where the FH1 game files are: the folder holding default.xex and media/.

From FH1_DUMP, else the path written in fh1/dump_path.txt (one line; the kn5port app writes it), else an error that
says how to set it. Every script reads the game through DUMP or MEDIA here.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVED = os.path.join(_HERE, '..', 'dump_path.txt')


def _find():
    p = os.environ.get('FH1_DUMP', '').strip()
    if not p and os.path.exists(_SAVED):
        p = open(_SAVED, encoding='utf8').read().strip()
    if not p:
        raise SystemExit('FH1 game folder not set: set FH1_DUMP, or write its path into fh1/dump_path.txt. '
                         'It is the folder holding default.xex and media/')
    p = p.replace('\\', '/').rstrip('/')
    if not os.path.exists(p + '/media/tracks/colorado/bin.zip'):
        raise SystemExit(f'{p} is not an extracted FH1 game folder: media/tracks/colorado/bin.zip is missing')
    return p


DUMP = _find()
MEDIA = DUMP + '/media/'
RIBBON = MEDIA + 'tracks/colorado/Ribbon_00/'
