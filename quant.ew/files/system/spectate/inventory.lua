-- Own only the native GUI instance borrowed/created for this spectator view.
-- Keep the original entity ID: player-data tables can replace their entity.
local M,view={},nil
local hidden,protections={},{}
local function present(e,c)
    if not EntityGetIsAlive(e)then return false end
    for _,value in ipairs(EntityGetComponentIncludingDisabled(e,"InventoryGuiComponent")or{})do if value==c then return true end end
    return false
end
function M.release()
    M.protect(false)
    if view and present(view.entity,view.component)then
        if view.created then EntityRemoveComponent(view.entity,view.component)
        else
            ComponentSetValue2(view.component,"mActive",view.active)
            EntitySetComponentIsEnabled(view.entity,view.component,view.enabled)
        end
    end
    view=nil
    for _,v in ipairs(hidden)do
        if present(v.entity,v.component)then
            ComponentSetValue2(v.component,'mActive',v.active)
            EntitySetComponentIsEnabled(v.entity,v.component,v.enabled)
        end
    end
    hidden={}
end
function M.protect(enable)
    if not enable then
        for _,v in ipairs(protections)do
            if EntityGetIsAlive(v.item)and EntityGetFirstComponentIncludingDisabled(v.item,'ItemComponent')==v.component then
                ComponentSetValue2(v.component,'permanently_attached',v.original)
            end
        end
        protections={};return
    end
    if not view then return end
    for _,child in ipairs(EntityGetAllChildren(view.entity)or{})do
        if EntityGetName(child)=='inventory_quick'then
            for _,item in ipairs(EntityGetAllChildren(child)or{})do
                local c=EntityGetFirstComponentIncludingDisabled(item,'ItemComponent')
                local known=false;for _,v in ipairs(protections)do if v.component==c then known=true;break end end
                if c and not known then protections[#protections+1]={item=item,component=c,original=ComponentGetValue2(c,'permanently_attached')}end
                if c then ComponentSetValue2(c,'permanently_attached',true)end
            end
        end
    end
end
-- Deliberate dead-view suppression, not a correction to native layout selection.
-- Never create a GUI. Borrow only the exact selected/local component, capturing
-- its original entity ID and state; restore it when this view ends.
function M.hide(entities)
    if view then M.release()end
    local wanted={};for _,e in ipairs(entities)do wanted[e]=true end
    for i=#hidden,1,-1 do
        local v=hidden[i]
        if not wanted[v.entity]then
            if present(v.entity,v.component)then
                ComponentSetValue2(v.component,'mActive',v.active)
                EntitySetComponentIsEnabled(v.entity,v.component,v.enabled)
            end
            table.remove(hidden,i)
        end
    end
    for e in pairs(wanted)do
        local c=EntityGetFirstComponentIncludingDisabled(e,'InventoryGuiComponent')
        if c then
            local known=false;for _,v in ipairs(hidden)do if v.component==c then known=true;break end end
            if not known then hidden[#hidden+1]={entity=e,component=c,active=ComponentGetValue2(c,'mActive'),enabled=ComponentGetIsEnabled(c)}end
            ComponentSetValue2(c,'mActive',false);EntitySetComponentIsEnabled(e,c,false)
        end
    end
end
function M.acquire(entity)
    M.release()
    local c=EntityGetFirstComponentIncludingDisabled(entity,"InventoryGuiComponent")
    view={entity=entity,component=c,created=c==nil}
    if c then
        view.active,view.enabled=ComponentGetValue2(c,"mActive"),ComponentGetIsEnabled(c)
        EntitySetComponentIsEnabled(entity,c,true)
    else view.component=EntityAddComponent2(entity,"InventoryGuiComponent")end
    return view.component
end
return M
