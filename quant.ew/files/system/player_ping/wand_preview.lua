local Snapshot = dofile_once("mods/quant.ew/files/system/player_ping/wand_snapshot.lua")
local EZWand = dofile_once("mods/quant.ew/files/lib/EZWand.lua")
local SpellInfo = dofile_once("mods/quant.ew/files/system/player_ping/spell_info.lua")
local SpellTooltip = dofile_once("mods/quant.ew/files/system/player_ping/spell_tooltip.lua")
local Identification = dofile_once("mods/quant.ew/files/system/player_ping/identification.lua")
local IdentityTooltip = dofile_once("mods/quant.ew/files/system/player_ping/identity_tooltip.lua")
local Geometry = dofile_once('mods/quant.ew/files/system/player_ping/hover_geometry.lua')
local Preview = {}
local hover, last_now, last_body, bounds
function Preview.clear()
    hover, last_now, last_body, bounds = nil, nil, nil, nil
end
-- Kept for adapter compatibility; transient held feedback is now caller-owned.
function Preview.clear_held() end
function Preview.active_key() return hover and hover.box and hover.key or nil end
function Preview.last_bounds() return bounds end
function Preview.blocks_world(x,y)
    return hover and hover.box and Geometry.inside(x,y,hover.box,0)
        or hover and hover.child and hover.child.box and Geometry.inside(x,y,hover.child.box,0) or false
end
local inside=Geometry.inside
local function marker_box(marker)
    if marker.core_box then return marker.core_box end
    return { x=marker.x, y=marker.y, w=marker.w or 8, h=marker.h or 8 }
end
local function passage(mx,my,marker,card)return Geometry.bridge(mx,my,marker_box(marker),card)end
local function place(marker, plan, w, h)
    local dot, pad = marker_box(marker), 4
    local x = dot.x + dot.w + 8
    if x + plan.width > w-pad then x = dot.x-plan.width-8 end
    local y = dot.y-8
    x = math.max(pad, math.min(w-plan.width-pad, x))
    y = math.max(pad, math.min(h-plan.height-pad, y))
    return { x=x, y=y, w=plan.width, h=plan.height }
