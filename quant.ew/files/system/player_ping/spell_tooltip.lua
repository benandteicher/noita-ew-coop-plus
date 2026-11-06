-- Geometry informed by noita-tooltip-example, copyright (c) 2026 Graham Forest.
-- MIT License
-- Permission is hereby granted, free of charge, to any person obtaining a copy
-- of this software and associated documentation files (the "Software"), to deal
-- in the Software without restriction, including without limitation the rights
-- to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
-- copies of the Software, and to permit persons to whom the Software is
-- furnished to do so, subject to the following conditions:
-- The above copyright notice and this permission notice shall be included in all
-- copies or substantial portions of the Software.
-- THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
-- IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
-- FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
-- AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
-- LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
-- OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
-- SOFTWARE.
-- GUI only: no native addresses/hooks, inventory entities or action evaluation.
local Artwork=dofile_once('mods/quant.ew/files/system/player_ping/spell_artwork.lua')
local M,metrics={},{}
local bg="data/ui_gfx/decorations/9piece0_gray.png"
local function clamp(n,a,b)return math.max(a,math.min(b,n))end
local function actual(gui)
    local _,_,_,x,y,w,h,dx,dy,dw,dh=GuiGetPreviousWidgetInfo(gui)
    if type(dw)=="number" and dw>0 and type(dh)=="number" and dh>0 then x,y,w,h=dx,dy,dw,dh end
    if type(x)=="number" and type(y)=="number" and type(w)=="number" and type(h)=="number"and w>0 and h>0 then return{x=x,y=y,w=w,h=h}end
end
local function fit(gui,s,width)
    s=tostring(s or "")
    if GuiGetTextDimensions(gui,s)<=width then return s end
    local out=""
    for c in s:gmatch("[%z\1-\127\194-\244][\128-\191]*")do
        if GuiGetTextDimensions(gui,out..c.."...")>width then break end;out=out..c
    end
    return out.."..."
