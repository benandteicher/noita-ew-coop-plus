local rules = dofile_once("mods/quant.ew/files/system/coop_revival/rules.lua")
local placement = dofile_once("mods/quant.ew/files/system/coop_revival/placement.lua")
local bitser = dofile_once("mods/quant.ew/files/lib/bitser.lua")
local base64 = dofile_once("mods/quant.ew/files/resource/base64.lua")
local spectate = dofile_once("mods/quant.ew/files/system/spectate/spectate.lua")
local hud = dofile_once("mods/quant.ew/files/system/coop_revival/hud.lua")
local visuals = dofile_once("mods/quant.ew/files/system/coop_revival/visuals.lua")
local mountains = dofile_once("mods/quant.ew/files/system/coop_revival/mountains.lua")
local return_ui = dofile_once("mods/quant.ew/files/system/coop_revival/return_ui.lua")
local rpc = net.new_rpc_namespace()
local module, state = {}, nil
local channel, arrival, grant
local host_arrivals = {}
local portal_notices, portal_sequence = {}, 0
local offers, host_ng = {}, nil
local return_cancel_frames = {}
local held_target, placement_blocked
local gui = GuiCreate()
local function get_setting(key) return ModSettingGet("quant.ew.coop_" .. key) end

local function clear_grant()
    grant = nil
    placement_blocked = nil
    spectate.clear_camera_position()
end

local function read(key)
    local value = GlobalsGetValue(key, "")
    if value == "" then return nil end
    local ok, result = pcall(function() return bitser.loads(base64.decode(value)) end)
    if not ok then error("Cooperative revival save is unreadable: " .. key) end
    return result
end

local function write(key, value)
    GlobalsSetValue(key, base64.encode(bitser.dumps(value)))
end

local function enabled()
    return state ~= nil and state.rules.enabled
end

local function living(peer)
    local p = ctx.players[peer]
    return p and not p.dc and p.entity and EntityGetIsAlive(p.entity)
        and p.status and p.status.is_alive and not EntityHasTag(p.entity, "heart_statue")
end

local function any_living()
    for peer in pairs(ctx.players) do if living(peer) then return true end end
    return false
end

local function save()
    if ctx.is_host and state then write("ew_coop_run_v1", state) end
end

rpc.opts_reliable()
function rpc.snapshot(value)
    if ctx.rpc_peer_id ~= ctx.host_id then return end
    state = value
end

local function publish()
    if not state then return end
    save()
    rpc.snapshot(state)
end

-- Existing rules stay locked. Only the three new runtime options can change.
local function initialize_host()
    if not ctx.is_host or state then return end
    state = read("ew_coop_run_v1")
    if not state then
        local r = rules.settings(get_setting)
        local compatible = ctx.proxy_opt.game_mode == "local_health" and ctx.proxy_opt.no_notplayer == true
            and not ctx.proxy_opt.pvp and not ctx.proxy_opt.perma_death
        r.mismatch, r.enabled = r.enabled and not compatible, r.enabled and compatible
        state = rules.new(r)
        save()
    end
    rules.migrate(state)
    rules.runtime(state, get_setting)
    if not state.return_run then
        local serial=(tonumber(ModSettingGet("quant.ew.return_run_serial"))or 0)+1
        ModSettingSet("quant.ew.return_run_serial",serial)
        state.return_run=ctx.host_id..":"..serial
    end
    -- Occupancy is reconstructed from current bodies, never from saved flags.
    -- Reconcile a previously in-flight optional return through the owner receipt.
    for _,p in pairs(state.players)do
        if p.pending and p.pending.reason=="return" then p.pending.cancel="Confirm a new Holy Mountain return." end
    end
    host_ng=mountains.ng()
    save()
end

function module.on_local_player_spawn()
    -- World Globals must have been restored before reading Continue's ledger.
    -- Register RPCs at mod init, but defer save initialization until player spawn.
    return_ui.restore_owned(ctx.my_player.entity)
    initialize_host()
    if ctx.is_host then publish() end
end

