"""Retake the FH1 README gallery (docs/screenshots/fh1/) in a running BeamNG through its MCP server.

Load fh1_colorado in BeamNG first, with any car: the car shots teleport your vehicle. The game's MCP server must be on
(127.0.0.1:29292). Keep the window at the resolution you want the shots in (the originals were 2560x1440).

    python packaging/fh1_shots.py [stills|daycycle|turbines|all]      (default: all)

Camera positions, times of day and frame choices are the ones picked for the 2026-11-14 gallery. Time of day: 0 = noon,
0.25 is about sunset, 0.3 is night, 0.79 early morning. Raw frames land in BeamNG's screenshots/readme/, and this script
then writes the README's files: stills as 1600-wide JPEG, the day cycles and turbine loop as animated WebP.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "screenshots" / "fh1"
SHOTS = Path(os.environ["LOCALAPPDATA"]) / "BeamNG" / "BeamNG.drive" / "current" / "screenshots" / "readme"
URL = "http://127.0.0.1:29292/mcp"

# Helpers: shotLook points the free camera from p at t; zoneView frames an outpost spawn from behind and above
HELPERS = r"""
function gz(x,y) return be:getSurfaceHeightBelow(vec3(x,y,2000)) end
function shotLook(px,py,pz,tx,ty,tz,fov)
  local p=vec3(px,py,pz) local q=quatFromDir((vec3(tx,ty,tz)-p):normalized(), vec3(0,0,1))
  if not commands.isFreeCamera() then commands.setFreeCamera() end
  core_camera.setPosRot(0, p.x,p.y,p.z, q.x,q.y,q.z,q.w)
  if fov then core_camera.setFOV(0, fov) end
end
function zoneView(name, yaw, back, up, pd, fov)
  local p=scenetree.findObject(name):getPosition()
  local r=math.rad(yaw) local d=vec3(math.sin(r),math.cos(r),0)
  local cp=p-d*back cp.z=math.max(gz(cp.x,cp.y),p.z)+up
  local t=p+d*200 t.z=cp.z-math.tan(math.rad(pd))*(200+back)
  shotLook(cp.x,cp.y,cp.z,t.x,t.y,t.z,fov or 60)
end
-- The Montano Plains shot: the car on the road 230 m out from the outpost, seen with the mesas behind it
function montanoCar()
  local p=scenetree.findObject('spawn_outpost_montano_plains'):getPosition() local v=be:getPlayerVehicle(0)
  local m=map.getMap() local target=p+vec3(math.sin(math.rad(-10)),math.cos(math.rad(-10)),0)*230 local best,bd
  for id,n in pairs(m.nodes) do local d=(n.pos-target):length() if not bd or d<bd then best,bd=id,d end end
  local n=m.nodes[best] local nb for l,_ in pairs(n.links) do nb=l break end
  local dir=(m.nodes[nb].pos-n.pos) dir.z=0 dir=dir:normalized()
  local pos=n.pos+vec3(dir.y,-dir.x,0)*(n.radius*0.4)
  spawn.safeTeleport(v,pos,quatFromDir(dir,vec3(0,0,1))) v:queueLuaCommand('electrics.setLightsState(0)')
  local toM=vec3(-3900,-500,0)-pos toM.z=0 toM=toM:normalized()
  local cp=pos-toM*14+dir*4 cp.z=pos.z+3.5 local t=pos+toM*25 t.z=pos.z+1
  shotLook(cp.x,cp.y,cp.z,t.x,t.y,t.z,60)
end
-- The Red Rock car shot: on the canyon road at nav2196, facing away from nav2219, low camera ahead of it, lights on
function redRockCar()
  local m=map.getMap() local n,o=m.nodes['nav2196'],m.nodes['nav2219']
  local dir=(n.pos-o.pos) dir.z=0 dir=dir:normalized() local right=vec3(dir.y,-dir.x,0) local pos=n.pos+right*2
  local v=be:getPlayerVehicle(0) spawn.safeTeleport(v,pos,quatFromDir(dir,vec3(0,0,1))) v:queueLuaCommand('electrics.setLightsState(1)')
  local cp=pos+dir*9-right*2.2 cp.z=gz(cp.x,cp.y)+0.8 local t=pos-dir*6 t.z=pos.z+1.6
  shotLook(cp.x,cp.y,cp.z,t.x,t.y,t.z,55)
