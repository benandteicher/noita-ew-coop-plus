-- Installed only on the supported native generators. Identity comes from EW's
-- forced tracking of the one canonical nugget, never a coordinate hash.
-- Native chest scripts can dofile another patched generator in the same Lua
-- environment. Install one wrapper there, not a nested wrapper per append.
if not ew_income_load_wrapped then
ew_income_load_wrapped=true
local native_load=EntityLoad
function EntityLoad(path,x,y,...)
    if path:match('^data/entities/items/pickup/goldnugget')or path:match('^data/entities/items/pickup/bloodmoney_')then
        local source=GetUpdatedEntityID()
        local action=CrossCall('ew_income_source',source)
        if action=='suppress'then
            -- Valid, inert return entity: native drop helpers may edit it before
            -- returning. Never load a second exploding/blood-money nugget.
            local e=EntityCreateNew('ew_retired_gold_generation')
            EntityAddTag(e,'ew_no_enemy_sync');EntityAddTag(e,'ew_income_discard')
            return e
        end
        local e=native_load(path,x,y,...)
        if action=='share'or action=='wait_share'or action=='wait_suppress'then CrossCall('ew_income_generated',e,action~='wait_suppress')end
        return e
    end
    return native_load(path,x,y,...)
end
end -- do not return: other native/EW script appends must still execute
