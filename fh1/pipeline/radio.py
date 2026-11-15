"""FH1's radio as its own BeamNG mod, fh1_radio, kept out of the level mods for its size: Horizon Bass Arena, Pulse and Rocks with their DJs,
jingles and festival interviews, plus the music FH1 plays at race starts. Run on its own at any time: python radio.py [--force]. Needs no level
rebuild; fh1Radio.lua finds the race starts in each level's time trials at load. Format notes are in docs/formats.md, Radio.

media/audio/radio holds RadioSystem.xml (stations, playlists, DJ lines), RadioSoundbankInfo_*.xml (each sample's bank index, length and cue
points in ms) and FSB4 banks of MP3 (not XMA, unlike the ambience banks): Radio_Music.fsb and Radio_VO_<language>.fsb. Bank sample names are cut to 29 characters, so samples are
found by the bank index RadioSoundbankInfo gives. Already converted files are kept unless --force."""
import json, os, re, shutil, struct, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import numpy as np
from PIL import Image
import gamezip, soundscape, tex

RADIO = gamezip.MEDIA + 'audio/radio/'
VO = 'EN'
# FH1's station, its name on air (from the livery decal and tip strings) and its DJ
STATIONS = [('Radio1', 'Horizon Bass Arena', 'ScottTyler'), ('Radio2', 'Horizon Pulse', 'HollyCruz'), ('Radio3', 'Horizon Rocks', 'PhoenixFox')]
# Music FH1 plays from a place rather than the car radio. Autoshow, workshop and paint shop are menu music for places the levels don't have yet
PLACES = {'prerace': 'Radio5_3d_PreRace', 'street': 'Radio9_3d_Streetrace'}
SILENT = {'DJ_Blank', 'Ident_Blank'}
# FH1's HUD radio logos in ui/textures/Horizon.zip, Hud/Radio/: Horizon Rocks is RS_2Eleven, an earlier name. RS_Off is a mute icon.
# Radio1 to 3.xds there are something else
LOGOS = {'station1': 'RS_BassArena', 'station2': 'RS_Pulse', 'station3': 'RS_2Eleven', 'off': 'RS_Off'}
XDS_FORMATS = {0x72: 0x52, 0x73: 0x53, 0x75: 0x54}   # format byte at 0x23 to tex.decode's DXT1, 3, 5
QUALITY = {'music': 4, 'place': 3, 'dj': 3}     # libvorbis -q: about 128 kb/s stereo, 64 kb/s mono
WORKERS = 6
FORCE = '--force' in sys.argv


def xml(name):
    return re.sub(r'<!--.*?-->', '', open(RADIO + name, encoding='utf-8', errors='replace').read(), flags=re.S)


def tags(text, tag):
    """attribute dicts of every <tag .../> in text"""
    return [dict(re.findall(r'(\w+)="([^"]*)"', m)) for m in re.findall(rf'<{tag}\s([^>]*?)/?>', text)]


def section(body, tag):
    m = re.search(rf'<{tag}\b[^>]*>(.*?)</{tag}>', body, re.S)
    return m[1] if m else ''


def fmod(a):
    return a.get('fmodName') or a.get('fmodname')


def bank_info(kind):
    """{sample name: {SoundBankIndex, OffsetSongStart, OffsetIdentStart, OffsetStartNextTrack, FileLength, ...}} as ints"""
    t = open(RADIO + f'RadioSoundbankInfo_{kind}.xml', encoding='utf-8').read()
    return {n: {k: int(v) for k, v in re.findall(r'(\w+)="(-?\d+)"', a)} for n, a in re.findall(r'<Bank Name="([^"]+)">\s*<SoundBankInfo ([^>]*)/>', t)}


system = xml('RadioSystem.xml')
stations = {m[1]: m[2] for m in re.finditer(r'<RadioStation name="(\w+)"[^>]*>(.*?)</RadioStation>', system, re.S)}
sysattr = {k: float(v) for k, v in tags(system, 'System')[0].items() if re.fullmatch(r'-?[\d.]+', v)}
music_info, vo_info = bank_info('Music'), bank_info(f'VO_{VO}')
banks = {'Radio_Music': open(RADIO + 'Radio_Music.fsb', 'rb').read(), f'Radio_VO_{VO}': open(RADIO + f'Radio_VO_{VO}.fsb', 'rb').read()}


