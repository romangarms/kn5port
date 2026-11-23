"""FM4's sky and backdrop as a BeamNG SkyBox cubemap (docs/fm4.md, Sky), baked the way FH1's sky.py bakes its own.

FM4 has no far geometry: Mount Tsukuba, the treeline and the fields are painted into the sky dome's panorama (<TRACK>_SKY_001, shader
sky_diff_1, an 8 km dome). Its pixel shader is   colour = tex.rgb * (tex.a * TimeGain.y + SkyScale.x),  written as sqrt like every FM4 pass.
- The texture's D3DFORMAT word (0x1A207F52) has GPUSIGN_GAMMA on RGB, so the fetch degammas it: Xenos's piecewise-linear gamma (xenos_degamma).
  Data textures (normal maps, masks) carry 0x1A200152-style words without it.
- SkyScale is taken as TrackSettings' skyScale (0.95 on Tsukuba): matched by name, not traced to the constant (c222 comes from the renderer
  through a VMX128 transform). TimeGain.y is taken as 0 (Tsukuba's skyGain; TimeGain's source isn't traced either).
- The dome is drawn at the camera, lowered by SkyboxHeightOffset (taken as camera-relative: the draw's matrix comes from the engine).
FM4's post is FH1's (its shaders in default.xex, fm4xex.py, match FH1's line for line): scene * 2^EV, Hable with TrackSettings' filmicTone over
its value at white, the colour grade LUT (FM4 ships none on any track: identity), colorTransform (desaturation by DesaturateAmount and
DesatLuminosity, then DesatPostMod, in FH1's order), sqrt. EV: ToneMapAdaptiveExposure's keys / 255 against the scene's mean linear luminance,
plus filmicTone's exposure; a bake has no scene, so the key is the sky's own mean from the horizon to 30 degrees, as FH1's sky.py (an assumption).
Not reproduced: bloom (bloomCutoff, bloomScale, bloomWeights), CameraExposureRange, exposure adaptation over time.
Directions the dome doesn't cover (below -1.4 degrees, where FM4 shows its own ground or else its clear colour) take TrackSettings'
WorldClear, squared as the post squares the framebuffer."""
import hashlib
import os
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(1, str(HERE.parent / 'pipeline'))
import dds
import fm4mesh
import fm4rmb
import tex

FACE = 1024
KEY_TOP = 30.0
LUM = np.array([.2126, .7152, .0722])


def settings(path):
    """TrackSettings.xml as {tag: {attribute: float}} (single-valued tags as {'value': v})"""
    t = open(path, encoding='utf-8', errors='replace').read(); out = {}
    for m in re.finditer(r'<(\w+)((?:\s+\w+="[^"]*")+)\s*/?>', t):
        out.setdefault(m.group(1), {k: float(v) for k, v in re.findall(r'(\w+)="(-?[\d.]+)"', m.group(2))})
    return out


def fog_settings(path):
    """TrackSettings' Fog block: [dict(enabled, angle, color (r, g, b), near, near_density, far, far_density)] for Settings0..7"""
    import xml.etree.ElementTree as ET
    out = []
    for e in ET.parse(path).getroot().find('Fog'):
        if not e.tag.startswith('Settings'): continue
        v = lambda t, a='value': float(e.find(t).get(a))
        out.append(dict(enabled=bool(v('Enabled')), angle=v('Angle'), color=tuple(v('Color', k) for k in 'rgb'), near=v('Near'),
                        near_density=v('NearDensity'), far=v('Far'), far_density=v('FarDensity')))
    return out


def xenos_degamma(c):
    """Xenos texture gamma to linear, c in 0..1: the piecewise-linear curve (segments at 64, 96 and 192 of 255, slopes 1/4 .. 2 in
    10-bit linear units), as the GPU applies it on fetch"""
    x = np.clip(c, 0, 1) * 255
    lin = np.where(x < 64, x, np.where(x < 96, x * 2 - 64, np.where(x < 192, x * 4 - 256, x * 8 - 1024)))
    lin = lin + np.where(x < 64, 0, np.floor(lin / np.where(x < 96, 512, np.where(x < 192, 256, 128))))
    return lin / 1023


