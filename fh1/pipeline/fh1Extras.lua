-- Colorado extras: FH1's regional ambience and placed sounds, nearby particles, the sky for the time of day, fog and bloom by FH1 zone and time,
-- and the current road on the minimap's location line.
-- Does nothing unless the level ships fh1_extras.json. levelinfo.py writes that file and installs this one.

local M = {}

local logTag = 'fh1Extras'
local FADE_SECONDS = 4
local UPDATE_SECONDS = 0.5
local ROAD_SEARCH_METRES = 30
local AMBIENCE_VOLUME = 0.6
local SHOW_COORDS = true -- adds the camera's map position to the minimap's location line
local PARTICLE_SECONDS = 2
local MAX_PARTICLE_NODES = 80
-- Particles alive at once across running emitters, from each entry's count. Nearer emitters win. Set after the user saw fps drop
local PARTICLE_BUDGET = 4000
local SKY_SECONDS = 2
local SEQUENCE_SECONDS = 1 -- how often the sequences near the camera are picked

local data
local levelDir
local originalZone
local patchedZone
local roadName
local ambienceOn = true
local sinceUpdate = 0
local shotsOn = true
local emitters
local awake = {} -- emitter name -> true
local sinceParticles = 0
local sky -- fh1_sky.json: the baked skies and which one each quarter hour shows
local skyBake -- index of the bake on show
local lookZone -- FH1 post-processing zone last under the camera. Between zones the last one holds, as FH1's have gaps
local look -- fog density and bloom cutoff and scale factors on show, easing towards the zone's
local lookBase -- the level's fog density and the engine's bloom threshold and intensity, which the factors scale. Bloom is restored on exit
local sinceSky = 0
local clock = 0
local shots = {} -- playing one-shots: { id, ends }
local nextShot = {} -- one-shot group -> clock time
local sources = {} -- sound file -> { id, volume }
local targets = {} -- sound file -> volume to fade towards
local sequences -- fh1_sequences.json: ANIM models' effect bones (the geyser, fireworks), switched and moved on their loops
local running = {} -- sequences near the camera and in their hours
local sinceSequences = 0

local function deleteSource(entry)
  local sound = scenetree.findObjectById(entry.id)
  if sound then
    sound:stop(-1)
    sound:delete()
  end
end

local function stopAmbience()
  for _, entry in pairs(sources) do deleteSource(entry) end
  sources = {}
  targets = {}
  for _, shot in ipairs(shots) do deleteSource(shot) end
  shots = {}
  nextShot = {}
end

local function startSource(file)
  local id = Engine.Audio.createSource('AudioStreamLoop2D', levelDir .. file)
  local sound = id and scenetree.findObjectById(id)
  if not sound then
    log('E', logTag, 'Could not play ' .. tostring(file) .. ', ambience is off')
    ambienceOn = false
    return
  end
  sound:setVolume(0)
  sound:play(-1)
  sources[file] = { id = id, volume = 0 }
end

local function fadeSounds(dt)
  for file in pairs(targets) do
    if ambienceOn and not sources[file] then startSource(file) end
  end
  local step = dt / FADE_SECONDS
  for file, entry in pairs(sources) do
    local target = targets[file] or 0
    entry.volume = clamp(entry.volume + clamp(target - entry.volume, -step, step), 0, 1)
    local sound = scenetree.findObjectById(entry.id)
    if sound then sound:setVolume(entry.volume) end
    if entry.volume == 0 and target == 0 then
      deleteSource(entry)
      sources[file] = nil
    end
  end
end

local function level(group, d)
  return group.volume * clamp((group.far - d) / (group.far - group.near), 0, 1) ^ 2
end

local function nearest(group, x, y)
  local best = math.huge
  for _, p in ipairs(group.points) do
    local dx, dy = x - p[1], y - p[2]
    best = math.min(best, dx * dx + dy * dy)
  end
  return math.sqrt(best)
end

