-- Cosmetic data only. Do not construct/deserialise an EZWand to read a wand.
local Snapshot = {}
local fallback = "data/items_gfx/handgun.png"
local function plain(t)
    return type(t) == "table" and getmetatable(t) == nil
end
local function number(n, lo, hi)
    return type(n) == "number" and n == n and n >= lo and n <= hi
end
local function integer(n, lo, hi)
    return number(n, lo, hi) and n % 1 == 0
end
local ranges = {
    spellsPerCast = { 0, 64 }, castDelay = { -360000, 360000 }, rechargeTime = { -360000, 360000 },
    manaMax = { 0, 100000000 }, mana = { 0, 100000000 }, manaChargeSpeed = { -1000000, 100000000 },
    capacity = { 0, 64 }, spread = { -360000, 360000 }, speedMultiplier = { -1000000, 1000000 },
}
local fields = { v=true, props=true, spells=true, always_cast_spells=true, spell_uses=true,
    always_cast_uses=true, sprite_image_file=true, offset_x=true, offset_y=true, tip_x=true,
    tip_y=true, name=true, show_name_in_ui=true }
local function known_keys(t, allowed)
    if not plain(t) then return false end
    local count = 0
    for k in pairs(t) do
        count = count + 1
        if count > 32 or not allowed[k] then return false end
    end
    return true
