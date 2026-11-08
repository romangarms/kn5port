"""FH1's placed sounds and reverb zones, from the *.soundscape tiles. Used by levelinfo.py for positions and ambience.py for audio. Format is in docs/formats.md.
Near, far, level and timing below are chosen by hand: the real distances are in FH1's .fev files, which aren't decoded."""
import glob, re, struct
import xml.etree.ElementTree as ET
import numpy as np
import zr

TILES = '../x/*.soundscape'
# Group name: FH1 events, sound bank, sample name pattern (names are cut to 29 characters), near, far, level
LOOPS = {'river': (('Default_River',), 'AMB_Default', r'Natural_River_02', 15, 140, .8),
         'crickets': (('Default_Crickets_Bed',), 'AMB_Default', r'Wildlife_Crickets_Day_Bed_01', 20, 100, .25),
         'waterfall_big': (('Mountains_Waterfall_MegaBig', 'Mountains_Waterfall_Big'), 'AMB_Mountains', r'Waterfall_large_02', 40, 380, 1),
         'waterfall_small': (('Mountains_Waterfall_Small',), 'AMB_Mountains', r'Waterfall_Small_01', 20, 160, .7),
         'dam': (('Mountains_Dam_Ambience',), 'AMB_Mountains', r'Industrial_Dam_Ambience_01', 50, 400, .9),
         'air_con': (('Mountains_Air_Con', 'MainTown_Air_Con'), 'AMB_Main_Town', r'Insustrial_Air_Con_01', 6, 45, .35),
         'fountain': (('MainTown_Fountain',), 'AMB_Main_Town', r'Insustrial_Fountain_01', 10, 80, .6),
         'wind_turbine': (('MainTown_Wind_Turbine',), 'AMB_Main_Town', r'Wind_Turbine_02', 25, 180, .5),
         'mining_b': (('Reservoir_Mining_Machine_B',), 'AMB_Reservoir', r'Distant_Mining_Machine_Groani', 60, 450, .6),
         'mining_c': (('Reservoir_Mining_Machine_C',), 'AMB_Reservoir', r'Industry_Mining_Machine_01', 40, 350, .6),
         'mining_d': (('Reservoir_Mining_Machine_D',), 'AMB_Reservoir', r'Industry_Mining_Machine_02', 40, 350, .6)}
# Group name: as above, then shortest and longest wait between plays in seconds
ONESHOTS = {'dogs': (('Default_Dogs', 'MainTown_Dogs'), 'AMB_Main_Town', r'AMB_Dog_Day', 30, 260, .45, 12, 35),
            'car_door': (('MainTown_Car_Door_Slam', 'Mountains_Car_Door_Slam'), 'AMB_Main_Town', r'Car_Door_Slam', 15, 130, .4, 15, 40),
            'car_horn': (('MainTown_Car_Horn_Distant', 'Mountains_Car_Horn_Distant'), 'AMB_Main_Town', r'(?i).*horn', 60, 420, .3, 25, 60),
            'house_door': (('Default_House_Door_Slam', 'Mountains_House_Door_Slam'), 'AMB_Default', r'AMB_Town_House_Door_Slam', 15, 120, .4, 20, 50),
            'rusty_metal': (('Mountains_Rusty_Metal', 'Plains_Rusty_Metal'), 'AMB_Plains', r'AMB_Rusty_Metal', 12, 100, .45, 10, 28),
            'rusty_squeak': (('Mountains_Rusty_Squeaks', 'Plains_Rusty_Squeaks'), 'AMB_Plains', r'AMB_Metal_Squeak', 12, 100, .4, 14, 34),
            'coyote': (('RedRock_Coyote',), 'AMB_Red_Rock', r'Wildlife_Other_Coyote', 120, 1000, .5, 30, 75),
            'hawk': (('RedRock_Hawk',), 'AMB_Red_Rock', r'Wildlife_Birds_Indv_Bird_21', 80, 600, .5, 25, 65),
            'rock_fall': (('RedRock_Distant_Rock_Fall',), 'AMB_Red_Rock', r'AMB_Distant_Rock_Fall', 120, 1000, .5, 40, 100),
            'squirrel': (('Foothills_Squirrel', 'RedStone_Squirrel'), 'AMB_Redstone', r'Wildlife_Other_DSquirrel', 10, 110, .4, 12, 32),
            'chainsaw': (('RedStone_Chainsaw', 'Foothills_Chainsaw'), 'AMB_Redstone', r'Chainsaw', 50, 420, .35, 18, 45),
            'wind_chimes': (('RedStone_Wind_Chimes',), 'AMB_Redstone', r'AMB_Town_Wind_Chimes', 8, 60, .35, 10, 25)}
