-- Read-only world selector shared by activation and the quiet pre-ping hint.
-- No inventory traversal, snapshots, physics setters or preview entities.
local M={MARGIN=3,ASSIST=7,MAX_PER_TAG=1024,MAX_CANDIDATES=2048,MAX_FINAL_CHECKS=16,MAX_SIGHT_RAYS=5}
local identity=dofile_once('mods/quant.ew/files/system/player_ping/identification.lua')
local catalogue=dofile_once('mods/quant.ew/files/system/player_ping/vanilla_catalogue.lua')
local function finite(n)return type(n)=='number'and n==n and math.abs(n)<math.huge end
local function call(fn,...)
    if type(fn)~='function'then return nil end
    local ok,a,b,c,d,e=pcall(fn,...);if ok then return a,b,c,d,e end
end
local function value(c,k)return call(ComponentGetValue2,c,k)end
local function component(e,k)return call(EntityGetFirstComponentIncludingDisabled,e,k)end
local function enabled(c)return c and call(ComponentGetIsEnabled,c)==true end
local function view_ok(v)
    return type(v)=='table'and finite(v.camera_x)and finite(v.camera_y)
        and finite(v.world_w)and v.world_w>0 and v.world_w<=8192
        and finite(v.world_h)and v.world_h>0 and v.world_h<=8192
        and finite(v.origin_x)and finite(v.origin_y)
end
local function attrs(s)
    local out={};for k,v in s:gmatch('([%w_%.]+)%s*=%s*"([^"]*)"')do out[k]=v end;return out
end
local xml_cache,xml_count={},0
local function image(path,animation)
    if type(path)~='string'or #path>240 or not path:match('^data/[%w_/.%-]+$')or path:find('..',1,true)then return end
    local size=catalogue.sprites[path]
    if size then return size[1],size[2],0,0 end
    if not path:match('%.xml$')then return end
    local parsed=xml_cache[path]
    if parsed==nil then
        if xml_count>=1024 then return end;xml_count=xml_count+1
        parsed=false
        local source=call(ModTextFileGetContent,path)
        if type(source)=='string'and #source<=262144 then
            source=source:gsub('<!%-%-.-%-%->','')
            local root=source:match('<Sprite%s+([^>]+)>')
            if root then
                local a=attrs(root);local frames={}
                local count=0
                for text in source:gmatch('<RectAnimation%s+([^>]+)>')do
                    count=count+1;if count>128 then frames={};break end
                    local f=attrs(text);local w,h=tonumber(f.frame_width),tonumber(f.frame_height)
                    if type(f.name)=='string'and finite(w)and finite(h)and w>0 and h>0 and w<=1024 and h<=1024 then
                        frames[f.name]={w,h}
                    end
                end
                parsed={frames=frames,default=a.default_animation,ox=tonumber(a.offset_x)or 0,oy=tonumber(a.offset_y)or 0}
            end
        end
        xml_cache[path]=parsed
    end
    if not parsed then return end
    local f=parsed.frames[animation]or parsed.frames[parsed.default]
    if f then return f[1],f[2],parsed.ox,parsed.oy end
end
local function rect(x,y,r,sx,sy,left,top,width,height)
    if not(finite(x)and finite(y)and finite(r)and finite(sx)and finite(sy)and finite(left)and finite(top)
        and finite(width)and finite(height))or sx==0 or sy==0 or math.abs(sx)>8 or math.abs(sy)>8
        or width<=0 or height<=0 or width>1024 or height>1024 or math.abs(left)>2048 or math.abs(top)>2048 then return end
    local c,s=math.cos(r),math.sin(r)
    local lx,ly=(left+width/2)*sx,(top+height/2)*sy
    local out={x=x+lx*c-ly*s,y=y+lx*s+ly*c,r=r,hw=width*math.abs(sx)/2,hh=height*math.abs(sy)/2}
    local ex,ey=out.hw*math.abs(c)+out.hh*math.abs(s),out.hw*math.abs(s)+out.hh*math.abs(c)
    out.min_x,out.max_x,out.min_y,out.max_y=out.x-ex,out.x+ex,out.y-ey,out.y+ey
    return out