local function finish(peer, death, id, success)
    local t = state.players[peer] and state.players[peer].pending
    if rules.finish(state, peer, death, id, success) then
        publish()
        if success and t and t.reason == "statue" then
            local x, y = t.x, t.y
            local carrier = t.carrier and ctx.players[t.carrier]
            if carrier and carrier.entity and EntityGetIsAlive(carrier.entity) then x, y = EntityGetTransform(carrier.entity) end
            -- Cosmetic failure must never change an already resolved transaction.
            pcall(visuals.completed, peer, death, x, y)
        end
    end
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.receipt(death, id, success, run)
    if ctx.is_host and enabled() then
        local p=state.players[ctx.rpc_peer_id]
        if run and(run~=state.return_run or not p or not p.pending or p.pending.reason~="return")then return end
        if p and p.pending and p.pending.reason=="return"and p.pending.return_run~=run then return end
        finish(ctx.rpc_peer_id, death, id, success)
    end
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.presence(death, receipt, location)
    if not ctx.is_host or not enabled() then return end
    local peer = ctx.rpc_peer_id
    local p = rules.player(state, peer)
    if receipt and p.pending and(not receipt.return_run or p.pending.reason=="return")
        and(p.pending.reason~="return"or receipt.return_run==p.pending.return_run)then finish(peer, receipt.death, receipt.id, true) end
    -- Death counters belong to the player and survive Continue/reconnect.
    if death and death > p.resolved and death >= p.death then
        if p.death ~= death then
            p.death = death
            p.location = nil
            -- An arrival may already have witnessed this exact death through
            -- health status, ahead of the reliable presence notification.
            if p.auto and p.auto.death ~= death then p.auto = nil end
            save()
        end
        if type(location)=="table" and location.death==death and mountains.finite(location.x)
            and mountains.finite(location.y) and type(location.ng)=="number" then
            p.location=location;save()
        end
    elseif not death and not p.pending and p.death ~= 0 then
        p.death, p.auto = 0, nil
        save()
    end
end

local function send_presence()
    local death
    if GameHasFlagRun("ew_flag_notplayer_active") then
        death = tonumber(GlobalsGetValue("ew_coop_death", "0"))
    end
    -- Dense RPC arguments: a nil receipt must not truncate a later location
    -- when the network adapter unpacks its serialized Lua argument table.
    rpc.presence(death or false, read("ew_coop_receipt")or false,(death and read("ew_coop_death_location"))or false)
end

rpc.opts_reliable()
function rpc.restore(peer, transaction)
    if ctx.rpc_peer_id ~= ctx.host_id or peer ~= ctx.my_id or not enabled() then return end
    if transaction.reason=="return"and transaction.return_run~=state.return_run then return end
    local receipt = read("ew_coop_receipt")
    if receipt and receipt.death >= transaction.death then
        -- A success whose acknowledgement was lost is replayed, never restored twice.
        rpc.receipt(receipt.death, receipt.id, true, receipt.return_run)
        return
    end
    if transaction.reason=="return" then
        local terminal=read("ew_coop_return_terminal")or{}
        if terminal[tostring(transaction.id)]==transaction.death then rpc.receipt(transaction.death,transaction.id,false,transaction.return_run);return end
    end
    if not grant then grant = transaction end
end

local function issue(peer, death, reason, x, y, carrier, offer)
    local t = rules.reserve(state, peer, death, reason, x, y, carrier, offer)
    if t then
        save() -- Persist reservation before telling the owner to restore.
        if peer == ctx.my_id then grant = t else rpc.restore(peer, t) end
    end
    return t
end

local unavailable="Mountain return is no longer available."
local function return_failure(peer,t)
    if not state or not state.rules.offer_return then return "Holy Mountain return was disabled." end
    if not enabled()or ctx.run_ended or not state.rules.mountain or not t or not t.return_room
        or t.return_room.ng~=mountains.ng() then return unavailable end
    if rules.return_remaining(state,peer,t.return_room.stage)==0 then return "No Holy Mountain returns left for this stage." end
    if not ctx.players[peer]or ctx.players[peer].dc then return unavailable end
    if not living(t.carrier)then
        for carrier in pairs(ctx.players)do
            if living(carrier)then
                local x,y=EntityGetTransform(ctx.players[carrier].entity);local room=mountains.room(x,y)
                if room and room.key==t.return_room.key and mountains.matches(t.return_location,room)then return unavailable end
            end
        end
        return "No teammate is available in the Holy Mountain."
    end
    local x,y=EntityGetTransform(ctx.players[t.carrier].entity)
    local room=mountains.room(x,y)
    if not room or room.key~=t.return_room.key then return "Your teammate left the Holy Mountain." end
    if not mountains.matches(t.return_location,room)then return unavailable end
