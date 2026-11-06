-- Read-only held-wand capture and current sender anchoring. Entity IDs stay local.
local Snapshot = dofile_once("mods/quant.ew/files/system/player_ping/wand_snapshot.lua")
local teammates = dofile_once("mods/quant.ew/files/system/player_arrows/teammates.lua")
local M = {}
local function entity_id(value)
    return type(value) == "number" and value >= 1 and value <= 2147483647 and value % 1 == 0
end
local function excluded(entity)
    return EntityHasTag(entity, "heart_statue") or EntityHasTag(entity, "ew_notplayer")
end
local function anchor(player)
    if type(player) ~= "table" or player.dc then return nil, nil, "gone" end
    if player.status and player.status.is_alive == false then return nil, nil, "dead" end
    local body = player.entity
    if entity_id(body) and EntityGetIsAlive(body) and excluded(body) then return nil, nil, "dead" end
    if not player.status or player.status.is_alive ~= true or not entity_id(body)
        or not EntityGetIsAlive(body) then return nil, nil, "unknown" end
    local root = EntityGetRootEntity(body)
    if not entity_id(root) or not EntityGetIsAlive(root) then return nil, nil, "unknown" end
    if excluded(root) then return nil, nil, "dead" end
    local x, y = teammates.position(player)
    if x == nil or y == nil then return nil, nil, "unknown" end
    return x, y, "alive"
end
function M.anchor(player)
    -- Streaming or a replaced body may invalidate a component/entity during reads.
    local ok, x, y, state = pcall(anchor, player)
    if ok then return x, y, state end
    return nil, nil, "unknown"
end
local function capture(player)
    local _, _, state = M.anchor(player)
    if state ~= "alive" then return end
    local body = player.entity
    local inventory = EntityGetFirstComponentIncludingDisabled(body, "Inventory2Component")
    if not inventory or inventory == 0 then return end
    local held = ComponentGetValue2(inventory, "mActiveItem")
    if not entity_id(held) or not EntityGetIsAlive(held) then return end
    -- Compare ownership roots, matching the existing rooted player position helper.
    local root = EntityGetRootEntity(body)
    if not entity_id(root) or EntityGetRootEntity(held) ~= root then return end
    local data = Snapshot.capture(held)
    if data then return data, body, held end
end
function M.capture(player)
    local ok, data, body, held = pcall(capture, player)
    if ok then return data, body, held end
end
-- Native inventory hover supplies an explicit local entity, never the held item.
-- The read-only native enumeration excludes nested spell/item children and loose
-- or foreign entities even if a stale tooltip survived an ownership change.
function M.capture_owned(player, item)
    local ok, data, body = pcall(function()
        local _, _, state = M.anchor(player)
        if state ~= "alive" or not entity_id(item) or not EntityGetIsAlive(item)
            or not EntityHasTag(item, "wand") then return end
        local entity = player.entity
        if not EntityGetFirstComponentIncludingDisabled(entity, "Inventory2Component")
            or EntityGetRootEntity(item) ~= EntityGetRootEntity(entity) then return end
        local items = GameGetAllInventoryItems(entity) or {}
        if #items > 256 then return end
        for _, candidate in ipairs(items) do
            if candidate == item then return Snapshot.capture(item), entity end
        end
    end)
    if ok and data then return data, body, item end
end
return M
