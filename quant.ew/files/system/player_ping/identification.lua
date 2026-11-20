-- Compact identities only. Metadata is a generated, bounded local vanilla catalogue.
-- No constructors, progression flags, inventory traversal or entity writes.
local M={}
local catalogue=dofile_once('mods/quant.ew/files/system/player_ping/vanilla_catalogue.lua')
local items,enemies=catalogue.items,catalogue.enemies
local function finite(n)return type(n)=='number'and n==n and math.abs(n)<math.huge end
local function call(fn,...)
    if type(fn)~='function'then return nil end
    local ok,a,b,c,d,e=pcall(fn,...);if ok then return a,b,c,d,e end
end
local function clean(s,limit)
    if type(s)~='string'then return nil end
    s=s:gsub('%c',' ');local out=''
    for c in s:gmatch('[%z\1-\127\194-\244][\128-\191]*')do
        if #out+#c>limit then break end;out=out..c
    end
    return out~=''and out or nil
end
local function translated(key,fallback,limit)
    local s=call(GameTextGetTranslatedOrNot,key)
    if s==key then s=nil end
    -- Native missing keys can return an empty string rather than the key.
    -- Clean the result first so empty/invalid text uses the local fallback.
    return clean(s,limit or 96)or clean(fallback,limit or 96)
