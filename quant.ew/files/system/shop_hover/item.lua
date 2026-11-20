-- Entity-local state survives EW serialization and Noita saves. Only explicitly
-- marked, newly generated temple spell cards enter this lifecycle.
local M = { tag = "ew_shop_hover", resume = "ew_shop_hover_resume" }
function M.marker(e)
    for _, c in ipairs(EntityGetComponentIncludingDisabled(e, "VariableStorageComponent") or {}) do
        if ComponentGetValue2(c, "name") == "ew_shop_hover" then return c end
    end
end
local function no_ground(e)
    for _, c in ipairs(EntityGetComponentIncludingDisabled(e, "VariableStorageComponent") or {}) do
        if ComponentGetValue2(c, "name") == "ew_no_ground" then return true end
    end
    return false
end
function M.hold(e)
    for _, kind in ipairs({ "SimplePhysicsComponent", "VelocityComponent" }) do
        for _, c in ipairs(EntityGetComponentIncludingDisabled(e, kind) or {}) do
            if ComponentGetIsEnabled(c) then
                ComponentAddTag(c, M.resume) -- remember only components WE disabled
                if kind == "VelocityComponent" then ComponentSetValue2(c, "mVelocity", 0, 0) end
                EntitySetComponentIsEnabled(e, c, false)
            end
        end
    end
end
function M.release(e)
    local marker = M.marker(e)
    if not marker then return end
    ComponentSetValue2(marker, "value_bool", true) -- terminal, including after dropping
    EntityRemoveTag(e, M.tag)
    local world = EntityGetRootEntity(e) == e
    for _, kind in ipairs({ "SimplePhysicsComponent", "VelocityComponent" }) do
        for _, c in ipairs(EntityGetComponentIncludingDisabled(e, kind) or {}) do
            if ComponentHasTag(c, M.resume) then
                ComponentRemoveTag(c, M.resume)
                -- Inventory's normal enabled_in_world lifecycle enables on drop.
                -- EW's independent loading guard owns SimplePhysics while marked.
                if world and not (kind == "SimplePhysicsComponent" and no_ground(e)) then
                    EntitySetComponentIsEnabled(e, c, true)
                end
            end
        end
    end
end
return M