def hable(x, f):
    A, Bs, Cs, D, E, F = f['shoulderStrength'], f['linearStrength'], f['linearAngle'], f['toeStrength'], f['toeNumerator'], f['toeDenominator']
    return (x * (A * x + Cs * Bs) + D * E) / (x * (A * x + Bs) + D * F) - E / F


def exposure(key, S):
    """BloomTonemap's vertex shader (FM4 0x1082F8, FH1's): the key table's segment the luminance falls in, lerped, plus filmicTone's exposure"""
    a = S['ToneMapAdaptiveExposure']; n = ('ExtraDark', 'Dark', 'Light', 'ExtraLight')
    k = [a['Key_' + x] / 255 for x in n]; e = [a['Exp_' + x] for x in n]
    i = 0 if key < k[1] else 2 if key >= k[2] else 1
    return e[i] + (e[i + 1] - e[i]) * min(max((key - k[i]) / (k[i + 1] - k[i]), 0), 1) + S['filmicTone']['exposure']


def post(lin, ev, S):
    """FM4's final pass on linear colour: display value 0..1 (what its framebuffer holds, sqrt-encoded)"""
    f = S['filmicTone']; c = np.clip(hable(lin * 2 ** ev, f) / hable(f['white'], f), 0, 1)
    d = S['DesaturateAmount']['value']; L = np.array([S['DesatLuminosity'][k] for k in 'rgb']); pm = np.array([S['DesatPostMod'][k] for k in 'rgb'])
    c = c * d + (1 - d) * (c @ L)[..., None]
    return np.sqrt(np.clip(c * pm, 0, 1))


def face_matrices():
    """per cube face, M with direction = M @ (u, v, 1): fh1 sky.py's directions() (D3D order, z up), u along columns and v down rows"""
    return [np.array(m, float).T for m in (((0, 0, -1), (0, -1, 0), (1, 0, 0)), ((0, 0, 1), (0, -1, 0), (-1, 0, 0)),
                                         ((1, 0, 0), (0, 0, 1), (0, 1, 0)), ((1, 0, 0), (0, 0, -1), (0, -1, 0)),
                                         ((1, 0, 0), (0, -1, 0), (0, 0, 1)), ((-1, 0, 0), (0, -1, 0), (0, 0, -1)))]


def dome(work, stem, no, F):
    """the sky mesh: BeamNG-axes positions (x east, y north, z up; mesh (x, y, z) -> (x, -z, y)), boxed uv0, triangles, its texture id slot"""
    b = open(f'{work}/x/{stem}.{no:05d}.rmb.bin', 'rb').read(); p = fm4rmb.parse(b); shs = fm4rmb.file_shaders(b, p); M = fm4mesh.materials(b, p)
    P = []; U = []; T = []; slot = None; base = 0
    for m in p['meshes']:
        lay = fm4mesh.mesh_layout(m, shs, F)
        pos = fm4mesh.attr(m, lay, 'position0')
        for s in m['secs']:
            sh, slots, ps = M[s['ints'][2]]
            if not shs[sh].startswith('sky_'): continue
            uv = np.array(s['fl'][4:6]) + fm4mesh.attr(m, lay, 'texcoord0') * np.array(s['fl'][6:8])
            if len(ps): uv = uv * ps[0][:2]              # c0.xy scales the UV in the pixel shader
            tri = fm4rmb.tris(s['ib'], m['nv'], s['ints'][1])
            P.append(pos[:, [0, 2, 1]] * [1, -1, 1]); U.append(uv); T.append(tri + base); base += len(pos); slot = slots[0]
    return np.concatenate(P), np.concatenate(U), np.concatenate(T), slot


