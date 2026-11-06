-- Compact identity only. All presentation metadata is read locally; no action
-- bodies, constructors, entity writes, inventory traversal or native hooks.
local M={}
local function finite(n)return type(n)=="number"and n==n and math.abs(n)<math.huge end
local function plain(t)return type(t)=="table"and getmetatable(t)==nil end
local function call(fn,...)
    if type(fn)~="function"then return nil end
    local ok,a,b,c,d,e=pcall(fn,...);if ok then return a,b,c,d,e end
end
local function clean(s,limit)
    if type(s)~="string"then return nil end
    s=s:gsub("%c"," ");local out=""
    for c in s:gmatch("[%z\1-\127\194-\244][\128-\191]*")do
        if #out+#c>limit then break end;out=out..c
    end
    return out~=""and out or nil
end
local function translated(key,fallback,limit)
    local s=call(GameTextGetTranslatedOrNot,key)
    if s==key then s=nil end
    return clean(s or fallback,limit or 96)
end
local items={
    potion={icon="data/ui_gfx/items/potion.png",icon_w=16,icon_h=16,name="$item_potion",fallback="Potion"},
    heart={icon="data/items_gfx/heart_extrahp.xml",icon_animation="default",icon_w=20,icon_h=20,
        icon_offset_x=8,icon_offset_y=21,name="$item_heart",fallback="Extra max HP"},
    heart_better={icon="data/items_gfx/heart_extrahp.xml",icon_animation="default",icon_w=20,icon_h=20,
        icon_offset_x=8,icon_offset_y=21,name="$item_heart_better",fallback="Large extra max HP"},
    heart_fullhp={icon="data/items_gfx/heart.xml",icon_animation="default",icon_w=20,icon_h=20,
        icon_offset_x=9,icon_offset_y=20,name="$item_heart_fullhp",fallback="Full health regeneration"},
    spell_refresh={icon="data/items_gfx/spell_refresh.xml",icon_animation="default",icon_w=20,icon_h=20,
        icon_offset_x=9,icon_offset_y=20,
        name="$item_spell_refresh",fallback="Spell refresher"},
    chest={icon="data/buildings_gfx/chest_random.png",icon_w=18,icon_h=13,name="$item_chest_treasure",fallback="Treasure chest"},
    chest_super={icon="data/buildings_gfx/chest_random_super.png",icon_w=18,icon_h=13,name="$item_chest_treasure_super",fallback="Great treasure chest"},
}
local item_paths={}
for _,id in ipairs({"heart","heart_better","heart_fullhp","spell_refresh"})do item_paths["data/entities/items/pickup/"..id..".xml"]=id end
item_paths["data/entities/items/pickup/heart_fullhp_temple.xml"]="heart_fullhp"
item_paths["data/entities/items/pickup/chest_random.xml"]="chest"
item_paths["data/entities/items/pickup/chest_random_super.xml"]="chest_super"
-- Material contents/fullness are deliberately not inferred or transmitted.
for _,suffix in ipairs({"","_starting","_empty","_water","_alcohol","_beer","_milk","_porridge",
    "_secret","_random_material","_slime","_vomit"})do item_paths["data/entities/items/pickup/potion"..suffix..".xml"]="potion" end
local enemies={
    zombie={name="$animal_zombie",fallback="Hurtta"},
    shotgunner={name="$animal_shotgunner",fallback="Haulikkohiisi"},
    longleg={name="$animal_longleg",fallback="Hämis"},
    miner={name="$animal_miner",fallback="Tappurahiisi"},
}
local enemy_paths={}
for id,meta in pairs(enemies)do
    meta.icon="data/ui_gfx/animal_icons/"..id..".png"
    meta.icon_w,meta.icon_h=16,16
    enemy_paths["data/entities/animals/"..id..".xml"]=id