end
local function geometry(e,x,y,r,sx,sy)
    local boxes={}
    for _,kind in ipairs({'SpriteComponent','PhysicsImageShapeComponent'})do
        local list=call(EntityGetComponentIncludingDisabled,e,kind)or{}
        if type(list)~='table'or #list>64 then return nil,'visual bound' end
        for _,c in ipairs(list)do
            local alpha=kind=='SpriteComponent'and value(c,'alpha')or 1
            if enabled(c)and(kind~='SpriteComponent'or(value(c,'visible')~=false and(not finite(alpha)or alpha>0)
                and value(c,'is_text_sprite')~=true))then
                local path=value(c,'image_file')
                local w,h,ix,iy=image(path,kind=='SpriteComponent'and value(c,'rect_animation')or nil)
                if w and h then
                    local ox,oy=value(c,'offset_x'),value(c,'offset_y')
                    local rr,xx,yy=r,x,y;local ssx,ssy=sx,sy
                    if kind=='PhysicsImageShapeComponent'then
                        -- The physics engine populates the shape pivot after
                        -- initialization. Use the actual body transform where
                        -- available; entity roots can lag/offset that visual.
                        ox=finite(ox)and ox or(value(c,'centered')==true and w/2 or 0)
                        oy=finite(oy)and oy or(value(c,'centered')==true and h/2 or 0)
                        local body_id=value(c,'body_id')
                        for _,body_kind in ipairs({'PhysicsBodyComponent','PhysicsBody2Component'})do
                            local bodies=call(EntityGetComponentIncludingDisabled,e,body_kind)or{}
                            if type(bodies)=='table'and #bodies<=32 then
                                for _,body in ipairs(bodies)do
                                    if enabled(body)and(body_id==nil or value(body,'uid')==body_id)then
                                        local px,py,pr=call(PhysicsComponentGetTransform,body)
                                        if finite(px)and finite(py)and finite(pr)then
                                            local gx,gy=call(PhysicsPosToGamePos,px,py)
                                            if finite(gx)and finite(gy)then xx,yy,rr,ssx,ssy=gx,gy,pr,1,1 end
                                        end
                                        break
                                    end
                                end
                            end
                        end
                    else
                        -- The engine uses the XML pivot for a zero component
                        -- pivot (ordinary enemies), and a nonzero component
                        -- pivot overrides it (animated hearts/refresher). Native
                        -- overlay verification confirmed these are not additive.
                        if(not finite(ox)and not finite(oy))or(ox==0 and oy==0)then
                            ox,oy=ix,iy
                        else ox,oy=finite(ox)and ox or ix,finite(oy)and oy or iy end
                        if value(c,'update_transform_rotation')==false then rr=0 end
                        -- has_special_scale is documented but not readable on
                        -- this native build. The exposed signed scale values
                        -- carry remote sprite-facing flips and default to 1.
                        local ax,ay=value(c,'special_scale_x'),value(c,'special_scale_y')
                        if finite(ax)then ssx=ssx*ax end;if finite(ay)then ssy=ssy*ay end
                        -- Manually positioned worm segments do not expose their
                        -- sprite transform through the Lua API. Their root/head
                        -- remains selectable by its native hitbox below.
                        if value(c,'update_transform')==false then w=nil end
                    end
                    if w then local b=rect(xx,yy,rr,ssx,ssy,-ox,-oy,w,h);if b then boxes[#boxes+1]=b end end
                end
            end
        end
    end
    if #boxes==0 then
        -- Body fallback for native segmented/AI-driven sprites with no exposed
        -- transform. Never synthesize an origin-only wand or item target.
        local worm=component(e,'WormComponent')or component(e,'BossDragonComponent')
        if worm then
            local radius=value(worm,'hitbox_radius')
            if finite(radius)and radius>0 and radius<=64 then boxes[1]=rect(x,y,0,1,1,-radius,-radius,radius*2,radius*2)end
        end
    end
    if #boxes==0 then return nil,'no displayed geometry' end
    return boxes
end
local function intersects(b,v)
    return b.max_x>=v.camera_x-v.world_w/2 and b.min_x<=v.camera_x+v.world_w/2
        and b.max_y>=v.camera_y-v.world_h/2 and b.min_y<=v.camera_y+v.world_h/2
end
local function body_visible(boxes,v)
    local checked=0
    -- A few points on the displayed body avoid a buried foot/origin. This is a
    -- simple terrain check, with no light/fog propagation or continuing tracking.
    for _,b in ipairs(boxes)do
        if intersects(b,v)then
            for _,f in ipairs({{0,0},{0,-.55},{-.55,0},{.55,0},{0,.55}})do
                local c,s=math.cos(b.r),math.sin(b.r)
                local dx,dy=f[1]*b.hw,f[2]*b.hh
                local x,y=b.x+dx*c-dy*s,b.y+dx*s+dy*c
                if math.abs(x-v.camera_x)<=v.world_w/2 and math.abs(y-v.camera_y)<=v.world_h/2
                    and call(DoesWorldExistAt,x-1,y-1,x+1,y+1)==true then
                    checked=checked+1;if checked>M.MAX_SIGHT_RAYS then return false end
                    if call(RaytraceSurfaces,v.origin_x,v.origin_y,x,y)==false then return true end
                end
            end
        end
    end
    return false
end
local function prepare(e,v,allow_unknown)
    if not view_ok(v)then return nil,'invalid view' end
    local ident,kind=identity.inspect(e)
    kind=kind or(ident and ident.category)
    local unknown,enemy
    if not kind then
        if not allow_unknown or not identity.world_root(e)then return nil,'unsupported identity or not a loose world root' end
        local item=component(e,'ItemComponent');enemy=call(EntityHasTag,e,'enemy')==true
        if not enemy and(not enabled(item)or value(item,'is_pickable')~=true)then return nil,'unsupported identity or not a loose world root' end
        unknown,kind=true,'unknown'
    end
    local x,y,r,sx,sy=call(EntityGetTransform,e)
    if not finite(x)or not finite(y)then return nil,'invalid transform' end
    r,sx,sy=finite(r)and r or 0,finite(sx)and sx or 1,finite(sy)and sy or 1
    local boxes,reason=geometry(e,x,y,r,sx,sy)
    if not boxes then return nil,reason end
    local visible={};local min_x,min_y,max_x,max_y
    for _,b in ipairs(boxes)do
        if intersects(b,v)then
            visible[#visible+1]=b
            min_x=math.min(min_x or b.min_x,b.min_x);max_x=math.max(max_x or b.max_x,b.max_x)
            min_y=math.min(min_y or b.min_y,b.min_y);max_y=math.max(max_y or b.max_y,b.max_y)
        end
    end
    if #visible==0 then return nil,'outside camera' end
    return{entity=e,x=x,y=y,kind=kind,ident=ident,boxes=visible,is_unknown=unknown,is_enemy=enemy or kind=='enemy',
        bounds={min_x=min_x,min_y=min_y,max_x=max_x,max_y=max_y}}
end
local function eligible(selection,v)
    local b=selection.bounds;local cx,cy=(b.min_x+b.max_x)/2,(b.min_y+b.max_y)/2
    if call(DoesWorldExistAt,cx-1,cy-1,cx+1,cy+1)~=true then return nil,'unloaded body' end
    if selection.ident and not identity.resolve(selection.ident)then return nil,'missing local identity art/name',true end
    if selection.is_enemy and not body_visible(selection.boxes,v)then return nil,'enemy body occluded' end
    if selection.is_unknown then return nil,'unsupported object under pointer',true end
    return selection
end
function M.capture(e,v)
    local selection,reason=prepare(e,v,false)
    if not selection then return nil,reason end
    return eligible(selection,v)
end
local function distance(b,x,y)
    local dx,dy=x-b.x,y-b.y;local c,s=math.cos(b.r),math.sin(b.r)
    local xx,yy=dx*c+dy*s,-dx*s+dy*c
    local ex,ey=math.max(0,math.abs(xx)-b.hw),math.max(0,math.abs(yy)-b.hh)
    return ex*ex+ey*ey,xx*xx/(b.hw*b.hw+1)+yy*yy/(b.hh*b.hh+1),b.hw*b.hh*4
end
local function score(selection,x,y)
    local best,center,area
    for _,b in ipairs(selection.boxes)do
        local d,c,a=distance(b,x,y)
        if not best or d<best or(d==best and c<center)then best,center,area=d,c,a end
    end
    if best and best<=M.ASSIST^2 then return best==0 and 0 or(best<=M.MARGIN^2 and 1 or 2),best,center,area end
end
function M.revalidate(selection,x,y,v)
    if type(selection)~='table'or not finite(x)or not finite(y)then return nil,'invalid selection' end
    local fresh,reason=M.capture(selection.entity,v)
    if not fresh then return nil,reason end
    if fresh.kind~=selection.kind or(fresh.ident and(not selection.ident or fresh.ident.id~=selection.ident.id
        or fresh.ident.category~=selection.ident.category))then return nil,'identity changed' end
    if not score(fresh,x,y)then return nil,'moved outside pointer assistance' end
    return fresh
end
function M.target(x,y,v,previous)
    if not finite(x)or not finite(y)or not view_ok(v)then return nil,'invalid pointer/view' end
    if math.abs(x-v.camera_x)>v.world_w/2 or math.abs(y-v.camera_y)>v.world_h/2 then return nil,'pointer outside camera' end
    -- Search from the camera, so a long wand or body is not lost merely because
    -- its entity origin lies beyond a tiny cursor-centred radius.
    local radius=math.sqrt(v.world_w^2+v.world_h^2)/2+256
    local ids,seen={},{}
    local tags={'wand','item','item_pickup','card_action','tablet','perk','enemy'}
    if type(EntityGetInRadiusWithTag)=='function'then
        for _,tag in ipairs(tags)do
            local list=call(EntityGetInRadiusWithTag,v.camera_x,v.camera_y,radius,tag)
            if list~=nil and(type(list)~='table'or #list>M.MAX_PER_TAG)then return nil,'candidate bound' end
            if type(list)=='table'then
                for _,e in ipairs(list)do if finite(e)and not seen[e]then seen[e]=true;ids[#ids+1]=e end end
            end
            if #ids>M.MAX_CANDIDATES then return nil,'candidate bound' end
        end
    else
        local list=call(EntityGetInRadius,v.camera_x,v.camera_y,radius)
        if type(list)~='table'or #list>M.MAX_CANDIDATES then return nil,'candidate bound' end
        ids=list
    end
    -- Classification and geometry are cheap, local getter work. Resolve names/
    -- artwork and test terrain only for ranked objects relevant to this pointer;
    -- a crowded camera must not raytrace every enemy merely to draw a hint.
    local ranked,old={}
    for _,e in ipairs(ids)do
        local candidate=prepare(e,v,true)
        if candidate then
            local tier,d,c,a=score(candidate,x,y)
            if tier and(not candidate.is_unknown or tier==0)then
                local rank={selection=candidate,tier=tier,d=d,center=c,area=a,
                    order=tier+(candidate.is_unknown and .5 or 0)}
                ranked[#ranked+1]=rank
                if e==previous and not candidate.is_unknown then old=rank end
            end
        end
    end
    table.sort(ranked,function(a,b)
        if a.order~=b.order then return a.order<b.order end
        if a.tier==0 then
            if a.area~=b.area then return a.area<b.area end
        elseif a.d~=b.d then return a.d<b.d end
        if a.center~=b.center then return a.center<b.center end
        return a.selection.entity<b.selection.entity
    end)
    -- Same-tier retention can never promote assistance above a direct hit or an
    -- unknown object directly under the cursor. Final eligibility is still fresh.
    local first=ranked[1]
    if old and first and old.order==first.order and(old.tier==0 or math.sqrt(old.d)<=math.sqrt(first.d)+.75)then
        for i,r in ipairs(ranked)do if r==old then table.remove(ranked,i);break end end
        table.insert(ranked,1,old)
    end
    local blocked_direct,last_reason
    for i,rank in ipairs(ranked)do
        if rank.tier>0 and blocked_direct then return nil,'unsupported object under pointer' end
        if i>M.MAX_FINAL_CHECKS then return nil,'pointer candidate bound' end
        local selection,reason,blocks=eligible(rank.selection,v)
        if selection then return selection end
        if blocks and rank.tier==0 then blocked_direct=true end
        last_reason=reason
    end
    return nil,blocked_direct and'unsupported object under pointer'or last_reason or'no eligible displayed object'
end
function M.draw_hint(gui,selection,v)
    if type(selection)~='table'or type(selection.bounds)~='table'or not view_ok(v)
        or not finite(v.w)or not finite(v.h)then return end
    local b=selection.bounds
    local x=v.w/2+(b.min_x-v.camera_x)*v.w/v.world_w-1
    local y=v.h/2+(b.min_y-v.camera_y)*v.h/v.world_h-1
    local w=(b.max_x-b.min_x)*v.w/v.world_w+2
    local h=(b.max_y-b.min_y)*v.h/v.world_h+2
    x,y=math.max(1,x),math.max(28,y);w,h=math.min(w,v.w-x-1),math.min(h,v.h-y-1)
    if w<=1 or h<=1 then return end
    local l=math.min(3,w/3,h/3);local t=.5
    call(GuiIdPushString,gui,'ping_world_target_hint')
    for i,line in ipairs({{x,y,l,t},{x,y,t,l},{x+w-l,y,l,t},{x+w-t,y,t,l},
        {x,y+h-t,l,t},{x,y+h-l,t,l},{x+w-l,y+h-t,l,t},{x+w-t,y+h-l,t,l}})do
        call(GuiZSetForNextWidget,gui,9);call(GuiColorSetForNextWidget,gui,.92,.84,.62,.38)
        call(GuiImage,gui,i,line[1],line[2],'data/ui_gfx/empty_white.png',.38,line[3]/2,line[4]/2)
    end
    call(GuiIdPop,gui)
end
return M
