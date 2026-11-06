-- Local native artwork only. Same eight action-type backgrounds as EZWand.
-- No action evaluation, entity access, paths or values taken from ping packets.
local M={}
local backgrounds={PROJECTILE='projectile',STATIC_PROJECTILE='static_projectile',
    MODIFIER='modifier',DRAW_MANY='draw_many',MATERIAL='material',OTHER='other',
    UTILITY='utility',PASSIVE='passive'}
function M.measure(gui,info)
    local kind=info.values and info.values.type
    local bg='data/ui_gfx/inventory/item_bg_'..(backgrounds[kind]or'other')..'.png'
    local bw,bh=GuiGetImageDimensions(gui,bg)
    local iw,ih=GuiGetImageDimensions(gui,info.icon)
    return{background=bg,width=math.max(bw,iw),height=math.max(bh,ih),bw=bw,bh=bh,iw=iw,ih=ih}
end
function M.draw(gui,x,y,info,plan,z)
    z=z or-1042
    GuiZSetForNextWidget(gui,z)
    GuiImage(gui,3,x+math.floor((plan.width-plan.bw)/2),y+math.floor((plan.height-plan.bh)/2),plan.background,.8,1,1)
    GuiZSetForNextWidget(gui,z-1)
    GuiImage(gui,1,x+math.floor((plan.width-plan.iw)/2),y+math.floor((plan.height-plan.ih)/2),info.icon,1,1,1)
end
return M
