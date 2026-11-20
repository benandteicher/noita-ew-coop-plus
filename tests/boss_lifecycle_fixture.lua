-- Portable fallback for the vanilla ONE_PER_COMPONENT_INSTANCE coroutine.
-- The local-engine probe also runs its real check_death body, extracted from
-- data.wak at test time; proprietary game source is not distributed here.
local is_dead = false
function check_death()
    if is_dead then return end
    local entity = GetUpdatedEntityID()
    local damage = EntityGetFirstComponent(entity, "DamageModelComponent")
    if damage and ComponentGetValue2(damage, "hp") <= 0 then
        EntityLoad("data/entities/buildings/teleport_ending_victory_delay.xml", 100, 150)
        ComponentSetValue2(damage, "kill_now", true)
        GlobalsSetValue("FINAL_BOSS_ACTIVE", "0")
        GlobalsSetValue("GLOBAL_BOSS_KILL_COUNT", tostring(tonumber(GlobalsGetValue("GLOBAL_BOSS_KILL_COUNT", "0")) + 1))
        AddFlagPersistent( "boss_centipede" )
        StatsLogPlayerKill(entity)
        is_dead = true
    end
end
-- The original coroutine may reach its death check before an append executes.
if boss_first_execution then check_death() end