def aligned(b):
    """soundscape.samples with each sample's data starting on a 32-byte boundary, as these banks' is"""
    out, at = [], None
    for name, frames, start, size, rate, channels in soundscape.samples(b):
        at = start if at is None else -(-at // 32) * 32
        out.append((name, frames, at, size, rate, channels)); at += size
    return out


samples = {k: aligned(b) for k, b in banks.items()}
jobs = {}   # output path relative to the sound folder -> (bank, sample tuple, quality)


def clip(name, sub):
    """register one sample for conversion: (file, seconds, its info row)"""
    music = sub in ('music', 'place')
    info = (music_info if music else vo_info).get(name)
    if info is None:
        return None
    bank = 'Radio_Music' if music else f'Radio_VO_{VO}'
    s = samples[bank][info['SoundBankIndex']]
    if s[0] != name[:len(s[0])]:
        sys.exit(f'{bank} index {info["SoundBankIndex"]} is {s[0]}, expected {name}')
    file = f'{sub}/{name}.ogg'; jobs[file] = (bank, s, QUALITY[sub])
    return file, round(s[1] / s[4], 3), info


def lines(body, tag, group=None):
    out = []
    for a in tags(section(body, tag), 'SimpleDialogue') + tags(section(body, tag), 'Ident'):
        n = fmod(a)
        if n in SILENT or (group is not None and a.get('group', '0') != group):
            continue
        c = clip(n, 'dj')
        if c:
            f, ln, info = c
            out.append({'file': f, 'len': ln, 'nextAt': round(info['OffsetStartNextTrack'] / 1000, 3) if info['OffsetStartNextTrack'] > 0 else None})
    return out


out_stations = []
for key, name, host in STATIONS:
    body = stations[key]
    regular = lines(body, 'DJRegularDialogue', '0')
    if not regular or not all(Path(r['file']).name.startswith(host) for r in regular):
        sys.exit(f'{key}: expected {host} as DJ, found {sorted({Path(r["file"]).name.split("_")[0] for r in regular})}')
    songs = []
    for a in tags(section(body, 'Playlist'), 'MusicTrack'):
        f, ln, info = clip(fmod(a), 'music')
        songs.append({'file': f, 'len': ln, 'title': a['name'], 'artist': a['artist'],
                      'songStart': round(info['OffsetSongStart'] / 1000, 3), 'identAt': round((info['OffsetIdentStart'] or info['FileLength']) / 1000, 3)})
    out_stations.append({'name': name, 'host': re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', host), 'songs': songs,
                         'idents': lines(body, 'Idents'), 'dj': regular, 'leadIn': lines(body, 'DJFestivalUpdateLeadIn'), 'leadOut': lines(body, 'DJFestivalUpdateLeadOut'),
                         'noRepeat': {t: int(re.search(rf'<{t} noRepeat="(\d+)"', body)[1]) for t in ('Playlist', 'Idents', 'DJRegularDialogue')}})

# festival updates: FH1 plays interviews between a DJ's lead-in and lead-out. Of the filler interviews RadioDJVoxPops.xml lists, only
# AnecdoteInterview4 has English audio; the rivals' opinion-of-the-player interviews suit any time too. Wristband, skill-level and
# defeated-by-player updates follow the story, so they are left out
INTERVIEWS = re.compile(r'RadioInterview_(Anecdote\w*|\w+OpinionOfPlayer)Interview\w*')
updates = [{'file': c[0], 'len': c[1]} for n in sorted(vo_info) if INTERVIEWS.fullmatch(n) for c in [clip(n, 'dj')]]

places = {}
for kind, key in PLACES.items():
    attrs = re.search(rf'<RadioStation name="{key}"([^>]*)>', system)[1]
    places[kind] = {'volume': float(re.search(r'volume="([\d.]+)"', attrs)[1]),
                    'songs': [{'file': c[0], 'len': c[1]} for a in tags(section(stations[key], 'Playlist'), 'MusicTrack') for c in [clip(fmod(a), 'place')] if c]}

mod = Path(beamng.find_mods()) / 'unpacked' / 'fh1_radio'
beamng.write_notice(mod, 'Forza Horizon (2012) soundtrack and radio', 'Microsoft, Playground Games, Turn 10 Studios and the artists and labels of each song')
sound = mod / 'art' / 'sound' / 'fh1radio'
for sub in ('music', 'place', 'dj'):
    (sound / sub).mkdir(parents=True, exist_ok=True)


def convert(item):
    file, (bank, (name, frames, at, size, rate, channels), q) = item
    dst = sound / file
    if dst.exists() and dst.stat().st_size and not FORCE:
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        raw = banks[bank][at:at + size]
        if raw[0] == 0xff and raw[1] & 0xe0 == 0xe0:      # the radio banks are MP3 (sample mode 0x200), frames padded to 4 bytes
            src = Path(tmp) / 'a.mp3'; src.write_bytes(raw)
        else:
            src = Path(tmp) / 'a.xma'; src.write_bytes(soundscape.riff(frames, rate, channels, raw))
        part = Path(tmp) / 'a.ogg'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src), '-c:a', 'libvorbis', '-q:a', str(q), str(part)], check=True)
        shutil.move(part, dst)
    return 1