REVERB_TEMPLATES = {'12', '17', '18', '19'}          # tunnel, underpass, multi-storey car park, warehouse in colorado_reverb.xml
# FH1's triggerable events: sounds that ANIM models name in their bones' tags ("Audio: path=Triggerable_Events/... eventfile=TRG_Fan_Generator
# oneshot=False"), the rides, sprinklers, geyser, fireworks and aircraft. Triggerable_Events.fev names each event's sound definition and its source
# .wav, which matches the bank's sample names (cut to 29 characters): event -> sample name pattern
TRIGGER_BANK = 'Triggerable_Events'
TRIGGERS = {'TRG_Fan_Generator': r'TRG_Festival_Ride_Fan_Generat', 'TRG_Fan_Whine': r'TRG_Festival_Ride_Fan_Whine_L', 'TRG_NoFear_Generator': r'TRG_Festival_Ride_No_Fear_04',
            'TRG_Booster_Generator': r'TRG_Festival_Ride_Booster_Gen', 'TRG_Booster_Whine': r'TRG_Festival_Ride_Booster_Whi',
            'TRG_Screams_Female_Loop': r'Festival_Screams_Female_Loop_', 'TRG_Screams_Loop': r'Crowd_Loop_Long_01', 'TRG_Screams_Oneshot': r'Cheers_Oneshot_0',
            'VFX_Sprinkler': r'VFX_Sprinkler_01', 'VFX_Geyser': r'VFX_Geyser_01', 'VFX_HotAirBalloon': r'VFX_HotAirBaloon_Fire_01', 'Fireworks_loop': r'Fireworks_loop',
            'BiPlane_Distant': r'BiPlane_Loop$', 'Helicopter': r'Heli_Loop_Main'}
# Loudness for the trigger sounds, chosen by ear like PLACED_RMS in ambience.py: one ride shouldn't drown the festival's own loops
TRIGGER_VOLUME = .7


def trigger_distances():
    """{event: (min, max distance in metres, volume)} from Triggerable_Events.fev. Each event's name is followed 0x30 bytes on by its 3D min and max
    distance as floats, and 0x8c on by its volume: rides 150 m, sprinklers 65, the geyser 300, fireworks 500, aircraft 250 to 600"""
    import gamezip
    b = open(gamezip.MEDIA + f'audio/tracks/colorado/{TRIGGER_BANK}.fev', 'rb').read(); out = {}
    for m in re.finditer(rb'([A-Za-z_0-9]+)\x00', b):
        name = m.group(1).decode(); e = m.end()
        if name in TRIGGERS and name not in out and len(b) > e + 0x90 and struct.unpack('<I', b[m.start() - 4:m.start()])[0] == len(name) + 1:
            lo, hi = struct.unpack('<2f', b[e + 0x30:e + 0x38]); vol = struct.unpack('<f', b[e + 0x8c:e + 0x90])[0]
            out[name] = (lo, hi, vol)
    missing = set(TRIGGERS) - set(out)
    if missing:
        raise KeyError(f'events not found in {TRIGGER_BANK}.fev: {sorted(missing)}')
    return out


