-- Colorado extras: FH1's regional ambience and placed sounds, nearby particles, the sky for the time of day, and the current road on the minimap's location line.
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
-- nightlights.py's point lights at FH1's lamps: at night only the nearest MAX_LAMPS within LAMP_FAR metres of the camera are on, as every lit light costs.
-- Not stock nightLight, which would switch all of them on at once
local LAMP_SECONDS = 2
local MAX_LAMPS = 20
local LAMP_FAR = 250

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
local sinceSky = 0
local lamps -- fh1_lamps.json: { name, x, y } per light
local lampsOn = {} -- light name -> true
local sinceLamps = 0
local clock = 0
local shots = {} -- playing one-shots: { id, ends }
local nextShot = {} -- one-shot group -> clock time
local sources = {} -- sound file -> { id, volume }
local targets = {} -- sound file -> volume to fade towards

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

local function setLamp(name, on)
  local obj = scenetree.findObject(name)
  if obj and obj.setLightEnabled then obj:setLightEnabled(on) end
end

local function updateLamps(x, y)
  local hour = hourNow()
  local near = {}
  if hour and (hour >= NIGHT_FROM or hour < NIGHT_TO) then
    for _, l in ipairs(lamps) do
      local dx, dy = x - l[2], y - l[3]
      local d = dx * dx + dy * dy
      if d < LAMP_FAR * LAMP_FAR then table.insert(near, { name = l[1], d = d }) end
    end
    table.sort(near, function(a, b) return a.d < b.d end)
  end
  local want = {}
  for i = 1, math.min(#near, MAX_LAMPS) do want[near[i].name] = true end
  for name in pairs(lampsOn) do
    if not want[name] then setLamp(name, false) end
  end
  for name in pairs(want) do
    if not lampsOn[name] then setLamp(name, true) end
  end
  lampsOn = want
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
  skyBake = nil
  nightShown = nil
  sinceSky = SKY_SECONDS
  lamps = FS:fileExists(levelDir .. 'fh1_lamps.json') and jsonReadFile(levelDir .. 'fh1_lamps.json') or nil
  lampsOn = {}
  sinceLamps = LAMP_SECONDS
  ambienceOn = true
  shotsOn = true
end

local function onUpdate(dtReal)
  if not data then return end
  sinceUpdate = sinceUpdate + dtReal
  clock = clock + dtReal
  sinceParticles = sinceParticles + dtReal
  sinceSky = sinceSky + dtReal
  sinceLamps = sinceLamps + dtReal
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
  if lamps and sinceLamps >= LAMP_SECONDS then
    sinceLamps = 0
    local x, y = core_camera.getPositionXYZ()
    local ok, err = pcall(updateLamps, x, y)
    if not ok then
      log('E', logTag, tostring(err) .. ', lamps are off')
      lamps = nil
    end
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
  if sinceUpdate >= UPDATE_SECONDS then
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
  data = nil
  lamps = nil
  lampsOn = {}
  stopAmbience()
  unpatchCity()
end

M.onExtensionLoaded = start
M.onClientPostStartMission = start
M.onClientEndMission = onClientEndMission
M.onUpdate = onUpdate

return M