end
function Preview.draw(gui, markers, opts)
    opts = opts or {}
    bounds=nil
    local now, w, h = opts.now or 0, opts.w or 427, opts.h or 240
    if opts.hidden then Preview.clear(); return nil end
    if (last_now and now < last_now) or (last_body ~= nil and opts.body ~= nil and last_body ~= opts.body) then Preview.clear() end
    last_now, last_body = now, opts.body
    local mx, my = opts.mx, opts.my
    if type(mx) ~= "number" or type(my) ~= "number" then hover=nil; return nil end
    local current, candidate
    for _, marker in ipairs(markers or {}) do
        if marker.record and (marker.record.wand or marker.record.ident) then
            if hover and marker.key == hover.key and marker.sender == hover.sender then current=marker end
            -- Renderer supplies the single selected visible core. Fallback is for
            -- callers of the standalone preview API without renderer metadata.
            local selected=markers.hover_key and marker.key==markers.hover_key
                or(not markers.hover_key and marker.hovered~=false and inside(mx,my,marker_box(marker),3))
            if selected and not candidate then candidate=marker end
        end
    end
    -- Actual removal/replacement is immediate, even during pointer exit grace.
    if hover and not current then hover=nil end
    -- A projected dot behind a visible card is not a visible hover target.
    -- Exposed different cores still take priority over bridge/exit retention.
    if hover and current and (inside(mx,my,hover.box,0)
        or(hover.child and inside(mx,my,hover.child.box,0)))then candidate=current end
    local in_child=hover and hover.child and hover.child.box and
        (inside(mx,my,hover.child.box,5)or passage(mx,my,{core_box=hover.child.slot},hover.child.box))
    local retained=hover and current and hover.box and
        (in_child or inside(mx,my,hover.box,5)or passage(mx,my,current,hover.box)
            or inside(mx,my,marker_box(current),3))
    if hover and current and hover.box and (not candidate or candidate.key==hover.key)and (retained or not candidate)and Geometry.retain(hover,retained,now)then
        -- Freeze the open card; brief overshoot does not close it.
    elseif candidate then
        if not hover or hover.key~=candidate.key or hover.sender~=candidate.sender then
            hover={key=candidate.key,sender=candidate.sender,since=now}
        end
        hover.left_at=nil;current=candidate
    else hover=nil end
    if not hover or not current or now-hover.since < 12 then return nil end
    local seq=type(current.record.seq)=="number" and current.record.seq or 0
    if hover.seq and seq > hover.seq then
        -- A deliberate re-ping refreshes the captured data for this logical dot.
        -- Ordinary frames keep the original snapshot and its fixed card position.
        hover.data, hover.plan, hover.child = nil, nil, nil
    end
    local rich=current.record.wand~=nil
    local compact=not rich and current.record.ident and current.record.ident.category~='spell'
    if not hover.data then
        if rich then hover.data=Snapshot.validate(current.record.wand)
        else
            local meta=Identification.resolve(current.record.ident)
            hover.data=meta and(meta.category=='spell'and SpellInfo.lookup(meta.id)or meta)
        end
    end
    if not hover.data then hover=nil; return nil end
    hover.seq=math.max(hover.seq or seq,seq)
    local qualifier=current.record.source=="held" and "shared" or "captured"
    if not hover.plan or hover.w ~= w or hover.h ~= h or hover.qualifier~=qualifier then
        if rich then hover.plan=EZWand.MeasureTooltip(gui,hover.data,{max_width=w-8,max_height=h-8,qualifier=qualifier})
        elseif compact then hover.plan=IdentityTooltip.measure(gui,hover.data,{max_width=w-8,max_height=h-8})
        else hover.plan=SpellTooltip.measure(gui,hover.data,{max_width=w-8,max_height=h-8})end
        if not hover.plan then hover=nil; return nil end
        hover.w, hover.h, hover.qualifier = w,h,qualifier
        if hover.box then
            hover.planned={x=math.max(4,math.min(w-hover.plan.width-4,hover.box.x)),
                y=math.max(4,math.min(h-hover.plan.height-4,hover.box.y)),w=hover.plan.width,h=hover.plan.height}
        else hover.planned=place(current,hover.plan,w,h) end
    end
    GuiIdPushString(gui,"ew_ping_wand_preview")
    hover.plan.mx,hover.plan.my=mx,my
    local actual
    if rich then
        actual=EZWand.RenderTooltip(hover.planned.x+5-hover.plan.frame_offset_x,
            hover.planned.y+5-hover.plan.frame_offset_y,hover.data,gui,-1020,hover.plan)
    elseif compact then
        actual=IdentityTooltip.draw(gui,marker_box(current),hover.data,{w=w,h=h,plan=hover.plan,box=hover.planned})
    else
        actual=SpellTooltip.draw(gui,marker_box(current),hover.data,{w=w,h=h,plan=hover.plan,box=hover.planned})
    end
    GuiIdPop(gui)
    if not actual then hover=nil;return nil end
    hover.box=actual
    bounds={planned=hover.planned,actual=actual}
    -- Frame insets are measured before placement. If a native style changes
    -- later, retain truthful draw bounds and correct the anchor on the next draw.
    local x=math.max(4,math.min(w-actual.w-4,actual.x))
    local y=math.max(4,math.min(h-actual.h-4,actual.y))
    if x~=actual.x or y~=actual.y then
        hover.planned={x=hover.planned.x+x-actual.x,y=hover.planned.y+y-actual.y,w=hover.plan.width,h=hover.plan.height}
        bounds.overflow=true
    end
    if math.abs(actual.w-hover.plan.width)>.5 or math.abs(actual.h-hover.plan.height)>.5 then
        -- The renderer refreshed its native frame metrics from this draw. Plan
        -- again against those metrics, including overflow slots if space shrank.
        hover.plan=nil
    end
    if not rich then hover.child=nil;return actual,bounds.planned end
    local selected,old_slot
    for _,slot in ipairs(actual.slot_rects or {})do
        if not(in_child and hover.child and inside(mx,my,hover.child.box,5))
            and inside(mx,my,slot,0)and not selected then selected=slot end
        if hover.child and slot.slot==hover.child.slot.slot and slot.always_cast==hover.child.slot.always_cast
            and slot.spell_id==hover.child.slot.spell_id then old_slot=slot end
    end
    if selected then
        local child=hover.child
        if not child or child.slot.slot~=selected.slot or child.slot.always_cast~=selected.always_cast
            or child.slot.spell_id~=selected.spell_id or child.slot.uses~=selected.uses then
            hover.child={slot=selected,info=SpellInfo.lookup(selected.spell_id,selected.uses)}
        else child.slot=selected;child.left_at=nil end
    elseif hover.child and old_slot and Geometry.retain(hover.child,in_child,now)then
        hover.child.slot=old_slot
    else hover.child=nil end
    if hover.child and hover.child.info then
        hover.child.box=SpellTooltip.draw(gui,hover.child.slot,hover.child.info,{w=w,h=h,parent=actual,box=hover.child.box})
        bounds.child=hover.child.box
    end
    return actual,bounds.planned
end
-- Retain only the brief inventory-share acknowledgement. Ordinary speed is now
-- part of the native inspected wand card; no held-wand speed overlay is drawn.
function Preview.draw_held(gui, entity, opts)
    opts=opts or {}
    if opts.hidden or not entity or entity==0 or not EntityGetIsAlive(entity)then return nil end
    local now,until_frame=opts.now,opts.shared_until
    if type(now)~="number"or now~=now or type(until_frame)~="number"or until_frame~=until_frame
        or until_frame==math.huge or now>=until_frame then return nil end
    local text="Shared"
    local tw,th=GuiGetTextDimensions(gui,text)
    local w,h=opts.w or 427,opts.h or 240
    if tw>w-16 or h<24 then return nil end
    local box={x=8,y=h-16,w=tw,h=th}
    GuiIdPushString(gui,"ew_ping_inventory_shared")
    GuiZSetForNextWidget(gui,-1010);GuiColorSetForNextWidget(gui,.72,.73,.68,.85)
    GuiText(gui,box.x,box.y,text)
    GuiIdPop(gui)
    return box
end
return Preview
