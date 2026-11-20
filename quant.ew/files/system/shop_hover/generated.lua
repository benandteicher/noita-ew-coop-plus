-- Only temple_altar.lua's calls are redirected here. Never replace the global
-- generator or alter its arguments, return value, price or pickup scripts.
local generate = generate_shop_item
function ew_shop_hover_generate(x, y, ...)
    local e = generate(x, y, ...)
    CrossCall("ew_shop_hover_generated", e, x, y)
    return e
end
