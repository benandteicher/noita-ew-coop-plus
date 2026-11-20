-- Ping+ is cosmetic: host-scoped run identity, sender revisions and bounded data.
local model=dofile_once('mods/quant.ew/files/system/player_ping/model.lua')
local renderer=dofile_once('mods/quant.ew/files/system/player_ping/renderer.lua')
local snapshot=dofile_once('mods/quant.ew/files/system/player_ping/wand_snapshot.lua')
local identification=dofile_once('mods/quant.ew/files/system/player_ping/identification.lua')
local world_target=dofile_once('mods/quant.ew/files/system/player_ping/world_target.lua')
local preview=dofile_once('mods/quant.ew/files/system/player_ping/wand_preview.lua')
local teammates=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
local sound=dofile_once('mods/quant.ew/files/system/player_ping/sound.lua')
local held_share=dofile_once('mods/quant.ew/files/system/player_ping/held_share.lua')
local inventory_hover=dofile_once('mods/quant.ew/files/system/player_ping/inventory_hover.lua')
local world_input=dofile_once('mods/quant.ew/files/system/player_ping/world_input.lua')
local gestures=dofile_once('mods/quant.ew/files/system/player_ping/world_gestures.lua')
local gesture_state=gestures.new()
local input_clock=world_input.new()
local rpc=net.new_rpc_namespace()
local module,gui,state={},GuiCreate(),model.new()
local run,run_serial,epoch,next_id,last_frame,last_body,last_ng,last_tap,last_id,last_cue,last_request
-- Recent logical targets outlive local presentation expiry. Each deliberate
-- re-ping gets a new event ID while replacing its target on every recipient.
local targets={}
local queued,inventory_queued,shared_until
local held_target,had_held,selected_core,last_input_device
local target_hint
local keynames
local function finite(n)return model.finite(n)end
local function counter(key)
    local n=tonumber(ModSettingGet('quant.ew.'..key))or 0
    if not finite(n)then n=0 end
    n=math.floor(math.max(0,math.min(2147483645,n)))+1
    ModSettingSet('quant.ew.'..key,n);return n
end
local function ng()return tonumber(SessionNumbersGetValue('NEW_GAME_PLUS_COUNT'))or 0 end
local function migrate()
    if ModSettingGet('quant.ew.pingplus_lifecycle_v2')then return end
    local old=tonumber(ModSettingGet('quant.ew.ping_life'))
    if not old or old==6 or old==20 then
        ModSettingSet('quant.ew.ping_life','30.0')
        if ModSettingSetNextValue then ModSettingSetNextValue('quant.ew.ping_life','30.0',false)end
    end
    ModSettingSet('quant.ew.pingplus_lifecycle_v2',true)
end
local function lifetime(kind)
    if kind~='danger'then return nil end
    local life=tonumber(ModSettingGet('quant.ew.ping_life'))or 30
    if not finite(life)then life=30 end
    return math.floor(math.max(2,math.min(120,life))*60)
end
local function connected(peer)local p=ctx.players[peer];return p and not p.dc end
local function menu_hidden()
    return ctx.run_ended or ctx.is_paused or ctx.is_texting or ctx.is_wand_pickup
end
local function inventory()return ctx.is_inventory_open or GameIsInventoryOpen()end
local focused=function()return false end
do
    local ok,fn=pcall(function()
        local ffi=require('ffi');ffi.cdef('void *SDL_GetKeyboardFocus(void);')
        local lib=ffi.load(ffi.os=='Windows'and'SDL2'or'libSDL2-2.0.so.0')
        return function()return lib.SDL_GetKeyboardFocus()~=nil end
    end)
    if ok then focused=fn end
end
local function clear_view(keep_held)
    GuiStartFrame(gui) -- Explicit suspension also clears the last drawn widgets.
    preview.clear();renderer.clear();sound.clear();last_tap,last_id,queued,selected_core,target_hint=nil,nil,nil,nil,nil;gestures.clear(gesture_state)
    if not keep_held then preview.clear_held();inventory_hover.close();inventory_queued,shared_until=nil,nil end
end
local function reset(value,serial,now)
    run,run_serial=value,serial;model.reset(state,run,now);targets={};held_target,had_held=nil,nil;last_cue,last_request=nil,nil;clear_view()
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.ping_run(serial,generation)
    if ctx.rpc_peer_id~=ctx.host_id or ctx.run_ended or generation~=ng()or not finite(serial)
        or serial<1 or serial%1~=0 or serial>2147483647 or(run_serial and serial<run_serial)then return end
    local token=ctx.host_id..':'..serial
    if token~=run then reset(token,serial,GameGetFrameNum())end
