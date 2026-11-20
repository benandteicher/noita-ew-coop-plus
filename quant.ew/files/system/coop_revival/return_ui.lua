-- Local confirmation/presentation only. The host owns eligibility/reservations.
local M={}
local labels=dofile_once('mods/quant.ew/files/system/spectate/presentation.lua')
local gui=GuiCreate()
local status,view,expires,armed,requested,pressed_at,guard,last_method,queued,swallow
local function smooth(v)v=math.max(0,math.min(1,v));return v*v*(3-2*v)end
local function blocked()
    return ctx.run_ended or ctx.is_paused or ctx.is_texting or ctx.is_inventory_open or ctx.is_wand_pickup or GameIsInventoryOpen()
end
local function buttons()
    local key=tonumber(ModSettingGet('quant.ew.rebind_mountain_return'))or 44
    local pad=tonumber(ModSettingGet('quant.ew.return_gamepad_button'))or 23
    local use_pad=not ModSettingGet('quant.ew.no_gamepad')and GameGetIsGamepadConnected()
    local kd=InputIsKeyDown and InputIsKeyDown(key)or false
    local kj=InputIsKeyJustDown(key)
    local pd=use_pad and InputIsJoystickButtonDown and InputIsJoystickButtonDown(0,pad)or false
    local pj=use_pad and InputIsJoystickButtonJustDown(0,pad)or false
    if kj then last_method='key'elseif pj then last_method='pad'end
    return kd or pd,kj or pj,key,pad,use_pad
end
function M.binding()
    local _,_,key,pad,use_pad=buttons()
    if use_pad and last_method~='key'then
        local names={[23]='A / Cross',[24]='B / Circle',[25]='X / Square',[26]='Y / Triangle',[19]='Left shoulder',[20]='Right shoulder'}
        return '['..(names[pad]or('Pad '..pad))..']'
    end
    return key==44 and'[Space]'or labels.binding('mountain_return',44)
end
function M.receive(value,reason,now)
    now=now or GameGetFrameNum()
    if value then
        if not status or status.id~=value.id then armed,requested,pressed_at=false,false,nil end
        status=value;expires=now+90
        if value.pending then requested=true;pressed_at=pressed_at or now end
    else
        status=nil;armed=false;requested=false
        if view then view.left=view.left or now;view.reason=reason end
    end
end
-- Only a component tagged by this feature may be restored. The tag survives a
-- cold module/Continue; untagged native/mod restrictions are never changed.
local guard_tag='ew_coop_return_controls_owned'
local function release_owned(entity)
    if not entity or not EntityGetIsAlive(entity)then return false end
    local restored=false
    for _,c in ipairs(EntityGetComponentIncludingDisabled(entity,'ControlsComponent')or{})do
        if ComponentHasTag(c,guard_tag)then
            ComponentRemoveTag(c,guard_tag)
            if ComponentGetValue2(c,'enabled')==false then ComponentSetValue2(c,'enabled',true)end
            restored=true
        end
    end
    return restored
end
local function release_guard()
    if guard then release_owned(guard.entity)end
    guard=nil
end
function M.before_snapshot(entity)
    -- Death and polymorph serializers must see the original control state.
    release_owned(entity)
    if guard and guard.entity==entity then guard=nil end
end
function M.restore_owned(entity)
    if release_owned(entity)then swallow=true end
end
local function acquire_guard(entity)
    local c=EntityGetFirstComponentIncludingDisabled(entity,'ControlsComponent')
    if guard and guard.component~=c then release_guard()end
    if c and ComponentGetValue2(c,'enabled')==true then
        ComponentAddTag(c,guard_tag)
        ComponentSetValue2(c,'enabled',false)
        guard={entity=entity,component=c}
    end
    if guard then
        for _,k in ipairs({'Jump','Fly','Up'})do ComponentSetValue2(guard.component,'mButtonDown'..k,false)end
    end
end
function M.suppress_restored(entity)
    release_guard();swallow=true
    local down=buttons()
    if down and not blocked()then acquire_guard(entity)end
    status,view,armed,requested=nil,nil,false,false
end
function M.input(focused)
    local down,just=buttons()
    ctx.coop_return_input_block=false
    local entity=ctx.my_player.entity
    -- Also recover owned state after a cold module, without relying on a Lua ref.
    if not guard then M.restore_owned(entity)end
    local dead=GameHasFlagRun('ew_flag_notplayer_active')
    if guard and(guard.entity~=entity or dead or blocked()or not focused or not down or ctx.run_ended)then release_guard()end
    if swallow then
        if not down or ctx.run_ended then swallow=false;release_guard()
        else
            ctx.coop_return_input_block=true
            if not dead and focused and not blocked()then acquire_guard(entity)end
        end
    end
    if not GameHasFlagRun('ew_flag_notplayer_active')or ctx.run_ended then status=nil;view=nil;return end
    local now=GameGetFrameNum()
    local ng=status and(status.ng or(status.room and status.room.ng))
    if ng and ng~=(tonumber(SessionNumbersGetValue('NEW_GAME_PLUS_COUNT'))or 0)then M.receive(nil,'Mountain return is no longer available.',now)end
    if status and now>(expires or 0)then M.receive(nil,'Mountain return is no longer available.',now)end
    if blocked()or not focused then armed=false;view=nil;ctx.coop_return_bounds=nil;return end
    if not status or status.pending or requested then return end
    if not down and not just then armed=true end
    if armed and just then
        armed=false;requested=true;pressed_at=now;view=nil;ctx.coop_return_input_block=true
        queued={death=status.death,id=status.id,run=status.run}
    end
    if requested and down then ctx.coop_return_input_block=true end