end
ZONES = {
  red_rock=function() zoneView('spawn_outpost_red_rock',10,75,35,9) end,
  carson=function() zoneView('spawn_outpost_carson',15,0,55,9) end,
}
function grab(name) screenshot.doScreenshot(nil,nil,'screenshots/readme/'..name,'jpg') end
"""

# README file -> (time of day, Lua that sets the scene). Since BeamNG's sun follows FH1's path (2026-11-15), sunset is 0.235: the
# Montano Plains and Steelworks shots were 0.9 before then. Car shots wait longer so the car settles after its teleport
STILLS = {
    "evening_festival": (0.235, "shotLook(-880,-40,165, -760,-150,170, 60)"),
    "zone_kettle_hills": (0.9, "zoneView('spawn_outpost_kettle_hills',0,60,30,8)"),
    "zone_clear_springs": (0.9, "shotLook(2350,-1700,gz(2350,-1700)+90, 1700,-1950,gz(1700,-1950)+10, 65)"),
    "zone_south_beaumont": (0.9, "zoneView('spawn_outpost_south_beaumont',0,60,35,9)"),
    "zone_rockies": (0.81, "shotLook(6450,2150,gz(6450,2150)+110, 5700,1500,gz(5700,1500), 65)"),
    "zone_montano_plains": (0.235, "montanoCar()"),
    "zone_route_4": (0.9, "zoneView('spawn_outpost_route_4',0,-15,45,6)"),
    "zone_gladstone_estate": (0.9, "zoneView('spawn_outpost_gladstone_estate',5,-10,45,8)"),
    "zone_steelworks": (0.235, "zoneView('spawn_outpost_steelworks',-55,10,45,8)"),
    "car_red_rock": (0.87, "redRockCar()"),
}
DAY = [0.79, 0.86, 0.0, 0.15, 0.225, 0.3]          # morning -> noon -> afternoon -> sunset -> night
DAY_ZONES = ["red_rock", "carson"]
TURBINE_FRAMES = 80                                 # physics steps of one graphics frame each; the loop is cut from these


def call(tool, args=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    text = urllib.request.urlopen(req, timeout=60).read().decode()
    if text.startswith("event:") or "\ndata:" in text:      # streamed reply: the JSON is on the data line
        text = next(l[5:] for l in text.splitlines() if l.startswith("data:"))
    r = json.loads(text)
    if "error" in r or r["result"].get("isError"):
        sys.exit(f"{tool} failed: {r}")
    return r["result"]["content"][0]["text"]


def lua(code):
    return call("run_lua", {"code": code})


def job(body, label):
    """Run Lua inside a core_jobsystem job (it may job.sleep) and wait for it to finish"""
    lua(f"SHOTS_DONE=false SHOTS_ERR=nil core_jobsystem.wrap(function(job) local ok,e=pcall(function() {body} end) "
        f"SHOTS_ERR=(not ok) and tostring(e) or nil SHOTS_DONE=true end)() return 'ok'")
    t0 = time.time()
    while True:
        time.sleep(3)
        s = lua("return tostring(SHOTS_DONE)..'|'..tostring(SHOTS_ERR)")
        done, err = s.split("|", 1)
        if done == "true":
            if err != "nil":
                sys.exit(f"{label}: {err}")
            print(f"{label}: {time.time() - t0:.0f} s")
            return


def stills():
    steps = "".join(f"core_environment.setTimeOfDay({{time={t}}}) {scene} job.sleep({5 if 'Car' in scene else 3}) grab('{n}') job.sleep(1.5) "
                    for n, (t, scene) in STILLS.items())
    job(steps, "stills")
    for n in STILLS:
        im = Image.open(SHOTS / f"{n}.jpg").convert("RGB")
        im.thumbnail((1600, 900), Image.LANCZOS)
        im.save(OUT / f"{n}.jpg", quality=85, optimize=True)


def daycycle():
    steps = "".join(f"ZONES['{z}']() " + "".join(f"core_environment.setTimeOfDay({{time={t}}}) job.sleep(2.5) grab('{z}_{round(t * 1000):03d}') job.sleep(0.8) "
                                                 for t in DAY) for z in DAY_ZONES)
    job(steps, "day cycles")
    (OUT / "daycycle").mkdir(exist_ok=True)
    W, HOLD, FADE, STEPS = 640, 900, 400, 6         # each time held 0.9 s, then a 0.4 s crossfade; night fades back to morning
    for z in DAY_ZONES:
        keys = []
        for t in DAY:
            im = Image.open(SHOTS / f"{z}_{round(t * 1000):03d}.jpg").convert("RGB")
            keys.append(im.resize((W, round(W * im.height / im.width)), Image.LANCZOS))
        frames, durs = [], []
        for i, k in enumerate(keys):
            nxt = keys[(i + 1) % len(keys)]
            frames.append(k); durs.append(HOLD)
            for s in range(1, STEPS):
                frames.append(Image.blend(k, nxt, s / STEPS)); durs.append(FADE // STEPS)
        frames[0].save(OUT / "daycycle" / f"{z}.webp", save_all=True, append_images=frames[1:], duration=durs, loop=0, quality=80, method=6)


def turbines():
    """Route 4's wind turbines, stepped one frame at a time with physics paused, cut where the rotors line up again"""
    lua("core_environment.setTimeOfDay({time=0.17}) local cx,cy=-1853+40,-1486-150 shotLook(cx,cy,gz(cx,cy)+30,-1853,-1486,236,50) return 'ok'")
    time.sleep(3)
    call("pause_physics")
    try:
        job(f"job.sleep(0.5) for i=0,{TURBINE_FRAMES - 1} do grab(string.format('turbine_%02d',i)) job.sleep(1.1) be:physicsStep(1) job.sleep(0.3) end", "turbines")
    finally:
        call("resume_physics")
    fr = [Image.open(SHOTS / f"turbine_{i:02d}.jpg").convert("RGB") for i in range(TURBINE_FRAMES)]
    fr = [f.crop((f.width // 8, 0, f.width - f.width // 8, f.height)).resize((720, 540), Image.LANCZOS) for f in fr]    # 4:3, centred
    # The rotors' period isn't a whole number of steps: pick the frame after 20 that looks most like the first
    diff = lambda a, b: sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)
    end = min(range(20, len(fr)), key=lambda i: diff(fr[0], fr[i]))
    print(f"turbine loop: {end} frames")
    fr[0].save(OUT / "turbines.webp", save_all=True, append_images=fr[1:end], duration=32, loop=0, quality=80, method=4)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    level = json.loads(call("get_status")).get("level", "")
    if "fh1_colorado" not in level:
        sys.exit(f"load fh1_colorado first (BeamNG has {level or 'no level'})")
    SHOTS.mkdir(parents=True, exist_ok=True)
    lua(HELPERS + " return 'ok'")
    call("toggle_ui", {"show": False})
    try:
        for name, fn in (("stills", stills), ("daycycle", daycycle), ("turbines", turbines)):
            if what in (name, "all"):
                fn()
    finally:
        call("toggle_ui", {"show": True})
    print("done ->", OUT)


if __name__ == "__main__":
    main()
