# FH1's sky

FH1 draws its sky in real time from its time-of-day curves (`Ribbon_00/TimeOfDayA.xml`), not from a skybox. `sky.py` reproduces those shaders on the CPU and bakes the result into cubemaps, one per bake time. This page records what was decoded, so the bake can be checked against the game rather than tuned by eye.

## Where it came from

- **Shaders.** They're compiled into `default.xex` as Xenos microcode, with their constant tables intact, so they keep FH1's constant names. `python xshader.py` lists all 482 shaders, and `python xshader.py Sky_PS` disassembles one. It needs the uncompressed TU4 xex from the recomp (path in `xshader.py`), and its instruction layouts follow Xenia's `ucode.h`.
  - Sky shaders: `Sky_*`, `SkyFog_*`, `CloseCloud_*`, `FarCloud_*`, `Sun_*` and `Star_*`.
  - The tone map shader has no name in the xex. It's the container at 001cc698.
- **Constants.** They were traced through the recomp with `x2.py`:
  - `sub_8265A428` evaluates the curves each frame. A curve with no keys keeps a default.
  - `sub_82E355C8` copies the values into the sky object.
  - `sub_8242AD58` sets the dome's constants, `sub_82E33660` the close clouds' and `sub_82435FE0` the far clouds'.
  - The meshes are built in `sub_82E34768` (dome), `sub_82E35830` (close clouds) and `sub_82E35D08` (far clouds).

## Model

Directions are unit view vectors. The sun is `SunObjectPos`, the visible disc. The haze uses `HazeObjectPos` instead when that curve has keys, and Colorado's has none. `SunPos`, FH1's light direction, isn't used by the sky.

**Dome.** Radius 300, from −6° to 84° plus an apex. Each vertex carries `w = (elevation + 6°) / 90°`.

**Sky** (`Sky_PS`):
- `t = sat((w − AtmosphereBottomClampPower) / (AtmosphereClampPower − AtmosphereBottomClampPower)) ^ max(AtmosphereColourPower, 0.01)`. Despite their names, the two clamp curves are thresholds on `w`.
- `colour = lerp(AtmosphereBottomColour, AtmosphereTopColour, t)`
- `u` = up, less its component along the sun.
- `haze = (sat(view·sun) · sat(1 + (AtmosphereHazeEllipticalScale − 1) · sat(view·u))) ^ AtmosphereHazePower · AtmosphereHazeAlpha`
- `sky = sat(lerp(colour, AtmosphereHazeColour · AtmosphereHazeIntensity, haze)) · SkyGain`
- The `CubeAtmosphereHaze*` curves are used only for the cube-map (reflection) pass.

**Close clouds** (`CloseCloud_PS`):
- **Texture coordinates.** The band textures span the whole dome: `V = 1 − w`, `U = 1 − azimuth/2π + offset`.
  - The front layer's offset starts at 0.5 and turns `CloseCloudFrontRevs` times an hour, which a still bake can't show.
  - The back layer starts at 0. Its `CloseCloudBackAlpha` is 0 in Colorado.
- **Textures.** `AlphaDensity` gives alpha (R) and density (G). `Ambient` gives ambient (G). `TopBottomAmbient` gives the top mask (R) and the bottom mask (G). Each is multiplied by the layer's alpha.
- **Colour.** `s = 0.5 + 0.5·(view·sun)`. Then `colour = Dense + amb·(Thin − Dense) + Back·s^BackPower·(1 − density) + Top·s^TopPower·top^TopMaskPower + Bottom·s^BottomPower·bottom^BottomMaskPower`. Each light colour is multiplied by its `Intensity` curve.
- **Alpha.** Multiplied by `TopAlpha + (BottomAlpha − TopAlpha)·(1 − w)^AlphaCurve`.
- Clouds don't get `SkyGain`.

**Far clouds** (`FarCloud_PS`):
- **Geometry.** A cap of a sphere of radius 10000, centred 9700 below the camera, reaching out to where a ray 6° below the horizon meets it.
- **Texture coordinates.** Disc coordinates `0.5·ρ·(sin az, cos az)`, with ρ from 0 at the top to 1 at the rim. That the rings are evenly spaced in angle is inferred.
- **Coverage.** The alpha map tiles 5 times. The mask is sampled at scales 1.01 and 3.2 and averaged.
  - `cover = lerp(0.616, 0.43, FarCloudCoverage)`
  - `density = (1 − 0.984^(255·sat(mask − cover))) · alphaMap`
- **Colour.** `lerp(Thin·ThinIntensity, Dense, density)`, with no sun term.
- **Alpha.** `density · lerp(CentreAlpha, EdgeAlpha, ρ^FarCloudAlphaPower)`. The back layer uses the `FarCloudBack*` curves and the front layer is drawn over it.

**Blending.** Every pass writes `sqrt(colour)`, and alpha blending happens on those stored values. The post pass squares them.

**Tone map** (`Ribbon_00/TimeOfDay.xml`):
- `out = sqrt(Hable(c · 2^EV) / Hable(white))`, using `filmicTone`'s parameters as Hable's A–F.
- `EV` comes from `toneAdaptiveExposure`: its keys, divided by 255, are looked up against the scene's mean sqrt luminance, and `filmicTone.exposure` is added.
- The bake has no scene to measure. It uses the sky's own mean between the horizon and 30°, which is an assumption.
- In daylight this gives EV −0.49. The model then reproduces measured FH1 screenshot colours within about 10%, except the blue above 20° (FH1 is bluer there).
- The zone colour-grade LUT and the brightness, contrast and desaturation matrix come after the tone map. The bake doesn't apply them.

## Not reproduced

- The clouds moving: close clouds turn several times an hour, and far clouds scroll by `FarCloudSpeedX/Z`.
- `DitherSettings`.
- The sun disc. BeamNG draws its own, which `fh1Extras.lua` moves along FH1's `SunObjectPos` path.
- The colour grade.
