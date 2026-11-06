"""FH1's 67 radio songs as tagged MP3s, for a music player: python soundtrack.py [output folder]. Defaults to ~/Music/Forza Horizon Soundtrack.

The radio bank already holds MP3 at 160 kb/s (see radio.py), so the songs are copied without re-encoding, starting after FH1's DJ bed. Each song gets FH1's title and
artist, its station as the album (one folder per station, numbered in FH1's playlist order), a genre, and the station's HUD logo as
cover art. The pre-race, autoshow and other location music is mono copies of these songs, so it isn't exported."""
import os, re, shutil, struct, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image
import gamezip, soundscape, tex

RADIO = gamezip.MEDIA + 'audio/radio/'
# FH1's station, album name, HUD logo and genre (from Tips.str)
STATIONS = [('Radio1', 'Horizon Bass Arena', 'RS_BassArena', 'Electronic'), ('Radio2', 'Horizon Pulse', 'RS_Pulse', 'Indie'),
            ('Radio3', 'Horizon Rocks', 'RS_2Eleven', 'Rock')]
COVER = 600
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / 'Music' / 'Forza Horizon Soundtrack'
XDS_FORMATS = {0x72: 0x52, 0x73: 0x53, 0x75: 0x54}

system = re.sub(r'<!--.*?-->', '', open(RADIO + 'RadioSystem.xml', encoding='utf-8').read(), flags=re.S)
info = open(RADIO + 'RadioSoundbankInfo_Music.xml', encoding='utf-8').read()
index = {n: int(i) for n, i in re.findall(r'<Bank Name="([^"]+)">\s*<SoundBankInfo SoundBankIndex="(\d+)"', info)}
# FH1 put a DJ bed before each song for its DJs to talk over, shared between songs. OffsetSongStart is where the song itself comes in,
# checked on two Horizon Rocks songs with the same bed: identical to 15.0 s, both songs' OffsetSongStart, then a crossfade of about 0.25 s
intro = {n: int(s) / 1000 for n, s in re.findall(r'<Bank Name="([^"]+)">\s*<SoundBankInfo [^>]*OffsetSongStart="(\d+)"', info)}
bank = open(RADIO + 'Radio_Music.fsb', 'rb').read()
samples, at = [], None
for name, frames, start, size, rate, channels in soundscape.samples(bank):     # sample data starts on 32-byte boundaries
    at = start if at is None else -(-at // 32) * 32
    samples.append((name, at, size)); at += size


def cover(name):
    b = gamezip.read('ui/textures/Horizon.zip', f'Hud/Radio/{name}.xds')
    size = struct.unpack('>I', b[0x24:0x28])[0]
    logo = Image.fromarray(tex.decode(b[52:], (size & 0x1fff) + 1, ((size >> 13) & 0x1fff) + 1, XDS_FORMATS[b[0x23]]), 'RGBA')
    logo = logo.resize((COVER * 9 // 10, COVER * 9 // 10 * logo.height // logo.width), Image.LANCZOS)
    im = Image.new('RGBA', (COVER, COVER), (18, 18, 28, 255)); im.alpha_composite(logo, ((COVER - logo.width) // 2, (COVER - logo.height) // 2))
    return im.convert('RGB')


def safe(s):
    return re.sub(r'[<>:"/\\|?*]', '', s).strip()


done = 0
with tempfile.TemporaryDirectory() as tmp:
    for key, album, logo, genre in STATIONS:
        body = re.search(rf'<RadioStation name="{key}"[^>]*>(.*?)</RadioStation>', system, re.S)[1]
        tracks = re.findall(r'<MusicTrack\s+name="([^"]*)"\s+artist="([^"]*)"\s+fmod[nN]ame="(\w+)"', re.search(r'<Playlist\b.*?</Playlist>', body, re.S)[0])
        folder = OUT / album; folder.mkdir(parents=True, exist_ok=True)
        art = Path(tmp) / f'{key}.jpg'; cover(logo).save(art, quality=92); shutil.copy(art, folder / 'cover.jpg')
        for n, (title, artist, fmod) in enumerate(tracks, 1):
            name, start, size = samples[index[fmod]]
            if name != fmod[:len(name)]:
                sys.exit(f'bank index {index[fmod]} is {name}, expected {fmod}')
            raw = Path(tmp) / 'a.mp3'; raw.write_bytes(bank[start:start + size])
            dst = folder / f'{n:02d} {safe(artist)} - {safe(title)}.mp3'
            tags = {'title': title, 'artist': artist, 'album': album, 'album_artist': 'Forza Horizon', 'track': f'{n}/{len(tracks)}',
                    'genre': genre, 'date': '2012', 'comment': 'Forza Horizon (2012) radio, ' + album}
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{intro[fmod]:.3f}', '-i', str(raw), '-i', str(art), '-map', '0:a', '-map', '1:v', '-c', 'copy',
                   '-id3v2_version', '3', '-metadata:s:v', 'title=Album cover', '-metadata:s:v', 'comment=Cover (front)', '-disposition:v', 'attached_pic']
            for k, v in tags.items():
                cmd += ['-metadata', f'{k}={v}']
            subprocess.run(cmd + [str(dst)], check=True)
            done += 1
        print(album, len(tracks), 'songs')
size = sum(p.stat().st_size for p in OUT.rglob('*.mp3'))
print(f'done {done} songs, {size / 2 ** 20:.0f} MB ->', OUT)