with ThreadPoolExecutor(WORKERS) as pool:
    made = sum(pool.map(convert, jobs.items()))
stale = [p for p in sound.rglob('*.ogg') if p.relative_to(sound).as_posix() not in jobs]
for p in stale:
    p.unlink()

keep = ('musicLevel', 'dialogueLevel', 'identLevel', 'duckValue', 'duckFadeUpTime', 'duckFadeDownTime', 'stationChangeFadeTimeUser', 'maxTracksBetweenDJ')
(sound / 'radio.json').write_text(json.dumps({'system': {k: sysattr[k] for k in keep}, 'stations': out_stations, 'updates': updates, 'places': places}, indent=1))


def logo(name):
    """a Hud/Radio .xds as RGBA: tiled DXT, size in the Xenos fetch constant"""
    b = gamezip.read('ui/textures/Horizon.zip', f'Hud/Radio/{name}.xds')
    size = struct.unpack('>I', b[0x24:0x28])[0]
    return Image.fromarray(tex.decode(b[52:], (size & 0x1fff) + 1, ((size >> 13) & 0x1fff) + 1, XDS_FORMATS[b[0x23]]), 'RGBA')


app = mod / 'ui' / 'modules' / 'apps' / 'FH1Radio'
app.mkdir(parents=True, exist_ok=True)
for f in ('app.js', 'app.json'):
    shutil.copy(Path('fh1radio_app') / f, app / f)
for stem, name in LOGOS.items():
    logo(name).save(app / f'{stem}.png')
icon = Image.new('RGBA', (250, 160)); a = logo(LOGOS['station1']); icon.alpha_composite(a, ((250 - a.width) // 2, (160 - a.height) // 2)); icon.save(app / 'app.png')

lua = mod / 'lua' / 'ge' / 'extensions'
(lua / 'core' / 'input' / 'actions').mkdir(parents=True, exist_ok=True)
shutil.copy('fh1Radio.lua', lua / 'fh1Radio.lua')
desc = 'Forza Horizon radio on the FH1 levels: Horizon Bass Arena, Horizon Pulse, Horizon Rocks, off'
(lua / 'core' / 'input' / 'actions' / 'fh1radio.json').write_text(json.dumps({
    'fh1RadioNext': {'cat': 'gameplay', 'order': 90, 'ctx': 'tlua', 'onDown': "extensions.hook('onFh1RadioTune', 1)", 'title': 'FH1 radio: next station', 'desc': desc},
    'fh1RadioPrev': {'cat': 'gameplay', 'order': 91, 'ctx': 'tlua', 'onDown': "extensions.hook('onFh1RadioTune', -1)", 'title': 'FH1 radio: previous station', 'desc': desc},
    'fh1RadioLouder': {'cat': 'gameplay', 'order': 92, 'ctx': 'tlua', 'onDown': "extensions.hook('onFh1RadioVolume', 1)", 'title': 'FH1 radio: volume up', 'desc': desc},
    'fh1RadioQuieter': {'cat': 'gameplay', 'order': 93, 'ctx': 'tlua', 'onDown': "extensions.hook('onFh1RadioVolume', -1)", 'title': 'FH1 radio: volume down', 'desc': desc}}, indent=1))
(mod / 'settings' / 'inputmaps').mkdir(parents=True, exist_ok=True)
(mod / 'settings' / 'inputmaps' / 'keyboard_fh1radio.json').write_text(json.dumps({'devicetype': 'keyboard', 'version': 1, 'bindings': [
    {'action': 'fh1RadioNext', 'control': 'rbracket'}, {'action': 'fh1RadioPrev', 'control': 'lbracket'},
    {'action': 'fh1RadioLouder', 'control': 'shift rbracket'}, {'action': 'fh1RadioQuieter', 'control': 'shift lbracket'}]}, indent=1))
(mod / 'scripts' / 'fh1_radio').mkdir(parents=True, exist_ok=True)
(mod / 'scripts' / 'fh1_radio' / 'modScript.lua').write_text('load("fh1Radio")\nsetExtensionUnloadMode("fh1Radio", "manual")\n')

size = sum(p.stat().st_size for p in sound.rglob('*.ogg'))
print('stations:', ', '.join(f"{s['name']} {len(s['songs'])} songs {len(s['dj'])} DJ lines {len(s['idents'])} idents" for s in out_stations))
print('festival updates', len(updates), '| places:', ', '.join(f'{k} {len(v["songs"])} songs' for k, v in places.items()))
print(f'converted {made} of {len(jobs)}, removed {len(stale)} stale, {size / 2 ** 20:.0f} MB ->', mod)
