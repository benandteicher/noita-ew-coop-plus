-- Optional local presentation bridge only. No RPC namespace, peers, saves,
-- Globals, entities, inputs or game settings are modified here.
local status = {}
local last_frame
local last_payload
local last_error_frame

local ordinary_biomes = {
    ["$biome_coalmine"] = "mines",
    ["$biome_excavationsite"] = "coal_pits",
    ["$biome_snowcave"] = "snowy_depths",
    ["$biome_snowcastle"] = "hiisi_base",
    ["$biome_rainforest"] = "jungle",
    ["$biome_vault"] = "vault",
    ["$biome_crypt"] = "temple",
}

function status.snapshot()
    if ctx.is_paused or ctx.is_wand_pickup or ctx.is_inventory_open then return "menus", "-" end
    if ctx.run_ended then return "unknown", "-" end
    local player = ctx.my_player
    local entity = player and player.entity
    if not entity or not EntityGetIsAlive(entity) then return "unknown", "-" end
    local dead = EntityHasTag(entity, "ew_notplayer") or GameHasFlagRun("ew_flag_notplayer_active")
        or (player.status and player.status.is_alive == false)
    if dead then
        local spectate = ctx.discord_spectate
        if spectate and spectate.discord_is_spectating_teammate and spectate.discord_is_spectating_teammate() then
            return "spectating", "-"
        end
        return "awaiting", "-"
    end
    local x, y = EntityGetTransform(entity)
    if x == nil or y == nil then return "unknown", "-" end
    local biome = BiomeMapGetName(x, y)
    if biome == "$biome_holymountain" then return "mountain", "-" end
    return "exploring", ordinary_biomes[biome] or "-"
end

function status.update(force)
    -- Presentation failure must never interrupt a gameplay hook. Failed reads or
    -- a closed local socket are retried at most once per 60 active game frames.
    local frame = GameGetFrameNum()
    if last_error_frame and frame >= last_error_frame and frame - last_error_frame < 60 then return end
    local ok = pcall(function()
        local context, area = status.snapshot()
        local payload = "v1 " .. context .. " " .. area
        if not force and payload == last_payload and last_frame and frame >= last_frame and frame - last_frame < 60 then return end
        -- Even rapid context changes are bounded to one local sample per frame;
        -- the proxy independently coalesces and throttles Discord publication.
        if last_frame == frame and not force then return end
        net.proxy_send("coopplus_discord_status", payload)
        last_payload, last_frame = payload, frame
    end)
    if not ok then last_error_frame = frame end
end

return status