local function playShot(group, volume)
  local clip = group.files[math.random(#group.files)]
  local id = Engine.Audio.createSource('AudioStream2D', levelDir .. clip.file)
  local sound = id and scenetree.findObjectById(id)
  if not sound then
    log('E', logTag, 'Could not play ' .. clip.file .. ', one-shots are off')
    shotsOn = false
    return
  end
  sound:setVolume(volume)
  sound:play(-1)
  table.insert(shots, { id = id, ends = clock + clip.seconds + 1 })
end

local function updateShots(x, y)
  for i, group in ipairs(data.oneshots or {}) do
    nextShot[i] = nextShot[i] or clock + math.random(group.wait[1], group.wait[2])
    if clock >= nextShot[i] then
      nextShot[i] = clock + math.random(group.wait[1], group.wait[2])
      local volume = level(group, nearest(group, x, y))
      if volume > 0 and shotsOn then playShot(group, volume) end
    end
  end
  for i = #shots, 1, -1 do
    if clock >= shots[i].ends then
      deleteSource(shots[i])
      table.remove(shots, i)
    end
  end
end

local function setAwake(name, on)
  local node = scenetree.findObject(name)
  if node then node:setActive(on) end
end

-- BeamNG's time of day runs from 0 at noon to 1, so this is the hour on a 24 hour clock
local function hourNow()
  local tod = core_environment and core_environment.getTimeOfDay and core_environment.getTimeOfDay()
  return tod and tod.time and (tod.time * 24 + 12) % 24
end

-- An emitter's optional window, from FH1's effect: e[6] to e[7], which may wrap past midnight
local function inWindow(e, hour)
  if not e[6] or not hour then return true end
  if e[6] <= e[7] then return hour >= e[6] and hour < e[7] end
  return hour >= e[6] or hour < e[7]
end

-- Nodes start switched off. The nearest ones run, measured against each one's own range, up to MAX_PARTICLE_NODES and PARTICLE_BUDGET.
local function updateParticles(x, y)
  local near = {}
  local hour = hourNow()
  for _, e in ipairs(emitters) do
    local dx, dy = x - e[2], y - e[3]
    local range = (dx * dx + dy * dy) / (e[4] * e[4])
    if range < 1 and inWindow(e, hour) then table.insert(near, { name = e[1], range = range, cost = e[5] or 0 }) end
  end
  table.sort(near, function(a, b) return a.range < b.range end)
  local want = {}
  local spent = 0
  for i = 1, math.min(#near, MAX_PARTICLE_NODES) do
    spent = spent + near[i].cost
    if spent > PARTICLE_BUDGET and i > 1 then break end
    want[near[i].name] = true
  end
  for name in pairs(awake) do
    if not want[name] then setAwake(name, false) end
  end
  for name in pairs(want) do
    if not awake[name] then setAwake(name, true) end
  end
  awake = want
end

-- Puts one of sky.py's bakes on the skybox, reflections and rivers, and hides the skybox for bakes marked hideBox. Only does work when the quarter hour picks a different bake
-- A moving node's place at time t along its keys { t, x, y, z }
local function along(path, t)
  if t <= path[1][1] then return path[1] end
  for i = 2, #path do
    if t <= path[i][1] then
      local a, b = path[i - 1], path[i]
      local f = (t - a[1]) / math.max(b[1] - a[1], 1e-6)
      return { t, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f, a[4] + (b[4] - a[4]) * f }
    end
  end
  return path[#path]
end

local function stopSequence(s)
  for _, n in ipairs(s.nodes) do
    if n.on then setAwake(n[1], false) end
    n.on = false
  end
end

-- Sequences run within their distance and hours, as other emitters do
local function pickSequences(x, y)
  local hour = hourNow()
  local want = {}
  for _, s in ipairs(sequences) do
    local dx, dy = x - s.x, y - s.y
    local hours = {} -- inWindow reads an emitter entry's window from [6] and [7]
    if s.hours then hours[6], hours[7] = s.hours[1], s.hours[2] end
    if dx * dx + dy * dy < s.dist * s.dist and inWindow(hours, hour) then want[s] = true end
  end
  for s in pairs(running) do
    if not want[s] then stopSequence(s) end
  end
  running = want
end

-- Each frame: every node of a running sequence is on during its windows of the loop, and a moving one is put on its path first
local function playSequences()
  for s in pairs(running) do
    local t = (clock + s.phase) % s.period
    for _, n in ipairs(s.nodes) do
      local on = false
      for _, w in ipairs(n[2]) do
        if t >= w[1] and t < w[2] then on = true break end
      end
      if on and n[3] then
        local node = scenetree.findObject(n[1])
        local p = along(n[3], t)
        if node then node:setPosition(vec3(p[2], p[3], p[4])) end
      end
      if on ~= n.on then
        setAwake(n[1], on)
        n.on = on
      end
    end
  end
end

-- nightlights.py's lightmap overlays are hidden by day: their black additive surface still shows a sheen in shade. Same 18:00 to 6:00 window as
-- stock core_environment's nightEmissive switch, which makes them glow. Only does work when day turns to night or back
local NIGHT_FROM, NIGHT_TO = 18, 6
local nightShown = nil
local function updateNightLights()
  local hour = hourNow()
  if not hour then return end
  local show = hour >= NIGHT_FROM or hour < NIGHT_TO
  if show == nightShown then return end
  local group = scenetree.findObject('fh1_night')
  if not group then return end
  nightShown = show
  for i = 0, group:getCount() - 1 do
    local obj = group:getObject(i)
    if obj then obj:setHidden(not show) end
  end
end

local function updateSky()
  local hour = hourNow()
  if not hour then return end
  local index = sky.quarterHours[math.floor(hour * 4) % 96 + 1]
  if index == skyBake then return end
  skyBake = index
  local bake = sky.bakes[index]
  local box = scenetree.findObject('fh1_skybox')
  if box then
    box:setField('material', 0, bake.material)
    box:postApply()
    -- At full night the skybox steps aside for BeamNG's own sky, whose stars stay visible
    box:setHidden(bake.hideBox == true)
  end
  local info = scenetree.findObject('theLevelInfo')
  if info then
    info:setField('globalEnviromentMap', 0, bake.cubemap)
    info:postApply()
  end
  for _, name in ipairs(scenetree.findClassObjects('River') or {}) do
    local river = scenetree.findObject(name)
    if river then
      river:setField('cubemap', 0, bake.cubemap)
      river:postApply()
    end
  end
end

local function inTriangle(t, x, y)
  local d1 = (x - t[2][1]) * (t[1][2] - t[2][2]) - (t[1][1] - t[2][1]) * (y - t[2][2])
  local d2 = (x - t[3][1]) * (t[2][2] - t[3][2]) - (t[2][1] - t[3][1]) * (y - t[3][2])
  local d3 = (x - t[1][1]) * (t[3][2] - t[1][2]) - (t[3][1] - t[1][1]) * (y - t[1][2])
  return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
end

local function lookZoneAt(x, y)
  for _, zone in ipairs(sky.zones or {}) do
    for _, t in ipairs(zone.tris) do
      if inTriangle(t, x, y) then return zone end
    end
  end
  return lookZone
end

local function restoreBloom()
  local bloom = scenetree.findObject('PostEffectBloomObject')
  if bloom and lookBase then
    if lookBase.threshold then bloom:setField('threshHold', 0, tostring(lookBase.threshold)) end
    if lookBase.intensity then bloom:setField('intensity', 0, tostring(lookBase.intensity)) end
  end
  lookBase = nil
  look = nil
end

-- FH1's fog and bloom templates for the zone, with fog scaled by FH1's density at the bake's time. Eased over the zone's own fade time
local function updateLook(dt)
  local info = scenetree.findObject('theLevelInfo')
  local bloom = scenetree.findObject('PostEffectBloomObject')
  if not info then return end
  if not lookBase then
    lookBase = { fog = sky.fogDensity or tonumber(info:getField('fogDensity', 0)) }
    if bloom then
      lookBase.threshold = tonumber(bloom:getField('threshHold', 0))
      lookBase.intensity = tonumber(bloom:getField('intensity', 0))
    end
  end
  local x, y = core_camera.getPositionXYZ()
  lookZone = lookZoneAt(x, y)
  local bake = skyBake and sky.bakes[skyBake]
  local target = { fog = (lookZone and lookZone.fog or 1) * (bake and bake.fog or 1),
                   cutoff = lookZone and lookZone.bloomCutoff or 1, scale = lookZone and lookZone.bloomScale or 1 }
  local first = not look
  look = look or {}
  local k = math.min(dt / (lookZone and lookZone.lerp or 15), 1)
  local fogBefore = look.fog
  for key, value in pairs(target) do
    look[key] = first and value or look[key] + (value - look[key]) * k
  end
  if first or math.abs(look.fog - fogBefore) > 0.005 * fogBefore then
    info:setField('fogDensity', 0, tostring(lookBase.fog * look.fog))
    info:postApply()
  end
  -- Set every time: a graphics settings change puts the engine's bloom back
  if bloom and lookBase.threshold then bloom:setField('threshHold', 0, tostring(lookBase.threshold * look.cutoff)) end
  if bloom and lookBase.intensity then bloom:setField('intensity', 0, tostring(lookBase.intensity * look.scale)) end
end

local function zoneWithRoad(pos)
  local zone = originalZone(pos)
  local name = zone and zone.name
  if roadName then name = name and (roadName .. ', ' .. name) or roadName end
  if SHOW_COORDS then
    local where = string.format('%d, %d', math.floor(pos.x), math.floor(pos.y))
    name = name and (name .. ' (' .. where .. ')') or where
  end
  if not name then return zone end
  return setmetatable({ name = name }, { __index = zone or {} })
end

local function patchCity()
  if not gameplay_city then extensions.load('gameplay_city') end
  if not gameplay_city or gameplay_city.getHighestPrioZone == patchedZone then return end
  originalZone = gameplay_city.getHighestPrioZone
  patchedZone = zoneWithRoad
  gameplay_city.getHighestPrioZone = patchedZone
end

local function unpatchCity()
  if gameplay_city and originalZone and gameplay_city.getHighestPrioZone == patchedZone then
    gameplay_city.getHighestPrioZone = originalZone
  end
  originalZone = nil
  patchedZone = nil
end

local function refresh()
  patchCity()
  if not originalZone then return end
  local x, y, z = core_camera.getPositionXYZ()
  local pos = vec3(x, y, z)
  local a, b = map.findClosestRoad(pos, ROAD_SEARCH_METRES)
  roadName = a and (data.roads[a] or data.roads[b]) or nil
  local zone = originalZone(pos)
  targets = {}
  if not ambienceOn then return end
  targets[zone and data.ambience[zone.name] or data.defaultAmbience] = AMBIENCE_VOLUME
  for _, s in ipairs(data.sounds or {}) do
    local volume = level(s, math.sqrt((x - s.x) ^ 2 + (y - s.y) ^ 2))
    if volume > 0 then targets[s.file] = math.max(targets[s.file] or 0, volume) end
  end
  for _, group in ipairs(data.placedLoops or {}) do
    local volume = level(group, nearest(group, x, y))
    if volume > 0 then targets[group.file] = volume end
  end
  updateShots(x, y)
end

local function start()
  stopAmbience()
  unpatchCity()
  data = nil
  roadName = nil
  local mission = getMissionFilename()
  levelDir = mission and mission ~= '' and path.split(mission)
  if not levelDir or not FS:fileExists(levelDir .. 'fh1_extras.json') then return end
  data = jsonReadFile(levelDir .. 'fh1_extras.json')
  awake = {}
  sinceParticles = PARTICLE_SECONDS
  emitters = FS:fileExists(levelDir .. 'fh1_particles.json') and jsonReadFile(levelDir .. 'fh1_particles.json') or nil
  sky = FS:fileExists(levelDir .. 'fh1_sky.json') and jsonReadFile(levelDir .. 'fh1_sky.json') or nil
  sequences = FS:fileExists(levelDir .. 'fh1_sequences.json') and jsonReadFile(levelDir .. 'fh1_sequences.json') or nil
  running = {}
  sinceSequences = SEQUENCE_SECONDS
  skyBake = nil
  restoreBloom()
  lookZone = nil
  nightShown = nil
  sinceSky = SKY_SECONDS
  ambienceOn = true
  shotsOn = true
end

local function onUpdate(dtReal)
  if not data then return end
  sinceUpdate = sinceUpdate + dtReal
  clock = clock + dtReal
  sinceParticles = sinceParticles + dtReal
  sinceSky = sinceSky + dtReal
  if sky and sinceSky >= SKY_SECONDS then
    sinceSky = 0
    local ok, err = pcall(updateSky)
    if not ok then
      log('E', logTag, tostring(err) .. ', sky changes are off')
      sky = nil
    end
    local okNight, errNight = pcall(updateNightLights)
    if not okNight then log('E', logTag, tostring(errNight) .. ', night lights stay as they are') end
  end
  if emitters and sinceParticles >= PARTICLE_SECONDS then
    sinceParticles = 0
    local x, y = core_camera.getPositionXYZ()
    local ok, err = pcall(updateParticles, x, y)
    if not ok then
      log('E', logTag, tostring(err) .. ', particles are off')
      emitters = nil
    end
  end
  sinceSequences = sinceSequences + dtReal
  if sequences then
    local ok, err = true, nil
    if sinceSequences >= SEQUENCE_SECONDS then
      sinceSequences = 0
      local x, y = core_camera.getPositionXYZ()
      ok, err = pcall(pickSequences, x, y)
    end
    if ok then ok, err = pcall(playSequences) end
    if not ok then
      log('E', logTag, tostring(err) .. ', the geyser and fireworks are off')
      sequences = nil
    end
  end
  if sinceUpdate >= UPDATE_SECONDS then
    if sky and sky.zones then
      local okLook, errLook = pcall(updateLook, sinceUpdate)
      if not okLook then
        log('E', logTag, tostring(errLook) .. ', fog and bloom by zone are off')
        sky.zones = nil
        pcall(restoreBloom)
      end
    end
    sinceUpdate = 0
    local ok, err = pcall(refresh)
    if not ok then
      log('E', logTag, tostring(err) .. ', extras are off')
      data = nil
      stopAmbience()
      unpatchCity()
      return
    end
  end
  if ambienceOn then fadeSounds(dtReal) end
end

local function onClientEndMission()
  pcall(restoreBloom)
  data = nil
  stopAmbience()
  unpatchCity()
end

M.onExtensionLoaded = start
M.onClientPostStartMission = start
M.onClientEndMission = onClientEndMission
M.onUpdate = onUpdate

return M
