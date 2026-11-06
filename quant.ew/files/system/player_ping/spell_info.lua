-- Read-only spell metadata. No action function or engine entity is invoked.
-- A sealed return-function constructor verifies native bytecode provenance only;
-- actual stats come from conservative text inspection, never action evaluation.
local M={}
local blocks,origins,files,file_count
local function finite(n)return type(n)=="number" and n==n and math.abs(n)<math.huge end
local function clean(s,limit)
    s=type(s)=="string" and s:gsub("%c"," ") or ""
    local out=""
    for c in s:gmatch("[%z\1-\127\194-\244][\128-\191]*")do
        if #out+#c>(limit or 512)then break end;out=out..c
    end
    return out
end
local function text(key,fallback)
    if type(GameTextGetTranslatedOrNot)=="function"then
        local ok,s=pcall(GameTextGetTranslatedOrNot,key)
        if ok and type(s)=="string" and s~="" and s~=key then return clean(s)end
    end
    return fallback or clean(key)
end
local function unit(key,value,fallback)
    return(text(key,"$0 "..fallback):gsub("%$0",function()return value end))
end
local function read(path)
    files=files or {};file_count=file_count or 0
    if files[path]~=nil then return files[path]or nil end
    if file_count>=128 or type(path)~="string" or #path>240 or path:find("..",1,true)
        or not path:match("^data/[%w_/%.-]+$") or type(ModTextFileGetContent)~="function"then return end
    file_count=file_count+1
    local ok,s=pcall(ModTextFileGetContent,path)
    if not ok or type(s)~="string" or #s>3000000 then files[path]=false;return end
    s=s:gsub("\r\n","\n"):gsub("\r","\n"):gsub("<!%-%-.-%-%->","")
    files[path]=s;return s