def sample(img, uv):
    """bilinear, wrapping in u, clamped in v; img (h, w, 3) linear"""
    h, w = img.shape[:2]; x = uv[:, 0] * w - .5; y = np.clip(uv[:, 1] * h - .5, 0, h - 1)
    x0 = np.floor(x).astype(int); y0 = np.floor(y).astype(int); fx = (x - x0)[:, None]; fy = (y - y0)[:, None]
    x1 = (x0 + 1) % w; x0 %= w; y1 = np.minimum(y0 + 1, h - 1)
    return (img[y0, x0] * (1 - fx) + img[y0, x1] * fx) * (1 - fy) + (img[y1, x0] * (1 - fx) + img[y1, x1] * fx) * fy


def cast(P, U, T, n):
    """per cube face, (n, n, 2) uv and (n, n) hit mask: each pixel's ray from the origin hits the dome triangle in front of it. Exact ray /
    triangle-plane intersection, so the interpolation is perspective-correct"""
    out = []
    for M in face_matrices():
        Mi = np.linalg.inv(M); uvs = np.zeros((n, n, 2)); hit = np.zeros((n, n), bool); depth = np.full((n, n), np.inf)
        for t in T:
            a, b_, c = P[t]; q = (Mi @ P[t].T).T                       # (u s, v s, s) per vertex
            if (q[:, 2] <= 1e-6).all(): continue
            # bounds on the face: clip the triangle to s > 0 by sampling its edges finely (it's convex, so its hull's box bounds it)
            w_ = np.linspace(0, 1, 33); pts = np.concatenate([q[i] * (1 - w_[:, None]) + q[(i + 1) % 3] * w_[:, None] for i in range(3)])
            pts = pts[pts[:, 2] > 1e-6]
            if not len(pts): continue
            uu = pts[:, 0] / pts[:, 2]; vv = pts[:, 1] / pts[:, 2]
            if uu.min() > 1 or uu.max() < -1 or vv.min() > 1 or vv.max() < -1: continue
            j0, j1 = [int(np.clip(np.floor((x + 1) * n / 2 + o), 0, n - 1)) for x, o in ((max(uu.min(), -1), -1), (min(uu.max(), 1), 1))]
            i0, i1 = [int(np.clip(np.floor((x + 1) * n / 2 + o), 0, n - 1)) for x, o in ((max(vv.min(), -1), -1), (min(vv.max(), 1), 1))]
            ii, jj = np.mgrid[i0:i1 + 1, j0:j1 + 1]
            d = np.stack([(jj + .5) * 2 / n - 1, (ii + .5) * 2 / n - 1, np.ones(ii.shape)], -1) @ M.T
            nrm = np.cross(b_ - a, c - a); den = d @ nrm
            with np.errstate(divide='ignore', invalid='ignore'):
                tt = (a @ nrm) / den
            p = d * tt[..., None]
            # barycentric of the hit point
            v0, v1 = b_ - a, c - a; v2 = p - a; d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1; d20 = v2 @ v0; d21 = v2 @ v1
            den2 = d00 * d11 - d01 * d01
            if abs(den2) < 1e-12: continue
            bv = (d11 * d20 - d01 * d21) / den2; bw = (d00 * d21 - d01 * d20) / den2; bu = 1 - bv - bw
            ok = (tt > 0) & (bu >= -1e-6) & (bv >= -1e-6) & (bw >= -1e-6) & (tt < depth[i0:i1 + 1, j0:j1 + 1])
            if not ok.any(): continue
            uvt = bu[..., None] * U[t[0]] + bv[..., None] * U[t[1]] + bw[..., None] * U[t[2]]
            sl = (slice(i0, i1 + 1), slice(j0, j1 + 1))
            uvs[sl][ok] = uvt[ok]; hit[sl][ok] = True; depth[sl][ok] = tt[ok]
        out.append((uvs, hit))
    return out