end
rpc.opts_reliable()
function rpc.request_run()
    if ctx.is_host and connected(ctx.rpc_peer_id)and run and not ctx.run_ended then rpc.ping_run(run_serial,ng())end
end
local function begin_run()
    local serial=counter('ping_run_serial');rpc.ping_run(serial,ng())
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.ping(packet)
    local peer=ctx.rpc_peer_id
    if ctx.run_ended or not run or not connected(peer)or type(packet)~='table'or getmetatable(packet)then return end
    local clean={v=packet.v,run=packet.run,epoch=packet.epoch,id=packet.id,seq=packet.seq,
        kind=packet.kind,x=packet.x,y=packet.y,target=packet.target,source=packet.source,life=packet.life}
    if packet.wand~=nil then
        if packet.kind~='wand'then return end
        clean.wand=snapshot.validate(packet.wand);if not clean.wand then return end
    elseif packet.kind=='wand'then return end
    local identified=packet.kind=='spell'or packet.kind=='item'or packet.kind=='enemy'
    if packet.ident~=nil or identified then
        if packet.wand~=nil or(packet.source and packet.source~='world')then return end
        clean.ident=identification.validate(packet.ident)
        if clean.ident and not identification.resolve(clean.ident)then clean.ident=nil end
        if not clean.ident or(clean.kind~='danger'and clean.kind~=clean.ident.category)then
            clean.ident=nil
            if identified then clean.kind,clean.life='location',nil end
        end
    end
    local now=GameGetFrameNum()
    local record,event=model.accept(state,peer,clean,now)
    if record and event=='new'and peer~=ctx.my_id and not menu_hidden()and not inventory()and focused()then
        if ModSettingGet('quant.ew.ping_sound')~=false and now-(last_cue or -100)>=30 then
            last_cue=now;sound.play(true,record.kind)
        end
    end
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.held_clear(token,session,through)
    if not run or ctx.run_ended or token~=run or not connected(ctx.rpc_peer_id)then return end
    model.retire_held(state,ctx.rpc_peer_id,session,through)
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.cancel(token,session,id)
    if ctx.run_ended or not run or token~=run or not connected(ctx.rpc_peer_id)then return end
    model.cancel(state,ctx.rpc_peer_id,session,id)
end
local function publish_owned(item,body)
    if not run or not epoch or ctx.run_ended then return false end
    if ctx.my_player.entity~=body then return false end
    local data=held_share.capture_owned(ctx.my_player,item)
    local x,y,kind=held_share.anchor(ctx.my_player)
    if not data or kind~='alive'then return false end
    next_id=next_id+1;held_target=held_target or next_id
    rpc.ping({v=1,run=run,epoch=epoch,id=next_id,seq=1,target=held_target,source='held',kind='wand',x=x,y=y,wand=data})
    had_held=true;last_tap,last_id=nil,nil;gestures.clear(gesture_state)
    return true
end
local function inventory_context(now)
    local body=ctx.my_player.entity
    local inv=EntityGetFirstComponentIncludingDisabled(body,'Inventory2Component')
    local native=EntityGetFirstComponentIncludingDisabled(body,'InventoryGuiComponent')
    local mx,my,buttons=inventory_hover.mouse()
    local _,_,alive=held_share.anchor(ctx.my_player)
    return {run=run,body=body,inventory=inv,frame=now,inventory_open=inventory(),focused=focused(),
        blocked=menu_hidden()or ctx.coop_return_input_block or alive~='alive'
            or GameHasFlagRun('ew_flag_notplayer_active')or not native or not ComponentGetIsEnabled(native)
            or not ComponentGetValue2(native,'mActive')
            or(ctx.spectating_over_peer_id and ctx.spectating_over_peer_id~=ctx.my_id),
        mx=mx,my=my,buttons=buttons}