end
local function wrap(gui,s,width,max_lines)
    local lines,line={},""
    for word in tostring(s or ""):gmatch("%S+")do
        local next_line=line=="" and word or line.." "..word
        if line~="" and GuiGetTextDimensions(gui,next_line)>width then
            lines[#lines+1]=fit(gui,line,width);line=word
            if #lines>=max_lines then lines[#lines]=fit(gui,lines[#lines].."...",width);return lines end
        else line=next_line end
    end
    if line~="" and #lines<max_lines then lines[#lines+1]=fit(gui,line,width)end
    return lines
end
function M.measure(gui,info,opts)
    opts=opts or {}
    if type(info)~="table"then return end
    GuiIdPushString(gui,"ew_spell_frame_measure")
    if not metrics[gui]then
        GuiOptionsAddForNextWidget(gui,GUI_OPTION.Layout_NoLayouting)
        GuiImageNinePiece(gui,1,5,5,20,20,0,bg)
        local b=actual(gui)
        if b then metrics[gui]={x=b.x-5,y=b.y-5,w=b.w-20,h=b.h-20}end
    end
    GuiIdPop(gui)
    local f=metrics[gui];if not f then return end
    local max_w,max_h=opts.max_width or 240,opts.max_height or 232
    local available=max_w-10-f.w
    local title_w=GuiGetTextDimensions(gui,tostring(info.name or ""))
    local description_w=GuiGetTextDimensions(gui,tostring(info.description or ""))
    local artwork=info.id and Artwork.measure(gui,info)or nil
    local icon_w,icon_h=artwork and artwork.width or info.icon_w or 16,artwork and artwork.height or info.icon_h or 16
    local icon_width=0
    for _,row in ipairs(info.rows or {})do
        local iw=0
        if row.icon then iw=GuiGetImageDimensions(gui,row.icon)end
        icon_width=math.max(icon_width,iw>0 and iw+3 or 0)
    end
    -- Prose is a preference, not a cap on the header or meaningful stat columns.
    local minimum=math.max(100,title_w+icon_w+8)
    for _,row in ipairs(info.rows or {})do
        minimum=math.max(minimum,GuiGetTextDimensions(gui,tostring(row.label or ""))+
            GuiGetTextDimensions(gui,tostring(row.value or ""))+8+icon_width)
    end
    local _,line_h=GuiGetTextDimensions(gui,"Ag")
    local step=math.max(8,line_h)
    for _,row in ipairs(info.rows or {})do
        if row.icon then local _,ih=GuiGetImageDimensions(gui,row.icon);step=math.max(step,ih)end
    end
    if available<100 or max_h<step*5+10+f.h then return end
    local header_h=math.max(icon_h,line_h)
    local function layout(content,policy)
        local title=fit(gui,info.name,content-icon_w-8)
        local desc=wrap(gui,info.description,content,3)
        local note=wrap(gui,info.note,content,2)
        local row_y=header_h+4+#desc*step+(#desc>0 and 4 or 0)
        local y,rows=row_y,{}
        local function measured_row(label,value,icon)
            local label_w=GuiGetTextDimensions(gui,tostring(label or ""))
            value=fit(gui,value,math.max(content*.48,content-icon_width-8-label_w))
            local vw=GuiGetTextDimensions(gui,value)
            local iw,ih=0,0
            if icon then iw,ih=GuiGetImageDimensions(gui,icon)end
            return{label=fit(gui,label,content-vw-8-icon_width),value=value,vw=vw,
                icon=icon,iw=iw,ih=ih,label_x=icon_width}
        end
        local reserved=#note*step+(#note>0 and 3 or 0)+8+f.h
        local previous_group,fields=nil,0
        for _,row in ipairs(info.rows or {})do
            local gap=previous_group and row.group and previous_group~=row.group and step or 0
            if y+gap+step+reserved>max_h then
                if #rows>0 then
                    rows[#rows]=measured_row("More fields","Not shown")
                    rows[#rows].overflow=true;fields=fields-1
                end
                break
            end
            local r=measured_row(row.label,row.value,row.icon);r.gap=gap
            rows[#rows+1]=r;fields=fields+1;y=y+gap+step;previous_group=row.group
        end
        local h=y+(#note>0 and 3+#note*step or 0)+8+f.h
        if h>max_h then return end
        return{width=content+10+f.w,height=h,frame=f,title=title,description=desc,note=note,rows=rows,
            row_y=row_y,step=step,content=content,info=info,icon_w=icon_w,icon_h=icon_h,artwork=artwork,
            header_height=header_h,title_y=math.floor((header_h-line_h)/2+.5),icon_y=math.floor((header_h-icon_h)/2+.5),
            description_y=header_h+4,field_count=fields,omitted_fields=#(info.rows or{})-fields,width_policy=policy}
    end
    local preferred=math.min(available,math.max(minimum,math.min(140,description_w)))
    local plan=layout(preferred,"preferred")
    -- At most two wider measurements, only when the narrow card loses fields
    -- or cannot fit. First retain R13's prose width, then use viewport space if
    -- necessary. Equal information keeps the narrower result; no width search.
    local wider=math.min(available,math.max(minimum,math.min(180,description_w)))
    for _,candidate in ipairs({{wider,"prose_fallback"},{available,"viewport_fallback"}})do
        if plan and plan.omitted_fields==0 then break end
        if candidate[1]>preferred then
            local next_plan=layout(candidate[1],candidate[2])
            if next_plan and(not plan or next_plan.field_count>plan.field_count)then plan=next_plan end
            preferred=candidate[1]
        end
    end
    return plan
end
local function area(a,b)
    if not b then return 0 end
    return math.max(0,math.min(a.x+a.w,b.x+b.w)-math.max(a.x,b.x))*math.max(0,math.min(a.y+a.h,b.y+b.h)-math.max(a.y,b.y))
end
local function place(slot,plan,w,h,parent)
    local candidates={
        {x=(parent or slot).x+(parent or slot).w+6,y=slot.y},
        {x=(parent or slot).x-plan.width-6,y=slot.y},
        {x=slot.x,y=slot.y-plan.height-6},{x=slot.x,y=slot.y+slot.h+6}}
    local best,score
    for i,c in ipairs(candidates)do
        c.x=math.floor(clamp(c.x,4,w-plan.width-4)+.5);c.y=math.floor(clamp(c.y,4,h-plan.height-4)+.5)
        c.w,c.h=plan.width,plan.height
        local rank=area(c,slot)*1000+area(c,parent)+i*.01
        if not score or rank<score then best,score=c,rank end
    end
    return best
end
function M.draw(gui,slot,info,opts)
    opts=opts or {}
    local w,h=opts.w or 427,opts.h or 240
    local plan=opts.plan
    if not plan or plan.width>w-8 or plan.height>h-8 then plan=M.measure(gui,info,{max_width=w-8,max_height=h-8})end
    if not plan or not slot then return end
    -- Preserve the previously displayed child origin while its spell remains
    -- selected. Only current content size and viewport constraints can move it.
    local b
    if opts.box and type(opts.box.x)=="number"and type(opts.box.y)=="number"then
        b={x=clamp(opts.box.x,4,w-plan.width-4),y=clamp(opts.box.y,4,h-plan.height-4),w=plan.width,h=plan.height}
    else b=place(slot,plan,w,h,opts.parent)end
    local ox,oy=b.x-plan.frame.x,b.y-plan.frame.y
    GuiIdPushString(gui,"ew_ping_spell_tooltip")
    local function text(x,y,s,shade)
        GuiZSetForNextWidget(gui,-1042);GuiColorSetForNextWidget(gui,shade or .82,shade or .82,shade or .82,1)
        GuiText(gui,ox+5+x,oy+5+y,s)
    end
    text(0,plan.title_y,plan.title,1)
    GuiZSetForNextWidget(gui,-1042)
    if plan.artwork then
        Artwork.draw(gui,ox+5+plan.content-plan.icon_w,oy+5+plan.icon_y,info,plan.artwork)
    elseif info.icon_animation then
        -- Native RectAnimation sprites retain their authored origin offsets.
        -- Compensate only this locally resolved metadata, never packet paths.
        GuiImage(gui,1,ox+5+plan.content-(plan.icon_w or 16)+(info.icon_offset_x or 0),oy+5+plan.icon_y+(info.icon_offset_y or 0),info.icon,1,1,1,0,
            GUI_RECT_ANIMATION_PLAYBACK.Loop,info.icon_animation)
    else GuiImage(gui,1,ox+5+plan.content-(plan.icon_w or 16),oy+5+plan.icon_y,info.icon,1,1,1)end
    local y=plan.description_y
    for _,line in ipairs(plan.description)do text(0,y,line);y=y+plan.step end
    y=plan.row_y
    for i,row in ipairs(plan.rows)do
        y=y+(row.gap or 0)
        if row.icon then
            GuiZSetForNextWidget(gui,-1042)
            GuiImage(gui,10+i,ox+5,oy+5+y+(plan.step-row.ih)/2,row.icon,1,1,1)
        end
        text(row.label_x or 0,y,row.label);local vw=row.vw or GuiGetTextDimensions(gui,row.value)
        text(plan.content-vw,y,row.value,1);y=y+plan.step
    end
    if #plan.note>0 then y=y+3;for _,line in ipairs(plan.note)do text(0,y,line,.60);y=y+plan.step end end
    GuiZSetForNextWidget(gui,-1040)
    GuiImageNinePiece(gui,2,ox,oy,plan.width-plan.frame.w,plan.height-plan.frame.h,1,bg)
    local drawn=actual(gui)
    GuiIdPop(gui)
    return drawn,plan
end
return M
