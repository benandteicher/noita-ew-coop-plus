local item = dofile_once("mods/quant.ew/files/system/shop_hover/item.lua")
local bitser = dofile_once("mods/quant.ew/files/lib/bitser.lua")
local base64 = dofile_once("mods/quant.ew/files/resource/base64.lua")
local module, rpc = {}, net.new_rpc_namespace()
local state, claims = nil, {}
local save_key = "ew_shop_hover_v1"
local function save()
    if ctx.is_host and state then GlobalsSetValue(save_key, base64.encode(bitser.dumps(state))) end
end
rpc.opts_reliable()
function rpc.snapshot(value)
    if ctx.rpc_peer_id == ctx.host_id then state = value end
end
local function publish()
    if state then rpc.snapshot(state) end
end
local function initialize()
    if not ctx.is_host or state then return end
    local data = GlobalsGetValue(save_key, "")
    state = data ~= "" and bitser.loads(base64.decode(data)) or { epoch = 0, enabled = false, released = {} }
    for key in pairs(claims) do state.released[key] = true end
    save()
end
rpc.opts_reliable()
rpc.opts_everywhere()
function rpc.claim(key)
    if type(key) ~= "string" then return end
    claims[key] = true
    if ctx.is_host and state and not state.released[key] then
        state.released[key] = true
        save(); publish()
    end
end
util.add_cross_call("ew_shop_hover_claimed", function(key) rpc.claim(key) end)
ctx.cap.shop_hover = {
    wait_for_rules = function()
        -- Called inside wang_hooks' existing unique-generation coroutine.
        -- Never generate according to a client's unsynchronized local option.
        while not state do wait(1) end
    end,
}
util.add_cross_call("ew_shop_hover_generated", function(e, x, y)
    if not state or not state.enabled or not EntityHasTag(e, "card_action") or item.marker(e) then return end
    local c = EntityGetFirstComponentIncludingDisabled(e, "ItemComponent")
    if not c or ComponentGetValue2(c, "has_been_picked_by_player") then return end
    local key = (SessionNumbersGetValue("NEW_GAME_PLUS_COUNT") or "0") .. ":" .. tostring(x) .. ":" .. tostring(y)
    if state.released[key] or claims[key] then return end
    EntityAddComponent2(e, "VariableStorageComponent", {
        name = "ew_shop_hover", value_string = key, value_int = state.epoch, value_bool = false,
    })
    EntityAddComponent2(e, "LuaComponent", { script_item_picked_up = "mods/quant.ew/files/system/shop_hover/pickup.lua" })
    EntityAddTag(e, item.tag)
    item.hold(e) -- before EW serializes the fresh item / before the first physics tick
end)
local file = "data/scripts/biomes/temple_altar.lua"
local source = ModTextFileGetContent(file)
local patched, count = source:gsub("generate_shop_item%(", "ew_shop_hover_generate(")
assert(count > 0, "Shop hover: unsupported temple spell generator")
ModTextFileSetContent(file, patched .. '\ndofile_once("mods/quant.ew/files/system/shop_hover/generated.lua")\n')

function module.on_local_player_spawn()
    initialize()
    if ctx.is_host then
        -- The host preference is runtime; disabling across Continue retires old epochs too.
        module.on_world_update()
        publish()
    end
end
function module.on_should_send_updates() if ctx.is_host then publish() end end
function module.on_world_update()
    if ctx.run_ended then return end
    if ctx.is_host and state then
        local enabled = ModSettingGet("quant.ew.shop_hover") == true
        if enabled ~= state.enabled then
            if not enabled then state.epoch = state.epoch + 1 end
            state.enabled = enabled
            save(); publish()
        end
    end
    for _, e in ipairs(EntityGetWithTag(item.tag)) do
        local m = item.marker(e)
        if m then
            local key = ComponentGetValue2(m, "value_string")
            -- The pickup callback is authoritative. Parenting is a fallback for
            -- restored inventory entities; a historical ItemComponent flag alone
            -- must not release an unclaimed, still-world item.
            local picked = EntityGetRootEntity(e) ~= e
            if picked then
                item.release(e)
                rpc.claim(key)
            elseif ComponentGetValue2(m, "value_bool") or claims[key]
                or (state and (not state.enabled or state.epoch ~= ComponentGetValue2(m, "value_int") or state.released[key])) then
                item.release(e)
            else
                -- After ewext's loading guard; do not borrow its ew_no_ground marker.
                item.hold(e)
            end
        end
    end
end
return module