end
local function visible_records()
    local list={}
    for _,r in ipairs(model.list(state))do
        if r.source=='held'then
            local x,y,kind=held_share.anchor(ctx.players[r.sender])
            if kind=='alive'then
                local projected={};for k,v in pairs(r)do projected[k]=v end
                projected.x,projected.y=x,y;list[#list+1]=projected
            end
        else list[#list+1]=r end
    end
    return list
end
local function view()
    local w,h=GuiGetScreenDimensions(gui)
    local x,y,ww,wh=GameGetCameraBounds()
    if not finite(ww)or not finite(wh)or ww<=0 or wh<=0 then return end
    local cx,cy=GameGetCameraPos();local ox,oy=teammates.origin()
    return {w=w,h=h,camera_x=cx,camera_y=cy,world_w=ww,world_h=wh,origin_x=ox,origin_y=oy}
end
local function point(v,source)
    if source=='controller'then
        local dx,dy=InputGetJoystickAnalogStick(0,1)
        return world_input.point(source,v,nil,nil,dx,dy)
    end
    local mx,my=DEBUG_GetMouseWorld()
    return world_input.point(source,v,mx,my)
end
local function input_position(v,device,x,y)
    -- Public SDL mouse coordinates are window-relative and independent of the
    -- camera. This query does not install/acquire a native tooltip hook.
    if device=='mouse'or device=='keyboard'then
        local mx,my=inventory_hover.mouse()
        if finite(mx)and finite(my)then return{x=mx,y=my,device=device,space='window',w=v.w,h=v.h}end
    end
    if finite(x)and finite(y)then
        return{x=v.w/2+(x-v.camera_x)*v.w/v.world_w,
            y=v.h/2+(y-v.camera_y)*v.h/v.world_h,device=device,space='gui',w=v.w,h=v.h}
    end
end
local function emit(x,y,v,now,selected,input)
    if not run or not epoch or not finite(x)or not finite(y)then return end
    local entity,wx,wy,data,ident
    if not selected then
        local choice=world_target.target(x,y,v,target_hint and target_hint.entity)
        choice=choice and world_target.revalidate(choice,x,y,v)
        if choice then
            entity,wx,wy,ident=choice.entity,choice.x,choice.y,choice.ident
            -- Only activation captures the wand's contents. Hover uses geometry.
            if choice.kind=='wand'then data=snapshot.capture(entity)end
            if not data and not ident then entity,wx,wy=nil,nil,nil end
        end
    end
    local kind=data and 'wand'or ident and ident.category or 'location'
    local identity=entity and((data and'wand:'or'object:')..entity)
    local identities={};for _,t in ipairs(targets)do identities[t.target]=t.key end
    local d=gestures.classify(gesture_state,{now=now,x=x,y=y,kind=kind,identity=identity,
        selected=selected,input=input,own=ctx.my_id,records=model.list(state),identities=identities})
    if d.action=='blocked'then gestures.commit(gesture_state,d);return end
    if d.action=='cancel'or d.action=='hide'then
        if d.action=='hide'then model.hide(state,d.record.key)
        else rpc.cancel(run,epoch,d.record.id)end
        gestures.commit(gesture_state,d);preview.clear();target_hint=nil
        if selected_core==d.record.key then selected_core=nil end
        return
    end
    local packet
    if d.action=='upgrade'then
        local r=d.record
        packet={v=1,run=run,epoch=epoch,id=r.id,seq=r.seq+1,target=r.target,
            kind='danger',x=r.x,y=r.y,life=lifetime('danger'),ident=r.ident}
    else
        next_id=next_id+1
        local logical=d.record and d.record.target or next_id
        packet={v=1,run=run,epoch=epoch,id=next_id,seq=1,target=logical,
            kind=kind,x=(data or ident)and wx or x,y=(data or ident)and wy or y,wand=data,ident=ident,
            life=kind=='enemy'and 1800 or nil}
        for i=#targets,1,-1 do if targets[i].target==logical then table.remove(targets,i)end end
        targets[#targets+1]={key=identity,target=logical}
        if #targets>32 then table.remove(targets,1)end
    end
    rpc.ping(packet)
    local accepted=state.records[ctx.my_id..':'..epoch..':'..packet.id]
        or{id=packet.id,target=packet.target,epoch=epoch,key=ctx.my_id..':'..epoch..':'..packet.id}
    gestures.commit(gesture_state,d,accepted)
    last_id,last_tap=packet.id,now
end
function module.on_local_player_spawn()
    inventory_hover.close()
    migrate();epoch=counter('ping_session_serial');next_id=0
    last_frame,last_body,last_ng=GameGetFrameNum(),ctx.my_player.entity,ng()
    if ctx.is_host then begin_run()else reset(nil,nil,last_frame);rpc.request_run()end
end
function module.on_should_send_updates()
    if ctx.is_host and run then rpc.ping_run(run_serial,ng())end
end
local function action_allowed(entity,bind,keyboard,now)
    -- Native action timestamps do not identify their source binding. Independent
    -- firing/jumping/movement must not veto a middle-mouse ping. Only proven EW
    -- binding overlaps and the actual blocking UI/return state are excluded.
    if keyboard then
        if bind==(tonumber(ModSettingGet('quant.ew.text'))or 40)
            or bind==(tonumber(ModSettingGet('quant.ew.stoptext'))or 76)or bind==41 then return false end
        if GameHasFlagRun('ew_flag_notplayer_active')then
            for _,binding in ipairs({{'lspectate',54},{'rspectate',55},{'sspectate',56},{'mspectate',52}})do
                if bind==(tonumber(ModSettingGet('quant.ew.rebind_'..binding[1]))or binding[2])then return false end
            end
        end
    end
    return true
end
local function ping_binding()
    if last_input_device=='controller'then return '[Right stick]'end
    local bind=tonumber(ModSettingGet('quant.ew.rebind_ping'))or 42
    if bind==42 then return '[Middle mouse]'end
    if not keynames then
        keynames={}
        local ok,text=pcall(ModTextFileGetContent,'data/scripts/debug/keycodes.lua')
        if ok and type(text)=='string'then for k,n in text:gmatch('Key_([%w_]+)%s*=%s*(%d+)')do keynames[tonumber(n)]=k:gsub('_',' ')end end
    end
    local punctuation={COMMA=',',PERIOD='.',SLASH='/',APOSTROPHE="'",SEMICOLON=';',MINUS='-',EQUALS='='}
    local key=keynames[bind]or('Key '..bind)
    return '['..(punctuation[key]or key)..']'
end
local function draw(now)
    GuiStartFrame(gui)
    local v=view();if not v then clear_view();return end
    local hidden=menu_hidden()or not focused()
    if inventory()then
        preview.clear();sound.clear();last_tap,last_id=nil,nil
        if not hidden and not GameHasFlagRun('ew_flag_notplayer_active')
            and(not ctx.spectating_over_peer_id or ctx.spectating_over_peer_id==ctx.my_id)then
            preview.draw_held(gui,ctx.my_player.entity,{now=now,w=v.w,h=v.h,shared_until=shared_until})
        else preview.clear_held()end
        return
    end
    preview.clear_held()
    if hidden then clear_view();return end
    local entity=ctx.my_player.entity;local mx,my=DEBUG_GetMouseWorld()
    mx,my=v.w/2+(mx-v.camera_x)*v.w/v.world_w,v.h/2+(my-v.camera_y)*v.h/v.world_h
    local names={};for peer,p in pairs(ctx.players)do if not p.dc then names[peer]=peer==ctx.my_id and'You'or p.name end end
    local reserved={{x=0,y=0,w=v.w,h=27}}
    if ctx.coop_return_bounds then reserved[#reserved+1]=ctx.coop_return_bounds end
    GuiZSet(gui,8)
    local occluded=preview.blocks_world and preview.blocks_world(mx,my)
    local markers=renderer.draw(gui,visible_records(),{view=v,now=now,names=names,
        local_peer=ctx.my_id,removal_hint=ping_binding()..' Remove ping',hide_hint=ping_binding()..' Hide ping',
        selected_key=preview.active_key()or selected_core,idle_opacity=(tonumber(ModSettingGet('quant.ew.ping_opacity'))or 65)/100,
        extra_size=tonumber(ModSettingGet('quant.ew.ping_size'))or 0,mx=not occluded and mx or nil,my=not occluded and my or nil,reserved=reserved})
    selected_core=markers.hover_key
    preview.draw(gui,markers,{now=now,w=v.w,h=v.h,mx=mx,my=my,body=entity})
    local previous_target=target_hint and target_hint.entity
    target_hint=nil
    if not selected_core and not preview.active_key()and not occluded and not ctx.coop_return_input_block
        and(not EntityHasTag(entity,'polymorphed')or EntityHasTag(entity,'ew_notplayer'))then
        local x,y=point(v,last_input_device or'mouse')
        target_hint=world_target.target(x,y,v,previous_target)
        if target_hint then world_target.draw_hint(gui,target_hint,v)end
    end
end
function module.on_world_update()
    local now=GameGetFrameNum();world_input.begin(input_clock,now);sound.update(now)
    if ctx.run_ended then reset(nil,run_serial,now);return end
    local _,_,own_state=held_share.anchor(ctx.my_player)
    if had_held and(own_state=='dead'or GameHasFlagRun('ew_flag_notplayer_active'))then
        next_id=next_id+1;rpc.held_clear(run,epoch,next_id)
        held_target,had_held=nil,nil;preview.clear_held()
    end
    local peers={};for peer,p in pairs(ctx.players)do if not p.dc then peers[peer]=true end end
    local rollback=model.update(state,now,peers)or(last_frame and now<last_frame)
    if rollback or(last_ng and last_ng~=ng())then
        epoch=counter('ping_session_serial');next_id=0;targets={};held_target,had_held=nil,nil;last_cue=nil;clear_view()
        if ctx.is_host then begin_run()else if last_ng~=ng()then reset(nil,run_serial,now)end;rpc.request_run()end
    end
    last_frame,last_ng=now,ng()
    if last_body~=ctx.my_player.entity then last_body=ctx.my_player.entity;clear_view()end
    if not run and not ctx.is_host and now-(last_request or -120)>=120 then last_request=now;rpc.request_run()end
    queued,inventory_queued=nil,nil
    local v=view();if not v or menu_hidden()or not focused()then clear_view();return end
    local entity=ctx.my_player.entity
    local can_ping=not ctx.coop_return_input_block and(not EntityHasTag(entity,'polymorphed')or EntityHasTag(entity,'ew_notplayer'))
    local bind=tonumber(ModSettingGet('quant.ew.rebind_ping'))or 42
    local mouse=bind==42 and InputIsMouseButtonJustDown(3)
    local key=bind~=42 and InputIsKeyJustDown(bind)
    local pad=not ModSettingGet('quant.ew.no_gamepad')and InputIsJoystickButtonJustDown(0,18)
    local source=world_input.choose(mouse,key,pad)
    local keyboard=source=='keyboard'
    local pressed=source~=nil
    if pressed then last_input_device=source end
    if inventory()then
        -- The native capture is one render behind. Two fresh, pointer-stable
        -- windows after this press resolve the explicit owned item, or cancel.
        clear_view(true)
        local c=inventory_context(now)
        local item=inventory_hover.poll(c)
        if item then inventory_queued={item=item,context=c}end
        if pressed and can_ping and action_allowed(entity,bind,keyboard,now)then
            inventory_hover.request(c)
        end
        return -- Never fall through to world/danger ping, even if unresolved.
    end
    inventory_hover.close();shared_until=nil
    if can_ping and pressed then
        local x,y=point(v,source)
        queued=world_input.intent(input_clock,{x=x,y=y,view=v,body=entity,run=run,frame=now,bind=bind,keyboard=keyboard,device=source,
            input=input_position(v,source,x,y)})
    end
end
function module.on_world_update_post()
    local q,iq=queued,inventory_queued;queued,inventory_queued=nil,nil;local now=GameGetFrameNum()
    if iq then
        local c=inventory_context(now);local old=iq.context
        if c.run==old.run and c.body==old.body and c.inventory==old.inventory
            and c.inventory_open and c.focused and not c.blocked
            and c.mx==old.mx and c.my==old.my and (c.buttons==0 or c.buttons==2)
            and publish_owned(iq.item,c.body)then shared_until=now+90 end
    end
    -- Recheck after native actions and chat/menu handlers. No controls are written.
    if world_input.matches(q,input_clock,now,ctx.my_player.entity,run)
        and not menu_hidden()and not inventory()and focused()and not ctx.coop_return_input_block
        and action_allowed(q.body,q.bind,q.keyboard,now)then
        local current=view()or q.view
        local px,py=point(current,q.device)
        local mx,my
        if finite(px)and finite(py)then
            mx=current.w/2+(px-current.camera_x)*current.w/current.world_w
            my=current.h/2+(py-current.camera_y)*current.h/current.world_h
        end
        local selected=renderer.hit and renderer.hit(visible_records(),{view=current,now=now,mx=mx,my=my,
            selected_key=preview.active_key()or selected_core,extra_size=tonumber(ModSettingGet('quant.ew.ping_size'))or 0})
        if not(preview.blocks_world and mx and my and preview.blocks_world(mx,my))then emit(q.x,q.y,current,now,selected,q.input)end
    end
    draw(now)
end
function module.on_world_initialized() inventory_hover.close()end
function module.on_inventory_hover_suspend() clear_view()end
function module.on_player_died(entity)
    if ctx.my_player and entity==ctx.my_player.entity then clear_view()end
end
return module