def bake(work, stem, no, F, tid, S, out_dir, rel, name='fm4_sky', n=FACE):
    """write the six faces (cached by inputs) -> (materials dict, info). tid: the panorama's texture id"""
    P, U, T, _ = dome(work, stem, no, F)
    P = P + [0, 0, S['SkyboxHeightOffset']['value']]
    raw = tex.load(tid)[0][..., :3].astype('f8') / 255
    lin = xenos_degamma(raw) * S['skyScale']['value']
    # exposure key: the sky's mean linear luminance between the horizon and KEY_TOP degrees (FH1 sky.py's assumption), from the dome itself
    key_rays = []
    az, el = np.meshgrid(np.linspace(0, 2 * np.pi, 360, endpoint=False), np.radians(np.linspace(.25, KEY_TOP, 60)))
    dk = np.stack([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)], -1).reshape(-1, 3)
    key = float((sample(lin, ray_uv(P, U, T, dk)) @ LUM).mean()); ev = exposure(key, S)
    # the clear colour is written into the framebuffer, which holds sqrt-encoded colour: the post squares it
    clear = np.array([S['WorldClear'][k] for k in 'rgb']) ** 2
    h = hashlib.md5(Path(__file__).read_bytes() + raw.tobytes()[:1 << 20] + repr(sorted(S['WorldClear'].items())).encode() + repr((ev, n)).encode()).hexdigest()[:10]
    faces = []
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [k for k in range(6) if not (out_dir / f'{name}_{h}_{k + 1}.color.dds').exists()]
    if todo:
        for k, (uvs, hit) in enumerate(cast(P, U, T, n)):
            col = np.where(hit[..., None], sample(lin, uvs.reshape(-1, 2)).reshape(n, n, 3), clear)
            disp = post(col, ev, S)
            dds.save(out_dir / f'{name}_{h}_{k + 1}.color.dds', (srgb_bytes(disp)))
    for k in range(6):
        faces.append(f'{rel}/{name}_{h}_{k + 1}.color.dds')
    for f_ in out_dir.glob(f'{name}_*_?.color.dds'):
        if f'_{h}_' not in f_.name: f_.unlink()
    mats = {name: {'name': name, 'mapTo': 'unmapped_mat', 'class': 'Material', 'Stages': [{}, {}, {}, {}], 'cubemap': name + '_cubemap', 'materialTag0': 'beamng'},
            name + '_cubemap': {'name': name + '_cubemap', 'class': 'CubemapData', 'cubeFace': faces}}
    return mats, dict(key=key, ev=ev)


def srgb_bytes(disp):
    """FM4's display value (gamma 2, sqrt of linear) stored as the face's bytes, as FH1's sky.py stores its bake: the byte is the display value"""
    return (np.clip(disp, 0, 1) * 255 + .5).astype('u1')


def ray_uv(P, U, T, d):
    """uv where each unit direction d (k, 3) from the origin hits the dome (nearest hit); misses get uv of nothing (nan)"""
    out = np.full((len(d), 2), np.nan); best = np.full(len(d), np.inf)
    for t in T:
        a, b_, c = P[t]; nrm = np.cross(b_ - a, c - a); den = d @ nrm
        with np.errstate(divide='ignore', invalid='ignore'):
            tt = (a @ nrm) / den
        p = d * tt[:, None]; v0, v1 = b_ - a, c - a; v2 = p - a
        d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1; den2 = d00 * d11 - d01 * d01
        if abs(den2) < 1e-12: continue
        bv = (d11 * (v2 @ v0) - d01 * (v2 @ v1)) / den2; bw = (d00 * (v2 @ v1) - d01 * (v2 @ v0)) / den2; bu = 1 - bv - bw
        ok = (tt > 0) & (bu >= -1e-6) & (bv >= -1e-6) & (bw >= -1e-6) & (tt < best)
        out[ok] = (bu[ok, None] * U[t[0]] + bv[ok, None] * U[t[1]] + bw[ok, None] * U[t[2]]); best[ok] = tt[ok]
    return np.nan_to_num(out)