end
function M.confirm(focused,send)
    -- Chat/menu handlers run after the early spectator-input guard. Recheck in
    -- post-update before sending so opening a blocking UI cannot leak a request.
    local q=queued;queued=nil
    if not q then return end
    if focused and not blocked()and status and status.id==q.id and not status.pending
        and GameHasFlagRun('ew_flag_notplayer_active')then send(q.death,q.id,q.run)
    else requested=false;armed=false;pressed_at=nil end
end
local function fit(g,text,width)
    text=tostring(text or'Teammate'):gsub('[%c]',' ')
    if GuiGetTextDimensions(g,text)<=width then return text end
    local out=''
    for c in text:gmatch('[%z\1-\127\194-\244][\128-\191]*')do
        if GuiGetTextDimensions(g,out..c..'...')>width then break end;out=out..c
    end
    return out..'...'
end
local function text(g,x,y,label,color,a)
    GuiColorSetForNextWidget(g,.025,.022,.016,a*.9);GuiText(g,x+1,y+1,label)
    GuiColorSetForNextWidget(g,color[1],color[2],color[3],a);GuiText(g,x,y,label)
end
function M.draw(g,v,o)
    local _,th=GuiGetTextDimensions(g,'Ag');th=math.max(8,th)
    local y=(o.spectator_top or o.h-44)-12-(th*2+4)
    if y<60 then return end
    local primary=v.pending and(o.now-(v.pressed or o.now)>=45 and'Waiting for a safe place…'or'Returning…')or v.reason
    local binding=not primary and(o.binding or M.binding())or''
    primary=primary or'Revive in the Holy Mountain'
    local lead=binding~=''and(binding..' ')or''
    local width=GuiGetTextDimensions(g,lead..primary);local x=math.floor((o.w-width)/2)
    local a=o.alpha or 1
    if lead~=''then text(g,x,y,lead,{.91,.72,.36},a)end
    text(g,x+GuiGetTextDimensions(g,lead),y,primary,{.9,.86,.71},a)
    local secondary
    if not v.reason then
        local suffix=tostring(v.percent or 30)..'% health'
        local space=GuiGetTextDimensions(g,' ')
        local gap=space*2+1
        local name=fit(g,v.name,math.min(240,o.w-24)-GuiGetTextDimensions(g,'With '..suffix)-gap)
        local left='With '..name
        local lw=GuiGetTextDimensions(g,left)
        local sw=lw+gap+GuiGetTextDimensions(g,suffix)
        local sx=math.floor((o.w-sw)/2)
        text(g,sx,y+th+4,left,{.65,.64,.55},a)
        -- The installed font renders U+00B7 as '?'. Draw its one-pixel middle
        -- dot at the text midline instead; all surrounding text uses native font.
        GuiColorSetForNextWidget(g,.025,.022,.016,a*.9)
        GuiImage(g,9100,sx+lw+space+1,math.floor(y+th+4+th/2)+1,'data/ui_gfx/empty_white.png',a,.5,.5)
        GuiColorSetForNextWidget(g,.65,.64,.55,a)
        GuiImage(g,9101,sx+lw+space,math.floor(y+th+4+th/2),'data/ui_gfx/empty_white.png',a,.5,.5)
        text(g,sx+lw+gap,y+th+4,suffix,{.65,.64,.55},a)
        secondary=sw
    end
    width=math.max(width,secondary or 0)
    return{x=(o.w-width)/2-1,y=y-1,w=width+2,h=th*2+6}
end
function M.render()
    GuiStartFrame(gui);ctx.coop_return_bounds=nil
    if blocked()or not GameHasFlagRun('ew_flag_notplayer_active')then view=nil;return end
    local now=GameGetFrameNum()
    if status and now>(expires or 0)then M.receive(nil,'Mountain return is no longer available.',now)end
    if status and not(view and(view.left or view.reason))then
        if not view or view.id~=status.id or view.left then view={id=status.id,entered=now}end
        view.name,view.percent=status.name,status.percent
        view.pending=status.pending or requested;view.pressed=pressed_at
    end
    if not view then return end
    local alpha=smooth((now-view.entered)/18)*(.8+.15*math.sin((now-view.entered)*math.pi/90))
    if view.left then
        alpha=alpha*(1-smooth((now-view.left)/12))
        if now-view.left>=12 then
            if view.reason then view={reason=view.reason,entered=now,notice_until=now+90};alpha=0 else view=nil;return end
        end
    end
    if view.notice_until and now>view.notice_until then alpha=alpha*(1-smooth((now-view.notice_until)/12));if now>view.notice_until+12 then view=nil;return end end
    local w,h=GuiGetScreenDimensions(gui);GuiZSet(gui,12)
    ctx.coop_return_bounds=M.draw(gui,view,{w=w,h=h,now=now,alpha=alpha,spectator_top=ctx.coop_spectator_top})
end
function M.clear()
    release_guard();release_owned(ctx.my_player and ctx.my_player.entity);swallow=false;status,view,armed,requested,queued=nil,nil,false,false,nil
    ctx.coop_return_input_block=false;ctx.coop_return_bounds=nil;GuiStartFrame(gui)
end
return M