end
local function clean_name(s)
    -- Keep complete UTF-8 sequences; strip controls, including embedded line breaks.
    local out, bytes, i = {}, 0, 1
    while i <= #s and bytes < 96 do
        local b, n = s:byte(i), 1
        if b >= 194 and b <= 223 then n = 2
        elseif b >= 224 and b <= 239 then n = 3
        elseif b >= 240 and b <= 244 then n = 4
        elseif b >= 128 then n = 0 end
        local valid = n > 0 and i + n - 1 <= #s
        for j = 1, n - 1 do
            local c = s:byte(i + j)
            if not c or c < 128 or c > 191 then valid = false end
        end
        local c = s:byte(i + 1)
        if n == 3 and ((b == 224 and c and c < 160) or (b == 237 and c and c > 159)) then valid = false end
        if n == 4 and ((b == 240 and c and c < 144) or (b == 244 and c and c > 143)) then valid = false end
        if valid and b >= 32 and b ~= 127 and bytes + n <= 96 then
            out[#out + 1], bytes = s:sub(i, i + n - 1), bytes + n
        end
        i = i + (valid and n or 1)
    end
    return table.concat(out)
end
local function safe_art(path)
    if path == "data/items_gfx/handgun.png" or path == "data/items_gfx/handgun.xml" then return fallback end
    if path == "data/items_gfx/bomb_wand.png" or path == "data/items_gfx/bomb_wand.xml" then return "data/items_gfx/bomb_wand.png" end
    -- Only native generated wand PNGs; XML and arbitrary mod/remote paths never reach GUI.
    local id = path:match("^data/items_gfx/wands/wand_(%d%d%d%d)%.png$")
    if id and tonumber(id) <= 1000 then return path end
    return fallback
end
local function array(t, limit, ids)
    if not plain(t) then return nil end
    local count, high = 0, 0
    for k, v in pairs(t) do
        if not integer(k, 1, limit) then return nil end
        count, high = count + 1, math.max(high, k)
        if ids then
            if type(v) ~= "string" or #v > 64 or (v ~= "" and not v:match("^[%w_]+$")) then return nil end
        elseif not integer(v, -1, 1000000) then return nil end
    end
    if high ~= count then return nil end
    local out = {}
    for i = 1, count do out[i] = t[i] end
    return out
end
function Snapshot.validate(data)
    if not known_keys(data, fields) or data.v ~= 1 or not plain(data.props) then return nil end
    local props, count = {}, 0
    for k, v in pairs(data.props) do
        count = count + 1
        if count > 10 then return nil end
        if k == "shuffle" then
            if type(v) ~= "boolean" then return nil end
        elseif not ranges[k] or not number(v, ranges[k][1], ranges[k][2]) then return nil end
        props[k] = v
    end
    if type(props.shuffle) ~= "boolean" then return nil end
    for k in pairs(ranges) do if props[k] == nil then return nil end end
    if not integer(props.capacity, 0, 64) or not integer(props.spellsPerCast, 0, 64) then return nil end
    local spells, always = array(data.spells, 64, true), array(data.always_cast_spells, 16, true)
    if not spells or not always or #spells ~= props.capacity then return nil end
    for _, id in ipairs(always) do if id == "" then return nil end end
    local uses = data.spell_uses == nil and {} or array(data.spell_uses, 64, false)
    local ac_uses = data.always_cast_uses == nil and {} or array(data.always_cast_uses, 16, false)
    if not uses or not ac_uses or (#uses ~= 0 and #uses ~= #spells) or (#ac_uses ~= 0 and #ac_uses ~= #always) then return nil end
    if type(data.sprite_image_file) ~= "string" or #data.sprite_image_file > 256 then return nil end
    if type(data.name) ~= "string" or #data.name > 384 or type(data.show_name_in_ui) ~= "boolean" then return nil end
    local out = { v=1, props=props, spells=spells, always_cast_spells=always, spell_uses=uses,
        always_cast_uses=ac_uses, sprite_image_file=safe_art(data.sprite_image_file),
        name=clean_name(data.name), show_name_in_ui=data.show_name_in_ui }
    for _, k in ipairs({ "offset_x", "offset_y", "tip_x", "tip_y" }) do
        if not number(data[k], -4096, 4096) then return nil end
        out[k] = data[k]
    end
    return out
end
local function capture(entity)
    if not integer(entity, 1, 2147483647) or not EntityGetIsAlive(entity) then return nil end
    local ability = EntityGetFirstComponentIncludingDisabled(entity, "AbilityComponent")
    if not ability or not ComponentGetValue2(ability, "use_gun_script") then return nil end
    local function get(key) return ComponentGetValue2(ability, key) end
    local function gun(key) return ComponentObjectGetValue2(ability, "gun_config", key) end
    local function action(key) return ComponentObjectGetValue2(ability, "gunaction_config", key) end
    local props = { shuffle=gun("shuffle_deck_when_empty"), spellsPerCast=gun("actions_per_round"),
        castDelay=action("fire_rate_wait"), rechargeTime=gun("reload_time"), manaMax=get("mana_max"),
        mana=get("mana"), manaChargeSpeed=get("mana_charge_speed"), capacity=gun("deck_capacity"),
        spread=action("spread_degrees"), speedMultiplier=action("speed_multiplier") }
    -- Native deck_capacity includes permanently attached actions. Our capacity,
    -- like EZWand.capacity and the inventory UI, counts removable slots only.
    local native_capacity = props.capacity
    if not integer(native_capacity, 0, 80) then return nil end
    local spells, uses, always, ac_uses, pending = {}, {}, {}, {}, {}
    local children = EntityGetAllChildren(entity) or {}
    if #children > 256 then return nil end
    for _, child in ipairs(children) do
        local ac = EntityGetFirstComponentIncludingDisabled(child, "ItemActionComponent")
        if ac then
            local item = EntityGetFirstComponentIncludingDisabled(child, "ItemComponent")
            local id = ComponentGetValue2(ac, "action_id")
            local remaining = item and ComponentGetValue2(item, "uses_remaining") or -1
            local slot = item and ComponentGetValue2(item, "inventory_slot") or -1
            if item and ComponentGetValue2(item, "permanently_attached") then
                always[#always+1], ac_uses[#ac_uses+1] = id, remaining
            else pending[#pending+1] = { id=id, uses=remaining, slot=slot } end
        end
    end
    props.capacity = native_capacity - #always
    if #always > 16 or not integer(props.capacity, 0, 64) then return nil end
    for i=1,props.capacity do spells[i], uses[i] = "", -1 end
    -- Reserve explicit nonzero slots first. Fresh uninitialised cards all report zero;
    -- fill the remaining positions in child order without mutating their components.
    for _, card in ipairs(pending) do
        if integer(card.slot, 1, props.capacity-1) and spells[card.slot+1] == "" then
            spells[card.slot+1], uses[card.slot+1], card.assigned = card.id, card.uses, true
        end
    end
    local next_slot = 1
    for _, card in ipairs(pending) do
        if not card.assigned then
            while next_slot <= props.capacity and spells[next_slot] ~= "" do next_slot = next_slot+1 end
            if next_slot > props.capacity then return nil end
            spells[next_slot], uses[next_slot] = card.id, card.uses
        end
    end
    local sprite = EntityGetFirstComponentIncludingDisabled(entity, "SpriteComponent", "item")
    local hotspot = EntityGetFirstComponentIncludingDisabled(entity, "HotspotComponent", "shoot_pos")
    local tx, ty = 0, 0
    if hotspot then tx, ty = ComponentGetValue2(hotspot, "offset") end
    local art = get("sprite_file")
    if (not art or art == "") and sprite then art = ComponentGetValue2(sprite, "image_file") end
    local item = EntityGetFirstComponentIncludingDisabled(entity, "ItemComponent")
    local name = item and ComponentGetValue2(item,"item_name") or ""
    return Snapshot.validate({ v=1, props=props, spells=spells, spell_uses=uses, always_cast_spells=always,
        always_cast_uses=ac_uses, sprite_image_file=safe_art(type(art)=="string" and art or fallback),
        offset_x=sprite and ComponentGetValue2(sprite,"offset_x") or 0,
        offset_y=sprite and ComponentGetValue2(sprite,"offset_y") or 0, tip_x=tx, tip_y=ty,
        name=clean_name(type(name)=="string" and name or ""), show_name_in_ui=item and ComponentGetValue2(item,"always_use_item_name_in_ui") or false })
end
function Snapshot.capture(entity)
    -- An entity may disappear between selection and component reads.
    local ok, data = pcall(capture, entity)
    if ok then return data end
end
return Snapshot