def trigger_clips():
    """{event: [(file stem, sample tuple)]} from the triggerable bank, numbered in bank order"""
    import gamezip
    b = open(gamezip.MEDIA + f'audio/tracks/colorado/{TRIGGER_BANK}.fsb', 'rb').read(); s = samples(b); out = {}
    for event, pattern in TRIGGERS.items():
        found = [x for x in s if re.match(pattern, x[0])]
        if not found:
            raise KeyError(f'{event}: nothing in {TRIGGER_BANK} matches {pattern}')
        out[event] = [(f't_{event.lower()}_{i}', x) for i, x in enumerate(found)]
    return out, b


def trigger(event, level=1.0):
    """an event in the form fh1Extras.lua plays: files with their lengths, full-volume and silent radii, and volume"""
    lo, hi, vol = trigger_distances()[event]; clips, _ = trigger_clips()
    return {'files': [{'file': f'art/sound/fh1/{stem}.ogg', 'seconds': round(s[1] / s[4], 2)} for stem, s in clips[event]],
            'near': round(max(lo, hi * .1), 1), 'far': round(hi, 1), 'volume': round(min(vol * TRIGGER_VOLUME * level, 1), 2)}


def samples(b):
    """FSB4 sample headers: (name, sample count, data offset, data size, rate, channels). Sample data runs in header order after the headers."""
    count, headers = struct.unpack('<2I', b[4:12])
    o = 48; at = 48 + headers; out = []
    for _ in range(count):
        size = struct.unpack('<H', b[o:o + 2])[0]; name = b[o + 2:o + 32].split(b'\0')[0].decode()
        frames, bytes_ = struct.unpack('<2I', b[o + 32:o + 40]); rate = struct.unpack('<I', b[o + 52:o + 56])[0]
        channels = struct.unpack('<H', b[o + 62:o + 64])[0]
        out.append((name, frames, at, bytes_, rate, channels)); at += bytes_; o += size
    return out

def riff(frames, rate, channels, data):
    """XMA2WAVEFORMATEX with one stream, which ffmpeg can open."""
    fmt = struct.pack('<HHIIHHH', 0x166, channels, rate, rate * channels * 2, 4, 16, 34)
    fmt += struct.pack('<HIIIIIIIBBH', 1, 3 if channels == 2 else 4, frames, 2048, 0, 0, 0, 0, 3, 0, 0)
    return b'RIFF' + struct.pack('<I', 28 + len(fmt) + len(data) - 8) + b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data


_banks = {}


def bank(name):
    if name not in _banks:
        _banks[name] = zr.read(next(e for e in zr.E0 if e['n'] == name + '.fsb'))
    return _banks[name]


def clips(table):
    """{group: [(file stem, sample tuple, bank name)]}. A group's samples are numbered in bank order."""
    out = {}
    for group, row in table.items():
        found = [s for s in samples(bank(row[1])) if re.match(row[2], s[0])]
        if not found:
            raise KeyError(f'{group}: nothing in {row[1]} matches {row[2]}')
        out[group] = [(f'p_{group}_{i}', s, row[1]) for i, s in enumerate(found)]
    return out


def read():
    """Placed objects as {event: [(x, nav z)]}, and reverb zones as [(templates, triangle centres)]. Tiles overlap, so repeats are dropped."""
    objects = {}; zones = {}
    for f in glob.glob(TILES):
        root = ET.fromstring(open(f, 'rb').read().decode('utf8', 'ignore'))
        for o in root.iter('Object'):
            p = o.find('Position1')
            objects.setdefault(o.get('eventName'), set()).add((round(float(p.get('x')), 1), round(float(p.get('z')), 1)))
        for z in root.iter('ReverbZone'):
            pts = [(float(p.get('positionX')), float(p.get('positionZ'))) for p in z.iter('ReverbTrianglePoint')]
            tri = np.array(pts).reshape(-1, 3, 2).mean(1)
            zones[(z.get('Template0'), z.get('Template1'), z.get('MinHeight'), round(float(tri[0, 0]), 1))] = tri
    return objects, [((k[0], k[1]), v) for k, v in zones.items()]