end
local function return_valid(peer,t)return return_failure(peer,t)==nil end

rpc.opts_reliable();rpc.opts_everywhere()
function rpc.return_offer(peer,run,value,reason)
    if ctx.rpc_peer_id==ctx.host_id and peer==ctx.my_id and state and run==state.return_run then return_ui.receive(value,reason)end
end

local function cancel_owned_return(t,reason)
    local receipt=read("ew_coop_receipt")
    if receipt and receipt.death>=t.death then rpc.receipt(receipt.death,receipt.id,true,receipt.return_run);return end
    local terminal=read("ew_coop_return_terminal")or{}
    local fresh=terminal[tostring(t.id)]~=t.death
    terminal[tostring(t.id)]=t.death;write("ew_coop_return_terminal",terminal)
    if grant and grant.id==t.id then clear_grant()end
    if fresh and GameHasFlagRun("ew_flag_notplayer_active")and tonumber(GlobalsGetValue("ew_coop_death","0"))==t.death then
        return_ui.receive(nil,reason)
    end
    rpc.receipt(t.death,t.id,false,t.return_run)
end

rpc.opts_reliable();rpc.opts_everywhere()
function rpc.cancel_return(peer,run,id,death,reason)
    if ctx.rpc_peer_id~=ctx.host_id or peer~=ctx.my_id or not state or run~=state.return_run then return end
    cancel_owned_return({id=id,death=death,return_run=run},reason)
end

rpc.opts_reliable();rpc.opts_everywhere()
function rpc.mountain_return(death,id,run)
    if not ctx.is_host or not enabled()or ctx.run_ended then return end
    if run~=state.return_run then return end
    if rules.runtime(state,get_setting)then publish()end
    local peer=ctx.rpc_peer_id;local p=state.players[peer];local offer=offers[peer]
    if not p or p.pending or p.auto or living(peer)then return end
    local t=offer and{carrier=offer.carrier,return_room=offer.room,return_location=p.location}
    if not offer or offer.id~=id or offer.percent~=state.rules.mountain_percent or death~=p.death or death~=offer.death or not return_valid(peer,t)
        or rules.return_remaining(state,peer,offer.room.stage)==0 then
        rpc.return_offer(peer,state.return_run,false,return_failure(peer,t)or unavailable);return
    end
    local x,y=EntityGetTransform(ctx.players[offer.carrier].entity)
    if issue(peer,death,"return",x,y,offer.carrier,offer)then
        offer.pending=true;offer.percent=state.rules.mountain_percent
        rpc.return_offer(peer,state.return_run,offer)
    end
end

