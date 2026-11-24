<p align="center"><img src="packaging/kn5port.png" alt="kn5port icon" width="128"></p>

# kn5port

Turn maps from other racing games you already own into BeamNG.drive levels, with one click.

## What it ports

- **[Assetto Corsa tracks](#assetto-corsa)**: the tracks in your AC install, Kunos and community alike, with
  roads that keep their grip, a spawn point at every pit, and either AC's own scenery or BeamNG terrain and trees.
- **[Forza Horizon 1's Colorado](#forza-horizon-1)**: the whole 16 km open world from your own copy of the
  Xbox 360 game, with its towns, roads, water, night lights, ambient sound and moving scenery, its races as time
  trials, and its radio as a separate mod.
- **[Forza Motorsport 4's circuits](#forza-motorsport-4-beta)** (beta): one circuit at a time, every layout of it in
  one level, with a spawn on each layout's grid.

<table>
<tr>
<td width="50%"><a href="#assetto-corsa"><img src="docs/screenshots/track.png" alt="Your Assetto Corsa track library in kn5port"></a><br><sub>Assetto Corsa</sub></td>
<td width="50%"><a href="#forza-horizon-1"><img src="docs/screenshots/fh1/daycycle/red_rock.webp" alt="Forza Horizon 1's Red Rock in BeamNG, morning to night"></a><br><sub>Forza Horizon 1</sub></td>
</tr>
</table>

## Assetto Corsa

Pick a track in the app and convert it. kn5port writes the BeamNG level into your mods folder.

- **Every track in one place.** kn5port finds Assetto Corsa and BeamNG through Steam, lists your tracks with
  their previews, and marks each one as ready, partly readable or protected. It also shows which ones you've
  already converted.
- **A drivable BeamNG level.** Roads, kerbs and gravel keep their grip, so asphalt drives like asphalt and grass
  like grass. Buildings and props come across, and every pit and start position becomes a spawn point.
- **AC accurate or BeamNG look.** Keep the track's own ground and trees as AC built them, or turn the ground
  into a real BeamNG terrain with 3D grass and AC's flat tree cards into BeamNG trees, sized to match the
  track's own. For the BeamNG look you pick which stock map the trees and ground come from: West Coast USA,
  Italy, Utah, Jungle Rock Island and more. Kunos tracks start on the BeamNG look, community tracks on AC
  accurate, and you can mix the two.
- **Checked before you drive.** After converting, kn5port checks the level, including that every AI racing line
  has road under it.

![Options: scenery style, ground and trees, each with its pros and cons](docs/screenshots/options.png)

![A finished conversion, ready to load in BeamNG](docs/screenshots/converted.png)

Magione (above) converts in under a minute; long roads take longer. **Fully restart BeamNG** afterwards (it caches materials), then
pick the track from the level list.

## Forza Horizon 1

The **Forza Horizon 1** tab turns Colorado from your own copy of Forza Horizon (2012, Xbox 360) into a BeamNG
level. You get FH1's own terrain, roads, buildings, plants and textures, its rivers and lakes, street lamps
and lit windows at night, ambient sound, the festival's crowds, and its moving scenery. FH1's 118 races become
time trials, and you spawn at the festival or any of its ten outposts. FH1's three radio stations, DJs
included, come as a separate mod that works on any map.

![The Forza Horizon 1 tab](docs/screenshots/fh1.png)

Point it at a Games on Demand folder, a disc image (`.iso`) or extracted files. Then build the whole 16 km
map (about 10 minutes, 3.3 GB) or a quick 4 km chunk around Red Rock or Clear Springs. The first time, the app
offers to download what it needs, about 100 MB. If you have the Rally Expansion DLC, as its original package or
installed in Xenia, you can also build its version of Colorado with the rally stages; that one is a beta and not fully working yet. See [`fh1/`](fh1/README.md) for how it works.

<table>
<tr>
<td width="50%"><img src="docs/screenshots/fh1/daycycle/red_rock.webp" alt="A day at the Red Rock outpost, morning to night"><br><sub>Red Rock, morning to night</sub></td>
<td width="50%"><img src="docs/screenshots/fh1/daycycle/carson.webp" alt="A day in Carson, morning to night"><br><sub>Carson, morning to night</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/fh1/car_red_rock.jpg" alt="A BeamNG car on a Red Rock canyon road"><br><sub>Red Rock canyon road</sub></td>
<td><img src="docs/screenshots/fh1/zone_montano_plains.jpg" alt="A car crossing a bridge on the Montano Plains at sunset"><br><sub>Montano Plains</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/fh1/zone_rockies.jpg" alt="A river gorge in the Rockies"><br><sub>The Rockies</sub></td>
<td><img src="docs/screenshots/fh1/zone_south_beaumont.jpg" alt="South Beaumont in its mountain valley"><br><sub>South Beaumont</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/fh1/zone_clear_springs.jpg" alt="The Clear Springs reservoir"><br><sub>Clear Springs</sub></td>
<td><img src="docs/screenshots/fh1/zone_gladstone_estate.jpg" alt="Forest and river at Gladstone Estate"><br><sub>Gladstone Estate</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/fh1/zone_kettle_hills.jpg" alt="The Kettle Hills outpost among pines"><br><sub>Kettle Hills</sub></td>
<td><img src="docs/screenshots/fh1/zone_route_4.jpg" alt="Wind farm along Route 4"><br><sub>Route 4</sub></td>
</tr>
<tr>
<td><img src="docs/screenshots/fh1/zone_steelworks.jpg" alt="The road to the steelworks at sunset"><br><sub>Steelworks</sub></td>
<td><img src="docs/screenshots/fh1/evening_festival.jpg" alt="The Horizon Festival's Ferris wheel at sunset"><br><sub>Horizon Festival</sub></td>
</tr>
</table>

FH1's moving scenery comes across too: wind turbines, windmills, the festival's rides, farm machines, balloons and
the blimp.

<p align="center"><img src="docs/screenshots/fh1/turbines.webp" alt="Wind turbines turning near Route 4" width="540"></p>

## Forza Motorsport 4 (beta)

The **Forza Motorsport 4** tab turns a circuit from your own copy of Forza Motorsport 4 (2011, Xbox 360) into a
BeamNG level: every layout of the circuit in one level, with FM4's track surfaces and collision, its buildings and
scenery, its baked lighting and sky, and a spawn on each layout's grid. The level selector shows FM4's own track
photo, names and layout maps.

Point it at a Games on Demand folder, an image of the first disc (`.iso`, which holds every track) or extracted
files. A circuit builds in about a minute and a half. This is a beta: 10 of FM4's 24 circuits build so far (Tsukuba, Sedona, Fujimi Kaido,
Indianapolis, Motegi, Mugello, the Nürburgring GP circuit, Road Atlanta, Sunset Peninsula and the Iberian venues), and
only Tsukuba has been looked over in BeamNG. See
[`fh1/docs/fm4.md`](fh1/docs/fm4.md) for how it works.

## Install

Download `kn5port.exe` from [releases](../../releases) and run it.

kn5port checks GitHub for a new release when it starts. When there is one, an **Update** button appears at the
top; click it and kn5port downloads the new `kn5port.exe`, checks it against the SHA-256 GitHub lists for it,
replaces itself and restarts. Your settings stay. Turn the check off under **⚙ Folders**.

##### From source (Python 3.9+):

```
git clone https://github.com/romangarms/kn5port
cd kn5port
pip install .
kn5port-app
```

## Command line

```
kn5port inspect  <AC track folder>     # what kn5port sees in a track
kn5port convert  <AC track folder>     # writes mods\unpacked\<track>
kn5port ui                             # the app, in your browser
```

`kn5port convert --help` lists every option: the look (`--preset ac` or `--preset beamng`), scenery style
(`--palette italy`), tree density, keeping AC's ground or trees, and more. If a track needs tweaks, put a `kn5port.json` next to it
([example](profiles/skyline_boulevard.json)).

## Known limits

- Big maps get coarser terrain, since BeamNG terrain tops out at 4096².
- AC's special shaders (CSP grass, reflections) don't exist in BeamNG, so materials are simpler.
- With the BeamNG look, trees and grass are BeamNG's own, so the species won't always match.
- No AI paths, race timing or night lighting for AC tracks yet.

## Personal use only

kn5port contains no game content. It reads files you already have and writes the level into your own mods
folder. Converted levels are derivative works, so don't share them, and check each track's license first.
Protected or encrypted tracks are skipped.

## More

- [How it works](docs/how-it-works.md) and [what broke along the way](docs/learnings.md)
- Build the exe: `packaging\build.ps1`. Release: `python packaging/release.py [patch|minor|major]` bumps the
  version, tags it and pushes; GitHub Actions then builds `kn5port.exe` and publishes it on the releases page.
- License: MIT for the code. It grants no rights to the tracks you convert or to BeamNG content.
