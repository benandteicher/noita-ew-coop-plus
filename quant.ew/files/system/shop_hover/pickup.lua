local item = dofile_once("mods/quant.ew/files/system/shop_hover/item.lua")
function item_pickup(e, who, name)
    local marker = item.marker(e)
    if not marker then return end
    local key = ComponentGetValue2(marker, "value_string")
    item.release(e) -- genuine engine pickup event; leave cost/purchasing untouched
    CrossCall("ew_shop_hover_claimed", key)
end
