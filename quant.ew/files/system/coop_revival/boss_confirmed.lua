-- Transform the confirmed-death site before the boss LuaComponent executes.
-- No runtime wrapper or appended-script ordering dependency. Keep the original
-- persistent flag, kill statistics, victory portal and ending behavior intact.
local call = 'AddFlagPersistent( "boss_centipede" )'
local marker = "-- EW cooperative revival: confirmed Kolmi death"
return function(source)
    if source:find(marker, 1, true) then return source end
    local first, last = source:find(call, 1, true)
    assert(first and not source:find(call, last + 1, true), "Cooperative revival: Kolmi death call site changed")
    local hook = call .. [[
            -- EW cooperative revival: confirmed Kolmi death
            if not EntityHasTag(GetUpdatedEntityID(), "ew_coop_kolmi_defeat_reported") then
                CrossCall("ew_coop_boss_defeated", GetUpdatedEntityID())
            end]]
    return source:sub(1, first - 1) .. hook .. source:sub(last + 1)
end
