-- Ordinary native wand cards, independent of pinging or Cooperative Revival.
-- Read getters only; native row code consumes a bounded process-local text map.
local M={}
local backend=dofile_once('mods/quant.ew/files/system/player_ping/native_inventory_capture.lua')
local EZ=dofile_once('mods/quant.ew/files/lib/EZWand.lua')
local last_error,failed,measure_gui
local function finite(n)return type(n)=='number'and n==n and math.abs(n)<math.huge end
local function values()
    local ids,seen,out={}, {}, {}
    -- Prefer current owned wands even in unusually item-dense loaded terrain.
    local body=ctx.my_player and ctx.my_player.entity
    if body and EntityGetIsAlive(body)then
        for _,id in ipairs(GameGetAllInventoryItems(body)or{})do
            if not seen[id]then seen[id]=true;ids[#ids+1]=id end
        end
    end
    for _,id in ipairs(EntityGetWithTag('wand')or{})do
        if not seen[id]then seen[id]=true;ids[#ids+1]=id end
    end
    for _,id in ipairs(ids)do
        if EntityGetIsAlive(id)then
            local ability=EntityGetFirstComponentIncludingDisabled(id,'AbilityComponent')
            if ability and ComponentGetValue2(ability,'use_gun_script')then
                local speed=ComponentObjectGetValue2(ability,'gunaction_config','speed_multiplier')
                if finite(speed)then
                    out[#out+1]={id=id,text=EZ.FormatSpeedMultiplier(speed)}
                    if #out==512 then break end
                end
            end
        end
    end
    return out
end
local function refresh()
    if failed then return end
    if ctx.run_ended then backend.close_speed();return end
    local ok,result=pcall(function()
        local data=values()
        if not backend.install_speed()then return false end
        measure_gui=measure_gui or GuiCreate()
        local label_width=GuiGetTextDimensions(measure_gui,"Projectile speed")
        return backend.update_speeds(data,label_width)
    end)
    if not ok or result==false then
        local reason=ok and(backend.speed_status().error or'unsupported native wand renderer')or tostring(result)
        if reason~=last_error then print('Co-op+ native wand speed inactive: '..reason);last_error=reason end
        backend.close_speed();failed=true -- usable native cards remain the fallback
    else last_error=nil end
end
function M.on_world_update()refresh()end
function M.on_inventory_hover_suspend()
    -- Pickup comparison may pause the world: retain the native row and refresh
    -- both objects. A real pause menu/run end releases only this owned patch.
    if ctx.run_ended or(ctx.is_paused and not ctx.is_wand_pickup and not GameIsInventoryOpen())then
        backend.close_speed()
    else refresh()end
end
function M.on_local_player_spawn()failed=false;refresh()end
function M.on_world_initialized()failed=false;backend.close();backend.close_speed()end
function M.inspect_values()return values()end -- read-only focused verification
return M
