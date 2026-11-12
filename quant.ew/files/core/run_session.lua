-- Startup agreement only. Identity comes from the ACTUAL loaded Noita world,
-- never a seed, a socket connection, or the proxy's expected-session value.
local session = { sent = false, accepted = false, failed = false }
local run_key, rules_key = "coopplus_run_id_v1", "coopplus_rules_id_v1"

local function hex(text)
    return (text:gsub(".", function(c) return string.format("%02x", string.byte(c)) end))
end

local function valid_id(text)
    return type(text) == "string" and #text == 16 and text:match("^[0-9a-f]+$") ~= nil
end

function session.enabled()
    return ctx.proxy_opt.coopplus_run_id ~= nil
end

function session.ready()
    return not session.enabled() or session.accepted
end

local function fail(message)
    if session.failed then return end
    session.failed = true
    print("Co-op+ world/rules agreement stopped: " .. message)
    GamePrintImportant("Co-op+ world/rules mismatch", message .. " Close Noita normally; do not reset the saved checkpoint.")
end

net.net_handling.proxy.coopplus_world_ack = function(_, value)
    if not session.enabled() or not session.sent then return end
    if tostring(value) == "1" and not session.failed then
        session.accepted = true
        print("Co-op+ actual world/rules receipt accepted")
    else
        fail("The proxy refused the actual loaded world or received host rules.")
    end
end

local function actual_mode()
    local opt = ctx.proxy_opt
    if opt.game_mode == "shared_health" then return "Shared" end
    if opt.game_mode ~= "local_health" then return "unknown" end
    if opt.pvp then return "PvP" end
    if opt.perma_death then return "LocalPermadeath" end
    if opt.no_notplayer then return "LocalAlternate" end
    return "LocalNormal"
end

function session.boundary(boundary)
    if not session.enabled() then return true end
    if session.sent or session.failed then return session.ready() end
    local world = GameGetWorldStateEntity()
    local component = world and EntityGetFirstComponentIncludingDisabled(world, "WorldStateComponent")
    local actual = component and ComponentGetValue2(component, "session_stat_file")
    -- The engine can allocate the field between world-init and player-spawn.
    -- Wait for it; an absent field is NEVER replaced with an expected value.
    if type(actual) ~= "string" or actual == "" then return false end
    local opt = ctx.proxy_opt
    local run, rules = tostring(opt.coopplus_run_id), tostring(opt.coopplus_rules_id)
    local expected = tostring(opt.coopplus_expected_session)
    local same = expected ~= "none" and hex(actual) == expected
    local intent = tostring(opt.coopplus_run_intent)
    local saved_run, saved_rules = GlobalsGetValue(run_key, ""), GlobalsGetValue(rules_key, "")
    local kind, receipt_run, receipt_rules = "mismatch", "none", "none"
    if valid_id(run) and valid_id(rules) then
        if intent == "legacy" and saved_run == "" and saved_rules == "" and (same or not ctx.is_host and expected == "none") then
            kind = "legacy" -- Unverified legacy rules remain explicitly unverified.
        elseif same and (intent == "continue" or intent == "join") and saved_run == run and saved_rules == rules then
            kind, receipt_run, receipt_rules = "continue", saved_run, saved_rules
        elseif not same and (intent == "new" or intent == "join") and saved_run == "" and saved_rules == "" then
            kind, receipt_run, receipt_rules = "new", run, rules
            -- Only an actual new world receives NEW shared IDs. Continue and
            -- legacy checkpoints never acquire invented verification markers.
            GlobalsSetValue(run_key, run)
            GlobalsSetValue(rules_key, rules)
        end
    end
    session.sent = true
    local mode = actual_mode()
    local message = table.concat({ "1", hex(actual), kind, receipt_run, receipt_rules,
        mode, opt.no_notplayer and "1" or "0", boundary }, " ")
    print("Co-op+ actual world/rules receipt: " .. message)
    net.proxy_send("coopplus_world_receipt", message)
    if kind == "mismatch" or mode ~= opt.coopplus_expected_mode then
        fail("The Noita New Game/Continue selection or received health mode differs from the proxy intent.")
    end
    return false
end

function session.pump(boundary)
    if not session.enabled() then return true end
    if session.accepted and not session.failed then return true end
    if not session.sent and not session.failed then session.boundary(boundary) end
    -- ACKs must be read before ordinary player initialization enables net.update.
    net.update(function() return session.accepted or session.failed end)
    return session.accepted and not session.failed
end

return session
