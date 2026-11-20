-- Engine substitute for protocol tests; this is NOT a Noita runtime.
frame, realtime, focused, loaded, blocked, menu = 0, 0, true, true, false, false
globals, flags, entities, calls, cross, rpc_handlers = {}, {}, {}, {}, {}, {}
calls.camera_free, calls.camera = false, { 100, 100 }
virtual_files = { ['data/entities/animals/boss_centipede/boss_centipede_update.lua'] = READ('tests/boss_lifecycle_fixture.lua') }
ctx = { my_id = PEER, host_id = "A", is_host = PEER == "A", cap = {}, players = {},
    proxy_opt = { game_mode = "local_health", no_notplayer = true, health_lost_on_revive = 80 },
    is_texting = false, is_paused = false, is_inventory_open = false }
settings = { enabled = true, statue = true, seconds = 5, allowance = "unlimited", count = 3,
    mountain = true, boss_unlimited = true, boss_recover = true }
function GlobalsGetValue(k, d) return globals[k] or d end
function GlobalsSetValue(k, v) globals[k] = v end
function ModSettingGet(k) return settings[k:gsub('quant%.ew%.coop_', '')] end
function ModSettingSet(k,v)settings[k:gsub('quant%.ew%.coop_', '')]=v end
function GameHasFlagRun(k) return flags[k] == true end
function GameAddFlagRun(k) flags[k] = true end
function GameRemoveFlagRun(k) flags[k] = nil end
function GameGetFrameNum() return frame end
function GameGetRealWorldTimeSinceStarted() return realtime end
function GameIsInventoryOpen() return menu end
function GuiCreate() return 1 end
function GuiStartFrame() calls.ui = ''; calls.images = {} end
function GuiText(_, x, y, t) calls.ui = calls.ui .. t .. '\n' end
function GuiGetScreenDimensions() return screen_w or 427, screen_h or 240 end
function GuiGetTextDimensions(_, text) return #text * 5, font_height or 8 end
function GuiColorSetForNextWidget() end
function GuiZSet() end
function GuiImage(_, id, x, y, path, alpha, sx, sy)
    calls.images[#calls.images+1] = {id=id,x=x,y=y,width=sx*2,height=sy*2,alpha=alpha,path=path}
end
function GuiImageNinePiece(_,id,x,y,w,h,alpha)
    calls.images[#calls.images+1]={id=id,x=x,y=y,width=w,height=h,alpha=alpha}
end
function GamePrint(t) calls.message = t end
function GamePlaySound(bank,event) calls.last_sound = event end
function GameGetWorldStateEntity() return 1 end
function EntityInflictDamage() end -- replica health effects; HP assignment is tested separately
function GameSetCameraFree(v) calls.camera_free = v end
function GameSetCameraPos(x, y) calls.camera = { x, y } end
function GameGetCameraPos() return unpack(calls.camera) end
function MagicNumbersGetValue(key) return key == 'VIRTUAL_RESOLUTION_X' and 427 or 0 end
function GameGetIsGamepadConnected() return false end
function InputIsKeyJustDown() return false end
function InputIsJoystickButtonJustDown() return false end
function DEBUG_GetMouseWorld() return EntityGetTransform(ctx.my_player.entity) end
function ModTextFileGetContent(path) return assert(virtual_files[path]) end
function ModTextFileSetContent(path, text) virtual_files[path] = text end
function DoesWorldExistAt() return loaded end
function RaytraceSurfaces() return false end
function RaytraceSurfacesAndLiquiform(x1, y, x2) return blocked or (obstacle and obstacle(x1, y, x2)) end
function BiomeMapGetName() return biome or "$biome_holymountain" end
function SessionNumbersGetValue() return tostring(ng or 0) end
function EntityGetIsAlive(e) return entities[e] and entities[e].alive or false end
function EntityGetTransform(e) return entities[e].x, entities[e].y end
function EntityGetFilename(e) return entities[e].filename end
function EntityGetName(e) return entities[e].name end
function EntityHasTag(e, tag) return entities[e] and entities[e].tags[tag] == true end
function EntityAddTag(e, tag) entities[e].tags[tag] = true end
function EntityRemoveTag(e, tag) entities[e].tags[tag] = nil end
function EntityGetParent(e) return entities[e].parent or 0 end
function EntityGetRootEntity(e)
    while entities[e].parent do e = entities[e].parent end
    return e
end
function EntityGetFirstComponentIncludingDisabled(e, name) return entities[e] and entities[e].c[name] end
EntityGetFirstComponent = EntityGetFirstComponentIncludingDisabled
function EntityGetComponentIncludingDisabled(e, name)
    local c = EntityGetFirstComponent(e, name)
    return c and { c } or {}
end
function EntityGetAllChildren() return {} end
function EntityGetInRadiusWithTag() return {} end
function ComponentHasTag(c,tag)return c._tags and c._tags[tag]==true or false end
function ComponentAddTag(c,tag)c._tags=c._tags or{};c._tags[tag]=true end
function ComponentRemoveTag(c,tag)if c._tags then c._tags[tag]=nil end end
function ComponentGetValue2(c, key)
    if c[key] == nil then return false end
    if type(c[key]) == 'table' then return unpack(c[key]) end
    return c[key]
end
function ComponentSetValue2(c, key, ...)
    local values = {...}; c[key] = #values > 1 and values or values[1]
end
function EntityRemoveFromParent(e) entities[e].parent = nil end
function EntityKill(e) entities[e].alive = false end
function EntitySetComponentIsEnabled(_,c,value) c._enabled=value end
function ComponentGetIsEnabled(c) return c._enabled~=false end
function EntitySetComponentsWithTagEnabled() end
function EntityAddComponent2(e, name, fields) entities[e].c[name] = fields or {}; return entities[e].c[name] end
function EntityRemoveComponent(e, component)
    for name, c in pairs(entities[e].c) do if c == component then entities[e].c[name] = nil end end
end
function LoadGameEffectEntityTo() calls.protection = true end
function EntityLoad(path,x,y)
    if path:find('/ritual/emitter.xml',1,true)then
        calls.emitters_created=(calls.emitters_created or 0)+1
        local e=1000+calls.emitters_created
        entities[e]={alive=true,x=x,y=y,tags={ew_no_enemy_sync=true,ew_ritual_cosmetic=true},c={ParticleEmitterComponent={},LifetimeComponent={}}}
        return e
    end
    calls.effect = true; return 400
end
function EntitySetTransform(e,x,y) entities[e].x,entities[e].y=x,y end
function async(fn) fn() end
function wait() end
function EntityRemoveStainStatusEffect() end
function EntityRemoveIngestionStatusEffect() end
function GetUpdatedEntityID() return updated or 200 end
function ModLuaFileAppend(a, b) calls.append = { a, b } end

local cache = {}
function dofile_once(path)
    if cache[path] then return cache[path] end
    local value
    if path == "data/scripts/status_effects/status_list.lua" then status_effects = {}; value = {}
    elseif path:find('/polymorph/polymorph.lua', 1, true) then
        value = { switch_entity = function(e) ctx.my_player.entity = e; calls.switch = (calls.switch or 0) + 1 end }
    elseif path:find('perk_fns.lua', 1, true) or path:find('nickname.lua', 1, true)
        or path:find('player_cosmetics.lua', 1, true) then value = {}
    else value = assert(loadstring(READ(path), '@' .. path))() end
    cache[path] = value
    return value
end

local bitser = dofile_once('mods/quant.ew/files/lib/bitser.lua')
local base64 = dofile_once('mods/quant.ew/files/resource/base64.lua')
function encode(v) return base64.encode(bitser.dumps(v)) end
function decode(v) return bitser.loads(base64.decode(v)) end
local ffi = require('ffi')
local old_load = ffi.load
ffi.load = function(name)
    if name == 'SDL2' or name == 'libSDL2-2.0.so.0' then
        return { SDL_GetKeyboardFocus = function() if focused then return 1 end end }
    end
    return old_load(name)
end

net = { new_rpc_namespace = function()
    local options = {}
    local n = { opts_reliable = function() options.reliable = true end,
        opts_everywhere = function() options.everywhere = true end }
    return setmetatable(n, { __newindex = function(t, name, fn)
        rpc_handlers[name] = fn
        local everywhere = options.everywhere
        options = {}
        rawset(t, name, function(...)
            SEND(name, encode({...}))
            if everywhere then
                ctx.rpc_peer_id, ctx.rpc_player_data = ctx.my_id, ctx.my_player
                fn(...)
                ctx.rpc_peer_id, ctx.rpc_player_data = nil, nil
            end
        end)
    end })
end }
function receive(sender, name, payload)
    ctx.rpc_peer_id, ctx.rpc_player_data = sender, ctx.players[sender]
    rpc_handlers[name](unpack(decode(payload)))
    ctx.rpc_peer_id, ctx.rpc_player_data = nil, nil
end

util = {
    add_cross_call = function(name, fn) cross[name] = fn end,
    replace_text_in = function(path, old, new)
        calls.replacements = calls.replacements or {}; calls.replacements[path] = new
    end,
    do_i_own = function(e) return entities[e].owned ~= false end,
    get_ent_health = function(e) local c = entities[e].c.DamageModelComponent; return c and c.hp or 0, c and c.max_hp or 0, c ~= nil end,
    set_ent_health = function(e, hp) local c = entities[e].c.DamageModelComponent; if c then c.hp, c.max_hp = hp[1], hp[2] end end,
    serialize_entity = function(e) return bitser.dumps(entities[e]) end,
    deserialize_entity = function(data, x, y)
        local e = decode(base64.encode(data)); e.x, e.y, e.alive = x, y, true
        entities[300] = e; calls.deserialize = (calls.deserialize or 0) + 1; return 300
    end,
}
np = {
    SetPlayerEntity = function(e) calls.set_player = e end,
    SetActiveHeldEntity = function(root, item) entities[root].c.Inventory2Component.mActiveItem = item end,
}
player_fns = { get_player_data_by_local_entity_id = function(e)
    for _, p in pairs(ctx.players) do if p.entity == e then return p end end
end }
inventory_helper = {}
function CrossCall(name, ...) return cross[name](...) end

entities[100] = { alive = true, x = 100, y = 100, name = 'carrier', tags = {}, c = {
    ControlsComponent = { enabled = true }, Inventory2Component = { mActiveItem = 200 },
    DamageModelComponent = { hp = 8, max_hp = 8 } } }
entities[101] = { alive = true, x = 100, y = 100, name = 'inventory_quick', tags = {}, c = {}, parent = 100 }
entities[200] = { alive = true, x = 900, y = 900, name = 'heart_statue', tags = { heart_statue = true, ew_notplayer = true }, c = {}, parent = 101 }
ctx.players.A = { peer_id = 'A', entity = 100, name = 'Alice', status = { is_alive = true } }
ctx.players.B = { peer_id = 'B', entity = 200, name = 'Bob', status = { is_alive = false } }
ctx.my_player = ctx.players[PEER]
if PEER == 'B' then
    flags.ew_flag_notplayer_active = true
    globals.ew_coop_death = '1'
    local saved = { alive = true, x = 9, y = 9, name = 'DEBUG_NAME:player', tags = {}, c = {
        DamageModelComponent = { hp = 1, max_hp = 8 }, ControlsComponent = { enabled = true },
        PlatformShooterPlayerComponent = { center_camera_on_this_entity = true, move_camera_with_aim = true },
        WalletComponent = { money = 12345 } },
        belongings = { wands = { 'wand A', 'wand B' }, spells = { 'spark bolt' }, inventory = { 'potion' }, perks = { 'shield' } } }
    globals.ew_local_player_dead = encode(saved)
end
function load_modules()
    coop = assert(loadstring(READ('mods/quant.ew/files/system/coop_revival/coop_revival.lua')))()
    health = assert(loadstring(READ('mods/quant.ew/files/system/local_health/local_health.lua')))()
    coop.on_local_player_spawn()
    spectate = dofile_once('mods/quant.ew/files/system/spectate/spectate.lua')
end
-- Model the real ordering: spectator pre-update -> engine component camera ->
-- cooperative post-update. Restoration does not reset global camera mode.
function camera_system()
    if calls.camera_free then return end
    local e = ctx.my_player.entity
    local c = EntityGetFirstComponentIncludingDisabled(e, 'PlatformShooterPlayerComponent')
    if c then
        if c.center_camera_on_this_entity then calls.camera = { EntityGetTransform(e) }
        elseif c.mDesiredCameraPos then calls.camera = { unpack(c.mDesiredCameraPos) } end
    end
end
function tick()
    frame, realtime = frame + 1, realtime + 1/60
    if ctx.is_host then coop.on_world_update_host() end
    coop.on_world_update()
    spectate.on_world_update()
    camera_system()
    coop.on_world_update_post()
end