end
local spells
local function spell_registry()
    if spells then return spells end
    local s=call(ModTextFileGetContent,"data/scripts/gun/gun_actions.lua")
    if type(s)~="string"or #s>3000000 then return {}end
    s=s:gsub("\r\n","\n"):gsub("\r","\n"):gsub("%-%-[^\n]*","")
    local starts={}
    for pos,id in s:gmatch('()\n%s*id%s*=%s*"([A-Z0-9_]+)"')do
        if #starts>=2048 then return {}end
        starts[#starts+1]={pos,id}
    end
    spells={}
    for i,start in ipairs(starts)do
        local block=s:sub(start[1],starts[i+1]and starts[i+1][1]-1 or #s)
        -- Stop before the action function. Only literal metadata is consumed.
        block=block:match("^(.-)action%s*=%s*function")or""
        local icon=block:match('sprite%s*=%s*"([^"]+)"')
        local name=block:match('name%s*=%s*"([^"]+)"')
        local desc=block:match('description%s*=%s*"([^"]+)"')
        if #start[2]<=64 and icon and icon:match("^data/ui_gfx/gun_actions/[a-z0-9_]+%.png$")
            and name and name:match("^%$action_[a-z0-9_]+$")
            and call(ModDoesFileExist,icon)==true then
            spells[start[2]]={icon=icon,name=name,
                description=desc and desc:match("^%$actiondesc_[a-z0-9_]+$")and desc or nil}
        end
    end
    return spells
end
function M.validate(value)
    if not plain(value)then return nil end
    local count=0
    for k in pairs(value)do
        count=count+1
        if count>3 or(k~="v"and k~="category"and k~="id")then return nil end
    end
    if value.v~=1 or type(value.id)~="string"or #value.id<1 or #value.id>64 then return nil end
    local known=value.category=="item"and items[value.id]or value.category=="enemy"and enemies[value.id]
        or value.category=="spell"and spell_registry()[value.id]
    if not known then return nil end
    return{v=1,category=value.category,id=value.id}
end
function M.resolve(value)
    local ident=M.validate(value);if not ident then return nil end
    local registry=ident.category=="item"and items or ident.category=="enemy"and enemies or spell_registry()
    local meta=registry[ident.id]
    if call(ModDoesFileExist,meta.icon)~=true then return nil end
    local name=translated(meta.name,meta.fallback,96);if not name then return nil end
    return{category=ident.category,id=ident.id,icon=meta.icon,name=name,
        icon_animation=meta.icon_animation,icon_w=meta.icon_w,icon_h=meta.icon_h,
        icon_offset_x=meta.icon_offset_x,icon_offset_y=meta.icon_offset_y,
        description=meta.description and translated(meta.description,nil,240)or nil}
end
local function component(e,kind)return call(EntityGetFirstComponentIncludingDisabled,e,kind)end
local function enabled(c)return c and call(ComponentGetIsEnabled,c)==true end
local function tag(e,name)return call(EntityHasTag,e,name)==true end
local function teammate(e)
    for _,name in ipairs({"player_unit","polymorphed_player","ew_peer","ew_notplayer","heart_statue"})do
        if tag(e,name)then return true end
    end
    if type(ctx)=="table"then
        if ctx.my_player and ctx.my_player.entity==e then return true end
        local count=0
        for _,p in pairs(ctx.players or {})do
            count=count+1;if count>64 then return true end
            if type(p)=="table"and p.entity==e then return true end
        end
    end
    return false
end
local function visible(e)
    -- Multiple sprites include disabled inventory/background overlays. Accept an
    -- enabled nontransparent visual, rather than relying on the first sprite.
    for _,kind in ipairs({"SpriteComponent","PhysicsImageShapeComponent"})do
        local list=call(EntityGetComponentIncludingDisabled,e,kind)or{}
        if type(list)~="table"or #list>32 then return false end
        for _,c in ipairs(list)do
            if enabled(c)then
                local image=call(ComponentGetValue2,c,"image_file")
                local alpha=kind=="SpriteComponent"and call(ComponentGetValue2,c,"alpha")or 1
                local shown=kind~="SpriteComponent"or call(ComponentGetValue2,c,"visible")~=false
                if shown and type(image)=="string"and image~=""and(not finite(alpha)or alpha>0)then return true end
            end
        end
    end
    return false
end
local function good_view(v)
    return type(v)=="table"and finite(v.camera_x)and finite(v.camera_y)
        and finite(v.world_w)and v.world_w>0 and finite(v.world_h)and v.world_h>0
        and finite(v.origin_x)and finite(v.origin_y)
end
function M.capture(entity,view)
    if not good_view(view)or not finite(entity)or entity<1 or entity%1~=0
        or call(EntityGetIsAlive,entity)~=true or call(EntityGetRootEntity,entity)~=entity
        or teammate(entity)or not visible(entity)then return nil end
    local x,y,rotation,scale_x,scale_y=call(EntityGetTransform,entity)
    if not finite(x)or not finite(y)or math.abs(x-view.camera_x)>view.world_w/2
        or math.abs(y-view.camera_y)>view.world_h/2
        or call(DoesWorldExistAt,x-2,y-2,x+2,y+2)~=true then return nil end
    local item=component(entity,"ItemComponent")
    local action=component(entity,"ItemActionComponent")
    local path=call(EntityGetFilename,entity)
    -- Native bottled liquids have a collision ProjectileComponent, unlike other
    -- supported pickups. This exact-path potion exception is intentionally narrow.
    if component(entity,"ProjectileComponent")and item_paths[path]~="potion"then return nil end
    for _,name in ipairs({"projectile_player","projectile_enemy","projectile_item"})do
        if tag(entity,name)then return nil end
    end
    local category,id
    if item and enabled(item)and call(ComponentGetValue2,item,"is_pickable")==true
        and enabled(action)and tag(entity,"card_action")then
        category,id="spell",call(ComponentGetValue2,action,"action_id")
    else
        local item_id=item_paths[path]
        if item_id and item and enabled(item)and tag(entity,"item_pickup")
            and call(ComponentGetValue2,item,"is_pickable")==true then
            local name=call(ComponentGetValue2,item,"item_name")
            -- Great chests inherit the ordinary ItemComponent name.
            local expected=item_id=="chest_super"and items.chest.name or items[item_id].name
            if name~=expected then return nil end
            if item_id=="potion"and not component(entity,"PotionComponent")then return nil end
            category,id="item",item_id
        else
            local enemy_id=enemy_paths[path]
            if enemy_id and tag(entity,"enemy")and component(entity,"AnimalAIComponent")
                and component(entity,"DamageModelComponent")and call(EntityGetName,entity)==enemies[enemy_id].name then
                category,id="enemy",enemy_id
            end
        end
    end
    local ident=M.validate({v=1,category=category,id=id})
    local meta=ident and M.resolve(ident)
    if not meta then return nil end
    local sx,sy=x,y
    if category=="spell"then
        if(finite(scale_x)and scale_x~=1)or(finite(scale_y)and scale_y~=1)then return nil end
        local found=false
        -- Native cards anchor at their bottom (16px art, offsets 8/17),
        -- often on the floor. Test the visible icon, not its buried foot.
        for _,c in ipairs(call(EntityGetComponentIncludingDisabled,entity,"SpriteComponent")or{})do
            local alpha=call(ComponentGetValue2,c,"alpha")
            if enabled(c)and call(ComponentGetValue2,c,"visible")~=false and(not finite(alpha)or alpha>0)
                and call(ComponentGetValue2,c,"image_file")==meta.icon then
                if call(ComponentGetValue2,c,"has_special_scale")==true then return nil end
                local ox,oy=call(ComponentGetValue2,c,"offset_x"),call(ComponentGetValue2,c,"offset_y")
                if finite(ox)and finite(oy)and math.abs(ox)<=32 and math.abs(oy)<=32 and finite(rotation)then
                    local dx,dy=8-ox,8-oy
                    sx,sy=x+dx*math.cos(rotation)-dy*math.sin(rotation),y+dx*math.sin(rotation)+dy*math.cos(rotation)
                    found=true
                end
                break
            end
        end
        if not found then return nil end
    end
    if call(DoesWorldExistAt,sx-2,sy-2,sx+2,sy+2)~=true
        or call(RaytraceSurfaces,view.origin_x,view.origin_y,sx,sy)~=false then return nil end
    return x,y,ident
end
function M.target(x,y,view)
    if not finite(x)or not finite(y)or not good_view(view)then return nil end
    local ids=call(EntityGetInRadius,x,y,12)
    if type(ids)~="table"or #ids>64 then return nil end
    local best,best_d,second,wx,wy,ident,seen=nil,nil,nil,nil,nil,nil,{}
    for _,entity in ipairs(ids)do
        local ex,ey,data
        if finite(entity)and not seen[entity]then
            seen[entity]=true;ex,ey,data=M.capture(entity,view)
        end
        if data then
            local d=(ex-x)^2+(ey-y)^2
            if d<=144 then
                if not best_d or d<best_d then
                    second,best,best_d,wx,wy,ident=best_d,entity,d,ex,ey,data
                elseif not second or d<second then second=d end
            end
        end
    end
    -- A one-world-unit distance margin avoids arbitrary nearest-ID tie choices.
    if not best or(second and math.sqrt(second)-math.sqrt(best_d)<=1)then return nil end
    return best,wx,wy,ident
end
return M
