-- Pure run bookkeeping. The host owns this table; Globals persist it with the save.
local M = {}

local function integer(value, default, low, high)
    value = tonumber(value)
    if not value or value ~= value or value == math.huge or value == -math.huge then value = default end
    return math.max(low, math.min(high, math.floor(value)))
end

function M.settings(get)
    local mode = get("allowance")
    if mode ~= "run" and mode ~= "stage" then mode = "unlimited" end
    return {
        enabled = get("enabled") == true,
        statue = get("statue") ~= false,
        seconds = integer(get("seconds"), 5, 1, 120),
        allowance = mode,
        count = integer(get("count"), 3, 1, 100),
        mountain = get("mountain") ~= false,
        boss_unlimited = get("boss_unlimited") ~= false,
        boss_recover = get("boss_recover") ~= false,
        statue_percent = integer(get("statue_percent"), 30, 1, 100),
        mountain_percent = integer(get("mountain_percent"), 30, 1, 100),
        manual = get("manual") == true,
        offer_return = get("offer_return") == true,
        return_limit = get("return_limit") == "unlimited" and 0 or integer(get("return_limit"), 1, 1, 100),
    }
end

-- Additive Continue migration: never replace the saved run or its locked rules.
function M.migrate(state)
    local r = state.rules
    if r.statue_percent == nil then r.statue_percent = 30 end
    if r.mountain_percent == nil then r.mountain_percent = 30 end
    if r.manual == nil then r.manual = false end
    if r.offer_return == nil then r.offer_return = false end
    if r.return_limit == nil then r.return_limit = 1 end
    state.return_serial = state.return_serial or 0
    for _, p in pairs(state.players) do
        p.returns = p.returns or {}
        if p.pending and p.pending.percent == nil then p.pending.percent = 30 end
    end
end

function M.runtime(state, get)
    local next_rules, changed = M.settings(get), false
    for _, key in ipairs({ "statue_percent", "mountain_percent", "manual", "offer_return", "return_limit" }) do
        if state.rules[key] ~= next_rules[key] then
            state.rules[key], changed = next_rules[key], true
        end
    end
    return changed
end

function M.new(rules)
    return { rules = rules, players = {}, checkpoints = {}, stage = 0, boss = false, serial = 0 }
end

function M.player(state, peer)
    if not state.players[peer] then
        state.players[peer] = { run = 0, stage = 0, resolved = 0, death = 0, returns = {} }
    end
    return state.players[peer]
end

function M.remaining(state, peer)
    local r, p = state.rules, M.player(state, peer)
    if r.allowance == "unlimited" or (state.boss and r.boss_unlimited) then return math.huge end
    return math.max(0, r.count - p[r.allowance])
end

function M.checkpoint(state, key)
    if state.checkpoints[key] then return false end
    state.checkpoints[key] = true
    state.stage = state.stage + 1
    for _, p in pairs(state.players) do p.stage = 0 end
    return true
end

function M.boss_defeated(state)
    if state.boss then return false end
    state.boss = true
    return true
end

function M.return_remaining(state,peer,stage)
    local p=M.player(state,peer);p.returns=p.returns or{}
    if state.rules.return_limit==0 then return math.huge end
    return math.max(0,(state.rules.return_limit or 1)-(p.returns[stage]or 0))
end

function M.reserve(state, peer, death, reason, x, y, carrier, offer)
    local p = M.player(state, peer)
    if death <= p.resolved or death ~= p.death or p.pending then return nil end
    if reason == "statue" and (not state.rules.statue or M.remaining(state, peer) == 0) then return nil end
    if reason == "return" and (not offer or M.return_remaining(state,peer,offer.room.stage)==0) then return nil end
    state.serial = state.serial + 1
    -- Freeze health when authorized, including while placement is waiting.
    p.pending = { death = death, id = state.serial, reason = reason, x = x, y = y, carrier = carrier,
        percent = reason == "statue" and (state.rules.statue_percent or 30) or (state.rules.mountain_percent or 30) }
    if reason=="return" then
        p.pending.return_run=state.return_run
        p.pending.return_room,p.pending.return_stage,p.pending.offer=offer.room,offer.room.stage,offer.id
        p.pending.return_location=p.location
    end
    return p.pending
end

function M.finish(state, peer, death, id, success)
    local p = M.player(state, peer)
    local t = p.pending
    if not t or t.death ~= death or t.id ~= id then return false end
    if success then
        if death <= p.resolved then return false end
        if t.reason == "statue" then
            p.run, p.stage = p.run + 1, p.stage + 1
        end
        if t.reason=="return" then
            p.returns=p.returns or{};p.returns[t.return_stage]=(p.returns[t.return_stage]or 0)+1
        end
        p.resolved, p.death, p.auto = death, 0, nil
    end
    p.pending = nil
    return true
end

-- Neither body displacement nor aiming is an input to the channel clock.
function M.channel(previous, target, death, frame, time, valid)
    if not valid then return nil end
    if not previous or previous.target ~= target or previous.death ~= death
        or frame ~= previous.frame + 1 or time - previous.time > 0.25 or time < previous.time then
        return { target = target, death = death, frame = frame, time = time, ticks = 0 }
    end
    previous.frame, previous.time, previous.ticks = frame, time, previous.ticks + 1
    return previous
end

return M
