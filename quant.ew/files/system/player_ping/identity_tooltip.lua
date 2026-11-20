-- Small identification-only cards. No entities, inventory, input or networking.
-- Native frame style/measurement is shared visually with spell_tooltip.lua.
local M,metrics={},{ }
local background="data/ui_gfx/decorations/9piece0_gray.png"
local function finite(n)return type(n)=="number"and n==n and math.abs(n)<math.huge end
local function round(n)return math.floor(n+.5)end
local function clamp(n,a,b)return math.max(a,math.min(b,n))end
local function actual(gui)
    local _,_,_,x,y,w,h,dx,dy,dw,dh=GuiGetPreviousWidgetInfo(gui)
    if finite(dw)and dw>0 and finite(dh)and dh>0 then x,y,w,h=dx,dy,dw,dh end
    if finite(x)and finite(y)and finite(w)and w>0 and finite(h)and h>0 then return{x=x,y=y,w=w,h=h}end
end
local function characters(s)
    local out={}
    for c in s:gmatch("[%z\1-\127\194-\244][\128-\191]*")do out[#out+1]=c end
    return out
end
local function wrap(gui,text,width)
    -- Break overlong single words at whole UTF-8 characters; normal names wrap
    -- at spaces. Three lines are enough for a short identification label.
    local lines,line,truncated={},"",false
    local function push()
        lines[#lines+1]=line;line=""
        return #lines<3
    end
    for word in text:gmatch("%S+")do
        local next_line=line==""and word or line.." "..word
        if GuiGetTextDimensions(gui,next_line)<=width then line=next_line
        else
            if line~=""and not push()then truncated=true;break end
            for _,c in ipairs(characters(word))do
                if GuiGetTextDimensions(gui,line..c)>width then
                    if not push()then truncated=true;break end
                end
                line=line..c
            end
            if #lines>=3 then break end
        end
    end
    if line~=""and #lines<3 then lines[#lines+1]=line end
    if truncated then
        local dots="..."
        while #dots>0 and GuiGetTextDimensions(gui,dots)>width do dots=dots:sub(1,-2)end
        local chars=characters(lines[3]);local s=table.concat(chars)
        while #chars>0 and GuiGetTextDimensions(gui,s..dots)>width do
            chars[#chars]=nil;s=table.concat(chars)
        end
        lines[3]=s..dots
    end
    return lines
end
function M.measure(gui,info,opts)
    opts=opts or{}
    if type(info)~="table"or type(info.name)~="string"or info.name==""
        or type(info.icon)~="string"or info.icon==""then return nil end
    GuiIdPushString(gui,"ew_identity_frame_measure")
    if not metrics[gui]then
        GuiOptionsAddForNextWidget(gui,GUI_OPTION.Layout_NoLayouting)
        GuiImageNinePiece(gui,1,5,5,20,20,0,background)
        local b=actual(gui)
        if b then metrics[gui]={x=b.x-5,y=b.y-5,w=b.w-20,h=b.h-20}end
    end
    GuiIdPop(gui)
    local f=metrics[gui];if not f then return nil end
    local limit_w,limit_h=opts.max_width or 240,opts.max_height or 232
    local available=math.min(160,limit_w-10-f.w)
    if available<8 then return nil end
    local iw,ih=info.icon_w,info.icon_h
    if not finite(iw)or not finite(ih)then iw,ih=GuiGetImageDimensions(gui,info.icon)end
    if not finite(iw)or not finite(ih)or iw<=0 or ih<=0 or iw>available or ih>64 then return nil end
    local lines=wrap(gui,info.name,available)
    if #lines==0 then return nil end
    local _,line_h=GuiGetTextDimensions(gui,"Ag");line_h=math.max(1,math.ceil(line_h))
    local content=iw
    for _,line in ipairs(lines)do content=math.max(content,GuiGetTextDimensions(gui,line))end
    local caption=info.category=="enemy"and"Spotted here"or nil
    if caption then content=math.max(content,GuiGetTextDimensions(gui,caption))end
    -- Caption is fixed and small. Refuse an impossibly narrow viewport instead
    -- of drawing text beyond its measured frame or shrinking native typography.
    if content>available then return nil end
    content=math.ceil(content)
    local icon_y=#lines*line_h+4
    local caption_y=caption and icon_y+ih+4 or nil
    local content_h=caption and caption_y+line_h or icon_y+ih
    local w,h=content+10+f.w,content_h+10+f.h
    if w>limit_w or h>limit_h then return nil end
    return{width=w,height=h,frame=f,padding=5,title_lines=lines,line_h=line_h,
        content=content,content_height=content_h,icon_w=iw,icon_h=ih,icon_y=icon_y,
        caption=caption,caption_y=caption_y}
end
function M.draw(gui,marker,info,opts)
    opts=opts or{}
    local w,h=opts.w or 427,opts.h or 240
    local plan=opts.plan
    if not plan or plan.width>w-8 or plan.height>h-8 then
        plan=M.measure(gui,info,{max_width=w-8,max_height=h-8})
    end
    if not plan or not marker then return nil end
    local x,y
    if opts.box and finite(opts.box.x)and finite(opts.box.y)then x,y=opts.box.x,opts.box.y
    else
        x,y=marker.x+(marker.w or 7)+8,marker.y-8
        if x+plan.width>w-4 then x=marker.x-plan.width-8 end
    end
    local b={x=clamp(round(x),4,math.floor(w-plan.width-4)),y=clamp(round(y),4,math.floor(h-plan.height-4)),w=plan.width,h=plan.height}
    local ox,oy=b.x-plan.frame.x,b.y-plan.frame.y
    local top=oy+plan.padding
    GuiIdPushString(gui,"ew_ping_identity_tooltip")
    local function centered(text,y,shade)
        local tw=GuiGetTextDimensions(gui,text)
        GuiZSetForNextWidget(gui,-1042);GuiColorSetForNextWidget(gui,shade,shade,shade,1)
        GuiText(gui,round(b.x+(b.w-tw)/2),y,text)
    end
    for j,line in ipairs(plan.title_lines)do centered(line,top+(j-1)*plan.line_h,1)end
    local ix,iy=round(b.x+(b.w-plan.icon_w)/2),top+plan.icon_y
    GuiZSetForNextWidget(gui,-1042)
    if info.icon_animation then
        GuiImage(gui,1,ix+(info.icon_offset_x or 0),iy+(info.icon_offset_y or 0),info.icon,1,1,1,0,
            GUI_RECT_ANIMATION_PLAYBACK.Loop,info.icon_animation)
    else GuiImage(gui,1,ix,iy,info.icon,1,1,1)end
    if plan.caption then centered(plan.caption,top+plan.caption_y,.62)end
    GuiZSetForNextWidget(gui,-1040)
    GuiImageNinePiece(gui,2,ox,oy,plan.width-plan.frame.w,plan.height-plan.frame.h,1,background)
    local drawn=actual(gui)
    GuiIdPop(gui)
    return drawn,plan
end
return M