local function update_returns()
    if mountains.ng()~=host_ng then
        for peer in pairs(offers)do rpc.return_offer(peer,state.return_run,false,unavailable)end
        host_ng=mountains.ng();host_arrivals={};portal_notices={};offers={}
        for _,p in pairs(state.players)do if p.pending and p.pending.reason=="return"then p.pending.cancel=unavailable end end
    end
    local now=GameGetFrameNum()
    for peer,player in pairs(ctx.players)do
        local p=state.players[peer];local old=offers[peer];local t=p and p.pending
        if t and t.reason=="return"then
            if not t.cancel then local reason=return_failure(peer,t);if reason then t.cancel=reason;save()end end
            if t.cancel then
                if now-(return_cancel_frames[t.id]or-math.huge)>=30 then
                    return_cancel_frames[t.id]=now;rpc.cancel_return(peer,state.return_run,t.id,t.death,t.cancel)
                end
            elseif now%30==0 then
                rpc.return_offer(peer,state.return_run,{id=t.offer,run=state.return_run,ng=t.return_room.ng,death=t.death,name=ctx.players[t.carrier].name,carrier=t.carrier,percent=t.percent,pending=true})
            end
        else
            local candidate
            if not player.dc and p and p.death>p.resolved and not living(peer)and not p.pending and not p.auto
                and state.rules.offer_return and state.rules.mountain and p.location then
                local ids={};for carrier in pairs(ctx.players)do ids[#ids+1]=carrier end;table.sort(ids)
                -- Prefer the existing destination while it remains valid.
                if old then table.insert(ids,1,old.carrier)end
                for _,carrier in ipairs(ids)do
                    if living(carrier)then
                        local x,y=EntityGetTransform(ctx.players[carrier].entity);local room=mountains.room(x,y)
                        if room and mountains.matches(p.location,room)and rules.return_remaining(state,peer,room.stage)>0 then
                            candidate={carrier=carrier,room=room};break
                        end
                    end
                end
            end
            if candidate then
                local same=old and old.death==p.death and old.carrier==candidate.carrier and old.room.key==candidate.room.key
                    and old.percent==state.rules.mountain_percent and not old.pending
                if not same then state.return_serial=state.return_serial+1;candidate.id=state.return_run..":"..state.return_serial;save()
                else candidate.id=old.id end
                candidate.run=state.return_run;candidate.death=p.death;candidate.name=ctx.players[candidate.carrier].name;candidate.percent=state.rules.mountain_percent
                offers[peer]=candidate
                if not same or now%30==0 or old.percent~=candidate.percent then rpc.return_offer(peer,state.return_run,candidate)end
            elseif old then
                offers[peer]=nil
                local reason
                if p and p.death>p.resolved and not living(peer)and not p.auto then
                    reason=return_failure(peer,{carrier=old.carrier,return_room=old.room,return_location=p.location})or unavailable
                end
                rpc.return_offer(peer,state.return_run,false,reason)
            end
        end
    end
end

local function held_by(carrier, target)
    if not living(carrier) then return false end
    local c, p = ctx.players[carrier], ctx.players[target]
    if not p or p.dc or not p.entity or not EntityHasTag(p.entity, "heart_statue") then return false end
    local inv = EntityGetFirstComponentIncludingDisabled(c.entity, "Inventory2Component")
    return inv and ComponentGetValue2(inv, "mActiveItem") == p.entity
        and EntityGetRootEntity(p.entity) == c.entity
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.statue_complete(target, death, ticks)
    if not ctx.is_host or not enabled() or not state.rules.statue then return end
    local carrier = ctx.rpc_peer_id
    local p = state.players[target]
    if type(ticks) ~= "number" or ticks < state.rules.seconds * 60 or not p or p.death ~= death
        or not held_by(carrier, target) then return end
    local x, y = EntityGetTransform(ctx.players[carrier].entity)
    issue(target, death, "statue", x, y, carrier)
end

local function capture_deaths()
    local deaths = {}
    for peer, player in pairs(ctx.players) do
        if not player.dc then
            local p = state.players[peer]
            local death = player.status and not player.status.is_alive and player.status.coop_death
            if peer == ctx.my_id and GameHasFlagRun("ew_flag_notplayer_active") then
                death = tonumber(GlobalsGetValue("ew_coop_death", "0"))
            end
            if p and p.death > p.resolved and (type(death) ~= "number" or death < p.death) then death = p.death end
            if death and death > (p and p.resolved or 0) then deaths[peer] = death end
        end
    end
    return deaths
end

local function automatic(reason, x, y, deaths)
    if not any_living() then return end
    for peer, death in pairs(deaths or capture_deaths()) do
        local p = rules.player(state, peer)
        local player = ctx.players[peer]
        if player and not player.dc and type(death) == "number" and death == math.floor(death)
            and death > p.resolved and (death == p.death or death == p.resolved + 1) then
            -- Freeze the event's exact death IDs. No grace window or later
            -- death can inherit an already visited checkpoint's opportunity.
            p.auto = p.auto or { death = death, reason = reason, x = x, y = y }
        end
    end
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.portal_notice(x, y, ng, sequence, deaths)
    if not ctx.is_host or not enabled() or type(sequence) ~= "number"or sequence~=math.floor(sequence)
        or not mountains.finite(x)or not mountains.finite(y)or ng~=mountains.ng()then return end
    local captured = capture_deaths()
    for peer, death in pairs(type(deaths) == "table" and deaths or {}) do
        if type(death) == "number" and (not captured[peer] or death > captured[peer]) then captured[peer] = death end
    end
    -- Freeze host/entrant knowledge at the early reliable portal notification,
    -- before destination streaming. Never sweep in subsequent deaths on arrival.
    portal_notices[ctx.rpc_peer_id] = { sequence = sequence, x = x, y = y, ng = ng, deaths = captured }
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.mountain_arrived(x, y, ng, deaths, sequence)
    if not ctx.is_host or not enabled() then return end
    -- Position updates may trail this reliable event. Validate once the host's
    -- replica catches up, instead of losing a valid arrival to packet ordering.
    if type(x) ~= "number" or type(y) ~= "number" or type(ng) ~= "number" then return end
    if sequence and ng~=mountains.ng()then return end
    local notice = portal_notices[ctx.rpc_peer_id]
    if sequence and (not notice or notice.sequence ~= sequence or notice.x ~= x or notice.y ~= y or notice.ng ~= ng) then return end
    host_arrivals[ctx.rpc_peer_id] = { x = x, y = y, ng = ng,
        deaths = notice and notice.deaths or capture_deaths() }
    portal_notices[ctx.rpc_peer_id] = nil
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.recovery_feedback(peer, message)
    if ctx.rpc_peer_id == ctx.host_id and peer == ctx.my_id then GamePrint(message) end
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.manual_recovery(death)
    if not ctx.is_host then return end
    local peer = ctx.rpc_peer_id
    local player = ctx.players[peer]
    if not player or player.dc then return end
    local function feedback(message) rpc.recovery_feedback(peer, message) end
    if ctx.run_ended then feedback("/revive: run ended."); return end
    if not enabled() then feedback("/revive: requires Cooperative Revival in Local Health / Alternate."); return end
    -- Poll only the host's runtime options, including requests arriving between ticks.
    if rules.runtime(state, get_setting) then publish() end
    if not state.rules.manual then feedback("/revive: disabled by the host."); return end
    if living(peer) then feedback("/revive: you are already alive."); return end
    if not any_living() then feedback("/revive: a living connected teammate is required."); return end
    local p = rules.player(state, peer)
    if p.pending then feedback("/revive: recovery already pending; waiting for safe placement."); return end
    if type(death) ~= "number" or death <= p.resolved or death ~= p.death then
        feedback("/revive: death not synchronized; try again."); return
    end
    -- Stable host choice, independent of requester settings, statue and camera.
    local peers = {}
    for id in pairs(ctx.players) do if living(id) then peers[#peers + 1] = id end end
    table.sort(peers)
    local carrier = peers[1]
    local x, y = EntityGetTransform(ctx.players[carrier].entity)
    issue(peer, death, "manual", x, y, carrier)
    feedback("/revive: authorized; waiting for safe placement beside a teammate.")
end

rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.boss_defeated(x, y)
    if not ctx.is_host or not enabled() then return end
    if rules.boss_defeated(state) then
        if state.rules.boss_recover then automatic("boss", x, y) end
        publish()
    end
end

-- FFI uses the game's SDL library, so loss of keyboard focus fails closed.
-- ControlsComponent supplies bound keyboard AND controller movement actions.
local focus = function() return false end
do
    local ok, check = pcall(function()
        local ffi = require("ffi")
        ffi.cdef("void *SDL_GetKeyboardFocus(void);")
        local sdl = ffi.load(ffi.os == "Windows" and "SDL2" or "libSDL2-2.0.so.0")
        return function() return sdl.SDL_GetKeyboardFocus() ~= nil end
    end)
    if ok then focus = check end
end

local function control_valid()
    if not living(ctx.my_id) or ctx.is_paused or ctx.is_inventory_open or ctx.is_wand_pickup
        or ctx.is_texting or ctx.my_player.currently_polymorphed or GameIsInventoryOpen() or not focus() then return false end
    local c = EntityGetFirstComponent(ctx.my_player.entity, "ControlsComponent")
    if not c or not ComponentGetValue2(c, "enabled") then return false end
    for _, child in ipairs(EntityGetAllChildren(ctx.my_player.entity) or {}) do
        local effect = EntityGetFirstComponent(child, "GameEffectComponent")
        if effect and ComponentGetValue2(effect, "disable_movement") then return false end
    end
    for _, action in ipairs({ "Left", "Right", "Up", "Down", "Jump", "Fly", "Run",
        "ChangeItemL", "ChangeItemR", "HolsterItem", "DropItem", "Throw", "Inventory" }) do
        if ComponentGetValue2(c, "mButtonDown" .. action) then return false end
    end
    return true
end

ctx.cap.coop_revival = {
    enabled = enabled,
    legacy = function() return state ~= nil and not state.rules.enabled end,
    before_snapshot = return_ui.before_snapshot,
    release_input_guard = return_ui.before_snapshot,
    died = function(entity)
        return_ui.before_snapshot(entity)
        if not enabled() then return end
        local death = tonumber(GlobalsGetValue("ew_coop_death", "0")) + 1
        GlobalsSetValue("ew_coop_death", tostring(death))
        local x,y=EntityGetTransform(entity)
        write("ew_coop_death_location",{death=death,x=x,y=y,ng=mountains.ng(),mountain=BiomeMapGetName(x,y)=="$biome_holymountain"})
        send_presence()
        local c = EntityGetFirstComponentIncludingDisabled(entity, "CharacterDataComponent")
        if c then
            local box = {}
            for i, field in ipairs({ "min_x", "min_y", "max_x", "max_y" }) do
                box[i] = ComponentGetValue2(c, "collision_aabb_" .. field)
            end
            write("ew_coop_body_box", box)
        end
    end,
    dead_ready = send_presence,
    presentation = function()
        local p = state and held_target and state.players[held_target]
        return { channel = channel and { target = channel.target, death = channel.death,
            progress = channel.ticks / (state.rules.seconds * 60) },
            pending = p and p.pending and p.pending.reason == "statue" and p.pending.carrier == ctx.my_id,
            panel = grant ~= nil or (state and state.rules.mismatch) }
    end,
    chat_command = function(text)
        if not text:match("^%s*/revive%s*$") then return false end
        if enabled() then send_presence() end
        rpc.manual_recovery(tonumber(GlobalsGetValue("ew_coop_death", "0")))
        return true
    end,
}

-- Natural end-of-level portals use the independent liquid-powered entity, NOT
-- teleport.xml. Hook both normal variants; special destinations still fail the
-- callback filename filter and the loaded Holy Mountain arrival check.
for _, file in ipairs({ "teleport.xml", "teleport_liquid_powered.xml" }) do
    util.replace_text_in("data/entities/buildings/" .. file, "</Entity>",
        '<LuaComponent script_portal_teleport_used="mods/quant.ew/files/system/coop_revival/portal_used.lua" /></Entity>')
end
local boss_script = "data/entities/animals/boss_centipede/boss_centipede_update.lua"
local boss_hook = dofile_once("mods/quant.ew/files/system/coop_revival/boss_confirmed.lua")
ModTextFileSetContent(boss_script, boss_hook(ModTextFileGetContent(boss_script)))

util.add_cross_call("ew_coop_portal_used", function(entity, x, y)
    if enabled() and entity == ctx.my_player.entity and living(ctx.my_id) then
        portal_sequence = portal_sequence + 1
        local ng = tonumber(SessionNumbersGetValue("NEW_GAME_PLUS_COUNT")) or 0
        arrival = { x = x, y = y, deaths = capture_deaths(), sequence = portal_sequence, ng = ng }
        rpc.portal_notice(x, y, ng, portal_sequence, arrival.deaths)
    end
end)

util.add_cross_call("ew_coop_boss_defeated", function(entity)
    if not enabled() or not util.do_i_own(entity) then return end
    if EntityHasTag(entity, "ew_coop_kolmi_defeat_reported") then return end
    local damage = EntityGetFirstComponentIncludingDisabled(entity, "DamageModelComponent")
    if not damage or ComponentGetValue2(damage, "hp") > 0 then return end
    EntityAddTag(entity, "ew_coop_kolmi_defeat_reported")
    local x, y = EntityGetTransform(entity)
    rpc.boss_defeated(x, y)
end)

function module.on_should_send_updates()
    if ctx.is_host then publish() end
end

function module.on_world_update_host()
    initialize_host()
    if not enabled() or ctx.run_ended then return end
    if rules.runtime(state, get_setting) then publish() end
    update_returns()
    for peer, a in pairs(host_arrivals) do
        if not ctx.players[peer] or ctx.players[peer].dc then
            host_arrivals[peer] = nil
        elseif living(peer) then
            local x, y = EntityGetTransform(ctx.players[peer].entity)
            if math.abs(x - a.x) <= 128 and math.abs(y - a.y) <= 128 then
                host_arrivals[peer] = nil
                -- Share stage rows across parallel worlds; NG+ has a new map.
                local key = tostring(a.ng) .. ":" .. tostring(math.floor(a.y / 512))
                if rules.checkpoint(state, key) then
                    if state.rules.mountain then automatic("mountain", a.x, a.y, a.deaths) end
                    publish()
                end
            end
        end
    end
    if GameGetFrameNum() % 60 == 0 then
        publish()
        if not any_living() then return end
        for peer, p in pairs(state.players) do
            local player = ctx.players[peer]
            if player and not player.dc then
                if p.pending then
                    if p.pending.reason=="return"and p.pending.cancel then
                        rpc.cancel_return(peer,state.return_run,p.pending.id,p.pending.death,p.pending.cancel)
                    elseif peer == ctx.my_id then grant = p.pending else rpc.restore(peer, p.pending) end
                elseif p.auto and p.auto.death == p.death then
                    local a = p.auto
                    issue(peer, p.death, a.reason, a.x, a.y)
                end
            end
        end
    end
end

function module.on_world_update()
    if enabled() and GameGetFrameNum() % 30 == 0 then send_presence() end
    return_ui.input(focus())
end

local function update_channel()
    if not enabled() then return end
    local frame = GameGetFrameNum()
    local target, death
    if living(ctx.my_id) then
        local inv = EntityGetFirstComponentIncludingDisabled(ctx.my_player.entity, "Inventory2Component")
        local item = inv and ComponentGetValue2(inv, "mActiveItem")
        local p = item and player_fns.get_player_data_by_local_entity_id(item)
        if p and state.players[p.peer_id] and held_by(ctx.my_id, p.peer_id) then
            target, death = p.peer_id, state.players[p.peer_id].death
        end
    end
    local remaining = target and rules.remaining(state, target)
    local pending = target and state.players[target].pending
    held_target = target
    channel = rules.channel(channel, target, death, frame, GameGetRealWorldTimeSinceStarted(),
        target ~= nil and death > 0 and not pending and state.rules.statue and remaining > 0 and control_valid())
    if channel then
        if channel.ticks >= state.rules.seconds * 60 then
            rpc.statue_complete(target, death, channel.ticks)
            channel = nil
        end
    end
end

local function notice()
    if not enabled() then
        if state and state.rules.mismatch then
            return { title = "Revival unavailable", body = "Select Local Health / Alternate in the proxy, then start a new run." }
        end
        return nil
    end
    local r = state.rules
    local function allowance(peer)
        local n = rules.remaining(state, peer)
        return "Revives remaining: " .. (n == math.huge and "Unlimited" or tostring(n))
    end
    local target = held_target
    if target and state.players[target] and state.players[target].death > 0 and held_by(ctx.my_id, target) then
        local footer = allowance(target)
        if channel then
            return nil -- the torso ritual is the ordinary charging display
        elseif state.players[target].pending then
            return { title = "Waiting for clear space", body = "No revive used yet." }
        elseif not r.statue then
            return { title = "Statue revival disabled", body = "The host has disabled this method." }
        elseif rules.remaining(state, target) == 0 then
            return { title = "Statue revives exhausted", footer = footer,
                body = r.mountain and "A new Holy Mountain can still revive them." or "No statue revives left for this allowance." }
        end
        return nil
    end
    if GameHasFlagRun("ew_flag_notplayer_active") then
        if grant and grant.reason=="return"then return nil end
        if grant then
            return { title = placement_blocked and "No clear place to revive" or "Awaiting revival",
                body = placement_blocked and "Waiting for safe ground nearby. No revive used yet." or "Finding a safe place nearby.",
                footer = allowance(ctx.my_id) }
        end
        return nil -- compact spectator help owns ordinary dead-player presentation
    end
    -- No permanent banner during ordinary living gameplay.
end

local notice_key, notice_until
local function render_status()
    local hidden = ctx.run_ended or ctx.is_paused or ctx.is_inventory_open or ctx.is_wand_pickup
        or ctx.is_texting or GameIsInventoryOpen()
    local status = notice()
    local key = status and (status.title .. tostring(held_target or "") .. tostring(grant and grant.id or ""))
    if key ~= notice_key then notice_key, notice_until = key, GameGetFrameNum() + 180 end
    if status and GameGetFrameNum() > notice_until then status = nil end
    if status and ModSettingGet("quant.ew.coop_timing_bar") == false then status.progress = nil end
    hud.render(gui, status, hidden)
end

local function process_grant()
    if not grant then return end
    local t = grant
    if t.reason=="return"then
        if t.cancel or not return_valid(ctx.my_id,t)then cancel_owned_return(t,t.cancel or return_failure(ctx.my_id,t));return end
        if ctx.is_paused or ctx.is_inventory_open or ctx.is_texting or ctx.is_wand_pickup or GameIsInventoryOpen()then return end
        t.active_ticks=(t.active_ticks or 0)+1
        if t.active_ticks>=300 then cancel_owned_return(t,"No safe place yet. You can confirm again.");return end
    end
    if not any_living() or not GameHasFlagRun("ew_flag_notplayer_active")
        or tonumber(GlobalsGetValue("ew_coop_death", "0")) ~= t.death then
        if t.reason=="return"then cancel_owned_return(t,unavailable)
        else clear_grant();rpc.receipt(t.death, t.id, false)end
        return
    end
    if t.reason == "statue" and not held_by(t.carrier, ctx.my_id) then
        clear_grant()
        rpc.receipt(t.death, t.id, false)
        return
    end
    if t.reason == "manual" and not living(t.carrier) then
        clear_grant()
        rpc.receipt(t.death, t.id, false)
        GamePrint("/revive: teammate unavailable; request again beside a living teammate.")
        return
    end
    -- Use the carrier's BODY, never the statue's transform or mouse position.
    local x, y = t.x, t.y
    if t.carrier then x, y = EntityGetTransform(ctx.players[t.carrier].entity) end
    spectate.request_camera_position(x, y)
    if not t.started then t.started = GameGetFrameNum() end
    if GameGetFrameNum() % 6 ~= 0 then return end
    local sx, sy = placement.find(x, y, read("ew_coop_body_box"), t.reason == "statue")
    if sx then
        if ctx.cap.health.coop_restore(sx, sy, t.percent) then
            if t.reason=="return"then return_ui.suppress_restored(ctx.my_player.entity)end
            write("ew_coop_receipt", { death = t.death, id = t.id, return_run = t.return_run })
            clear_grant()
            rpc.receipt(t.death, t.id, true, t.return_run)
            send_presence()
        end
    else
        if t.reason == "manual" and not placement_blocked then
            GamePrint("/revive: no loaded clear space yet; recovery remains pending.")
        end
        placement_blocked = true
        if t.reason == "statue" and GameGetFrameNum() - t.started >= 300 then
            clear_grant()
            rpc.receipt(t.death, t.id, false)
        end
    end
end

-- Destination streaming borrows the existing spectator component for one
-- engine frame at a time; it never enters global free-camera mode.
function module.on_world_update_post()
    if ctx.run_ended then clear_grant(); return_ui.clear(); visuals.clear(); hud.render(gui, nil, true); return end
    -- Sample after engine item/control changes and EW's chat/menu handlers.
    update_channel()
    if not enabled() then clear_grant(); return_ui.clear(); visuals.clear(); render_status(); return end
    return_ui.confirm(focus(),function(death,id,run)rpc.mountain_return(death,id,run)end)
    if arrival then
        local x, y = EntityGetTransform(ctx.my_player.entity)
        if not living(ctx.my_id) then
            arrival = nil
        else
            if math.abs(x - arrival.x) < 96 and math.abs(y - arrival.y) < 96 then arrival.reached = true end
        end
        if arrival and arrival.reached
            and DoesWorldExistAt(arrival.x - 8, arrival.y - 16, arrival.x + 8, arrival.y + 8)
            and BiomeMapGetName(arrival.x, arrival.y) == "$biome_holymountain" then
            local a = arrival
            arrival = nil
            rpc.mountain_arrived(a.x, a.y, a.ng, a.deaths, a.sequence)
        end
    end
    local ok, err = pcall(process_grant)
    if not ok then
        local t = grant
        if t and t.reason=="return"then cancel_owned_return(t,unavailable)
        else clear_grant();if t then rpc.receipt(t.death, t.id, false)end end
        render_status()
        error(err)
    end
    visuals.update(ctx.cap.coop_revival.presentation())
    return_ui.render()
    render_status()
end

return module
