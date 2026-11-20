-- Local GUI presentation only. Layout/hit are pure; the adapter owns all input.
local model = dofile_once("mods/quant.ew/files/system/player_ping/model.lua")
local projection = dofile_once("mods/quant.ew/files/system/player_ping/projection.lua")
local M, states, last_frame = {}, {}, nil
local assets = "mods/quant.ew/files/system/player_ping/assets/"
local palette = { location = { .83, .64, .29 }, danger = { .91, .31, .24 }, wand = { .30, .76, .83 },
    spell={.70,.47,.88},item={.48,.76,.48},enemy={.98,.46,.14} }
local function clamp(n, lo, hi) return math.max(lo, math.min(hi, n)) end
local function round(n) return math.floor(n + .5) end
local function smooth(n) n=clamp(n,0,1);return n*n*(3-2*n) end
local function contains(box,x,y)
    return model.finite(x) and model.finite(y) and x>=box.x-3 and x<=box.x+box.w+3
        and y>=box.y-3 and y<=box.y+box.h+3
end
local function newer(a,b)
    local ar,br=a.record,b.record
    local au=model.finite(ar.updated) and ar.updated or ar.created
    local bu=model.finite(br.updated) and br.updated or br.created
    if au~=bu then return au>bu end
    local ai,bi=model.finite(ar.id) and ar.id or 0,model.finite(br.id) and br.id or 0
    if ai~=bi then return ai>bi end
    return a.key<b.key
end
function M.select(markers,opts)
    opts=opts or {}
    local best,distance
    for _,marker in ipairs(markers or {}) do
        if contains(marker.core_box,opts.mx,opts.my) then
            if marker.key==opts.selected_key then return marker end
            local d=(opts.mx-marker.center_x)^2+(opts.my-marker.center_y)^2
            if not best or d<distance or (d==distance and newer(marker,best)) then best,distance=marker,d end
        end
    end
    return best
end
function M.layout(records,opts)
    opts=opts or {}
    local result={}
    if type(opts.view)~="table" or not model.finite(opts.now) then return result end
    local size=model.finite(opts.extra_size) and opts.extra_size or 0
    local diameter=round(clamp(7+size,6,10))
    for _,record in ipairs(records or {}) do
        if type(record)=="table" and type(record.key)=="string" and model.finite(record.created)
            and (model.finite(record.expires) or record.expires==math.huge)
            and opts.now>=record.created and opts.now<record.expires then
            local p=projection.project(record.x,record.y,opts.view)
            if p then
                local x,y=round(p.x),round(p.y)
                local core={x=x-math.floor(diameter/2),y=y-math.floor(diameter/2),w=diameter,h=diameter}
                result[#result+1]={key=record.key,sender=record.sender,record=record,center_x=x,center_y=y,
                    x=core.x,y=core.y,w=core.w,h=core.h,core_box=core,core_size=diameter,
                    outside=p.outside,edge_pinned=p.edge_pinned}
            end
        end
    end
    table.sort(result,function(a,b)return a.key<b.key end)
    local selected=M.select(result,opts)
    result.hover_key=selected and selected.key or nil
    for _,marker in ipairs(result) do marker.hovered=marker==selected end
    return result,result.hover_key
end
function M.hit(records,opts)
    local markers=M.layout(records,opts)
    return M.select(markers,opts),markers
end
function M.clear() states,last_frame={},nil end

local function overlap(a,b)
    return a.x<b.x+b.w+2 and b.x<a.x+a.w+2 and a.y<b.y+b.h+2 and b.y<a.y+a.h+2
end
local function name_for(gui,name)
    name=type(name)=="string" and name:gsub("%c"," ") or "?"
    local result=""
    for char in name:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
        if GuiGetTextDimensions(gui,result..char.."...")>72 then return result.."..." end
        result=result..char
    end
    return result~="" and result or "?"
end
local function scope(gui,key)
    if type(GuiIdPushString)=="function" and type(GuiIdPop)=="function" then
        GuiIdPushString(gui,key);return true
    end