end
local spells
local function spell_registry()
    if spells then return spells end
    local s=call(ModTextFileGetContent,'data/scripts/gun/gun_actions.lua')
    if type(s)~='string'or #s>3000000 then return {}end
    s=s:gsub('\r\n','\n'):gsub('\r','\n'):gsub('%-%-[^\n]*','')
    local starts={}
    for pos,id in s:gmatch('()\n%s*id%s*=%s*"([A-Z0-9_]+)"')do
        if #starts>=2048 then return {}end
        starts[#starts+1]={pos,id}
    end
    spells={}
    for i,start in ipairs(starts)do
        local block=s:sub(start[1],starts[i+1]and starts[i+1][1]-1 or #s)
        block=block:match('^(.-)action%s*=%s*function')or''
        local icon=block:match('sprite%s*=%s*"([^"]+)"')
        local name=block:match('name%s*=%s*"([^"]+)"')
        local desc=block:match('description%s*=%s*"([^"]+)"')
        if #start[2]<=64 and icon and icon:match('^data/ui_gfx/gun_actions/[a-z0-9_]+%.png$')
            and name and name:match('^%$action_[a-z0-9_]+$')and call(ModDoesFileExist,icon)==true then
            spells[start[2]]={icon=icon,name=name,
                description=desc and desc:match('^%$actiondesc_[a-z0-9_]+$')and desc or nil}
        end
    end
    return spells
end
function M.validate(value)
    if type(value)~='table'or getmetatable(value)then return nil end
    local count=0
    for k in pairs(value)do
        count=count+1;if count>3 or(k~='v'and k~='category'and k~='id')then return nil end
    end
    if value.v~=1 or type(value.id)~='string'or #value.id<1 or #value.id>64
        or not value.id:match('^[%w_]+$')then return nil end
    local known=value.category=='item'and items[value.id]or value.category=='enemy'and enemies[value.id]
        or value.category=='spell'and spell_registry()[value.id]
    if not known then return nil end
    return{v=1,category=value.category,id=value.id}
end
function M.resolve(value)
    local ident=M.validate(value);if not ident then return nil end
    local registry=ident.category=='item'and items or ident.category=='enemy'and enemies or spell_registry()
    local meta=registry[ident.id]
    if call(ModDoesFileExist,meta.icon)~=true then return nil end
    local name=translated(meta.name,meta.fallback,96);if not name then return nil end
    return{category=ident.category,id=ident.id,icon=meta.icon,name=name,
        icon_animation=meta.icon_animation,icon_w=meta.icon_w,icon_h=meta.icon_h,
        icon_offset_x=meta.icon_offset_x,icon_offset_y=meta.icon_offset_y,
        description=meta.description and translated(meta.description,nil,240)or nil}
end
local function component(e,kind)return call(EntityGetFirstComponentIncludingDisabled,e,kind)end
local function tag(e,name)return call(EntityHasTag,e,name)==true end
function M.world_root(e)
    if not finite(e)or e<1 or e%1~=0 or call(EntityGetIsAlive,e)~=true
        or call(EntityGetRootEntity,e)~=e then return false end
    for _,name in ipairs({'player_unit','polymorphed_player','ew_peer','ew_notplayer','heart_statue'})do
        if tag(e,name)then return false end
    end
    if type(ctx)=='table'then
        if ctx.my_player and ctx.my_player.entity==e then return false end
        local count=0
        for _,p in pairs(ctx.players or{})do
            count=count+1;if count>128 then return false end
            if type(p)=='table'and p.entity==e then return false end
        end
    end
    return true
end
-- Classification uses native identity metadata, not AI implementation. EW removes
-- AnimalAI/PhysicsAI/AdvancedFishAI from ordinary reconstructed remote entities.
function M.inspect(entity)
    if not M.world_root(entity)then return nil end
    local item=component(entity,'ItemComponent')
    if item and call(ComponentGetIsEnabled,item)==true and call(ComponentGetValue2,item,'is_pickable')==true then
        if tag(entity,'wand')then
            local ability=component(entity,'AbilityComponent')
            if ability and call(ComponentGetValue2,ability,'use_gun_script')==true then return nil,'wand' end
            return nil
        end
        local action=component(entity,'ItemActionComponent')
        if action and tag(entity,'card_action')then
            return M.validate({v=1,category='spell',id=call(ComponentGetValue2,action,'action_id')})
        end
        if tag(entity,'perk')or tag(entity,'item_perk')then
            local vars=call(EntityGetComponentIncludingDisabled,entity,'VariableStorageComponent')or{}
            if type(vars)~='table'or #vars>64 then return nil end
            for _,c in ipairs(vars)do
                if call(ComponentGetValue2,c,'name')=='perk_id'then
                    local id=call(ComponentGetValue2,c,'value_string')
                    if type(id)=='string'then return M.validate({v=1,category='item',id='perk_'..id})end
                    return nil
                end
            end
            return nil
        end
        local path=call(EntityGetFilename,entity)
        local id=type(path)=='string'and catalogue.item_paths[path]
        if id then
            local expected=catalogue.item_names[path]
            if expected and call(ComponentGetValue2,item,'item_name')~=expected then return nil end
            -- Collision ProjectileComponents are ordinary parts of physical
            -- potions, sacks, tablets, eggs and stones; they are not an exclusion.
            return M.validate({v=1,category='item',id=id})
        end
        -- Serialization may lose EntityGetFilename. Only an absent filename can
        -- use a unique validated native item icon/name signature; mod paths do not.
        if path==nil or path==''then
            local name=call(ComponentGetValue2,item,'item_name')
            local icon=call(ComponentGetValue2,item,'ui_sprite')
            if component(entity,'BookComponent')and icon=='data/ui_gfx/items/emerald_tablet.png'then
                return M.validate({v=1,category='item',id='tablet'})
            end
            local found
            for candidate,meta in pairs(items)do
                if not candidate:match('^perk_')and meta.name==name and meta.icon==icon then
                    if found then return nil end;found=candidate
                end
            end
            if found then return M.validate({v=1,category='item',id=found})end
        end
        return nil
    end
    if not tag(entity,'enemy')or not component(entity,'DamageModelComponent')then return nil end
    local path=call(EntityGetFilename,entity)
    local id=type(path)=='string'and catalogue.enemy_paths[path]
    local name=call(EntityGetName,entity)
    if not id and(path==nil or path=='')then
        -- Native DES reconstruction may retain name/body but erase filename.
        -- Name alone is not enough: require a unique, locally generated primary
        -- body signature. Nonempty unknown/mod paths never use this fallback.
        local bodies=type(name)=='string'and catalogue.enemy_bodies[name]
        if not bodies then return nil end
        for _,kind in ipairs({'SpriteComponent','PhysicsImageShapeComponent'})do
            local list=call(EntityGetComponentIncludingDisabled,entity,kind)or{}
            if type(list)~='table'or #list>64 then return nil end
            for _,c in ipairs(list)do
                local alpha=kind=='SpriteComponent'and call(ComponentGetValue2,c,'alpha')or 1
                if call(ComponentGetIsEnabled,c)==true and(kind~='SpriteComponent'or
                    (call(ComponentGetValue2,c,'visible')~=false and(not finite(alpha)or alpha>0)))then
                    local image=call(ComponentGetValue2,c,'image_file')
                    local candidate=type(image)=='string'and bodies[image]
                    if candidate then
                        if id and id~=candidate then return nil end
                        id=candidate
                    end
                end
            end
        end
    end
    if not id then return nil end
    local meta=enemies[id]
    if name~=meta.name then return nil end
    return M.validate({v=1,category='enemy',id=id})
end
-- Compatibility entry points delegate to the same selector used by activation
-- and the hover hint. Neither path captures a wand snapshot.
function M.capture(entity,view)
    local s=dofile_once('mods/quant.ew/files/system/player_ping/world_target.lua').capture(entity,view)
    if s and s.ident then return s.x,s.y,s.ident end
end
function M.target(x,y,view)
    local s=dofile_once('mods/quant.ew/files/system/player_ping/world_target.lua').target(x,y,view)
    if s and s.ident then return s.entity,s.x,s.y,s.ident end
end
return M
