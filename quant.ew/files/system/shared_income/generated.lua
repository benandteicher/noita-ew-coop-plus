-- Installed only on the supported native generators. Identity comes from EW's
-- forced tracking of the one canonical nugget, never a coordinate hash.
-- Native chest scripts can dofile another patched generator in the same Lua
-- environment. Install one wrapper there, not a nested wrapper per append.
if not ew_income_load_wrapped then
ew_income_load_wrapped=true
local native_load=EntityLoad
local native_seed=SetRandomSeed
local function apply_money_seed(seeds)
    if type(seeds)~='string'then return false end
    local x,y=seeds:match('^(%d+):(%d+)$')
    if not x then return false end
    native_seed(tonumber(x),tonumber(y));return true
end
-- Only explicitly annotated v2 chest-money descendants get deterministic RNG.
-- Every ordinary native source retains its exact original seed arguments.
function SetRandomSeed(x,y,...)
    if not apply_money_seed(CrossCall('ew_income_money_seed',GetUpdatedEntityID()))then
        return native_seed(x,y,...)
    end
end
function ew_income_seed_attempt(source,index)
    apply_money_seed(CrossCall('ew_income_money_attempt',source,index))
end
local money_sources={
 ['data/entities/items/pickup/physics_gold_orb.xml']='orb',
 ['data/entities/items/pickup/physics_gold_orb_greed.xml']='orb',
 ['data/entities/projectiles/rain_gold.xml']='rain',
}
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
        local identity=CrossCall('ew_income_gold_identity',source)
        local e=native_load(path,x,y,...)
        if action=='share'or action=='wait_share'or action=='wait_suppress'then CrossCall('ew_income_generated',e,action~='wait_suppress',identity)end
        return e
    end
    local e=native_load(path,x,y,...)
    if money_sources[path]then CrossCall('ew_income_money_source',GetUpdatedEntityID(),e,money_sources[path])end
    return e
end
end -- do not return: other native/EW script appends must still execute
