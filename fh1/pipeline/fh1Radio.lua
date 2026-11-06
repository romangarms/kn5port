-- FH1's radio on the fh1_* levels: Horizon Bass Arena, Pulse and Rocks with their DJs, jingles and festival interviews, then off.
-- ] and [ cycle the stations; an overlay shows the station and song when you tune or a song starts, as FH1's did.
-- All three stations play at once and the two not tuned are silent, so a station has moved on when you come back, as on a real radio.
-- Also plays FH1's pre-race and street race music near those races' starts, found in the level's time trials (races.py).
-- radio.py writes art/sound/fh1radio/radio.json and installs this file in the fh1_radio mod.

local M = {}

local logTag = 'fh1Radio'
local ROOT = '/art/sound/fh1radio/'
local SFX = 'AudioStream2D'
local OVER_DELAY = 1.0         -- seconds into a song's intro before the DJ talks over it
local DJ_GAP = 0.4             -- seconds between a DJ line and what follows it
local DJ_CHANCE = 0.5          -- a break is a DJ line this often, and always after maxTracksBetweenDJ songs without one
local OVER_CHANCE = 0.7        -- a DJ break talks over the next song's intro this often, when a line fits
local IDENT_CHANCE = 0.7       -- otherwise a jingle this often, else straight into the next song
local UPDATE_EVERY = 1500      -- seconds between festival interviews on a station, roughly
local OVERLAY_SECONDS = 5
local PLACE_NEAR, PLACE_FAR = 25, 120  -- metres: full volume within NEAR of a race start, silent beyond FAR
local PLACE_GAIN = 1.5         -- FH1's place volumes were for its 3D mixer; this scales them for a 2D stream
local PLACE_MERGE = 60         -- race starts closer than this share one source
local COLORS = { '#19c3ff', '#ff3d9a', '#ff8a1c' }

local data, running = nil, false
local stations = {}
local tuned = 1                -- 1 to #stations, or 0 for off
local clock = 0
local volume = 1
local places = {}              -- kind -> { {x, y, z} }
local placePlay = {}           -- kind -> { id, ends, recent }

local function approach(a, b, step)
  if a < b then return math.min(b, a + step) end
  return math.max(b, a - step)
end

local function newSource(file)
  local id = Engine.Audio.createSource(SFX, ROOT .. file)
  local s = id and scenetree.findObjectById(id)
  if not s then
    log('W', logTag, 'could not play ' .. file)
    return nil
  end
  s:setVolume(0)
  s:play(-1)
  return id
end

local function setVolume(id, v)
  local s = scenetree.findObjectById(id)
  if s then s:setVolume(v) end
end

local function kill(id)
  local s = id and scenetree.findObjectById(id)
  if s then
    s:stop(-1)
    s:delete()
  end
end

-- a random item not among the last n picked from this list
local function pick(list, recent, n)
  if not list or #list == 0 then return nil end
  n = math.min(n or 0, #list - 1)
  local choices = {}
  for i = 1, #list do
    local ok = true
    for j = math.max(1, #recent - n + 1), #recent do
      if recent[j] == i then ok = false break end
    end
    if ok then choices[#choices + 1] = i end
  end
  local i = choices[math.random(#choices)]
  recent[#recent + 1] = i
  if #recent > 64 then table.remove(recent, 1) end
  return list[i]
end

local function musicChannel()
  local ok, v = pcall(Engine.Audio.getChannelVolume, 'AudioChannelMusic', true)
  return (ok and type(v) == 'number') and v or 1
end

-- the overlay: an element added to BeamNG's UI page, shown for OVERLAY_SECONDS
local OVERLAY_JS = [[
(function(a){var d=document.getElementById('fh1radio');
if(!d){d=document.createElement('div');d.id='fh1radio';
d.style.cssText='position:fixed;left:2.5vw;top:20vh;z-index:9999;pointer-events:none;font-family:"Segoe UI",Arial,sans-serif;color:#fff;'+
'background:linear-gradient(90deg,rgba(10,10,20,.82),rgba(10,10,20,.55) 80%,rgba(10,10,20,0));padding:10px 48px 12px 14px;border-left:5px solid #fff;'+
'opacity:0;transform:translateX(-24px);transition:opacity .35s ease,transform .35s ease;text-shadow:0 1px 2px rgba(0,0,0,.6);min-width:220px';
['s','t','r'].forEach(function(k){var e=document.createElement('div');e.className='fh1radio-'+k;d.appendChild(e);});
d.children[0].style.cssText='font-size:13px;font-weight:700;letter-spacing:.18em;text-transform:uppercase';
d.children[1].style.cssText='font-size:22px;font-weight:600;margin-top:2px';
d.children[2].style.cssText='font-size:16px;opacity:.85';
document.body.appendChild(d);}
d.style.borderLeftColor=a.color;d.children[0].style.color=a.color;
d.children[0].textContent=a.station;d.children[1].textContent=a.title||'';d.children[2].textContent=a.artist||'';
d.children[1].style.display=a.title?'':'none';d.children[2].style.display=a.artist?'':'none';
d.style.opacity='1';d.style.transform='none';clearTimeout(window.fh1radioTimer);
window.fh1radioTimer=setTimeout(function(){d.style.opacity='0';d.style.transform='translateX(-24px)';},a.ms);})(__ARGS__);
]]

local function overlay(station, title, artist, color)
  local args = jsonEncode({ station = station, title = title, artist = artist, color = color or '#ffffff', ms = OVERLAY_SECONDS * 1000 })
  local ok, err = pcall(function() be:executeJS((OVERLAY_JS:gsub('__ARGS__', function() return args end))) end)
  if not ok then
    log('W', logTag, 'overlay: ' .. tostring(err))
    ui_message(station .. (title and ('\n' .. (artist or '') .. ' - ' .. title) or ''), OVERLAY_SECONDS, logTag, 'radio')
  end
end

local function hideOverlay()
  pcall(function() be:executeJS("(function(){var d=document.getElementById('fh1radio');if(d){d.style.opacity='0';}})();") end)
end

local function announce(i)
  local st = stations[i]
  if not st then
    overlay('Radio off', nil, nil, '#9a9aa6')
  else
    local song = st.song
    overlay(st.def.name, song and song.title, song and song.artist, COLORS[i])
  end
end

-- what a station plays next after its song reaches its jingle point: a queue ending with the next song
local function plan(st)
  local d, sys = st.def, data.system
  local song = pick(d.songs, st.recentSongs, d.noRepeat.Playlist)
  local q = {}
  st.sinceDJ = st.sinceDJ + 1
  if clock >= st.nextUpdate and #data.updates > 0 and #d.leadIn > 0 and #d.leadOut > 0 then
    st.nextUpdate = clock + UPDATE_EVERY * (0.75 + 0.5 * math.random())
    local u = pick(data.updates, st.recentUpdates, math.floor(#data.updates / 2))
    for _, line in ipairs({ pick(d.leadIn, st.recentLeadIn, 2), u, pick(d.leadOut, st.recentLeadOut, 2) }) do
      q[#q + 1] = { file = line.file, len = line.len, kind = 'dj', nextAt = line.len + DJ_GAP }
    end
    st.sinceDJ = 0
  elseif #d.dj > 0 and (st.sinceDJ > (sys.maxTracksBetweenDJ or 1) or math.random() < DJ_CHANCE) then
    st.sinceDJ = 0
    -- a line short enough to talk over the song's intro, before its vocals at songStart, when there is one
    local fitting = {}
    for _, l in ipairs(d.dj) do
      if l.len + OVER_DELAY <= song.songStart then fitting[#fitting + 1] = l end
    end
    if #fitting > 0 and math.random() < OVER_CHANCE then
      local line = pick(fitting, st.recentOver, math.floor(#fitting / 2))
      q[#q + 1] = { file = song.file, len = song.len, kind = 'music', nextAt = song.identAt, song = song, over = line }
      return q
    end
    local line = pick(d.dj, st.recentDJ, d.noRepeat.DJRegularDialogue)
    q[#q + 1] = { file = line.file, len = line.len, kind = 'dj', nextAt = line.len + DJ_GAP }
  elseif #d.idents > 0 and math.random() < IDENT_CHANCE then
    local id = pick(d.idents, st.recentIdents, d.noRepeat.Idents)
    q[#q + 1] = { file = id.file, len = id.len, kind = 'ident', nextAt = id.nextAt or id.len }
  end
  q[#q + 1] = { file = song.file, len = song.len, kind = 'music', nextAt = song.identAt, song = song }
  return q
end

local function play(st, i, item)
  local id = newSource(item.file)
  if id then st.sounds[#st.sounds + 1] = { id = id, kind = item.kind, ends = clock + item.len + 0.5 } end
  st.nextAt = clock + math.max(0.5, item.nextAt or item.len)
  if item.song then
    st.song = item.song
    if i == tuned then announce(i) end
  end
  if item.over then st.over = { at = clock + OVER_DELAY, line = item.over } end
end

local function level(kind)
  local sys = data.system
  if kind == 'music' then return sys.musicLevel or 1 end
  if kind == 'ident' then return sys.identLevel or 1 end
  return sys.dialogueLevel or 1
end

local function updateStation(st, i, dt, master)
  local sys = data.system
  if st.over and clock >= st.over.at then
    local line = st.over.line
    st.over = nil
    local id = newSource(line.file)
    if id then st.sounds[#st.sounds + 1] = { id = id, kind = 'dj', ends = clock + line.len + 0.5 } end
  end
  if clock >= st.nextAt then
    if #st.queue == 0 then st.queue = plan(st) end
    play(st, i, table.remove(st.queue, 1))
  end
  st.gain = approach(st.gain, i == tuned and 1 or 0, dt / math.max(0.05, sys.stationChangeFadeTimeUser or 0.25))
  local talking = false
  for _, s in ipairs(st.sounds) do
    if s.kind == 'dj' then talking = true break end
  end
  local duck = sys.duckValue or 0.3
  local fade = talking and (sys.duckFadeDownTime or 0.1) or (sys.duckFadeUpTime or 0.5)
  st.duck = approach(st.duck, talking and duck or 1, dt * (1 - duck) / math.max(0.05, fade))
  for k = #st.sounds, 1, -1 do
    local s = st.sounds[k]
    if clock >= s.ends then
      kill(s.id)
      table.remove(st.sounds, k)
    else
      setVolume(s.id, level(s.kind) * (s.kind == 'music' and st.duck or 1) * st.gain * master)
    end
  end
end

local function stopPlace(kind)
  local p = placePlay[kind]
  if p and p.id then kill(p.id) end
  if p then p.id = nil end
end

local function updatePlaces(master)
  if not data.places then return end
  local x, y, z = core_camera.getPositionXYZ()
  for kind, def in pairs(data.places) do
    local best = math.huge
    for _, p in ipairs(places[kind] or {}) do
      local dx, dy, dz = p[1] - x, p[2] - y, p[3] - z
      best = math.min(best, dx * dx + dy * dy + dz * dz)
    end
    best = math.sqrt(best)
    local v = def.volume * PLACE_GAIN * math.max(0, math.min(1, (PLACE_FAR - best) / (PLACE_FAR - PLACE_NEAR)))
    local p = placePlay[kind] or { recent = {} }
    placePlay[kind] = p
    if v <= 0 then
      stopPlace(kind)
    else
      if not p.id or clock >= p.ends then
        stopPlace(kind)
        local song = pick(def.songs, p.recent, math.floor(#def.songs / 2))
        if song then
          p.id = newSource(song.file)
          p.ends = clock + song.len
        end
      end
      if p.id then setVolume(p.id, v * master) end
    end
  end
end

-- race starts from the level's time trials: Street races get street race music, the rest pre-race music
local function findPlaces(levelName)
  places = {}
  local dir = '/gameplay/missions/' .. levelName .. '/timeTrial/'
  for _, file in ipairs(FS:findFiles(dir, 'info.json', -1, true, false) or {}) do
    local info = jsonReadFile(file)
    local pos = info and info.startTrigger and info.startTrigger.pos
    if pos and pos[1] then
      local kind = (info.description or ''):find('^Street') and 'street' or 'prerace'
      local list = places[kind] or {}
      places[kind] = list
      local near = false
      for _, p in ipairs(list) do
        if (p[1] - pos[1]) ^ 2 + (p[2] - pos[2]) ^ 2 < PLACE_MERGE ^ 2 then near = true break end
      end
      if not near then list[#list + 1] = { pos[1], pos[2], pos[3] or 0 } end
    end
  end
end

local function stopAll()
  for _, st in ipairs(stations) do
    for _, s in ipairs(st.sounds) do kill(s.id) end
  end
  stations = {}
  for kind in pairs(placePlay) do stopPlace(kind) end
  placePlay = {}
  running = false
  hideOverlay()
end

local function start()
  stopAll()
  local mission = getMissionFilename()
  local levelName = mission and mission:match('levels/([^/]+)/')
  if not levelName or not levelName:find('^fh1_') then return end
  if not FS:fileExists(ROOT .. 'radio.json') then
    log('E', logTag, ROOT .. 'radio.json is missing: run radio.py')
    return
  end
  data = jsonReadFile(ROOT .. 'radio.json')
  clock = 0
  for i, def in ipairs(data.stations) do
    stations[i] = { def = def, sounds = {}, queue = {}, nextAt = 0, gain = i == tuned and 1 or 0, duck = 1, sinceDJ = 0,
                    nextUpdate = UPDATE_EVERY * math.random(), recentSongs = {}, recentDJ = {}, recentIdents = {}, recentUpdates = {},
                    recentLeadIn = {}, recentLeadOut = {}, recentOver = {} }
    -- each station starts on a random song rather than all on a DJ line
    local song = pick(def.songs, stations[i].recentSongs, 0)
    stations[i].queue = { { file = song.file, len = song.len, kind = 'music', nextAt = song.identAt, song = song } }
  end
  local ok, err = pcall(findPlaces, levelName)
  if not ok then log('W', logTag, 'race starts: ' .. tostring(err)) end
  running = true
end

local function onUpdate(dtReal)
  if not running then return end
  clock = clock + dtReal
  local master = volume * musicChannel()
  local ok, err = pcall(function()
    for i, st in ipairs(stations) do updateStation(st, i, dtReal, master) end
    updatePlaces(master)
  end)
  if not ok then
    log('E', logTag, tostring(err) .. ', radio is off')
    stopAll()
  end
end

-- ] and [ through fh1radio.json's actions: stations 1 to 3, then off
local function onFh1RadioTune(step)
  if not running then return end
  tuned = (tuned + (step or 1)) % (#stations + 1)
  announce(tuned)
end

M.onExtensionLoaded = start
M.onClientPostStartMission = start
M.onClientEndMission = stopAll
M.onExtensionUnloaded = stopAll
M.onUpdate = onUpdate
M.onFh1RadioTune = onFh1RadioTune
M.setVolume = function(v) volume = math.max(0, v or 1) end
M.tune = function(i) tuned = math.max(0, math.min(#stations, i or 0)) announce(tuned) end

return M