end
local function native_blocks()
    if blocks then return blocks end
    blocks,origins={},{}
    local s=read("data/scripts/gun/gun_actions.lua")or""
    local starts={}
    for pos,id in s:gmatch('()\n%s*id%s*=%s*"([%w_]+)"')do
        if #starts>=2048 then break end;starts[#starts+1]={pos,id}
    end
    local prior_pos,prior_line=1,1
    for i,v in ipairs(starts)do
        local _,advance=s:sub(prior_pos,v[1]-1):gsub("\n","")
        prior_line,prior_pos=prior_line+advance,v[1]
        local block=s:sub(v[1],starts[i+1]and starts[i+1][1]-1 or #s)
        blocks[v[2]]=block
        local function_pos=block:match("action%s*=%s*()function%s*%b()")
        local ending=function_pos and block:match("\n%s*()end%s*,",function_pos)
        if function_pos and ending then
            local _,first=block:sub(1,function_pos-1):gsub("\n","")
            local _,last=block:sub(1,ending-1):gsub("\n","")
            origins[v[2]]={first=prior_line+first,last=prior_line+last,
                definition=block:sub(function_pos,ending+2)}
        end
    end
    return blocks
end
local function matches_definition(fn,origin)
    if origin.prototype==nil then
        -- This constructor can only return a closure. Its body is never called,
        -- and its environment has no gameplay functions, globals or metatable.
        local loader=loadstring("return "..origin.definition,"@ew_spell_info_definition")
        local ok,constructor
        if loader then setfenv(loader,{});ok,constructor=pcall(loader)end
        if ok and type(constructor)=="function"then
            local dumped,prototype=pcall(string.dump,constructor,true)
            origin.prototype=dumped and prototype or false
        else origin.prototype=false end
    end
    if not origin.prototype then return false end
    local ok,prototype=pcall(string.dump,fn,true)
    return ok and prototype==origin.prototype
end
local function quoted(s,key)return s:match('%f[%w_]'..key..'%s*=%s*"([^"]*)"')end
local function number(s,key)return tonumber(s:match('%f[%w_]'..key..'%s*=%s*([^,\n}]+)'))end
local types={PROJECTILE={"projectile","Projectile"},STATIC_PROJECTILE={"staticprojectile","Static proj."},
    MODIFIER={"modifier","Proj. modifier"},DRAW_MANY={"drawmany","Multicast"},MATERIAL={"material","Material"},
    OTHER={"other","Other"},UTILITY={"utility","Utility"},PASSIVE={"passive","Passive"}}
local function xml(path,depth,seen)
    if depth>8 or seen[path]then return nil end;seen[path]=true
    local s=read(path);if not s then return nil end
    local values={}
    for base in s:gmatch('<Base%s+[^>]-file%s*=%s*"([^"]+)"')do
        local inherited=xml(base,depth+1,seen)
        if inherited then for k,v in pairs(inherited)do values[k]=v end end
    end
    local attrs,body=s:match("<ProjectileComponent%s+([^>]-)>(.-)</ProjectileComponent>")
    attrs=attrs or s:match("<ProjectileComponent%s+([^>]-)/>")
    if attrs then
        for _,k in ipairs({"damage","speed_min","speed_max","lifetime","lifetime_randomness","bounces"})do
            local v=tonumber(attrs:match('%f[%w_]'..k..'%s*=%s*"([^"]+)"'));if finite(v)then values[k]=v end
        end
        local explosion=(body or ""):match("<config_explosion%s+([^>]+)")
        if explosion then
            for _,k in ipairs({"damage","explosion_radius"})do
                local v=tonumber(explosion:match('%f[%w_]'..k..'%s*=%s*"([^"]+)"'))
                if finite(v)then values["explosion_"..k]=v end
            end
        end
    end
    return values
end
-- Installed renderer helpers: B5F320/B5F2E0 use lroundf after damage*25;
-- B5F360 formats frames/60 to two decimals; spread/mana use cvttss2si.
local function truncate(n)return n<0 and math.ceil(n)or math.floor(n)end
local function rounded(n)return n<0 and math.ceil(n-.5)or math.floor(n+.5)end
local function decimal(n,places)
    -- B5F060 removes the fractional part only when all fractional digits are
    -- zero; a nonintegral value keeps its full fixed precision (e.g. 0.50).
    local s=string.format("%."..places.."f",n):gsub("%.0+$","")
    return s=="-0"and"0"or s
end
local function signed(s)return(s:sub(1,1)=="-"and""or"+")..s end
local icons={type="action_type",mana="mana_drain",uses="action_max_uses",cast_delay="fire_rate_wait",
    recharge="reload_time",spread="spread_degrees",projectile_damage="damage_projectile",
    explosion_damage="damage_explosion",explosion_radius="explosion_radius",projectile_speed="speed_multiplier",
    speed_multiplier="speed_multiplier"}
local rank={type=1,uses=2,mana=3,projectile_damage=4,explosion_damage=5,explosion_radius=6,
    projectile_speed=7,cast_delay=8,recharge=9,speed_multiplier=10,spread=11}
for i,damage in ipairs({"projectile","melee","fire","electricity","explosion","ice","slice","healing","curse","drill"})do
    rank["bonus_"..damage]=20+i
end
function M.clear()blocks,origins,files,file_count=nil,nil,nil,nil end
function M.lookup(id,uses)
    if type(id)~="string" or #id>64 or not id:match("^[A-Z0-9_]+$")then return nil end
    local block=native_blocks()[id]
    local action
    for i,a in ipairs(actions or {})do if i>2048 then break end;if a.id==id then action=a;break end end
    if not block and not action then
        return{id=id,name=id,description="Unknown spell",unknown=true,rows={},values={},icon="data/ui_gfx/gun_actions/unidentified.png"}
    end
    block=block or""
    local meta=block:match("^(.-)action%s*=%s*function")or block
    meta=meta:gsub("%-%-[^\n]*","")
    local function field(k)return action and action[k]or quoted(meta,k)end
    local name=field("name")or id
    local info={id=id,name=text(name,id),description=text(field("description")or"",""),rows={},values={},display={},
        icon=field("sprite")or"data/ui_gfx/gun_actions/unidentified.png"}
    if type(info.icon)~="string"or not info.icon:match("^data/ui_gfx/gun_actions/[%w_]+%.png$")then info.icon="data/ui_gfx/gun_actions/unidentified.png"end
    local function row(key,label,value,raw)
        local icon=icons[key]or key:match("^bonus_(.+)$")and("damage_"..key:match("^bonus_(.+)$"))
        info.rows[#info.rows+1]={key=key,label=label,value=value,raw=raw,
            group=(key=="cast_delay"or key=="recharge"or key=="spread"or key=="speed_multiplier"or key:match("^bonus_"))and"modifier"or"base",
            icon=icon and("data/ui_gfx/inventory/icon_"..icon..".png")or nil}
        info.values[key],info.display[key]=raw,value
    end
    local kind
    if action and action.type~=nil then
        for k in pairs(types)do if action.type==_G["ACTION_TYPE_"..k]then kind=k;break end end
    else kind=meta:match("type%s*=%s*ACTION_TYPE_([A-Z_]+)") end
    local t=types[kind or ""]
    row("type",text("$inventory_actiontype","Type"),t and text("$inventory_actiontype_"..t[1],t[2])or"Unknown",kind)
    local mana=action and action.mana or number(meta,"mana")
    if mana==nil and block~="" and not meta:match("%f[%w_]mana%s*=")then mana=10 end -- installed gun.lua default
    if finite(mana)then row("mana",text("$inventory_manadrain","Mana drain"),tostring(truncate(mana)),mana)end
    if finite(uses)and uses>=-1 then
        info.values.uses=uses
        if uses>=0 then row("uses",text("$inventory_usesremaining","Uses remaining"),tostring(uses),uses)end
    end
    local body=block:match("action%s*=%s*function%s*%b()(.*)")or""
    -- No copied/modded action can silently borrow a vanilla function's stats.
    -- Inspect debug metadata only; never invoke the action or change its environment.
    local origin=origins and origins[id]
    local debug_info=action and type(action.action)=="function"and debug and debug.getinfo and debug.getinfo(action.action,"S")
    local native=origin and debug_info and (debug_info.source=="@data/scripts/gun/gun_actions.lua"
        or debug_info.source=="data/scripts/gun/gun_actions.lua")
        and debug_info.linedefined==origin.first and debug_info.lastlinedefined==origin.last
        and matches_definition(action.action,origin)
    local dynamic=body==""or body:find("%f[%a]if%f[%A]")or body:find("%f[%a]for%f[%A]")or body:find("%f[%a]while%f[%A]")
        or body:find("Random",1,true)or body:find("GameGet",1,true)
    local incomplete=not native
    local represented={}
    local function delta(field)
        local value,count,statement
        for line in body:gmatch("[^\n]+")do
            line=line:gsub("%-%-.*$","")
            local expression=line:match("^%s*c%."..field.."%s*=%s*(.-)%s*$")
            if expression then
                count=(count or 0)+1
                local op,n=expression:match("^c%."..field.."%s*([+%-])%s*([%-]?%d*%.?%d+)%s*;?%s*$")
                if not n then incomplete=true;return end
                value=tonumber(n)*(op=="-"and -1 or 1)
                statement=line:gsub("%s",""):gsub(";$","")
            end
        end
        if count==1 then represented[statement]=true;return value elseif count then incomplete=true end
    end
    if native and not dynamic then
        for _,entry in ipairs({{"fire_rate_wait","cast_delay","Cast delay",60," s","$inventory_mod_castdelay"},
            {"spread_degrees","spread","Spread",1," DEG","$inventory_mod_spread"}})do
            local n=delta(entry[1]);if finite(n)then
                local shown=entry[4]==60 and decimal(n/60,2)or tostring(truncate(n))
                row(entry[2],text(entry[6],entry[3]),unit(entry[4]==60 and"$inventory_seconds"or"$inventory_degrees",signed(shown),entry[4]==60 and"s"or"DEG"),n/entry[4])
            end
        end
        local critical=delta("damage_critical_chance")
        if finite(critical)then info.values.critical_chance=critical end -- no native spell-card row
        -- These visual/trajectory bookkeeping assignments have no native stat
        -- rows. Their absence alone does not require an extra analysis footer.
        delta("screenshake");delta("gore_particles")
        for line in body:gmatch("[^\n]+")do
            local code=line:gsub("%-%-.*$","")
            if code:match("^%s*c%.dampening%s*=%s*[%-]?%d*%.?%d+%s*;?%s*$")then
                represented[code:gsub("%s",""):gsub(";$","")]=true
            end
        end
        for _,damage in ipairs({"projectile","melee","fire","electricity","explosion","ice","slice","healing","curse","drill"})do
            local n=delta("damage_"..damage.."_add")
            if finite(n)then
                local key=damage=="projectile"and"$inventory_mod_damage"or"$inventory_mod_damage_"..(damage=="electricity"and"electric"or damage)
                row("bonus_"..damage,text(key,damage=="projectile"and"Damage"or"Dmg. "..damage),signed(tostring(rounded(n*25))),n*25)
            end
        end
        for line in body:gmatch("[^\n]+")do
            line=line:gsub("%-%-.*$","")
            local op,n=line:match("^%s*current_reload_time%s*=%s*current_reload_time%s*([+%-])%s*([%-]?%d*%.?%d+)%s*;?%s*$")
            if n then
                n=tonumber(n)*(op=="-"and -1 or 1)/60;row("recharge",text("$inventory_mod_rechargetime","Recharge time"),unit("$inventory_seconds",signed(decimal(n,2)),"s"),n)
                represented[line:gsub("%s",""):gsub(";$","")]=true
            end
        end
    end
    local speed_definition=origin and origin.definition:gsub("%-%-[^\n]*",""):gsub("%s","")
    local known_speed="function()c.speed_multiplier=c.speed_multiplier*2.5if(c.speed_multiplier>=20)then"
        .."c.speed_multiplier=math.min(c.speed_multiplier,20)elseif(c.speed_multiplier<0)then"
        .."c.speed_multiplier=0enddraw_actions(1,true)end"
    local represented_speed=native and id=="SPEED" and speed_definition==known_speed
    if represented_speed then
        row("speed_multiplier",text("$inventory_mod_speed","Proj. Speed"),"x "..decimal(2.5,2),2.5);dynamic=false
    end
    local paths={};local related=meta:match("related_projectiles%s*=%s*{(.-)}")or""
    for p in related:gmatch('"([^"]+%.xml)"')do paths[#paths+1]=p end
    if native and #paths==1 then
        local v=xml(paths[1],0,{})
        if v then
            if finite(v.damage)and v.damage~=0 then row("projectile_damage",text("$inventory_damage","Damage"),tostring(rounded(v.damage*25)),v.damage*25)end
            -- Keep the unrounded zero in raw data even when the native row is absent.
            if finite(v.damage)then info.values.projectile_damage=v.damage*25 end
            if finite(v.explosion_damage)and v.explosion_damage>0 then
                row("explosion_damage",text("$inventory_dmg_explosion","Dmg. Expl"),tostring(truncate(v.explosion_damage*25)),v.explosion_damage*25)
                if finite(v.explosion_explosion_radius)and v.explosion_explosion_radius>0 then
                    row("explosion_radius",text("$inventory_explosion_radius","Expl. Radius"),tostring(truncate(v.explosion_explosion_radius)),v.explosion_explosion_radius)
                end
            end
            if finite(v.speed_min)and finite(v.speed_max)then
                local speed=(v.speed_min+v.speed_max)/2
                row("projectile_speed",text("$inventory_speed","Speed"),tostring(truncate(speed)),speed)
                info.values.projectile_speed_range={v.speed_min,v.speed_max}
            end
        else dynamic=true end
    elseif #paths>1 then dynamic=true end
    if native and not represented_speed then
        -- A small statement allowlist describes what was actually represented.
        -- Every other mutation/call is partial, including static effects that
        -- cannot be summarized by the rows above. Never infer completeness from
        -- the absence of an if/Random expression.
        for line in origin.definition:gmatch("[^\n]+")do
            local code=line:gsub("%-%-.*$",""):gsub("%s",""):gsub(";$","")
            local known=code==""or code=="function()"or code=="end"or represented[code]
                or code=="draw_actions(1,true)"
            local projectile=code:match('^add_projectile%("([^"]+)"%)$')
            if projectile and #paths==1 and projectile==paths[1]then known=true end
            if not known then incomplete=true end
        end
    end
    info.partial=not not(dynamic or incomplete)
    table.sort(info.rows,function(a,b)return(rank[a.key]or 20)<(rank[b.key]or 20)end)
    info.note=info.partial and "Partial details; some effects unavailable."or nil
    return info
end
return M
