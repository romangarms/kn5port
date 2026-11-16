"""FH1's regional ambient loops as .ogg files in a built level. Run after build3.py: python ambience.py [fh1_colorado|fh1_redrock].
AMB_Quads_Stream.fsb is FSB4 holding XMA. Each region has front and rear stereo loops, and only the front ones are used: two regions' pairs differ in length."""
import os, shutil, struct, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import gamepath, gamezip, soundscape

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
LOOPS = {'AMB_Quads_Stream': {'Festival_Quad_F': 'festival', 'Main_Town_Quad_F_MoreBass': 'main_town', 'Mountains_Quad_F_MoreBass': 'mountains',
                              'Quad_Plain_F_MoreBass': 'plain', 'Red_Rock_Quad_F_MoreBass': 'red_rock'},
         'AMB_Festival_Ambience': {'Festival_Exterior_Crowd': 'festival_crowd', 'Festival_Exterior_Music': 'festival_music', 'FestivalStageCrowd_01': 'stage_crowd',
                                   'FestivalStageMusic_01': 'stage_music_a', 'FestivalStageMusic_03': 'stage_music_b'},
         'Colorado_Festival': {'Festival_Tanoy_01': 'tanoy'}}
OGG_QUALITY = 4; TARGET_RMS = .05; PEAK = .9; PLACED_RMS = .1


riff = soundscape.riff


def gain(src, target=TARGET_RMS):
    """One fixed gain per loop, which keeps the loop seamless. FH1 balanced them in its mixer: raw levels differ by 22 times."""
    a = np.frombuffer(subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(src), '-f', 'f32le', '-'], check=True, capture_output=True).stdout, '<f4')
    return min(target / np.sqrt((a ** 2).mean()), PEAK / np.abs(a).max())


if not shutil.which('ffmpeg'):          # the app warns about this before the build; the level works without these sounds
    print('ffmpeg not found: skipping the ambient sound. Install ffmpeg (the FH1 tab has a button for it) and build again to add it')
    sys.exit(0)
level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
if not level.exists():
    sys.exit(f'{level} is missing: run build3.py first')
out = level / 'art' / 'sound' / 'fh1'; out.mkdir(parents=True, exist_ok=True)


def export(sample, data, stem, tmp, target):
    name, frames, at, size, rate, channels = sample
    src = Path(tmp) / f'{stem}.xma'; src.write_bytes(riff(frames, rate, channels, data[at:at + size]))
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src), '-af', f'volume={gain(src, target):.3f}', '-c:a', 'libvorbis', '-q:a', str(OGG_QUALITY),
                    str(out / f'{stem}.ogg')], check=True)


done = 0
with tempfile.TemporaryDirectory() as tmp:
    for bank, loops in LOOPS.items():
        b = open(gamepath.AUDIO + f'{bank}.fsb', 'rb').read()
        for name, frames, at, size, rate, channels in soundscape.samples(b):
            if name in loops:
                export((name, frames, at, size, rate, channels), b, loops[name], tmp, TARGET_RMS)
                done += 1
    if done != sum(len(l) for l in LOOPS.values()):
        sys.exit(f'found only {done} loops')
    for table in (soundscape.LOOPS, soundscape.ONESHOTS):
        for group in soundscape.clips(table).values():
            for stem, sample, bank in group:
                export(sample, soundscape.bank(bank), stem, tmp, PLACED_RMS); done += 1
    # The rides', sprinklers', geyser's, fireworks' and aircraft's sounds, which animated.py and particles.py place
    clips, b = soundscape.trigger_clips()
    for group in clips.values():
        for stem, sample in group:
            export(sample, b, stem, tmp, PLACED_RMS); done += 1
print('done', done, 'loops ->', out)