end
local function unscope(gui,pushed) if pushed then GuiIdPop(gui) end end
local function fallback_id(key)
    local n=0;for i=1,#key do n=(n*131+key:byte(i))%100000000 end
    return n*4
end
function M.draw(gui,records,opts)
    opts=opts or {}
    if type(opts.view)~="table" or not model.finite(opts.now) then M.clear();return {} end
    local now=opts.now
    if last_frame and now<last_frame then M.clear() end
    last_frame=now
    local markers,hover_key=M.layout(records,opts)
    local seen,labels={},{}
    local idle=model.finite(opts.idle_opacity) and clamp(opts.idle_opacity,0,1) or .65
    local function image(marker,layer,x,y,file,color,alpha)
        local pushed=scope(gui,"ew_ping:"..marker.key)
        GuiColorSetForNextWidget(gui,color[1],color[2],color[3],1)
        GuiImage(gui,(pushed and 0 or fallback_id(marker.key))+layer,round(x),round(y),assets..file..".png",alpha,1,1)
        unscope(gui,pushed)
    end
    local function add_label(marker,kind,value,offsets,alpha,color)
        if not value or alpha<=0 then return end
        local width,height=GuiGetTextDimensions(gui,value)
        width,height=math.ceil(width),math.ceil(height)
        local w,h=opts.view.w,opts.view.h
        if width>w-5 then return end
        local candidates={}
        for _,offset in ipairs(offsets) do
            local y=round(marker.center_y+offset)
            if y>=2 and y+height+1<=h-2 then
                candidates[#candidates+1]={x=round(clamp(marker.center_x-width/2,2,w-width-3)),y=y,w=width+1,h=height+1}
            end
        end
        labels[#labels+1]={marker=marker,kind=kind,value=value,candidates=candidates,alpha=alpha,color=color}
    end
    for _,marker in ipairs(markers) do
        local record=marker.record
        local age,remaining=now-record.created,record.expires-now
        local s=states[marker.key]
        if not s or s.created~=record.created then s={created=record.created};states[marker.key]=s end
        seen[marker.key]=true
        local selected=opts.selected_key==marker.key
        local target=(marker.hovered or selected) and 1 or clamp(idle+(record.kind=="danger" and .08 or 0),0,1)
        if s.target~=target then s.from=s.alpha or target;s.target=target;s.changed=now end
        s.alpha=s.from+(target-s.from)*smooth((now-s.changed)/9)
        local fade=smooth(remaining/45)
        local emphasis=(record.kind=="danger" and .96 or .88)*(1-smooth(age/90))
        marker.alpha=math.max(s.alpha,emphasis)*fade
        marker.label_alpha=s.alpha*fade -- no wave/pulse modulation of text
        marker.label_boxes={}
        local color=palette[record.kind] or palette.location
        local period=record.kind=="danger" and 72 or 120
        local duration,phase=36,age%period
        if phase<duration then
            marker.wave_size=marker.core_size+2+2*math.floor(7*phase/(duration-1))
            image(marker,1,marker.center_x-math.floor(marker.wave_size/2),marker.center_y-math.floor(marker.wave_size/2),
                "round_wave_"..marker.wave_size,color,marker.alpha*.46*(1-phase/duration))
        end
        if marker.edge_pinned then
            s.distance=model.distance(record.x,record.y,opts.view.origin_x,opts.view.origin_y)
            s.distance_at=now
        end
        local distance_fade=s.distance_at and 1-smooth((now-s.distance_at)/12) or 0
        if s.distance and distance_fade>0 then
            local _,height=GuiGetTextDimensions(gui,s.distance)
            height=math.ceil(height)
            add_label(marker,"distance",s.distance,{14,-14-height,26,-26-height},
                marker.label_alpha*.86*distance_fade,{.75,.73,.66})
        end
        if age<90 or marker.hovered or selected then
            local shared=record.source=="held"
            local names=type(opts.names)=="table" and opts.names or {}
            add_label(marker,"name",name_for(gui,names[record.sender]),shared and {-35,-47,26,38} or {-23,-35,26,38},
                marker.label_alpha*.86,{.72,.70,.64})
            if shared then add_label(marker,"source","shared wand",{-23,-35,-47,38,50,26},marker.label_alpha*.78,{.60,.65,.65}) end
        end
        local hint
        if marker.sender==opts.local_peer then hint=opts.removal_hint else hint=opts.hide_hint end
        if marker.hovered and opts.local_peer and age>18 and type(hint)=="string"then
            -- Share the measured text occupancy instead of drawing a second
            -- independent hint over the owner/distance/shared-wand labels.
            add_label(marker,"hint",hint,{14,-47,26,-59,38,50},.9,{.78,.76,.66})
        end
    end
    -- Only text yields to HUD/text/core conflicts. All cores keep their projection.
    table.sort(labels,function(a,b)
        if a.marker~=b.marker then
            local ap=a.marker.hovered or a.marker.key==opts.selected_key
            local bp=b.marker.hovered or b.marker.key==opts.selected_key
            if ap~=bp then return ap end
            return newer(a.marker,b.marker)
        end
        local priority={distance=1,name=2,source=3,hint=4}
        return priority[a.kind]<priority[b.kind]
    end)
    local occupied,accepted={},{}
    for _,label in ipairs(labels) do
        local selected=label.marker.hovered or label.marker.key==opts.selected_key
        for _,candidate in ipairs(label.candidates) do
            local free=true
            for _,marker in ipairs(markers) do if overlap(candidate,marker.core_box) then free=false;break end end
            if free then for _,box in ipairs(occupied) do if overlap(candidate,box) then free=false;break end end end
            if free and not selected then for _,box in ipairs(opts.reserved or {}) do
                if model.finite(box.x) and model.finite(box.y) and model.finite(box.w) and model.finite(box.h)
                    and overlap(candidate,box) then free=false;break end
            end end
            if free then label.box=candidate;break end
        end
        if label.box then
            occupied[#occupied+1]=label.box
            local marker,box=label.marker,label.box
            marker.label_boxes[label.kind]=box
            marker[label.kind=="source" and "source_visible" or label.kind.."_visible"]=true
            accepted[#accepted+1]=label
        end
    end
    local function text(label)
        local pushed=scope(gui,"ew_ping:"..label.marker.key..":"..label.kind)
        local box=label.box
        if label.kind=="hint"then GuiZSetForNextWidget(gui,-1012)end
        GuiColorSetForNextWidget(gui,label.kind=="hint"and .03 or .035,.025,.015,label.kind=="hint"and .8 or label.alpha*.75);GuiText(gui,box.x+1,box.y+1,label.value)
        if label.kind=="hint"then GuiZSetForNextWidget(gui,-1012)end
        GuiColorSetForNextWidget(gui,label.color[1],label.color[2],label.color[3],label.alpha);GuiText(gui,box.x,box.y,label.value)
        unscope(gui,pushed)
    end
    -- Give selected text first choice of space, then draw it in the foreground.
    for _,label in ipairs(accepted) do
        if not(label.marker.hovered or label.marker.key==opts.selected_key) then text(label) end
    end
    for _,label in ipairs(accepted) do
        if label.marker.hovered or label.marker.key==opts.selected_key then text(label) end
    end
    local function core(marker)
        local color=palette[marker.record.kind] or palette.location
        image(marker,2,marker.core_box.x,marker.core_box.y,"clipped_outline_"..marker.core_size,{.07,.055,.04},marker.alpha)
        image(marker,3,marker.core_box.x,marker.core_box.y,"clipped_fill_"..marker.core_size,color,marker.alpha*.82)
    end
    -- The selected core remains legible even when accepted positions coincide.
    for _,marker in ipairs(markers) do if not marker.hovered then core(marker) end end
    for _,marker in ipairs(markers) do if marker.hovered then core(marker) end end
    for key in pairs(states) do if not seen[key] then states[key]=nil end end
    return markers,hover_key
end
return M
